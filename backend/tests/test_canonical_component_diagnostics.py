"""Component failures remain fail closed and cannot leak upstream error contents."""

from datetime import timedelta
from uuid import uuid4

import httpx
import pytest

import app.evidence_pipeline.assembler as assembly
from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.evidence_pipeline.canonical import first_slice_read_policy
from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.adapters.bybit_usdt_perpetual import BybitUsdtPerpetualSource
from app.market_contracts.adapters.failover import FailoverPerpetualSource
from app.market_contracts.adapters.replay import ReplayPerpetualSource
from app.market_contracts.cvd import first_slice_baseline_open
from app.market_contracts.derivatives import DerivativeMetric, derivative_observation
from app.market_contracts.enums import FreshnessState, VenueId
from app.market_contracts.errors import (
    IncompleteTradeWindowError,
    IncompleteWarmUpError,
    MarketContractError,
    WrongInstrumentError,
    WrongMarketError,
    WrongSourceError,
)
from app.market_contracts.evidence_diagnostics import (
    MAX_COMPONENT_DIAGNOSTICS,
    DiagnosticReason,
    DiagnosticStatus,
    diagnostic_from_exception,
)
from app.market_contracts.evidence_diagnostics import (
    EvidenceComponent as Component,
)
from app.market_contracts.identity import binance_usdm_btcusdt, bybit_usdt_perpetual_btcusdt
from app.market_contracts.replay_fixtures import canonical_first_slice_fixture
from app.schemas.common import Timeframe
from app.schemas.nested_continuation import EvidenceAvailability as Availability
from app.signal_fusion.enums import EvidenceRole
from app.signal_fusion.lifecycle import CandidateLifecycleService
from app.signal_fusion.memory import InMemoryCandidateRepository
from app.watcher.fusion_evaluation import (
    BoundEvaluationClock,
    WatcherFusionEvaluationService,
)
from tests.support.phase5_market import EVALUATED_AT, RECORDED_FIXTURE_GRACE
from tests.support.phase6_fusion import ORG_ID
from tests.test_live_evidence_pipeline import (
    _evaluation_command,
    _fixture_usdm_handler,
    _non_placeholder_executable,
)
from tests.test_market_intelligence_oi_funding import contract_payload, payload


def replay():
    return FirstSliceEvidenceAssembler(ReplayPerpetualSource(), replay=True)


def test_complete_components_keep_canonical_hash_and_independent_clocks():
    assembler = replay()
    result = assembler.assemble(organization_id=ORG_ID)
    rows = {item.component: item for item in assembler.diagnostics}
    assert rows[Component.OHLCV_15M].timeframe is Timeframe.M15
    assert rows[Component.OHLCV_4H].timeframe is Timeframe.H4
    assert rows[Component.PRICE].source_time == result.current_price.source_time
    assert rows[Component.PRICE].freshness is result.current_price.freshness.state
    assert rows[Component.CVD].source_time == result.cvd.event_time_max
    assert rows[Component.COVERAGE].status is DiagnosticStatus.AVAILABLE
    assert rows[Component.RESISTANCE].reason_code is DiagnosticReason.MISSING
    assert rows[Component.OPEN_INTEREST].status is DiagnosticStatus.NOT_REQUIRED
    assert (
        assembler.assemble(organization_id=ORG_ID).evidence_window_hash
        == result.evidence_window_hash
    )


