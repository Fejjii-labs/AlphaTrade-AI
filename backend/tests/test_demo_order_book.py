"""Executable quote contracts with simulated venue IO; no live acceptance claims."""

from copy import deepcopy
from decimal import Decimal
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest

from app.core.errors import TradingPolicyError
from app.providers.exchange.demo_order_book import parse_demo_book
from app.providers.exchange.demo_preflight import DemoPreflightError
from app.schemas.manual_demo import ManualDemoPreviewRequest
from app.schemas.trade_plan import EntrySide
from app.services.demo_leverage_config import DemoLeverageConfig
from app.services.manual_demo_plan import build_manual_plan
from app.services.planned_reward_risk import PlannedRewardRiskError, execution_reward_risk
from tests.support.phase5_market import EVALUATED_AT
from tests.test_demo_preflight_diagnostics import provider, service
from tests.test_governed_blofin_demo import Venue
from tests.test_governed_blofin_quote_freshness import _plan

STAMP = str(int(EVALUATED_AT.timestamp() * 1000))
BOOK = [
    {
        "ts": STAMP,
        "asks": [["100000", "1"], ["100010", "2"]],
        "bids": [["99999.9", "2"], ["99990", "1"]],
    }
]


def parsed(data=BOOK, side=EntrySide.BUY):
    return parse_demo_book(
        data,
        instrument="BTC-USDT",
        side=side,
        tick=Decimal("0.1"),
        lot=Decimal("1"),
        received_at=EVALUATED_AT,
        request_duration_ms=25,
    )


@pytest.mark.parametrize(
    "side,price,worst", [(EntrySide.BUY, "100000", "100010"), (EntrySide.SELL, "99999.9", "99990")]
)
def test_side_uses_executable_prices_and_sums_contract_depth(side, price, worst):
    book = parsed(side=side)
    assert book.price == Decimal(price)
    assert book.worst_price(
        Decimal("3"), lot=Decimal("1"), minimum=Decimal("1"), maximum=Decimal("5")
    ) == Decimal(worst)
    assert book.observed_at == EVALUATED_AT


@pytest.mark.parametrize("quantity", ["0", "-1", "NaN", "Infinity", "1e99999", "0.5", "6"])
def test_quantity_is_exact_contract_lots_not_base_coins(quantity):
    with pytest.raises(ValueError):
        parsed().worst_price(
            Decimal(quantity), lot=Decimal("1"), minimum=Decimal("1"), maximum=Decimal("5")
        )


@pytest.mark.parametrize(
    "data,reason",
    [
        (None, "quote_unavailable"),
        ([], "quote_unavailable"),
        ([{}, {}], "quote_unavailable"),
        ([{**BOOK[0], "instId": "ETH-USDT"}], "quote_instrument_mismatch"),
        ([{**BOOK[0], "asks": []}], "quote_depth_unavailable"),
        ([{**BOOK[0], "bids": []}], "quote_depth_unavailable"),
        ([{**BOOK[0], "asks": [["100000", "0"]]}], "invalid_instrument_or_quote_value"),
        ([{**BOOK[0], "asks": [["100000", "1e999999"]]}], "invalid_instrument_or_quote_value"),
        ([{**BOOK[0], "asks": [["100000", "NaN"]]}], "invalid_instrument_or_quote_value"),
        ([{**BOOK[0], "asks": [["100000"]]}], "quote_depth_malformed"),
        ([{**BOOK[0], "asks": [["100000.01", "1"]]}], "quote_depth_invalid_increments"),
        ([{**BOOK[0], "asks": [["100000", "0.5"]]}], "quote_depth_invalid_increments"),
        ([{**BOOK[0], "asks": [["100000", "1"], ["99999", "1"]]}], "quote_depth_unordered"),
        ([{**BOOK[0], "bids": [["99999.9", "1"], ["99999.9", "1"]]}], "quote_depth_unordered"),
        ([{**BOOK[0], "bids": [["100000", "1"]]}], "quote_spread_invalid"),
        ([{**BOOK[0], "bids": [["100001", "1"]]}], "quote_spread_invalid"),
        ([{**BOOK[0], "bids": [["99000", "1"]]}], "quote_spread_excessive"),
    ],
)
def test_invalid_book_refuses_preview_with_actionable_safe_error(data, reason):
    venue = Venue(Decimal("100000"))
    reads = []

    def handle(request):
        assert request.method == "GET"
        reads.append(request.url.path)
        if request.url.path.endswith("/books"):
            assert dict(request.url.params) == {"instId": "BTC-USDT", "size": "100"}
            return httpx.Response(200, json={"code": "0", "data": data})
        return venue.handle(request)

    manual, session, tenant = service(provider(handle))
    with pytest.raises(TradingPolicyError) as caught:
        manual.preview(
            tenant,
            ManualDemoPreviewRequest(side="BUY", quantity="2", stop="99000", target="102000"),
        )
    assert caught.value.details["preflight"]["reason_code"] == reason
    assert "BloFin demo" in str(caught.value)
    assert "preflight unavailable" not in str(caught.value)
    assert not any(path.endswith("/tickers") for path in reads)
    session.add.assert_not_called()
    assert venue.post_count == 0


