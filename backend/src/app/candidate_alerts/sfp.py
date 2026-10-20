"""Informational SFP facts projected from the canonical Brain event journal."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime

from app.market_contracts.models import CanonicalModel
from app.schemas.telegram_policy import (
    AlertPhase,
    NotificationEventType,
    NotificationSeverity,
    TelegramNotificationEvent,
)
from app.services.canonical_serialization import canonical_sha256

SEVERITIES = {
    NotificationEventType.SFP_SWEEP_DETECTED: NotificationSeverity.WATCH,
    NotificationEventType.SFP_RECLAIM_FORMING: NotificationSeverity.WATCH,
    NotificationEventType.SFP_CONFIRMED: NotificationSeverity.ACTION,
    NotificationEventType.SFP_INVALIDATED: NotificationSeverity.INFO,
    NotificationEventType.SFP_EXPIRED: NotificationSeverity.INFO,
    NotificationEventType.SFP_BLOCKED_BY_RISK: NotificationSeverity.WATCH,
    NotificationEventType.SFP_LEVEL_APPROACH: NotificationSeverity.WATCH,
}


class SfpAlertSummary(CanonicalModel):
    organization_id: UUID
    strategy_version_id: UUID
    setup_id: UUID
    event_type: NotificationEventType
    occurred_at: AwareDatetime
    evidence_at: AwareDatetime
    symbol: str
    venue: str
    timeframe: str
    direction: str
    level_type: str
    structural_level: str
    sweep_extreme: str
    reclaim_state: str
    quality_coverage: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    risk_state: str
    risk_reasons: tuple[str, ...] = ()
    paper_mode: Literal[True] = True
    # Empty salt keeps the historical duplicate key. Approach alerts set a bucket.
    duplicate_salt: str = ""

    def notification_event(self) -> TelegramNotificationEvent:
        if self.event_type not in SEVERITIES:
            raise ValueError("Not an SFP notification event")
        phase = None
        if self.event_type in {
            NotificationEventType.SFP_SWEEP_DETECTED,
            NotificationEventType.SFP_RECLAIM_FORMING,
        }:
            phase = AlertPhase.FORMING
        elif self.event_type == NotificationEventType.SFP_CONFIRMED:
            phase = AlertPhase.CONFIRMED
        return TelegramNotificationEvent(
            event_type=self.event_type,
            severity=SEVERITIES[self.event_type],
            strategy_id=self.strategy_version_id,
            symbol=self.symbol,
            phase=phase,
            # The detector exposes component values, not a 0..100 score.
            # Coverage must not masquerade as strategy quality.
            quality=None,
            duplicate_key=canonical_sha256(
                {
                    "organization": self.organization_id,
                    "version": self.strategy_version_id,
                    "setup": self.setup_id,
                    "event_type": self.event_type,
                    **({"salt": self.duplicate_salt} if self.duplicate_salt else {}),
                }
            ),
            occurred_at=self.occurred_at,
        )

    def text(self) -> str:
        if self.event_type == NotificationEventType.SFP_LEVEL_APPROACH:
            return "\n".join(
                (
                    "PAPER MODE — SFP LEVEL APPROACH",
                    "Preparation only. Price is near a level and has not swept it.",
                    "Execution is not authorized.",
                    f"{self.symbol} | {self.venue} | {self.timeframe} | {self.direction}",
                    f"Structural level: {self.level_type} {self.structural_level}",
                    f"Live price: {self.sweep_extreme}",
                    f"Strategy version: {self.strategy_version_id}",
                    f"Level reference: {self.setup_id}",
                    f"Evidence timestamp: {self.evidence_at.isoformat()}",
                    f"Event timestamp: {self.occurred_at.isoformat()}",
                )
            )
        return "\n".join(
            (
                f"PAPER MODE — {self.event_type.value.replace('_', ' ')}",
                f"{self.symbol} | {self.venue} | {self.timeframe} | {self.direction}",
                f"Structural level: {self.level_type} {self.structural_level}",
                f"Sweep extreme: {self.sweep_extreme}",
                f"Reclaim state: {self.reclaim_state}",
                f"Strategy version: {self.strategy_version_id}",
                f"Setup ID: {self.setup_id}",
                f"Evidence timestamp: {self.evidence_at.isoformat()}",
                f"Event timestamp: {self.occurred_at.isoformat()}",
                f"Quality coverage: {', '.join(self.quality_coverage) or 'none'}",
                f"Missing evidence: {', '.join(self.missing_evidence) or 'none'}",
                "Quality score: unavailable",
                f"Risk state: {self.risk_state}",
                f"Risk reasons: {', '.join(self.risk_reasons) or 'none'}",
            )
        )