@pytest.mark.parametrize(
    "target,component",
    [
        ("instrument_for_source", Component.INSTRUMENT),
        ("_trigger_and_subsequent", Component.TRIGGER),
        ("require_complete_window_coverage", Component.COVERAGE),
        ("first_slice_cvd_window", Component.CVD),
        ("bar_signed_quote_flow", Component.SIGNED_FLOW),
        ("evaluate_freshness", Component.FRESHNESS),
        ("quote_current_price", Component.PRICE),
        ("_nearest_resistance_ref", Component.RESISTANCE),
        ("evidence_window_from_assessment_command", Component.CONTRACT),
    ],
)
def test_failed_component_is_exact_and_logs_only_bounded_metadata(
    monkeypatch, capsys, target, component
):
    def missing(*args, **kwargs):
        raise IncompleteWarmUpError("SECRET token upstream-payload https://private.example")

    monkeypatch.setattr(assembly, target, missing)
    assembler = replay()
    with pytest.raises(IncompleteWarmUpError) as exc:
        assembler.assemble(organization_id=ORG_ID)
    diagnostic = diagnostic_from_exception(exc.value)
    assert diagnostic.component is component
    assert diagnostic.status is DiagnosticStatus.UNAVAILABLE
    assert diagnostic.reason_code is DiagnosticReason.WARMUP_INCOMPLETE
    assert diagnostic.provider == "binance-usdm-perpetual-replay"
    assert diagnostic.symbol == "BTCUSDT"
    assert diagnostic.failover_attempted is False
    assert "SECRET" not in capsys.readouterr().out
    assert "SECRET" not in diagnostic.model_dump_json()


@pytest.mark.parametrize(
    "timeframe,component",
    [(Timeframe.M15, Component.OHLCV_15M), (Timeframe.H4, Component.OHLCV_4H)],
)
def test_missing_candles_are_identified(timeframe, component):
    class Missing(ReplayPerpetualSource):
        def fetch_closed_ohlcv(self, **kwargs):
            if kwargs["timeframe"] is timeframe:
                raise IncompleteWarmUpError("no candles")
            return super().fetch_closed_ohlcv(**kwargs)

    assembler = FirstSliceEvidenceAssembler(Missing(), replay=True)
    with pytest.raises(IncompleteWarmUpError) as exc:
        assembler.assemble(organization_id=ORG_ID)
    assert diagnostic_from_exception(exc.value).component is component


@pytest.mark.parametrize(
    "error,reason",
    [
        (WrongInstrumentError, DiagnosticReason.WRONG_INSTRUMENT),
        (WrongMarketError, DiagnosticReason.WRONG_MARKET),
        (WrongSourceError, DiagnosticReason.WRONG_SOURCE),
        (IncompleteTradeWindowError, DiagnosticReason.COVERAGE_INCOMPLETE),
    ],
)
def test_trade_contract_refusals_do_not_become_valid_evidence(error, reason):
    class Invalid(ReplayPerpetualSource):
        def fetch_ordered_trades(self, **kwargs):
            raise error("unusable")

    assembler = FirstSliceEvidenceAssembler(Invalid(), replay=True)
    with pytest.raises(error) as exc:
        assembler.assemble(organization_id=ORG_ID)
    diagnostic = diagnostic_from_exception(exc.value)
    assert diagnostic.component is Component.TRADES
    assert diagnostic.reason_code is reason


@pytest.mark.parametrize(
    "metric,role,component",
    [
        (DerivativeMetric.OPEN_INTEREST, EvidenceRole.OPEN_INTEREST, Component.OPEN_INTEREST),
        (DerivativeMetric.FUNDING, EvidenceRole.FUNDING, Component.FUNDING),
    ],
)
@pytest.mark.parametrize(
    "state,reason",
    [
        (Availability.MISSING, DiagnosticReason.MISSING),
        (Availability.UNSUPPORTED, DiagnosticReason.UNSUPPORTED),
        (Availability.INCOMPLETE, DiagnosticReason.INCOMPLETE),
    ],
)
def test_each_missing_derivative_is_distinct(metric, role, component, state, reason):
    class Missing(ReplayPerpetualSource):
        def fetch_derivative_observation(self, **kwargs):
            return derivative_observation(
                identity=kwargs["identity"],
                metric=metric,
                observed_at=kwargs["observed_at"],
                availability=state,
            )

    assembler = FirstSliceEvidenceAssembler(Missing(), replay=True)
    policy = first_slice_read_policy(ORG_ID)
    policy = policy.model_copy(update={"required_roles": (*policy.required_roles, role)})
    with pytest.raises(MarketContractError) as exc:
        assembler.assemble(organization_id=ORG_ID, policy=policy)
    diagnostic = diagnostic_from_exception(exc.value)
    assert diagnostic.component is component
    assert diagnostic.reason_code is reason


