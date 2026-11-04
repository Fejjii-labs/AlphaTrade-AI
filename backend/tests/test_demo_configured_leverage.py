"""The app sets the owner-configured leverage on BloFin DEMO before an entry.

The write happens only after the plan has passed sizing, the stop-loss band,
the exposure cap and approval, immediately before the claim and the entry.
"""

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
from tests.test_governed_blofin_demo import Venue, demo_settings, seed_band_ledger_headroom

DEMO_URL = "https://demo-trading-openapi.blofin.com"
TIERS = ((Decimal("0"), Decimal("0.004")),)
# Total-exposure limit with room for a 1-5% band plan at 10x (as in #424's tests).
BAND_LEDGER_MAX_NOTIONAL = Decimal("200000")


@dataclass
class LeverageVenue(Venue):
    """Venue fake whose account-setting endpoints can fail or lie on read-back."""

    set_leverage_status: int = 200
    set_margin_status: int = 200
    stale_readback: bool = False
    open_position: bool = False
    maintenance_rate: str | None = None
    # After the entry POST, positions return this row (e.g. without liqPrice).
    post_fill_position: dict | None = None
    close_posts: int = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/trade/close-position":
            self.close_posts += 1
            return httpx.Response(200, json={"code": "0", "data": []})
        if path.endswith("/positions") and self.post_fill_position and self.post_count:
            row = {**self.post_fill_position}
            if self.order is not None:
                signed = ("-" if self.order["side"] == "sell" else "") + self.order["size"]
                row.setdefault("positions", signed)
                row.setdefault("marginMode", self.order["marginMode"])
                row.setdefault("averagePrice", str(self.price))
            return httpx.Response(200, json={"code": "0", "data": [row]})
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
                    "data": [
                        {"instId": "BTC-USDT", "marginMode": self.margin_mode, "leverage": "1"}
                    ],
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


def _run_worker(
    monkeypatch: pytest.MonkeyPatch,
    venue: Venue,
    config: DemoLeverageConfig,
    *,
    auto_write: bool = True,
    ledger_max_notional: Decimal | None = BAND_LEDGER_MAX_NOTIONAL,
):
    factory, scan, _loop = _run_worker_with_loop(
        monkeypatch,
        venue,
        config,
        auto_write=auto_write,
        ledger_max_notional=ledger_max_notional,
    )
    return factory, scan


def _run_worker_with_loop(
    monkeypatch: pytest.MonkeyPatch,
    venue: Venue,
    config: DemoLeverageConfig,
    *,
    auto_write: bool = True,
    ledger_max_notional: Decimal | None = BAND_LEDGER_MAX_NOTIONAL,
):
    """Run one worker cycle; also return a demo loop for venue reconciliation.

    ``ledger_max_notional`` seeds the account's total-exposure limit (None keeps
    the risk-settings default, 1x the fixture equity).
    """

    from app.signal_fusion.memory import FrozenClock

    factory = phase7_plan_session_factory()
    with factory() as session:
        _seed(session)
        account = session.scalar(select(ExecutionAccount))
        assert account is not None
        if ledger_max_notional is not None:
            seed_band_ledger_headroom(session, account.id, max_notional=ledger_max_notional)
            session.commit()
        settings = demo_settings(str(account.id)).model_copy(
            update={"governed_demo_auto_leverage_write_enabled": auto_write}
        )
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
    loop = GovernedBloFinDemoLoop(
        runtime._canonical_runtime, settings, FrozenClock(EVALUATED_AT), provider=provider
    )
    return factory, cycle.scans[0], loop


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


