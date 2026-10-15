"""Actual assembler + public adapters with independently advancing receipt clocks."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest

from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.evidence_pipeline.canonical import first_slice_read_policy
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.adapters.bybit_usdt_perpetual import BybitUsdtPerpetualSource
from app.market_contracts.adapters.observation_cache import CausalObservationCache
from app.market_contracts.derivatives import DerivativeMetric, derivative_freshness_policy
from app.market_contracts.enums import VenueId
from app.market_contracts.errors import MarketContractError
from app.market_contracts.evidence_diagnostics import DiagnosticStatus, EvidenceComponent
from app.market_contracts.replay_fixtures import build_closed_bars, build_window_trades
from app.market_contracts.trade_reduction import build_released_trade_snapshot
from app.market_contracts.trades import build_trade_event
from app.schemas.common import Timeframe
from app.schemas.nested_continuation import EvidenceAvailability
from app.signal_fusion.adapters import evidence_window_from_assessment_command
from app.signal_fusion.enums import EvidenceRole
from app.signal_fusion.evaluator import evaluate_setup
from tests.support.phase6_fusion import ORG_ID
from tests.test_live_evidence_pipeline import _agg_row, _kline_row
from tests.test_market_intelligence_oi_funding import contract_payload, payload

SCAN = datetime(2026, 1, 15, 15, 0, tzinfo=UTC)
EVENT = SCAN - timedelta(seconds=2)
VENUES = (VenueId.BINANCE, VenueId.BYBIT)
METRICS = tuple(DerivativeMetric)


class AdvancingClock:
    def __init__(self):
        self.value = SCAN

    def __call__(self):
        return self.value

    def advance(self, seconds=1):
        self.value += timedelta(seconds=seconds)


def _policy(metrics=METRICS):
    policy = first_slice_read_policy(ORG_ID)
    return policy.model_copy(
        update={
            "required_roles": (*policy.required_roles, *(EvidenceRole(x.value) for x in metrics))
        }
    )


def _source(venue, clock, *, event=EVENT, receipt_skew=0, delay=1):
    """Only transport is simulated: candles, trade coverage and derivatives are real adapters."""
    bars = {
        Timeframe.M15: build_closed_bars(
            timeframe=Timeframe.M15,
            count=102,
            last_open=SCAN - timedelta(minutes=30),
            evaluated_at=SCAN,
        ),
        Timeframe.H4: build_closed_bars(
            timeframe=Timeframe.H4,
            count=32,
            last_open=SCAN.replace(hour=8),
            evaluated_at=SCAN,
        ),
    }
    trades = build_window_trades(
        bars[Timeframe.M15], source_connection_id=UUID(int=1), receive_at=SCAN
    )
    raw = [_agg_row(trade) for trade in trades]
    # Bybit requires a prefix witness and the actual quote has a recent terminal print.
    raw.insert(
        0,
        {
            **raw[0],
            "a": int(raw[0]["a"]) - 1,
            "T": int(raw[0]["T"]) - 15 * 60 * 1000,
        },
    )
    raw.append({**raw[-1], "a": int(raw[-1]["a"]) + 1, "T": int(EVENT.timestamp() * 1000)})
    counts = Counter()

    def handler(request):
        assert request.method == "GET"
        assert "authorization" not in request.headers
        path = request.url.path
        if path.endswith(("exchangeInfo", "instruments-info")):
            return httpx.Response(200, json=contract_payload(venue))
        metric = (
            DerivativeMetric.OPEN_INTEREST
            if path.endswith(("openInterest", "open-interest"))
            else DerivativeMetric.FUNDING
            if path.endswith(("fundingRate", "funding/history"))
            else None
        )
        if metric is not None:
            counts[metric] += 1
            clock.advance(delay)
            if metric is DerivativeMetric.FUNDING or venue is VenueId.BYBIT:
                assert int(request.url.params["endTime"]) <= int(clock.value.timestamp() * 1000)
            return httpx.Response(
                200, json=payload(venue, metric, stamp=int(event.timestamp() * 1000))
            )
        if path.endswith(("klines", "kline")):
            interval = request.url.params["interval"]
            selected = bars[Timeframe.M15 if interval in {"15m", "15"} else Timeframe.H4]
            if venue is VenueId.BINANCE:
                return httpx.Response(200, json=[_kline_row(bar) for bar in selected])
            return httpx.Response(
                200,
                json={
                    "retCode": 0,
                    "result": {
                        "category": "linear",
                        "symbol": "BTCUSDT",
                        "list": [
                            [
                                str(int(bar.interval_start.timestamp() * 1000)),
                                str(bar.open),
                                str(bar.high),
                                str(bar.low),
                                str(bar.close),
                                str(bar.base_volume),
                                str(bar.quote_volume),
                            ]
                            for bar in reversed(selected)
                        ],
                    },
                },
            )
        if path.endswith("aggTrades"):
            params = request.url.params
            rows = raw
            if "fromId" in params:
                rows = [x for x in rows if int(x["a"]) >= int(params["fromId"])]
            elif "startTime" in params:
                rows = [
                    x
                    for x in rows
                    if int(params["startTime"]) <= int(x["T"]) <= int(params["endTime"])
                ]
            return httpx.Response(200, json=rows)
        if path.endswith("recent-trade"):
            return httpx.Response(
                200,
                json={
                    "retCode": 0,
                    "result": {
                        "category": "linear",
                        "symbol": "BTCUSDT",
                        "list": [
                            {
                                "execId": str(x["a"]),
                                "symbol": "BTCUSDT",
                                "price": x["p"],
                                "size": x["q"],
                                "side": "Sell" if x["m"] else "Buy",
                                "time": str(x["T"]),
                                "seq": str(int(x["a"]) * 7),
                            }
                            for x in reversed(raw)
                        ],
                    },
                },
            )
        return httpx.Response(404)

    cls = BinanceUsdmPerpetualSource if venue is VenueId.BINANCE else BybitUsdtPerpetualSource
    source = cls(
        transport=httpx.MockTransport(handler),
        max_retries=0,
        observation_clock=lambda: clock() + timedelta(seconds=receipt_skew),
        observation_cache=CausalObservationCache(clock=lambda: (clock() - SCAN).total_seconds()),
    )
    if venue is VenueId.BYBIT:
        # Bybit recent REST cannot establish the first slice's eight-hour trade
        # history. Supply that independent core proof from recorded fixtures;
        # leave the actual assembler, candles, quote and derivative adapter/cache
        # untouched. This test makes no native Bybit historical coverage claim.
        source = RecordedClosedHistory(source, trades)
    return source, counts


class RecordedClosedHistory:
    def __init__(self, source, trades):
        self.source = source
        self.trades = trades

    def __getattr__(self, name):
        return getattr(self.source, name)

    def reduce_ordered_trades(
        self, *, identity, instrument, start, end, source_connection_id, receive_at
    ):
        normalized = (
            build_trade_event(
                instrument=instrument,
                venue_trade_id=trade.venue_trade_id,
                sequence=index,
                price=trade.price,
                quantity=trade.quantity,
                buyer_is_maker=trade.aggressor_side.value == "sell",
                event_timestamp=trade.event_timestamp,
                receive_timestamp=receive_at,
                source_connection_id=source_connection_id,
                adapter_version=identity.source.adapter_version,
                aggressor_convention=identity.source.aggressor_convention,
            )
            for index, trade in enumerate(self.trades)
            if start <= trade.event_timestamp < end
        )
        return build_released_trade_snapshot(
            normalized,
            identity=identity,
            lineage_id=source_connection_id,
            window_start=start,
            window_end=end,
            observed_at=receive_at,
        )


@pytest.mark.parametrize("venue", VENUES)
def test_actual_assembler_accepts_delayed_receipts_and_cache_hits(venue):
    clock = AdvancingClock()
    source, counts = _source(venue, clock)
    assembler = FirstSliceEvidenceAssembler(source, replay=False, clock=clock)
    first = assembler.assemble(organization_id=ORG_ID, policy=_policy())
    assert first.evaluated_at == SCAN + timedelta(seconds=2)
    assert first.clocks.evidence_cutoff_at == SCAN
    assert first.clocks.acquisition_completed_at == first.evaluated_at
    assert first.trigger_bar.interval_end == SCAN - timedelta(minutes=15)
    assert first.identity.timeframe is Timeframe.M15
    facts = {x.metric: x for x in first.bundle.market_intelligence}
    assert facts[METRICS[0]].collected_at == SCAN + timedelta(seconds=1)
    assert facts[METRICS[1]].collected_at == SCAN + timedelta(seconds=2)
    assert all(
        x.availability is EvidenceAvailability.AVAILABLE and x.event_time == EVENT
        for x in facts.values()
    )
    assert all(x.freshness.evaluated_at == first.evaluated_at for x in facts.values())
    assert all(
        row.status is DiagnosticStatus.AVAILABLE
        for row in assembler.diagnostics
        if row.component in {EvidenceComponent.OPEN_INTEREST, EvidenceComponent.FUNDING}
    )
    clock.advance(0.25)
    second = assembler.assemble(organization_id=ORG_ID, policy=_policy())
    assert counts == Counter(dict.fromkeys(METRICS, 1))
    assert second.evaluated_at == clock()
    for fact in second.bundle.market_intelligence:
        assert fact.collected_at == facts[fact.metric].collected_at
        assert fact.observed_at == second.evaluated_at
        assert fact.content_hash == facts[fact.metric].content_hash
        envelope = next(
            x
            for x in second.assessment_command.public_observations
            if x.payload_content_hash == fact.content_hash
        )
        assert envelope.receive_time == fact.collected_at
        assert envelope.observed_at == second.evaluated_at
    assert (
        evidence_window_from_assessment_command(second.assessment_command).content_hash
        == second.evidence_window_hash
        == first.evidence_window_hash
    )
    assessment = evaluate_setup(
        policy=_policy(),
        command=second.assessment_command,
        evidence=second.bundle,
        evaluated_at=second.evaluated_at,
    )
    assert next(x for x in assessment.rule_results if x.rule_id == "complete_warmup").passed


@pytest.mark.parametrize("venue", VENUES)
@pytest.mark.parametrize("metric", METRICS)
def test_single_required_metric_delayed_receipt(venue, metric):
    clock = AdvancingClock()
    source, counts = _source(venue, clock)
    assembled = FirstSliceEvidenceAssembler(source, replay=False, clock=clock).assemble(
        organization_id=ORG_ID, evaluated_at=SCAN, policy=_policy((metric,))
    )
    fact = assembled.bundle.market_intelligence[0]
    assert fact.metric is metric
    assert fact.availability is EvidenceAvailability.AVAILABLE
    assert fact.event_time == EVENT
    assert fact.collected_at == SCAN + timedelta(seconds=1)
    assert fact.observed_at == assembled.evaluated_at == clock()
    assert counts == Counter({metric: 1})


@pytest.mark.parametrize("venue", VENUES)
@pytest.mark.parametrize("metric", METRICS)
@pytest.mark.parametrize(
    "case",
    [
        "stale_event",
        "event_after_cutoff",
        "event_after_receipt",
        "future_receipt",
        "strict_historical_receipt",
    ],
)
def test_actual_assembler_rejects_invalid_timing(venue, metric, case):
    clock = AdvancingClock()
    event = (
        SCAN
        - timedelta(seconds=derivative_freshness_policy(metric, venue).trade_max_age_seconds + 1)
        if case == "stale_event"
        else SCAN + timedelta(milliseconds=500)
        if case == "event_after_cutoff"
        else SCAN + timedelta(seconds=2)
        if case == "event_after_receipt"
        else EVENT
    )
    source, counts = _source(
        venue, clock, event=event, receipt_skew=1 if case == "future_receipt" else 0
    )
    assembler = FirstSliceEvidenceAssembler(
        source, replay=False, clock=None if case == "strict_historical_receipt" else clock
    )
    with pytest.raises(MarketContractError, match=f"required_{metric.value}"):
        assembler.assemble(organization_id=ORG_ID, evaluated_at=SCAN, policy=_policy((metric,)))
    assert counts[metric] == 1
    assert clock() == SCAN + timedelta(seconds=1)
    assert assembler.diagnostics[-1].component.value == metric.value
    assert assembler.diagnostics[-1].status is DiagnosticStatus.UNAVAILABLE


@pytest.mark.parametrize("venue", VENUES)
def test_cached_receipt_cannot_leak_into_earlier_historical_assembly(venue):
    clock = AdvancingClock()
    source, counts = _source(venue, clock)
    current = FirstSliceEvidenceAssembler(source, replay=False, clock=clock).assemble(
        organization_id=ORG_ID, policy=_policy()
    )
    original = current.bundle.market_intelligence[0].collected_at
    with pytest.raises(MarketContractError, match="required_open_interest"):
        FirstSliceEvidenceAssembler(source, replay=False).assemble(
            organization_id=ORG_ID, evaluated_at=SCAN, policy=_policy()
        )
    assert (
        counts[METRICS[0]] == 2
    )  # Later cache cannot be read backward; new receipt still fails as-of.
    assert (
        current.bundle.market_intelligence[0].collected_at
        == original
        == SCAN + timedelta(seconds=1)
    )


@pytest.mark.parametrize("venue", VENUES)
def test_final_scan_age_rechecks_an_earlier_required_fact(venue):
    clock = AdvancingClock()
    source, _ = _source(
        venue,
        clock,
        event=SCAN
        - timedelta(
            seconds=derivative_freshness_policy(METRICS[0], venue).trade_max_age_seconds - 1
        ),
    )
    with pytest.raises(MarketContractError, match="open_interest"):
        FirstSliceEvidenceAssembler(source, replay=False, clock=clock).assemble(
            organization_id=ORG_ID, policy=_policy()
        )
    assert clock() == SCAN + timedelta(seconds=2)