@pytest.mark.parametrize(
    "metric,role",
    [
        (DerivativeMetric.OPEN_INTEREST, EvidenceRole.OPEN_INTEREST),
        (DerivativeMetric.FUNDING, EvidenceRole.FUNDING),
    ],
)
@pytest.mark.parametrize(
    "error_type,reason",
    [
        (WrongInstrumentError, DiagnosticReason.WRONG_INSTRUMENT),
        (WrongMarketError, DiagnosticReason.WRONG_MARKET),
        (WrongSourceError, DiagnosticReason.WRONG_SOURCE),
    ],
)
def test_derivative_acquisition_retains_provenance_failure(metric, role, error_type, reason):
    class WrongProvenance(ReplayPerpetualSource):
        def fetch_derivative_observation(self, **kwargs):
            raise error_type("SECRET upstream payload")

    assembler = FirstSliceEvidenceAssembler(WrongProvenance(), replay=True)
    policy = first_slice_read_policy(ORG_ID)
    policy = policy.model_copy(update={"required_roles": (*policy.required_roles, role)})
    with pytest.raises(MarketContractError) as exc:
        assembler.assemble(organization_id=ORG_ID, policy=policy)
    diagnostic = diagnostic_from_exception(exc.value)
    assert diagnostic.component is Component(metric.value)
    assert diagnostic.reason_code is reason
    assert "SECRET" not in diagnostic.model_dump_json()


def test_stale_derivative_reports_staleness_and_preserves_event_time():
    class Stale(ReplayPerpetualSource):
        def fetch_derivative_observation(self, **kwargs):
            return derivative_observation(
                identity=kwargs["identity"],
                metric=kwargs["metric"],
                observed_at=kwargs["observed_at"],
                row={
                    "openInterest": "10",
                    "time": int((EVALUATED_AT - timedelta(hours=1)).timestamp() * 1000),
                },
                value_key="openInterest",
                time_key="time",
            )

    assembler = FirstSliceEvidenceAssembler(Stale(), replay=True)
    policy = first_slice_read_policy(ORG_ID)
    policy = policy.model_copy(
        update={"required_roles": (*policy.required_roles, EvidenceRole.OPEN_INTEREST)}
    )
    with pytest.raises(MarketContractError) as exc:
        assembler.assemble(organization_id=ORG_ID, policy=policy)
    diagnostic = diagnostic_from_exception(exc.value)
    assert diagnostic.component is Component.OPEN_INTEREST
    assert diagnostic.reason_code is DiagnosticReason.STALE
    assert diagnostic.freshness is FreshnessState.STALE


def test_missing_order_flow_cannot_be_satisfied_by_candles():
    assembler = replay()
    policy = first_slice_read_policy(ORG_ID)
    policy = policy.model_copy(
        update={"required_roles": (*policy.required_roles, EvidenceRole.ORDER_FLOW_5M)}
    )
    with pytest.raises(MarketContractError) as exc:
        assembler.assemble(organization_id=ORG_ID, policy=policy)
    diagnostic = diagnostic_from_exception(exc.value)
    assert diagnostic.component is Component.ORDER_FLOW
    assert diagnostic.timeframe is Timeframe.M5
    assert diagnostic.reason_code is DiagnosticReason.UNSUPPORTED


