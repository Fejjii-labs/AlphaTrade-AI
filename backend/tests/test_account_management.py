"""Slice 25: email verification, password reset, and invitations."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.models import AuditLog, EmailVerificationToken, Membership, PasswordResetToken, User
from app.db.session import get_session
from app.main import create_app
from app.providers.email.factory import reset_email_provider_for_tests, resolve_email_provider
from app.schemas.common import AuditEventType, MembershipRole
from app.security.account_tokens import hash_account_token
from app.security.rate_limit import reset_rate_limiter


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    reset_rate_limiter()
    reset_email_provider_for_tests()


@pytest.fixture
def account_settings() -> Settings:
    return Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="test-secret-key-for-account-slice-32b-min",
        require_email_verified=False,
        email_send_enabled=True,
        email_provider="mock",
        access_token_denylist_use_redis=False,
        rate_limit_use_redis=False,
    )


AccountClientFixture = tuple[TestClient, sessionmaker[Session]]


@pytest.fixture
def account_client(account_settings: Settings) -> Iterator[AccountClientFixture]:
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

    get_settings.cache_clear()
    app = create_app(settings=account_settings)
    app.dependency_overrides[get_session] = _override_session

    with TestClient(app) as client:
        yield client, factory

    app.dependency_overrides.clear()
    get_settings.cache_clear()
    reset_email_provider_for_tests()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _register(client: TestClient, *, email: str, org: str = "Account Org") -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "secure-password-1",
            "organization_name": org,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _extract_token_from_mock_email(settings: Settings, template: str) -> str:
    provider = resolve_email_provider(settings)
    message = next(m for m in provider.sent if m.template == template)
    match = re.search(r"token=([^&\s]+)", message.text_body)
    assert match is not None
    return match.group(1)


def test_login_blocked_when_email_unverified_when_required(
    account_client: AccountClientFixture,
) -> None:
    """When email verification is required, login fails until confirm."""
    _client, _ = account_client
    get_settings.cache_clear()
    settings = Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="test-secret-key-for-account-slice-32b-min",
        require_email_verified=True,
        email_send_enabled=True,
        email_provider="mock",
        access_token_denylist_use_redis=False,
        rate_limit_use_redis=False,
    )
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
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def _override_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app = create_app(settings=settings)
    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as verify_client:
        _register(verify_client, email="staging-smoke@example.com")
        login = verify_client.post(
            "/auth/login",
            json={"email": "staging-smoke@example.com", "password": "secure-password-1"},
        )
        assert login.status_code == 401
        assert "not verified" in login.json()["error"]["message"].lower()
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_must_verify_email_true_for_staging_environment() -> None:
    settings = Settings(
        environment="staging",
        debug=False,
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        provider_mode="fallback",
        database_url="postgresql+psycopg://user:pass@db.example.com:5432/alphatrade",
        redis_url="redis://:pass@redis.example.com:6379/0",
        qdrant_url="https://cluster.example.com",
        openai_api_key="sk-test-not-a-real-key",
        jwt_secret="test-secret-key-for-account-slice-32b-min",
        cors_origins=["https://app.example.com"],
        auth_refresh_cookie_enabled=True,
        auth_cookie_secure=True,
        rate_limit_use_redis=True,
        rate_limit_allow_in_memory_fallback=False,
        trusted_proxy_hops=1,
    )
    assert settings.must_verify_email is True


def test_register_creates_verification_token(
    account_client: AccountClientFixture,
    account_settings: Settings,
) -> None:
    client, factory = account_client
    registered = _register(client, email="verify@example.com")
    assert registered["user"]["email_verified"] is False

    with factory() as session:
        user = session.scalars(select(User).where(User.email == "verify@example.com")).one()
        rows = session.scalars(
            select(EmailVerificationToken).where(EmailVerificationToken.user_id == user.id)
        ).all()
        assert len(rows) == 1
        assert rows[0].token_hash != ""
        assert len(rows[0].token_hash) == 64

    _extract_token_from_mock_email(account_settings, "email_verification")


def test_verify_email_success(
    account_client: AccountClientFixture,
    account_settings: Settings,
) -> None:
    client, _ = account_client
    _register(client, email="confirm@example.com")
    token = _extract_token_from_mock_email(account_settings, "email_verification")
    response = client.post("/auth/verify-email/confirm", json={"token": token})
    assert response.status_code == 200
    login = client.post(
        "/auth/login",
        json={"email": "confirm@example.com", "password": "secure-password-1"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["email_verified"] is True


def test_verify_email_expired_token_fails(
    account_client: AccountClientFixture,
    account_settings: Settings,
) -> None:
    client, factory = account_client
    _register(client, email="expired@example.com")
    token = _extract_token_from_mock_email(account_settings, "email_verification")
    with factory() as session:
        row = session.scalars(select(EmailVerificationToken)).one()
        from datetime import UTC, datetime, timedelta

        row.expires_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()
    response = client.post("/auth/verify-email/confirm", json={"token": token})
    assert response.status_code == 401


def test_verify_email_invalid_token_fails(account_client: AccountClientFixture) -> None:
    client, _ = account_client
    _register(client, email="badtoken@example.com")
    response = client.post(
        "/auth/verify-email/confirm",
        json={"token": "not-a-valid-token-value-xxxxxxxx"},
    )
    assert response.status_code == 401


def test_resend_verification_uses_profile_email_for_authenticated_empty_body(
    account_client: AccountClientFixture,
    account_settings: Settings,
) -> None:
    client, _ = account_client
    registered = _register(client, email="resend-profile@example.com")
    reset_email_provider_for_tests()
    response = client.post(
        "/auth/verify-email/request",
        json={},
        headers=_auth(registered["tokens"]["access_token"]),
    )
    assert response.status_code == 200
    provider = resolve_email_provider(account_settings)
    sent = [m for m in provider.sent if m.template == "email_verification"]
    assert [m.to_address for m in sent] == ["resend-profile@example.com"]


def test_resend_verification_ignores_body_email_when_authenticated(
    account_client: AccountClientFixture,
    account_settings: Settings,
) -> None:
    client, _ = account_client
    registered = _register(client, email="resend-owner@example.com")
    _register(client, email="resend-other@example.com", org="Other Org")
    reset_email_provider_for_tests()
    response = client.post(
        "/auth/verify-email/request",
        json={"email": "resend-other@example.com"},
        headers=_auth(registered["tokens"]["access_token"]),
    )
    assert response.status_code == 200
    provider = resolve_email_provider(account_settings)
    sent = [m for m in provider.sent if m.template == "email_verification"]
    assert [m.to_address for m in sent] == ["resend-owner@example.com"]


def test_resend_verification_uses_body_email_when_signed_out(
    account_client: AccountClientFixture,
    account_settings: Settings,
) -> None:
    client, _ = account_client
    _register(client, email="resend-anon@example.com")
    reset_email_provider_for_tests()
    response = client.post(
        "/auth/verify-email/request",
        json={"email": "resend-anon@example.com"},
    )
    assert response.status_code == 200
    assert "requires verification" in response.json()["message"]
    provider = resolve_email_provider(account_settings)
    sent = [m for m in provider.sent if m.template == "email_verification"]
    assert [m.to_address for m in sent] == ["resend-anon@example.com"]


def test_resend_verification_without_session_or_email_is_rejected(
    account_client: AccountClientFixture,
) -> None:
    client, _ = account_client
    response = client.post("/auth/verify-email/request", json={})
    assert response.status_code == 422
    assert "Email is required" in response.text


def test_resend_verification_rate_limited(
    account_client: AccountClientFixture,
) -> None:
    client, _ = account_client
    registered = _register(client, email="resend@example.com")
    headers = _auth(registered["tokens"]["access_token"])
    for _ in range(6):
        ok = client.post("/auth/verify-email/request", json={}, headers=headers)
        if ok.status_code == 429:
            break
        assert ok.status_code == 200
    blocked = client.post("/auth/verify-email/request", json={}, headers=headers)
    assert blocked.status_code == 429


def test_password_reset_request_generic_success(account_client: AccountClientFixture) -> None:
    client, _ = account_client
    _register(client, email="reset@example.com")
    response = client.post(
        "/auth/password-reset/request",
        json={"email": "reset@example.com"},
    )
    assert response.status_code == 200
    assert "account exists" in response.json()["message"].lower()
    unknown = client.post(
        "/auth/password-reset/request",
        json={"email": "unknown@example.com"},
    )
    assert unknown.status_code == 200
    assert unknown.json()["message"] == response.json()["message"]


def test_password_reset_confirm_success(
    account_client: AccountClientFixture,
    account_settings: Settings,
) -> None:
    client, _ = account_client
    _register(client, email="newpass@example.com")
    client.post("/auth/password-reset/request", json={"email": "newpass@example.com"})
    token = _extract_token_from_mock_email(account_settings, "password_reset")
    response = client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "new-secure-password-9"},
    )
    assert response.status_code == 200
    login = client.post(
        "/auth/login",
        json={"email": "newpass@example.com", "password": "new-secure-password-9"},
    )
    assert login.status_code == 200
    old = client.post(
        "/auth/login",
        json={"email": "newpass@example.com", "password": "secure-password-1"},
    )
    assert old.status_code == 401


def test_password_reset_token_one_time_use(
    account_client: AccountClientFixture,
    account_settings: Settings,
) -> None:
    client, _ = account_client
    _register(client, email="once@example.com")
    client.post("/auth/password-reset/request", json={"email": "once@example.com"})
    token = _extract_token_from_mock_email(account_settings, "password_reset")
    first = client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "another-secure-pass-1"},
    )
    assert first.status_code == 200
    second = client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "another-secure-pass-2"},
    )
    assert second.status_code == 401


def test_password_reset_revokes_refresh_tokens(
    account_client: AccountClientFixture,
    account_settings: Settings,
) -> None:
    client, _ = account_client
    registered = _register(client, email="revoke@example.com")
    refresh = registered["tokens"]["refresh_token"]
    client.post("/auth/password-reset/request", json={"email": "revoke@example.com"})
    token = _extract_token_from_mock_email(account_settings, "password_reset")
    client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": "revoked-secure-pass-1"},
    )
    stale = client.post("/auth/refresh", json={"refresh_token": refresh})
    assert stale.status_code == 401


def test_invalid_reset_token_audited(
    account_client: AccountClientFixture,
) -> None:
    client, factory = account_client
    _register(client, email="auditreset@example.com")
    client.post(
        "/auth/password-reset/confirm",
        json={
            "token": "invalid-token-value-xxxxxxxxxx",
            "new_password": "audit-secure-pass-1",
        },
    )
    with factory() as session:
        failed = session.scalars(
            select(AuditLog).where(AuditLog.action == AuditEventType.AUTH_PASSWORD_RESET_FAILED)
        ).all()
    assert len(failed) >= 1


def test_owner_creates_invitation(
    account_client: AccountClientFixture,
    account_settings: Settings,
) -> None:
    client, _ = account_client
    owner = _register(client, email="owner@example.com")
    headers = _auth(owner["tokens"]["access_token"])
    response = client.post(
        "/organizations/invitations",
        headers=headers,
        json={"email": "invitee@example.com", "role": "trader"},
    )
    assert response.status_code == 201
    assert response.json()["email"] == "invitee@example.com"
    _extract_token_from_mock_email(account_settings, "organization_invitation")


def test_viewer_cannot_create_invitation(
    account_client: AccountClientFixture,
) -> None:
    client, factory = account_client
    owner = _register(client, email="owner2@example.com")
    with factory() as session:
        membership = session.scalars(select(Membership)).one()
        membership.role = MembershipRole.VIEWER
        session.commit()
    headers = _auth(owner["tokens"]["access_token"])
    response = client.post(
        "/organizations/invitations",
        headers=headers,
        json={"email": "blocked@example.com", "role": "viewer"},
    )
    assert response.status_code == 403


def test_invite_accept_creates_membership(
    account_client: AccountClientFixture,
    account_settings: Settings,
) -> None:
    client, factory = account_client
    owner = _register(client, email="orgowner@example.com", org="Invite Org")
    headers = _auth(owner["tokens"]["access_token"])
    created = client.post(
        "/organizations/invitations",
        headers=headers,
        json={"email": "member@example.com", "role": "viewer"},
    )
    invitation_id = created.json()["id"]
    invitee = _register(client, email="member@example.com", org="Other Org")
    token = _extract_token_from_mock_email(account_settings, "organization_invitation")
    accept = client.post(
        f"/organizations/invitations/{invitation_id}/accept",
        headers=_auth(invitee["tokens"]["access_token"]),
        json={"token": token},
    )
    assert accept.status_code == 200
    with factory() as session:
        user = session.scalars(select(User).where(User.email == "member@example.com")).one()
        org_id = uuid.UUID(created.json()["organization_id"])
        membership = session.scalars(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.organization_id == org_id,
            )
        ).one()
        assert membership.role == MembershipRole.VIEWER


def test_invite_revoke_works(account_client: AccountClientFixture) -> None:
    client, _ = account_client
    owner = _register(client, email="revoker@example.com")
    headers = _auth(owner["tokens"]["access_token"])
    created = client.post(
        "/organizations/invitations",
        headers=headers,
        json={"email": "revoked@example.com", "role": "trader"},
    )
    invitation_id = created.json()["id"]
    revoked = client.post(
        f"/organizations/invitations/{invitation_id}/revoke",
        headers=headers,
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None


def test_tokens_not_stored_plaintext(
    account_client: AccountClientFixture,
    account_settings: Settings,
) -> None:
    client, factory = account_client
    _register(client, email="hash@example.com")
    raw = _extract_token_from_mock_email(account_settings, "email_verification")
    with factory() as session:
        row = session.scalars(select(EmailVerificationToken)).one()
        assert row.token_hash == hash_account_token(raw)
        assert row.token_hash != raw
    client.post("/auth/password-reset/request", json={"email": "hash@example.com"})
    reset_raw = _extract_token_from_mock_email(account_settings, "password_reset")
    with factory() as session:
        row = session.scalars(select(PasswordResetToken)).one()
        assert row.token_hash == hash_account_token(reset_raw)


def test_tokens_not_logged(
    account_client: AccountClientFixture,
    account_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _ = account_client
    _register(client, email="nolog@example.com")
    token = _extract_token_from_mock_email(account_settings, "email_verification")
    client.post("/auth/verify-email/confirm", json={"token": token})
    assert token not in caplog.text


def test_real_trading_remains_disabled(account_client: AccountClientFixture) -> None:
    client, _ = account_client
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["real_trading_enabled"] is False


def _verified_required_client() -> Iterator[tuple[TestClient, sessionmaker[Session], Settings]]:
    get_settings.cache_clear()
    settings = Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="test-secret-key-for-account-slice-32b-min",
        require_email_verified=True,
        email_send_enabled=True,
        email_provider="mock",
        access_token_denylist_use_redis=False,
        rate_limit_use_redis=False,
    )
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def _override_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app = create_app(settings=settings)
    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as client:
        yield client, session_factory, settings
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_unverified_registration_session_is_limited_when_verification_required() -> None:
    """M14: register tokens only reach /auth/me and verification until verified."""
    gen = _verified_required_client()
    client, factory, _settings = next(gen)
    try:
        registered = _register(client, email="unverified@example.com")
        access = registered["tokens"]["access_token"]
        refresh = registered["tokens"]["refresh_token"]

        me = client.get("/auth/me", headers=_auth(access))
        assert me.status_code == 200, me.text
        assert me.json()["user"]["email_verified"] is False
        resend = client.post("/auth/verify-email/request", headers=_auth(access), json={})
        assert resend.status_code == 200, resend.text

        blocked = client.get("/organizations/invitations", headers=_auth(access))
        assert blocked.status_code == 401
        assert "not verified" in blocked.json()["error"]["message"].lower()

        refreshed = client.post("/auth/refresh", json={"refresh_token": refresh})
        assert refreshed.status_code == 401
        assert "not verified" in refreshed.json()["error"]["message"].lower()

        with factory() as session:
            user = session.scalars(select(User).where(User.email == "unverified@example.com")).one()
            user.email_verified = True
            session.commit()

        allowed = client.get("/organizations/invitations", headers=_auth(access))
        assert allowed.status_code == 200, allowed.text
        assert client.post("/auth/refresh", json={"refresh_token": refresh}).status_code == 200
    finally:
        gen.close()


def test_resolve_tenant_uses_membership_matching_token_org(
    account_client: AccountClientFixture,
    account_settings: Settings,
) -> None:
    """L12: a token for a newer (invited) org resolves that org's membership."""
    from app.security.tokens import create_access_token

    client, _factory = account_client
    owner = _register(client, email="inviter@example.com", org="Inviting Org")
    created = client.post(
        "/organizations/invitations",
        headers=_auth(owner["tokens"]["access_token"]),
        json={"email": "joiner@example.com", "role": "viewer"},
    )
    invitation_id = created.json()["id"]
    inviting_org = uuid.UUID(created.json()["organization_id"])
    joiner = _register(client, email="joiner@example.com", org="Joiner Home Org")
    token = _extract_token_from_mock_email(account_settings, "organization_invitation")
    accept = client.post(
        f"/organizations/invitations/{invitation_id}/accept",
        headers=_auth(joiner["tokens"]["access_token"]),
        json={"token": token},
    )
    assert accept.status_code == 200, accept.text

    joiner_id = uuid.UUID(joiner["user"]["id"])
    second_org_token, _ = create_access_token(
        user_id=joiner_id,
        organization_id=inviting_org,
        email="joiner@example.com",
        settings=account_settings,
        actor="user",
    )
    me = client.get("/auth/me", headers=_auth(second_org_token))
    assert me.status_code == 200, me.text
    assert me.json()["organization"]["id"] == str(inviting_org)
    assert me.json()["membership_role"] == "viewer"

    # A token for an org the user is not a member of is still refused.
    stranger_token, _ = create_access_token(
        user_id=joiner_id,
        organization_id=uuid.uuid4(),
        email="joiner@example.com",
        settings=account_settings,
        actor="user",
    )
    assert client.get("/auth/me", headers=_auth(stranger_token)).status_code == 401


