"""Alert delivery schemas (Slice 41)."""

from __future__ import annotations

from pydantic import Field

from app.schemas.alerts import PaperAlert
from app.schemas.common import (
    AlertDeliveryChannel,
    StrictModel,
)
from app.schemas.notifications import ChannelProviderStatus

ALERT_TYPE_LABELS: dict[str, str] = {
    "setup_signal_detected": "Setup signal detected",
    "paper_trade_opened": "Paper trade opened",
    "paper_trade_closed": "Paper trade closed",
    "stop_hit": "Stop hit",
    "tp_hit": "Take profit hit",
    "runner_exit": "Runner exit",
    "data_stale": "Data stale",
    "strategy_blocked": "Strategy blocked",
    "promotion_status_changed": "Promotion status changed",
    "paper_validation_restricted": "Paper validation restricted",
    "overtrading_warning": "Overtrading warning",
    "daily_loss_lock_warning": "Daily loss lock warning",
    "demo_liquidation_through_stop": "LIQUIDATION THROUGH STOP",
    "paper_exit_backfill_loss": "Paper exit backfill loss",
    "paper_exit_degraded_repeat": "Paper exit monitor degraded",
    "paper_exit_clock_skew": "Paper exit clock skew",
}


class AlertDeliveryStatusResponse(StrictModel):
    delivery_enabled: bool
    webhook_enabled: bool
    telegram_enabled: bool
    email_enabled: bool
    push_enabled: bool
    webhook_configured: bool
    telegram_configured: bool = False
    effective_external_enabled: bool
    channels: list[str] = Field(default_factory=list)
    channel_statuses: list[ChannelProviderStatus] = Field(default_factory=list)
    paper_only: bool = True
    limitations: list[str] = Field(default_factory=list)


class AlertDeliverResult(StrictModel):
    alert: PaperAlert
    delivered: bool
    channel: AlertDeliveryChannel
    message: str


class AlertDeliverPendingResult(StrictModel):
    processed: int
    delivered: int
    failed: int
    skipped: int
    results: list[AlertDeliverResult] = Field(default_factory=list)


class AlertDeliverySummary(StrictModel):
    total: int
    pending: int
    delivered: int
    failed: int
    disabled: int
    skipped: int


def alert_type_label(alert_type: str) -> str:
    return ALERT_TYPE_LABELS.get(alert_type, alert_type.replace("_", " ").title())
