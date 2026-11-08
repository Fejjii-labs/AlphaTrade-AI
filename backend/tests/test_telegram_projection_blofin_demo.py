"""Telegram paper projection on the governed staging BloFin demo worker."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.controlled_activation.profile import (
    _paper_exchange_allowed,
    controlled_telegram_projection,
    telegram_enrollment_runtime,
)
from app.core.config import ExchangeMode, Settings, TelegramInboundMode
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


# --- Direct gate tests (Griller C1/C2) -------------------------------------
# Settings(...) validation refuses every bad combination before the gate runs,
# so these build a valid demo Settings and then bypass validation with
# model_copy(update=...). The outcome then depends on _paper_exchange_allowed.

_GATE_NEGATIVE: dict[str, dict[str, object]] = {
    "pin_off": {"governed_blofin_demo_enabled": False},
    # Manual API capability satisfies the BloFin gate's live-evidence clause,
    # so only the governed-pin check refuses this one.
    "manual_pin_instead_of_governed": {
        "governed_blofin_demo_enabled": False,
        "manual_blofin_demo_enabled": True,
    },
    "pin_invalid_uuid": {"governed_blofin_demo_account_id": "not-a-uuid"},
    "prod_host": {"blofin_demo_rest_base_url": "https://openapi.blofin.com"},
    "demo_flag_off": {"blofin_demo_enabled": False},
    "trade_live": {"exchange_mode": ExchangeMode.TRADE_LIVE},
    "enable_real_trading": {"enable_real_trading": True},
}


def _enrollment_base() -> Settings:
    return _settings(telegram_paper_activation_armed=False, telegram_chat_id="")


def test_gate_allows_full_demo_and_internal_paper() -> None:
    settings = _settings()
    assert _paper_exchange_allowed(settings) is True
    internal = settings.model_copy(update={"exchange_mode": ExchangeMode.PAPER_INTERNAL})
    assert _paper_exchange_allowed(internal) is True


@pytest.mark.parametrize("case", sorted(_GATE_NEGATIVE))
def test_gate_refuses_directly(case: str) -> None:
    settings = _settings().model_copy(update=_GATE_NEGATIVE[case])
    assert _paper_exchange_allowed(settings) is False


def test_gate_refuses_pin_alone_without_blofin_authorization() -> None:
    # Pin holds, BloFin gate fails: an and->or mutation would return True.
    settings = _settings().model_copy(update={"blofin_demo_enabled": False})
    assert _paper_exchange_allowed(settings) is False


def test_gate_refuses_blofin_authorization_alone_without_pin() -> None:
    # BloFin gate holds, pin fails: an and->or mutation would return True.
    settings = _settings().model_copy(update={"governed_blofin_demo_enabled": False})
    assert _paper_exchange_allowed(settings) is False


def test_enrollment_base_is_allowed() -> None:
    assert telegram_enrollment_runtime(_enrollment_base()) is True


@pytest.mark.parametrize("case", sorted(_GATE_NEGATIVE))
def test_enrollment_refused_by_gate_without_watcher_arm(case: str) -> None:
    # telegram_enrollment_runtime never calls controlled_watcher_arm, so the
    # exchange gate is its only profile-level exchange check (C2).
    settings = _enrollment_base().model_copy(update=_GATE_NEGATIVE[case])
    assert telegram_enrollment_runtime(settings) is False
    assert controlled_telegram_projection(settings) is False


def test_manual_pin_passes_blofin_gate_but_not_exchange_gate() -> None:
    from app.core.execution_credentials import blofin_execution_authorized

    settings = _settings().model_copy(
        update={"governed_blofin_demo_enabled": False, "manual_blofin_demo_enabled": True}
    )
    assert blofin_execution_authorized(settings) is True
    assert _paper_exchange_allowed(settings) is False


# --- Coexistence on the worker: Telegram and BloFin demo trades together ---------


def test_worker_runs_telegram_projection_and_demo_trades_together() -> None:
    """One staging worker config arms both: the projection and governed demo orders."""

    from app.core.deployment_safety import validate_deployment_settings
    from app.market_activation.profile import live_market_activation_violations
    from app.services.autonomous_trading import AutonomousMode, resolve_autonomous_mode
    from app.services.governed_blofin_demo import demo_posture_refusal
    from app.telegram_activation.preflight import configuration_blockers
    from app.telegram_activation.runtime import resolve_telegram_posture
    from app.workers.paper_worker import isolate_runtime_settings
    from app.workers.watcher_activation import staging_static_arm_ok

    settings = _settings()
    validate_deployment_settings(settings)
    assert live_market_activation_violations(settings) == []
    watcher, telegram = isolate_runtime_settings(settings)
    # Telegram side: projection posture with no preflight blocker.
    assert resolve_telegram_posture(telegram) == "projection"
    assert configuration_blockers(telegram) == ()
    # Demo side: the Watcher arm holds and the governed demo loop is not refused.
    assert staging_static_arm_ok(watcher) is True
    assert demo_posture_refusal(watcher) is None
    assert resolve_autonomous_mode(watcher).mode is AutonomousMode.BLOFIN_DEMO
    # Real money stays off on both copies.
    for copy in (watcher, telegram):
        assert copy.enable_real_trading is False
        assert copy.real_trading_enabled is False


def test_incomplete_demo_pin_names_the_missing_gate() -> None:
    with pytest.raises(ValidationError) as exc:
        _settings(governed_blofin_demo_account_id="not-a-uuid")
    text = str(exc.value)
    assert "telegram on exchange_mode=paper_exchange_demo requires the governed demo" in text
    # The original refusals are still reported; nothing is relaxed.
    assert "telegram_interaction_enabled must be false in staging/production" in text


def test_demo_host_outside_allowlist_names_the_blofin_gate() -> None:
    settings = _settings().model_copy(
        update={"blofin_demo_rest_base_url": "https://openapi.blofin.com"}
    )
    errors = _telegram_activation_errors(settings)
    assert any("BloFin demo execution gate" in error for error in errors)
    assert "telegram_interaction_enabled must be false in staging/production" in errors


def test_demo_reason_is_absent_for_internal_paper_and_disarmed_telegram() -> None:
    internal = _settings().model_copy(
        update={
            "exchange_mode": ExchangeMode.PAPER_INTERNAL,
            "watcher_orchestration_enabled": False,
        }
    )
    assert not any("paper_exchange_demo" in e for e in _telegram_activation_errors(internal))
    quiet = _settings().model_copy(
        update={
            "governed_blofin_demo_enabled": False,
            "telegram_interaction_enabled": False,
            "telegram_paper_activation_armed": False,
            "telegram_network_permitted": False,
            "telegram_inbound_mode": TelegramInboundMode.OFF,
        }
    )
    assert _telegram_activation_errors(quiet) == []


def test_coexistence_keeps_the_real_money_refusals() -> None:
    for override in (
        {"enable_real_trading": True},
        {"exchange_mode": "trade_live"},
        {"blofin_demo_rest_base_url": "https://openapi.blofin.com"},
        {"environment": "production"},
        {"telegram_alerts_enabled": True},
    ):
        with pytest.raises(ValidationError):
            _settings(**override)