@pytest.mark.parametrize(
    "status,reason",
    [(429, DiagnosticReason.RATE_LIMITED), (451, DiagnosticReason.REGIONAL_FAILURE)],
)
def test_binance_refusal_has_exact_component_and_no_substitution(status, reason):
    source = BinanceUsdmPerpetualSource(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json={})),
        max_retries=0,
    )
    assembler = FirstSliceEvidenceAssembler(source, replay=False)
    with pytest.raises(MarketContractError) as exc:
        assembler.assemble(organization_id=ORG_ID, evaluated_at=EVALUATED_AT)
    diagnostic = diagnostic_from_exception(exc.value)
    assert diagnostic.component is Component.OHLCV_15M
    assert diagnostic.provider == "binance-usdm-perpetual"
    assert diagnostic.reason_code is reason
    assert diagnostic.failover_attempted is False


def test_watcher_retains_diagnostics_and_creates_no_candidate(monkeypatch):
    def missing(*args, **kwargs):
        raise IncompleteTradeWindowError("SECRET provider payload")

    monkeypatch.setattr(assembly, "first_slice_cvd_window", missing)
    executable = _non_placeholder_executable(ORG_ID)
    port = AssemblingWatcherScanEvidence(replay(), executable_resolver=lambda command: executable)
    repository = InMemoryCandidateRepository()
    clock = BoundEvaluationClock()
    service = WatcherFusionEvaluationService(
        evidence=port,
        lifecycle=CandidateLifecycleService(repository=repository, clock=clock.now),
        clock=clock,
    )
    outcome = service.evaluate(_evaluation_command(ORG_ID))
    assert outcome.reason_code == "canonical_cvd_coverage_incomplete"
    assert outcome.evidence_diagnostics[-1].component is Component.CVD
    assert outcome.candidate_ids == ()
    assert port.last_assembly() is None
    assert "SECRET" not in outcome.model_dump_json()


def test_repeated_scans_release_old_evidence_and_diagnostics():
    import gc
    import weakref

    assembler = replay()
    executable = _non_placeholder_executable(ORG_ID)
    port = AssemblingWatcherScanEvidence(assembler, executable_resolver=lambda command: executable)
    first = port.load(_evaluation_command(ORG_ID))
    reference = weakref.ref(first)
    del first
    for _ in range(15):
        command = _evaluation_command(ORG_ID)
        command = command.model_copy(update={"evaluation_input_hash": uuid4().hex})
        loaded = port.load(command)
        assert port.load(command) is loaded
        assert len(port._load_cache) == 1
        assert len(assembler.diagnostics) <= MAX_COMPONENT_DIAGNOSTICS
    gc.collect()
    assert reference() is None


def test_complete_exchange_data_reaches_assessment_but_missing_resistance_creates_no_candidate():
    assembler = replay()
    executable = _non_placeholder_executable(ORG_ID)
    port = AssemblingWatcherScanEvidence(assembler, executable_resolver=lambda command: executable)
    clock = BoundEvaluationClock()
    service = WatcherFusionEvaluationService(
        evidence=port,
        lifecycle=CandidateLifecycleService(
            repository=InMemoryCandidateRepository(), clock=clock.now
        ),
        clock=clock,
    )
    outcome = service.evaluate(_evaluation_command(ORG_ID))
    assert port.last_assembly() is not None
    assert outcome.reason_code != "canonical_evidence_unavailable"
    assert outcome.candidate_ids == ()
    assert outcome.evidence_diagnostics == assembler.diagnostics
    assert (
        next(
            row for row in outcome.evidence_diagnostics if row.component is Component.RESISTANCE
        ).reason_code
        is DiagnosticReason.MISSING
    )