@requires_postgres
def test_configured_plan_is_sized_within_the_remaining_total_exposure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#424 band sizing at 10x would exceed this limit; the plan is sized to fit it.

    The limit sits between the notional a 1%-floor loss needs on the fixture
    stop and the notional the band's sizing target would take, so only the
    exposure cap lets the plan both clear the floor and pass the claim.
    """

    from app.db.models import AccountRiskAccountingState

    limit = Decimal("35000")
    venue = LeverageVenue(_price())
    factory, scan = _run_worker(
        monkeypatch,
        venue,
        DemoLeverageConfig(10, "isolated", "default"),
        ledger_max_notional=limit,
    )
    assert scan.trade_plan_revision_id is not None, scan.paper_loop_reason
    assert venue.post_count == 1, scan.paper_loop_reason
    with factory() as session:
        ledger = session.scalar(select(AccountRiskAccountingState))
        assert ledger is not None and ledger.max_notional == limit
        used = ledger.reserved_notional + ledger.actual_notional
        assert 0 < used <= limit


@requires_postgres
def test_exposure_cap_leaves_room_for_fee_and_slippage_allowances_at_the_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plan sized right at the exposure limit still passes the claim.

    The claim reserves notional plus 0.1% fee and 0.1% slippage allowances, so
    the cap divides the remaining exposure by 1.002. The limit here is exactly
    350 contracts of notional at the fixture entry (one level, so the worst
    price is the entry). With the divisor the plan sizes 349 contracts and fits;
    without it, 350 contracts plus allowances exceed the limit and the claim
    refuses with no order.
    """

    from app.db.models import AccountRiskAccountingState

    multiplier = Decimal("0.001")  # Fixture contractValue, lot size 1.
    entry = _price()  # The fixture setup sells at the bid.
    contract = entry * multiplier
    limit = 350 * contract
    venue = LeverageVenue(_price())
    factory, scan = _run_worker(
        monkeypatch,
        venue,
        DemoLeverageConfig(10, "isolated", "default"),
        ledger_max_notional=limit,
    )
    assert scan.trade_plan_revision_id is not None, scan.paper_loop_reason
    assert venue.post_count == 1, scan.paper_loop_reason
    assert venue.order is not None and venue.order["side"] == "sell"
    assert venue.order["size"] == "349"
    with factory() as session:
        ledger = session.scalar(select(AccountRiskAccountingState))
        assert ledger is not None and ledger.max_notional == limit
        used = ledger.reserved_notional + ledger.actual_notional
        # At the edge: the claim accepted 349 contracts with allowances, and one
        # more contract with its allowances would not fit.
        assert used <= limit < (349 + 1) * contract * Decimal("1.002")


