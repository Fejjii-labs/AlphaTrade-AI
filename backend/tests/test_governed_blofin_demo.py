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
from app.services.governed_blofin_demo import GovernedBloFinDemoLoop
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
        elif path.endswith("leverage-info"):
            data = {"instId": "BTC-USDT", "marginMode": "cross", "leverage": "1"}
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
        autonomous_blofin_demo_enabled=True,
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
    source = _live_source()
    from tests.support.phase6_evaluator import build_pattern_15m_bars, build_slice_trades

    price = build_slice_trades(build_pattern_15m_bars())[-1].price
    venue = Venue(price, behavior)

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