def test_missing_side_is_not_guessed():
    with pytest.raises(ValueError, match="side"):
        parsed(side="BUY")


@pytest.mark.parametrize(
    "mutation", ["none", "legacy", "bad_marker", "duplicate", "strategy", "geometry"]
)
def test_only_explicit_manual_connectivity_policy_can_omit_one_r(mutation):
    snapshot = provider(Venue(Decimal("100000")).handle).snapshot(
        symbol="BTCUSDT", now=EVALUATED_AT, side=EntrySide.BUY, quantity=Decimal("2")
    )
    plan = build_manual_plan(
        ManualDemoPreviewRequest(side="BUY", quantity="2", stop="99000", target="100500"),
        snapshot,
        organization_id=uuid4(),
        user_id=uuid4(),
        account_id=uuid4(),
        now=EVALUATED_AT,
    )
    marker = next(item for item in plan.calculation_inputs if item.name == "manual_connectivity_rr")
    others = tuple(item for item in plan.calculation_inputs if item is not marker)
    if mutation == "legacy":
        plan = plan.model_copy(update={"calculation_inputs": others})
    elif mutation == "bad_marker":
        plan = plan.model_copy(
            update={
                "calculation_inputs": (*others, marker.model_copy(update={"formula_id": "other"}))
            }
        )
    elif mutation == "duplicate":
        plan = plan.model_copy(update={"calculation_inputs": (*plan.calculation_inputs, marker)})
    elif mutation == "strategy":
        plan = plan.model_copy(update={"schema_version": "CanonicalTradePlanContentV1"})
    elif mutation == "geometry":
        plan = plan.model_copy(
            update={
                "risk_and_exits": plan.risk_and_exits.model_copy(
                    update={
                        "stop": plan.risk_and_exits.stop.model_copy(
                            update={"value": Decimal("100010")}
                        )
                    }
                )
            }
        )
    if mutation == "none":
        assert 0 < execution_reward_risk(plan).ratio < 1
    else:
        with pytest.raises(PlannedRewardRiskError):
            execution_reward_risk(plan)


@pytest.mark.parametrize("side", [EntrySide.BUY, EntrySide.SELL])
def test_depth_outside_slippage_bound_is_not_executable(side):
    data = deepcopy(BOOK)
    data[0]["asks"][1][0] = "100100.1"
    data[0]["bids"] = [["99999.9", "1"], ["99899.9", "1000000"]]
    with pytest.raises(ValueError, match="insufficient depth"):
        parsed(data, side).worst_price(
            Decimal("3"), lot=Decimal("1"), minimum=Decimal("1"), maximum=Decimal("1000000")
        )


@pytest.mark.parametrize("side", [EntrySide.BUY, EntrySide.SELL])
def test_valid_manual_plan_has_fresh_demo_book_and_base_quantity_conversion(side):
    venue = Venue(Decimal("100000"))
    snapshot = provider(venue.handle).snapshot(
        symbol="BTCUSDT", now=EVALUATED_AT, side=side, quantity=Decimal("2")
    )
    request = ManualDemoPreviewRequest(
        side=side,
        quantity="2",
        stop="99000" if side is EntrySide.BUY else "101000",
        target="102000" if side is EntrySide.BUY else "98000",
    )
    plan = build_manual_plan(
        request,
        snapshot,
        organization_id=uuid4(),
        user_id=uuid4(),
        account_id=uuid4(),
        now=EVALUATED_AT,
    )
    assert plan.quantity.value == 2 and plan.quantity.unit == "CONTRACTS"
    assert plan.quantity.value * plan.instrument_rules.contract_multiplier == Decimal("0.002")
    assert plan.evidence_venue == "BLOFIN_DEMO"
    assert plan.evidence_observed_at == snapshot.book.observed_at
    assert plan.evidence_freshness_seconds == 10
    assert not plan.evidence_fallback_used
    assert venue.post_count == 0


