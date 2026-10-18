"""What every accepted trade must record. Changes apply to new trades only."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.services.risk_policy.adjusters import AdjusterResult
from app.services.risk_policy.ceilings import POLICY_ID
from app.services.risk_policy.grader import GradeDecision
from app.services.risk_policy.profile import Grade
from app.services.risk_policy.sizer import SizingResult


@dataclass(frozen=True)
class TradeRiskRecord:
    """Immutable facts for one new trade. Existing trades keep their own row."""

    profile_version_id: UUID
    grade: Grade
    grader_version: str
    adjusters: dict[str, str]
    calculation: dict[str, str]
    policy_id: str = POLICY_ID
    applies_to_new_trades_only: bool = True
    ai_explanation: str | None = None


def build_trade_record(
    *,
    profile_version_id: UUID,
    decision: GradeDecision,
    adjusters: AdjusterResult,
    sizing: SizingResult,
    ai_explanation: str | None = None,
) -> TradeRiskRecord:
    if decision.grade is None or decision.grader_version is None:
        raise ValueError("trade record requires a bound grade")
    return TradeRiskRecord(
        profile_version_id=profile_version_id,
        grade=decision.grade,
        grader_version=decision.grader_version,
        adjusters=adjusters.as_record(),
        calculation=sizing.calculation(),
        ai_explanation=ai_explanation,
    )
