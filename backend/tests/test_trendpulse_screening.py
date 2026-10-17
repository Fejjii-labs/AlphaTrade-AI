"""Disposable committed PostgreSQL fixtures: restart, uniqueness and research fences."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.experiments.models import ExperimentSampleRow
from app.schemas.trendpulse_screening import TrendPulseScreeningCreate
from app.strategy_brain.trendpulse_1r.contracts import TrendPulseStatus
from app.strategy_brain.trendpulse_screening.acquisition import BinanceScreeningAcquirer
from app.strategy_brain.trendpulse_screening.models import TrendPulseScreeningRow
from app.strategy_brain.trendpulse_screening.service import TrendPulseScreeningService
from tests.support.experiment_fixtures import World
from tests.support.experiment_fixtures import (
    experiment_engine as _experiment_engine,  # noqa: F401
)
from tests.support.trendpulse_screening import BinanceTransport, DelayedReplayAcquirer, after_close
from tests.support.trendpulse_screening import screening_budget as _screening_budget  # noqa: F401
from tests.test_trendpulse_1r_adapter import END, path, replace_bar
from tests.test_trendpulse_1r_domain import configure


@pytest.fixture(name="screening_world")
def screening_world(experiment_engine):
    with Session(experiment_engine, expire_on_commit=False) as session:
        w = World(session)
        w.now = after_close()
        w.version = w.create(configure(w))
        w.settings = w.settings.model_copy(
            update={
                "trendpulse_screening_enabled": True,
                "perpetual_evidence_source": "binance_usdm",
                "perpetual_evidence_secondary_source": "none",
                "market_data_enabled": True,
            }
        )
        session.commit()  # A new independent connection must see the exact records.
        yield w


def request(**changes):
    return TrendPulseScreeningCreate(
        request_id=uuid4(), variant_key="baseline", trigger_end=END, **changes
    )


def service(w, source=None, session=None):
    return TrendPulseScreeningService(
        session or w.session,
        w.settings,
        acquirer=source or DelayedReplayAcquirer(),
        clock=lambda: w.now,
    )


def screen(w, s, body=None):
    result = s.screen(w.tenant, w.version.experiment_id, w.version.id, body or request())
    s.session.commit()
    return result


def test_real_get_acquisition_detector_durable_write_and_read_consumer(screening_world):
    w = screening_world
    provider = BinanceTransport()
    result = screen(
        w, service(w, BinanceScreeningAcquirer(transport=provider.transport, clock=lambda: w.now))
    )
    assert result.status is TrendPulseStatus.QUALIFIED
    assert (
        result.signal.signal.target - result.signal.signal.entry
        == result.signal.signal.entry - result.signal.signal.structural_stop
    )
    assert len(provider.calls) == 5 and result.evidence_mode == "public_rest"
    assert result.trend_receipts == 250 and result.entry_receipts == 60
    assert not result.execution_authorized and not result.sample_eligible
    assert result.performance is None and not result.signal.native_execution
    assert w.session.scalar(select(func.count()).select_from(ExperimentSampleRow)) == 0
    assert service(w).detail(w.tenant, result.id) == result


def test_restart_and_new_request_deduplicate_without_rewriting_first_receipts(
    screening_world, experiment_engine
):
    w = screening_world
    source = DelayedReplayAcquirer()
    body = request()
    first = screen(w, service(w, source), body)
    assert first.status is TrendPulseStatus.QUALIFIED
    original = first.evidence.model_dump(mode="json")
    with Session(experiment_engine) as restarted:
        restarted_source = DelayedReplayAcquirer()
        s = service(w, restarted_source, restarted)
        assert screen(w, s, body) == first
        assert restarted_source.calls == 0
        duplicate = screen(w, s)
        assert duplicate.status is TrendPulseStatus.DUPLICATE
        assert duplicate.signal is None and duplicate.signal_id == first.signal_id
        assert duplicate.duplicate_of == first.id
        assert s.detail(w.tenant, first.id).evidence.model_dump(mode="json") == original
        assert (
            restarted.scalar(
                select(func.count())
                .select_from(TrendPulseScreeningRow)
                .where(
                    TrendPulseScreeningRow.version_id == w.version.id,
                    TrendPulseScreeningRow.status == "qualified_research_signal",
                )
            )
            == 1
        )


def test_database_guards_uniqueness_and_original_arrival_history(screening_world):
    w = screening_world
    first = screen(w, service(w))
    stored = w.session.get(TrendPulseScreeningRow, first.id)
    with pytest.raises(ConflictError), w.session.begin_nested():
        stored.reason = "rewritten"
        w.session.flush()
    with pytest.raises(DBAPIError, match="screening_immutable"), w.session.begin_nested():
        w.session.execute(
            text("UPDATE trendpulse_screening_runs SET evidence='{}' WHERE id=:id"),
            {"id": first.id},
        )
    with pytest.raises(DBAPIError, match="screening_immutable"), w.session.begin_nested():
        w.session.execute(
            text("DELETE FROM trendpulse_screening_runs WHERE id=:id"), {"id": first.id}
        )
    values = {c.name: getattr(stored, c.name) for c in stored.__table__.columns if c.name != "id"}
    values["request_id"] = uuid4()
    with pytest.raises(IntegrityError), w.session.begin_nested():
        w.session.add(TrendPulseScreeningRow(**values))
        w.session.flush()
    assert service(w).detail(w.tenant, first.id) == first


def test_later_public_arrivals_deduplicate_and_keep_each_original_receipt(screening_world):
    w = screening_world
    first_provider = BinanceTransport()
    first = screen(
        w,
        service(
            w, BinanceScreeningAcquirer(transport=first_provider.transport, clock=lambda: w.now)
        ),
    )
    w.now += timedelta(seconds=1)
    later_provider = BinanceTransport()
    later = screen(
        w,
        service(
            w, BinanceScreeningAcquirer(transport=later_provider.transport, clock=lambda: w.now)
        ),
    )
    assert later.status is TrendPulseStatus.DUPLICATE and later.duplicate_of == first.id
    assert later.evidence_hash != first.evidence_hash
    assert later.evidence.entry_observations[-1].receive_time == w.now
    assert first.evidence.entry_observations[-1].receive_time == w.now - timedelta(seconds=1)
    assert service(w).detail(w.tenant, first.id) == first


@pytest.mark.parametrize(
    "failure,reason",
    [
        ("rate_limit", "acquisition_ratelimited"),
        ("row_bound", "acquisition_row_bound"),
        ("changed_confirmation", "acquisition_formingcandle"),
    ],
)
def test_public_rejections_are_persisted_with_partial_evidence(screening_world, failure, reason):
    w = screening_world
    provider = BinanceTransport(failure=failure)
    s = service(w, BinanceScreeningAcquirer(transport=provider.transport, clock=lambda: w.now))
    result = screen(w, s)
    assert result.status is TrendPulseStatus.UNAVAILABLE and result.reason == reason
    assert result.signal is None and result.performance is None
    assert s.detail(w.tenant, result.id).reason == reason
    if failure == "rate_limit":
        assert result.trend_receipts == 250 and result.entry_receipts == 0


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("expired", "trigger_expired"),
        ("future", "trigger_not_closed"),
        ("settlement", "provider_settlement_pending"),
        ("missing", "missing_causal_history"),
        ("weak", "trend_not_confirmed"),
    ],
)
def test_detector_and_clock_rejections_are_durable(screening_world, kind, reason):
    w = screening_world
    inputs = path(trend_step="0.005") if kind == "weak" else path()
    if kind == "expired":
        w.now = END + timedelta(seconds=60)
    elif kind == "future":
        w.now = END - timedelta(seconds=1)
    elif kind == "settlement":
        w.now = END + timedelta(seconds=2)
    elif kind == "missing":
        inputs["trend_bars"] = inputs["trend_bars"][1:]
        inputs["trend_observations"] = inputs["trend_observations"][1:]
    source = DelayedReplayAcquirer(inputs)
    result = screen(w, service(w, source))
    assert result.reason == reason
    assert result.signal is None and result.performance is None
    assert source.calls == (1 if kind in {"weak", "missing"} else 0)
    assert service(w).detail(w.tenant, result.id) == result


def test_request_conflicts_and_organization_user_isolation(screening_world):
    w = screening_world
    body = request()
    s = service(w)
    first = screen(w, s, body)
    with pytest.raises(ConflictError, match="reused"):
        s.screen(
            w.tenant,
            w.version.experiment_id,
            w.version.id,
            body.model_copy(update={"trigger_end": END + timedelta(minutes=5)}),
        )
    for tenant in (replace(w.tenant, organization_id=uuid4()), replace(w.tenant, user_id=uuid4())):
        with pytest.raises(NotFoundError):
            s.detail(tenant, first.id)
        with pytest.raises(NotFoundError):
            s.list(tenant, w.version.experiment_id, w.version.id)


def test_disabled_default_does_not_acquire_or_write_and_read_remains_available(screening_world):
    w = screening_world
    first = screen(w, service(w))
    w.settings = w.settings.model_copy(update={"trendpulse_screening_enabled": False})
    source = DelayedReplayAcquirer()
    s = service(w, source)
    with pytest.raises(AppError) as error:
        screen(w, s)
    assert error.value.code == "screening_disabled" and source.calls == 0
    assert s.detail(w.tenant, first.id) == first
    assert s.list(w.tenant, w.version.experiment_id, w.version.id).total == 1


def test_public_screening_requires_explicit_source_with_no_secondary_fallback(screening_world):
    w = screening_world
    provider = BinanceTransport()
    for changes in (
        {"perpetual_evidence_source": "replay"},
        {"market_data_enabled": False},
        {"perpetual_evidence_secondary_source": "replay"},
    ):
        settings = w.settings.model_copy(update=changes)
        s = TrendPulseScreeningService(
            w.session,
            settings,
            acquirer=BinanceScreeningAcquirer(transport=provider.transport, clock=lambda: w.now),
            clock=lambda: w.now,
        )
        with pytest.raises(AppError) as error:
            screen(w, s)
        assert error.value.code == "screening_source_not_enabled"
        assert provider.calls == []
    assert service(w).list(w.tenant, w.version.experiment_id, w.version.id).total == 0


def test_single_flight_is_cross_process_nonblocking_and_releases_on_completion(
    screening_world, experiment_engine
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    w = screening_world
    entered, release = Event(), Event()
    source = DelayedReplayAcquirer()

    class Blocked:
        mode = "replay"
        provenance = "synthetic_fixture"

        def acquire(self, spec, trigger_end):
            entered.set()
            assert release.wait(timeout=5)
            return source.acquire(spec, trigger_end)

    def run():
        with Session(experiment_engine) as session:
            return screen(w, service(w, Blocked(), session))

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(run)
        try:
            assert entered.wait(timeout=5)
            # Independent SQL connection; reads progress while acquisition stays blocked.
            with Session(experiment_engine) as session:
                s = service(w, DelayedReplayAcquirer(), session)
                assert s.list(w.tenant, w.version.experiment_id, w.version.id).total == 0
                with pytest.raises(ConflictError) as error:
                    s.screen(w.tenant, w.version.experiment_id, w.version.id, request())
                assert error.value.code == "screening_in_progress"
                assert s.acquirer.calls == 0
                assert not release.is_set() and not pending.done()
        finally:
            release.set()
        first = pending.result(timeout=5)
    assert first.status is TrendPulseStatus.QUALIFIED and source.calls == 1
    assert screen(w, service(w)).status is TrendPulseStatus.DUPLICATE


def test_failed_acquisition_releases_capacity_and_new_request_can_complete(screening_world):
    w = screening_world
    broken = BinanceTransport(failure="rate_limit")
    assert (
        screen(
            w, service(w, BinanceScreeningAcquirer(transport=broken.transport, clock=lambda: w.now))
        ).status
        is TrendPulseStatus.UNAVAILABLE
    )
    recovered = screen(w, service(w))
    assert recovered.status is TrendPulseStatus.QUALIFIED
    page = service(w).list(
        w.tenant, w.version.experiment_id, w.version.id, status=TrendPulseStatus.UNAVAILABLE
    )
    assert page.total == 1 and page.items[0].reason == "acquisition_ratelimited"


def test_expiration_during_acquisition_is_recorded_at_the_actual_later_decision(screening_world):
    w = screening_world
    source = DelayedReplayAcquirer()

    class Late:
        mode = "replay"
        provenance = "synthetic_fixture"

        def acquire(self, spec, trigger_end):
            evidence = source.acquire(spec, trigger_end)
            w.now = END + timedelta(seconds=61)
            return evidence

    result = screen(w, service(w, Late()))
    assert result.reason == "trigger_expired" and result.status is TrendPulseStatus.REFUSED
    assert result.decision_at == END + timedelta(seconds=61)
    assert result.entry_receipts == 60 and result.signal is None
    assert result.evidence.entry_observations[-1].receive_time == END + timedelta(seconds=3)


def test_promotion_keeps_research_outside_the_fresh_validation_sample(screening_world):
    from app.schemas.experiments import ExperimentPromotion

    w = screening_world
    w.version = w.transition(w.approve(w.transition(w.version, "submit")), "start")
    first = screen(w, service(w))
    assert w.version.sample_counts == {"baseline": 0}
    # Independent trusted fixture sample, not the screening signal or receipts.
    w.sample(w.version, "independent-fixture-proof")
    completed = w.transition(w.version, "complete")
    promoted = w.service.promote(
        w.tenant,
        completed.experiment_id,
        completed.id,
        ExperimentPromotion(expected_revision=completed.revision, variant_key="baseline"),
    )
    w.session.commit()
    w.version = promoted
    second = screen(w, service(w))
    assert second.status is TrendPulseStatus.QUALIFIED  # A research study of the same event.
    assert second.signal_id == first.signal_id
    assert second.experiment_version_id != first.experiment_version_id
    assert second.signal.sample_group_id != first.signal.sample_group_id
    assert promoted.sample_counts == {"baseline": 0}
    assert not second.sample_eligible and not second.signal.sample_eligible
    assert second.performance is None


def test_conflicting_derivation_is_persisted_as_refusal_and_cannot_replace_the_signal(
    screening_world,
):
    from decimal import Decimal

    w = screening_world
    first = screen(w, service(w))
    changed = replace_bar(path(), "entry", -1, close=Decimal("125.61"))
    result = screen(w, service(w, DelayedReplayAcquirer(changed)))
    assert result.status is TrendPulseStatus.REFUSED
    assert result.reason == "persisted_signal_derivation_conflict" and result.signal is None
    assert service(w).detail(w.tenant, first.id) == first
    page = service(w).list(
        w.tenant, w.version.experiment_id, w.version.id, status=TrendPulseStatus.QUALIFIED
    )
    assert page.total == 1