@requires_postgres
def test_configured_plan_refuses_when_the_exposure_limit_cannot_hold_the_loss_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default limit (1x equity): no 1%-floor plan fits, so no order and no exposure block."""

    venue = LeverageVenue(_price())
    _factory, scan = _run_worker(
        monkeypatch,
        venue,
        DemoLeverageConfig(10, "isolated", "default"),
        ledger_max_notional=None,
    )
    assert scan.paper_loop_reason == "stop_loss_below_band"
    assert venue.post_count == 0
    # A refused plan never writes leverage.
    assert venue.account_writes == []


@pytest.mark.parametrize(
    ("venue_kwargs", "reason"),
    [
        ({"set_leverage_status": 400}, "demo_leverage_set_failed"),
        ({"stale_readback": True}, "demo_leverage_readback_mismatch"),
        ({"set_margin_status": 400}, "demo_margin_mode_set_failed"),
    ],
)
@requires_postgres
def test_failed_write_after_a_passing_plan_blocks_before_the_claim(
    monkeypatch: pytest.MonkeyPatch, venue_kwargs: dict[str, object], reason: str
) -> None:
    from app.db.models import ExecutionCommand

    venue = LeverageVenue(_price(), **venue_kwargs)  # type: ignore[arg-type]
    factory, scan = _run_worker(monkeypatch, venue, DemoLeverageConfig(10, "isolated", "default"))
    assert scan.paper_loop_reason == reason
    # The plan passed, the write was attempted, and nothing was claimed or sent.
    assert scan.trade_plan_revision_id is not None
    assert venue.account_writes
    assert venue.post_count == 0
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ExecutionCommand)) == 0
        assert session.scalar(select(func.count()).select_from(JournalTrade)) == 0


@requires_postgres
def test_passing_plan_reads_kill_switch_commits_writes_then_enters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.orm import Session

    from app.services import autonomous_trading
    from app.services.execution_service import ExecutionService

    events: list[str] = []
    venue = LeverageVenue(_price())
    handle = venue.handle

    def recording_handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/v1/trade/order":
            events.append("entry")
        return handle(request)

    venue.handle = recording_handle  # type: ignore[method-assign]
    kill_read = autonomous_trading.kill_switch_refusal

    def recording_kill_read(*args: object, **kwargs: object) -> str | None:
        events.append("kill_read")
        return kill_read(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(autonomous_trading, "kill_switch_refusal", recording_kill_read)
    commit = Session.commit

    def recording_commit(self: Session) -> None:
        events.append("commit")
        commit(self)

    monkeypatch.setattr(Session, "commit", recording_commit)
    apply = GovernedBloFinDemoProvider.apply_configured_leverage

    def recording_apply(self: GovernedBloFinDemoProvider, **kwargs: object) -> dict[str, str]:
        events.append("write")
        return apply(self, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(GovernedBloFinDemoProvider, "apply_configured_leverage", recording_apply)
    claim = ExecutionService.execute_paper_plan

    def recording_claim(self: ExecutionService, *args: object, **kwargs: object) -> object:
        events.append("claim")
        return claim(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ExecutionService, "execute_paper_plan", recording_claim)
    _factory, scan = _run_worker(monkeypatch, venue, DemoLeverageConfig(10, "isolated", "default"))
    assert scan.trade_plan_revision_id is not None, scan.paper_loop_reason
    assert venue.post_count == 1
    assert events.count("write") == 1 and events.count("entry") == 1
    write = events.index("write")
    # Fresh kill-switch read, commit, then the write; the claim and entry follow.
    assert events[write - 2 : write + 1] == ["kill_read", "commit", "write"]
    assert write < events.index("claim") < events.index("entry")


@pytest.mark.parametrize(
    ("venue_kwargs", "config", "reason"),
    [
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
def test_refused_plan_writes_no_leverage_and_sends_no_order(
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
    # Refused at planning: leverage is never written.
    assert venue.account_writes == []
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(JournalTrade)) == 0


# --- Griller #412 conditions: C1 staging-only writes, C3 journal, C2 post-fill hold ---


@requires_postgres
def test_auto_write_flag_off_never_writes_and_blocks_on_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A saved setting stays binding without the write: the 1x cross account
    # does not read back 10x isolated, so nothing is planned or sent.
    venue = LeverageVenue(_price())
    _factory, scan = _run_worker(
        monkeypatch, venue, DemoLeverageConfig(10, "isolated", "account"), auto_write=False
    )
    assert venue.account_writes == []
    assert venue.post_count == 0
    assert scan.trade_plan_revision_id is None


@requires_postgres
def test_auto_write_flag_off_without_a_saved_setting_reads_existing_leverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # #424 path: no write and no saved setting, so no configured-leverage refusal.
    venue = LeverageVenue(_price())
    _factory, scan = _run_worker(
        monkeypatch, venue, DemoLeverageConfig(10, "isolated", "default"), auto_write=False
    )
    assert venue.account_writes == []
    assert scan.paper_loop_reason != "demo_leverage_readback_mismatch"


def test_leverage_write_allowed_only_with_flag_in_staging_or_local() -> None:
    from types import SimpleNamespace

    from app.core.config import Environment
    from app.services.governed_blofin_demo import demo_leverage_write_allowed

    def ns(flag: bool, env: Environment) -> SimpleNamespace:
        return SimpleNamespace(governed_demo_auto_leverage_write_enabled=flag, environment=env)

    assert demo_leverage_write_allowed(ns(True, Environment.STAGING))
    assert demo_leverage_write_allowed(ns(True, Environment.LOCAL))
    assert not demo_leverage_write_allowed(ns(True, Environment.PRODUCTION))
    assert not demo_leverage_write_allowed(ns(False, Environment.STAGING))


@requires_postgres
def test_successful_leverage_write_is_journaled_with_old_and_new_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db.models import AuditLog

    venue = LeverageVenue(_price())
    factory, _scan = _run_worker(monkeypatch, venue, DemoLeverageConfig(10, "isolated", "default"))
    with factory() as session:
        rows = session.scalars(
            select(AuditLog).where(AuditLog.action == "demo_leverage_applied")
        ).all()
    assert len(rows) == 1
    meta = rows[0].redacted_metadata
    assert meta["leverage"] == "10"
    assert meta["margin_mode"] == "isolated"
    assert meta["previous_margin_mode"] == "cross"
    assert "previous_leverage" in meta


def test_apply_returns_previous_and_new_values() -> None:
    venue = LeverageVenue(_price())
    result = _provider(venue).apply_configured_leverage(
        symbol="BTCUSDT", leverage=Decimal("10"), margin_mode="isolated"
    )
    assert result["previous_margin_mode"] == "cross"
    assert result["leverage"] == "10"
    assert result["margin_mode_changed"] == "true"


def test_batch_leverage_info_needs_exactly_one_row_for_the_instrument() -> None:
    from app.providers.exchange.blofin_account import _batch_inst_row

    row = {"instId": "BTC-USDT", "leverage": "10", "marginMode": "isolated"}
    assert _batch_inst_row([row], "BTC-USDT") == row
    assert _batch_inst_row([row, {**row, "instId": "ETH-USDT"}], "BTC-USDT") == row
    # Fail closed: object (old deprecated shape), duplicates, no match, no instId.
    assert _batch_inst_row(row, "BTC-USDT") == {}
    assert _batch_inst_row([row, row], "BTC-USDT") == {}
    assert _batch_inst_row([{**row, "instId": "ETH-USDT"}], "BTC-USDT") == {}
    assert _batch_inst_row([{"leverage": "10", "marginMode": "isolated"}], "BTC-USDT") == {}
    assert _batch_inst_row([], "BTC-USDT") == {}
    assert _batch_inst_row("x", "BTC-USDT") == {}


def test_leverage_read_uses_batch_endpoint_and_refuses_an_unmatched_row() -> None:
    from app.providers.exchange.blofin_account import BloFinAccountProvider

    paths: list[str] = []
    rows: list[dict] = [{"instId": "ETH-USDT", "leverage": "10", "marginMode": "isolated"}]

    def handle(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"code": "0", "data": rows})

    client = BloFinClient(
        base_url=DEMO_URL,
        api_key="k",
        api_secret="s",
        api_passphrase="p",
        max_retries=0,
        transport=httpx.MockTransport(handle),
        sleeper=lambda _: None,
    )
    info = BloFinAccountProvider(client).get_leverage_info(
        inst_id="BTC-USDT", margin_mode="isolated"
    )
    assert paths == ["/api/v1/account/batch-leverage-info"]
    assert info.leverage == 0 and not info.margin_mode_supplied


def _configured_plan(side: str, stop: Decimal):
    from types import SimpleNamespace

    from app.schemas.trade_plan import EntrySide

    return SimpleNamespace(
        execution_instrument="BTC-USDT",
        side=EntrySide.BUY if side == "long" else EntrySide.SELL,
        margin_mode="ISOLATED",
        risk_and_exits=SimpleNamespace(
            stop=SimpleNamespace(value=stop),
            leverage=Decimal("10"),
            margin_assumption_id=CONFIGURED_DEMO_MARGIN_ASSUMPTION,
        ),
    )


@pytest.mark.parametrize(
    ("side", "stop", "liq", "expected"),
    [
        ("long", Decimal("99"), Decimal("90"), None),  # 10 away, buffer 2
        ("long", Decimal("95"), Decimal("92"), "demo_post_fill_liquidation_inside_buffer"),
        ("long", Decimal("95"), Decimal("96"), "demo_post_fill_liquidation_inside_stop"),
        ("short", Decimal("101"), Decimal("110"), None),
        ("short", Decimal("105"), Decimal("108"), "demo_post_fill_liquidation_inside_buffer"),
        ("short", Decimal("105"), Decimal("104"), "demo_post_fill_liquidation_inside_stop"),
    ],
)
def test_post_fill_venue_liquidation_check_for_configured_plans(
    monkeypatch: pytest.MonkeyPatch, side: str, stop: Decimal, liq: Decimal, expected: str | None
) -> None:
    from app.services import governed_blofin_demo as mod

    provider = object.__new__(GovernedBloFinDemoProvider)
    provider.read_open_liquidation = lambda **_k: liq  # type: ignore[method-assign]
    # The SimpleNamespace plan stands in for the schema in this pure check.
    monkeypatch.setattr("app.schemas.trade_plan.TradePlanRevision", object)
    plan = _configured_plan(side, stop)
    assert mod._demo_post_fill_liquidation(provider, plan, Decimal("100")) == expected


class _Session:
    """Stand-in session that records commits into a shared event list."""

    def __init__(self, events: list[str]) -> None:
        self._events = events

    def commit(self) -> None:
        self._events.append("commit")


# --- Griller #412 re-review C-a: failed close and unreadable liqPrice paths ---


class _RecordingSwitch:
    activated: list[dict]
    events: list[str]

    def __init__(self, *_a: object, **_k: object) -> None:
        return None

    def activate(self, **kwargs: object) -> None:
        type(self).activated.append(dict(kwargs))
        type(self).events.append("arm")


def _hold_loop(monkeypatch: pytest.MonkeyPatch, provider: object, events: list[str] | None = None):
    from types import SimpleNamespace

    from app.services import governed_blofin_demo as mod

    alerts: list[dict] = []
    events = [] if events is None else events

    class _Alerts:
        def __init__(self, *_a: object, **_k: object) -> None:
            return None

        def create(self, **kwargs: object) -> None:
            alerts.append(dict(kwargs))
            events.append("alert")

    switch = type("_Switch", (_RecordingSwitch,), {"activated": [], "events": events})
    monkeypatch.setattr(mod, "KillSwitchService", switch)
    monkeypatch.setattr(mod, "PaperAlertService", _Alerts)
    monkeypatch.setattr(mod, "AuditService", lambda _s: None)
    loop = object.__new__(GovernedBloFinDemoLoop)
    loop._settings = SimpleNamespace()
    monkeypatch.setattr(GovernedBloFinDemoLoop, "_get_provider", lambda _self: provider)
    return loop, alerts, switch.activated


@requires_postgres
def test_unreadable_liquidation_on_configured_fill_warns_without_hold_or_flatten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db.models import (
        ExecutionCommand,
        KillSwitchState,
        PaperValidationAlert,
        VenueSubmitEffect,
    )

    venue = LeverageVenue(
        _price(),
        # Open NET position with no liqPrice (cross positions may omit it).
        post_fill_position={"instId": "BTC-USDT", "positionSide": "net"},
    )
    factory, scan, loop = _run_worker_with_loop(
        monkeypatch, venue, DemoLeverageConfig(10, "isolated", "default")
    )
    assert scan.trade_plan_revision_id is not None, scan.paper_loop_reason
    assert venue.post_count == 1
    with factory() as session:
        command = session.scalar(select(ExecutionCommand))
        assert command is not None
        result = loop.reconcile_command(session, command_id=command.id)
        assert not result.startswith("demo_post_fill_liquidation"), result
        effect = session.scalar(select(VenueSubmitEffect))
        assert effect is not None
        assert effect.reconciliation_disposition != "DEMO_LIQUIDATION_UNREADABLE"
        switch = session.scalar(select(KillSwitchState))
        assert switch is None or not switch.active, switch.reason
        warnings = session.scalars(
            select(PaperValidationAlert).where(
                PaperValidationAlert.alert_type == "demo_liquidation_unreadable"
            )
        ).all()
        # Deduplicated per command: the in-cycle and the explicit reconcile share one row.
        assert len(warnings) == 1
        assert warnings[0].severity == "warning"
        assert warnings[0].metadata_json["auto_close"] is False
    assert venue.close_posts == 0


class _NoVenueCallsProvider:
    """Any provider call from the breach hold is a venue call and fails the test."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> object:
        self.calls.append(name)
        raise AssertionError(f"Breach hold must not call the provider: {name}")


