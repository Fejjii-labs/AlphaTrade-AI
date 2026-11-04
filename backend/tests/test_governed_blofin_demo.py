"""Simulated venue acceptance through genuine Watcher → Journal authorities."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import Environment, Settings
from app.core.errors import NotFoundError, TradingPolicyError
from app.core.execution_credentials import (
    blofin_execution_authorized,
    governed_demo_worker_access_requested,
)
from app.db.learning_attribution import LearningAttributionRecordRow
from app.db.models import (
    AccountRiskAccountingState,
    ConversationMessage,
    ExecutionAccount,
    ExecutionCommand,
    ExecutionFillFact,
    ExecutionProjection,
    JournalTrade,
    KillSwitchState,
    RiskReservation,
    VenueSubmitEffect,
)
from app.interactive_agent.contracts import AgentTurnRequest
from app.interactive_agent.paper_execution_explanation import read_paper_execution
from app.interactive_agent.service import InteractiveAgentService
from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.governed_blofin import GovernedBloFinDemoProvider
from app.schemas.execution_protocol import ClosePaperPlanRequest, VenueSubmitEffectState
from app.schemas.risk import KillSwitchMutationRequest
from app.services.audit_service import AuditService
from app.services.execution_service import ExecutionService
from app.services.governed_blofin_demo import (
    GovernedBloFinDemoLoop,
    profile_liquidation_hold,
)
from app.services.risk.kill_switch import KillSwitchService
from app.signal_fusion.memory import FrozenClock
from tests.support.phase5_market import EVALUATED_AT
from tests.support.postgres_persistence import phase7_plan_session_factory, requires_postgres
from tests.test_automated_paper_loop import _live_source, _monitor, _runtime_for, _seed
from tests.test_watcher_paper_runtime import ORG, USER

_TS = str(int(EVALUATED_AT.timestamp() * 1000))


@dataclass
class Venue:
    price: Decimal
    behavior: str = "success"
    post_count: int = 0
    order: dict | None = None
    reads: int = 0
    ticker_reads: int = 0
    clock_delta: int = 0
    after_preflight: Callable[[], None] | None = None
    entry_fee_currency: str | None = "USDT"
    tier_min_size: str = "0"
    # Existing demo account leverage (read only). The 1-5% stop-loss band needs
    # enough notional headroom for the fixture setup's stop distance.
    account_leverage: str = "1"

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        data: object
        if request.method == "POST":
            assert path == "/api/v1/trade/order", "Unexpected account mutation"
            self.post_count += 1
            self.order = json.loads(request.content)
            assert self.order["slOrderPrice"] == self.order["tpOrderPrice"] == "-1"
            assert self.order["positionSide"] == "net"
            if self.behavior == "rejected":
                self.order = None
                return httpx.Response(400, json={"code": "51000", "msg": "Rejected protection"})
            if self.behavior in {"timeout", "restart"}:
                raise httpx.ReadTimeout("Response lost", request=request)
            data = [{"orderId": "demo-1", "clientOrderId": self.order["clientOrderId"]}]
        elif path.endswith("query-apikey"):
            data = {"readOnly": 1 if self.behavior == "permissions" else 0}
        elif path.endswith("position-mode"):
            data = {"positionMode": "net_mode"}
        elif path.endswith("/margin-mode"):
            data = {"marginMode": "cross"}
        elif path.endswith("position-tiers"):
            data = [
                {
                    "instId": "BTC-USDT",
                    "marginMode": "cross",
                    "minSize": self.tier_min_size,
                    "maxSize": "100000",
                    "maintenanceMarginRate": "0.004",
                    "maxLeverage": "125",
                }
            ]
        elif path.endswith("leverage-info"):
            data = {"instId": "BTC-USDT", "marginMode": "cross", "leverage": self.account_leverage}
        elif path.endswith("instruments"):
            data = [
                {
                    "instId": "BTC-USDT",
                    "baseCurrency": "BTC",
                    "quoteCurrency": "USDT",
                    "contractType": "linear",
                    "contractValue": "0.001",
                    "tickSize": "0.1",
                    "lotSize": "1",
                    "minSize": "1",
                    "maxMarketSize": "1000000",
                    "state": "live",
                    "instType": "SWAP",
                }
            ]
        elif path.endswith("books"):
            self.ticker_reads += 1
            quoted = self.price * Decimal("1.01") if self.behavior == "basis" else self.price
            if self.behavior == "expiry" and self.ticker_reads == 2:
                self.clock_delta = 11
            if self.ticker_reads == 2 and self.after_preflight is not None:
                self.after_preflight()
            data = [
                {
                    "instId": "BTC-USDT",
                    "asks": [[str(quoted + Decimal("0.1")), "1000000"]],
                    "bids": [[str(quoted), "1000000"]],
                    "ts": str(int(_TS) - 11000) if self.behavior == "stale" else _TS,
                }
            ]
        elif path.endswith("positions") or path.endswith("orders-pending"):
            data = []
        elif path.endswith("balance"):
            data = [{"currency": "USDT", "balance": "10000", "available": "10000"}]
        elif path.endswith("order-detail"):
            self.reads += 1
            if self.order is None or (self.behavior == "restart" and self.reads == 1):
                data = []
            else:
                assert request.url.params["clientOrderId"] == self.order["clientOrderId"]
                filled = "0" if self.behavior == "unfilled" else self.order["size"]
                if self.behavior in {"partial", "partial_cancel"}:
                    filled = str(Decimal(self.order["size"]) / 2)
                data = [
                    {
                        **self.order,
                        "orderId": "demo-1",
                        "filledSize": filled,
                        "state": "canceled"
                        if self.behavior == "partial_cancel"
                        else (
                            "partially_filled"
                            if self.behavior == "partial"
                            else ("live" if self.behavior == "unfilled" else "filled")
                        ),
                        "createTime": _TS,
                    }
                ]
        elif path.endswith("fills-history"):
            assert self.order is not None
            data = (
                []
                if self.behavior == "unfilled"
                else [
                    {
                        "orderId": "demo-1",
                        "tradeId": "fill-1",
                        "instId": "BTC-USDT",
                        "side": self.order["side"],
                        "positionSide": "net",
                        "fillPrice": str(self.price),
                        "fillSize": str(Decimal(self.order["size"]) / 2)
                        if self.behavior in {"partial", "partial_cancel"}
                        else self.order["size"],
                        "fee": "0.02",
                        "feeCurrency": self.entry_fee_currency,
                        "ts": _TS,
                    }
                ]
            )
        elif path.endswith("orders-tpsl-pending"):
            if self.order is None:
                return httpx.Response(200, json={"code": "0", "data": []})
            if self.behavior == "protection_outage":
                return httpx.Response(503)
            data = (
                []
                if self.behavior == "protection_failure"
                else [
                    {
                        **self.order,
                        "tpslId": "protect-1",
                        "clientOrderId": "unrelated"
                        if self.behavior == "protection_wrongid"
                        else self.order["clientOrderId"],
                        "side": "buy" if self.order["side"] == "sell" else "sell",
                        "state": "live",
                        "createTime": "malformed"
                        if self.behavior == "protection_malformed"
                        else _TS,
                    }
                ]
            )
            if self.behavior == "protection_unrelated_malformed":
                data.insert(0, {"clientOrderId": "unrelated", "createTime": "malformed"})
        else:
            raise AssertionError(f"Unexpected endpoint: {path}")
        return httpx.Response(200, json={"code": "0", "data": data})


def seed_band_ledger_headroom(session, account_id) -> None:
    """Ledger headroom for a 1-5% stop-loss band at the fixture's 10x leverage.

    The default 10k total cap would refuse the band-sized plan.
    """

    session.add(
        AccountRiskAccountingState(
            organization_id=ORG,
            account_id=account_id,
            reserved_notional=Decimal("0"),
            reserved_daily_loss=Decimal("0"),
            reserved_trade_slots=0,
            actual_notional=Decimal("0"),
            actual_daily_loss=Decimal("0"),
            actual_trade_count=0,
            symbol_reserved={},
            symbol_actual={},
            max_notional=Decimal("200000"),
            max_daily_loss=Decimal("5000"),
            max_trade_slots=20,
            max_symbol_notional=Decimal("200000"),
            daily_locked=False,
            exposure_unit="USDT",
            version=1,
        )
    )


def demo_settings(account_id: str, **updates: object) -> Settings:
    values = dict(  # noqa: C408
        _env_file=None,
        environment="local",
        provider_mode="mock",
        execution_mode="paper",
        enable_real_trading=False,
        exchange_mode="paper_exchange_demo",
        blofin_demo_enabled=True,
        blofin_live_evidence_demo_enabled=False,
        governed_blofin_demo_enabled=True,
        watcher_orchestration_enabled=True,
        watcher_paper_staging_activation=True,
        governed_blofin_demo_organization_id=str(ORG),
        governed_blofin_demo_user_id=str(USER),
        governed_blofin_demo_account_id=account_id,
        blofin_api_key="simulated-key",
        blofin_api_secret="simulated-secret",
        blofin_api_passphrase="simulated-pass",
        blofin_demo_rest_base_url="https://demo-trading-openapi.blofin.com",
        perpetual_evidence_source="replay",
        jwt_secret="simulated-demo-jwt-secret-32-minimum",
    )
    values.update(updates)
    return Settings(**values).model_copy(
        update={
            "environment": Environment.STAGING,
            "perpetual_evidence_source": "binance_usdm",
            "blofin_live_evidence_demo_enabled": True,
        }
    )


def test_demo_gate_requires_separate_arm_and_exact_principal_pins() -> None:
    settings = demo_settings(str(uuid4()))
    assert blofin_execution_authorized(settings)
    assert governed_demo_worker_access_requested(settings)
    for updates in (
        {"governed_blofin_demo_enabled": False},
        {"governed_blofin_demo_user_id": ""},
        {"watcher_paper_staging_activation": False},
        {"environment": "production"},
    ):
        assert not governed_demo_worker_access_requested(settings.model_copy(update=updates))


@requires_postgres
@pytest.mark.parametrize(
    "behavior",
    [
        "success",
        "timeout",
        "restart",
        "rejected",
        "unfilled",
        "protection_failure",
        "protection_outage",
        "partial",
        "partial_cancel",
        "permissions",
        "basis",
        "stale",
        "expiry",
        "protection_wrongid",
        "protection_malformed",
        "protection_unrelated_malformed",
        "preflight_kill",
        "preflight_fence",
        "leverage_above_ceiling",
    ],
)
def test_worker_venue_fills_journal_restart_and_protection(
    monkeypatch: pytest.MonkeyPatch, behavior: str
) -> None:
    factory = phase7_plan_session_factory()
    with factory() as session:
        _seed(session)
        account = session.scalar(select(ExecutionAccount))
        assert account is not None
        settings = demo_settings(str(account.id))
        seed_band_ledger_headroom(session, account.id)
        session.commit()
    source = _live_source()
    from tests.support.phase6_evaluator import build_pattern_15m_bars, build_slice_trades

    price = build_slice_trades(build_pattern_15m_bars())[-1].price
    venue = Venue(
        price,
        behavior,
        account_leverage="25" if behavior == "leverage_above_ceiling" else "10",
    )

    def late_safety_change() -> None:
        with factory() as session:
            if behavior == "preflight_kill":
                KillSwitchService(session, AuditService(session), settings).activate(
                    organization_id=ORG,
                    actor_user_id=USER,
                    payload=KillSwitchMutationRequest(
                        confirm=True, reason="Simulated operator stop"
                    ),
                )
            else:
                effect = session.scalar(select(VenueSubmitEffect))
                assert effect is not None
                effect.dispatch_fencing_token = int(effect.dispatch_fencing_token) + 1
            session.commit()

    if behavior in {"preflight_kill", "preflight_fence"}:
        venue.after_preflight = late_safety_change
    client = BloFinClient(
        base_url=settings.blofin_demo_rest_base_url,
        api_key="simulated-key",
        api_secret="simulated-secret",
        api_passphrase="simulated-pass",
        max_retries=2,
        transport=httpx.MockTransport(venue.handle),
        sleeper=lambda _: None,
    )
    provider = GovernedBloFinDemoProvider(
        client, clock=lambda: EVALUATED_AT + timedelta(seconds=venue.clock_delta)
    )
    monkeypatch.setattr(GovernedBloFinDemoLoop, "_get_provider", lambda _: provider)
    monitor = _monitor(source, replay=False, clock=lambda: EVALUATED_AT)
    held: list = []
    runtime = _runtime_for(factory, source, monitor, replay=False, held=held)
    runtime._settings = settings
    cycle = runtime.run_cycle()
    assert cycle.scans
    scan = cycle.scans[0]
    assert scan.candidate_ids, scan.reason_code
    if behavior == "leverage_above_ceiling":
        # 25x demo account above the 20x ceiling: refused before any plan or POST.
        assert scan.paper_loop_reason == "demo_leverage_above_ceiling"
        assert scan.trade_plan_revision_id is None
        assert venue.post_count == 0
        return
    if behavior in {"permissions", "basis", "stale", "expiry", "preflight_kill", "preflight_fence"}:
        assert venue.post_count == 0
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 0
            assert session.scalar(select(func.count()).select_from(JournalTrade)) == 0
        return
    assert scan.trade_plan_revision_id is not None, scan.paper_loop_reason
    assert venue.post_count == 1, scan.paper_loop_reason
    if behavior == "restart":
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 0
            assert session.scalar(select(func.count()).select_from(JournalTrade)) == 0
            KillSwitchService(session, AuditService(session), settings).activate(
                organization_id=ORG,
                actor_user_id=USER,
                payload=KillSwitchMutationRequest(
                    confirm=True, reason="Recovery under operator stop"
                ),
            )
            session.commit()
        # A new worker with no bound scan clock recovers before a source gate refusal.
        runtime = _runtime_for(factory, source, monitor, replay=False, held=[])
        runtime._settings = settings
        runtime._activation_gate = lambda: SimpleNamespace(
            allowed=False, primary_reason="source_unavailable"
        )
        assert runtime.run_cycle().reason_code == "source_unavailable"
    with factory() as session:
        command = session.scalar(select(ExecutionCommand))
        assert command is not None
        loop = GovernedBloFinDemoLoop(
            runtime._canonical_runtime, settings, FrozenClock(EVALUATED_AT), provider=provider
        )
        loop.reconcile_pending(session)
        assert venue.post_count == 1
        fill_count = session.scalar(select(func.count()).select_from(ExecutionFillFact))
        trade_count = session.scalar(select(func.count()).select_from(JournalTrade))
        if behavior in {"rejected", "unfilled"}:
            assert fill_count == trade_count == 0
        else:
            assert fill_count == trade_count == 1
            trade = session.scalar(select(JournalTrade))
            assert trade is not None
            assert trade.entry_time == EVALUATED_AT
            assert trade.entry_price == price
            assert trade.candidate_id == scan.candidate_ids[0]
            multiplier = (
                Decimal("0.0005") if behavior in {"partial", "partial_cancel"} else Decimal("0.001")
            )
            assert trade.size == Decimal(venue.order["size"]) * multiplier
            assert trade.fees == Decimal("0.02")
            assert trade.net_pnl is None
            projection = session.scalar(select(ExecutionProjection))
            assert projection is not None and projection.fees == trade.fees
            assert scan.paper_fill_id is not None or behavior == "restart"
            learning = session.scalar(select(LearningAttributionRecordRow))
            assert learning is not None
            assert learning.learning_venue_mode == "paper_exchange_demo"
            assert learning.journal_trade_id == trade.id
            assert not learning.closed
            if behavior == "partial_cancel":
                reservation = session.scalar(select(RiskReservation))
                assert reservation is not None
                assert reservation.remaining_reserved_notional == 0
                assert projection.remaining_quantity == 0
            if behavior == "success":
                service = ExecutionService(
                    session,
                    settings,
                    AuditService(session),
                    canonical_runtime=runtime._canonical_runtime,
                )
                with pytest.raises(TradingPolicyError, match="governed venue reconciliation"):
                    service.apply_paper_plan_fill(
                        command_id=command.id,
                        fill_quantity=Decimal("1"),
                        fill_price=price,
                        source_identity="synthetic-forbidden",
                        occurred_at=EVALUATED_AT,
                    )
                with pytest.raises(TradingPolicyError, match="actual exchange exit evidence"):
                    service.close_canonical_paper_plan(
                        ClosePaperPlanRequest(
                            organization_id=ORG,
                            user_id=USER,
                            account_id=command.account_id,
                            revision_id=command.revision_id,
                            command_id=command.id,
                            exit_price=price,
                            occurred_at=EVALUATED_AT,
                            exit_reason="synthetic-forbidden",
                            idempotency_key="synthetic-forbidden",
                        )
                    )
        if behavior == "success":
            from app.interactive_agent.actions import RecordedTradeInput
            from app.interactive_agent.recorded_trade import read_recorded_trade

            natural = read_recorded_trade(
                session,
                RecordedTradeInput(symbol="BTCUSDT", direction="short", latest=True),
                organization_id=ORG,
                user_id=USER,
            )
            assert "BloFin demo (actual recorded venue fills)" in natural.reply
            assert "internal paper simulator" not in natural.reply
            assert str(trade.id) in {ref.record_id for ref in natural.connections}
            assert "detailed captured RiskEngine decision" in natural.reply
            assert venue.post_count == 1
        if behavior in {
            "protection_failure",
            "protection_outage",
            "protection_wrongid",
            "protection_malformed",
        }:
            switch = session.scalar(select(KillSwitchState))
            assert switch is not None and switch.active
        if behavior == "rejected":
            effect = session.scalar(select(VenueSubmitEffect))
            assert effect is not None and effect.state is VenueSubmitEffectState.SEND_ATTEMPTED
            assert effect.reconciliation_disposition == "REJECTED"
        venue_counts = (venue.post_count, venue.reads, venue.ticker_reads)
        assert session.scalar(select(func.count()).select_from(ConversationMessage)) == 0
        explained = read_paper_execution(
            session, settings=settings, organization_id=ORG, user_id=USER, command_id=command.id
        )
        assert explained.source_message_id is None
        assert "Recorded BloFin demo command" in explained.recorded_evidence
        if fill_count:
            assert "Actual demo fill" in explained.recorded_evidence
            assert f"recorded fees {trade.fees}" in explained.recorded_evidence
            assert "net PnL unavailable" in explained.recorded_evidence
            assert "venue paper_exchange_demo" in explained.recorded_evidence
            assert "closed=False" in explained.recorded_evidence
            protection = (
                "unavailable"
                if behavior in {"protection_outage", "protection_malformed"}
                else (
                    "missing"
                    if behavior in {"protection_failure", "protection_wrongid"}
                    else "verified"
                )
            )
            assert (
                f"Protection evidence: {protection} at reconciliation"
                in explained.recorded_evidence
            )
        else:
            assert "no recorded exchange fill evidence" in explained.reply
            assert "Journal unavailable" in explained.recorded_evidence
        for organization, user in ((uuid4(), USER), (ORG, uuid4())):
            with pytest.raises(NotFoundError):
                read_paper_execution(
                    session,
                    settings=settings,
                    organization_id=organization,
                    user_id=user,
                    command_id=command.id,
                )
        result = InteractiveAgentService(session, settings=settings).handle_turn(
            AgentTurnRequest(message=f"Explain paper execution {command.id}"),
            organization_id=ORG,
            user_id=USER,
        )
        assert result.recorded_evidence == explained.recorded_evidence
        assert not result.authority_mutated and not result.execution_attempted
        assert not result.proposals
        assistant = session.get(ConversationMessage, result.assistant_message_id)
        assert assistant is not None and "paper_execution" not in assistant.payload
        capture = assistant.payload["interactive_agent"]["paper_execution_explanation"]
        assert capture["source_message_id"] is None
        assert (venue.post_count, venue.reads, venue.ticker_reads) == venue_counts
        assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == fill_count
        assert session.scalar(select(func.count()).select_from(JournalTrade)) == trade_count


def test_unreadable_liquidation_is_not_labeled_inside_the_stop(monkeypatch) -> None:
    armed: list[str] = []
    alerts: list[tuple[str, str]] = []

    class _Switch:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        def activate(self, **kwargs: object) -> None:
            payload = kwargs["payload"]
            armed.append(str(getattr(payload, "reason", "")))

    class _Alerts:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        def create(self, **kwargs: object) -> None:
            alerts.append((str(kwargs["alert_type"]), str(kwargs["severity"])))

    warnings: list[str] = []

    def _warn(message: str, *args: object) -> None:
        warnings.append(message % args if args else message)

    monkeypatch.setattr("app.services.governed_blofin_demo.KillSwitchService", _Switch)
    monkeypatch.setattr("app.services.governed_blofin_demo.PaperAlertService", _Alerts)
    monkeypatch.setattr("app.services.governed_blofin_demo.AuditService", lambda _session: None)
    monkeypatch.setattr("app.services.governed_blofin_demo.logger.warning", _warn)
    disposition, arm = profile_liquidation_hold(
        liquidation_reason="demo_post_fill_liquidation_unreadable",
        protected=False,
    )
    assert disposition == "DEMO_LIQUIDATION_UNREADABLE"
    assert arm is True
    inside, armed_inside = profile_liquidation_hold(
        liquidation_reason="demo_post_fill_liquidation_inside_stop",
        protected=False,
    )
    assert inside == "DEMO_LIQUIDATION_INSIDE_STOP"
    assert armed_inside is True
    protected_disposition, protected_arm = profile_liquidation_hold(
        liquidation_reason="demo_post_fill_liquidation_unreadable",
        protected=True,
    )
    assert protected_disposition == "DEMO_LIQUIDATION_UNREADABLE"
    assert protected_arm is False
    through, through_arm = profile_liquidation_hold(
        liquidation_reason="demo_post_fill_liquidation_inside_stop",
        protected=True,
    )
    assert through == "DEMO_LIQUIDATION_INSIDE_STOP"
    assert through_arm is True
    with pytest.raises(ValueError, match="Unknown demo liquidation hold"):
        profile_liquidation_hold(liquidation_reason="other", protected=False)

    loop = object.__new__(GovernedBloFinDemoLoop)
    loop._settings = SimpleNamespace()
    effect = SimpleNamespace(reconciliation_disposition=None)
    command = SimpleNamespace(id=uuid4(), organization_id=uuid4(), user_id=uuid4())
    loop._hold_profile_liquidation(
        object(),  # type: ignore[arg-type]
        command=command,  # type: ignore[arg-type]
        effect=effect,  # type: ignore[arg-type]
        liquidation_reason="demo_post_fill_liquidation_unreadable",
        protected=False,
    )
    assert effect.reconciliation_disposition == "DEMO_LIQUIDATION_UNREADABLE"
    assert armed == ["BloFin demo protection missing; operator action required"]
    assert alerts == []
    assert warnings and "unreadable" in warnings[-1]
    loop._hold_profile_liquidation(
        object(),  # type: ignore[arg-type]
        command=command,  # type: ignore[arg-type]
        effect=effect,  # type: ignore[arg-type]
        liquidation_reason="demo_post_fill_liquidation_unreadable",
        protected=True,
    )
    assert effect.reconciliation_disposition == "DEMO_LIQUIDATION_UNREADABLE"
    assert armed == ["BloFin demo protection missing; operator action required"]
    assert alerts == []
    assert "protected=True" in warnings[-1]
    loop._hold_profile_liquidation(
        object(),  # type: ignore[arg-type]
        command=command,  # type: ignore[arg-type]
        effect=effect,  # type: ignore[arg-type]
        liquidation_reason="demo_post_fill_liquidation_inside_stop",
        protected=True,
    )
    assert effect.reconciliation_disposition == "DEMO_LIQUIDATION_INSIDE_STOP"
    assert armed == [
        "BloFin demo protection missing; operator action required",
        "BloFin demo liquidation is through the stop; operator action required",
    ]
    assert alerts == [("demo_liquidation_through_stop", "critical")]


def test_liquidation_hold_runs_before_exit_reconciliation() -> None:
    import inspect

    source = inspect.getsource(GovernedBloFinDemoLoop.reconcile_command)
    hold = source.index("_hold_profile_liquidation")
    exit_read = source.index("reconcile_exit")
    assert hold < exit_read
    from app.services.governed_blofin_demo import _alert_liquidation_through_stop

    helper = inspect.getsource(GovernedBloFinDemoLoop._hold_profile_liquidation)
    alert = inspect.getsource(_alert_liquidation_through_stop)
    assert "KillSwitchService" in helper
    assert "_alert_liquidation_through_stop" in helper
    assert "trade/order" not in helper
    assert "setLeverage" not in helper
    assert "set-leverage" not in helper
    assert "trade/order" not in alert
    assert "set-leverage" not in alert
    assert "No automatic close" in alert


def test_funding_read_uses_the_live_print_and_not_the_mock_fallback() -> None:
    from app.providers.exchange.governed_blofin import GovernedBloFinDemoProvider

    provider = object.__new__(GovernedBloFinDemoProvider)

    class _Down:
        def request(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("funding unavailable")

    provider._client = _Down()  # type: ignore[attr-defined]
    assert provider.read_funding_rate(symbol="BTCUSDT") is None

    class _Live:
        def request(self, method: str, path: str, params: dict[str, str] | None = None) -> object:
            assert method == "GET"
            assert path == "/api/v1/market/funding-rate"
            assert params is not None and params["instId"]
            return [{"fundingRate": "0.00025"}]

    provider._client = _Live()  # type: ignore[attr-defined]
    assert provider.read_funding_rate(symbol="BTCUSDT") == Decimal("0.00025")


def test_position_tier_min_size_stays_quote_notional() -> None:
    from app.schemas.trade_plan import EntrySide

    venue = Venue(Decimal("100"), tier_min_size="2500")
    client = BloFinClient(
        base_url="https://demo-trading-openapi.blofin.com",
        api_key="simulated-key",
        api_secret="simulated-secret",
        api_passphrase="simulated-pass",
        transport=httpx.MockTransport(venue.handle),
    )
    provider = GovernedBloFinDemoProvider(client, clock=lambda: EVALUATED_AT)
    snapshot = provider.snapshot(
        symbol="BTCUSDT",
        now=EVALUATED_AT,
        side=EntrySide.BUY,
        required_leverage=None,
    )
    floors = [floor for floor, _rate in snapshot.maintenance_tiers]
    assert floors == [Decimal("2500")]
    scaled = Decimal("2500") * snapshot.multiplier * snapshot.price
    assert scaled != Decimal("2500")
    assert scaled not in floors


def test_staging_settings_require_complete_scoped_demo_arm() -> None:
    from app.market_activation.profile import controlled_watcher_arm
    from tests.test_execution_credential_isolation import _STAGING

    values = {
        **_STAGING,
        "_env_file": None,
        "blofin_demo_enabled": True,
        "exchange_mode": "paper_exchange_demo",
        "blofin_demo_rest_base_url": "https://demo-trading-openapi.blofin.com",
        "governed_blofin_demo_enabled": True,
        "watcher_orchestration_enabled": True,
        "watcher_paper_staging_activation": True,
        "governed_blofin_demo_organization_id": str(ORG),
        "governed_blofin_demo_user_id": str(USER),
        "governed_blofin_demo_account_id": str(uuid4()),
    }
    settings = Settings(**values)
    assert controlled_watcher_arm(settings)
    assert blofin_execution_authorized(settings)
    with pytest.raises(ValueError):
        Settings(**{**values, "governed_blofin_demo_enabled": False})
    with pytest.raises(ValueError):
        Settings(**{**values, "governed_blofin_demo_user_id": ""})


def test_governed_demo_loop_refuses_sfp_before_planning() -> None:
    from types import SimpleNamespace
    from uuid import uuid4

    from tests.test_sfp_detector import spec as sfp_spec

    loop = GovernedBloFinDemoLoop(None, None, SimpleNamespace(now=lambda: EVALUATED_AT))
    proof = loop._bound(
        None,
        target=SimpleNamespace(organization_id=ORG, user_id=USER),
        candidate=SimpleNamespace(candidate_id=uuid4(), correlation_id=None),
        assessment=SimpleNamespace(),
        window=SimpleNamespace(),
        assembled=SimpleNamespace(),
        policy=SimpleNamespace(authored_spec=sfp_spec()),
        account=SimpleNamespace(id=uuid4()),
    )
    assert proof.stage == "skipped"
    assert proof.reason_code == "sfp_execution_plan_not_authorized"


# --- Fresh demo quote at eligibility (worker memory fix, item 6) -------------


def _scan_market(valid_until: object) -> object:
    from app.market_contracts.enums import VenueId
    from app.signal_fusion.action_eligibility import MarketActionEvidence

    return MarketActionEvidence(
        venue_state_id=uuid4(),
        evidence_venue=VenueId.BINANCE,
        execution_venue=VenueId.BINANCE,
        evidence_price=Decimal("100"),
        execution_price=Decimal("100"),
        action_evidence_valid_until=valid_until,
        symbol="BTCUSDT",
    )


def test_fresh_demo_quote_is_the_action_window_when_the_scan_quote_aged() -> None:
    from app.services.governed_blofin_demo import demo_action_evidence
    from app.signal_fusion.action_eligibility import ActionEligibilityService

    now = EVALUATED_AT + timedelta(seconds=12)
    # The scan quote passed its gate at preflight, then aged past 10s.
    market = _scan_market(EVALUATED_AT + timedelta(seconds=10))
    book = SimpleNamespace(
        instrument="BTC-USDT", price=Decimal("100.05"), observed_at=now - timedelta(seconds=1)
    )
    evidence = demo_action_evidence(market, book)  # type: ignore[arg-type]
    assert evidence.action_evidence_valid_until == book.observed_at + timedelta(seconds=10)
    assert evidence.execution_price == Decimal("100.05")
    assert evidence.evidence_price == Decimal("100")
    command = SimpleNamespace(market_action=evidence)
    assert ActionEligibilityService._action_evidence_stale(command, now) is False  # type: ignore[arg-type]
    # A demo book that is itself older than 10s still blocks as data quality.
    old = SimpleNamespace(
        instrument="BTC-USDT", price=Decimal("100"), observed_at=now - timedelta(seconds=10)
    )
    stale = SimpleNamespace(market_action=demo_action_evidence(market, old))  # type: ignore[arg-type]
    assert ActionEligibilityService._action_evidence_stale(stale, now) is True  # type: ignore[arg-type]


class _RefreshingProvider:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail = fail

    def snapshot(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("venue down")
        return SimpleNamespace(observed_at=kwargs["now"], price=Decimal("101"))


def _refresh_loop(
    provider: _RefreshingProvider, observed_at: object, *, received: float | None = 0.0
) -> tuple[object, list[float]]:
    """Loop holding a preflight book received at monotonic ``received``."""

    mono = [0.0]
    loop = GovernedBloFinDemoLoop(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        SimpleNamespace(now=lambda: EVALUATED_AT),  # type: ignore[arg-type]
        provider=provider,  # type: ignore[arg-type]
    )
    loop._monotonic = lambda: mono[0]  # type: ignore[method-assign]
    loop._snapshot = SimpleNamespace(observed_at=observed_at, price=Decimal("100"))  # type: ignore[assignment]
    loop._snapshot_received_at = received
    loop._snapshot_request = {"symbol": "BTCUSDT", "side": "BUY", "margin_mode": "cross"}
    return loop, mono


def test_aged_preflight_quote_is_read_again_right_before_eligibility() -> None:
    provider = _RefreshingProvider()
    now = EVALUATED_AT + timedelta(seconds=6)
    loop, mono = _refresh_loop(provider, EVALUATED_AT)
    mono[0] = 6.0
    fresh = loop._action_time_snapshot(now)  # type: ignore[attr-defined]
    assert fresh.observed_at == now and fresh.price == Decimal("101")
    assert loop._snapshot is fresh  # type: ignore[attr-defined]
    assert loop._snapshot_received_at == 6.0  # type: ignore[attr-defined]
    assert provider.calls == [
        {"now": now, "symbol": "BTCUSDT", "side": "BUY", "margin_mode": "cross"}
    ]


def test_recent_preflight_quote_is_not_read_again() -> None:
    provider = _RefreshingProvider()
    loop, mono = _refresh_loop(provider, EVALUATED_AT)
    mono[0] = 1.9
    kept = loop._action_time_snapshot(EVALUATED_AT + timedelta(seconds=1))  # type: ignore[attr-defined]
    assert kept.price == Decimal("100")
    assert provider.calls == []


def test_failed_quote_refresh_keeps_the_old_quote_so_eligibility_blocks_it() -> None:
    provider = _RefreshingProvider(fail=True)
    loop, mono = _refresh_loop(provider, EVALUATED_AT)
    mono[0] = 11.0
    kept = loop._action_time_snapshot(EVALUATED_AT + timedelta(seconds=11))  # type: ignore[attr-defined]
    assert kept.observed_at == EVALUATED_AT
    assert len(provider.calls) == 1


def test_quote_refresh_uses_local_receipt_time_not_skewed_venue_time() -> None:
    # Local wall clock 5s ahead of the venue: the book looks 5s old on arrival.
    provider = _RefreshingProvider()
    loop, mono = _refresh_loop(provider, EVALUATED_AT - timedelta(seconds=5), received=100.0)
    mono[0] = 100.5
    kept = loop._action_time_snapshot(EVALUATED_AT)  # type: ignore[attr-defined]
    assert kept.price == Decimal("100")
    assert provider.calls == []
    # Venue clock ahead of local (book stamped after local now), but received 3s ago.
    ahead, mono = _refresh_loop(provider, EVALUATED_AT + timedelta(seconds=1), received=100.0)
    mono[0] = 103.0
    fresh = ahead._action_time_snapshot(EVALUATED_AT)  # type: ignore[attr-defined]
    assert fresh.price == Decimal("101")
    assert len(provider.calls) == 1


def test_unknown_receipt_time_reads_the_quote_again() -> None:
    provider = _RefreshingProvider()
    loop, _mono = _refresh_loop(provider, EVALUATED_AT, received=None)
    loop._action_time_snapshot(EVALUATED_AT)  # type: ignore[attr-defined]
    assert len(provider.calls) == 1


def test_preflight_snapshot_records_local_monotonic_receipt_time() -> None:
    import inspect

    source = inspect.getsource(GovernedBloFinDemoLoop._bound)
    read = source.index("self._snapshot = self._get_provider().snapshot(")
    stamp = source.index("self._snapshot_received_at = self._monotonic()")
    assert read < stamp < source.index("except Exception", read)


def test_loop_eligibility_uses_the_sixty_second_default_window(monkeypatch) -> None:
    """Sofien's choice (2026-11-04): the governed demo action window defaults to 60s.

    Drives GovernedBloFinDemoLoop._eligibility_command with default Settings, so a
    default of 10s or 3600s (clamped to the 300s ceiling) fails here.
    """
    import app.services.governed_blofin_demo as demo_module
    from app.providers.exchange.demo_quote import DemoQuoteTimingError, quote_time_at_receipt
    from app.signal_fusion.action_eligibility import ActionEligibilityService

    settings = Settings(_env_file=None)
    assert settings.governed_demo_plan_valid_seconds == 60
    now = EVALUATED_AT + timedelta(seconds=1)
    market = _scan_market(EVALUATED_AT + timedelta(seconds=10))

    class _Command(SimpleNamespace):
        def model_copy(self, *, update: dict[str, object]) -> SimpleNamespace:
            return SimpleNamespace(**{**vars(self), **update})

    monkeypatch.setattr(
        demo_module, "_eligibility_command", lambda *a, **k: _Command(market_action=market)
    )
    provider = _RefreshingProvider()
    loop, mono = _refresh_loop(provider, now)
    loop._snapshot = SimpleNamespace(  # type: ignore[attr-defined]
        instrument="BTC-USDT", observed_at=now, price=Decimal("100")
    )
    mono[0] = 0.5  # recent receipt, no re-read
    command = loop._eligibility_command(  # type: ignore[attr-defined]
        None,
        settings=settings,
        target=None,
        candidate=None,
        assessment=None,
        window=None,
        assembled=None,
        account=None,
        now=now,
    )
    evidence = command.market_action
    assert evidence.action_evidence_valid_until == now + timedelta(seconds=60)
    assert provider.calls == []
    stale = ActionEligibilityService._action_evidence_stale
    assert stale(command, now + timedelta(seconds=59)) is False  # type: ignore[arg-type]
    assert stale(command, now + timedelta(seconds=60)) is True  # type: ignore[arg-type]

    # Orders still need a demo quote under 10s old at receipt.
    base_ms = 1_800_000_000_000
    received = datetime_from_ms(base_ms)
    assert quote_time_at_receipt(base_ms - 9_999, received) == datetime_from_ms(base_ms - 9_999)
    with pytest.raises(DemoQuoteTimingError):
        quote_time_at_receipt(base_ms - 10_000, received)


def datetime_from_ms(ms: int):
    from datetime import UTC, datetime

    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=ms)