def test_invite_token_mismatch_audit_lands_in_caller_org(
    account_client: AccountClientFixture,
) -> None:
    """L12: a wrong invitation token is audited in the caller's org, not the inviter's."""
    client, factory = account_client
    owner = _register(client, email="victim-owner@example.com", org="Victim Org")
    created = client.post(
        "/organizations/invitations",
        headers=_auth(owner["tokens"]["access_token"]),
        json={"email": "guesser@example.com", "role": "viewer"},
    )
    invitation_id = created.json()["id"]
    victim_org = uuid.UUID(created.json()["organization_id"])
    guesser = _register(client, email="guesser@example.com", org="Guesser Org")
    guesser_org = uuid.UUID(guesser["organization"]["id"])
    wrong = client.post(
        f"/organizations/invitations/{invitation_id}/accept",
        headers=_auth(guesser["tokens"]["access_token"]),
        json={"token": "definitely-not-the-token"},
    )
    assert wrong.status_code == 401
    with factory() as session:
        rows = session.scalars(
            select(AuditLog).where(AuditLog.action == AuditEventType.AUTH_INVALID_TOKEN)
        ).all()
        mismatch = [
            r for r in rows if (r.redacted_metadata or {}).get("reason") == "invite_token_mismatch"
        ]
        assert mismatch, "expected an invite_token_mismatch audit row"
        assert {r.organization_id for r in mismatch} == {guesser_org}
        assert victim_org not in {r.organization_id for r in mismatch}
