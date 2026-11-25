"""Retired pins never gate fresh evidence or authorize Candidates."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, Organization
from app.db.setup_lifetime import SetupLifetimePin
from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.evidence_pipeline.setup_lifetime import (
    SetupLifetimeStore,
    SetupTriggerPin,
    expiry_bars,
    setup_trigger_lifetime_elapsed,
)
from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.errors import FormingCandleError, MarketContractError
from app.market_contracts.evidence_diagnostics import (
    DiagnosticReason,
    DiagnosticStatus,
    EvidenceComponent,
    diagnostic_from_exception,
)
from app.market_contracts.first_slice import CANONICAL_EVALUATED_AT, CANONICAL_TRIGGER_INTERVAL_END
from app.market_contracts.identity import interval_timedelta
from app.persistence.setup_lifetime import SqlAlchemySetupLifetimeStore
from app.schemas.common import Timeframe
from app.signal_fusion.lifecycle import CandidateLifecycleService
from app.signal_fusion.memory import InMemoryCandidateRepository
from app.watcher.contracts import EvaluationMode
from app.watcher.fusion_evaluation import BoundEvaluationClock, WatcherFusionEvaluationService
from tests.support.phase5_market import RECORDED_FIXTURE_GRACE
from tests.support.phase6_fusion import ORG_ID
from tests.support.postgres_persistence import postgres_available, requires_postgres
from tests.test_live_evidence_pipeline import (
    _evaluation_command,
    _fixture_usdm_handler,
    _non_placeholder_executable,
)
from tests.test_setup_lifetime_postgres import _engine, _lifetime_key, _policy, _seed_org


@pytest.fixture(params=("memory", "postgres"))
def lifetime(request):
    if request.param == "memory":
        yield SetupLifetimeStore(), None
        return
    if not postgres_available():
        pytest.skip("Disposable PostgreSQL is required for durable pin tests")
    factory = _engine()
    with factory() as session:
        _seed_org(session)
        yield SqlAlchemySetupLifetimeStore(session), session


def pin(*, end=CANONICAL_TRIGGER_INTERVAL_END, expired=False):
    return SetupTriggerPin(trigger_end=end, trigger_bar_hash="ab" * 32, expired=expired)


def source(*, omit_trades=False):
    return BinanceUsdmPerpetualSource(
        transport=_fixture_usdm_handler(omit_trades=omit_trades),
        max_retries=0,
        settlement_grace=RECORDED_FIXTURE_GRACE,
    )


def retired_audits(session):
    return session.scalars(
        select(AuditLog).where(AuditLog.resource_type == "setup_lifetime_pin")
    ).all()


def test_already_expired_pin_remains_observable_but_never_active(lifetime):
    store, _ = lifetime
    key = _lifetime_key(_policy())
    store.remember(key, pin(expired=True))
    assert store.get(key).expired is True
    assert store.get(key).trigger_end == CANONICAL_TRIGGER_INTERVAL_END
    assert store.active_trigger_end(key) is None


def test_current_pin_is_active_and_expiry_cannot_resurrect_it(lifetime):
    store, session = lifetime
    key = _lifetime_key(_policy())
    store.remember(key, pin())
    original = store.get(key)
    assert store.active_trigger_end(key) == CANONICAL_TRIGGER_INTERVAL_END
    store.expire(key)
    store.expire(key)
    store.remember(key, pin())
    assert store.active_trigger_end(key) is None
    assert store.get(key).expired is True
    assert store.get(key).required_lineage_hash == original.required_lineage_hash
    if session is not None:
        assert len(retired_audits(session)) == 1
        assert retired_audits(session)[0].redacted_metadata["required_lineage_hash"] == (
            original.required_lineage_hash
        )


def test_later_trigger_preserves_legacy_expired_lineage(lifetime):
    store, session = lifetime
    key = _lifetime_key(_policy())
    store.remember(key, pin(expired=True))
    retired = store.get(key)
    store.remember(key, pin(end=CANONICAL_TRIGGER_INTERVAL_END + timedelta(minutes=15)))
    store.remember(key, pin())
    assert store.active_trigger_end(key) == CANONICAL_TRIGGER_INTERVAL_END + timedelta(minutes=15)
    if session is not None:
        history = retired_audits(session)
        assert len(history) == 1
        assert history[0].payload_hash == retired.required_lineage_hash
        assert history[0].redacted_metadata["trigger_end"] == retired.trigger_end.isoformat()
        assert history[0].redacted_metadata["expired"] is True


@pytest.mark.parametrize(
    "offset,expected",
    [
        (-timedelta(microseconds=1), False),
        (timedelta(0), True),
        (timedelta(microseconds=1), True),
    ],
)
def test_canonical_expiry_boundary(offset, expected):
    boundary = CANONICAL_TRIGGER_INTERVAL_END + interval_timedelta(Timeframe.M15) * expiry_bars()
    assert (
        setup_trigger_lifetime_elapsed(
            CANONICAL_TRIGGER_INTERVAL_END, latest_closed_end=boundary + offset
        )
        is expected
    )


def test_stale_false_pin_recovers_in_same_scan_without_changing_evidence(lifetime):
    store, session = lifetime
    policy = _policy()
    key = _lifetime_key(policy)
    stale = pin(end=CANONICAL_TRIGGER_INTERVAL_END - timedelta(days=7))
    store.remember(key, stale)
    original = store.get(key)
    expected = FirstSliceEvidenceAssembler(source(), replay=False).assemble(
        organization_id=ORG_ID, policy=policy, evaluated_at=CANONICAL_EVALUATED_AT
    )
    assembler = FirstSliceEvidenceAssembler(source(), replay=False, lifetime=store)
    recovered = assembler.assemble(
        organization_id=ORG_ID, policy=policy, evaluated_at=CANONICAL_EVALUATED_AT
    )
    assert recovered.trigger_bar.interval_end == CANONICAL_TRIGGER_INTERVAL_END
    assert recovered.evidence_window_hash == expected.evidence_window_hash
    assert original.trigger_bar_hash == stale.trigger_bar_hash
    assert store.active_trigger_end(key) == CANONICAL_TRIGGER_INTERVAL_END
    assert any(
        row.component is EvidenceComponent.TRIGGER and row.reason_code is DiagnosticReason.STALE
        for row in assembler.diagnostics
    )
    assert not any(
        row.reason_code is DiagnosticReason.FORMING_CANDLE for row in assembler.diagnostics
    )
    if session is not None:
        history = retired_audits(session)
        assert len(history) == 1
        assert history[0].redacted_metadata["expired"] is True
        assert history[0].redacted_metadata["trigger_end"] == original.trigger_end.isoformat()
        assert history[0].redacted_metadata["trigger_bar_hash"] == original.trigger_bar_hash
        assert (
            history[0].redacted_metadata["required_lineage_hash"] == original.required_lineage_hash
        )
        assert len(session.scalars(select(SetupLifetimePin)).all()) == 1
    # A repeated scan is pinned to current evidence and neither repeats retirement
    # nor revives the old trigger. Canonical hashes remain deterministic.
    again = assembler.assemble(
        organization_id=ORG_ID, policy=policy, evaluated_at=CANONICAL_EVALUATED_AT
    )
    assert again.evidence_window_hash == recovered.evidence_window_hash
    store.remember(key, stale)
    assert store.active_trigger_end(key) == CANONICAL_TRIGGER_INTERVAL_END
    if session is not None:
        assert len(retired_audits(session)) == 1


@pytest.mark.parametrize("scope", ("tenant", "strategy"))
def test_recovery_does_not_expire_another_scope(lifetime, scope):
    store, session = lifetime
    policy = _policy()
    key = _lifetime_key(policy)
    other = replace(
        key, **{"organization_id" if scope == "tenant" else "strategy_version_id": uuid4()}
    )
    if session is not None and scope == "tenant":
        session.add(Organization(id=other.organization_id, name="Other lifetime tenant"))
        session.flush()
    store.remember(key, pin(end=CANONICAL_TRIGGER_INTERVAL_END - timedelta(days=7)))
    store.remember(other, pin())
    FirstSliceEvidenceAssembler(source(), replay=False, lifetime=store).assemble(
        organization_id=ORG_ID, policy=policy, evaluated_at=CANONICAL_EVALUATED_AT
    )
    assert store.get(other).expired is False
    assert store.active_trigger_end(other) == CANONICAL_TRIGGER_INTERVAL_END


def test_missing_pin_inside_lifetime_is_not_silently_discarded(lifetime):
    store, _ = lifetime
    policy = _policy()
    key = _lifetime_key(policy)
    future = pin(end=CANONICAL_TRIGGER_INTERVAL_END + timedelta(minutes=15))
    store.remember(key, future)
    with pytest.raises(FormingCandleError) as error:
        FirstSliceEvidenceAssembler(source(), replay=False, lifetime=store).assemble(
            organization_id=ORG_ID, policy=policy, evaluated_at=CANONICAL_EVALUATED_AT
        )
    assert diagnostic_from_exception(error.value).component is EvidenceComponent.TRIGGER
    assert store.get(key).expired is False


def test_explicit_old_trigger_cannot_expire_a_different_current_pin(lifetime):
    store, session = lifetime
    policy = _policy()
    key = _lifetime_key(policy)
    store.remember(key, pin())
    original = store.get(key)
    result = FirstSliceEvidenceAssembler(source(), replay=False, lifetime=store).assemble(
        organization_id=ORG_ID,
        policy=policy,
        evaluated_at=CANONICAL_EVALUATED_AT,
        setup_trigger_end=CANONICAL_TRIGGER_INTERVAL_END - timedelta(days=7),
    )
    assert result.trigger_bar.interval_end == CANONICAL_TRIGGER_INTERVAL_END
    assert store.get(key).expired is False
    # The verified current candle can correct its pin hash under the existing
    # convergence rule; the stale explicit request cannot create an expiry audit.
    assert store.get(key).trigger_end == original.trigger_end
    if session is not None:
        assert retired_audits(session) == []


def test_actual_current_forming_candle_still_fails_closed(lifetime):
    store, _ = lifetime
    policy = _policy()
    key = _lifetime_key(policy)
    store.remember(key, pin(end=CANONICAL_TRIGGER_INTERVAL_END - timedelta(days=7)))
    with pytest.raises(FormingCandleError) as error:
        FirstSliceEvidenceAssembler(source(), replay=False, lifetime=store).assemble(
            organization_id=ORG_ID,
            policy=policy,
            evaluated_at=CANONICAL_TRIGGER_INTERVAL_END - timedelta(microseconds=1),
        )
    diagnostic = diagnostic_from_exception(error.value)
    assert diagnostic.component is EvidenceComponent.OHLCV_15M
    assert diagnostic.reason_code is DiagnosticReason.FORMING_CANDLE
    assert store.get(key).expired is False


@requires_postgres
def test_restart_preserves_expiry_when_current_trades_are_unavailable():
    factory = _engine()
    policy = _policy()
    key = _lifetime_key(policy)
    with factory() as session:
        _seed_org(session)
        store = SqlAlchemySetupLifetimeStore(session)
        store.remember(key, pin(end=CANONICAL_TRIGGER_INTERVAL_END - timedelta(days=7)))
        original = store.get(key)
        session.commit()
    with factory() as restarted:
        store = SqlAlchemySetupLifetimeStore(restarted)
        with pytest.raises(MarketContractError):
            FirstSliceEvidenceAssembler(
                source(omit_trades=True), replay=False, lifetime=store
            ).assemble(organization_id=ORG_ID, policy=policy, evaluated_at=CANONICAL_EVALUATED_AT)
        restarted.commit()
    with factory() as after_expiry:
        store = SqlAlchemySetupLifetimeStore(after_expiry)
        assert store.get(key).expired is True
        assert store.get(key).required_lineage_hash == original.required_lineage_hash
        assert store.active_trigger_end(key) is None
        result = FirstSliceEvidenceAssembler(source(), replay=False, lifetime=store).assemble(
            organization_id=ORG_ID, policy=policy, evaluated_at=CANONICAL_EVALUATED_AT
        )
        after_expiry.commit()
        assert result.trigger_bar.interval_end == CANONICAL_TRIGGER_INTERVAL_END
        assert len(retired_audits(after_expiry)) == 1


def test_recovery_alone_does_not_mint_candidate(lifetime):
    store, _ = lifetime
    executable = _non_placeholder_executable(ORG_ID)
    key = _lifetime_key(executable.fusion_policy)
    store.remember(key, pin(end=CANONICAL_TRIGGER_INTERVAL_END - timedelta(days=7)))
    port = AssemblingWatcherScanEvidence(
        FirstSliceEvidenceAssembler(
            source(), replay=False, lifetime=store, clock=lambda: CANONICAL_EVALUATED_AT
        ),
        executable_resolver=lambda command: executable,
    )
    clock = BoundEvaluationClock()
    service = WatcherFusionEvaluationService(
        evidence=port,
        lifecycle=CandidateLifecycleService(
            repository=InMemoryCandidateRepository(), clock=clock.now
        ),
        clock=clock,
    )
    command = _evaluation_command(ORG_ID).model_copy(
        update={"mode": EvaluationMode.PERSIST_EVIDENCE}
    )
    outcome = service.evaluate(command)
    assert port.last_assembly() is not None
    assert outcome.reason_code != "canonical_evidence_unavailable"
    assert outcome.candidate_ids == ()
    persisted = service.persist_confirmed_setup(command, outcome)
    assert persisted.candidate_ids == ()
    assert service.published_candidate_ids == ()
    assert any(
        row.component is EvidenceComponent.RESISTANCE and row.status is DiagnosticStatus.UNAVAILABLE
        for row in outcome.evidence_diagnostics
    )
