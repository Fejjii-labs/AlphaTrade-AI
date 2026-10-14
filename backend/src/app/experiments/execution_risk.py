"""Conservative experiment budgets at the existing serialized account claim.

No IO, settings mutations or second ledger. Pending/uncertain entries consume
capacity before fills or samples exist. Filled loss allocations remain charged;
profits and unknown fee/funding semantics never replenish an execution budget.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AuditLog, ExecutionCommand, RiskReservation
from app.db.models import TradePlanRevision as PlanRow
from app.experiments.identity import utc
from app.experiments.models import ExperimentVersionRow
from app.experiments.native_producer import ORIGIN, NativePlanOrigin
from app.experiments.service import semantic_hash
from app.schemas.experiments import ExperimentConfiguration
from app.schemas.trade_plan import TradePlanRevision

if TYPE_CHECKING:
    from app.services.execution_claim import ReservationIntent


def experiment_reservation_refusal(
    session: Session, plan: TradePlanRevision, intent: ReservationIntent, now: datetime
) -> str | None:
    origin = session.scalar(
        select(AuditLog).where(
            AuditLog.organization_id == plan.organization_id,
            AuditLog.user_id == plan.user_id,
            AuditLog.resource_type == "experiment_execution",
            AuditLog.resource_id == str(plan.revision_id),
            AuditLog.redacted_metadata["operation"].as_string() == ORIGIN,
        )
    )
    if origin is None:
        return None  # Ordinary governed demo retains its account authority.
    body = dict(origin.redacted_metadata)
    digest = body.pop("evidence_hash", None)
    body.pop("operation", None)
    receipt = NativePlanOrigin.model_validate(body)
    if (
        origin.actor != "experiment_executor"
        or origin.actor_type != "system"
        or origin.result != "success"
        or origin.payload_hash != digest
        or semantic_hash(receipt.model_dump()) != digest
        or (
            receipt.organization_id,
            receipt.user_id,
            receipt.execution_account_id,
            receipt.revision_id,
            receipt.plan_content_hash,
        )
        != (
            plan.organization_id,
            plan.user_id,
            plan.account_id,
            plan.revision_id,
            plan.content_hash,
        )
    ):
        return "experiment_origin_invalid"
    version = session.get(ExperimentVersionRow, receipt.version_id, populate_existing=True)
    if (
        version is None
        or version.configuration_hash != receipt.configuration_hash
        or version.state != "running"
        or version.authorized_until is None
        or now >= utc(version.authorized_until)
    ):
        return "experiment_sample_window"
    config = ExperimentConfiguration.model_validate(version.configuration)
    limits = config.risk_limits
    # The account epoch is held by the caller. Another claim cannot commit
    # between this read and charging the existing RiskReservation row.
    rows = list(
        session.execute(
            select(ExecutionCommand, RiskReservation)
            .join(RiskReservation, RiskReservation.command_id == ExecutionCommand.id)
            .join(PlanRow, PlanRow.id == ExecutionCommand.revision_id)
            .where(
                ExecutionCommand.organization_id == plan.organization_id,
                ExecutionCommand.account_id == plan.account_id,
                ExecutionCommand.outcome == "ALLOW",
                PlanRow.execution_venue == "BLOFIN_DEMO",
            )
            .limit(10001)
        ).tuples()
    )
    if len(rows) > 10000:
        return "experiment_risk_history_budget"
    charged = [(c, r) for c, r in rows if r.converted_trade_slots or r.release_state != "RELEASED"]
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = today - timedelta(days=today.weekday())
    daily = [(c, r) for c, r in charged if utc(c.created_at) >= today]
    weekly = [(c, r) for c, r in charged if utc(c.created_at) >= week]
    if len(daily) >= limits.max_trades_per_day or len(charged) >= limits.max_trades_total:
        return "experiment_reserved_trade_limit"
    for history, budget, reason in (
        (daily, limits.max_daily_loss, "experiment_reserved_daily_loss"),
        (weekly, limits.max_weekly_loss, "experiment_reserved_weekly_loss"),
        (charged, limits.max_drawdown, "experiment_reserved_drawdown"),
    ):
        if (
            sum((r.daily_loss_allocation for _, r in history), Decimal(0)) + intent.daily_loss
            > budget
        ):
            return reason
    # The native entry gate already permits one exclusively governed position.
    # Include all reservations, including manual origins, in the account limits.
    active = [r for _, r in charged if r.release_state != "RELEASED"]
    if len(active) >= limits.max_open_positions:
        return "experiment_reserved_position_limit"
    if intent.pending_notional > limits.max_position_notional:
        return "experiment_reserved_position_notional"
    if (
        sum((r.remaining_reserved_notional for r in active), Decimal(0)) + intent.pending_notional
        > limits.max_total_exposure
    ):
        return "experiment_reserved_total_exposure"
    if intent.daily_loss > limits.max_risk_per_trade:
        return "experiment_reserved_risk_per_trade"
    return None