@pytest.mark.parametrize(
    ("reason", "disposition", "arms"),
    [
        # Existing behaviour: a liquidation through the stop arms the kill switch.
        ("demo_post_fill_liquidation_inside_stop", "DEMO_LIQUIDATION_INSIDE_STOP", 1),
        # Inside the 2x buffer only: warn and alert, arm nothing.
        ("demo_post_fill_liquidation_inside_buffer", "DEMO_LIQUIDATION_INSIDE_BUFFER", 0),
    ],
)
def test_breach_hold_alerts_without_any_close_or_order_call(
    monkeypatch: pytest.MonkeyPatch, reason: str, disposition: str, arms: int
) -> None:
    from types import SimpleNamespace
    from uuid import uuid4

    provider = _NoVenueCallsProvider()
    loop, alerts, activated = _hold_loop(monkeypatch, provider)
    command = SimpleNamespace(id=uuid4(), organization_id=uuid4(), user_id=uuid4())
    effect = SimpleNamespace(reconciliation_disposition=None)
    loop._hold_profile_liquidation(
        _Session([]),  # type: ignore[arg-type]
        command=command,  # type: ignore[arg-type]
        effect=effect,  # type: ignore[arg-type]
        liquidation_reason=reason,
        protected=True,
    )
    assert provider.calls == []
    assert len(activated) == arms
    assert effect.reconciliation_disposition == disposition
    assert len(alerts) == 1
    assert alerts[0]["metadata"]["auto_close"] is False
    assert "No automatic close" in str(alerts[0]["message"])
    assert not hasattr(GovernedBloFinDemoProvider, "flatten_position")


