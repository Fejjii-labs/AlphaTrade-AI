"""Read existing SFP lifecycle facts; never detect, transition, or score setups."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.candidate_alerts.sfp import SfpAlertSummary
from app.db.strategy_brain import BrainSetupEventRow, BrainSetupRow
from app.market_contracts.identity import interval_timedelta
from app.schemas.common import Timeframe
from app.schemas.telegram_policy import NotificationEventType as EventType
from app.strategy_brain.records import aware


def _plan_prices(plan: object) -> tuple[str | None, str | None, str | None]:
    """Copy a stored structural plan. Missing or partial plans stay unset."""

    if not isinstance(plan, dict):
        return None, None, None
    prices = []
    for name in ("entry", "stop", "take_profit"):
        value = plan.get(name)
        if not isinstance(value, str) or not value:
            return None, None, None
        prices.append(value)
    return prices[0], prices[1], prices[2]


def notification_type(kind: str, payload: dict[str, Any]) -> EventType | None:
    if kind == "setup_expired" or payload.get("state") == "EXPIRED":
        return EventType.SFP_EXPIRED
    if kind == "paper_decision":
        if payload.get("paper_stage") == "blocked" and set(payload.get("reason_codes", [])) & {
            "risk_block",
            "kill_switch_active",
        }:
            return EventType.SFP_BLOCKED_BY_RISK
        return None
    if kind != "system_observation":
        return None
    if payload.get("state") == "INVALIDATED":
        return EventType.SFP_INVALIDATED
    if payload.get("state") == "CONFIRMED":
        return EventType.SFP_CONFIRMED
    if payload.get("state") == "FORMING":
        if payload.get("reclaim_observation_id"):
            return EventType.SFP_RECLAIM_FORMING
        return EventType.SFP_SWEEP_DETECTED
    return None


def sfp_notification_summaries(
    session: Session,
    *,
    organization_id: UUID,
    strategy_version_id: UUID,
    now: datetime,
) -> tuple[SfpAlertSummary, ...]:
    """Recover fresh journal facts across restarts; outbox owns idempotency."""
    now = aware(now)
    rows = session.execute(
        select(BrainSetupEventRow, BrainSetupRow)
        .join(BrainSetupRow, BrainSetupRow.id == BrainSetupEventRow.setup_id)
        .where(
            BrainSetupEventRow.organization_id == organization_id,
            BrainSetupRow.organization_id == organization_id,
            BrainSetupRow.strategy_version_id == strategy_version_id,
            BrainSetupEventRow.occurred_at <= now,
        )
        .order_by(BrainSetupEventRow.occurred_at, BrainSetupEventRow.id)
    )
    summaries = []
    for event, setup in rows:
        payload = event.payload
        if payload.get("family") != "sfp":
            continue
        event_type = notification_type(event.kind, payload)
        if event_type is None:
            continue
        max_age = (
            interval_timedelta(Timeframe(payload["timeframe"]))
            * payload["required_evidence_max_age_bars"]
        )
        if now >= aware(event.occurred_at) + max_age:
            continue
        sweep = payload["sweep"]
        level = sweep["reference_level"]
        quality = payload.get("quality", {})
        available = tuple(
            name
            for name, component in quality.items()
            if component.get("availability") == "AVAILABLE"
        )
        missing = tuple(
            f"{name}:{component.get('availability', 'MISSING')}"
            for name, component in quality.items()
            if component.get("availability") != "AVAILABLE"
        )
        if not quality:
            missing += ("quality:MISSING",)
        if payload.get("required_evidence") != "AVAILABLE":
            missing += (f"required_evidence:{payload.get('required_evidence', 'MISSING')}",)
        plan = payload.get("trade_plan")
        plan_prices = _plan_prices(plan)
        summary = SfpAlertSummary(
            organization_id=organization_id,
            strategy_version_id=strategy_version_id,
            setup_id=setup.id,
            event_type=event_type,
            occurred_at=aware(event.occurred_at),
            evidence_at=datetime.fromisoformat(payload["evidence_at"]),
            symbol=setup.symbol,
            venue=payload["venue"],
            timeframe=payload["timeframe"],
            direction=payload["direction"],
            level_type=level["kind"],
            structural_level=str(level["price"]),
            sweep_extreme=str(sweep["extreme"]),
            reclaim_state=payload["condition"],
            quality_coverage=available,
            missing_evidence=missing,
            risk_state=payload.get("risk_state", "not_evaluated"),
            risk_reasons=tuple(payload.get("risk_reason_codes", [])),
            entry=plan_prices[0],
            stop=plan_prices[1],
            take_profit=plan_prices[2],
        )
        # A closed candle can first reveal both the sweep and reclaim. The
        # canonical sweep proof still supplies its own timestamp; do not require
        # a separate pre-reclaim scan or invent an earlier receipt.
        swept_at = datetime.fromisoformat(sweep["observed_at"])
        if (
            event.kind == "system_observation"
            and event_type != EventType.SFP_SWEEP_DETECTED
            and swept_at <= now < swept_at + max_age
        ):
            summaries.append(
                summary.model_copy(
                    update={
                        "event_type": EventType.SFP_SWEEP_DETECTED,
                        "occurred_at": swept_at,
                        "evidence_at": datetime.fromisoformat(sweep["evidence"]["observed_at"]),
                        "reclaim_state": summary.reclaim_state
                        if swept_at == summary.occurred_at
                        else "not_recorded_at_sweep",
                        "quality_coverage": (),
                        "missing_evidence": ("quality:MISSING",),
                        "risk_state": "not_evaluated",
                        "risk_reasons": (),
                        "entry": None,
                        "stop": None,
                        "take_profit": None,
                    }
                )
            )
        summaries.append(summary)
    # Repeated detector observations cannot create new semantic lifecycle events.
    unique: dict[tuple[UUID, EventType], SfpAlertSummary] = {}
    for summary in summaries:
        unique.setdefault((summary.setup_id, summary.event_type), summary)
    return tuple(unique.values())
