"""Public endpoint fixtures and actual Agent consumer; no native exchange claims."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from threading import Event

import httpx
import pytest

from app.core.config import Settings
from app.evidence_pipeline.market_intelligence import read_order_book
from app.evidence_pipeline.service import CanonicalEvidenceService
from app.interactive_agent.canonical_market import CanonicalPerpetualQuoteReader
from app.interactive_agent.contracts import AgentTurnRequest
from app.interactive_agent.service import InteractiveAgentService
from app.market_contracts.adapters.aggtrade_cache import TtlValueCache
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.adapters.book_sequence import BookSequenceGuard
from app.market_contracts.adapters.bybit_usdt_perpetual import BybitUsdtPerpetualSource
from app.market_contracts.adapters.observation_cache import CausalObservationCache
from app.market_contracts.adapters.request_budget import request_weight
from app.market_contracts.context import MarketEvidenceContext
from app.market_contracts.derivatives import DerivativeMetric, derivative_observation
from app.market_contracts.enums import VenueId
from app.market_contracts.errors import (
    GapDetectedError,
    MarketContractError,
    StaleEvidenceError,
    WrongSourceError,
)
from app.market_contracts.order_book import (
    hash_order_book,
    require_order_book,
)
from app.schemas.common import Timeframe
from app.schemas.nested_continuation import EvidenceAvailability as State
from tests.test_interactive_agent_foundation import ORG_A, USER_A
from tests.test_interactive_agent_foundation import agent_db as agent_db
from tests.test_market_intelligence_cvd_orderflow import NOW, provider
from tests.test_market_intelligence_oi_funding import contract_payload, identity, instrument

VENUES = (VenueId.BINANCE, VenueId.BYBIT)
STAMP = int(NOW.timestamp() * 1000)


def depth_payload(venue, stamp=STAMP):
    bids, asks = [["100.01", "1.2345"], ["99.99", "2"]], [["100.02", "0.5"]]
    if venue is VenueId.BINANCE:
        return {"lastUpdateId": 100, "T": stamp - 1, "E": stamp, "bids": bids, "asks": asks}
    return {
        "retCode": 0,
        "result": {
            "s": "BTCUSDT",
            "b": bids,
            "a": asks,
            "u": 100,
            "seq": 900,
            "cts": stamp - 1,
            "ts": stamp,
        },
    }


def source(venue, response=None, *, status=200, requests=None, cache=None):
    def handle(request):
        if requests is not None:
            requests.append(request)
        assert request.method == "GET" and "authorization" not in request.headers
        if request.url.path.endswith(("exchangeInfo", "instruments-info")):
            return httpx.Response(200, json=contract_payload(venue))
        return httpx.Response(status, json=depth_payload(venue) if response is None else response)

    cls = BinanceUsdmPerpetualSource if venue is VenueId.BINANCE else BybitUsdtPerpetualSource
    return cls(transport=httpx.MockTransport(handle), max_retries=0, observation_cache=cache)


def read(venue, src=None, at=NOW):
    return read_order_book(
        src or source(venue), identity=identity(venue), instrument=instrument(venue), observed_at=at
    )


@pytest.mark.parametrize("venue", VENUES)
def test_native_endpoint_units_times_coverage_and_unrounded_totals(venue):
    requests = []
    result = read(venue, source(venue, requests=requests))
    assert result.availability is State.AVAILABLE
    assert result.identity.venue is venue and result.identity.timeframe is None
    assert result.identity.provenance.provider_name == result.identity.source.provider_name
    assert result.observed_at == result.collected_at == NOW
    assert result.event_time == NOW - timedelta(milliseconds=1)
    assert result.provider_generated_at == NOW
    assert (result.base_units, result.quote_units, result.price_units) == (
        "BTC",
        "USDT",
        "USDT/BTC",
    )
    assert result.bid_base_quantity == Decimal("3.2345")
    assert result.bid_quote_notional == Decimal("323.442345")
    assert result.spread == Decimal("0.01")
    assert result.coverage_kind == "depth_limited_snapshot" and not result.historical_coverage
    assert result.excluded_liquidity == "RPI"
    assert len(requests) == 2
    assert requests[-1].url.params["limit"] == "20"
    if venue is VenueId.BYBIT:
        assert requests[-1].url.params["category"] == "linear"
    require_order_book(result, identity=identity(venue), evaluated_at=NOW)
    with pytest.raises(MarketContractError, match="historical"):
        require_order_book(result, identity=identity(venue), evaluated_at=NOW, historical=True)


@pytest.mark.parametrize("venue", VENUES)
@pytest.mark.parametrize(
    "damage",
    [
        "duplicate",
        "unordered",
        "crossed",
        "zero",
        "float",
        "future",
        "missing_time",
        "bad_id",
        "too_deep",
        "wrong_symbol",
    ],
)
def test_malformed_or_future_books_never_offer_liquidity(venue, damage):
    data = deepcopy(depth_payload(venue))
    row = data if venue is VenueId.BINANCE else data["result"]
    bid_key, ask_key = ("bids", "asks") if venue is VenueId.BINANCE else ("b", "a")
    if damage == "duplicate":
        row[bid_key].append(row[bid_key][0])
    elif damage == "unordered":
        row[bid_key].reverse()
    elif damage == "crossed":
        row[ask_key][0][0] = "99"
    elif damage in {"zero", "float"}:
        row[bid_key][0][1] = "0" if damage == "zero" else 1.25
    elif damage == "future":
        row["E" if venue is VenueId.BINANCE else "ts"] = STAMP + 1
    elif damage == "missing_time":
        row.pop("T" if venue is VenueId.BINANCE else "cts")
    elif damage == "bad_id":
        row["lastUpdateId" if venue is VenueId.BINANCE else "u"] = True
    elif damage == "too_deep":
        row[bid_key] *= 11
    else:
        row["s"] = "ETHUSDT"
    result = read(venue, source(venue, data))
    assert result.availability is State.INCOMPLETE
    assert not result.bids and result.bid_quote_notional is None
    with pytest.raises(MarketContractError):
        require_order_book(result, identity=identity(venue), evaluated_at=NOW)


@pytest.mark.parametrize("venue", VENUES)
def test_stale_data_is_retained_and_required_consumption_rechecks_age(venue):
    result = read(venue, source(venue, depth_payload(venue, STAMP - 10000)))
    assert result.availability is State.STALE and result.spread is not None
    with pytest.raises(MarketContractError):
        require_order_book(result, identity=identity(venue), evaluated_at=NOW)
    fresh = read(venue)
    with pytest.raises(MarketContractError):
        require_order_book(
            fresh, identity=identity(venue), evaluated_at=NOW + timedelta(seconds=11)
        )
    with pytest.raises(MarketContractError, match="future_observation"):
        require_order_book(
            fresh, identity=identity(venue), evaluated_at=NOW - timedelta(milliseconds=1)
        )
    tampered = hash_order_book(fresh.model_copy(update={"base_units": "USD"}))
    with pytest.raises(WrongSourceError):
        require_order_book(tampered, identity=identity(venue), evaluated_at=NOW)


@pytest.mark.parametrize("venue", VENUES)
def test_public_cache_shares_collect_once_reages_and_never_leaks_backwards(venue):
    requests, ticks = [], [0.0]
    cache = CausalObservationCache(clock=lambda: ticks[0])
    first, second = (
        source(venue, requests=requests, cache=cache),
        source(venue, requests=requests, cache=cache),
    )
    original = read(venue, first)
    reused = read(venue, second, NOW + timedelta(seconds=1))
    assert len(requests) == 2
    assert reused.collected_at == original.collected_at == NOW
    assert reused.observed_at == NOW + timedelta(seconds=1)
    assert reused.freshness.age_seconds > original.freshness.age_seconds
    assert reused.content_hash == original.content_hash
    earlier = read(venue, second, NOW - timedelta(milliseconds=2))
    assert len(requests) == 4 and earlier.availability is State.INCOMPLETE
    ticks[0] = 3.0
    read(venue, second, NOW + timedelta(seconds=3))
    assert len(requests) == 6


def test_single_collector_for_concurrent_consumers():
    cache = CausalObservationCache(clock=lambda: 0)
    entered, release = Event(), Event()
    calls = []
    fact = derivative_observation(
        identity=identity(VenueId.BINANCE),
        metric=DerivativeMetric.OPEN_INTEREST,
        observed_at=NOW,
        row={"v": "12", "t": STAMP},
        value_key="v",
        time_key="t",
    )

    def loader():
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return fact

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.collect, ("p", "BTCUSDT", "oi", "v2"), NOW, loader)
        assert entered.wait(2)
        second = pool.submit(cache.collect, ("p", "BTCUSDT", "oi", "v2"), NOW, loader)
        release.set()
        assert first.result() == second.result()
    assert calls == [1]


def test_binance_bridge_duplicates_gap_disconnect_and_resnapshot():
    guard = BookSequenceGuard(VenueId.BINANCE)
    guard.snapshot(100)
    assert not guard.delta({"U": 80, "u": 99, "pu": 70})
    initial = {"U": 99, "u": 102, "pu": 98, "b": [["100", "2"]]}
    assert guard.delta(initial)
    assert not guard.delta(initial)
    assert guard.delta({"U": 103, "u": 110, "pu": 102})
    with pytest.raises(GapDetectedError, match="predecessor_gap"):
        guard.delta({"U": 111, "u": 112, "pu": 109})
    assert guard.snapshot_id is None
    with pytest.raises(GapDetectedError, match="snapshot_required"):
        guard.delta(initial)
    guard.snapshot(112)
    assert guard.delta({"U": 111, "u": 113, "pu": 110})
    guard.disconnect()
    assert guard.snapshot_id is None


@pytest.mark.parametrize("venue", VENUES)
def test_adapter_gap_invalidates_cached_book_next_get_resynchronizes(venue):
    requests = []
    src = source(venue, requests=requests)
    read(venue, src)
    result = src.observe_order_book_delta(
        identity=identity(venue),
        instrument=instrument(venue),
        row={"s": "BTCUSDT", "U": 101, "u": 102, "pu": 100, "b": [["100", "0"]]},
        observed_at=NOW + timedelta(seconds=1),
    )
    assert result.availability is State.INCOMPLETE and result.sequence_status == "resync_required"
    assert not result.bids
    assert read(venue, src, NOW + timedelta(seconds=1)).availability is State.AVAILABLE
    assert len(requests) == 4


def test_bybit_never_guesses_contiguous_update_ids_and_snapshots_reset():
    guard = BookSequenceGuard(VenueId.BYBIT)
    guard.snapshot(100)
    with pytest.raises(GapDetectedError, match="continuity_unproven"):
        guard.delta({"u": 101, "seq": 901})
    guard.snapshot(1)
    assert guard.snapshot_id == 1


@pytest.mark.parametrize("venue", VENUES)
def test_http_failure_and_replay_unsupported_are_explicit(venue):
    item = read(venue, source(venue, status=451))
    assert item.availability is State.MISSING and item.spread is None
    from app.market_contracts.adapters.replay import ReplayPerpetualSource

    unsupported = read(venue, ReplayPerpetualSource())
    assert unsupported.availability is State.UNSUPPORTED


def test_depth_budget_uses_official_weight_schedule():
    assert [request_weight("/fapi/v1/depth", {"limit": n}) for n in (20, 100, 500, 1000)] == [
        2,
        5,
        10,
        20,
    ]


def test_cross_venue_context_is_explicit_and_other_timeframes_remain():
    item = read(VenueId.BYBIT)
    with pytest.raises(ValueError, match="Cross-venue"):
        MarketEvidenceContext(
            evaluated_at=NOW, anchor_venue="binance", anchor_symbol="BTCUSDT", order_book=item
        )
    labeled = MarketEvidenceContext(
        evaluated_at=NOW,
        anchor_venue="binance",
        anchor_symbol="BTCUSDT",
        order_book=item,
        cross_venue_components=("order_book",),
    )
    assert not labeled.qualification_authority
    for timeframe in (Timeframe.M1, Timeframe.M15, Timeframe.H1, Timeframe.H4):
        ident = identity(VenueId.BYBIT).model_copy(update={"timeframe": timeframe})
        got = read_order_book(
            source(VenueId.BYBIT),
            identity=ident,
            instrument=instrument(VenueId.BYBIT),
            observed_at=NOW,
        )
        assert ident.timeframe == timeframe and got.identity.timeframe is None


def test_actual_agent_turn_consumes_canonical_oi_flow_cvd_and_resting_book(agent_db):
    factory, settings = agent_db
    venue = VenueId.BINANCE
    flow_source, _ = provider(venue)
    src = source(venue)
    # Extend the existing executed-print fixture source; no invented live network data.
    src.fetch_ordered_trades = flow_source.fetch_ordered_trades
    src.fetch_order_flow_snapshot = flow_source.fetch_order_flow_snapshot
    src.fetch_derivative_observation = flow_source.fetch_derivative_observation
    canonical = CanonicalEvidenceService(
        Settings(perpetual_evidence_source="binance_usdm"), source=src, clock=lambda: NOW
    )
    reader = CanonicalPerpetualQuoteReader(canonical, ORG_A)
    with factory() as session:
        result = InteractiveAgentService(
            session, settings=settings, market_reader=reader
        ).handle_turn(
            AgentTurnRequest(message="Show BTCUSDT market context", symbol="BTCUSDT"),
            organization_id=ORG_A,
            user_id=USER_A,
        )
    context = result.market_quote.evidence_context
    assert context.order_book.availability is State.AVAILABLE
    assert context.order_flow.availability is State.AVAILABLE
    assert "OI change and OI notional unavailable" in result.reply
    assert "closed 5m buy=" in result.reply and "10m CVD=" in result.reply
    assert "bullish_print_close_to_close" in result.reply
    assert "Resting order book venue=binance" in result.reply
    assert "historical coverage unavailable" in result.reply
    assert "provider=binance-usdm-perpetual" in result.reply
    assert f"observed_at={NOW.isoformat()}" in result.reply
    assert f"collected_at={NOW.isoformat()}" in result.reply
    assert "coverage=proven_executed_trade_window" in result.reply
    assert not result.execution_attempted


def test_agent_retains_missing_book_reason_without_values_or_raw_provider_text():
    from app.interactive_agent.market_context import market_context_text

    unavailable = read(VenueId.BINANCE, source(VenueId.BINANCE, {}, status=200))
    assert unavailable.availability is State.INCOMPLETE
    context = MarketEvidenceContext(
        evaluated_at=NOW, anchor_venue="binance", anchor_symbol="BTCUSDT", order_book=unavailable
    )
    text = market_context_text(context)
    assert "Resting order book venue=binance: INCOMPLETE" in text
    assert f"reason={unavailable.reason}" in text
    assert "consumer_check=MarketContractError" in text
    assert "spread=" not in text
    unsafe = unavailable.model_copy(update={"reason": "private provider body https://secret"})
    text = market_context_text(context.model_copy(update={"order_book": unsafe}))
    assert "reason=unrecorded" in text and "https://secret" not in text


def test_capture_time_is_separate_from_asof_and_required_consumption_is_causal():
    requests = []
    src = source(VenueId.BINANCE, depth_payload(VenueId.BINANCE, STAMP + 1000), requests=requests)
    src._observation_clock = lambda: NOW + timedelta(seconds=2)
    captured = read_order_book(
        src,
        identity=identity(VenueId.BINANCE),
        instrument=instrument(VenueId.BINANCE),
        observed_at=NOW,
        allow_later_observation=True,
    )
    assert captured.collected_at == captured.observed_at == NOW + timedelta(seconds=2)
    assert captured.event_time == NOW + timedelta(milliseconds=999)
    require_order_book(
        captured, identity=identity(VenueId.BINANCE), evaluated_at=NOW + timedelta(seconds=3)
    )
    with pytest.raises(MarketContractError, match="future_observation"):
        require_order_book(captured, identity=identity(VenueId.BINANCE), evaluated_at=NOW)
    # A normal as-of read does not permit the later capture.
    strict = read(VenueId.BINANCE, src, NOW)
    assert strict.availability is State.INCOMPLETE


def test_required_historical_book_fails_closed_and_optional_book_preserves_setup():
    from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
    from app.evidence_pipeline.canonical import first_slice_read_policy
    from app.market_contracts.adapters.replay import ReplayPerpetualSource
    from app.signal_fusion.enums import EvidenceRole
    from app.signal_fusion.evaluator import evaluate_setup

    assembler = FirstSliceEvidenceAssembler(ReplayPerpetualSource(), replay=True)
    policy = first_slice_read_policy(ORG_A)
    baseline = assembler.assemble(organization_id=ORG_A, policy=policy)
    optional = policy.model_copy(update={"optional_roles": (EvidenceRole.ORDER_BOOK,)})
    assert (
        assembler.assemble(organization_id=ORG_A, policy=optional).evidence_window_hash
        == baseline.evidence_window_hash
    )
    required = policy.model_copy(
        update={"required_roles": (*policy.required_roles, EvidenceRole.ORDER_BOOK)}
    )
    with pytest.raises(MarketContractError, match="historical_snapshot_coverage_unavailable"):
        assembler.assemble(organization_id=ORG_A, policy=required)
    result = evaluate_setup(
        policy=required,
        command=baseline.assessment_command,
        evidence=baseline.bundle,
        evaluated_at=baseline.evaluated_at,
    )
    assert not next(r for r in result.rule_results if r.rule_id == "complete_warmup").passed
    assert result.state.value not in {"confirmed_setup", "entry_ready"}


def test_shared_bybit_page_is_collected_once_for_multiple_flow_consumers():
    from uuid import uuid4

    from app.evidence_pipeline.market_intelligence import read_order_flow
    from app.market_contracts.order_flow import (
        closed_order_flow_bounds,
        order_flow_identity,
        require_order_flow,
    )

    requests, ticks = [], [0.0]
    src, _ = provider(VenueId.BYBIT, requests=requests)
    src._reuse_recent_pages = True  # Same mode wired by the production process factory.
    # Flow reduction runtime is not elapsed cache time. Keep the production one-second
    # TTL, but advance its monotonic clock explicitly so a slow CI host cannot expire it.
    src._recent_print_cache = TtlValueCache(max_entries=1, ttl_seconds=1, clock=lambda: ticks[0])
    start, end = closed_order_flow_bounds(NOW)
    for elapsed in (0.0, 0.5):
        ticks[0] = elapsed
        flow = src.fetch_order_flow_snapshot(
            identity=order_flow_identity(identity(VenueId.BYBIT)),
            instrument=instrument(VenueId.BYBIT),
            start=start,
            end=end,
            source_connection_id=uuid4(),
            receive_at=NOW,
        )
        assert flow.coverage.completeness.value == "complete"
    assert sum(r.url.path.endswith("recent-trade") for r in requests) == 1
    ticks[0] = 1.0
    reused = read_order_flow(
        src,
        identity=identity(VenueId.BYBIT),
        instrument=instrument(VenueId.BYBIT),
        observed_at=NOW,
    )
    assert reused.availability is State.AVAILABLE
    assert sum(r.url.path.endswith("recent-trade") for r in requests) == 1
    require_order_flow(reused, identity=identity(VenueId.BYBIT), evaluated_at=NOW)
    with pytest.raises(StaleEvidenceError):
        require_order_flow(
            reused,
            identity=identity(VenueId.BYBIT),
            evaluated_at=NOW + timedelta(minutes=10),
        )

    ticks[0] = 1.001
    refreshed = read_order_flow(
        src,
        identity=identity(VenueId.BYBIT),
        instrument=instrument(VenueId.BYBIT),
        observed_at=NOW,
    )
    assert refreshed.availability is State.AVAILABLE
    assert refreshed.event_time == reused.event_time
    assert sum(r.url.path.endswith("recent-trade") for r in requests) == 2


def test_shared_bybit_page_coalesces_concurrent_collectors(monkeypatch):
    requests = []
    src, _ = provider(VenueId.BYBIT, requests=requests)
    src._reuse_recent_pages = True
    src._recent_print_cache = TtlValueCache(max_entries=1, ttl_seconds=1, clock=lambda: 0)
    entered, release, second_started = Event(), Event(), Event()
    original_loader = src._load_recent_prints

    def load_recent_prints():
        entered.set()
        assert release.wait(5)
        return original_loader()

    def second_consumer():
        second_started.set()
        return src._cached_recent_prints(NOW)

    monkeypatch.setattr(src, "_load_recent_prints", load_recent_prints)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(src._cached_recent_prints, NOW)
        assert entered.wait(5)
        second = pool.submit(second_consumer)
        assert second_started.wait(5)
        release.set()
        first_rows, second_rows = first.result(), second.result()
    assert first_rows == second_rows
    assert first_rows is not second_rows and first_rows[0] is not second_rows[0]
    assert sum(r.url.path.endswith("recent-trade") for r in requests) == 1


def test_future_bybit_tail_cannot_prove_historical_window_end():
    from app.evidence_pipeline.market_intelligence import read_order_flow

    src, rows = provider(VenueId.BYBIT)
    # The latest tail witness must be at/before evaluation, not a future print.
    rows[-1]["time"] = str(STAMP + 1)
    item = read_order_flow(
        src, identity=identity(VenueId.BYBIT), instrument=instrument(VenueId.BYBIT), observed_at=NOW
    )
    assert item.availability is State.INCOMPLETE
    assert item.rolling_cvd is None


@pytest.mark.parametrize("shared", [False, True])
def test_all_future_bybit_prints_are_incomplete_without_an_empty_page_crash(shared):
    from app.evidence_pipeline.market_intelligence import read_order_flow

    src, rows = provider(VenueId.BYBIT)
    src._reuse_recent_pages = shared
    for index, row in enumerate(rows):
        row["time"] = str(STAMP + index + 1)
    item = read_order_flow(
        src, identity=identity(VenueId.BYBIT), instrument=instrument(VenueId.BYBIT), observed_at=NOW
    )
    assert item.availability is State.INCOMPLETE and not item.windows


def test_agent_reports_stale_at_consumer_time_and_explicit_optional_missing():
    from app.interactive_agent.market_context import market_context_text

    context = MarketEvidenceContext(
        evaluated_at=NOW + timedelta(seconds=11),
        anchor_venue="binance",
        anchor_symbol="BTCUSDT",
        order_book=read(VenueId.BINANCE),
    )
    text = market_context_text(context)
    assert "Open interest: MISSING" in text and "Executed flow and CVD: MISSING" in text
    assert "Resting order book venue=binance: STALE" in text
    assert "spread=" not in text