@requires_postgres
def test_liquidation_breach_after_configured_fill_sends_no_close_or_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db.models import ExecutionCommand, PaperValidationAlert

    price = _price()
    venue = LeverageVenue(
        price,
        # The fixture setup is a short: a liquidation price at or below its stop
        # is through the stop.
        post_fill_position={
            "instId": "BTC-USDT",
            "positionSide": "net",
            "liquidationPrice": str(price - 1),
        },
    )
    factory, scan, loop = _run_worker_with_loop(
        monkeypatch, venue, DemoLeverageConfig(10, "isolated", "default")
    )
    assert scan.trade_plan_revision_id is not None, scan.paper_loop_reason
    assert venue.post_count == 1
    with factory() as session:
        command = session.scalar(select(ExecutionCommand))
        assert command is not None
        result = loop.reconcile_command(session, command_id=command.id)
        assert result.startswith("demo_post_fill_liquidation"), result
        alerts = session.scalars(
            select(PaperValidationAlert).where(
                PaperValidationAlert.alert_type == "demo_liquidation_through_stop"
            )
        ).all()
        assert len(alerts) == 1
        assert alerts[0].metadata_json["auto_close"] is False
    # Only the entry order: no close-position and no further order on breach.
    assert venue.close_posts == 0
    assert venue.post_count == 1


