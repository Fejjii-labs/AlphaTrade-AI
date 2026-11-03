"""production_demo profile: production mirrors staging, real money stays blocked."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import DeploymentProfile, Environment, ExchangeMode, Settings
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
        {"automatic_telegram_delivery_enabled": True},
        {"market_watcher_enabled": True},
        {"market_watcher_bridge_enabled": True},
        {"market_watcher_bridge_auto_tick": True},
        {"telegram_webhook_secret": "hook"},
        {"perpetual_evidence_source": "replay"},
        {"provider_mode": "mock"},
        {"jwt_secret": "changeme"},
        {"rate_limit_allow_in_memory_fallback": True},
        {"access_token_denylist_fail_closed": False},
        {"trusted_proxy_hops": 0},
        {"cors_origins": "http://app.example.com"},
        {"auth_cookie_secure": False},
        {"database_url": "postgresql+psycopg://u:p@localhost:5432/db"},
        {"require_email_verified": False},
        {"demo_seed_enabled": True},
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


@pytest.mark.parametrize(
    "override", [{"require_email_verified": False}, {"demo_seed_enabled": True}]
)
def test_standard_production_enforces_email_verification_and_no_demo_seed(override: dict) -> None:
    with pytest.raises(ValidationError):
        Settings(**{**_BASE, "deployment_profile": "standard", **override})


def test_profile_property_alone_needs_production_and_paper() -> None:
    base = Settings()
    assert base.production_demo_active is False
    for env, profile, expected in [
        ("production", "production_demo", True),
        ("production", "standard", False),
        ("staging", "standard", False),
        ("local", "standard", False),
    ]:
        probe = base.model_copy(
            update={
                "environment": Environment(env),
                "deployment_profile": DeploymentProfile(profile),
            }
        )
        assert probe.production_demo_active is expected, (env, profile)
    live = base.model_copy(
        update={
            "environment": Environment.PRODUCTION,
            "deployment_profile": DeploymentProfile.PRODUCTION_DEMO,
            "enable_real_trading": True,
        }
    )
    assert live.production_demo_active is False
    assert live.paper_runtime_environment is False


@pytest.mark.parametrize(
    ("attr", "url", "fragment"),
    [
        ("blofin_demo_rest_base_url", "https://openapi.blofin.com", "BloFin live host"),
        ("blofin_demo_ws_url", "wss://openapi.blofin.com/ws/public", "BloFin live host"),
        ("blofin_demo_rest_base_url", "https://evil.example.com", "allowlisted BloFin demo"),
    ],
)
def test_profile_blofin_host_pins_directly(attr: str, url: str, fragment: str) -> None:
    from app.core.deployment_safety import _production_demo_errors

    probe = Settings().model_copy(
        update={
            "environment": Environment.PRODUCTION,
            "deployment_profile": DeploymentProfile.PRODUCTION_DEMO,
            **{k: v for k, v in _DEMO_EXCHANGE.items() if not k.startswith("blofin_api")},
            "exchange_mode": ExchangeMode.PAPER_EXCHANGE_DEMO,
            attr: url,
        }
    )
    assert any(fragment in error for error in _production_demo_errors(probe))
