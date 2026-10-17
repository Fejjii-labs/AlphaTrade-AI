"""Deterministic official OI/funding payload contracts; never contacts a venue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.core.config import Settings
from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.evidence_pipeline.canonical import first_slice_read_policy
from app.evidence_pipeline.market_intelligence import read_market_intelligence
from app.evidence_pipeline.service import CanonicalEvidenceService
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.adapters.bybit_usdt_perpetual import BybitUsdtPerpetualSource
from app.market_contracts.adapters.failover import FailoverPerpetualSource
from app.market_contracts.adapters.http import ReadOnlyHttpGetClient
from app.market_contracts.adapters.replay import ReplayPerpetualSource
from app.market_contracts.derivatives import (
    DerivativeMetric,
    derivative_freshness_policy,
    derivative_observation,
    hash_derivative_observation,
    require_derivative_observations,
)
from app.market_contracts.enums import FreshnessState, VenueId
from app.market_contracts.errors import (
    EvidenceSourceSwitchRequiredError,
    MarketContractError,
    NetworkMutationForbiddenError,
    StaleEvidenceError,
    WrongInstrumentError,
    WrongMarketError,
    WrongSourceError,
)
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.identity import binance_usdm_perpetual, bybit_usdt_perpetual
from app.market_contracts.observation import observation_from_derivative
from app.schemas.common import Timeframe
from app.schemas.nested_continuation import EvidenceAvailability as State
from app.signal_fusion.adapters import evidence_window_from_assessment_command
from app.signal_fusion.enums import EvidenceRole, SetupAssessmentState
from app.signal_fusion.evaluator import evaluate_setup
from tests.support.phase6_fusion import ORG_ID

NOW = datetime(2026, 10, 1, 16, tzinfo=UTC)
MILLIS = int(NOW.timestamp() * 1000)
METRICS = tuple(DerivativeMetric)
VENUES = (VenueId.BINANCE, VenueId.BYBIT)


def instrument(venue, symbol="BTCUSDT"):
    return (bybit_usdt_perpetual if venue is VenueId.BYBIT else binance_usdm_perpetual)(symbol)


def identity(venue, symbol="BTCUSDT"):
    return first_slice_identity(
        timeframe=Timeframe.M15, replay=False, is_live=True, instrument=instrument(venue, symbol)
    )


def contract_payload(venue, symbol="BTCUSDT"):
    if venue is VenueId.BINANCE:
        return {
            "symbols": [
                {
                    "symbol": symbol,
                    "contractType": "PERPETUAL",
                    "quoteAsset": "USDT",
                    "baseAsset": symbol[:-4],
                    "status": "TRADING",
                }
            ]
        }
    return {
        "retCode": 0,
        "result": {
            "category": "linear",
            "list": [
                {
                    "symbol": symbol,
                    "contractType": "LinearPerpetual",
                    "quoteCoin": "USDT",
                    "baseCoin": symbol[:-4],
                    "status": "Trading",
                }
            ],
        },
    }


def payload(venue, metric, value=None, stamp=MILLIS, symbol="BTCUSDT"):
    value = value if value is not None else ("123.4500" if metric is METRICS[0] else "-0.00010000")
    if venue is VenueId.BINANCE:
        return (
            {"symbol": symbol, "openInterest": value, "time": stamp}
            if metric is METRICS[0]
            else [{"symbol": symbol, "fundingRate": value, "fundingTime": stamp}]
        )
    row = (
        {"openInterest": value, "timestamp": str(stamp)}
        if metric is METRICS[0]
        else {"symbol": symbol, "fundingRate": value, "fundingRateTimestamp": str(stamp)}
    )
    return {
        "retCode": 0,
        "time": MILLIS + 90000,
        "result": {"category": "linear", "symbol": symbol, "list": [row]},
    }


def source(venue, response=None, *, status=200, contract=None, requests=None):
    def handle(request):
        if requests is not None:
            requests.append(request)
        assert request.method == "GET"
        assert "authorization" not in request.headers
        if request.url.path.endswith(("exchangeInfo", "instruments-info")):
            return httpx.Response(
                200, json=contract if contract is not None else contract_payload(venue)
            )
        metric = (
            METRICS[0]
            if request.url.path.endswith(("openInterest", "open-interest"))
            else METRICS[1]
        )
        return httpx.Response(
            status, json=response if response is not None else payload(venue, metric)
        )

    cls = BybitUsdtPerpetualSource if venue is VenueId.BYBIT else BinanceUsdmPerpetualSource
    return cls(transport=httpx.MockTransport(handle), max_retries=0)


def fetch(venue, metric, src=None):
    return (src or source(venue)).fetch_derivative_observation(
        identity=identity(venue), instrument=instrument(venue), metric=metric, observed_at=NOW
    )


@pytest.mark.parametrize("venue", VENUES)
@pytest.mark.parametrize("metric", METRICS)
def test_official_contract_metadata_and_normalization(venue, metric):
    requests = []
    src = source(venue, requests=requests)
    result = fetch(venue, metric, src)
    assert result.availability is State.AVAILABLE
    assert result.value == (Decimal("123.45") if metric is METRICS[0] else Decimal("-0.0001"))
    assert result.event_time == NOW
    assert result.observed_at == NOW
    assert result.identity.instrument == instrument(venue)
    assert result.identity.venue is venue
    assert result.identity.market_type.value == "perpetual"
    assert result.identity.provenance.provider_name == src.name
    assert result.identity.provenance.fallback_used is False
    assert result.units == ("BTC" if metric is METRICS[0] else "ratio_per_settlement")
    assert result.identity.timeframe == (
        Timeframe.M5 if venue is VenueId.BYBIT and metric is METRICS[0] else None
    )
    assert result.freshness.state is FreshnessState.FRESH
    assert hash_derivative_observation(result).content_hash == result.content_hash
    assert result.model_dump(mode="json")["value"] in {"123.4500", "-0.00010000"}
    assert len(requests) == 2
    assert requests[-1].url.params["symbol"] == "BTCUSDT"
    if venue is VenueId.BYBIT:
        assert requests[-1].url.params["category"] == "linear"
        if metric is METRICS[0]:
            assert requests[-1].url.params["intervalTime"] == "5min"
            assert "sum_both_sides" in result.calculation_method
    if metric is METRICS[1]:
        assert requests[-1].url.params["limit"] == "1"
        assert requests[-1].url.params["endTime"] == str(MILLIS)
        assert result.calculation_method == "provider_reported_settled_rate"
    public = observation_from_derivative(result)
    assert public.payload_content_hash == result.content_hash
    assert public.event_time == NOW
    assert public.identity == result.identity


@pytest.mark.parametrize("venue", VENUES)
@pytest.mark.parametrize("metric", METRICS)
@pytest.mark.parametrize("value", [None, "", "NaN", "Infinity", True, 1.5])
def test_invalid_values_are_incomplete_without_a_zero_default(venue, metric, value):
    data = payload(venue, metric)
    row = (
        data
        if venue is VenueId.BINANCE and metric is METRICS[0]
        else (data[0] if venue is VenueId.BINANCE else data["result"]["list"][0])
    )
    key = "openInterest" if metric is METRICS[0] else "fundingRate"
    if value is None:
        row.pop(key)
    else:
        row[key] = value
    result = fetch(venue, metric, source(venue, data))
    assert result.availability is State.INCOMPLETE
    assert result.value is None
    assert result.reason == "malformed_provider_record"


@pytest.mark.parametrize("venue", VENUES)
@pytest.mark.parametrize("metric", METRICS)
def test_wrong_symbol_is_incomplete(venue, metric):
    result = read_market_intelligence(
        source(venue, payload(venue, metric, symbol="ETHUSDT")),
        identity=identity(venue),
        instrument=instrument(venue),
        observed_at=NOW,
        metrics=(metric,),
    )[0]
    assert result.availability is State.INCOMPLETE
    assert result.value is None


@pytest.mark.parametrize("venue", VENUES)
@pytest.mark.parametrize("metric", METRICS)
@pytest.mark.parametrize("stamp", [0, True, 1.5, "", "broken", 10**100])
def test_invalid_event_time_is_never_replaced_by_receive_time(venue, metric, stamp):
    result = fetch(venue, metric, source(venue, payload(venue, metric, stamp=stamp)))
    assert result.availability is State.INCOMPLETE
    assert result.event_time is None
    assert result.value is None


@pytest.mark.parametrize("venue", VENUES)
@pytest.mark.parametrize("metric", METRICS)
def test_freshness_boundary_and_future_clock(venue, metric):
    max_age = derivative_freshness_policy(metric, venue).trade_max_age_seconds
    for seconds, expected in [
        (max_age, State.AVAILABLE),
        (max_age + 1, State.STALE),
        (-3, State.INCOMPLETE),
    ]:
        stamp = MILLIS - seconds * 1000
        result = fetch(venue, metric, source(venue, payload(venue, metric, stamp=stamp)))
        assert result.availability is expected
        if expected is State.STALE:
            assert result.value is not None
            with pytest.raises(ValueError):
                observation_from_derivative(result)
        if expected is State.INCOMPLETE:
            assert result.reason == "future_event_time"
            assert result.value is None


@pytest.mark.parametrize("venue", VENUES)
@pytest.mark.parametrize("metric", METRICS)
def test_zero_is_valid_but_negative_oi_is_not(venue, metric):
    assert fetch(venue, metric, source(venue, payload(venue, metric, value="0"))).value == 0
    result = fetch(venue, METRICS[0], source(venue, payload(venue, METRICS[0], value="-1")))
    assert result.availability is State.INCOMPLETE


@pytest.mark.parametrize("venue", VENUES)
def test_empty_history_is_missing(venue):
    data = (
        []
        if venue is VenueId.BINANCE
        else {"retCode": 0, "result": {"category": "linear", "list": []}}
    )
    result = fetch(venue, METRICS[1], source(venue, data))
    assert result.availability is State.MISSING
    assert result.event_time is None and result.value is None


@pytest.mark.parametrize("venue", VENUES)
def test_wrong_perpetual_contract_is_unsupported_before_metric_read(venue):
    data = contract_payload(venue)
    row = data["symbols"][0] if venue is VenueId.BINANCE else data["result"]["list"][0]
    row["contractType"] = "CURRENT_QUARTER" if venue is VenueId.BINANCE else "LinearFutures"
    requests = []
    result = fetch(venue, METRICS[0], source(venue, contract=data, requests=requests))
    assert result.availability is State.UNSUPPORTED
    assert len(requests) == 1


@pytest.mark.parametrize("venue", VENUES)
@pytest.mark.parametrize("status", [403, 418, 429, 451, 503])
def test_provider_failure_is_explicit_and_optional_reads_continue(venue, status):
    observations = read_market_intelligence(
        source(venue, status=status),
        identity=identity(venue),
        instrument=instrument(venue),
        observed_at=NOW,
    )
    assert all(item.availability is State.MISSING for item in observations)
    assert all(
        item.value is None and item.reason.startswith("provider_failure:") for item in observations
    )
    with pytest.raises(MarketContractError, match="required_open_interest:MISSING"):
        require_derivative_observations(
            observations, required_metrics=METRICS, identity=identity(venue), evaluated_at=NOW
        )


@pytest.mark.parametrize("metric", METRICS)
def test_failover_requires_whole_identity_switch(metric):
    secondary_requests = []
    failover = FailoverPerpetualSource(
        source(VENUES[0], status=451),
        source(VENUES[1], requests=secondary_requests),
        primary_instrument=instrument(VENUES[0]),
        secondary_instrument=instrument(VENUES[1]),
    )
    with pytest.raises(EvidenceSourceSwitchRequiredError):
        fetch(VENUES[0], metric, failover)
    assert not secondary_requests
    with pytest.raises(EvidenceSourceSwitchRequiredError):
        fetch(VENUES[0], metric, failover)
    result = fetch(VENUES[1], metric, failover)
    assert result.availability is State.AVAILABLE
    assert result.identity.venue is VenueId.BYBIT
    assert result.identity.provenance.provider_name == "bybit-usdt-perpetual"


def test_secondary_failure_remains_missing():
    src = FailoverPerpetualSource(
        source(VENUES[0], status=451),
        source(VENUES[1], status=503),
        primary_instrument=instrument(VENUES[0]),
        secondary_instrument=instrument(VENUES[1]),
    )
    with pytest.raises(EvidenceSourceSwitchRequiredError):
        read_market_intelligence(
            src, identity=identity(VENUES[0]), instrument=instrument(VENUES[0]), observed_at=NOW
        )
    result = read_market_intelligence(
        src, identity=identity(VENUES[1]), instrument=instrument(VENUES[1]), observed_at=NOW
    )
    assert all(item.availability is State.MISSING for item in result)


@pytest.mark.parametrize("venue", VENUES)
def test_required_facts_revalidate_age_identity_and_hash(venue):
    items = read_market_intelligence(
        source(venue), identity=identity(venue), instrument=instrument(venue), observed_at=NOW
    )
    require_derivative_observations(
        items, required_metrics=METRICS, identity=identity(venue), evaluated_at=NOW
    )
    with pytest.raises(StaleEvidenceError):
        require_derivative_observations(
            items,
            required_metrics=(METRICS[0],),
            identity=identity(venue),
            evaluated_at=NOW + timedelta(seconds=601),
        )
    with pytest.raises(WrongSourceError):
        require_derivative_observations(
            items,
            required_metrics=METRICS,
            identity=identity(VENUES[1] if venue is VENUES[0] else VENUES[0]),
            evaluated_at=NOW,
        )
    corrupted = items[0].model_copy(update={"value": Decimal("999")})
    with pytest.raises(WrongSourceError):
        require_derivative_observations(
            (corrupted,), required_metrics=(METRICS[0],), identity=identity(venue), evaluated_at=NOW
        )


def test_replay_read_labels_oi_funding_unsupported_without_fabrication():
    read = CanonicalEvidenceService(Settings(perpetual_evidence_source="replay")).read(
        organization_id=ORG_ID
    )
    assert len(read.market_intelligence) == 2
    assert all(
        item.availability is State.UNSUPPORTED and item.value is None
        for item in read.market_intelligence
    )
    assert read.live_executable is False and read.watcher_activated is False


def test_required_replay_input_blocks_assembly_and_optional_inputs_do_not_change_hash():
    policy = first_slice_read_policy(ORG_ID)
    assembler = FirstSliceEvidenceAssembler(ReplayPerpetualSource(), replay=True)
    before = assembler.assemble(organization_id=ORG_ID, policy=policy)
    optional = policy.model_copy(update={"optional_roles": (EvidenceRole.OPEN_INTEREST,)})
    assert (
        assembler.assemble(organization_id=ORG_ID, policy=optional).evidence_window_hash
        == before.evidence_window_hash
    )
    required = policy.model_copy(
        update={"required_roles": (*policy.required_roles, EvidenceRole.OPEN_INTEREST)}
    )
    with pytest.raises(MarketContractError, match="required_open_interest:UNSUPPORTED"):
        assembler.assemble(organization_id=ORG_ID, policy=required)


def test_evaluator_cannot_confirm_required_unbound_payload_or_reuse_expired_fact():
    assembled = FirstSliceEvidenceAssembler(ReplayPerpetualSource(), replay=True).assemble(
        organization_id=ORG_ID
    )
    policy = first_slice_read_policy(ORG_ID)
    policy = policy.model_copy(
        update={"required_roles": (*policy.required_roles, EvidenceRole.FUNDING)}
    )
    now = assembled.evaluated_at
    fact = derivative_observation(
        identity=assembled.identity,
        metric=METRICS[1],
        observed_at=now,
        row={"fundingRate": "0.0001", "fundingTime": int(now.timestamp() * 1000)},
        value_key="fundingRate",
        time_key="fundingTime",
    )
    public = observation_from_derivative(fact)
    command = assembled.assessment_command.model_copy(
        update={
            "mandatory_evidence_roles": policy.required_roles,
            "public_observations": (*assembled.assessment_command.public_observations, public),
            "selected_roles": (*assembled.assessment_command.selected_roles, EvidenceRole.FUNDING),
        }
    )
    evidence_window_from_assessment_command(command)
    for bundle, at in [
        (assembled.bundle, now),
        (
            assembled.bundle.model_copy(update={"market_intelligence": (fact,)}),
            now + timedelta(days=2),
        ),
        (
            assembled.bundle.model_copy(
                update={
                    "market_intelligence": (
                        hash_derivative_observation(
                            fact.model_copy(update={"value": Decimal("0.5")})
                        ),
                    )
                }
            ),
            now,
        ),
    ]:
        assessment = evaluate_setup(
            policy=policy, command=command, evidence=bundle, evaluated_at=at
        )
        assert assessment.state is not SetupAssessmentState.CONFIRMED_SETUP
        result = next(r for r in assessment.rule_results if r.rule_id == "complete_warmup")
        assert result.passed is False


@pytest.mark.parametrize("venue", VENUES)
def test_read_only_client_still_refuses_orders_and_mutations(venue):
    src = source(venue)
    with pytest.raises(NetworkMutationForbiddenError):
        src._http.request_json("POST", "/fapi/v1/order")
    with pytest.raises(NetworkMutationForbiddenError):
        src._http.get_json("/v5/order/create")
    with pytest.raises((WrongMarketError, WrongInstrumentError)):
        fetch(VENUES[1] if venue is VENUES[0] else VENUES[0], METRICS[0], src)
    assert isinstance(src._http, ReadOnlyHttpGetClient)


def test_required_live_inputs_bind_into_existing_evidence_window():
    from tests.support.phase5_market import EVALUATED_AT
    from tests.test_live_evidence_pipeline import _fixture_usdm_handler

    transport = _fixture_usdm_handler()
    at = int(EVALUATED_AT.timestamp() * 1000)

    def handle(request):
        path = request.url.path
        if path.endswith("exchangeInfo"):
            return httpx.Response(200, json=contract_payload(VENUES[0]))
        if path.endswith("openInterest"):
            return httpx.Response(200, json=payload(VENUES[0], METRICS[0], stamp=at))
        if path.endswith("fundingRate"):
            return httpx.Response(200, json=payload(VENUES[0], METRICS[1], stamp=at))
        return transport.handle_request(request)

    src = BinanceUsdmPerpetualSource(transport=httpx.MockTransport(handle), max_retries=0)
    policy = first_slice_read_policy(ORG_ID)
    policy = policy.model_copy(
        update={
            "required_roles": (
                *policy.required_roles,
                EvidenceRole.OPEN_INTEREST,
                EvidenceRole.FUNDING,
            )
        }
    )
    assembled = FirstSliceEvidenceAssembler(src, replay=False).assemble(
        organization_id=ORG_ID, evaluated_at=EVALUATED_AT, policy=policy
    )
    assert len(assembled.bundle.market_intelligence) == 2
    assert {o.payload_content_hash for o in assembled.assessment_command.public_observations} >= {
        fact.content_hash for fact in assembled.bundle.market_intelligence
    }
    assert (
        evidence_window_from_assessment_command(assembled.assessment_command).content_hash
        == assembled.evidence_window_hash
    )
    assessment = evaluate_setup(
        policy=policy,
        command=assembled.assessment_command,
        evidence=assembled.bundle,
        evaluated_at=EVALUATED_AT,
    )
    assert next(r for r in assessment.rule_results if r.rule_id == "complete_warmup").passed
    read = CanonicalEvidenceService(
        Settings(perpetual_evidence_source="binance_usdm"), source=src, clock=lambda: EVALUATED_AT
    ).read(organization_id=ORG_ID)
    assert read.setup_evidence.available
    assert read.current_price.usable_as_current_market_price
    assert all(item.availability is State.AVAILABLE for item in read.market_intelligence)
    assert read.source.venue == "binance"


def test_canonical_read_discards_all_primary_facts_when_funding_switches_venue(monkeypatch):
    from app.evidence_pipeline.http_schemas import (
        CanonicalCompletenessRead,
        CanonicalSetupEvidenceRead,
    )
    from app.evidence_pipeline.service import _unavailable_price
    from app.evidence_pipeline.types import CurrentPricePresentation

    def primary_handle(request):
        if request.url.path.endswith("exchangeInfo"):
            return httpx.Response(200, json=contract_payload(VENUES[0]))
        if request.url.path.endswith("openInterest"):
            return httpx.Response(200, json=payload(VENUES[0], METRICS[0], value="999"))
        return httpx.Response(451)

    src = FailoverPerpetualSource(
        BinanceUsdmPerpetualSource(transport=httpx.MockTransport(primary_handle), max_retries=0),
        source(VENUES[1]),
        primary_instrument=instrument(VENUES[0]),
        secondary_instrument=instrument(VENUES[1]),
    )
    service = CanonicalEvidenceService(
        Settings(perpetual_evidence_source="binance_usdm"), source=src, clock=lambda: NOW
    )
    quote_venues, setup_venues = [], []

    def quote(**kwargs):
        quote_venues.append(src.active_instrument().venue)
        return _unavailable_price(
            identity_is_live=True, presentation=CurrentPricePresentation.UNAVAILABLE
        ), "unavailable"

    def setup(**kwargs):
        setup_venues.append(src.active_instrument().venue)
        return CanonicalSetupEvidenceRead(
            available=False,
            completeness=CanonicalCompletenessRead(
                ohlcv_15m="unknown", ohlcv_4h="unknown", cvd="unknown", signed_flow="unknown"
            ),
        ), "unavailable"

    monkeypatch.setattr(service, "_quote", quote)
    monkeypatch.setattr(service, "_setup", setup)
    result = service.read(organization_id=ORG_ID)
    assert quote_venues == setup_venues == list(VENUES)
    assert result.source.venue == "bybit"
    assert result.source.provider_name == "bybit-usdt-perpetual"
    assert all(item.identity.venue is VenueId.BYBIT for item in result.market_intelligence)
    assert result.market_intelligence[0].value == Decimal("123.45")


@pytest.mark.parametrize("state", [State.MISSING, State.STALE, State.UNSUPPORTED, State.INCOMPLETE])
def test_every_unavailable_required_state_blocks(state):
    fact = fetch(VENUES[0], METRICS[0])
    changed = fact.model_copy(
        update={"availability": state, "value": fact.value if state is State.STALE else None}
    )
    changed = hash_derivative_observation(changed)
    with pytest.raises(MarketContractError, match=state.value):
        require_derivative_observations(
            (changed,),
            required_metrics=(METRICS[0],),
            identity=identity(VENUES[0]),
            evaluated_at=NOW,
        )


@pytest.mark.parametrize("code", [10006, 10001, 10029])
def test_bybit_retcode_failures_never_publish_values(code):
    result = read_market_intelligence(
        source(VENUES[1], {"retCode": code, "result": {}}),
        identity=identity(VENUES[1]),
        instrument=instrument(VENUES[1]),
        observed_at=NOW,
    )
    assert all(item.availability is State.MISSING and item.value is None for item in result)


@pytest.mark.parametrize("venue", VENUES)
def test_malformed_json_is_incomplete(venue):
    def handle(request):
        if request.url.path.endswith(("exchangeInfo", "instruments-info")):
            return httpx.Response(200, json=contract_payload(venue))
        return httpx.Response(200, content=b"broken-json")

    cls = BybitUsdtPerpetualSource if venue is VenueId.BYBIT else BinanceUsdmPerpetualSource
    result = read_market_intelligence(
        cls(transport=httpx.MockTransport(handle), max_retries=0),
        identity=identity(venue),
        instrument=instrument(venue),
        observed_at=NOW,
    )
    assert all(item.availability is State.INCOMPLETE and item.value is None for item in result)


@pytest.mark.parametrize("venue", VENUES)
@pytest.mark.parametrize("metric", METRICS)
def test_future_event_is_rejected_even_with_small_clock_skew(venue, metric):
    for milliseconds in (1, 2000, 2001):
        result = fetch(
            venue, metric, source(venue, payload(venue, metric, stamp=MILLIS + milliseconds))
        )
        assert result.availability is State.INCOMPLETE
        assert result.value is None
        assert result.reason == "future_event_time"
        with pytest.raises(MarketContractError):
            require_derivative_observations(
                (result,),
                required_metrics=(metric,),
                identity=identity(venue),
                evaluated_at=NOW,
            )


@pytest.mark.parametrize("venue", VENUES)
@pytest.mark.parametrize("broken", ["missing_list", "bad_list", "bad_row", "missing_field"])
def test_malformed_contract_book_is_incomplete_instead_of_unsupported(venue, broken):
    book = contract_payload(venue)
    container = book if venue is VenueId.BINANCE else book["result"]
    key = "symbols" if venue is VenueId.BINANCE else "list"
    if broken == "missing_list":
        container.pop(key)
    elif broken == "bad_list":
        container[key] = {}
    elif broken == "bad_row":
        container[key] = [123]
    else:
        container[key][0].pop("contractType")
    result = read_market_intelligence(
        source(venue, contract=book),
        identity=identity(venue),
        instrument=instrument(venue),
        observed_at=NOW,
    )
    assert all(item.availability is State.INCOMPLETE and item.value is None for item in result)


@pytest.mark.parametrize("venue", VENUES)
def test_verified_contract_book_omission_is_unsupported(venue):
    book = contract_payload(venue, symbol="ETHUSDT")
    result = read_market_intelligence(
        source(venue, contract=book),
        identity=identity(venue),
        instrument=instrument(venue),
        observed_at=NOW,
    )
    assert all(item.availability is State.UNSUPPORTED and item.value is None for item in result)


@pytest.mark.parametrize("venue", VENUES)
@pytest.mark.parametrize(
    "error_type", [httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError, httpx.ProxyError]
)
def test_transport_failures_are_explicit_missing(venue, error_type):
    def handle(request):
        if request.url.path.endswith(("exchangeInfo", "instruments-info")):
            return httpx.Response(200, json=contract_payload(venue))
        raise error_type("scripted provider transport failure", request=request)

    cls = BybitUsdtPerpetualSource if venue is VenueId.BYBIT else BinanceUsdmPerpetualSource
    src = cls(transport=httpx.MockTransport(handle), max_retries=0)
    result = read_market_intelligence(
        src, identity=identity(venue), instrument=instrument(venue), observed_at=NOW
    )
    assert all(item.availability is State.MISSING and item.value is None for item in result)
    assert all(item.reason == "provider_failure:RegionalProviderFailureError" for item in result)


@pytest.mark.parametrize("venue", VENUES)
@pytest.mark.parametrize("metric", METRICS)
def test_same_provider_fact_converges_across_observation_clocks(venue, metric):
    src = source(venue)
    first = fetch(venue, metric, src)
    second = src.fetch_derivative_observation(
        identity=identity(venue),
        instrument=instrument(venue),
        metric=metric,
        observed_at=NOW + timedelta(seconds=1),
    )
    assert first.observed_at != second.observed_at
    assert first.freshness.age_seconds != second.freshness.age_seconds
    assert first.content_hash == second.content_hash
    assert (
        observation_from_derivative(first).content_hash
        == observation_from_derivative(second).content_hash
    )
    modified = hash_derivative_observation(
        second.model_copy(update={"freshness_policy_version": "different-policy/v1"})
    )
    assert modified.content_hash != second.content_hash
    with pytest.raises(WrongSourceError, match="wrong_freshness_policy"):
        require_derivative_observations(
            (modified,),
            required_metrics=(metric,),
            identity=identity(venue),
            evaluated_at=second.observed_at,
        )
