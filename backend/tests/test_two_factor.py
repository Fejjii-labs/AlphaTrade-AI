"""Two-factor authentication (TOTP): enrolment, login step, lock, remember-device, disable."""

from __future__ import annotations

import base64
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from httpx import Response
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Environment, Settings, get_settings
from app.core.dependencies import get_audit_service
from app.db.base import Base
from app.db.models import AuditLog, Membership, PasswordResetToken
from app.db.session import get_session
from app.db.two_factor import (
    TwoFactorRecoveryCodeRow,
    TwoFactorTrustedDeviceRow,
    TwoFactorUsedPendingTokenRow,
    UserTwoFactorRow,
)
from app.main import create_app
from app.security import token_denylist
from app.security import two_factor as tf
from app.security.account_tokens import hash_account_token
from app.security.rate_limit import reset_rate_limiter
from app.services import auth_service as auth_service_module
from app.services.audit_service import AuditService
from app.services.two_factor_service import TwoFactorService, TwoFactorUnavailableError

PASSWORD = "secure-password-1"
_BASE_TIME = 1_800_000_000.0  # fixed clock: tests never depend on wall-clock steps


@dataclass
class Harness:
    client: TestClient
    sessions: sessionmaker[Session]
    clock: SimpleNamespace

    def advance(self, steps: int = 1) -> None:
        self.clock.now += steps * tf.TOTP_PERIOD_SECONDS

    def code(self, secret: str, *, offset: int = 0) -> str:
        return tf.totp_code(secret, step=tf.current_time_step(self.clock.now) + offset)


@pytest.fixture
def two_factor_settings() -> Settings:
    return Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="test-secret-key-for-two-factor-32b-min",
        access_token_denylist_use_redis=False,
        rate_limit_use_redis=False,
    )