# --- v3 port: reconciliation with #424 (existing leverage, stop-loss band, 60s window) ---


def test_configured_leverage_binds_only_with_a_write_or_a_saved_setting() -> None:
    from types import SimpleNamespace

    from app.core.config import Environment
    from app.services.governed_blofin_demo import binding_demo_leverage_config

    write = SimpleNamespace(
        governed_demo_auto_leverage_write_enabled=True, environment=Environment.STAGING
    )
    no_write = SimpleNamespace(
        governed_demo_auto_leverage_write_enabled=False, environment=Environment.PRODUCTION
    )
    default = DemoLeverageConfig(10, "isolated", "default")
    saved = DemoLeverageConfig(3, "cross", "account")
    override = DemoLeverageConfig(5, "isolated", "symbol_override")
    assert binding_demo_leverage_config(default, write) == default
    assert binding_demo_leverage_config(saved, no_write) == saved
    assert binding_demo_leverage_config(override, no_write) == override
    # Nothing saved and no write allowed: #424's existing-leverage path.
    assert binding_demo_leverage_config(default, no_write) is None


def test_submit_requires_stored_leverage_only_for_profile_and_configured_plans() -> None:
    from types import SimpleNamespace

    from app.providers.exchange.governed_blofin import (
        CONFIGURED_DEMO_MARGIN_ASSUMPTION,
        demo_plan_leverage,
    )

    def plan(assumption: str, mode: str = "CROSS") -> object:
        return SimpleNamespace(
            risk_and_exits=SimpleNamespace(margin_assumption_id=assumption, leverage=Decimal("7")),
            margin_mode=SimpleNamespace(value=mode),
        )

    assert demo_plan_leverage(plan(CONFIGURED_DEMO_MARGIN_ASSUMPTION, "CROSS")) == (
        Decimal("7"),
        "cross",
    )
    assert demo_plan_leverage(plan("verified-demo-read-leverage")) == (Decimal("7"), "isolated")
    assert demo_plan_leverage(plan("verified-demo-existing-1x")) == (None, "cross")
    assert demo_plan_leverage(plan("verified-demo-existing-leverage-cross")) == (None, "cross")
    assert demo_plan_leverage(plan("verified-demo-existing-leverage-isolated")) == (
        None,
        "isolated",
    )


def _configured_band_plan(
    monkeypatch: pytest.MonkeyPatch,
    *,
    config: DemoLeverageConfig,
    leverage: str,
    margin_mode: str,
    stop: str,
    worst: str,
    remaining_exposure: Decimal | None = Decimal("1000000"),
) -> object:
    """Drive GovernedBloFinDemoLoop._plan_terms (unprofiled) with a binding config.

    ``remaining_exposure`` stands in for the ledger headroom read before planning.
    """

    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from app.core.config import Settings
    from app.services.governed_blofin_demo import GovernedBloFinDemoLoop

    now = datetime(2026, 11, 4, 8, 0, tzinfo=UTC)
    side = SimpleNamespace(value="BUY")

    def _dump(**_kwargs: object) -> dict[str, object]:
        raise AssertionError("plan passed the stop-loss band")

    terms = SimpleNamespace(
        side=side,
        account_id=uuid4(),
        risk_and_exits=SimpleNamespace(
            stop=SimpleNamespace(value=Decimal(stop)),
            targets=[SimpleNamespace(price=SimpleNamespace(value=Decimal("110")))],
        ),
        model_dump=_dump,
    )
    monkeypatch.setattr("app.services.governed_blofin_demo._plan_terms", lambda **_k: terms)
    snapshot = SimpleNamespace(
        equity=Decimal("10000"),
        available=Decimal("10000"),
        book=SimpleNamespace(side=side, worst_price=lambda *_a, **_k: Decimal(worst)),
        observed_at=now,
        tick=Decimal("0.1"),
        price=Decimal("100"),
        account_leverage=Decimal(leverage),
        margin_mode=margin_mode,
        multiplier=Decimal("1"),
        maximum=Decimal("1000000"),
        lot=Decimal("1"),
        minimum=Decimal("1"),
        instrument="BTC-USDT",
        maintenance_tiers=((Decimal("0"), Decimal("0.004")),),
    )
    loop = GovernedBloFinDemoLoop(None, Settings(_env_file=None), SimpleNamespace())  # type: ignore[arg-type]
    loop._leverage_config = config
    loop._remaining_exposure = remaining_exposure
    monkeypatch.setattr(loop, "_require_snapshot", lambda: snapshot)
    return loop._plan_terms(
        candidate=SimpleNamespace(),  # type: ignore[arg-type]
        assembled=SimpleNamespace(),  # type: ignore[arg-type]
        policy=SimpleNamespace(),  # type: ignore[arg-type]
        account_id=uuid4(),
        equity=Decimal("10000"),
        now=now,
        eligibility_valid_until=now,
        eligibility_id=uuid4(),
    )


