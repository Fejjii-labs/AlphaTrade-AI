"""Deterministic real-print contracts, bounded 5m flow, and canonical gating."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from app.core.config import Settings
from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.evidence_pipeline.canonical import first_slice_read_policy
from app.evidence_pipeline.market_intelligence import read_order_flow
from app.evidence_pipeline.service import CanonicalEvidenceService
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.adapters.bybit_usdt_perpetual import BybitUsdtPerpetualSource
from app.market_contracts.adapters.failover import FailoverPerpetualSource
from app.market_contracts.adapters.replay import ReplayPerpetualSource
from app.market_contracts.derivatives import DerivativeMetric
from app.market_contracts.enums import DataCompleteness, GapState, VenueId
from app.market_contracts.errors import (
    DuplicateDataError,
    EvidenceSourceSwitchRequiredError,
    GapDetectedError,
    MarketContractError,
    OutOfOrderTradesError,
    StaleEvidenceError,
    WrongSourceError,
)
from app.market_contracts.observation import observation_from_order_flow
from app.market_contracts.order_flow import (
    CVD_RESET,
    ORDER_FLOW_METHOD,
    closed_order_flow_bounds,
    hash_order_flow,
    order_flow_identity,
    order_flow_lineage,
    order_flow_observation,
    require_order_flow,
)
from app.market_contracts.trade_reduction import build_released_trade_snapshot
from app.market_contracts.trades import build_trade_event
from app.schemas.nested_continuation import EvidenceAvailability as State
from app.signal_fusion.adapters import evidence_window_from_assessment_command
from app.signal_fusion.enums import EvidenceRole, SetupAssessmentState
from app.signal_fusion.evaluator import evaluate_setup
from tests.support.phase5_market import RECORDED_FIXTURE_GRACE
from tests.support.phase6_fusion import ORG_ID
from tests.test_market_intelligence_oi_funding import (
    contract_payload,
    identity,
    instrument,
    payload,
)

END = datetime(2026, 10, 1, 12, 10, tzinfo=UTC)
START = END - timedelta(minutes=10)
NOW = END + timedelta(seconds=2)
VENUES = (VenueId.BINANCE, VenueId.BYBIT)


def prints():
    return [
        (START, "100", "2", False),
        (START + timedelta(minutes=2), "100", "1", True),
        (START + timedelta(minutes=5), "90", "5", False),
        (END - timedelta(seconds=1), "90", "1", True),
    ]


def provider(venue, *, rows=None, status=200, requests=None, book=None):
    data = prints() if rows is None else rows
    if venue is VenueId.BINANCE:
        records = [
            {"a": 10 + index, "p": price, "q": qty, "m": maker, "T": int(at.timestamp() * 1000)}
            for index, (at, price, qty, maker) in enumerate(data)
        ]
    else:
        # The prefix proves the request start; terminal print proves the closed end.
        data = [
            (START - timedelta(seconds=1), "100", "1", False),
            *data,
            (END + timedelta(seconds=1), "90", "1", False),
        ]
        records = [
            {
                "execId": f"bybit-{index}",
                "symbol": "BTCUSDT",
                "price": price,
                "size": qty,
                "side": "Sell" if maker else "Buy",
                "time": str(int(at.timestamp() * 1000)),
            }
            for index, (at, price, qty, maker) in enumerate(data)
        ]

    def handle(request):
        if requests is not None:
            requests.append(request)
        assert request.method == "GET"
        assert "authorization" not in request.headers
        if request.url.path.endswith(("exchangeInfo", "instruments-info")):
            return httpx.Response(200, json=book if book is not None else contract_payload(venue))
        if request.url.path.endswith(
            ("openInterest", "open-interest", "fundingRate", "funding/history")
        ):
            metric = (
                DerivativeMetric.OPEN_INTEREST
                if request.url.path.endswith(("openInterest", "open-interest"))
                else DerivativeMetric.FUNDING
            )
            return httpx.Response(
                200, json=payload(venue, metric, stamp=int(NOW.timestamp() * 1000))
            )
        response = (
            records
            if venue is VenueId.BINANCE
            else {"retCode": 0, "result": {"category": "linear", "list": list(reversed(records))}}
        )
        return httpx.Response(status, json=response)

    cls = BinanceUsdmPerpetualSource if venue is VenueId.BINANCE else BybitUsdtPerpetualSource
    return cls(transport=httpx.MockTransport(handle), max_retries=0), records


def read(venue=VenueId.BINANCE, src=None, **kwargs):
    if src is None:
        src, _ = provider(venue)
    return read_order_flow(
        src,
        identity=identity(venue),
        instrument=instrument(venue),
        observed_at=kwargs.pop("observed_at", NOW),
        **kwargs,
    )


def snapshot(venue=VenueId.BINANCE, *, data=None, receive_at=NOW, lineage=None):
    ident = order_flow_identity(identity(venue))
    lineage = lineage or order_flow_lineage(ident)
    trades = [
        build_trade_event(
            instrument=instrument(venue),
            venue_trade_id=str(index),
            sequence=index,
            price=Decimal(price),
            quantity=Decimal(qty),
            buyer_is_maker=maker,
            event_timestamp=at,
            receive_timestamp=receive_at,
            source_connection_id=lineage,
            adapter_version=ident.source.adapter_version,
            aggressor_convention=ident.source.aggressor_convention,
        )
        for index, (at, price, qty, maker) in enumerate(prints() if data is None else data)
    ]
    return build_released_trade_snapshot(
        trades,
        identity=ident,
        lineage_id=lineage,
        window_start=START,
        window_end=END,
        observed_at=receive_at,
    )


@pytest.mark.parametrize("venue", VENUES)
def test_verified_provider_normalization_and_bounded_cvd(venue):
    requests = []
    src, _ = provider(venue, requests=requests)
    result = read(venue, src)
    assert result.availability is State.AVAILABLE
    assert result.completeness is DataCompleteness.COMPLETE
    assert result.identity.venue is venue
    assert result.identity.timeframe.value == "5m"
    assert result.identity.market_type.value == "perpetual"
    assert result.identity.instrument == instrument(venue)
    assert result.identity.provenance.is_live and not result.identity.provenance.is_mock
    assert result.window_start == START and result.window_end == END
    assert result.observed_at == NOW and result.event_time == END - timedelta(seconds=1)
    assert result.base_units == result.cvd_units == "BTC" and result.quote_units == "USDT"
    assert result.calculation_method == ORDER_FLOW_METHOD and result.reset_semantics == CVD_RESET
    assert result.baseline == 0
    previous, current = result.windows
    assert (previous.trade_count, current.trade_count) == (2, 2)
    assert current.aggressive_buy_base_volume == 5
    assert current.aggressive_sell_base_volume == 1
    assert current.aggressive_buy_quote_volume == 450
    assert current.aggressive_sell_quote_volume == 90
    assert current.signed_volume_delta == 4
    assert current.quote_volume_delta == 360
    assert current.buy_sell_imbalance_ratio == Decimal(2) / Decimal(3)
    assert current.rolling_cvd == result.rolling_cvd == 5
    assert result.rolling_quote_cvd == 460
    assert result.cvd_change == 4 and result.cvd_slope_base_per_second == Decimal(4) / 300
    assert result.order_flow_strengthening_side == result.cvd_supportive_side == "buy"
    assert result.cvd_weakening is False
    assert result.cvd_divergence == "bullish_print_close_to_close"
    require_order_flow(result, identity=identity(venue), evaluated_at=NOW)
    assert len(result.windows) == 2
    if venue is VenueId.BINANCE:
        assert requests[-1].url.params["endTime"] == str(int(END.timestamp() * 1000) - 1)
        assert requests[-1].url.params["startTime"] == str(int(START.timestamp() * 1000))
    else:
        assert requests[-1].url.params["category"] == "linear"
        assert requests[-1].url.params["limit"] == "1000"


@pytest.mark.parametrize("venue", VENUES)
@pytest.mark.parametrize("maker,expected", [(True, "sell"), (False, "buy")])
def test_verified_aggressor_mapping(venue, maker, expected):
    data = [(START, "100", "1", maker), (END - timedelta(seconds=1), "100", "2", maker)]
    src, _ = provider(venue, rows=data)
    item = read(venue, src)
    assert item.cvd_supportive_side == expected
    assert item.rolling_cvd == (-3 if maker else 3)


@pytest.mark.parametrize("value", [None, "false", "true", 0, 1, [], {}])
def test_binance_never_coerces_an_unverified_maker_flag(value):
    src, records = provider(VenueId.BINANCE)
    records[0]["m"] = value
    result = read(src=src)
    assert result.availability in {State.INCOMPLETE, State.UNSUPPORTED}
    assert result.rolling_cvd is None


@pytest.mark.parametrize("value", [None, "", "maker", "Unknown", True])
def test_bybit_unknown_taker_side_fails_closed(value):
    src, records = provider(VenueId.BYBIT)
    records[1]["side"] = value
    result = read(VenueId.BYBIT, src)
    assert result.availability is State.INCOMPLETE
    assert result.rolling_cvd is None


@pytest.mark.parametrize(
    "seconds,expected", [(0, END), (299, END), (300, END + timedelta(minutes=5))]
)
def test_five_minute_utc_close_boundaries(seconds, expected):
    start, end = closed_order_flow_bounds(END + timedelta(seconds=seconds))
    assert end == expected and end - start == timedelta(minutes=10)


def test_exact_five_minute_boundary_belongs_to_next_window_and_end_is_excluded():
    src, records = provider(VenueId.BINANCE)
    records.append({"a": 14, "p": "90", "q": "10000", "m": False, "T": int(END.timestamp() * 1000)})
    result = read(src=src)
    assert result.windows[0].trade_count == result.windows[1].trade_count == 2
    assert result.rolling_cvd == 5


@pytest.mark.parametrize("venue", VENUES)
def test_duplicate_prints_are_counted_once_and_conflicts_are_refused(venue):
    src, records = provider(venue)
    records.insert(2, dict(records[1]))
    assert sum(window.trade_count for window in read(venue, src).windows) == 4
    src, records = provider(venue)
    duplicate = dict(records[1])
    duplicate["q" if venue is VenueId.BINANCE else "size"] = "99"
    records.insert(2, duplicate)
    assert read(venue, src).availability is State.INCOMPLETE


def test_sortable_out_of_order_delivery_is_deterministic_but_reversed_event_time_is_not():
    src, records = provider(VenueId.BINANCE)
    expected = read().content_hash
    records.reverse()
    assert read(src=src).content_hash == expected
    src, records = provider(VenueId.BINANCE)
    records[1]["T"] = records[2]["T"] + 1
    result = read(src=src)
    assert result.availability is State.INCOMPLETE
    assert result.rolling_cvd is None


def test_missing_aggregate_id_cannot_prove_coverage():
    src, records = provider(VenueId.BINANCE)
    records.pop(1)
    assert read(src=src).availability is State.INCOMPLETE


def test_drained_binance_empty_window_is_missing_and_not_neutral_confirmation():
    src, _ = provider(VenueId.BINANCE, rows=[])
    item = read(src=src)
    assert item.availability is State.MISSING
    assert item.rolling_cvd is item.cvd_supportive_side is item.cvd_weakening is None


def test_bybit_missing_prefix_missing_end_and_lost_overlap_are_incomplete():
    for removal in (0, -1):
        src, records = provider(VenueId.BYBIT)
        records.pop(removal)
        if removal == 0:
            records.pop(0)  # Earliest remaining print is after the requested start.
        assert read(VenueId.BYBIT, src).availability is State.INCOMPLETE
    src, records = provider(VenueId.BYBIT)
    assert read(VenueId.BYBIT, src).availability is State.AVAILABLE
    records[:] = [
        {**records[-1], "execId": "disconnected", "time": str(int(NOW.timestamp() * 1000))}
    ]
    assert read(VenueId.BYBIT, src).availability is State.INCOMPLETE


@pytest.mark.parametrize("venue", VENUES)
def test_unverified_product_is_unsupported(venue):
    book = contract_payload(venue)
    row = book["symbols"][0] if venue is VenueId.BINANCE else book["result"]["list"][0]
    row["contractType"] = "CURRENT_QUARTER" if venue is VenueId.BINANCE else "LinearFutures"
    requests = []
    src, _ = provider(venue, book=book, requests=requests)
    result = read(venue, src)
    assert result.availability is State.UNSUPPORTED and result.windows == ()
    assert len(requests) == 1


@pytest.mark.parametrize("venue", VENUES)
@pytest.mark.parametrize("status", [403, 418, 429, 451, 503])
def test_upstream_failures_are_explicit_missing(venue, status):
    src, _ = provider(venue, status=status)
    assert read(venue, src).availability is State.MISSING


def test_incomplete_snapshot_and_wrong_venue_are_never_usable():
    good = snapshot()
    bad = good.model_copy(update={"usable": False})
    with pytest.raises(MarketContractError):
        order_flow_observation(identity=identity(VenueId.BINANCE), observed_at=NOW, snapshot=bad)
    with pytest.raises(WrongSourceError):
        order_flow_observation(identity=identity(VenueId.BYBIT), observed_at=NOW, snapshot=good)
    gap = good.coverage.model_copy(
        update={"gap_state": GapState.CONFIRMED, "completeness": DataCompleteness.PARTIAL}
    )
    bad = good.model_copy(update={"coverage": gap})
    with pytest.raises(MarketContractError):
        order_flow_observation(identity=identity(VenueId.BINANCE), observed_at=NOW, snapshot=bad)


def test_stale_closed_window_and_consumer_recheck():
    item = read()
    with pytest.raises(StaleEvidenceError):
        require_order_flow(
            item, identity=identity(VenueId.BINANCE), evaluated_at=END + timedelta(seconds=600)
        )
    require_order_flow(
        item, identity=identity(VenueId.BINANCE), evaluated_at=END + timedelta(seconds=599)
    )
    stale = read(observed_at=END + timedelta(seconds=600), window_end=END)
    assert stale.availability is State.STALE
    assert stale.rolling_cvd == 5
    with pytest.raises(ValueError):
        observation_from_order_flow(stale)


@pytest.mark.parametrize("state", [State.MISSING, State.STALE, State.UNSUPPORTED, State.INCOMPLETE])
def test_all_unavailable_required_states_refuse(state):
    if state is State.STALE:
        item = read(observed_at=END + timedelta(seconds=600), window_end=END)
    else:
        item = order_flow_observation(
            identity=identity(VenueId.BINANCE), observed_at=NOW, availability=state
        )
    with pytest.raises(MarketContractError, match=state.value):
        require_order_flow(item, identity=identity(VenueId.BINANCE), evaluated_at=item.observed_at)


def test_deterministic_replay_semantic_hash_and_transport_clock_convergence():
    one = order_flow_observation(
        identity=identity(VenueId.BINANCE), observed_at=NOW, snapshot=snapshot(lineage=uuid4())
    )
    two = order_flow_observation(
        identity=identity(VenueId.BINANCE),
        observed_at=NOW + timedelta(seconds=1),
        snapshot=snapshot(receive_at=NOW + timedelta(seconds=1), lineage=uuid4()),
    )
    assert one.content_hash == two.content_hash
    assert (
        observation_from_order_flow(one).content_hash
        == observation_from_order_flow(two).content_hash
    )
    changed = hash_order_flow(two.model_copy(update={"reset_semantics": "incorrect-reset/v9"}))
    assert changed.content_hash != one.content_hash
    with pytest.raises(WrongSourceError):
        require_order_flow(
            changed, identity=identity(VenueId.BINANCE), evaluated_at=two.observed_at
        )
    changed = two.model_copy(update={"rolling_cvd": Decimal(999)})
    with pytest.raises(MarketContractError):
        require_order_flow(
            changed, identity=identity(VenueId.BINANCE), evaluated_at=two.observed_at
        )


def test_window_roll_rebuilds_baseline_instead_of_carrying_absolute_cvd():
    first = read()
    later = [(at + timedelta(minutes=5), price, qty, maker) for at, price, qty, maker in prints()]
    src, _ = provider(VenueId.BINANCE, rows=later)
    second = read(src=src, observed_at=NOW + timedelta(minutes=5))
    assert second.rolling_cvd == first.rolling_cvd == 5
    assert second.baseline == first.baseline == 0
    assert second.series_identity != first.series_identity
    assert second.window_start == first.window_start + timedelta(minutes=5)


def test_venue_failover_requires_atomic_restart_and_new_venue_bound_cvd():
    primary, _ = provider(VenueId.BINANCE, status=451)
    secondary, _ = provider(VenueId.BYBIT)
    failover = FailoverPerpetualSource(
        primary,
        secondary,
        primary_instrument=instrument(VenueId.BINANCE),
        secondary_instrument=instrument(VenueId.BYBIT),
    )
    with pytest.raises(EvidenceSourceSwitchRequiredError):
        read(src=failover)
    result = read(VenueId.BYBIT, failover)
    assert result.identity.venue is VenueId.BYBIT
    assert result.baseline == 0 and result.rolling_cvd == 5
    assert result.series_identity != read().series_identity
    with pytest.raises(WrongSourceError):
        require_order_flow(result, identity=identity(VenueId.BINANCE), evaluated_at=NOW)


def test_weakening_and_bearish_divergence_require_defined_inputs():
    data = [(START, "100", "5", True), (END - timedelta(seconds=1), "110", "2", True)]
    src, _ = provider(VenueId.BINANCE, rows=data)
    item = read(src=src)
    assert item.cvd_weakening is True
    assert item.cvd_supportive_side == "sell"
    assert item.order_flow_strengthening_side is None
    assert item.cvd_divergence == "bearish_print_close_to_close"
    src, _ = provider(VenueId.BINANCE, rows=[(END - timedelta(seconds=1), "100", "2", False)])
    item = read(src=src)
    assert item.windows[0].trade_count == 0
    assert item.windows[0].buy_sell_imbalance_ratio is None
    assert item.cvd_supportive_side is item.cvd_weakening is item.cvd_divergence is None


def test_replay_unsupported_and_optional_roles_preserve_setup_identity():
    service = CanonicalEvidenceService(Settings(perpetual_evidence_source="replay"))
    item = service.read(organization_id=ORG_ID).order_flow
    assert item.availability is State.UNSUPPORTED and item.rolling_cvd is None
    assembler = FirstSliceEvidenceAssembler(ReplayPerpetualSource(), replay=True)
    policy = first_slice_read_policy(ORG_ID)
    baseline = assembler.assemble(organization_id=ORG_ID, policy=policy)
    optional = policy.model_copy(
        update={"optional_roles": (EvidenceRole.CVD_5M, EvidenceRole.ORDER_FLOW_5M)}
    )
    result = assembler.assemble(organization_id=ORG_ID, policy=optional)
    assert result.evidence_window_hash == baseline.evidence_window_hash
    assert result.bundle.order_flow is None
    for role in (EvidenceRole.CVD_5M, EvidenceRole.ORDER_FLOW_5M):
        required = policy.model_copy(update={"required_roles": (*policy.required_roles, role)})
        with pytest.raises(MarketContractError, match="UNSUPPORTED"):
            assembler.assemble(organization_id=ORG_ID, policy=required)


def live_assembler():
    from tests.test_live_evidence_pipeline import _fixture_usdm_handler

    transport = _fixture_usdm_handler()

    def handle(request):
        if request.url.path.endswith("exchangeInfo"):
            return httpx.Response(200, json=contract_payload(VenueId.BINANCE))
        return transport.handle_request(request)

    src = BinanceUsdmPerpetualSource(
        transport=httpx.MockTransport(handle),
        max_retries=0,
        settlement_grace=RECORDED_FIXTURE_GRACE,
    )
    return FirstSliceEvidenceAssembler(src, replay=False)


def test_required_flow_is_hashed_bound_and_rechecked_by_evaluator():
    from tests.support.phase5_market import EVALUATED_AT

    policy = first_slice_read_policy(ORG_ID)
    policy = policy.model_copy(
        update={
            "required_roles": (
                *policy.required_roles,
                EvidenceRole.CVD_5M,
                EvidenceRole.ORDER_FLOW_5M,
            )
        }
    )
    result = live_assembler().assemble(
        organization_id=ORG_ID, policy=policy, evaluated_at=EVALUATED_AT
    )
    assert result.bundle.order_flow.availability is State.AVAILABLE
    assert result.bundle.order_flow.window_end == result.trigger_bar.interval_end
    assert (
        evidence_window_from_assessment_command(result.assessment_command).content_hash
        == result.evidence_window_hash
    )
    hashes = {obs.payload_content_hash for obs in result.assessment_command.public_observations}
    assert result.bundle.order_flow.content_hash in hashes
    valid = evaluate_setup(
        policy=policy,
        command=result.assessment_command,
        evidence=result.bundle,
        evaluated_at=EVALUATED_AT,
    )
    assert next(rule for rule in valid.rule_results if rule.rule_id == "complete_warmup").passed
    for bundle, at in [
        (result.bundle.model_copy(update={"order_flow": None}), EVALUATED_AT),
        (result.bundle, EVALUATED_AT + timedelta(minutes=11)),
        (result.bundle.model_copy(update={"order_flow": read()}), EVALUATED_AT),
    ]:
        assessment = evaluate_setup(
            policy=policy, command=result.assessment_command, evidence=bundle, evaluated_at=at
        )
        assert assessment.state is not SetupAssessmentState.CONFIRMED_SETUP
        assert not next(
            rule for rule in assessment.rule_results if rule.rule_id == "complete_warmup"
        ).passed
    # Optional evidence supplied to the evaluator cannot alter the canonical window.
    policy = first_slice_read_policy(ORG_ID)
    baseline = live_assembler().assemble(
        organization_id=ORG_ID, policy=policy, evaluated_at=EVALUATED_AT
    )
    before = evaluate_setup(
        policy=policy,
        command=baseline.assessment_command,
        evidence=baseline.bundle,
        evaluated_at=EVALUATED_AT,
    )
    after = evaluate_setup(
        policy=policy,
        command=baseline.assessment_command,
        evidence=baseline.bundle.model_copy(update={"order_flow": read()}),
        evaluated_at=EVALUATED_AT,
    )
    assert before == after


@pytest.mark.parametrize("error", [DuplicateDataError, GapDetectedError, OutOfOrderTradesError])
def test_acquisition_errors_become_incomplete_without_values(error):
    class Broken:
        def fetch_order_flow_snapshot(self, **kwargs):
            raise error("unproven prints")

    item = read(src=Broken())
    assert item.availability is State.INCOMPLETE and item.windows == ()


def test_order_flow_failover_discards_quote_setup_and_derivative_primary_facts(monkeypatch):
    from app.evidence_pipeline.http_schemas import (
        CanonicalCompletenessRead,
        CanonicalSetupEvidenceRead,
    )
    from app.evidence_pipeline.service import _unavailable_price
    from app.evidence_pipeline.types import CurrentPricePresentation

    primary, _ = provider(VenueId.BINANCE, status=451)
    secondary, _ = provider(VenueId.BYBIT)
    source = FailoverPerpetualSource(
        primary,
        secondary,
        primary_instrument=instrument(VenueId.BINANCE),
        secondary_instrument=instrument(VenueId.BYBIT),
    )
    service = CanonicalEvidenceService(
        Settings(perpetual_evidence_source="binance_usdm"), source=source, clock=lambda: NOW
    )
    quotes, setups = [], []

    def quote(**kwargs):
        quotes.append(source.active_instrument().venue)
        return _unavailable_price(
            identity_is_live=True, presentation=CurrentPricePresentation.UNAVAILABLE
        ), "unavailable"

    def setup(**kwargs):
        setups.append(source.active_instrument().venue)
        return CanonicalSetupEvidenceRead(
            available=False,
            completeness=CanonicalCompletenessRead(
                ohlcv_15m="unknown", ohlcv_4h="unknown", cvd="unknown", signed_flow="unknown"
            ),
        ), "unavailable"

    monkeypatch.setattr(service, "_quote", quote)
    monkeypatch.setattr(service, "_setup", setup)
    result = service.read(organization_id=ORG_ID)
    assert quotes == setups == list(VENUES)
    assert result.source.venue == "bybit"
    assert all(
        item.identity.venue is VenueId.BYBIT and item.availability is State.AVAILABLE
        for item in result.market_intelligence
    )
    assert result.order_flow.availability is State.AVAILABLE
    assert result.order_flow.identity.venue is VenueId.BYBIT
    assert result.order_flow.baseline == 0


@pytest.mark.parametrize("venue", VENUES)
@pytest.mark.parametrize("bad", ["overlong", "forming", "misaligned", "wrong_timeframe"])
def test_new_provider_acquisition_refuses_unbounded_or_forming_requests(venue, bad):
    requests = []
    src, _ = provider(venue, requests=requests)
    ident, start, end = order_flow_identity(identity(venue)), START, END
    if bad == "overlong":
        start -= timedelta(hours=1)
    elif bad == "forming":
        start += timedelta(minutes=5)
        end += timedelta(minutes=5)
    elif bad == "misaligned":
        start += timedelta(seconds=1)
        end += timedelta(seconds=1)
    else:
        ident = identity(venue)
    with pytest.raises(MarketContractError):
        src.fetch_order_flow_snapshot(
            identity=ident,
            instrument=instrument(venue),
            start=start,
            end=end,
            source_connection_id=uuid4(),
            receive_at=NOW,
        )
    assert requests == []


def test_binance_reduced_cache_keeps_five_and_fifteen_minute_identities_separate():
    from app.schemas.common import Timeframe

    # A common aligned interval whose aggregate cache keys previously collided.
    end, start = END - timedelta(minutes=10), END - timedelta(minutes=25)
    data = [
        (start + timedelta(seconds=1), "100", "1", False),
        (end - timedelta(seconds=1), "100", "1", True),
    ]
    src, _ = provider(VenueId.BINANCE, rows=data)
    results = []
    for timeframe in (Timeframe.M15, Timeframe.M5, Timeframe.M15, Timeframe.M5):
        ident = identity(VenueId.BINANCE).model_copy(update={"timeframe": timeframe})
        result = src.reduce_ordered_trades(
            identity=ident,
            instrument=instrument(VenueId.BINANCE),
            start=start,
            end=end,
            source_connection_id=uuid4(),
            receive_at=NOW,
        )
        assert result.cursor.identity.timeframe is timeframe
        results.append(result)
    assert results[0].released_tape == results[2].released_tape
    assert results[1].released_tape == results[3].released_tape
    assert len(results[0].released_tape.bars) == 1
    assert len(results[1].released_tape.bars) == 2


@pytest.mark.parametrize("venue", VENUES)
def test_malformed_contract_book_is_incomplete(venue):
    book = contract_payload(venue)
    row = book["symbols"][0] if venue is VenueId.BINANCE else book["result"]["list"][0]
    row.pop("contractType")
    src, _ = provider(venue, book=book)
    assert read(venue, src).availability is State.INCOMPLETE


@pytest.mark.parametrize("venue", VENUES)
def test_wrong_instrument_or_provider_never_yields_a_mixed_series(venue):
    other = VENUES[1] if venue is VENUES[0] else VENUES[0]
    src, _ = provider(other)
    item = read(venue, src)
    assert item.availability is State.INCOMPLETE
    assert item.rolling_cvd is None


def test_rehashing_an_inconsistent_directional_state_cannot_make_it_usable():
    item = read()
    bad = hash_order_flow(item.model_copy(update={"cvd_supportive_side": "sell"}))
    with pytest.raises(WrongSourceError, match="invalid_structure"):
        require_order_flow(bad, identity=identity(VenueId.BINANCE), evaluated_at=NOW)


def test_bybit_hashes_ignore_synthetic_rank_offsets_from_prior_poll_history():
    baseline = read(VenueId.BYBIT)
    src, records = provider(VenueId.BYBIT)
    for index in range(3):
        records.insert(
            0,
            {
                **records[0],
                "execId": f"earlier-{index}",
                "time": str(int((START - timedelta(seconds=4 - index)).timestamp() * 1000)),
            },
        )
    assert read(VenueId.BYBIT, src).content_hash == baseline.content_hash


def test_bybit_start_bucket_requires_a_strictly_earlier_prefix():
    src, records = provider(VenueId.BYBIT)
    records.pop(0)  # Endpoint's oldest print is now exactly at the start bucket.
    result = read(VenueId.BYBIT, src)
    assert result.availability is State.INCOMPLETE
    assert result.rolling_cvd is None


@pytest.mark.parametrize("role", [EvidenceRole.CVD_5M, EvidenceRole.ORDER_FLOW_5M])
def test_strategy_brain_family_cannot_bypass_required_print_gating(role):
    from app.schemas.nested_continuation import NestedContinuationSpec
    from app.signal_fusion.order_flow_inputs import require_bound_order_flow

    assembled = FirstSliceEvidenceAssembler(ReplayPerpetualSource(), replay=True).assemble(
        organization_id=ORG_ID
    )
    policy = first_slice_read_policy(ORG_ID).model_copy(
        update={"required_roles": (*first_slice_read_policy(ORG_ID).required_roles, role)}
    )
    # Required roles must pass the same payload gate even on the family dispatch path.
    fact = read()
    for item in (None, fact):
        with pytest.raises(MarketContractError):
            require_bound_order_flow(
                item,
                command=assembled.assessment_command,
                required_roles=policy.required_roles,
                evaluated_at=assembled.evaluated_at,
                trigger_end=assembled.trigger_bar.interval_end,
            )
    command = assembled.assessment_command.model_copy(
        update={
            "mandatory_evidence_roles": policy.required_roles,
            "selected_roles": (*assembled.assessment_command.selected_roles, role),
            "public_observations": (
                *assembled.assessment_command.public_observations,
                observation_from_order_flow(fact, cvd=role is EvidenceRole.CVD_5M),
            ),
        }
    )
    # Use an in-family identity observation to exercise required-payload validation.
    public = command.public_observations[-1].model_copy(update={"identity": assembled.identity})
    command = command.model_copy(
        update={"public_observations": (*command.public_observations[:-1], public)}
    )
    assessment = evaluate_setup(
        policy=policy,
        command=command,
        evidence=assembled.bundle,
        evaluated_at=assembled.evaluated_at,
        nested_spec=NestedContinuationSpec(symbol="BTCUSDT", direction=command.direction),
    )
    assert assessment.state is not SetupAssessmentState.CONFIRMED_SETUP
    assert not next(
        rule for rule in assessment.rule_results if rule.rule_id == "required_order_flow_binding"
    ).passed
