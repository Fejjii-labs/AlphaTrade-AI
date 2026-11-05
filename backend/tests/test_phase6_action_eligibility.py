"""Phase 6 ActionEligibility: paper action gate, setup-truth isolation, determinism."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import ExchangeMode, ExecutionMode
from app.market_contracts.enums import VenueId
from app.schemas.common import TradeDirection
from app.signal_fusion.action_eligibility import (
    FIRST_SLICE_CROSS_VENUE_BASIS_THRESHOLD_BPS,
    ActionEligibilityService,
    in_memory_action_eligibility,
    uniqueness_hash,
)
from app.signal_fusion.enums import (
    ActionEligibilityState,
    EligibilityReasonCode,
    SetupAssessmentState,
)
from app.signal_fusion.errors import ActionEligibilityLineageError
from app.signal_fusion.evaluator import evaluate_setup
from tests.support.phase5_market import EVALUATED_AT
from tests.support.phase6_eligibility import (
    ACCOUNT_B,
    EVIDENCE_PRICE,
    ORG_B,
    RISK_SNAPSHOT_B,
    USER_B,
    account_identity,
    confirmed_bundle,
    eligibility_command,
    market_action,
    non_confirmed_command,
    paper_configuration,
    portfolio_state,
    risk_snapshot,
    safety_snapshot,
)
from tests.support.phase6_evaluator import make_world
from tests.support.phase6_fusion import ACCOUNT_ID, ORG_ID, USER_ID, make_evidence_window


def _service() -> ActionEligibilityService:
    return in_memory_action_eligibility(now=EVALUATED_AT)


def test_confirmed_setup_with_valid_account_and_risk_is_eligible() -> None:
    window, assessment, candidate = confirmed_bundle()
    result = _service().evaluate(
        eligibility_command(window=window, assessment=assessment, candidate=candidate)
    )
    assert assessment.state is SetupAssessmentState.CONFIRMED_SETUP
    assert result.eligibility.state is ActionEligibilityState.ELIGIBLE
    assert result.eligibility.reason_codes == (EligibilityReasonCode.ELIGIBLE,)
    assert result.paper_actionable is True
    assert result.live_executable is False
    assert result.evaluation_revision == 1
    assert result.evidence_window_hash == window.content_hash
    assert result.setup_assessment_content_hash == assessment.content_hash
    assert result.eligibility.organization_id == ORG_ID
    assert result.eligibility.account_id == ACCOUNT_ID
    assert result.eligibility.candidate_id == candidate.candidate_id
    assert result.eligibility.candidate_revision == candidate.transition_version
    assert result.eligibility.assessment_id == assessment.assessment_id
    assert result.safety_epoch == 1
    assert result.currently_paper_actionable(EVALUATED_AT) is True


def test_kill_switch_blocks_and_dominates() -> None:
    window, assessment, candidate = confirmed_bundle()
    before = assessment.content_hash
    result = _service().evaluate(
        eligibility_command(
            window=window,
            assessment=assessment,
            candidate=candidate,
            safety=safety_snapshot(kill_switch_active=True),
            risk=risk_snapshot(daily_locked=True),
            configuration=paper_configuration(enable_real_trading=True),
        )
    )
    assert result.eligibility.state is ActionEligibilityState.BLOCKED
    assert result.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_KILL_SWITCH,)
    assert result.paper_actionable is False
    assert result.live_executable is False
    assert assessment.content_hash == before
    assert assessment.state is SetupAssessmentState.CONFIRMED_SETUP


def test_daily_loss_limit_does_not_block() -> None:
    """The cash daily loss limit is display only (AT-ADR-152)."""
    result = _service().evaluate(
        eligibility_command(
            risk=risk_snapshot(
                daily_locked=True,
                daily_loss_limit=Decimal("50"),
                realized_pnl_today=Decimal("-50"),
            )
        )
    )
    assert result.eligibility.state is ActionEligibilityState.ELIGIBLE
    assert EligibilityReasonCode.BLOCKED_DAILY_LOSS not in result.eligibility.reason_codes


def test_risk_capacity_blocks() -> None:
    result = _service().evaluate(
        eligibility_command(portfolio=portfolio_state(open_exposure_notional=Decimal("500")))
    )
    assert result.eligibility.state is ActionEligibilityState.BLOCKED
    assert result.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_EXPOSURE,)


def test_account_mismatch_blocks() -> None:
    result = _service().evaluate(
        eligibility_command(
            account=account_identity(),
            portfolio=portfolio_state(account_id=ACCOUNT_B),
            risk=risk_snapshot(account_id=ACCOUNT_B),
            safety=safety_snapshot(account_id=ACCOUNT_B),
        )
    )
    assert result.eligibility.state is ActionEligibilityState.BLOCKED
    assert result.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_ACCOUNT_STATE,)


def test_tenant_mismatch_blocks() -> None:
    result = _service().evaluate(
        eligibility_command(
            account=account_identity(organization_id=ORG_B, user_id=USER_B, account_id=ACCOUNT_B),
            portfolio=portfolio_state(organization_id=ORG_B, account_id=ACCOUNT_B),
            risk=risk_snapshot(organization_id=ORG_B, user_id=USER_B, account_id=ACCOUNT_B),
            safety=safety_snapshot(organization_id=ORG_B, account_id=ACCOUNT_B),
        )
    )
    assert result.eligibility.state is ActionEligibilityState.BLOCKED
    assert result.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_ACCOUNT_STATE,)
    assert result.eligibility.organization_id == ORG_B


def test_stale_required_action_evidence_blocks() -> None:
    result = _service().evaluate(
        eligibility_command(market=market_action(required_action_evidence_fresh=False))
    )
    assert result.eligibility.state is ActionEligibilityState.BLOCKED
    assert result.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_DATA_QUALITY,)


def test_cross_venue_basis_above_20_bps_blocks() -> None:
    result = _service().evaluate(
        eligibility_command(
            market=market_action(
                execution_venue=VenueId.BLOFIN,
                execution_price=Decimal("100210"),
            )
        )
    )
    assert Decimal("20") == FIRST_SLICE_CROSS_VENUE_BASIS_THRESHOLD_BPS
    assert result.eligibility.state is ActionEligibilityState.BLOCKED
    assert result.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_BASIS,)


def test_cross_venue_basis_below_threshold_is_eligible() -> None:
    result = _service().evaluate(
        eligibility_command(
            market=market_action(
                execution_venue=VenueId.BLOFIN,
                execution_price=Decimal("100190"),
            )
        )
    )
    assert result.eligibility.state is ActionEligibilityState.ELIGIBLE
    assert result.paper_actionable is True
    assert result.live_executable is False


def test_basis_at_threshold_is_eligible() -> None:
    result = _service().evaluate(
        eligibility_command(
            market=market_action(
                execution_venue=VenueId.BLOFIN,
                execution_price=Decimal("100200"),
            )
        )
    )
    relative = (Decimal("100200") - EVIDENCE_PRICE) / EVIDENCE_PRICE * Decimal("10000")
    assert relative == Decimal("20")
    assert result.eligibility.state is ActionEligibilityState.ELIGIBLE


def test_basis_block_does_not_alter_setup_assessment() -> None:
    window, assessment, candidate = confirmed_bundle()
    before_hash = assessment.content_hash
    before_state = assessment.state
    before_dump = assessment.model_dump()
    result = _service().evaluate(
        eligibility_command(
            window=window,
            assessment=assessment,
            candidate=candidate,
            market=market_action(
                execution_venue=VenueId.BLOFIN,
                execution_price=Decimal("100210"),
            ),
        )
    )
    assert result.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_BASIS,)
    assert assessment.content_hash == before_hash
    assert assessment.state is before_state is SetupAssessmentState.CONFIRMED_SETUP
    assert assessment.model_dump() == before_dump
    world = make_world()
    left = evaluate_setup(
        policy=world.policy,
        command=world.command,
        evidence=world.evidence,
        evaluated_at=world.evaluated_at,
        account_context={"kill_switch": True, "basis_bps": 40},
    )
    right = evaluate_setup(
        policy=world.policy,
        command=world.command,
        evidence=world.evidence,
        evaluated_at=world.evaluated_at,
        account_context=None,
    )
    assert left.content_hash == right.content_hash
    assert left.state is SetupAssessmentState.CONFIRMED_SETUP


@pytest.mark.parametrize(
    "state",
    [
        SetupAssessmentState.NO_SETUP,
        SetupAssessmentState.WATCH,
        SetupAssessmentState.PARTIAL_MATCH,
        SetupAssessmentState.INVALIDATED,
        SetupAssessmentState.EXPIRED,
    ],
)
def test_non_confirmed_setup_cannot_become_actionable(state: SetupAssessmentState) -> None:
    command = non_confirmed_command(state)
    before = command.assessment.content_hash
    result = _service().evaluate(command)
    assert command.assessment.state is state
    assert result.eligibility.state is ActionEligibilityState.BLOCKED
    assert result.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_SETUP_NOT_CONFIRMED,)
    assert result.paper_actionable is False
    assert result.live_executable is False
    assert command.assessment.content_hash == before
    assert command.assessment.state is state


def test_duplicate_identical_evaluation_converges() -> None:
    window, assessment, candidate = confirmed_bundle()
    command = eligibility_command(window=window, assessment=assessment, candidate=candidate)
    service = _service()
    first = service.evaluate(command)
    second = service.evaluate(command)
    third = service.evaluate(
        eligibility_command(window=window, assessment=assessment, candidate=candidate)
    )
    assert first.eligibility.eligibility_id == second.eligibility.eligibility_id
    assert first.eligibility.content_hash == second.eligibility.content_hash
    assert first.content_hash == second.content_hash == third.content_hash
    assert first.uniqueness_hash == uniqueness_hash(command)
    assert first.evaluation_revision == second.evaluation_revision == 1
    history = service.history(
        organization_id=ORG_ID, account_id=ACCOUNT_ID, candidate_id=candidate.candidate_id
    )
    assert len(history) == 1


def test_changed_risk_snapshot_reevaluates_without_mutating_history() -> None:
    window, assessment, candidate = confirmed_bundle()
    service = _service()
    first = service.evaluate(
        eligibility_command(window=window, assessment=assessment, candidate=candidate)
    )
    second = service.evaluate(
        eligibility_command(
            window=window,
            assessment=assessment,
            candidate=candidate,
            risk=risk_snapshot(risk_snapshot_id=RISK_SNAPSHOT_B, cooldown_active=True),
        )
    )
    assert first.eligibility.state is ActionEligibilityState.ELIGIBLE
    assert second.eligibility.state is ActionEligibilityState.BLOCKED
    assert second.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_COOLDOWN,)
    assert first.eligibility.eligibility_id != second.eligibility.eligibility_id
    assert first.eligibility.content_hash != second.eligibility.content_hash
    assert first.evaluation_revision == 1
    assert second.evaluation_revision == 2
    assert second.eligibility.risk_snapshot_id == RISK_SNAPSHOT_B
    history = service.history(
        organization_id=ORG_ID, account_id=ACCOUNT_ID, candidate_id=candidate.candidate_id
    )
    assert len(history) == 2
    assert history[0].content_hash == first.content_hash
    assert history[0].eligibility.state is ActionEligibilityState.ELIGIBLE


def test_live_trading_configuration_cannot_make_result_executable() -> None:
    real = _service().evaluate(
        eligibility_command(configuration=paper_configuration(enable_real_trading=True))
    )
    trade_mode = _service().evaluate(
        eligibility_command(configuration=paper_configuration(execution_mode=ExecutionMode.TRADE))
    )
    live_exchange = _service().evaluate(
        eligibility_command(
            configuration=paper_configuration(exchange_mode=ExchangeMode.TRADE_LIVE)
        )
    )
    for result in (real, trade_mode, live_exchange):
        assert result.eligibility.state is ActionEligibilityState.BLOCKED
        assert result.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_CONFIGURATION,)
        assert result.paper_actionable is False
        assert result.live_executable is False
    eligible = _service().evaluate(eligibility_command())
    assert eligible.eligibility.state is ActionEligibilityState.ELIGIBLE
    assert eligible.live_executable is False


def test_concurrent_identical_evaluation_converges() -> None:
    window, assessment, candidate = confirmed_bundle()
    command = eligibility_command(window=window, assessment=assessment, candidate=candidate)
    service = _service()

    def _run() -> str:
        return service.evaluate(command).eligibility.content_hash

    with ThreadPoolExecutor(max_workers=8) as pool:
        hashes = list(pool.map(lambda _: _run(), range(16)))
    assert len(set(hashes)) == 1
    history = service.history(
        organization_id=ORG_ID, account_id=ACCOUNT_ID, candidate_id=candidate.candidate_id
    )
    assert len(history) == 1


def test_changed_safety_epoch_reevaluates_deterministically() -> None:
    window, assessment, candidate = confirmed_bundle()
    service = _service()
    first = service.evaluate(
        eligibility_command(window=window, assessment=assessment, candidate=candidate)
    )
    second = service.evaluate(
        eligibility_command(
            window=window,
            assessment=assessment,
            candidate=candidate,
            safety=safety_snapshot(safety_epoch=2, kill_switch_active=True),
        )
    )
    assert first.safety_epoch == 1
    assert second.safety_epoch == 2
    assert second.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_KILL_SWITCH,)
    assert first.eligibility.eligibility_id != second.eligibility.eligibility_id


def test_missing_cross_venue_price_blocks_venue_state() -> None:
    result = _service().evaluate(
        eligibility_command(
            market=market_action(execution_venue=VenueId.BLOFIN, execution_price=None)
        )
    )
    assert result.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_VENUE_STATE,)


def test_lineage_mismatch_fails_closed() -> None:
    window, assessment, candidate = confirmed_bundle()
    other_window = make_evidence_window(direction=TradeDirection.LONG)
    assert other_window.content_hash != window.content_hash
    with pytest.raises(ActionEligibilityLineageError):
        _service().evaluate(
            eligibility_command(window=other_window, assessment=assessment, candidate=candidate)
        )


def test_eligibility_states_remain_frozen_and_distinct_from_setup() -> None:
    assert set(ActionEligibilityState) == {
        ActionEligibilityState.ELIGIBLE,
        ActionEligibilityState.BLOCKED,
        ActionEligibilityState.EXPIRED,
    }
    assert set(ActionEligibilityState) != set(SetupAssessmentState)
    assert SetupAssessmentState.CONFIRMED_SETUP not in ActionEligibilityState
    result = _service().evaluate(eligibility_command())
    with pytest.raises(ValidationError, match="frozen"):
        result.eligibility.state = ActionEligibilityState.BLOCKED  # type: ignore[misc]
    with pytest.raises(ValidationError, match="frozen"):
        result.live_executable = True  # type: ignore[misc]


def test_action_eligibility_module_does_not_create_plans_or_call_venues() -> None:
    import app.signal_fusion.action_eligibility as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "from app.schemas.trade_plan" not in source
    assert "TradePlanRevision" not in source
    assert "evaluate_setup" not in source
    assert "telegram" not in source.lower()
    assert "KillSwitchService" not in source
    assert "httpx" not in source
    assert "requests" not in source
    assert "langchain" not in source
    assert "openai" not in source
    assert "anthropic" not in source
    assert USER_ID
    assert ACCOUNT_ID
