"""production_demo profile: production mirrors staging, real money stays blocked."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.deployment_safety import deployment_posture
from app.core.exchange_safety import BLOFIN_DEMO_HOST_ALLOWLIST
from app.services.autonomous_trading import resolve_autonomous_mode
from app.telegram_activation.preflight import configuration_blockers
from app.workers.watcher_activation import activation_config_from_settings, staging_static_arm_ok

_DEMO_HOST = next(iter(BLOFIN_DEMO_HOST_ALLOWLIST))
_BASE = {
    "environment": "production",
    "deployment_profile": "production_demo",
    "jwt_secret": "x" * 32,
    "database_url": "postgresql+psycopg://user:pass@db.example.com:5432/alphatrade",
    "redis_url": "redis://redis.example.com:6379/0",
    "qdrant_url": "https://qdrant.example.com",
    "openai_api_key": "sk-test-not-a-real-key",
    "cors_origins": "https://app.example.com",
    "auth_refresh_cookie_enabled": True,
    "auth_cookie_secure": True,
    "auth_cookie_samesite": "none",
    "enable_real_trading": False,
    "execution_mode": "paper",
    "provider_mode": "fallback",
    "rate_limit_use_redis": True,
    "rate_limit_allow_in_memory_fallback": False,
    "trusted_proxy_hops": 1,
    "debug": False,
}
_DEMO_EXCHANGE = {
    "exchange_mode": "paper_exchange_demo",
    "blofin_demo_enabled": True,
    "blofin_api_key": "demo-key",
    "blofin_api_secret": "demo-secret",
    "blofin_api_passphrase": "demo-pass",
    "blofin_demo_rest_base_url": f"https://{_DEMO_HOST}",
    "blofin_demo_ws_url": f"wss://{_DEMO_HOST}/ws/public",
    "governed_blofin_demo_enabled": True,
    "governed_blofin_demo_organization_id": "00000000-0000-4000-8000-000000000001",
    "governed_blofin_demo_user_id": "00000000-0000-4000-8000-000000000002",
    "governed_blofin_demo_account_id": "00000000-0000-4000-8000-000000000003",
}
_WATCHER_TELEGRAM = {
    "perpetual_evidence_source": "binance_usdm",
    "watcher_orchestration_enabled": True,
    "watcher_paper_staging_activation": True,
    "telegram_interaction_enabled": True,
    "telegram_paper_activation_armed": True,
    "telegram_inbound_mode": "polling",
    "telegram_bot_id": "bot-100",
    "telegram_chat_id": "tg-chat-1",
    "telegram_bot_token": "123456789:AAHtestTokenValueForStagingPackage",
    "telegram_network_permitted": True,
}
_MIRROR = {**_BASE, **_DEMO_EXCHANGE, **_WATCHER_TELEGRAM, "autonomous_blofin_demo_enabled": True}


def test_full_staging_mirror_constructs_in_production() -> None:
    settings = Settings(**_MIRROR)
    assert settings.production_demo_active is True
    assert settings.paper_runtime_environment is True
    assert settings.exchange_demo_active is True
    assert settings.real_trading_enabled is False
    posture = deployment_posture(settings)
    assert posture["deployment_profile"] == "production_demo"
    assert posture["production_demo_active"] is True


def test_watcher_static_arm_and_telegram_preflight_open_under_profile() -> None:
    settings = Settings(**_MIRROR)
    assert activation_config_from_settings(settings).production_demo is True
    assert staging_static_arm_ok(settings) is True
    blockers = configuration_blockers(settings)
    assert "environment_forbidden" not in blockers


def test_autonomous_demo_not_refused_for_production_under_profile() -> None:
    resolution = resolve_autonomous_mode(Settings(**_MIRROR))
    assert resolution.refusal != "autonomous_production_refused"


def test_standard_production_keeps_api_only_posture() -> None:
    standard = {k: v for k, v in _MIRROR.items() if k != "deployment_profile"}
    with pytest.raises(ValidationError):
        Settings(**standard)
    with pytest.raises(ValidationError, match="paper_exchange_demo is not allowed"):
        Settings(**{**_BASE, **_DEMO_EXCHANGE, "deployment_profile": "standard"})


@pytest.mark.parametrize(
    "override",
    [
        {"enable_real_trading": True},
        {"execution_mode": "trade", "enable_real_trading": True},
        {"exchange_mode": "trade_live"},
        {"blofin_demo_rest_base_url": "https://openapi.blofin.com"},
        {"blofin_demo_rest_base_url": "https://evil.example.com"},
        {"debug": True},
        {"telegram_alerts_enabled": True},
    ],
    ids=lambda o: next(iter(o)),
)
def test_real_money_and_unsafe_overrides_still_refuse(override: dict) -> None:
    with pytest.raises(ValidationError):
        Settings(**{**_MIRROR, **override})


def test_profile_refused_outside_production() -> None:
    with pytest.raises(ValidationError, match="requires environment=production"):
        Settings(**{**_MIRROR, "environment": "staging"})
    with pytest.raises(ValidationError, match="requires environment=production"):
        Settings(environment="local", deployment_profile="production_demo")
