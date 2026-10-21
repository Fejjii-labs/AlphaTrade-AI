"""Telegram safety-alert allowlist and paper exit informational bodies."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.providers.alert_delivery.base import AlertDeliveryResult
from app.providers.alert_delivery.telegram import TelegramAlertDeliveryProvider
from app.schemas.common import AlertDeliveryChannel, TradeDirection
from app.services.paper_exit_monitor import ExitMonitorAlert
from app.services.paper_exit_telegram import (
    format_paper_exit_close_telegram_text,
    realized_r_multiple_label,
)
from app.services.safety_alerts import (
    deliver_safety_alert,
    reset_safety_alert_dedup,
    safety_alert_telegram_allowed,
)

_NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
_TOKEN = "123456789:AAFakeTokenForTests"
_CHAT = "123456789"


def test_default_allowlist_blocks_logs_only_monitor_codes() -> None:
    settings = Settings(_env_file=None)
    assert safety_alert_telegram_allowed(settings, "kill_switch_activated") is True
    assert safety_alert_telegram_allowed(settings, "stop_hit") is True
    assert safety_alert_telegram_allowed(settings, "worker_heartbeat_missing") is True
    assert safety_alert_telegram_allowed(settings, "exit_monitor_heartbeat_stale") is True
    assert safety_alert_telegram_allowed(settings, "degraded_repeat") is False
    assert safety_alert_telegram_allowed(settings, "clock_skew") is False
    assert safety_alert_telegram_allowed(settings, "arming_refused") is False


def test_mandatory_codes_stay_allowed_when_env_extra_is_empty() -> None:
    settings = Settings(_env_file=None, telegram_safety_alert_allowlist=[])
    assert safety_alert_telegram_allowed(settings, "backfill_loss") is True
    assert safety_alert_telegram_allowed(settings, "stale_live_loss") is True


def test_add_only_extra_allowlist_is_case_insensitive() -> None:
    settings = Settings(_env_file=None, telegram_safety_alert_allowlist="PAPER_TRADE_OPENED")
    assert safety_alert_telegram_allowed(settings, "paper_trade_opened") is True
    assert safety_alert_telegram_allowed(settings, "stop_hit") is True


def test_unknown_extra_code_fails_startup_validation() -> None:
    with pytest.raises(ValidationError, match="Unknown telegram safety alert code"):
        Settings(_env_file=None, telegram_safety_alert_allowlist="not_a_real_code")


def test_logs_only_code_cannot_be_added_via_env() -> None:
    with pytest.raises(ValidationError, match="logs-only"):
        Settings(_env_file=None, telegram_safety_alert_allowlist="degraded_repeat")


def test_degraded_repeat_is_not_delivered_to_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_safety_alert_dedup()
    calls: list[str] = []

    def _deliver(
        self: TelegramAlertDeliveryProvider,
        payload: object,
        *,
        bypass_enable_check: bool = False,
    ) -> AlertDeliveryResult:
        del self, bypass_enable_check
        calls.append(payload.message)  # type: ignore[attr-defined]
        return AlertDeliveryResult(success=True, channel=AlertDeliveryChannel.TELEGRAM)

    monkeypatch.setattr(TelegramAlertDeliveryProvider, "deliver", _deliver)
    settings = Settings(_env_file=None, telegram_bot_token=_TOKEN, telegram_chat_id=_CHAT)
    alert = ExitMonitorAlert(
        code="degraded_repeat",
        severity="warning",
        message="Paper exit monitor cycles stayed degraded.",
        fields={"dedup_key": "degraded_repeat:boot"},
    )
    assert deliver_safety_alert(settings, alert, now=_NOW) is False
    assert calls == []


def test_stop_hit_verbatim_body_omits_prices_and_optional_pnl(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_safety_alert_dedup()
    calls: list[str] = []

    def _deliver(
        self: TelegramAlertDeliveryProvider,
        payload: object,
        *,
        bypass_enable_check: bool = False,
    ) -> AlertDeliveryResult:
        del self, bypass_enable_check
        calls.append(payload.message)  # type: ignore[attr-defined]
        return AlertDeliveryResult(success=True, channel=AlertDeliveryChannel.TELEGRAM)

    monkeypatch.setattr(TelegramAlertDeliveryProvider, "deliver", _deliver)
    settings = Settings(_env_file=None, telegram_bot_token=_TOKEN, telegram_chat_id=_CHAT)
    body = format_paper_exit_close_telegram_text(
        symbol="BTCUSDT",
        direction=TradeDirection.SHORT,
        r_multiple="1.00",
        timeframe="15m",
        exit_reason="target_hit",
    )
    alert = ExitMonitorAlert(
        code="tp_hit",
        severity="info",
        message=body,
        fields={"dedup_key": "tp_hit:trade-1"},
        telegram_verbatim=True,
    )
    assert deliver_safety_alert(settings, alert, now=_NOW) is True
    assert len(calls) == 1
    text = calls[0]
    assert "AlphaTrade PAPER informational take-profit hit." in text
    assert "Outcome: take-profit" in text
    assert "R: 1.00" in text
    assert "Entry:" not in text
    assert "Exit:" not in text
    assert "P&L" not in text
    assert "AlphaTrade alert (" not in text


def test_r_multiple_uses_planned_risk_when_present() -> None:
    label = realized_r_multiple_label(
        direction=TradeDirection.LONG,
        entry_price=Decimal("100"),
        exit_price=Decimal("110"),
        stop_price=Decimal("90"),
        planned_risk_amount=Decimal("25"),
        net_pnl=Decimal("50"),
    )
    assert label == "2.00"
