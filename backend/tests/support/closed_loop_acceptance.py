"""Deterministic market IO only; every decision and write uses product authorities.

The fixture emulates the contracted public REST input at a fixed clock. Its
``is_live`` metadata exercises the live adapter contract, not a claim that this
test contacted Binance. No HTTP request leaves MockTransport.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.persistence_firewall import install_persistence_firewall
from app.db.models import ExecutionAccount, Membership, Organization, User
from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.evidence_pipeline.service import CanonicalEvidenceService
from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_monitor.monitor import PerpetualMarketMonitor
from app.market_monitor.watcher_port import MarketMonitorWatcherPort
from app.persistence.setup_lifetime import SqlAlchemySetupLifetimeStore
from app.runtime.canonical import ProductionCanonicalRuntime, build_production_canonical_runtime
from app.schemas.common import MembershipRole, Timeframe
from app.schemas.trade_plan import AccountMode, ExecutionMode
from app.services.agent_service import AgentInvokeContext, AgentService, build_agent_service
from app.services.automated_paper_loop import _eligibility_command
from app.workers.watcher_market import WatchlistContractDiscovery
from app.workers.watcher_paper import WatcherPaperScanReport, build_watcher_paper_runtime
from app.workers.watcher_paper_targets import list_paper_scan_targets
from tests.support.phase5_market import EVALUATED_AT, trade
from tests.support.phase6_evaluator import (
    build_context_4h_bars,
    build_pattern_15m_bars,
    build_slice_trades,
)
from tests.support.postgres_persistence import POSTGRES_URL, phase7_plan_session_factory
from tests.test_live_evidence_pipeline import _agg_row, _kline_row
from tests.test_watcher_paper_runtime import (
    ORG,
    ORG_B,
    USER,
    USER_B,
    _seed_approved_compiled,
    _settings,
)
from tests.test_watcher_product_proof_postgres import _persist_resistance

ACCOUNT = UUID("00000000-0000-0000-0000-00000000a091")


@dataclass
class AcceptanceClock:
    moment: datetime = EVALUATED_AT

    def now(self) -> datetime:
        return self.moment


class FixtureMarket:
    def __init__(self, *, failure: str | None = None) -> None:
        bars = build_pattern_15m_bars()
        self.klines = {
            "15m": [_kline_row(bar) for bar in bars],
            "4h": [_kline_row(bar) for bar in build_context_4h_bars()],
        }
        events = build_slice_trades(bars)
        events.append(
            trade(
                sequence=events[-1].sequence + 1,
                price="100080",
                quantity="0.001",
                buyer_is_maker=True,
                event_time=EVALUATED_AT - timedelta(seconds=1),
                receive_at=EVALUATED_AT,
            )
        )
        self.trades = [_agg_row(event) for event in events]
        self.failure = failure
        self.requests: list[str] = []
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET", "Fixture must never receive a mutation."
        assert request.url.host == "fapi.binance.com" or (
            request.url.host == "www.binance.com" and request.url.path == "/fapi/v1/exchangeInfo"
        )
        path, params = request.url.path, request.url.params
        self.requests.append(path)
        if path == "/fapi/v1/ping":
            return httpx.Response(200, json={})
        if path == "/fapi/v1/exchangeInfo":
            return httpx.Response(
                200,
                json={
                    "futuresType": "U_MARGINED",
                    "symbols": [
                        {
                            "symbol": "BTCUSDT",
                            "baseAsset": "BTC",
                            "quoteAsset": "USDT",
                            "contractType": "PERPETUAL",
                            "status": "TRADING",
                        }
                    ],
                },
            )
        if path == "/fapi/v1/klines":
            return httpx.Response(200, json=self.klines[params["interval"]])
        if path == "/fapi/v1/depth":
            return httpx.Response(
                200,
                json={
                    "lastUpdateId": 1,
                    "E": int(EVALUATED_AT.timestamp() * 1000),
                    "T": int(EVALUATED_AT.timestamp() * 1000),
                    "bids": [["100079", "1"]],
                    "asks": [["100081", "1"]],
                },
            )
        if path == "/fapi/v1/aggTrades":
            if self.failure == "missing":
                return httpx.Response(200, json=[])
            rows = self.trades
            if self.failure == "stale":
                rows = [
                    row for row in rows if row["T"] < int(EVALUATED_AT.timestamp() * 1000) - 15000
                ]
            if "fromId" in params:
                rows = [row for row in rows if row["a"] >= int(params["fromId"])]
            elif "startTime" in params:
                rows = [
                    row
                    for row in rows
                    if int(params["startTime"]) <= row["T"] <= int(params["endTime"])
                ]
            return httpx.Response(200, json=rows[: int(params.get("limit", "1000"))])
        if path == "/fapi/v1/openInterest":
            return httpx.Response(
                200,
                json={
                    "symbol": "BTCUSDT",
                    "openInterest": "10",
                    "time": int(EVALUATED_AT.timestamp() * 1000),
                },
            )
        if path == "/fapi/v1/fundingRate":
            return httpx.Response(
                200,
                json=[
                    {
                        "symbol": "BTCUSDT",
                        "fundingRate": "0",
                        "fundingTime": int(EVALUATED_AT.timestamp() * 1000),
                    }
                ],
            )
        raise AssertionError(f"Uncontracted fixture request: {path}")


@dataclass
class ClosedLoopWorld:
    factory: sessionmaker[Session]
    settings: Settings
    clock: AcceptanceClock
    market: FixtureMarket
    source: BinanceUsdmPerpetualSource
    monitor: PerpetualMarketMonitor
    runtime: ProductionCanonicalRuntime
    ports: list[AssemblingWatcherScanEvidence] = field(default_factory=list)

    def scan(self, *, evidence_factory=None) -> WatcherPaperScanReport:
        def factory(session, store, symbol):
            port = AssemblingWatcherScanEvidence(
                FirstSliceEvidenceAssembler(
                    self.source,
                    replay=False,
                    lifetime=SqlAlchemySetupLifetimeStore(session),
                    clock=self.clock.now,
                ),
                session=session,
                watcher_store=store,
                symbol=symbol,
                monitor=MarketMonitorWatcherPort(self.monitor),
            )
            self.ports.append(port)
            return port

        resolved = evidence_factory or factory
        resolved.discovery = WatchlistContractDiscovery(
            self.settings, transport=self.market.transport
        )
        watcher = build_watcher_paper_runtime(
            self.settings,
            self.factory,
            enabled=True,
            evidence_factory=resolved,
            clock=self.clock,
            monitor=self.monitor,
        )
        cycle = watcher.run_cycle()
        assert len(cycle.scans) == 1, cycle
        return cycle.scans[0]

    def enable_account_and_eligibility(self, scan: WatcherPaperScanReport):
        # The scan runs without an execution account, so the normal automatic
        # continuation stops at execution_account_missing. The interactive
        # account then requires its own explicit presented confirmation.
        assert scan.paper_loop_reason == "execution_account_missing"
        with self.factory() as session:
            account = ExecutionAccount(
                id=ACCOUNT,
                organization_id=ORG,
                user_id=USER,
                name="Acceptance paper account",
                execution_mode=ExecutionMode.PAPER,
                account_mode=AccountMode.NET,
            )
            session.add(account)
            session.flush()
            target = list_paper_scan_targets(
                session, symbols=["BTCUSDT"], organization_id=ORG, limit=1
            )[0]
            assembled, _policy = self.ports[-1].last_assembly()
            discussion = scan.discussion
            with self.runtime.bind_session(session):
                evaluation = self.runtime.eligibility.evaluate(
                    _eligibility_command(
                        session,
                        settings=self.settings,
                        target=target,
                        candidate=discussion.candidate,
                        assessment=discussion.assessment,
                        window=discussion.window,
                        assembled=assembled,
                        account=account,
                        now=self.clock.now(),
                    )
                )
            session.commit()
            return evaluation

    def agent(self, session: Session) -> AgentService:
        return build_agent_service(
            settings=self.settings,
            session=session,
            canonical_runtime=self.runtime,
            canonical_evidence=CanonicalEvidenceService(
                self.settings,
                source=self.source,
                clock=self.clock.now,
                session=session,
            ),
        )

    def prepare_message(self, scan: WatcherPaperScanReport) -> str:
        return "Prepare paper trade " + json.dumps(
            {
                "candidate_id": str(scan.candidate_ids[0]),
                "account_id": str(ACCOUNT),
                "symbol": "BTCUSDT",
                "venue": "binance",
                "market": "PERPETUAL",
                "timeframe": Timeframe.M15.value,
                "direction": "short",
                "entry": "100080",
                "stop": "100240",
                "targets": ["99920", "99760"],
            }
        )


def agent_turn(
    service: AgentService, message: str, *, conversation_id=None, organization_id=ORG, user_id=USER
):
    from uuid import uuid4

    return service.run(
        message,
        AgentInvokeContext(
            request_id=str(uuid4()),
            organization_id=organization_id,
            user_id=user_id,
            conversation_id=UUID(conversation_id) if conversation_id else None,
        ),
    )


def build_world(*, failure: str | None = None) -> ClosedLoopWorld:
    install_persistence_firewall()
    factory = phase7_plan_session_factory()
    # Use the existing strategy-library/version/compiler/lifecycle authorities.
    # An account is enrolled after discovery, before eligibility and confirmation.
    with factory() as session:
        session.add_all(
            [
                Organization(id=ORG, name="Closed loop acceptance"),
                Organization(id=ORG_B, name="Other acceptance tenant"),
                User(id=USER_B, email="other-loop@test.example", hashed_password="fixture-only"),
                User(id=USER, email="closed-loop@test.example", hashed_password="fixture-only"),
            ]
        )
        session.flush()
        session.add_all(
            [
                Membership(organization_id=ORG, user_id=USER, role=MembershipRole.OWNER),
                Membership(organization_id=ORG_B, user_id=USER_B, role=MembershipRole.OWNER),
            ]
        )
        session.commit()
        _seed_approved_compiled(session)
        _persist_resistance(session)
    settings = _settings(
        database_url=POSTGRES_URL,
        exchange_mode="paper_internal",
        global_kill_switch_active=False,
        watcher_orchestration_enabled=False,
        watcher_paper_organization_id=str(ORG),
        perpetual_evidence_source="binance_usdm",
        narrative_llm_enabled=False,
        market_watcher_enabled=False,
        telegram_alerts_enabled=False,
        telegram_interaction_enabled=False,
        telegram_network_permitted=False,
        automatic_telegram_delivery_enabled=False,
        telegram_paper_activation_armed=False,
        alert_delivery_enabled=False,
    )
    clock = AcceptanceClock()
    market = FixtureMarket(failure=failure)
    source = BinanceUsdmPerpetualSource(transport=market.transport)
    monitor = PerpetualMarketMonitor(source, replay=False, clock=clock.now)
    runtime = build_production_canonical_runtime(factory, settings=settings, clock=clock)
    return ClosedLoopWorld(factory, settings, clock, market, source, monitor, runtime)
