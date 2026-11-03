"""The app sets the owner-configured leverage on BloFin DEMO before an entry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.db.models import ExecutionAccount, JournalTrade
from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.demo_preflight import failure_diagnostics
from app.providers.exchange.governed_blofin import (
    CONFIGURED_DEMO_MARGIN_ASSUMPTION,
    GovernedBloFinDemoProvider,
    demo_order_margin_mode,
)
from app.services import demo_leverage_config
from app.services.demo_leverage_config import DemoLeverageConfig, demo_liquidation_refusal
from app.services.governed_blofin_demo import GovernedBloFinDemoLoop
from tests.support.phase5_market import EVALUATED_AT
from tests.support.postgres_persistence import phase7_plan_session_factory, requires_postgres
from tests.test_automated_paper_loop import _live_source, _monitor, _runtime_for, _seed
from tests.test_governed_blofin_demo import Venue, demo_settings

DEMO_URL = "https://demo-trading-openapi.blofin.com"
TIERS = ((Decimal("0"), Decimal("0.004")),)


@dataclass
class LeverageVenue(Venue):
    """Venue fake whose account-setting endpoints can fail or lie on read-back."""

    set_leverage_status: int = 200
    set_margin_status: int = 200
    stale_readback: bool = False
    open_position: bool = False
    maintenance_rate: str | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/account/set-leverage" and self.set_leverage_status != 200:
            self.account_writes.append((path, {}))
            return httpx.Response(
                self.set_leverage_status, json={"code": "152001", "msg": "rejected"}
            )
        if path == "/api/v1/account/set-margin-mode" and self.set_margin_status != 200:
            self.account_writes.append((path, {}))
            return httpx.Response(
                self.set_margin_status, json={"code": "152002", "msg": "position open"}
            )
        if path.endswith("/positions") and self.open_position:
            return httpx.Response(
                200, json={"code": "0", "data": [{"instId": "BTC-USDT", "positions": "1"}]}
            )
        if path.endswith("position-tiers") and self.maintenance_rate is not None:
            tier = {"minSize": "0", "maintenanceMarginRate": self.maintenance_rate}
            return httpx.Response(200, json={"code": "0", "data": [tier]})
        if path.endswith("leverage-info") and self.stale_readback:
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": {"instId": "BTC-USDT", "marginMode": self.margin_mode, "leverage": "1"},
                },
            )
        return super().handle(request)


def _provider(venue: Venue) -> GovernedBloFinDemoProvider:
    client = BloFinClient(
        base_url=DEMO_URL,
        api_key="simulated-key",
        api_secret="simulated-secret",
        api_passphrase="simulated-pass",
        max_retries=0,
        transport=httpx.MockTransport(venue.handle),
        sleeper=lambda _: None,
    )
    return GovernedBloFinDemoProvider(client, clock=lambda: EVALUATED_AT)


def _reason(exc: Exception) -> str:
    return str(failure_diagnostics(exc, stage="leverage_write")["reason_code"])


def test_sets_configured_margin_mode_and_leverage_on_demo_host() -> None:
    venue = LeverageVenue(Decimal("100000"))
    _provider(venue).apply_configured_leverage(
        symbol="BTCUSDT", leverage=Decimal("10"), margin_mode="isolated"
    )
    assert venue.account_writes == [
        ("/api/v1/account/set-margin-mode", {"marginMode": "isolated"}),
        (
            "/api/v1/account/set-leverage",
            {
                "instId": "BTC-USDT",
                "leverage": "10",
                "marginMode": "isolated",
                "positionSide": "net",
            },
        ),
    ]
    assert (venue.margin_mode, venue.leverage, venue.post_count) == ("isolated", "10", 0)


def test_margin_mode_already_matching_sends_only_set_leverage() -> None:
    venue = LeverageVenue(Decimal("100000"), margin_mode="cross")
    _provider(venue).apply_configured_leverage(
        symbol="BTCUSDT", leverage=Decimal("3"), margin_mode="cross"
    )
    assert [path for path, _ in venue.account_writes] == ["/api/v1/account/set-leverage"]
    assert venue.leverage == "3"


class _LiveHostClient:
    base_url = "https://openapi.blofin.com"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, path: str, **_kwargs: object) -> object:
        self.calls.append((method, path))
        raise AssertionError("No request may reach a live host")


def test_refuses_live_host_before_any_request() -> None:
    client = _LiveHostClient()
    provider = GovernedBloFinDemoProvider(client)  # type: ignore[arg-type]
    with pytest.raises(ValueError) as exc:
        provider.apply_configured_leverage(
            symbol="BTCUSDT", leverage=Decimal("10"), margin_mode="isolated"
        )
    assert _reason(exc.value) == "leverage_write_host_refused"
    assert client.calls == []
    # The real client also refuses to be built against the live host.
    with pytest.raises(ValueError, match="production host"):
        BloFinClient(base_url=client.base_url, api_key="k", api_secret="s", api_passphrase="p")


@pytest.mark.parametrize("leverage", [Decimal("21"), Decimal("0"), Decimal("2.5")])
def test_out_of_range_request_never_writes(leverage: Decimal) -> None:
    venue = LeverageVenue(Decimal("100000"))
    with pytest.raises(ValueError) as exc:
        _provider(venue).apply_configured_leverage(
            symbol="BTCUSDT", leverage=leverage, margin_mode="isolated"
        )
    assert _reason(exc.value) == "leverage_request_invalid"
    assert venue.account_writes == []


@pytest.mark.parametrize(
    ("venue", "reason"),
    [
        (LeverageVenue(Decimal("100000"), set_leverage_status=400), "leverage_set_failed"),
        (LeverageVenue(Decimal("100000"), set_margin_status=400), "margin_mode_set_failed"),
        (LeverageVenue(Decimal("100000"), stale_readback=True), "leverage_readback_mismatch"),
        (LeverageVenue(Decimal("100000"), open_position=True), "account_not_flat"),
    ],
)
def test_write_failures_and_readback_mismatch_fail_closed(venue: Venue, reason: str) -> None:
    with pytest.raises(ValueError) as exc:
        _provider(venue).apply_configured_leverage(
            symbol="BTCUSDT", leverage=Decimal("10"), margin_mode="isolated"
        )
    assert _reason(exc.value) == reason
    assert venue.post_count == 0
    if reason == "account_not_flat":
        # A margin-mode change cannot apply to an open position: nothing is sent.
        assert venue.account_writes == []


def test_liquidation_gate_long_and_short() -> None:
    # 10x, 0.4% maintenance: liquidation 9.6% away. Buffer is 2x the stop distance.
    common = {"leverage": Decimal("10"), "notional": Decimal("1000"), "maintenance_tiers": TIERS}
    entry = Decimal("100")
    assert (
        demo_liquidation_refusal(entry=entry, stop=Decimal("97"), direction="long", **common)
        is None
    )
    assert (
        demo_liquidation_refusal(entry=entry, stop=Decimal("95"), direction="long", **common)
        == "demo_liquidation_inside_stop_buffer"
    )
    assert (
        demo_liquidation_refusal(entry=entry, stop=Decimal("103"), direction="short", **common)
        is None
    )
    assert (
        demo_liquidation_refusal(entry=entry, stop=Decimal("111"), direction="short", **common)
        == "demo_liquidation_inside_stop"
    )
    twenty = {**common, "leverage": Decimal("20")}
    assert (
        demo_liquidation_refusal(entry=entry, stop=Decimal("95"), direction="long", **twenty)
        == "demo_liquidation_inside_stop"
    )
    assert (
        demo_liquidation_refusal(
            entry=entry,
            stop=Decimal("99"),
            direction="long",
            leverage=Decimal("10"),
            notional=Decimal("1000"),
            maintenance_tiers=(),
        )
        == "demo_maintenance_tiers_unavailable"
    )


def test_order_margin_mode_follows_the_configured_plan() -> None:
    assert demo_order_margin_mode(CONFIGURED_DEMO_MARGIN_ASSUMPTION, "ISOLATED") == "isolated"
    assert demo_order_margin_mode(CONFIGURED_DEMO_MARGIN_ASSUMPTION, "CROSS") == "cross"
    assert demo_order_margin_mode("verified-demo-existing-1x", "ISOLATED") == "cross"
    with pytest.raises(ValueError):
        demo_order_margin_mode(CONFIGURED_DEMO_MARGIN_ASSUMPTION, None)


def _run_worker(monkeypatch: pytest.MonkeyPatch, venue: Venue, config: DemoLeverageConfig):
    factory = phase7_plan_session_factory()
    with factory() as session:
        _seed(session)
        account = session.scalar(select(ExecutionAccount))
        assert account is not None
        settings = demo_settings(str(account.id))
    source = _live_source()
    client = BloFinClient(
        base_url=settings.blofin_demo_rest_base_url,
        api_key="simulated-key",
        api_secret="simulated-secret",
        api_passphrase="simulated-pass",
        max_retries=0,
        transport=httpx.MockTransport(venue.handle),
        sleeper=lambda _: None,
    )
    provider = GovernedBloFinDemoProvider(
        client, clock=lambda: EVALUATED_AT + timedelta(seconds=venue.clock_delta)
    )
    monkeypatch.setattr(GovernedBloFinDemoLoop, "_get_provider", lambda _: provider)
    monkeypatch.setattr(demo_leverage_config, "load_demo_leverage_config", lambda *_a, **_k: config)
    runtime = _runtime_for(
        factory,
        source,
        _monitor(source, replay=False, clock=lambda: EVALUATED_AT),
        replay=False,
        held=[],
    )
    runtime._settings = settings
    cycle = runtime.run_cycle()
    assert cycle.scans and cycle.scans[0].candidate_ids
    return factory, cycle.scans[0]


def _price() -> Decimal:
    from tests.support.phase6_evaluator import build_pattern_15m_bars, build_slice_trades

    return build_slice_trades(build_pattern_15m_bars())[-1].price


@requires_postgres
def test_happy_path_sets_leverage_then_enters_at_configured_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    venue = LeverageVenue(_price())
    _factory, scan = _run_worker(monkeypatch, venue, DemoLeverageConfig(10, "isolated", "default"))
    assert scan.trade_plan_revision_id is not None, scan.paper_loop_reason
    assert venue.post_count == 1
    paths = [path for path, _ in venue.account_writes]
    assert paths == ["/api/v1/account/set-margin-mode", "/api/v1/account/set-leverage"]
    assert venue.account_writes[-1][1]["leverage"] == "10"
    assert venue.order is not None and venue.order["marginMode"] == "isolated"


@pytest.mark.parametrize(
    ("venue_kwargs", "config", "reason"),
    [
        (
            {"set_leverage_status": 400},
            DemoLeverageConfig(10, "isolated", "default"),
            "demo_leverage_set_failed",
        ),
        (
            {"stale_readback": True},
            DemoLeverageConfig(10, "isolated", "default"),
            "demo_leverage_readback_mismatch",
        ),
        (
            {"set_margin_status": 400},
            DemoLeverageConfig(10, "isolated", "default"),
            "demo_margin_mode_set_failed",
        ),
        # The fixture stop is ~0.145% away. 20x with a 4.9% maintenance tier puts
        # liquidation 0.1% away, inside the stop: refused before any order.
        (
            {"maintenance_rate": "0.049"},
            DemoLeverageConfig(20, "isolated", "account"),
            "demo_liquidation_inside_stop",
        ),
        # 4.8%: liquidation 0.2% away, beyond the stop but inside the 2x buffer.
        (
            {"maintenance_rate": "0.048"},
            DemoLeverageConfig(20, "isolated", "account"),
            "demo_liquidation_inside_stop_buffer",
        ),
    ],
)
@requires_postgres
def test_worker_blocks_without_an_order(
    monkeypatch: pytest.MonkeyPatch,
    venue_kwargs: dict[str, object],
    config: DemoLeverageConfig,
    reason: str,
) -> None:
    venue = LeverageVenue(_price(), **venue_kwargs)  # type: ignore[arg-type]
    factory, scan = _run_worker(monkeypatch, venue, config)
    assert scan.paper_loop_reason == reason
    assert scan.trade_plan_revision_id is None
    assert venue.post_count == 0
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(JournalTrade)) == 0
