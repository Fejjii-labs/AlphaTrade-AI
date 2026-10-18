"""Access-token actor claims."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import Settings
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
