"""Tests for settings loading and trading-safety invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import ExecutionMode, Settings
from app.services.paper_fill import DEFAULT_PAPER_FILL_SPREAD_HAIRCUT
from app.strategy_brain.sfp.contracts import SFP_MIN_STOP_ATR_FLOOR


def test_defaults_are_safe() -> None:
    settings = Settings()
    assert settings.execution_mode is ExecutionMode.PAPER
    assert settings.enable_real_trading is False
    assert settings.real_trading_enabled is False
    assert settings.provider_mode == "mock"
    assert settings.observability_strict_mode is False
    assert settings.telegram_alerts_enabled is False
    assert settings.telegram_interaction_enabled is False
    assert settings.telegram_paper_activation_armed is False
    assert settings.telegram_inbound_mode.value == "off"
    assert settings.telegram_network_permitted is False
    assert settings.telegram_webhook_secret == ""
    assert settings.telegram_bot_id == ""
    assert settings.telegram_chat_id == ""


def test_paper_fill_and_sfp_stop_floor_defaults_are_nonzero() -> None:
    from decimal import Decimal

    settings = Settings()
    assert settings.paper_fill_spread_haircut == DEFAULT_PAPER_FILL_SPREAD_HAIRCUT
    assert settings.paper_fill_spread_haircut == Decimal("0.0001")
    assert settings.sfp_min_stop_atr_multiple == SFP_MIN_STOP_ATR_FLOOR
    assert settings.sfp_min_stop_atr_multiple == Decimal("0.5")
    assert settings.enable_real_trading is False
    with pytest.raises(ValidationError):
        Settings(paper_fill_spread_haircut=Decimal("-0.0001"))
    with pytest.raises(ValidationError):
        Settings(sfp_min_stop_atr_multiple=Decimal("100"))


def test_cors_origins_accepts_comma_separated_string() -> None:
    settings = Settings(cors_origins="http://a.test, http://b.test")
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_cors_origins_loads_from_env_file(tmp_path, monkeypatch) -> None:
    """Fresh-clone setup copies `.env.example`; comma-separated CORS must parse."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CORS_ORIGINS", "")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    class EnvFileSettings(Settings):
        model_config = Settings.model_config | {"env_file": str(env_file)}

    settings = EnvFileSettings()
    assert settings.cors_origins == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_auth_cookie_samesite_normalized() -> None:
    settings = Settings(auth_cookie_samesite="None")
    assert settings.auth_cookie_samesite == "none"


def test_trade_mode_is_permanently_rejected() -> None:
    with pytest.raises(ValidationError, match="execution_mode=trade is permanently rejected"):
        Settings(execution_mode="trade", enable_real_trading=False)


def test_enable_real_trading_true_is_permanently_rejected() -> None:
    with pytest.raises(ValidationError, match="ENABLE_REAL_TRADING=true is permanently rejected"):
        Settings(execution_mode="paper", enable_real_trading=True)


def test_real_trading_enabled_cannot_be_constructed() -> None:
    with pytest.raises(ValidationError, match="permanently rejected"):
        Settings(execution_mode="trade", enable_real_trading=True)


def test_redis_url_normalizes_redis_cli_wrapper() -> None:
    settings = Settings(
        redis_url="redis-cli --tls -u rediss://default:token@host.upstash.io:6379",
    )
    assert settings.redis_url == "rediss://default:token@host.upstash.io:6379"