@pytest.mark.parametrize(
    "mutation", ["stale", "future", "malformed", "empty", "insufficient", "spread", "quantity"]
)
def test_confirmation_reacquires_quote_and_refuses_before_submit(mutation):
    venue = Venue(Decimal("100000"))
    changed = False

    def handle(request):
        if request.url.path.endswith("/books") and changed:
            data = deepcopy(BOOK)
            if mutation == "stale":
                data[0]["ts"] = str(int(STAMP) - 10000)
            elif mutation == "future":
                data[0]["ts"] = str(int(STAMP) + 1)
            elif mutation == "malformed":
                data[0]["ts"] = "not-a-timestamp"
            elif mutation == "empty":
                data[0]["asks"] = []
            elif mutation == "insufficient":
                data[0]["asks"] = [["100000", "1"]]
                data[0]["bids"] = [["99999.9", "1"]]
            elif mutation == "spread":
                data[0]["bids"] = [["99000", "1"]]
            else:
                # Lot changes make the previously authorized exact size invalid.
                pass
            return httpx.Response(200, json={"code": "0", "data": data})
        response = venue.handle(request)
        if changed and mutation == "quantity" and request.url.path.endswith("/instruments"):
            payload = response.json()
            payload["data"][0]["lotSize"] = "3"
            return httpx.Response(200, json=payload)
        return response

    quotes = provider(handle)
    plan = _plan(valid_for_ms=6000)
    plan = plan.model_copy(
        update={"quantity": plan.quantity.model_copy(update={"value": Decimal("2")})}
    )
    quotes.snapshot(
        symbol="BTCUSDT", now=EVALUATED_AT, side=plan.side, quantity=plan.quantity.value
    )
    changed = True
    before_post = Mock()
    with pytest.raises((DemoPreflightError, ValueError)):
        quotes.submit(plan=plan, client_order_id="never-submit-invalid", before_post=before_post)
    before_post.assert_not_called()
    assert venue.post_count == 0


def test_order_book_outage_retries_only_bounded_safe_reads():
    from app.providers.exchange.blofin_client import BloFinClient
    from app.providers.exchange.governed_blofin import GovernedBloFinDemoProvider

    venue = Venue(Decimal("100000"))
    books = []

    def handle(request):
        assert request.method == "GET"
        if request.url.path.endswith("/books"):
            books.append(request.url.path)
            return httpx.Response(503)
        return venue.handle(request)

    quotes = GovernedBloFinDemoProvider(
        BloFinClient(
            base_url="https://demo-trading-openapi.blofin.com",
            api_key="simulated-key",
            api_secret="simulated-secret",
            api_passphrase="simulated-pass",
            max_retries=2,
            transport=httpx.MockTransport(handle),
            sleeper=lambda _: None,
        ),
        clock=lambda: EVALUATED_AT,
    )
    with pytest.raises(DemoPreflightError) as caught:
        quotes.snapshot(
            symbol="BTCUSDT", now=EVALUATED_AT, side=EntrySide.BUY, quantity=Decimal("2")
        )
    assert caught.value.diagnostics["reason_code"] == "venue_unavailable"
    assert caught.value.diagnostics["endpoint_name"] == "GET /api/v1/market/books"
    assert len(books) == 3
    assert venue.post_count == 0


def test_confirmation_cannot_use_depth_outside_authorized_strategy_entry_zone():
    venue = Venue(Decimal("100000"))

    def handle(request):
        response = venue.handle(request)
        if request.url.path.endswith("/books"):
            payload = response.json()
            payload["data"][0]["bids"] = [["100000", "1"], ["99940", "1000000"]]
            return httpx.Response(200, json=payload)
        return response

    plan = _plan(valid_for_ms=6000)
    plan = plan.model_copy(
        update={"quantity": plan.quantity.model_copy(update={"value": Decimal("2")})}
    )
    before_post = Mock()
    with pytest.raises(ValueError, match="outside the authorized entry range"):
        provider(handle).submit(plan=plan, client_order_id="blocked-depth", before_post=before_post)
    before_post.assert_not_called()
    assert venue.post_count == 0


