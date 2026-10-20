"""Tests for staging/production deployment safety invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Environment, Settings
from app.core.deployment_safety import deployment_posture, validate_deployment_settings

_STAGING_BASE = {
    "environment": "staging",
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

_PRODUCTION_BASE = {**_STAGING_BASE, "environment": "production"}


def test_staging_valid_settings_pass() -> None:
    settings = Settings(**_STAGING_BASE)
    assert settings.environment.value == "staging"
    assert settings.real_trading_enabled is False


def test_production_rejects_weak_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="jwt_secret"):
        Settings(**{**_STAGING_BASE, "environment": "production", "jwt_secret": "short"})


def test_staging_rejects_known_weak_jwt_placeholder() -> None:
    with pytest.raises(ValidationError, match="weak placeholder"):
        Settings(
            **{
                **_STAGING_BASE,
                "jwt_secret": "change-me-in-production-use-long-random-value",
            }
        )


def test_staging_rejects_missing_managed_database_url() -> None:
    with pytest.raises(ValidationError, match="database_url"):
        Settings(**{**_STAGING_BASE, "database_url": "postgresql+psycopg://u:p@localhost:5432/db"})


def test_staging_rejects_unsafe_cookie_config() -> None:
    with pytest.raises(ValidationError, match="auth_cookie_secure"):
        Settings(**{**_STAGING_BASE, "auth_cookie_secure": False})


def test_staging_rejects_cookie_mode_disabled() -> None:
    with pytest.raises(ValidationError, match="auth_refresh_cookie_enabled"):
        Settings(**{**_STAGING_BASE, "auth_refresh_cookie_enabled": False})


def test_staging_rejects_real_trading_enabled() -> None:
    with pytest.raises(ValidationError, match="ENABLE_REAL_TRADING=true"):
        Settings(**{**_STAGING_BASE, "enable_real_trading": True})


def test_staging_rejects_trade_execution_mode() -> None:
    with pytest.raises(ValidationError, match="execution_mode=trade"):
        Settings(**{**_STAGING_BASE, "execution_mode": "trade", "enable_real_trading": True})


def test_staging_requires_secure_cookies() -> None:
    with pytest.raises(ValidationError, match="auth_cookie_secure"):
        Settings(**{**_STAGING_BASE, "auth_cookie_secure": False})


def test_staging_requires_strong_jwt_secret() -> None:
    with pytest.raises(ValueError, match="jwt_secret must be at least"):
        Settings(**{**_STAGING_BASE, "jwt_secret": "short"})


def test_staging_validates_cors_origins_https() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        Settings(**{**_STAGING_BASE, "cors_origins": "http://app.example.com"})


def test_staging_rejects_localhost_cors_origin() -> None:
    with pytest.raises(ValueError, match="localhost"):
        Settings(**{**_STAGING_BASE, "cors_origins": "https://localhost:3000"})


def test_staging_accepts_provider_fallback_mode() -> None:
    settings = Settings(**{**_STAGING_BASE, "provider_mode": "fallback"})
    assert settings.provider_mode == "fallback"
    validate_deployment_settings(settings)


def test_staging_requires_qdrant_url() -> None:
    with pytest.raises(ValidationError, match="qdrant_url"):
        Settings(**{**_STAGING_BASE, "qdrant_url": ""})


def test_staging_requires_openai_api_key() -> None:
    with pytest.raises(ValidationError, match="openai_api_key"):
        Settings(**{**_STAGING_BASE, "openai_api_key": ""})


def test_production_requires_qdrant_url() -> None:
    with pytest.raises(ValidationError, match="qdrant_url"):
        Settings(**{**_PRODUCTION_BASE, "qdrant_url": ""})


def test_normalizes_render_postgres_database_url() -> None:
    settings = Settings(
        database_url="postgres://user:pass@dpg.example.com:5432/alphatrade",
        environment=Environment.LOCAL,
    )
    assert settings.database_url.startswith("postgresql+psycopg://")


def test_production_rejects_either_autonomous_flag() -> None:
    with pytest.raises(ValidationError, match="autonomous_internal_paper_enabled"):
        Settings(**{**_PRODUCTION_BASE, "autonomous_internal_paper_enabled": True})
    with pytest.raises(ValidationError, match="autonomous_blofin_demo_enabled"):
        Settings(**{**_PRODUCTION_BASE, "autonomous_blofin_demo_enabled": True})


def test_production_rejects_paper_exit_monitor() -> None:
    with pytest.raises(ValidationError, match="paper_exit_monitor_enabled"):
        Settings(**{**_PRODUCTION_BASE, "paper_exit_monitor_enabled": True})


def test_staging_accepts_paper_exit_monitor_flag() -> None:
    settings = Settings(**{**_STAGING_BASE, "paper_exit_monitor_enabled": True})
    assert settings.paper_exit_monitor_enabled is True
    assert settings.paper_exit_monitor_dry_run is False
    validate_deployment_settings(settings)


def test_staging_accepts_an_autonomous_internal_paper_flag() -> None:
    settings = Settings(**{**_STAGING_BASE, "autonomous_internal_paper_enabled": True})
    assert settings.autonomous_internal_paper_enabled is True
    assert settings.autonomous_blofin_demo_enabled is False
    validate_deployment_settings(settings)


def test_production_rejects_debug_mode() -> None:
    with pytest.raises(ValidationError, match="debug"):
        Settings(**{**_STAGING_BASE, "environment": "production", "debug": True})


def test_deployment_posture_excludes_secrets() -> None:
    settings = Settings(
        **{
            **_STAGING_BASE,
            "openai_api_key": "sk-secret",
            "qdrant_api_key": "qdrant-secret",
        }
    )
    posture = deployment_posture(settings)
    assert "sk-secret" not in str(posture)
    assert "qdrant-secret" not in str(posture)
    assert posture["openai_configured"] is True
    assert posture["qdrant_api_key_configured"] is True
    assert "jwt_secret" not in posture
    assert "qdrant_api_key" not in posture


def test_staging_rejects_provider_mode_mock() -> None:
    with pytest.raises(ValidationError, match="provider_mode=mock"):
        Settings(**{**_STAGING_BASE, "provider_mode": "mock"})


# --- AT-018: proxy trust + Redis-required rate limits + fail-closed denylist ---


def test_staging_rejects_in_memory_rate_limit_fallback() -> None:
    with pytest.raises(ValidationError, match="rate_limit_allow_in_memory_fallback"):
        Settings(**{**_STAGING_BASE, "rate_limit_allow_in_memory_fallback": True})


def test_staging_rejects_raised_register_rate_limit() -> None:
    with pytest.raises(ValidationError, match="auth_register_rate_limit"):
        Settings(**{**_STAGING_BASE, "auth_register_rate_limit": 200})


def test_production_rejects_in_memory_rate_limit_fallback() -> None:
    with pytest.raises(ValidationError, match="rate_limit_allow_in_memory_fallback"):
        Settings(**{**_PRODUCTION_BASE, "rate_limit_allow_in_memory_fallback": True})


def test_staging_rejects_zero_trusted_proxy_hops() -> None:
    with pytest.raises(ValidationError, match="trusted_proxy_hops"):
        Settings(**{**_STAGING_BASE, "trusted_proxy_hops": 0})


def test_staging_rejects_denylist_disabled() -> None:
    with pytest.raises(ValidationError, match="access_token_denylist_enabled"):
        Settings(**{**_STAGING_BASE, "access_token_denylist_enabled": False})


def test_staging_rejects_denylist_without_redis() -> None:
    with pytest.raises(ValidationError, match="access_token_denylist_use_redis"):
        Settings(**{**_STAGING_BASE, "access_token_denylist_use_redis": False})


def test_staging_rejects_denylist_fail_open() -> None:
    with pytest.raises(ValidationError, match="access_token_denylist_fail_closed"):
        Settings(**{**_STAGING_BASE, "access_token_denylist_fail_closed": False})


def test_local_allows_memory_fallback_and_zero_proxy_hops() -> None:
    settings = Settings(
        environment="local",
        rate_limit_allow_in_memory_fallback=True,
        trusted_proxy_hops=0,
    )
    validate_deployment_settings(settings)  # no raise


def test_deployment_posture_reports_at018_hardening() -> None:
    settings = Settings(**_STAGING_BASE)
    posture = deployment_posture(settings)
    assert posture["rate_limit_allow_in_memory_fallback"] is False
    assert posture["access_token_denylist_enabled"] is True
    assert posture["access_token_denylist_fail_closed"] is True
    assert posture["trusted_proxy_hops"] == 1
    assert posture["telegram_interaction_enabled"] is False
    assert posture["telegram_alerts_enabled"] is False
    assert posture["automatic_telegram_delivery_enabled"] is False
    assert posture["market_watcher_enabled"] is False
    assert posture["market_watcher_bridge_enabled"] is False
    assert posture["watcher_orchestration_enabled"] is False
    assert posture["real_trading_enabled"] is False
    assert posture["autonomous_internal_paper_enabled"] is False
    assert posture["autonomous_blofin_demo_enabled"] is False
    assert posture["paper_exit_monitor_enabled"] is False
    assert posture["paper_exit_monitor_dry_run"] is False
    assert posture["paper_exit_monitor_degraded_alert_after_seconds"] == 900
    assert posture["paper_exit_monitor_max_clock_skew_seconds"] == 15


def test_staging_rejects_market_watcher_enabled() -> None:
    with pytest.raises(ValidationError, match="market_watcher_enabled"):
        Settings(**{**_STAGING_BASE, "market_watcher_enabled": True})


def test_staging_rejects_watcher_orchestration_enabled() -> None:
    with pytest.raises(ValidationError, match="watcher_orchestration_enabled"):
        Settings(**{**_STAGING_BASE, "watcher_orchestration_enabled": True})


def test_staging_rejects_activation_arm_without_orchestration() -> None:
    with pytest.raises(ValidationError, match="watcher_paper_staging_activation"):
        Settings(**{**_STAGING_BASE, "watcher_paper_staging_activation": True})


def test_staging_armed_replay_evidence_is_rejected() -> None:
    with pytest.raises(ValidationError, match="replay evidence"):
        Settings(
            **{
                **_STAGING_BASE,
                "watcher_orchestration_enabled": True,
                "watcher_paper_staging_activation": True,
                "perpetual_evidence_source": "replay",
            }
        )


def test_staging_armed_live_evidence_can_construct_settings() -> None:
    settings = Settings(
        **{
            **_STAGING_BASE,
            "watcher_orchestration_enabled": True,
            "watcher_paper_staging_activation": True,
            "perpetual_evidence_source": "binance_usdm",
        }
    )
    assert settings.watcher_paper_staging_activation is True
    assert settings.enable_real_trading is False
    assert settings.real_trading_enabled is False
    assert settings.telegram_alerts_enabled is False


def test_production_rejects_paper_activation_arm() -> None:
    with pytest.raises(ValidationError, match="watcher_paper_staging_activation"):
        Settings(**{**_PRODUCTION_BASE, "watcher_paper_staging_activation": True})


def test_staging_rejects_telegram_alerts_enabled() -> None:
    with pytest.raises(ValidationError, match="telegram_alerts_enabled"):
        Settings(**{**_STAGING_BASE, "telegram_alerts_enabled": True})


def test_staging_rejects_telegram_interaction_enabled() -> None:
    with pytest.raises(ValidationError, match="telegram_interaction_enabled"):
        Settings(**{**_STAGING_BASE, "telegram_interaction_enabled": True})


def test_staging_rejects_telegram_paper_activation_armed() -> None:
    with pytest.raises(ValidationError, match="telegram_paper_activation_armed"):
        Settings(**{**_STAGING_BASE, "telegram_paper_activation_armed": True})


def test_staging_rejects_telegram_inbound_mode() -> None:
    with pytest.raises(ValidationError, match="telegram_inbound_mode"):
        Settings(**{**_STAGING_BASE, "telegram_inbound_mode": "polling"})


def test_staging_rejects_telegram_network_permitted() -> None:
    with pytest.raises(ValidationError, match="telegram_network_permitted"):
        Settings(**{**_STAGING_BASE, "telegram_network_permitted": True})


def test_staging_rejects_telegram_webhook_secret() -> None:
    with pytest.raises(ValidationError, match="telegram_webhook_secret"):
        Settings(**{**_STAGING_BASE, "telegram_webhook_secret": "x" * 32})


def test_production_rejects_telegram_paper_activation() -> None:
    with pytest.raises(ValidationError, match="telegram_paper_activation_armed"):
        Settings(**{**_PRODUCTION_BASE, "telegram_paper_activation_armed": True})


def test_staging_rejects_automatic_telegram_delivery_enabled() -> None:
    with pytest.raises(ValidationError, match="automatic_telegram_delivery_enabled"):
        Settings(**{**_STAGING_BASE, "automatic_telegram_delivery_enabled": True})


def test_production_rejects_watcher_orchestration_enabled() -> None:
    with pytest.raises(ValidationError, match="watcher_orchestration_enabled"):
        Settings(**{**_PRODUCTION_BASE, "watcher_orchestration_enabled": True})


def test_staging_controlled_package_can_construct() -> None:
    settings = Settings(
        **{
            **_STAGING_BASE,
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
    )
    assert settings.telegram_paper_activation_armed is True
    assert settings.enable_real_trading is False
    assert settings.real_trading_enabled is False
    assert settings.telegram_alerts_enabled is False
    validate_deployment_settings(settings)


def test_production_rejects_controlled_package() -> None:
    with pytest.raises(ValidationError, match="telegram_interaction_enabled"):
        Settings(
            **{
                **_PRODUCTION_BASE,
                "perpetual_evidence_source": "replay",
                "watcher_orchestration_enabled": True,
                "watcher_paper_staging_activation": True,
                "telegram_interaction_enabled": True,
                "telegram_paper_activation_armed": True,
                "telegram_inbound_mode": "polling",
                "telegram_bot_id": "bot-100",
                "telegram_chat_id": "tg-chat-1",
            }
        )


def test_production_rejects_telegram_interaction_enabled() -> None:
    with pytest.raises(ValidationError, match="telegram_interaction_enabled"):
        Settings(**{**_PRODUCTION_BASE, "telegram_interaction_enabled": True})


def test_local_still_allows_watcher_and_telegram_flags() -> None:
    settings = Settings(
        environment="local",
        market_watcher_enabled=True,
        telegram_alerts_enabled=True,
        watcher_orchestration_enabled=True,
    )
    validate_deployment_settings(settings)
    assert settings.market_watcher_enabled is True
    assert settings.real_trading_enabled is False


def test_validate_deployment_skips_local() -> None:
    settings = Settings()
    validate_deployment_settings(settings)  # no raise


_DEMO_HOST = "demo-trading-openapi.blofin.com"
_DEMO_EXCHANGE = {
    "exchange_mode": "paper_exchange_demo",
    "blofin_demo_enabled": True,
    "blofin_api_key": "demo-key",
    "blofin_api_secret": "demo-secret",
    "blofin_api_passphrase": "demo-pass",
    "blofin_demo_rest_base_url": f"https://{_DEMO_HOST}",
}


def test_staging_allows_paper_exchange_demo() -> None:
    settings = Settings(**{**_STAGING_BASE, **_DEMO_EXCHANGE})
    assert settings.exchange_demo_active is True
    validate_deployment_settings(settings)  # no raise


def test_production_rejects_paper_exchange_demo() -> None:
    with pytest.raises(ValidationError, match="paper_exchange_demo is not allowed in production"):
        Settings(**{**_PRODUCTION_BASE, **_DEMO_EXCHANGE})


def test_deployment_posture_reports_exchange_axis_without_secrets() -> None:
    settings = Settings(**{**_STAGING_BASE, **_DEMO_EXCHANGE})
    posture = deployment_posture(settings)
    assert posture["exchange_mode"] == "paper_exchange_demo"
    assert posture["exchange_demo_active"] is True
    assert posture["blofin_demo_configured"] is True
    assert "demo-secret" not in str(posture)
    assert "demo-pass" not in str(posture)
