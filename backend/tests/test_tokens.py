"""Access-token actor claims."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import Settings
from app.core.errors import AuthError
from app.security.tokens import create_access_token, decode_access_token


def _settings() -> Settings:
    return Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="test-secret-key-for-auth-slice-32b-min",
        access_token_expire_minutes=15,
        refresh_token_expire_days=7,
        access_token_denylist_use_redis=False,
    )


def test_human_access_tokens_are_stamped_user_and_other_actors_keep_theirs() -> None:
    settings = _settings()
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    def issue(actor: str = "user") -> dict[str, object]:
        token, _expires = create_access_token(
            user_id=user_id,
            organization_id=organization_id,
            email="owner@example.com",
            settings=settings,
            actor=actor,
        )
        return decode_access_token(token, settings)

    default = decode_access_token(
        create_access_token(
            user_id=user_id,
            organization_id=organization_id,
            email="owner@example.com",
            settings=settings,
        )[0],
        settings,
    )
    assert default["actor"] == "user"
    assert default["type"] == "access"
    assert issue("user")["actor"] == "user"
    assert issue("agent")["actor"] == "agent"
    assert issue("bot")["actor"] == "bot"
    assert issue("service")["actor"] == "service"
    with pytest.raises(ValueError, match="access token actor"):
        issue("")
    with pytest.raises(ValueError, match="access token actor"):
        issue("undeclared")


def test_access_token_rejects_missing_future_and_non_numeric_time_and_bindings() -> None:
    settings = _settings()
    user_id = str(uuid.uuid4())
    organization_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    def encoded(claims: dict[str, object]) -> str:
        token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
        assert isinstance(token, str)
        return token

    valid = {
        "sub": user_id,
        "org_id": organization_id,
        "type": "access",
        "actor": "user",
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "iat": int(now.timestamp()),
    }
    assert decode_access_token(encoded(valid), settings)["sub"] == user_id
    for drop in ("exp", "iat", "sub", "org_id"):
        omitted = dict(valid)
        del omitted[drop]
        with pytest.raises(AuthError):
            decode_access_token(encoded(omitted), settings)
    future = dict(valid)
    future["iat"] = int((now + timedelta(minutes=5)).timestamp())
    with pytest.raises(AuthError):
        decode_access_token(encoded(future), settings)
    for bad_exp in ("later", True):
        with pytest.raises(AuthError):
            decode_access_token(encoded({**valid, "exp": bad_exp}), settings)
    with pytest.raises(AuthError):
        decode_access_token(encoded({**valid, "sub": ""}), settings)
    with pytest.raises(AuthError):
        decode_access_token(encoded({**valid, "org_id": 12345}), settings)
    with pytest.raises(AuthError):
        decode_access_token(encoded({**valid, "actor": ["user"]}), settings)
    with pytest.raises(AuthError):
        decode_access_token(
            encoded({**valid, "type": "risk_policy_step_up"}),
            settings,
        )
