"""REST close-boundary and same-revision volume/count conflict regressions."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.public_market_observations import PublicMarketObservationRow
from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.enums import FreshnessState
from app.market_contracts.errors import DuplicateDataError, FormingCandleError, WrongMarketError
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.identity import ADAPTER_VERSION, binance_usdm_btcusdt
from app.market_contracts.observation import PublicMarketObservation, observation_from_ohlcv
from app.market_contracts.ohlcv import ClosedOhlcvSeries, OhlcvBar, build_ohlcv_bar
from app.persistence.public_market_observations import observed_ohlcv_history, remember_observation
from app.schemas.common import Timeframe
from tests.test_sfp_receipt_reuse import receipt_store as receipt_store
from tests.test_sfp_strategy_brain_runtime import approve, padded_evidence
from tests.test_sfp_strategy_brain_runtime import postgres_store as postgres_store
from tests.test_sfp_strategy_brain_runtime import store as store

ReceiptStore = tuple[Session, UUID, UUID, str]

START = datetime(2026, 10, 5, 21, 15, tzinfo=UTC)
END = START + timedelta(minutes=15)
EARLY = END + timedelta(microseconds=614496)
SETTLED = END + timedelta(seconds=15)
OBSERVATION_ID = UUID("55121195-4489-5d6e-b733-6c23c2f4e618")


def row(*, increased: bool = False, start: datetime = START) -> list[object]:
    # Supplied volumes/count are exact; OHLC and later increments are test fixtures.
    return [
        int(start.timestamp() * 1000),
        "85000",
        "86000",
        "84000",
        "85500",
        "239.970" if increased else "238.970",
        int((start + timedelta(minutes=15)).timestamp() * 1000) - 1,
        "20579928.80890" if increased else "20494228.80890",
        8529 if increased else 8528,
        "0",
        "0",
        "0",
    ]


def source_for(
    responses: list[list[list[object]]], on_read: Callable[[], None] | None = None
) -> tuple[BinanceUsdmPerpetualSource, list[httpx.Request]]:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.path == "/fapi/v1/klines"
        calls.append(request)
        if on_read is not None:
            on_read()
        return httpx.Response(200, json=responses[min(len(calls) - 1, len(responses) - 1)])

    return BinanceUsdmPerpetualSource(transport=httpx.MockTransport(handler)), calls


def fetch(source: BinanceUsdmPerpetualSource, *, at: datetime = SETTLED) -> ClosedOhlcvSeries:
    return source.fetch_closed_ohlcv(
        identity=first_slice_identity(timeframe=Timeframe.M15, replay=False, is_live=True),
        instrument=binance_usdm_btcusdt(),
        timeframe=Timeframe.M15,
        min_final_bars=1,
        evaluated_at=at,
    )


def legacy() -> tuple[OhlcvBar, PublicMarketObservation]:
    identity = first_slice_identity(timeframe=Timeframe.M15, replay=False, is_live=True)
    identity = identity.model_copy(
        update={
            "source": identity.source.model_copy(update={"adapter_version": ADAPTER_VERSION}),
            "provenance": identity.provenance.model_copy(
                update={"adapter_version": ADAPTER_VERSION}
            ),
        }
    )
    bar = build_ohlcv_bar(
        instrument=binance_usdm_btcusdt(),
        timeframe=Timeframe.M15,
        interval_start=START,
        open_=Decimal("85000"),
        high=Decimal("86000"),
        low=Decimal("84000"),
        close=Decimal("85500"),
        base_volume=Decimal("238.970"),
        quote_volume=Decimal("20494228.80890"),
        trade_count=8528,
        evaluated_at=EARLY,
        grace=timedelta(0),
        adapter_version=ADAPTER_VERSION,
    )
    observation = observation_from_ohlcv(
        bar,
        identity=identity,
        observed_at=EARLY,
        receive_time=EARLY,
        freshness_state=FreshnessState.FRESH,
    )
    assert observation.observation_id == OBSERVATION_ID
    assert observation.revision == 1 and observation.finality.value == "final"
    return bar, observation


@pytest.mark.parametrize("seconds", [0, 0.614496, 4.999999])
def test_early_rest_receipt_cannot_claim_final_even_with_sufficient_older_bars(
    seconds: float,
) -> None:
    source, _calls = source_for([[row(start=START - timedelta(minutes=15)), row()]])
    try:
        with pytest.raises(FormingCandleError):
            fetch(source, at=END + timedelta(seconds=seconds))
    finally:
        source.close()


def test_closed_candle_that_changes_between_confirmation_reads_is_refused() -> None:
    source, calls = source_for([[row()], [row(increased=True)]])
    try:
        with pytest.raises(FormingCandleError):
            fetch(source)
        assert len(calls) == 2
    finally:
        source.close()


@pytest.mark.parametrize("seconds", [5, 15, 900])
def test_confirmed_reads_are_bounded_pinned_and_keep_provider_revision_one(seconds: float) -> None:
    source, calls = source_for([[row()], [row()]])
    at = END + timedelta(seconds=seconds)
    try:
        series = fetch(source, at=at)
        assert len(calls) == 2
        assert calls[0].url.params == calls[1].url.params
        assert calls[0].url.params["endTime"] == str(int(at.timestamp() * 1000))
        assert series.identity.source.adapter_version == "binance-usdm-perpetual/v2"
        assert series.bars[0].adapter_version == "binance-usdm-perpetual/v2"
        assert series.bars[0].revision == 1
    finally:
        source.close()


def test_forming_row_changes_do_not_contaminate_confirmed_closed_rows() -> None:
    source, calls = source_for(
        [
            [row(), row(start=END)],
            [row(), row(start=END, increased=True)],
        ]
    )
    try:
        assert len(fetch(source).bars) == 1
        assert len(calls) == 2
    finally:
        source.close()


@pytest.mark.parametrize(
    "close_time",
    [True, None, "bad", 0, float(int(END.timestamp() * 1000) - 1), int(END.timestamp() * 1000)],
)
def test_provider_close_time_must_match_exact_half_open_interval(close_time: object) -> None:
    invalid = row()
    invalid[6] = close_time
    source, calls = source_for([[invalid]])
    try:
        with pytest.raises(WrongMarketError):
            fetch(source)
        assert len(calls) == 1
    finally:
        source.close()


def test_confirmation_compares_canonical_decimals_not_wire_format() -> None:
    equivalent = row()
    equivalent[5] = "238.970000"
    equivalent[7] = "20494228.8089000"
    source, calls = source_for([[row()], [equivalent]])
    try:
        assert fetch(source).bars[0].base_volume == Decimal("238.970")
        assert len(calls) == 2
    finally:
        source.close()


@pytest.mark.parametrize("count", [True, None, "8528", 8528.0])
def test_native_trade_count_cannot_be_missing_or_coerced(count: object) -> None:
    invalid = row()
    invalid[8] = count
    source, calls = source_for([[invalid]])
    try:
        with pytest.raises(WrongMarketError):
            fetch(source)
        assert len(calls) == 1
    finally:
        source.close()


def test_truncated_native_row_cannot_be_final() -> None:
    source, calls = source_for([[row()[:8]]])
    try:
        with pytest.raises(WrongMarketError):
            fetch(source)
        assert len(calls) == 1
    finally:
        source.close()


def test_reported_value_conflict_remains_strict_under_legacy_identity(
    receipt_store: ReceiptStore,
) -> None:
    session, _org, _user, _url = receipt_store
    bar, original = legacy()
    remember_observation(session, original, bar=bar)
    session.commit()
    changed = build_ohlcv_bar(
        instrument=bar.instrument,
        timeframe=bar.timeframe,
        interval_start=START,
        open_=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        base_volume=bar.base_volume + Decimal("1"),
        quote_volume=bar.quote_volume + Decimal("85700"),
        trade_count=8529,
        evaluated_at=SETTLED,
        grace=timedelta(0),
        adapter_version=ADAPTER_VERSION,
    )
    incoming = observation_from_ohlcv(
        changed,
        identity=original.identity,
        observed_at=SETTLED,
        receive_time=SETTLED,
        freshness_state=FreshnessState.FRESH,
    )
    assert incoming.observation_id == original.observation_id
    fields = sorted(
        name for name in type(bar).model_fields if getattr(bar, name) != getattr(changed, name)
    )
    assert fields == ["base_volume", "content_hash", "quote_volume", "trade_count"]
    with pytest.raises(DuplicateDataError):
        remember_observation(session, incoming, bar=changed)
    session.rollback()
    assert remember_observation(session, original, bar=bar) == original


def snapshot(
    session: Session,
) -> dict[tuple[str, UUID], tuple[dict[str, object], dict[str, object] | None]]:
    return {
        (item.identity_hash, item.observation_id): deepcopy((item.payload, item.ohlcv))
        for item in session.scalars(select(PublicMarketObservationRow))
    }


def test_v2_rebootstrap_preserves_v1_history_and_refuses_new_policy_conflicts(
    receipt_store: ReceiptStore,
) -> None:
    session, _org, _user, _url = receipt_store
    old_bar, original = legacy()
    remember_observation(session, original, bar=old_bar)
    session.commit()
    before = snapshot(session)
    source, _calls = source_for([[row(increased=True)], [row(increased=True)]])
    try:
        series = fetch(source)
        new_bar = series.bars[0]
        fresh = observation_from_ohlcv(
            new_bar,
            identity=series.identity,
            observed_at=SETTLED,
            receive_time=SETTLED,
            freshness_state=FreshnessState.FRESH,
        )
        assert fresh.observation_id == original.observation_id
        assert fresh.revision == original.revision == 1
        remember_observation(session, fresh, bar=new_bar)
        session.commit()
        current = snapshot(session)
        assert len(current) == 2 and all(current[key] == value for key, value in before.items())
        assert observed_ohlcv_history(
            session, identity=original.identity, since=START, evaluated_at=SETTLED, limit=2
        ) == ((old_bar, original),)
        assert not observed_ohlcv_history(
            session, identity=series.identity, since=START, evaluated_at=EARLY, limit=2
        )
        assert observed_ohlcv_history(
            session, identity=series.identity, since=START, evaluated_at=SETTLED, limit=2
        ) == ((new_bar, fresh),)
        changed = new_bar.model_copy(update={"trade_count": 8530})
        from app.market_contracts.hashing import with_content_hash

        changed = with_content_hash(changed)
        unexplained = observation_from_ohlcv(
            changed,
            identity=series.identity,
            observed_at=SETTLED + timedelta(seconds=1),
            receive_time=SETTLED + timedelta(seconds=1),
            freshness_state=FreshnessState.FRESH,
        )
        with pytest.raises(DuplicateDataError):
            remember_observation(session, unexplained, bar=changed)
        session.rollback()
        assert snapshot(session) == current
    finally:
        source.close()


def test_both_sfp_scopes_resume_with_actual_post_confirmation_receipt_clocks(
    receipt_store: ReceiptStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.market_contracts.errors import MarketContractError
    from tests.test_live_evidence_pipeline import _evaluation_command
    from tests.test_sfp_detector import spec

    session, org, user, url = receipt_store
    old_bar, original = legacy()
    remember_observation(session, original, bar=old_bar)
    session.commit()
    before = snapshot(session)
    policies = [approve(session, org, user, spec(bearish=direction)) for direction in (False, True)]
    bars, _ = padded_evidence()
    selected = bars[-256:]
    rows = []
    for i, bar in enumerate(selected):
        start = START - timedelta(minutes=15) * (len(selected) - 1 - i)
        data = row(start=start)
        data[1:5] = [str(bar.open), str(bar.high), str(bar.low), str(bar.close)]
        rows.append(data)
    rows[-1] = row(increased=True)
    instant = [SETTLED]

    def advance() -> None:
        instant[0] += timedelta(seconds=1)

    def no_optional_quote(*args: object, **kwargs: object) -> None:
        raise MarketContractError("Optional quote absent in isolated OHLCV fixture")

    monkeypatch.setattr("app.strategy_brain.assembly.quote_current_price", no_optional_quote)
    source, calls = source_for([rows], on_read=advance)
    engine = create_engine(url)
    first_trigger: PublicMarketObservation | None = None
    try:
        for active in (session, Session(engine)):
            try:
                for policy in policies:
                    port = AssemblingWatcherScanEvidence(
                        FirstSliceEvidenceAssembler(source, replay=False, clock=lambda: instant[0]),
                        session=active,
                        executable_resolver=lambda _, policy=policy: policy,
                    )
                    result = port.load(_evaluation_command(org))
                    assert result is not None
                    active.commit()
                    trigger = result.assessment_command.public_observations[0]
                    assert trigger.identity.source.adapter_version == "binance-usdm-perpetual/v2"
                    assert trigger.revision == 1
                    assert trigger.receive_time >= SETTLED + timedelta(seconds=2)
                    assert trigger.observed_at == trigger.receive_time
                    if first_trigger is None:
                        first_trigger = trigger
                        assert trigger.receive_time == instant[0]
                    else:
                        assert trigger == first_trigger
            finally:
                if active is not session:
                    active.close()
        after = snapshot(session)
        assert len(after) == 257
        assert all(after[key] == value for key, value in before.items())
        # Four identical 15m windows share one confirmation pair inside the 10s cache.
        assert len(calls) == 2
    finally:
        source.close()
        engine.dispose()