def test_manual_confirmation_conservative_size_bound_survives_balance_drop():
    venue = Venue(Decimal("100000"))
    quotes = provider(venue.handle)
    snapshot = quotes.snapshot(
        symbol="BTCUSDT", now=EVALUATED_AT, side=EntrySide.BUY, quantity=Decimal("2")
    )
    plan = build_manual_plan(
        ManualDemoPreviewRequest(side="BUY", quantity="2", stop="99000", target="102000"),
        snapshot,
        organization_id=uuid4(),
        user_id=uuid4(),
        account_id=uuid4(),
        now=EVALUATED_AT,
    )

    def handle(request):
        if request.url.path.endswith("/balance"):
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": [{"currency": "USDT", "balance": "10000", "available": "4000.004"}],
                },
            )
        return venue.handle(request)

    before_post = Mock()
    with pytest.raises(TradingPolicyError, match="Manual demo limit") as caught:
        provider(handle).submit(plan=plan, client_order_id="blocked-size", before_post=before_post)
    assert caught.value.details["reason"] == "manual_demo_notional_limit"
    before_post.assert_not_called()
    assert venue.post_count == 0


# "size" is gone: the old 10%-of-equity notional cap was replaced by the
# stop-loss band and the account's existing leverage.
@pytest.mark.parametrize("mode", ["top", "depth", "side"])
def test_governed_executable_price_below_one_r_returns_refusal(monkeypatch, mode):
    from app.services import governed_blofin_demo
    from tests.support.phase7_trade_plan import make_world, plan_terms

    world = make_world()
    terms = plan_terms(world.candidate)
    # This short is exactly 1R at 100000; a lower executable bid worsens it.
    risk = terms.risk_and_exits.model_copy(
        update={
            "stop": terms.risk_and_exits.stop.model_copy(
                update={"value": Decimal("95000") if mode == "size" else Decimal("101000")}
            ),
            "targets": (
                terms.risk_and_exits.targets[0].model_copy(
                    update={
                        "quantity_fraction": Decimal("1"),
                        "price": terms.risk_and_exits.targets[0].price.model_copy(
                            update={
                                "value": Decimal("110050") if mode == "size" else Decimal("99000")
                            }
                        ),
                    }
                ),
            ),
            "runner": terms.risk_and_exits.runner.model_copy(
                update={
                    "enabled": False,
                    "activation_target_order": None,
                    "remaining_quantity_fraction": Decimal("0"),
                }
            ),
        }
    )
    terms = terms.model_copy(
        update={"side": EntrySide.BUY if mode == "size" else EntrySide.SELL, "risk_and_exits": risk}
    )
    monkeypatch.setattr(governed_blofin_demo, "_plan_terms", lambda **kwargs: terms)
    loop = governed_blofin_demo.GovernedBloFinDemoLoop(None, None, None)
    # Owner-configured demo leverage matching the fixture venue's 1x cross read.
    loop._leverage_config = DemoLeverageConfig(1, "cross", "account")

    def evaluate():
        return loop._plan_terms(
            candidate=world.candidate,
            assembled=None,
            policy=None,
            account_id=uuid4(),
            equity=Decimal("10000"),
            now=EVALUATED_AT,
            eligibility_valid_until=terms.valid_until,
            eligibility_id=uuid4(),
        )

    control = Venue(Decimal("99999.9") if mode == "size" else Decimal("100000"))
    loop._snapshot = provider(control.handle).snapshot(
        symbol="BTCUSDT", now=EVALUATED_AT, side=terms.side
    )
    control_plan = evaluate()
    assert not isinstance(control_plan, str)
    # Stop-distance loss lands inside the default 1%-5% equity band.
    loss = control_plan.risk_and_exits.maximum_loss.value
    equity = min(Decimal("10000"), loop._snapshot.equity, loop._snapshot.available)
    assert equity * Decimal("0.01") <= loss <= equity * Decimal("0.05")
    assert (control_plan.valid_until - loop._snapshot.observed_at).total_seconds() > 3
    venue = Venue(Decimal("99999.9") if mode in {"top", "size"} else Decimal("100000"))

    def handle(request):
        response = venue.handle(request)
        if mode == "depth" and request.url.path.endswith("/books"):
            content = response.json()
            content["data"][0]["bids"] = [["100000", "1"], ["99999.9", "1000000"]]
            return httpx.Response(200, json=content)
        if mode == "size" and request.url.path.endswith("/books"):
            content = response.json()
            content["data"][0]["asks"] = [["100000", "1"], ["100010", "1000000"]]
            return httpx.Response(200, json=content)
        return response

    loop._snapshot = provider(handle).snapshot(
        symbol="BTCUSDT",
        now=EVALUATED_AT,
        side=EntrySide.BUY if mode == "side" else terms.side,
    )
    result = evaluate()
    assert result == (
        "demo_depth_exceeds_size_limit"
        if mode == "size"
        else "demo_quote_side_mismatch"
        if mode == "side"
        else "planned_reward_risk_below_minimum"
    )
    assert venue.post_count == 0
