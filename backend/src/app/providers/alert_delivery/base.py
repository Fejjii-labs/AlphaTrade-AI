"""Alert delivery provider abstraction (Slice 41)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.schemas.common import AlertDeliveryChannel


@dataclass(frozen=True)
class AlertDeliveryPayload:
    alert_id: str
    organization_id: str
    alert_type: str
    severity: str
    message: str
    strategy_id: str | None = None
    paper_validation_run_id: str | None = None
    paper_trade_id: str | None = None
    dedup_key: str | None = None
    metadata: dict | None = None
    event_id: str | None = None
    idempotency_key: str | None = None
    timestamp: str | None = None
    telegram_chat_id: str | None = None
    is_test: bool = False
    # When true, ``message`` is sent without the generic safety-alert wrapper.
    use_message_verbatim: bool = False


@dataclass(frozen=True)
class AlertDeliveryResult:
    success: bool
    channel: AlertDeliveryChannel
    error: str | None = None
    skipped: bool = False


class AlertDeliveryProvider(Protocol):
    channel: AlertDeliveryChannel

    def is_enabled(self) -> bool:
        """Return True when this provider may send externally."""

    def deliver(self, payload: AlertDeliveryPayload) -> AlertDeliveryResult:
        """Attempt delivery. Must not raise — return failure result instead."""
