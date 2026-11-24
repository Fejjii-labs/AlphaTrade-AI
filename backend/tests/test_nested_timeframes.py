"""One Nested detector across canonical intervals; no network or trading activation."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.db.strategy_brain import BrainSetupEventRow, BrainSetupRow
from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.adapters.bybit_usdt_perpetual import BybitUsdtPerpetualSource
from app.market_contracts.enums import Finality
from app.market_contracts.errors import MarketContractError, WrongMarketError
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.identity import bybit_usdt_perpetual_btcusdt, interval_timedelta
from app.schemas.common import BacktestSplitLabel, Timeframe, TradeDirection
from app.schemas.nested_continuation import BrainSetupState, NestedContinuationSpec
from app.services.canonical_strategy_evaluation import resolve_executable_strategy_policy
from app.services.strategy_replay_adapter import NestedReplayAdapter
from app.signal_fusion.strategy_evaluation_policy import evaluate_canonical_strategy
from app.strategy_brain.detector import detect_nested
from app.strategy_brain.records import record_detections
from app.watcher.contracts import EvaluationCommand, EvaluationMode, ScanRequest, ScanTrigger
from app.watcher.hashing import evaluation_input_hash, scan_request_hash
from app.workers.watcher_paper_targets import list_watchlist_scan_targets
from tests.test_strategy_brain_nested import (
    PRICES,
    START,
    approved,
    bars,
)
from tests.test_strategy_brain_nested import (
    tenant_store as nested_store,
)

tenant_store = nested_store

REPRESENTATIVE = [
    Timeframe.M5,
    Timeframe.M15,
    Timeframe.H1,
    Timeframe.H4,
    Timeframe.D1,
    Timeframe.W1,
]
BYBIT_INTERVALS = {
    Timeframe.M1: "1",
    Timeframe.M3: "3",
    Timeframe.M5: "5",
    Timeframe.M15: "15",
    Timeframe.M30: "30",
    Timeframe.H1: "60",
    Timeframe.H2: "120",
    Timeframe.H4: "240",
    Timeframe.H6: "360",
    Timeframe.H12: "720",
    Timeframe.D1: "D",
    Timeframe.W1: "W",
}


@pytest.mark.parametrize("timeframe", REPRESENTATIVE)
@pytest.mark.parametrize("direction", list(TradeDirection))
def test_same_detector_progression_and_causal_replay(timeframe, direction):
    spec = NestedContinuationSpec(
        symbol="BTCUSDT", trigger_timeframe=timeframe, direction=direction
    )
    series = bars(timeframe=timeframe, bearish=direction is TradeDirection.SHORT)
    events = detect_nested(series, spec, evaluated_at=series[-1].interval_end)
    confirmed = [e for e in events if e.state is BrainSetupState.CONFIRMED]
    assert [e.stage for e in confirmed] == ["N1", "N2", "N3", "N4_PLUS"]
    assert len({e.sequence_id for e in confirmed}) == 1
    adapter = NestedReplayAdapter()
    for length in (10, 12, 13, 19, 20, 27):
        at = series[length - 1].interval_end
        prefix = detect_nested(series[:length], spec, evaluated_at=at)
        assert prefix == tuple(e for e in events if e.event_index < length)
        replayed = list(
            adapter.events(
                series[:length], spec.model_dump(mode="json"), BacktestSplitLabel.IN_SAMPLE
            )
        )
        assert [index for index, _ in replayed] == [e.event_index for e in prefix]
        assert all(candidate.detected_at <= at for _, candidate in replayed)
    # A future or unfinished confirmation candle cannot confirm a live setup.
    first_break = series[:13]
    assert detect_nested(first_break, spec, evaluated_at=first_break[-1].interval_start) == ()
    assert (
        detect_nested(
            (*first_break[:-1], first_break[-1].model_copy(update={"finality": Finality.FORMING})),
            spec,
            evaluated_at=first_break[-1].interval_end,
        )
        == ()
    )


@pytest.mark.parametrize("timeframe", REPRESENTATIVE)
@pytest.mark.parametrize("failure", ["incomplete", "gap", "wrong_timeframe"])
def test_required_evidence_guards_remain_closed(timeframe, failure):
    spec = NestedContinuationSpec(symbol="BTCUSDT", trigger_timeframe=timeframe)
    series = bars(PRICES[:13], timeframe=timeframe)
    if failure == "incomplete":
        series = (*series[:-1], series[-1].model_copy(update={"provider_complete": False}))
    elif failure == "gap":
        series = (*series[:5], *series[6:])
    else:
        spec = spec.model_copy(update={"trigger_timeframe": Timeframe.M3})
    assert detect_nested(series, spec, evaluated_at=series[-1].interval_end) == ()


def native_source(timeframe, *, bybit=False):
    """Exercise real adapter parsing over native-shaped HTTP fixtures, including a forming bar."""
    step = interval_timedelta(timeframe)
    prices = [*([105] * 243), *PRICES[:13], 999]
    # v3 admits a bar 60 s after close (half a bar for 1m).
    now = START + step * 256 + min(timedelta(seconds=60), step / 2)
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path not in {"/fapi/v1/klines", "/v5/market/kline"}:
            raise MarketContractError("Optional quote unavailable in this candle-only fixture")
        rows = []
        for index, price in enumerate(prices):
            start = START + step * index
            opened = int(start.timestamp() * 1000)
            value = str(price)
            ohlcv = [value, str(price + 1), str(price - 1), value, "100"]
            turnover = str(price * 100)
            if bybit:
                rows.append([str(opened), *ohlcv, turnover])
            else:
                rows.append(
                    [opened, *ohlcv, int((start + step).timestamp() * 1000) - 1, turnover, 4]
                )
        if bybit:
            return httpx.Response(
                200,
                json={
                    "retCode": 0,
                    "result": {
                        "category": "linear",
                        "symbol": "BTCUSDT",
                        "list": list(reversed(rows)),
                    },
                },
            )
        return httpx.Response(200, json=rows)

    source_type = BybitUsdtPerpetualSource if bybit else BinanceUsdmPerpetualSource
    return source_type(transport=httpx.MockTransport(handler), max_retries=0), now, requests


@pytest.mark.parametrize("timeframe", list(Timeframe))
def test_every_timeframe_compiles_acquires_and_uses_canonical_watcher(tenant_store, timeframe):
    session, org, user = tenant_store
    spec = NestedContinuationSpec(symbol="BTCUSDT", trigger_timeframe=timeframe)
    template = approved(session, org, user, spec)
    executable = resolve_executable_strategy_policy(
        session, organization_id=org, strategy_version_id=template["version_id"]
    )
    targets = list_watchlist_scan_targets(
        session, symbols=["BTCUSDT"], organization_id=org, limit=50
    )
    assert len(targets) == 1
    target = targets[0]
    assert (target.symbol, target.timeframe, target.strategy_version_id) == (
        spec.symbol,
        timeframe,
        template["version_id"],
    )
    request = ScanRequest(
        organization_id=org,
        principal_id=user,
        scan_scope=target.scan_scope,
        policy_id=target.policy_id,
        policy_version=1,
        policy_content_hash="a" * 64,
        watchlist_item_ids=(),
        timeframe=timeframe.value,
        idempotency_key="native-candles",
    )
    command = EvaluationCommand(
        command_id=uuid4(),
        request=request,
        request_hash=scan_request_hash(request),
        evaluation_input_hash=evaluation_input_hash(request),
        mode=EvaluationMode.PREVIEW,
        trigger=ScanTrigger.MANUAL,
        correlation_id=uuid4(),
    )
    source, now, requests = native_source(timeframe)
    try:
        port = AssemblingWatcherScanEvidence(
            FirstSliceEvidenceAssembler(source, replay=False, clock=lambda: now),
            executable_resolver=lambda _: executable,
            session=session,
            symbol="BTCUSDT",
        )
        loaded = port.load(command)
        assert loaded is not None
        assembled, bound = port.last_assembly()
        assert bound.strategy_version_id == template["version_id"]
        assert assembled.identity.timeframe is timeframe
        assert assembled.assessment_command.evidence_identity.timeframe is timeframe
        assert len(loaded.evidence.bars_15m) == 256
        assert all(
            b.timeframe is timeframe
            and b.finality is Finality.FINAL
            and b.provider_complete
            and b.interval_end <= now
            for b in loaded.evidence.bars_15m
        )
        assert loaded.evidence.bars_15m[-1].close == Decimal(114)
        assert requests[0].url.params["interval"] == timeframe.value
        candle_reads = [r for r in requests if r.url.path.endswith("klines")]
        # REST v2 confirms the settled window with two identical read-only requests.
        assert len(candle_reads) == 2
        assert candle_reads[0].url == candle_reads[1].url
        assert all(r.method == "GET" for r in candle_reads)
        count_before_reuse = len(requests)
        assert port.load(command) is loaded
        assert len(requests) == count_before_reuse  # Cached command causes no new reads.
        assessment = evaluate_canonical_strategy(
            executable_policy=executable,
            command=loaded.assessment_command,
            evidence=loaded.evidence,
            evaluated_at=now,
        )
        assert assessment.state.value == "confirmed_setup"
        stale = evaluate_canonical_strategy(
            executable_policy=executable,
            command=loaded.assessment_command,
            evidence=loaded.evidence,
            evaluated_at=now + interval_timedelta(timeframe),
        )
        assert stale.state.value == "no_setup"
        session.commit()
        row = session.scalar(
            select(BrainSetupRow).where(BrainSetupRow.strategy_version_id == template["version_id"])
        )
        assert row.payload["timeframe"] == timeframe.value
        assert row.payload["direction"] == "long"
        assert row.symbol == spec.symbol
    finally:
        source.close()


@pytest.mark.parametrize("timeframe,interval", list(BYBIT_INTERVALS.items()))
def test_bybit_native_intervals_preserve_closed_final_evidence(timeframe, interval):
    source, now, requests = native_source(timeframe, bybit=True)
    instrument = bybit_usdt_perpetual_btcusdt()
    identity = first_slice_identity(
        timeframe=timeframe, replay=False, is_live=True, instrument=instrument
    )
    try:
        series = source.fetch_closed_ohlcv(
            identity=identity,
            instrument=instrument,
            timeframe=timeframe,
            min_final_bars=256,
            evaluated_at=now,
        )
        assert requests[0].url.params["interval"] == interval
        assert len(series.bars) == 256
        assert all(
            b.timeframe is timeframe and b.finality is Finality.FINAL and b.provider_complete
            for b in series.bars
        )
        assert series.bars[-1].close == Decimal(114)
    finally:
        source.close()


def test_bybit_3d_fails_closed_without_fetching_or_substitution():
    source, now, requests = native_source(Timeframe.D3, bybit=True)
    instrument = bybit_usdt_perpetual_btcusdt()
    identity = first_slice_identity(
        timeframe=Timeframe.D3, replay=False, is_live=True, instrument=instrument
    )
    try:
        with pytest.raises(WrongMarketError, match="3d is not contracted"):
            source.fetch_closed_ohlcv(
                identity=identity,
                instrument=instrument,
                timeframe=Timeframe.D3,
                min_final_bars=256,
                evaluated_at=now,
            )
        assert requests == []
    finally:
        source.close()


def test_n2_updates_cannot_mutate_other_timeframes_or_versions(tenant_store):
    session, org, user = tenant_store
    stored = {}
    for timeframe in (Timeframe.M15, Timeframe.H1, Timeframe.H4):
        spec = NestedContinuationSpec(symbol="BTCUSDT", trigger_timeframe=timeframe)
        template = approved(session, org, user, spec)
        series = bars(PRICES[:19], timeframe=timeframe)
        events = detect_nested(series, spec, evaluated_at=series[-1].interval_end)
        assert events[-1].stage == "N2" and events[-1].state is BrainSetupState.FORMING
        record_detections(
            session,
            organization_id=org,
            strategy_id=template["strategy_id"],
            version_id=template["version_id"],
            spec=spec,
            bars=series,
            events=events,
            evidence_hash="a" * 64,
            evaluated_at=series[-1].interval_end,
        )
        session.commit()
        row = session.scalar(
            select(BrainSetupRow).where(
                BrainSetupRow.strategy_version_id == template["version_id"],
                BrainSetupRow.payload["stage"].as_string() == "N2",
            )
        )
        stored[timeframe] = (spec, template, row.id, row.payload.copy())
    assert len({v[2] for v in stored.values()}) == 3
    assert len({v[3]["sequence_id"] for v in stored.values()}) == 3
    spec, template, identity, _ = stored[Timeframe.M15]
    series = bars(PRICES[:20])
    record_detections(
        session,
        organization_id=org,
        strategy_id=template["strategy_id"],
        version_id=template["version_id"],
        spec=spec,
        bars=series,
        events=detect_nested(series, spec, evaluated_at=series[-1].interval_end),
        evidence_hash="b" * 64,
        evaluated_at=series[-1].interval_end,
    )
    session.commit()
    session.expire_all()
    assert session.get(BrainSetupRow, identity).state == "CONFIRMED"
    for timeframe in (Timeframe.H1, Timeframe.H4):
        _, other_template, other_id, original = stored[timeframe]
        assert session.get(BrainSetupRow, other_id).payload == original
        assert session.get(BrainSetupRow, other_id).state == "FORMING"
        assert all(
            event.payload["timeframe"] == timeframe.value
            for event in session.scalars(
                select(BrainSetupEventRow).where(BrainSetupEventRow.setup_id == other_id)
            )
        )
        assert other_template["version_id"] != template["version_id"]
    # Parameter changes still create a separate immutable instance on the same timeframe.
    changed = spec.model_copy(
        update={
            "parameters": spec.parameters.model_copy(update={"minimum_impulse": Decimal("0.006")})
        }
    )
    changed_template = approved(session, org, user, changed)
    assert changed_template["version_id"] != template["version_id"]
    assert (
        resolve_executable_strategy_policy(
            session, organization_id=org, strategy_version_id=template["version_id"]
        ).authored_spec
        == spec
    )