@pytest.mark.parametrize(
    ("remaining", "reason"),
    [
        # Headroom never read (or unreadable): fail closed.
        (None, "risk_limits_unverified"),
        (Decimal("NaN"), "risk_limits_unverified"),
        (Decimal("0"), "insufficient_total_exposure"),
        # 10x would size ~11.6k notional for this 1% stop; 5k headroom caps it
        # below the 1% loss floor instead of planning a claim-refused trade.
        (Decimal("5000"), "stop_loss_below_band"),
    ],
)
def test_configured_plan_is_bounded_by_the_remaining_total_exposure(
    monkeypatch: pytest.MonkeyPatch, remaining: Decimal | None, reason: str
) -> None:
    assert (
        _configured_band_plan(
            monkeypatch,
            config=DemoLeverageConfig(10, "isolated", "account"),
            leverage="10",
            margin_mode="isolated",
            stop="99",
            worst="100",
            remaining_exposure=remaining,
        )
        == reason
    )


def test_configured_plan_refuses_a_leverage_readback_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = DemoLeverageConfig(10, "isolated", "account")
    assert (
        _configured_band_plan(
            monkeypatch, config=config, leverage="5", margin_mode="isolated", stop="99", worst="100"
        )
        == "demo_leverage_readback_mismatch"
    )
    assert (
        _configured_band_plan(
            monkeypatch, config=config, leverage="10", margin_mode="cross", stop="99", worst="100"
        )
        == "demo_leverage_readback_mismatch"
    )


def test_configured_plan_uses_the_stop_loss_band_bounded_by_configured_leverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Configured 1x caps notional at 9500; a 0.5% stop then risks ~66 < 1% floor (100).
    assert (
        _configured_band_plan(
            monkeypatch,
            config=DemoLeverageConfig(1, "cross", "account"),
            leverage="1",
            margin_mode="cross",
            stop="99.5",
            worst="100",
        )
        == "stop_loss_below_band"
    )
    # Configured 5x isolated: a thin book pushes the loss above the 5% ceiling
    # (liquidation ~83.6 stays outside the 2x stop buffer from 104).
    assert (
        _configured_band_plan(
            monkeypatch,
            config=DemoLeverageConfig(5, "isolated", "account"),
            leverage="5",
            margin_mode="isolated",
            stop="99",
            worst="104",
        )
        == "stop_loss_above_band"
    )


# --- Griller #430 risk 2: kill switch re-checked immediately before the leverage write ---


class _KillReadSession:
    """Session stand-in: the org kill-switch read is the only query before the write."""

    def __init__(self, events: list[str], *, row_active: bool | None, fail: bool) -> None:
        self._events = events
        self._row_active = row_active
        self._fail = fail

    def scalar(self, _statement: object) -> object:
        self._events.append("kill_read")
        if self._fail:
            raise RuntimeError("kill switch table unreadable")
        if self._row_active is None:
            return None
        from types import SimpleNamespace

        return SimpleNamespace(active=self._row_active)

    def commit(self) -> None:
        self._events.append("commit")

    def rollback(self) -> None:
        self._events.append("rollback")


