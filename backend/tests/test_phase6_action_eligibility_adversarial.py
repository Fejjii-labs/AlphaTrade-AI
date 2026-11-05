"""Adversarial ActionEligibility contracts: isolation, fail-closed identity, precedence."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.config import ExchangeMode, ExecutionMode
from app.market_contracts.enums import VenueId
from app.schemas.common import TradeDirection
from app.signal_fusion.action_eligibility import (
    ActionEligibilityEvaluation,
    ActionEligibilityService,
    InMemoryActionEligibilityStore,
    in_memory_action_eligibility,
    uniqueness_hash,
)
from app.signal_fusion.candidate import Candidate, build_candidate
from app.signal_fusion.enums import (
    ActionEligibilityState,
    CandidateState,
    EligibilityReasonCode,
    SetupAssessmentState,
)
from app.signal_fusion.errors import (
    ActionEligibilityLineageError,
    ConflictingActionEligibilityError,
)
from tests.support.phase5_market import EVALUATED_AT
from tests.support.phase6_eligibility import (
    ACCOUNT_B,
    ORG_B,
    RISK_SNAPSHOT_B,
    USER_B,
    VENUE_STATE_B,
    account_identity,
    confirmed_bundle,
    eligibility_command,
    market_action,
    paper_configuration,
    portfolio_state,
    risk_snapshot,
    safety_snapshot,
    synthetic_candidate,
)
from tests.support.phase6_fusion import (
    ACCOUNT_ID,
    CORRELATION_A,
    CORRELATION_B,
    ORG_ID,
    VALID_UNTIL,
    make_assessment,
    make_evidence_window,
)


def _service(
    *,
    now: datetime = EVALUATED_AT,
    store: InMemoryActionEligibilityStore | None = None,
) -> ActionEligibilityService:
    return in_memory_action_eligibility(now=now, store=store)


def _reject_candidate(candidate: Candidate) -> Candidate:
    payload = candidate.model_dump()
    payload["state"] = CandidateState.REJECTED
    payload["transition_version"] = candidate.transition_version + 1
    payload["content_hash"] = "0" * 64
    return build_candidate(**payload)


def test_kill_switch_unavailable_fails_closed() -> None:
    result = _service().evaluate(
        eligibility_command(safety=safety_snapshot(kill_switch_unavailable=True))
    )
    assert result.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_KILL_SWITCH,)
    assert result.paper_actionable is False
    assert result.live_executable is False


def test_global_kill_switch_blocks() -> None:
    result = _service().evaluate(
        eligibility_command(safety=safety_snapshot(global_kill_switch_active=True))
    )
    assert result.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_KILL_SWITCH,)


def test_kill_switch_dominates_tenant_mismatch_and_stale_evidence() -> None:
    window, assessment, candidate = confirmed_bundle()
    result = _service().evaluate(
        eligibility_command(
            window=window,
            assessment=assessment,
            candidate=candidate,
            account=account_identity(organization_id=ORG_B, user_id=USER_B, account_id=ACCOUNT_B),
            portfolio=portfolio_state(organization_id=ORG_B, account_id=ACCOUNT_B),
            risk=risk_snapshot(organization_id=ORG_B, user_id=USER_B, account_id=ACCOUNT_B),
            safety=safety_snapshot(
                organization_id=ORG_B,
                account_id=ACCOUNT_B,
                kill_switch_active=True,
            ),
            market=market_action(required_action_evidence_fresh=False),
            configuration=paper_configuration(enable_real_trading=True),
        )
    )
    assert result.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_KILL_SWITCH,)
    assert assessment.state is SetupAssessmentState.CONFIRMED_SETUP


def test_weekly_loss_blocks_with_distinct_reason() -> None:
    result = _service().evaluate(
        eligibility_command(risk=risk_snapshot(weekly_loss_pct=Decimal("8")))
    )
    assert result.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_WEEKLY_LOSS,)
    assert result.paper_actionable is False


def test_daily_loss_percent_path_does_not_block() -> None:
    """Realized daily loss is not a refusal; the 10% equity stop is (AT-ADR-152)."""
    result = _service().evaluate(
        eligibility_command(
            risk=risk_snapshot(
                daily_locked=True,
                daily_loss_limit=None,
                realized_pnl_today=Decimal("-300"),
            )
        )
    )
    assert EligibilityReasonCode.BLOCKED_DAILY_LOSS not in result.eligibility.reason_codes
    assert result.eligibility.state is ActionEligibilityState.ELIGIBLE


def test_cooldown_and_portfolio_conflict_block() -> None:
    cooldown = _service().evaluate(eligibility_command(risk=risk_snapshot(cooldown_active=True)))
    conflict = _service().evaluate(eligibility_command(risk=risk_snapshot(portfolio_conflict=True)))
    assert cooldown.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_COOLDOWN,)
    assert conflict.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_PORTFOLIO_CONFLICT,)


def test_inactive_account_and_user_mismatch_block() -> None:
    inactive = _service().evaluate(
        eligibility_command(account=account_identity(account_active=False))
    )
    user = _service().evaluate(
        eligibility_command(account=account_identity(user_id=USER_B), risk=risk_snapshot())
    )
    assert inactive.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_ACCOUNT_STATE,)
    assert user.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_ACCOUNT_STATE,)


def test_elapsed_action_evidence_and_stale_basis_block_data_quality() -> None:
    elapsed = _service().evaluate(
        eligibility_command(market=market_action(action_evidence_valid_until=EVALUATED_AT))
    )
    stale_basis = _service().evaluate(eligibility_command(market=market_action(basis_fresh=False)))
    assert elapsed.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_DATA_QUALITY,)
    assert stale_basis.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_DATA_QUALITY,)


def test_active_candidate_past_ttl_expires() -> None:
    result = _service(now=VALID_UNTIL).evaluate(
        eligibility_command(
            market=market_action(action_evidence_valid_until=VALID_UNTIL + timedelta(minutes=1))
        )
    )
    assert result.eligibility.state is ActionEligibilityState.EXPIRED
    assert result.eligibility.reason_codes == (EligibilityReasonCode.EXPIRED,)
    assert result.paper_actionable is False


def test_non_active_candidate_is_blocked_not_eligible() -> None:
    window, assessment, candidate = confirmed_bundle()
    rejected = _reject_candidate(candidate)
    result = _service().evaluate(
        eligibility_command(window=window, assessment=assessment, candidate=rejected)
    )
    assert result.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_CANDIDATE_STATE,)
    assert result.paper_actionable is False
    assert assessment.state is SetupAssessmentState.CONFIRMED_SETUP


def test_negative_basis_bps_blocks() -> None:
    result = _service().evaluate(
        eligibility_command(
            market=market_action(
                execution_venue=VenueId.BLOFIN,
                execution_price=None,
                basis_bps=Decimal("-21"),
            )
        )
    )
    assert result.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_BASIS,)


def test_conflicting_stated_and_computed_basis_fails_closed() -> None:
    with pytest.raises(ConflictingActionEligibilityError):
        _service().evaluate(
            eligibility_command(
                market=market_action(
                    execution_venue=VenueId.BLOFIN,
                    execution_price=Decimal("100210"),
                    basis_bps=Decimal("1"),
                )
            )
        )


def test_read_only_and_demo_configuration_boundaries() -> None:
    read_only = _service().evaluate(
        eligibility_command(
            configuration=paper_configuration(execution_mode=ExecutionMode.READ_ONLY)
        )
    )
    demo = _service().evaluate(
        eligibility_command(
            configuration=paper_configuration(exchange_mode=ExchangeMode.PAPER_EXCHANGE_DEMO)
        )
    )
    assert read_only.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_CONFIGURATION,)
    assert read_only.live_executable is False
    assert demo.eligibility.state is ActionEligibilityState.ELIGIBLE
    assert demo.paper_actionable is True
    assert demo.live_executable is False


def test_tampered_assessment_hash_fails_closed() -> None:
    window, assessment, candidate = confirmed_bundle()
    tampered_hash = assessment.model_copy(update={"content_hash": "ab" * 32})
    tampered_state = assessment.model_copy(update={"state": SetupAssessmentState.WATCH})
    assert tampered_state.content_hash == assessment.content_hash
    with pytest.raises(ActionEligibilityLineageError):
        _service().evaluate(
            eligibility_command(window=window, assessment=tampered_hash, candidate=candidate)
        )
    with pytest.raises(ActionEligibilityLineageError):
        _service().evaluate(
            eligibility_command(window=window, assessment=tampered_state, candidate=candidate)
        )


def test_candidate_direction_mismatch_fails_closed() -> None:
    window, assessment, candidate = confirmed_bundle()
    payload = candidate.model_dump()
    payload["direction"] = TradeDirection.LONG
    payload["content_hash"] = "0" * 64
    flipped = build_candidate(**payload)
    with pytest.raises(ActionEligibilityLineageError):
        _service().evaluate(
            eligibility_command(window=window, assessment=assessment, candidate=flipped)
        )


def test_assessment_organization_mismatch_fails_closed() -> None:
    window = make_evidence_window()
    assessment = make_assessment(window, organization_id=ORG_B)
    candidate = synthetic_candidate(assessment, window)
    with pytest.raises(ActionEligibilityLineageError):
        _service().evaluate(
            eligibility_command(window=window, assessment=assessment, candidate=candidate)
        )


def test_reused_risk_snapshot_id_with_changed_facts_fails_closed() -> None:
    window, assessment, candidate = confirmed_bundle()
    store = InMemoryActionEligibilityStore()
    service = _service(store=store)
    first = service.evaluate(
        eligibility_command(window=window, assessment=assessment, candidate=candidate)
    )
    with pytest.raises(ConflictingActionEligibilityError):
        service.evaluate(
            eligibility_command(
                window=window,
                assessment=assessment,
                candidate=candidate,
                risk=risk_snapshot(daily_locked=True),
            )
        )
    history = service.history(
        organization_id=ORG_ID, account_id=ACCOUNT_ID, candidate_id=candidate.candidate_id
    )
    assert len(history) == 1
    assert history[0].content_hash == first.content_hash
    assert history[0].eligibility.state is ActionEligibilityState.ELIGIBLE


def test_reused_venue_state_and_safety_epoch_fail_closed() -> None:
    window, assessment, candidate = confirmed_bundle()
    store = InMemoryActionEligibilityStore()
    service = _service(store=store)
    service.evaluate(eligibility_command(window=window, assessment=assessment, candidate=candidate))
    with pytest.raises(ConflictingActionEligibilityError):
        service.evaluate(
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
    with pytest.raises(ConflictingActionEligibilityError):
        service.evaluate(
            eligibility_command(
                window=window,
                assessment=assessment,
                candidate=candidate,
                safety=safety_snapshot(kill_switch_active=True),
            )
        )


def test_new_venue_state_id_creates_distinct_revision() -> None:
    window, assessment, candidate = confirmed_bundle()
    store = InMemoryActionEligibilityStore()
    service = _service(store=store)
    first = service.evaluate(
        eligibility_command(window=window, assessment=assessment, candidate=candidate)
    )
    second = service.evaluate(
        eligibility_command(
            window=window,
            assessment=assessment,
            candidate=candidate,
            market=market_action(
                venue_state_id=VENUE_STATE_B,
                required_action_evidence_fresh=False,
            ),
        )
    )
    assert first.evaluation_revision == 1
    assert second.evaluation_revision == 2
    assert second.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_DATA_QUALITY,)
    assert first.eligibility.state is ActionEligibilityState.ELIGIBLE


def test_correlation_id_is_excluded_from_uniqueness() -> None:
    window, assessment, candidate = confirmed_bundle()
    store = InMemoryActionEligibilityStore()
    service = _service(store=store)
    first = service.evaluate(
        eligibility_command(
            window=window,
            assessment=assessment,
            candidate=candidate,
            correlation_id=CORRELATION_A,
        )
    )
    second = service.evaluate(
        eligibility_command(
            window=window,
            assessment=assessment,
            candidate=candidate,
            correlation_id=CORRELATION_B,
        )
    )
    assert (
        uniqueness_hash(
            eligibility_command(
                window=window,
                assessment=assessment,
                candidate=candidate,
                correlation_id=CORRELATION_B,
            )
        )
        == first.uniqueness_hash
    )
    assert first.content_hash == second.content_hash
    assert first.eligibility.correlation_id == CORRELATION_A
    assert (
        len(
            service.history(
                organization_id=ORG_ID, account_id=ACCOUNT_ID, candidate_id=candidate.candidate_id
            )
        )
        == 1
    )


def test_tenant_history_isolation() -> None:
    window, assessment, candidate = confirmed_bundle()
    service = _service()
    eligible = service.evaluate(
        eligibility_command(window=window, assessment=assessment, candidate=candidate)
    )
    blocked = service.evaluate(
        eligibility_command(
            window=window,
            assessment=assessment,
            candidate=candidate,
            account=account_identity(organization_id=ORG_B, user_id=USER_B, account_id=ACCOUNT_B),
            portfolio=portfolio_state(organization_id=ORG_B, account_id=ACCOUNT_B),
            risk=risk_snapshot(
                risk_snapshot_id=RISK_SNAPSHOT_B,
                organization_id=ORG_B,
                user_id=USER_B,
                account_id=ACCOUNT_B,
            ),
            safety=safety_snapshot(organization_id=ORG_B, account_id=ACCOUNT_B),
        )
    )
    assert eligible.eligibility.state is ActionEligibilityState.ELIGIBLE
    assert blocked.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_ACCOUNT_STATE,)
    owned = service.history(
        organization_id=ORG_ID, account_id=ACCOUNT_ID, candidate_id=candidate.candidate_id
    )
    foreign = service.history(
        organization_id=ORG_B, account_id=ACCOUNT_B, candidate_id=candidate.candidate_id
    )
    assert len(owned) == 1
    assert owned[0].eligibility.state is ActionEligibilityState.ELIGIBLE
    assert len(foreign) == 1
    assert foreign[0].eligibility.state is ActionEligibilityState.BLOCKED


def test_replay_does_not_mutate_historical_eligible_after_clock_advance() -> None:
    window, assessment, candidate = confirmed_bundle()
    store = InMemoryActionEligibilityStore()
    first = _service(store=store).evaluate(
        eligibility_command(window=window, assessment=assessment, candidate=candidate)
    )
    later = VALID_UNTIL + timedelta(seconds=1)
    replayed = _service(now=later, store=store).evaluate(
        eligibility_command(window=window, assessment=assessment, candidate=candidate)
    )
    assert replayed.content_hash == first.content_hash
    assert replayed.eligibility.state is ActionEligibilityState.ELIGIBLE
    assert first.currently_paper_actionable(EVALUATED_AT) is True
    assert replayed.currently_paper_actionable(later) is False


def test_live_executable_cannot_be_constructed() -> None:
    result = _service().evaluate(eligibility_command())
    payload = result.model_dump()
    payload["live_executable"] = True
    with pytest.raises(ValidationError):
        ActionEligibilityEvaluation.model_validate(payload)


def test_exposure_at_capacity_blocks_below_capacity_eligible() -> None:
    blocked = _service().evaluate(
        eligibility_command(portfolio=portfolio_state(open_exposure_notional=Decimal("500")))
    )
    eligible = _service().evaluate(
        eligibility_command(portfolio=portfolio_state(open_exposure_notional=Decimal("499")))
    )
    assert blocked.eligibility.reason_codes == (EligibilityReasonCode.BLOCKED_EXPOSURE,)
    assert eligible.eligibility.state is ActionEligibilityState.ELIGIBLE
