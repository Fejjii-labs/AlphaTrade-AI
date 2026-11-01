"""Telegram paper projection on the governed staging BloFin demo worker."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.controlled_activation.profile import (
    controlled_telegram_projection,
    telegram_enrollment_runtime,
)
from app.core.config import Settings
from app.core.deployment_safety import _telegram_activation_errors
from tests.test_controlled_paper_activation import _STAGING

_ORG = str(uuid.uuid4())
_USER = str(uuid.uuid4())
_ACCOUNT = str(uuid.uuid4())

_DEMO: dict[str, object] = {
    "exchange_mode": "paper_exchange_demo",
    "blofin_demo_enabled": True,
    "blofin_api_key": "fixture-demo-key",
    "blofin_api_secret": "fixture-demo-secret",
    "blofin_api_passphrase": "fixture-demo-pass",
    "blofin_demo_rest_base_url": "https://demo-trading-openapi.blofin.com",
    "governed_blofin_demo_enabled": True,
    "governed_blofin_demo_organization_id": _ORG,
    "governed_blofin_demo_user_id": _USER,
    "governed_blofin_demo_account_id": _ACCOUNT,
    "autonomous_blofin_demo_enabled": True,
}

_TELEGRAM: dict[str, object] = {
    "watcher_orchestration_enabled": True,
    "watcher_paper_staging_activation": True,
    "telegram_interaction_enabled": True,
    "telegram_paper_activation_armed": True,
    "telegram_inbound_mode": "polling",
    "telegram_bot_id": "123456789",
    "telegram_chat_id": "tg-chat-1",
    "telegram_bot_token": "123456789:AAHtestTokenValueForStagingPackage",
    "telegram_network_permitted": True,
}


def _settings(**overrides: object) -> Settings:
    return Settings(**{**_STAGING, **_DEMO, **_TELEGRAM, **overrides})


def test_demo_worker_projection_is_allowed() -> None:
    settings = _settings()
    assert settings.exchange_demo_active is True
    assert settings.real_trading_enabled is False
    assert controlled_telegram_projection(settings) is True
    assert _telegram_activation_errors(settings) == []


def test_demo_worker_enrollment_is_allowed() -> None:
    settings = _settings(telegram_paper_activation_armed=False, telegram_chat_id="")
    assert controlled_telegram_projection(settings) is False
    assert telegram_enrollment_runtime(settings) is True
    assert _telegram_activation_errors(settings) == []


def test_demo_without_governed_pin_is_refused() -> None:
    with pytest.raises(ValidationError, match="telegram"):
        _settings(governed_blofin_demo_enabled=False)


def test_demo_with_non_demo_host_is_refused() -> None:
    with pytest.raises(ValidationError):
        _settings(blofin_demo_rest_base_url="https://openapi.blofin.com")


def test_trade_live_is_refused() -> None:
    with pytest.raises(ValidationError):
        _settings(exchange_mode="trade_live")


def test_real_trading_is_refused() -> None:
    with pytest.raises(ValidationError):
        _settings(enable_real_trading=True)


def test_production_is_refused() -> None:
    with pytest.raises(ValidationError):
        _settings(environment="production")


def test_legacy_alerts_still_refused_in_demo() -> None:
    with pytest.raises(ValidationError, match="telegram_alerts_enabled"):
        _settings(telegram_alerts_enabled=True)