def test_binance_primary_complete_and_controlled_bybit_failover_refuses_missing_history():
    fixture = canonical_first_slice_fixture()
    base = _fixture_usdm_handler()
    fail_primary = False
    requests = []

    def handler(request):
        requests.append((request.url.host, request.url.path))
        if request.url.host == "fapi.binance.com":
            if fail_primary:
                return httpx.Response(451)
            return base.handle_request(request)
        if request.url.path.endswith("/kline"):
            bars = (
                fixture["bars_15m"]
                if request.url.params["interval"] == "15"
                else fixture["bars_4h"]
            )
            rows = [
                [
                    str(int(bar.interval_start.timestamp() * 1000)),
                    str(bar.open),
                    str(bar.high),
                    str(bar.low),
                    str(bar.close),
                    str(bar.base_volume),
                    str(bar.quote_volume),
                ]
                for bar in reversed(bars)
            ]
            result = {"category": "linear", "symbol": "BTCUSDT", "list": rows}
        elif request.url.path.endswith("recent-trade"):
            result = {
                "category": "linear",
                "symbol": "BTCUSDT",
                "list": [
                    {
                        "execId": str(trade.sequence),
                        "symbol": "BTCUSDT",
                        "price": str(trade.price),
                        "size": str(trade.quantity),
                        "side": "Buy" if trade.aggressor_side.value == "buy" else "Sell",
                        "time": str(int(trade.event_timestamp.timestamp() * 1000)),
                    }
                    for trade in reversed(fixture["trades"])
                ],
            }
            # Explicit mock prefix proves the first millisecond bucket for Bybit.
            result["list"].append(
                {
                    "execId": "mock-prefix",
                    "symbol": "BTCUSDT",
                    "price": "100",
                    "size": "1",
                    "side": "Buy",
                    "time": str(
                        int(first_slice_baseline_open(fixture["bars_15m"][-1]).timestamp() * 1000)
                        - 1
                    ),
                }
            )
        elif request.url.path.endswith("instruments-info"):
            return httpx.Response(200, json=contract_payload(VenueId.BYBIT))
        else:
            metric = (
                DerivativeMetric.OPEN_INTEREST
                if request.url.path.endswith("open-interest")
                else DerivativeMetric.FUNDING
            )
            return httpx.Response(
                200, json=payload(VenueId.BYBIT, metric, stamp=int(EVALUATED_AT.timestamp() * 1000))
            )
        return httpx.Response(200, json={"retCode": 0, "result": result})

    transport = httpx.MockTransport(handler)
    primary = BinanceUsdmPerpetualSource(
        transport=transport, max_retries=0, settlement_grace=RECORDED_FIXTURE_GRACE
    )
    secondary = BybitUsdtPerpetualSource(transport=transport, max_retries=0)
    source = FailoverPerpetualSource(
        primary,
        secondary,
        primary_instrument=binance_usdm_btcusdt(),
        secondary_instrument=bybit_usdt_perpetual_btcusdt(),
    )
    assembler = FirstSliceEvidenceAssembler(source, replay=False)
    result = assembler.assemble(organization_id=ORG_ID, evaluated_at=EVALUATED_AT)
    assert result.identity.venue is VenueId.BINANCE
    assert not source.using_secondary
    fail_primary = True  # Mock transport only; production connectivity is untouched.
    with pytest.raises(IncompleteTradeWindowError) as exc:
        assembler.assemble(organization_id=ORG_ID, evaluated_at=EVALUATED_AT)
    assert source.using_secondary
    diagnostic = diagnostic_from_exception(exc.value)
    assert diagnostic.provider == secondary.name
    assert diagnostic.instrument_id == bybit_usdt_perpetual_btcusdt().instrument_id
    assert diagnostic.component is Component.TRADES
    assert diagnostic.reason_code is DiagnosticReason.COVERAGE_INCOMPLETE
    assert any(
        row.component is Component.OHLCV_4H
        and row.attempt == 2
        and row.status is DiagnosticStatus.AVAILABLE
        for row in assembler.diagnostics
    )
    failures = [
        row for row in assembler.diagnostics if row.status is DiagnosticStatus.SWITCH_REQUIRED
    ]
    assert failures[0].provider == primary.name
    assert failures[0].reason_code is DiagnosticReason.REGIONAL_FAILURE
    assert all(row.failover_attempted for row in assembler.diagnostics if row.attempt == 2)
    assert all(row.failover_attempted for row in failures)
    assert all(row.provider == secondary.name for row in assembler.diagnostics if row.attempt == 2)
    assert all("spot" not in path and "/api/v3/" not in path for host, path in requests)
