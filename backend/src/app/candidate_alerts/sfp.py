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
    # Present only when a confirmed setup has a valid structural paper plan.
    entry: str | None = None
    stop: str | None = None
    take_profit: str | None = None
    paper_mode: Literal[True] = True
    # Empty salt keeps the historical duplicate key. Approach alerts set a bucket.
    duplicate_salt: str = ""
    # Opt-in watchlist projection: authored market whose parameters were reused.
    projection_source: str | None = None

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

    def _plain_reason(self) -> str | None:
        """One plain-language line for terminal or preparatory events."""
        side = self.direction.capitalize()
        back = "above" if self.direction.lower() == "short" else "below"
        level = self.structural_level
        reasons = {
            NotificationEventType.SFP_INVALIDATED: (
                f"{side} cancelled: price closed back {back} {level}"
            ),
            NotificationEventType.SFP_EXPIRED: f"{side} expired: no confirmation in time",
            NotificationEventType.SFP_LEVEL_APPROACH: (
                f"Heads-up: price is near {level}, not swept yet"
            ),
            NotificationEventType.SFP_SWEEP_DETECTED: f"Sweep of {level}, waiting for reclaim",
            NotificationEventType.SFP_RECLAIM_FORMING: f"Reclaim of {level} forming",
            NotificationEventType.SFP_BLOCKED_BY_RISK: f"{side} blocked by risk rules",
        }
        return reasons.get(self.event_type)

    def text(self) -> str:
        """Shared strategy layout; IDs, risk and coverage stay in the stored summary."""
        from app.candidate_alerts.content import render_strategy_alert_text

        approach = self.event_type == NotificationEventType.SFP_LEVEL_APPROACH
        return render_strategy_alert_text(
            symbol=self.symbol,
            strategy="sfp",
            direction=self.direction,
            entry=self.entry,
            stop=self.stop,
            target=self.take_profit,
            timeframe=self.timeframe,
            stage=(
                "level approach"
                if approach
                else self.event_type.value.lower().removeprefix("sfp_").replace("_", " ")
            ),
            at=self.evidence_at,
            projection_source=self.projection_source,
            reason=self._plain_reason(),
            show_plan=self.event_type
            not in {NotificationEventType.SFP_INVALIDATED, NotificationEventType.SFP_EXPIRED},
            key_levels=(f"Level: {self.level_type.replace('_', ' ')} {self.structural_level}",),
        )