@pytest.fixture
def harness(two_factor_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[Harness]:
    reset_rate_limiter()
    # Fresh in-memory denylist: an earlier module may leave a Redis-backed singleton.
    monkeypatch.setattr(token_denylist, "_denylist", None)
    clock = SimpleNamespace(now=_BASE_TIME)
    monkeypatch.setattr(tf, "time", SimpleNamespace(time=lambda: clock.now))
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    def _override_audit(session: Session = Depends(get_session)) -> AuditService:
        # Isolated (durable) audit writes must land in this test database too.
        return AuditService(session, session_factory=factory)

    get_settings.cache_clear()
    app = create_app(settings=two_factor_settings)
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_audit_service] = _override_audit
    with TestClient(app) as client:
        yield Harness(client=client, sessions=factory, clock=clock)
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _register(h: Harness, email: str) -> dict:
    response = h.client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "organization_name": f"Org {email}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(h: Harness, email: str, **extra: object) -> dict:
    response = h.client.post("/auth/login", json={"email": email, "password": PASSWORD, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def _enable(h: Harness, email: str) -> tuple[str, str, list[str]]:
    """Register, enrol and confirm. Returns (access token, secret, recovery codes)."""
    access = _register(h, email)["tokens"]["access_token"]
    setup = h.client.post(
        "/auth/two-factor/setup", headers=_bearer(access), json={"password": PASSWORD}
    )
    assert setup.status_code == 200, setup.text
    secret = setup.json()["manual_key"]
    confirm = h.client.post(
        "/auth/two-factor/confirm", headers=_bearer(access), json={"code": h.code(secret)}
    )
    assert confirm.status_code == 200, confirm.text
    h.advance()  # the confirm step is now spent; later checks use a fresh step
    body = confirm.json()
    return body["tokens"]["access_token"], secret, body["recovery_codes"]


def _setup(h: Harness, access: str, password: str = PASSWORD) -> Response:
    return h.client.post(
        "/auth/two-factor/setup", headers=_bearer(access), json={"password": password}
    )


def _disable(h: Harness, access: str, password: str = PASSWORD, **factor: object) -> Response:
    return h.client.post(
        "/auth/two-factor/disable",
        headers=_bearer(access),
        json={"password": password, **factor},
    )


def _second_step(h: Harness, pending: str, **factor: object) -> Response:
    return h.client.post("/auth/login/two-factor", json={"pending_token": pending, **factor})


# --------------------------------------------------------------------- primitives


def test_totp_matches_rfc6238_sha1_vectors() -> None:
    secret = base64.b32encode(b"12345678901234567890").decode()
    # RFC 6238 Appendix B (8-digit values; the 6-digit code is the last six).
    assert tf.totp_code(secret, step=59 // 30) == "287082"
    assert tf.totp_code(secret, step=1111111109 // 30) == "081804"
    assert tf.totp_code(secret, step=2000000000 // 30) == "279037"


def test_totp_accepts_plus_minus_one_step_only() -> None:
    secret = tf.generate_totp_secret()
    step = tf.current_time_step(_BASE_TIME)
    for offset in (-1, 0, 1):
        code = tf.totp_code(secret, step=step + offset)
        assert tf.match_totp_step(secret, code, now=_BASE_TIME) == step + offset
    for offset in (-2, 2):
        code = tf.totp_code(secret, step=step + offset)
        assert tf.match_totp_step(secret, code, now=_BASE_TIME) is None
    assert tf.match_totp_step(secret, "12345", now=_BASE_TIME) is None
    assert tf.match_totp_step(secret, "abcdef", now=_BASE_TIME) is None


def test_secret_encryption_round_trip_is_bound_to_user(two_factor_settings: Settings) -> None:
    secret = tf.generate_totp_secret()
    owner = uuid.uuid4()
    sealed = tf.encrypt_totp_secret(secret, user_id=owner, settings=two_factor_settings)
    assert secret not in sealed and sealed.startswith("v1:")
    assert tf.decrypt_totp_secret(sealed, user_id=owner, settings=two_factor_settings) == secret
    with pytest.raises(tf.TwoFactorSecretError):
        tf.decrypt_totp_secret(sealed, user_id=uuid.uuid4(), settings=two_factor_settings)
    other_key = two_factor_settings.model_copy(
        update={"two_factor_encryption_key": base64.urlsafe_b64encode(b"k" * 32).decode()}
    )
    with pytest.raises(tf.TwoFactorSecretError):
        tf.decrypt_totp_secret(sealed, user_id=owner, settings=other_key)


def test_malformed_two_factor_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="local", two_factor_encryption_key="too-short")


# ---------------------------------------------------------------------- enrolment


def test_enrolment_stores_only_ciphertext_and_digests(harness: Harness) -> None:
    access = _register(harness, "enrol@example.com")["tokens"]["access_token"]
    status = harness.client.get("/auth/two-factor", headers=_bearer(access)).json()
    assert status["enabled"] is False

    setup = _setup(harness, access).json()
    secret = setup["manual_key"]
    assert setup["otpauth_uri"].startswith("otpauth://totp/AlphaTrade%20AI%3Aenrol%40example.com?")
    assert f"secret={secret}" in setup["otpauth_uri"]
    assert setup["qr_svg_data_uri"].startswith("data:image/svg+xml;base64,")
    status = harness.client.get("/auth/two-factor", headers=_bearer(access)).json()
    assert status["enabled"] is False and status["pending_setup"] is True

    wrong = harness.client.post(
        "/auth/two-factor/confirm", headers=_bearer(access), json={"code": "000000"}
    )
    assert wrong.status_code == 400
    assert wrong.json()["error"]["code"] == "two_factor_code_invalid"

    confirm = harness.client.post(
        "/auth/two-factor/confirm", headers=_bearer(access), json={"code": harness.code(secret)}
    )
    assert confirm.status_code == 200, confirm.text
    codes = confirm.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10

    status = harness.client.get("/auth/two-factor", headers=_bearer(access)).json()
    assert status["enabled"] is True and status["recovery_codes_remaining"] == 10

    with harness.sessions() as session:
        row = session.scalars(select(UserTwoFactorRow)).one()
        assert secret not in row.secret_ciphertext
        stored = set(session.scalars(select(TwoFactorRecoveryCodeRow.code_hash)))
        assert stored.isdisjoint(codes) and len(stored) == 10

    again = _setup(harness, access)
    assert again.status_code == 409


def test_setup_requires_authentication(harness: Harness) -> None:
    response = harness.client.post("/auth/two-factor/setup", json={"password": PASSWORD})
    assert response.status_code == 401


def test_setup_requires_the_current_password(harness: Harness) -> None:
    access = _register(harness, "setup-pw@example.com")["tokens"]["access_token"]
    missing = harness.client.post("/auth/two-factor/setup", headers=_bearer(access), json={})
    assert missing.status_code == 422
    wrong = _setup(harness, access, password="not-the-password")
    assert wrong.status_code == 400
    assert wrong.json()["error"]["code"] == "current_password_invalid"
    assert "manual_key" not in wrong.text
    with harness.sessions() as session:
        assert session.scalars(select(UserTwoFactorRow)).first() is None
        failed = session.scalars(
            select(AuditLog).where(AuditLog.action == "auth_two_factor_failed")
        ).all()
        assert [row.redacted_metadata.get("method") for row in failed] == ["password"]
    assert _setup(harness, access).status_code == 200


def test_confirm_signs_out_every_other_session(harness: Harness) -> None:
    email = "sessions@example.com"
    first = _register(harness, email)["tokens"]
    second = _login(harness, email)["tokens"]
    secret = _setup(harness, first["access_token"]).json()["manual_key"]
    confirm = harness.client.post(
        "/auth/two-factor/confirm",
        headers=_bearer(first["access_token"]),
        json={"code": harness.code(secret)},
    )
    assert confirm.status_code == 200, confirm.text
    body = confirm.json()
    assert body["revoked_sessions"] == 2
    for old in (first, second):
        stale = harness.client.post("/auth/refresh", json={"refresh_token": old["refresh_token"]})
        assert stale.status_code == 401
    fresh = harness.client.post(
        "/auth/refresh", json={"refresh_token": body["tokens"]["refresh_token"]}
    )
    assert fresh.status_code == 200, fresh.text
    me = harness.client.get("/auth/me", headers=_bearer(body["tokens"]["access_token"]))
    assert me.status_code == 200


# -------------------------------------------------------------------------- login


def test_login_without_two_factor_is_unchanged(harness: Harness) -> None:
    _register(harness, "plain@example.com")
    body = _login(harness, "plain@example.com")
    assert body["tokens"]["access_token"]
    assert "two_factor_required" not in body


def test_login_with_two_factor_needs_second_step(harness: Harness) -> None:
    _, secret, _ = _enable(harness, "mfa@example.com")
    challenge = _login(harness, "mfa@example.com")
    assert challenge["two_factor_required"] is True
    assert "tokens" not in challenge
    pending = challenge["pending_token"]

    # The pending token is not a session.
    assert harness.client.get("/auth/me", headers=_bearer(pending)).status_code == 401

    done = _second_step(harness, pending, code=harness.code(secret))
    assert done.status_code == 200, done.text
    access = done.json()["tokens"]["access_token"]
    assert done.json()["device_token"] is None
    assert harness.client.get("/auth/me", headers=_bearer(access)).status_code == 200

    # Single use: the same pending token cannot open a second session.
    harness.advance()
    reused = _second_step(harness, pending, code=harness.code(secret))
    assert reused.status_code == 401


def test_wrong_code_and_replayed_totp_are_refused(harness: Harness) -> None:
    _, secret, _ = _enable(harness, "replay@example.com")
    pending = _login(harness, "replay@example.com")["pending_token"]
    assert _second_step(harness, pending, code="000000").status_code == 401

    code = harness.code(secret)
    assert _second_step(harness, pending, code=code).status_code == 200

    # Same code, same step, new login: refused (no TOTP replay).
    pending_again = _login(harness, "replay@example.com")["pending_token"]
    assert _second_step(harness, pending_again, code=code).status_code == 401


def test_second_step_rejects_both_or_neither_factor(harness: Harness) -> None:
    _, secret, codes = _enable(harness, "shape@example.com")
    pending = _login(harness, "shape@example.com")["pending_token"]
    assert _second_step(harness, pending).status_code == 422
    both = _second_step(harness, pending, code=harness.code(secret), recovery_code=codes[0])
    assert both.status_code == 422


def test_recovery_code_works_once(harness: Harness) -> None:
    access, _, codes = _enable(harness, "recover@example.com")
    pending = _login(harness, "recover@example.com")["pending_token"]
    used = _second_step(harness, pending, recovery_code=codes[0].upper().replace("-", " "))
    assert used.status_code == 200, used.text

    pending = _login(harness, "recover@example.com")["pending_token"]
    replay = _second_step(harness, pending, recovery_code=codes[0])
    assert replay.status_code == 401

    new_access = used.json()["tokens"]["access_token"]
    status = harness.client.get("/auth/two-factor", headers=_bearer(new_access)).json()
    assert status["recovery_codes_remaining"] == 9
    del access


def test_five_failures_lock_the_code_step(harness: Harness) -> None:
    _, secret, _ = _enable(harness, "lock@example.com")
    pending = _login(harness, "lock@example.com")["pending_token"]
    for _ in range(4):
        assert _second_step(harness, pending, code="000000").status_code == 401
    fifth = _second_step(harness, pending, code="000000")
    assert fifth.status_code == 429
    assert fifth.json()["error"]["code"] == "two_factor_locked"

    # A correct code is refused while locked.
    locked = _second_step(harness, pending, code=harness.code(secret))
    assert locked.status_code == 429

    # After the lock expires, a correct code works again.
    with harness.sessions() as session:
        row = session.scalars(select(UserTwoFactorRow)).one()
        row.locked_until = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    assert _second_step(harness, pending, code=harness.code(secret)).status_code == 200


def test_failures_outside_the_window_do_not_add_up(harness: Harness) -> None:
    _, _, _ = _enable(harness, "window@example.com")
    pending = _login(harness, "window@example.com")["pending_token"]
    for _ in range(4):
        assert _second_step(harness, pending, code="000000").status_code == 401
    with harness.sessions() as session:
        row = session.scalars(select(UserTwoFactorRow)).one()
        row.failure_window_started_at = datetime.now(UTC) - timedelta(minutes=16)
        session.commit()
    assert _second_step(harness, pending, code="000000").status_code == 401


def test_audit_records_outcomes_without_codes(harness: Harness) -> None:
    _, secret, codes = _enable(harness, "audit@example.com")
    pending = _login(harness, "audit@example.com")["pending_token"]
    _second_step(harness, pending, code="000000")
    good = harness.code(secret)
    _second_step(harness, pending, code=good)
    with harness.sessions() as session:
        rows = session.scalars(
            select(AuditLog).where(AuditLog.resource_type == "auth_two_factor")
        ).all()
    actions = {str(row.action) for row in rows}
    assert {
        "auth_two_factor_setup_started",
        "auth_two_factor_enabled",
        "auth_two_factor_failed",
        "auth_two_factor_verified",
    } <= actions
    dumped = repr([(row.redacted_metadata, row.before, row.after) for row in rows])
    for value in (secret, good, "000000", *codes):
        assert value not in dumped


# ----------------------------------------------------------------- remember device


def test_remember_device_skips_code_until_revoked(harness: Harness) -> None:
    _, secret, _ = _enable(harness, "device@example.com")
    pending = _login(harness, "device@example.com")["pending_token"]
    done = _second_step(harness, pending, code=harness.code(secret), remember_device=True).json()
    device_token = done["device_token"]
    assert device_token and done["device_token_expires_at"]

    remembered = _login(harness, "device@example.com", device_token=device_token)
    assert remembered["tokens"]["access_token"]

    # A tampered token is ignored (challenge again), not an error.
    tampered = device_token[:-2] + ("A" if device_token[-2] != "A" else "B") + device_token[-1]
    assert _login(harness, "device@example.com", device_token=tampered)["two_factor_required"]

    access = remembered["tokens"]["access_token"]
    revoke = harness.client.post("/auth/two-factor/devices/revoke", headers=_bearer(access))
    assert revoke.status_code == 200
    again = _login(harness, "device@example.com", device_token=device_token)
    assert again["two_factor_required"] is True


def test_device_token_of_another_user_is_ignored(harness: Harness) -> None:
    _, secret_a, _ = _enable(harness, "a@example.com")
    pending = _login(harness, "a@example.com")["pending_token"]
    token_a = _second_step(
        harness, pending, code=harness.code(secret_a), remember_device=True
    ).json()["device_token"]
    _enable(harness, "b@example.com")
    assert _login(harness, "b@example.com", device_token=token_a)["two_factor_required"]


# --------------------------------------------------------- disable / regenerate


def test_disable_requires_password_and_current_code(harness: Harness) -> None:
    access, secret, codes = _enable(harness, "disable@example.com")
    wrong = _disable(harness, access, code="000000")
    assert wrong.status_code == 400
    assert wrong.json()["error"]["code"] == "two_factor_code_invalid"
    # A recovery code is not a TOTP code, and a code alone (no password) is not enough.
    assert _disable(harness, access, code=codes[0]).status_code == 422
    no_password = harness.client.post(
        "/auth/two-factor/disable", headers=_bearer(access), json={"code": harness.code(secret)}
    )
    assert no_password.status_code == 422
    # Wrong password: refused before the code is checked, so the TOTP step stays unspent.
    bad_password = _disable(harness, access, password="nope-nope", code=harness.code(secret))
    assert bad_password.status_code == 400
    assert bad_password.json()["error"]["code"] == "current_password_invalid"

    ok = _disable(harness, access, code=harness.code(secret))
    assert ok.status_code == 200, ok.text
    status = harness.client.get("/auth/two-factor", headers=_bearer(access)).json()
    assert status["enabled"] is False
    assert _login(harness, "disable@example.com")["tokens"]["access_token"]
    with harness.sessions() as session:
        assert session.scalars(select(TwoFactorRecoveryCodeRow)).first() is None


def test_regenerate_recovery_codes_requires_code_and_replaces_old(harness: Harness) -> None:
    access, secret, old_codes = _enable(harness, "regen@example.com")
    wrong = harness.client.post(
        "/auth/two-factor/recovery-codes", headers=_bearer(access), json={"code": "000000"}
    )
    assert wrong.status_code == 400
    regen = harness.client.post(
        "/auth/two-factor/recovery-codes",
        headers=_bearer(access),
        json={"code": harness.code(secret)},
    )
    assert regen.status_code == 200, regen.text
    new_codes = regen.json()["recovery_codes"]
    assert len(new_codes) == 10 and set(new_codes).isdisjoint(old_codes)

    pending = _login(harness, "regen@example.com")["pending_token"]
    assert _second_step(harness, pending, recovery_code=old_codes[0]).status_code == 401
    assert _second_step(harness, pending, recovery_code=new_codes[0]).status_code == 200


def test_disable_revokes_remembered_devices(harness: Harness) -> None:
    access, secret, _ = _enable(harness, "revoke@example.com")
    pending = _login(harness, "revoke@example.com")["pending_token"]
    device_token = _second_step(
        harness, pending, code=harness.code(secret), remember_device=True
    ).json()["device_token"]
    harness.advance()
    assert _disable(harness, access, code=harness.code(secret)).status_code == 200
    # Re-enrol: the old device grant must not skip the new second factor.
    setup = _setup(harness, access).json()
    harness.advance()
    harness.client.post(
        "/auth/two-factor/confirm",
        headers=_bearer(access),
        json={"code": harness.code(setup["manual_key"])},
    )
    assert _login(harness, "revoke@example.com", device_token=device_token)["two_factor_required"]


# ------------------------------------------------------------ review follow-ups


def test_disable_with_recovery_code_for_a_lost_phone(harness: Harness) -> None:
    access, _, codes = _enable(harness, "lost-phone@example.com")
    assert _disable(harness, access, recovery_code="aaaaa-aaaaa").status_code == 400
    assert (
        _disable(harness, access, password="wrong-wrong", recovery_code=codes[0]).status_code == 400
    )
    ok = _disable(harness, access, recovery_code=codes[0])
    assert ok.status_code == 200, ok.text
    status = harness.client.get("/auth/two-factor", headers=_bearer(access)).json()
    assert status["enabled"] is False
    assert _login(harness, "lost-phone@example.com")["tokens"]["access_token"]
    # The user can enrol a new phone straight away.
    assert _setup(harness, access).status_code == 200


def test_password_reset_revokes_remembered_devices(harness: Harness) -> None:
    email = "reset-devices@example.com"
    _, secret, _ = _enable(harness, email)
    pending = _login(harness, email)["pending_token"]
    device_token = _second_step(
        harness, pending, code=harness.code(secret), remember_device=True
    ).json()["device_token"]
    assert "tokens" in _login(harness, email, device_token=device_token)

    raw = "reset-token-for-two-factor-test-0001"
    with harness.sessions() as session:
        user_id = session.scalars(select(UserTwoFactorRow.user_id)).one()
        session.add(
            PasswordResetToken(
                user_id=user_id,
                token_hash=hash_account_token(raw),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        session.commit()
    new_password = "brand-new-password-2"
    reset = harness.client.post(
        "/auth/password-reset/confirm", json={"token": raw, "new_password": new_password}
    )
    assert reset.status_code == 200, reset.text
    with harness.sessions() as session:
        devices = session.scalars(select(TwoFactorTrustedDeviceRow)).all()
        assert devices and all(device.revoked_at is not None for device in devices)
    again = harness.client.post(
        "/auth/login",
        json={"email": email, "password": new_password, "device_token": device_token},
    )
    assert again.status_code == 200 and again.json()["two_factor_required"] is True


def test_pending_token_is_single_use_without_the_denylist(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        auth_service_module,
        "get_access_token_denylist",
        lambda _settings: token_denylist.NoOpDenylist(),
    )
    _, secret, codes = _enable(harness, "single-use@example.com")
    pending = _login(harness, "single-use@example.com")["pending_token"]
    assert _second_step(harness, pending, code=harness.code(secret)).status_code == 200
    with harness.sessions() as session:
        used = session.scalars(select(TwoFactorUsedPendingTokenRow)).all()
        assert len(used) == 1
    harness.advance()
    replay_totp = _second_step(harness, pending, code=harness.code(secret))
    replay_recovery = _second_step(harness, pending, recovery_code=codes[0])
    assert replay_totp.status_code == 401 and replay_recovery.status_code == 401
    with harness.sessions() as session:
        row = session.scalars(select(UserTwoFactorRow)).one()
        # Refused before any code check: nothing counted, the recovery code is unspent.
        assert row.failed_attempts == 0
        assert (
            session.scalars(
                select(TwoFactorRecoveryCodeRow).where(
                    TwoFactorRecoveryCodeRow.used_at.is_not(None)
                )
            ).first()
            is None
        )
    # A new sign-in still works.
    fresh = _login(harness, "single-use@example.com")["pending_token"]
    assert _second_step(harness, fresh, code=harness.code(secret)).status_code == 200


def test_unreadable_secret_is_a_server_error_not_a_wrong_code(
    harness: Harness, two_factor_settings: Settings
) -> None:
    email = "key-changed@example.com"
    _, secret, codes = _enable(harness, email)
    with harness.sessions() as session:
        row = session.scalars(select(UserTwoFactorRow)).one()
        # Valid format, wrong key binding: what a changed key looks like.
        row.secret_ciphertext = tf.encrypt_totp_secret(
            secret, user_id=uuid.uuid4(), settings=two_factor_settings
        )
        session.commit()
    pending = _login(harness, email)["pending_token"]
    for _ in range(two_factor_settings.two_factor_max_failures + 1):
        totp = _second_step(harness, pending, code=harness.code(secret))
        assert totp.status_code == 503
        assert totp.json()["error"]["code"] == "two_factor_unavailable"
    recovery = _second_step(harness, pending, recovery_code=codes[0])
    assert recovery.status_code == 503
    with harness.sessions() as session:
        row = session.scalars(select(UserTwoFactorRow)).one()
        assert row.failed_attempts == 0 and row.locked_until is None
        audits = session.scalars(
            select(AuditLog).where(AuditLog.action == "auth_two_factor_failed")
        ).all()
        assert any(a.redacted_metadata.get("reason") == "secret_unreadable" for a in audits)


def test_enrolment_is_refused_outside_local_without_a_key(
    harness: Harness, two_factor_settings: Settings
) -> None:
    _register(harness, "staging@example.com")
    staging = two_factor_settings.model_copy(update={"environment": Environment.STAGING})
    with harness.sessions() as session:
        assert session.scalars(select(UserTwoFactorRow.user_id)).first() is None
        membership = session.scalars(select(Membership)).one()
        service = TwoFactorService(session, staging)
        assert service.status(membership.user_id).enrolment_available is False
        with pytest.raises(TwoFactorUnavailableError):
            service.start_setup(
                user_id=membership.user_id,
                organization_id=membership.organization_id,
                password=PASSWORD,
            )
        key = base64.urlsafe_b64encode(b"k" * 32).decode()
        keyed = TwoFactorService(
            session, staging.model_copy(update={"two_factor_encryption_key": key})
        )
        assert keyed.status(membership.user_id).enrolment_available is True
        setup = keyed.start_setup(
            user_id=membership.user_id,
            organization_id=membership.organization_id,
            password=PASSWORD,
        )
        assert setup.manual_key