def _write_loop(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    *,
    config: DemoLeverageConfig = DemoLeverageConfig(10, "isolated", "default"),
    write_enabled: bool = True,
) -> GovernedBloFinDemoLoop:
    from types import SimpleNamespace

    from app.core.config import Settings
    from app.signal_fusion.memory import FrozenClock

    class _Provider:
        def apply_configured_leverage(self, **_kwargs: object) -> None:
            events.append("write")
            raise ValueError("Demo set-leverage failed.")

    monkeypatch.setattr(demo_leverage_config, "load_demo_leverage_config", lambda *_a, **_k: config)
    # SimpleNamespace plans stand in for the schema in this unit check.
    monkeypatch.setattr("app.schemas.trade_plan.TradePlanRevision", SimpleNamespace)
    settings = Settings(
        _env_file=None, environment="local", governed_demo_auto_leverage_write_enabled=write_enabled
    )
    return GovernedBloFinDemoLoop(
        SimpleNamespace(),  # type: ignore[arg-type]
        settings,
        FrozenClock(EVALUATED_AT),
        provider=_Provider(),  # type: ignore[arg-type]
    )


def _stored_plan(assumption: str = CONFIGURED_DEMO_MARGIN_ASSUMPTION) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(
        risk_and_exits=SimpleNamespace(margin_assumption_id=assumption, leverage=Decimal("10")),
        margin_mode=SimpleNamespace(value="ISOLATED"),
    )


def _apply(loop: GovernedBloFinDemoLoop, session: object, plan: object) -> str | None:
    from types import SimpleNamespace
    from uuid import uuid4

    return loop._apply_planned_leverage(
        session,  # type: ignore[arg-type]
        SimpleNamespace(organization_id=uuid4(), user_id=uuid4(), symbol="BTCUSDT"),  # type: ignore[arg-type]
        SimpleNamespace(candidate_id=uuid4()),  # type: ignore[arg-type]
        plan,
    )


@pytest.mark.parametrize(
    ("observed", "row_active", "fail", "reason"),
    [
        # Clean: observed off, org switch off -> the write is attempted.
        (False, None, False, "demo_leverage_set_failed"),
        (False, False, False, "demo_leverage_set_failed"),
        # Armed after the cycle observed it off: the fresh read blocks.
        (False, True, False, "kill_switch_active"),
        # Unreadable org switch counts as on.
        (False, None, True, "kill_switch_unverified"),
        # Unknown cycle observation counts as on.
        (None, None, False, "kill_switch_active"),
        ("unknown", None, False, "kill_switch_active"),
        (True, None, False, "kill_switch_active"),
    ],
)
def test_kill_switch_rechecked_immediately_before_leverage_write(
    monkeypatch: pytest.MonkeyPatch,
    observed: object,
    row_active: bool | None,
    fail: bool,
    reason: str,
) -> None:
    events: list[str] = []
    loop = _write_loop(monkeypatch, events)
    loop._observed_kill_switch = observed  # type: ignore[assignment]
    session = _KillReadSession(events, row_active=row_active, fail=fail)
    assert _apply(loop, session, _stored_plan()) == reason
    if reason == "demo_leverage_set_failed":
        # The fresh read is the last step before the write.
        assert events[-3:] == ["kill_read", "commit", "write"]
    else:
        assert "write" not in events


@pytest.mark.parametrize(
    ("assumption", "config", "write_enabled", "reason"),
    [
        # Not a configured plan, or no write allowed: nothing is read or written.
        ("verified-demo-existing-1x", DemoLeverageConfig(10, "isolated", "default"), True, None),
        (
            CONFIGURED_DEMO_MARGIN_ASSUMPTION,
            DemoLeverageConfig(10, "isolated", "account"),
            False,
            None,
        ),
        # The owner changed the setting after the plan was sized: no write.
        (
            CONFIGURED_DEMO_MARGIN_ASSUMPTION,
            DemoLeverageConfig(5, "isolated", "account"),
            True,
            "demo_leverage_config_changed",
        ),
        (
            CONFIGURED_DEMO_MARGIN_ASSUMPTION,
            DemoLeverageConfig(10, "cross", "account"),
            True,
            "demo_leverage_config_changed",
        ),
    ],
)
def test_planned_leverage_write_only_for_a_matching_configured_plan(
    monkeypatch: pytest.MonkeyPatch,
    assumption: str,
    config: DemoLeverageConfig,
    write_enabled: bool,
    reason: str | None,
) -> None:
    events: list[str] = []
    loop = _write_loop(monkeypatch, events, config=config, write_enabled=write_enabled)
    loop._observed_kill_switch = False  # type: ignore[assignment]
    session = _KillReadSession(events, row_active=None, fail=False)
    assert _apply(loop, session, _stored_plan(assumption)) == reason
    assert events == []
