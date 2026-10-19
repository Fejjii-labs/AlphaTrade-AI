"""Receipt-time quote freshness and final expiry; all venue IO is simulated."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import httpx
import pytest

from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.governed_blofin import GovernedBloFinDemoProvider
from app.schemas.trade_plan import EntrySide, TradePlanRevision
from tests.support.phase5_market import EVALUATED_AT
from tests.support.phase7_trade_plan import make_world, plan_command, plan_terms
from tests.test_governed_blofin_demo import Venue


@dataclass
class AdvancingClock:
    current: datetime = EVALUATED_AT
    ticker_received: bool = False
    after_receipt_step_ms: int = 0

    def now(self) -> datetime:
        observed = self.current
        if self.ticker_received:
            self.current += timedelta(milliseconds=self.after_receipt_step_ms)
        return observed


def _provider(
    *, quote_age_ms: int = 250, after_receipt_step_ms: int = 0
) -> tuple[GovernedBloFinDemoProvider, Venue, AdvancingClock]:
    clock = AdvancingClock(after_receipt_step_ms=after_receipt_step_ms)
    venue = Venue(Decimal("100000"))

    def handle(request: httpx.Request) -> httpx.Response:
        clock.current += timedelta(milliseconds=500)
        response = venue.handle(request)
        if request.url.path.endswith("/books"):
            payload = response.json()
            quote_time = clock.current - timedelta(milliseconds=quote_age_ms)
            payload["data"][0]["ts"] = str(int(quote_time.timestamp() * 1000))
            response = httpx.Response(200, json=payload)
            clock.ticker_received = True
        return response

    client = BloFinClient(
        base_url="https://demo-trading-openapi.blofin.com",
        api_key="simulated-key",
        api_secret="simulated-secret",
        api_passphrase="simulated-pass",
        transport=httpx.MockTransport(handle),
        sleeper=lambda _: None,
    )
    return GovernedBloFinDemoProvider(client, clock=clock.now), venue, clock


def _plan(*, valid_for_ms: int) -> TradePlanRevision:
    world = make_world()
    terms = plan_terms(
        world.candidate, valid_until=EVALUATED_AT + timedelta(milliseconds=valid_for_ms)
    )
    terms = terms.model_copy(
        update={
            "instrument_rules": terms.instrument_rules.model_copy(
                update={"contract_multiplier": Decimal("0.001")}
            )
        }
    )
    return world.plans.create(plan_command(world, terms=terms)).plan


def test_quote_generated_during_preflight_is_fresh_when_received() -> None:
    provider, venue, clock = _provider()
    started = clock.now()
    snapshot = provider.snapshot(symbol="BTCUSDT", now=started, side=EntrySide.BUY)
    assert clock.current - started == timedelta(milliseconds=5500)
    assert snapshot.observed_at > started
    assert clock.current - snapshot.observed_at == timedelta(milliseconds=250)
    assert snapshot.price == venue.price + Decimal("0.1")
    assert venue.post_count == 0


@pytest.mark.parametrize("quote_age_ms", [0, 9999])
def test_receipt_time_preserves_valid_freshness_boundaries(quote_age_ms: int) -> None:
    provider, venue, _clock = _provider(quote_age_ms=quote_age_ms)
    assert provider.snapshot(
        symbol="BTCUSDT", now=EVALUATED_AT, side=EntrySide.BUY
    ).price == venue.price + Decimal("0.1")
    assert venue.post_count == 0


@pytest.mark.parametrize("quote_age_ms", [10000, 10001, 20000, -1, -1000])
def test_genuinely_stale_and_future_quotes_are_refused(quote_age_ms: int) -> None:
    provider, venue, _clock = _provider(quote_age_ms=quote_age_ms)
    with pytest.raises(ValueError, match="Demo quote stale or future dated"):
        provider.snapshot(symbol="BTCUSDT", now=EVALUATED_AT, side=EntrySide.BUY)
    assert venue.post_count == 0


@pytest.mark.parametrize("valid_for_ms,after_receipt_step_ms", [(4500, 0), (4499, 0), (5000, 500)])
def test_fresh_quote_does_not_bypass_final_dispatch_expiry(
    valid_for_ms: int, after_receipt_step_ms: int
) -> None:
    provider, venue, _clock = _provider(quote_age_ms=0, after_receipt_step_ms=after_receipt_step_ms)
    before_post = Mock()
    with pytest.raises(ValueError, match="Demo plan expired during dispatch preflight"):
        provider.submit(
            plan=_plan(valid_for_ms=valid_for_ms),
            client_order_id="simulated-expired-entry",
            before_post=before_post,
        )
    before_post.assert_not_called()
    assert venue.post_count == 0


def test_fresh_preflight_preserves_guard_and_attached_protection() -> None:
    provider, venue, _clock = _provider()
    before_post = Mock()
    plan = _plan(valid_for_ms=6000)
    assert (
        provider.submit(plan=plan, client_order_id="simulated-fresh-entry", before_post=before_post)
        == "demo-1"
    )
    before_post.assert_called_once_with()
    assert venue.post_count == 1
    assert venue.order is not None
    assert venue.order["clientOrderId"] == "simulated-fresh-entry"
    assert venue.order["slTriggerPrice"] == str(plan.risk_and_exits.stop.value)
    assert venue.order["tpTriggerPrice"] == str(plan.risk_and_exits.targets[0].price.value)
    assert venue.order["slOrderPrice"] == venue.order["tpOrderPrice"] == "-1"


def test_quote_expiring_during_final_safety_callback_never_posts() -> None:
    provider, venue, clock = _provider(quote_age_ms=9999)

    def before_post() -> None:
        clock.current += timedelta(milliseconds=1)

    with pytest.raises(ValueError, match="Demo quote stale or future dated"):
        provider.submit(
            plan=_plan(valid_for_ms=20000),
            client_order_id="simulated-quote-expired-in-guard",
            before_post=before_post,
        )
    assert venue.post_count == 0


def test_quote_expiring_in_transport_throttle_never_posts() -> None:
    provider, venue, clock = _provider(quote_age_ms=9999)

    def before_post() -> None:
        def delayed_throttle() -> None:
            clock.current += timedelta(milliseconds=1)

        provider._client._throttle = delayed_throttle

    with pytest.raises(ValueError, match="Demo quote stale or future dated"):
        provider.submit(
            plan=_plan(valid_for_ms=20000),
            client_order_id="simulated-quote-expired-in-throttle",
            before_post=before_post,
        )
    assert venue.post_count == 0
