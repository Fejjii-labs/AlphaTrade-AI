"""Informational projection of an already confirmed Nested episode."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.market_contracts.models import CanonicalModel, PositiveCanonicalDecimal
from app.signal_fusion.assessment import SetupAssessment
from app.signal_fusion.evidence_window import CanonicalEvidenceWindowV1


class NestedAlertSummary(CanonicalModel):
    organization_id: UUID
    strategy_version_id: UUID
    setup_id: UUID
    symbol: str = Field(min_length=1, max_length=32)
    stage: str = Field(min_length=1, max_length=32)
    evidence_at: AwareDatetime
    decision: str = Field(min_length=1, max_length=80)
    risk_state: str = Field(min_length=1, max_length=80)
    reasons: tuple[str, ...]
    # Detector prices. None when that leg was not measured.
    entry: PositiveCanonicalDecimal | None = None
    stop: PositiveCanonicalDecimal | None = None
    target: PositiveCanonicalDecimal | None = None
    paper_mode: Literal[True] = True
    freshness: Literal["AVAILABLE"] = "AVAILABLE"


def required_evidence_fresh(
    assessment: SetupAssessment, window: CanonicalEvidenceWindowV1, now: datetime
) -> bool:
    from app.market_contracts.identity import interval_timedelta
    from app.signal_fusion.enums import SetupAssessmentState

    required = {
        "market_identity",
        "fresh_closed_ohlcv",
        "final_causal_series",
        "trigger_binding",
        "required_observation_binding",
        "compiled_policy_binding",
        "nested_confirmation",
    }
    passed = {rule.rule_id for rule in assessment.rule_results if rule.passed}
    return (
        assessment.state is SetupAssessmentState.CONFIRMED_SETUP
        and required <= passed
        and window.interval.end <= now < window.interval.end + interval_timedelta(window.timeframe)
        and now < assessment.valid_until
    )
