"""PR152 integration regressions: tenant DB authority and real market composition."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.errors import ConflictError
from app.db.base import Base
from app.db.models import Organization
from app.db.session import get_session
from app.db.watcher_watchlist import WatcherSymbolStatusRow, WatcherWatchlistRow
from app.main import create_app
from app.market_contracts.adapters.failover import FailoverPerpetualSource
from app.market_contracts.enums import VenueId
from app.market_contracts.errors import StaleEvidenceError, WrongInstrumentError
from app.market_contracts.provider_contracts import ContractBook
from app.repositories.watcher_watchlist import WatcherWatchlistRepository
from app.signal_fusion.lifecycle import CandidateLifecycleService
from app.signal_fusion.memory import InMemoryCandidateRepository
from app.watcher.fusion_evaluation import BoundEvaluationClock
from app.watcher.memory import InMemoryWatcherStore
from app.workers.watcher_market import ContractUnavailableError, SymbolMarketFactory
from app.workers.watcher_paper import build_watcher_paper_runtime
from app.workers.watcher_paper_targets import PaperScanTarget
from app.workers.watcher_watchlist import DEFAULT_WATCHLIST_SYMBOLS, SymbolStatusBook

ORG = UUID(int=101)
OTHER = UUID(int=102)


class _InstantClock:
    """Wall start time that jumps forward only when a test asks to sleep."""

    def __init__(self) -> None:
        self.current = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


def _market_factory(cfg: Settings, transport, clock: _InstantClock) -> SymbolMarketFactory:
    return SymbolMarketFactory(cfg, transport=transport, clock=clock, sleeper=clock.sleep)


def settings(**kwargs):
    return Settings(
        _env_file=None,
        environment="local",
        execution_mode="paper",
        enable_real_trading=False,
        real_trading_enabled=False,
        database_url="sqlite+pysqlite:///:memory:",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        market_data_cache_use_redis=False,
        watcher_orchestration_enabled=False,
        perpetual_evidence_source=kwargs.pop("perpetual_evidence_source", "binance_usdm"),
        binance_request_max_retries=0,
        **kwargs,
    )


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'watchlist.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        session.add_all([Organization(id=ORG, name="A"), Organization(id=OTHER, name="B")])
        session.commit()
    yield factory
    engine.dispose()


class PublicMarket:
    def __init__(
        self, *, unsupported=(), fail=(), failover=(), stale=False, wrong=False, clock=None
    ):
        self.calls = []
        self.unsupported, self.fail, self.failover = unsupported, fail, failover
        self.stale, self.wrong = stale, wrong
        self.clock = clock or _InstantClock()
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request):
        path, symbol = request.url.path, request.url.params.get("symbol")
        self.calls.append((request.url.host, path, symbol))
        assert request.method == "GET"
        if path.endswith("exchangeInfo"):
            return httpx.Response(
                200,
                json={
                    "futuresType": "U_MARGINED",
                    "symbols": [
                        {
                            "symbol": s,
                            "baseAsset": s[:-4],
                            "quoteAsset": "USDT",
                            "contractType": "PERPETUAL",
                            "status": "TRADING",
                        }
                        for s in (*DEFAULT_WATCHLIST_SYMBOLS, "SOLUSDT")
                        if s not in self.unsupported
                    ],
                },
            )
        if path.endswith("instruments-info"):
            return httpx.Response(
                200,
                json={
                    "retCode": 0,
                    "result": {
                        "category": "linear",
                        "list": [
                            {
                                "symbol": symbol,
                                "baseCoin": symbol[:-4],
                                "quoteCoin": "USDT",
                                "contractType": "LinearPerpetual",
                                "status": "Trading",
                            }
                        ],
                    },
                },
            )
        if path in {"/fapi/v1/klines", "/v5/market/kline"}:
            if symbol in self.fail:
                return httpx.Response(503)
            if symbol in self.failover and path.startswith("/fapi"):
                return httpx.Response(451)
            assert int(request.url.params["limit"]) == 4
            now = self.clock()
            end = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 15)
            if self.stale:
                end -= timedelta(minutes=15)
            starts = [end - timedelta(minutes=30), end - timedelta(minutes=15)]
            if path.startswith("/fapi"):
                rows = [
                    [
                        int(t.timestamp() * 1000),
                        "10",
                        "12",
                        "9",
                        "11",
                        "100",
                        int((t + timedelta(minutes=15)).timestamp() * 1000) - 1,
                        "1100",
                        10,
                        "50",
                        "550",
                        "0",
                    ]
                    for t in starts
                ]
                return httpx.Response(200, json=rows)
            rows = [
                [str(int(t.timestamp() * 1000)), "10", "12", "9", "11", "100", "1100"]
                for t in starts
            ]
            return httpx.Response(
                200,
                json={
                    "retCode": 0,
                    "result": {
                        "category": "linear",
                        "symbol": "BTCUSDT" if self.wrong else symbol,
                        "list": rows,
                    },
                },
            )
        raise AssertionError(f"Unexpected market request: {path}")


def runtime(
    db,
    market,
    *,
    enabled=True,
    source="binance_usdm",
    secondary="",
    target_loader=None,
    organization_id="",
):
    cfg = settings(
        perpetual_evidence_source=source,
        perpetual_evidence_secondary_source=secondary,
        watcher_paper_organization_id=organization_id,
    )
    factory = _market_factory(cfg, market.transport, market.clock)
    candidates = InMemoryCandidateRepository()
    worker = build_watcher_paper_runtime(
        cfg,
        db,
        store=InMemoryWatcherStore(),
        lifecycle=CandidateLifecycleService(repository=candidates, clock=BoundEvaluationClock()),
        evidence_factory=factory,
        target_loader=target_loader,
        kill_switch_probe=lambda _org: False,
        enabled=enabled,
    )
    return worker, factory, candidates


def test_configured_organization_scope_pins_one_tenant(db):
    market = PublicMarket()
    worker, _, _ = runtime(db, market, organization_id=str(OTHER))

    report = worker.run_cycle()

    assert report.reason_code == "completed"
    with db() as session:
        rows = list(session.scalars(select(WatcherWatchlistRow)))
        assert [row.organization_id for row in rows] == [OTHER]


def test_invalid_configured_organization_scope_is_rejected():
    with pytest.raises(ValueError, match="watcher_paper_organization_id must be a UUID"):
        settings(watcher_paper_organization_id="not-a-uuid")


def test_repeated_worker_cycles_release_history_and_keep_compositions_bounded(db, capsys):
    import gc
    import json

    from app.observability.process_memory import read_process_memory, release_allocator_memory
    from app.workers.paper_worker import (
        CycleOutcome,
        PaperWorkerSupervisor,
        outcome_from_watcher_report,
    )

    market = PublicMarket()
    worker, factory, candidates = runtime(db, market)
    worker._settings.paper_worker_memory_diagnostics_enabled = True
    supervisor = PaperWorkerSupervisor(
        watcher_cycle=lambda: outcome_from_watcher_report(worker.run_cycle()),
        telegram_cycle=lambda: CycleOutcome(status="disarmed"),
        memory_diagnostics_enabled=True,
    )
    # Every worker step calls release_allocator_memory(), i.e. a full gc.collect().
    # Late in a full suite run the process heap holds millions of objects left by
    # earlier tests, so 81 full collections took ~300 s and hit the timeout.
    # Freeze that pre-existing heap so collection only walks this test's objects;
    # everything allocated by the cycles below is still collected normally.
    # Collect first so unreachable objects from earlier tests are reclaimed
    # instead of being pinned for the duration of the freeze.
    gc.collect()
    gc.freeze()
    before = release_allocator_memory().rss_bytes
    rss = []
    try:
        for _ in range(40):
            health = supervisor.run_round()
            rss.append(read_process_memory().rss_bytes)
            counts = factory.memory_counts()
            assert health.watcher.last_error == ""
            assert worker.history.held_symbol is None
            assert len(worker.history.completed) <= 5
            assert counts["market_compositions"] == 5
            assert counts["raw_cache_rows"] == counts["reduced_cache_entries"] == 0
            assert counts["monitor_trades"] == counts["bybit_proven_prints"] == 0
            assert len(factory._reads) <= 5
            assert len(worker.symbol_status.snapshot()) == 5
            assert candidates.list_for_organization(ORG) == ((), 0)
            market.calls.clear()  # The test recorder is not a production retained structure.
    finally:
        factory.retain(())
        gc.unfreeze()
    # Record measurements; assert logical bounds, never a platform-specific absolute RSS.
    with capsys.disabled():
        print(
            json.dumps(
                {
                    "workload": "40 five-market no-strategy cycles; mocked GET; Telegram disarmed",
                    "baseline_rss_bytes": before,
                    "rss_after_cleanup_bytes": rss,
                    "growth_bytes_per_cycle_after_warmup": (rss[-1] - rss[9]) / 30,
                }
            )
        )


def test_database_revision_restart_and_stale_worker_fence(db):
    with db() as session:
        repo = WatcherWatchlistRepository(session)
        original = repo.load(ORG)
        new = repo.replace(ORG, [("ETHUSDT", True), ("BTCUSDT", False)], expected_revision=0)
        session.commit()
    with db() as restarted:
        repo = WatcherWatchlistRepository(restarted)
        assert repo.load(ORG) == new
        assert repo.load(OTHER) == original
        with pytest.raises(ConflictError):
            repo.replace(ORG, [("SOLUSDT", True)], expected_revision=0)
        assert not repo.publish(ORG, original, (), observed_at=datetime.now(UTC))
        assert repo.load(ORG).revision == 1


@pytest.mark.parametrize("source", ["binance_usdm", "bybit_usdt_perpetual"])
def test_normal_production_composition_probes_all_five_without_candidates(db, source):
    market = PublicMarket()
    worker, factory, candidates = runtime(db, market, source=source)
    report = worker.run_cycle()
    assert report.reason_code == "completed" and report.candidates_created == 0
    assert not report.scans  # probes never become strategy/candidate scans
    assert candidates.list_for_organization(ORG) == ((), 0)
    with db() as session:
        config, rows = WatcherWatchlistRepository(session).status(
            ORG, source_mode=source, now=datetime.now(UTC), max_age_seconds=90
        )
        assert {r["symbol"] for r in rows} == set(DEFAULT_WATCHLIST_SYMBOLS)
        assert all(
            r["last_successful_scan"] and r["freshness"] == "fresh_closed_candle" for r in rows
        )
        assert all(r["configuration_revision"] == config.revision for r in rows)
        assert all(r["strategy_matches"] == [] and r["alert_state"] == "none" for r in rows)
        _, other = WatcherWatchlistRepository(session).status(
            OTHER, source_mode=source, now=datetime.now(UTC), max_age_seconds=90
        )
        assert all(r["last_successful_scan"] is None for r in other)
    assert len(factory._entries) == 5 and len(worker.history.completed) == 5
    assert worker.history.peak_in_flight == 1
    candle_reads = Counter(s for _, path, s in market.calls if path.endswith(("klines", "kline")))
    # Binance REST v2 admission confirms each settled window; Bybit remains one read.
    assert candle_reads == dict.fromkeys(
        DEFAULT_WATCHLIST_SYMBOLS, 2 if source == "binance_usdm" else 1
    )


def test_worker_refreshes_same_database_and_replacement_evicts_state(db):
    market = PublicMarket()
    worker, factory, _ = runtime(db, market)
    worker.run_cycle()
    with db() as session:
        repo = WatcherWatchlistRepository(session)
        repo.replace(ORG, [("SOLUSDT", True), ("BTCUSDT", False)], expected_revision=0)
        session.commit()
    worker.run_cycle()  # other organization
    before = len(market.calls)
    worker.run_cycle()  # first organization, new revision
    assert {s for _, path, s in market.calls[before:] if path.endswith("klines")} == {"SOLUSDT"}
    assert set(factory._entries) == {"SOLUSDT"}
    with db() as session:
        rows = list(
            session.scalars(
                select(WatcherSymbolStatusRow).where(WatcherSymbolStatusRow.organization_id == ORG)
            )
        )
        assert {r.symbol for r in rows} == {"SOLUSDT", "BTCUSDT"}
        assert all(r.configuration_revision == 1 for r in rows)
    restarted, _, _ = runtime(db, market)
    restarted.run_cycle()
    assert restarted.snapshot().symbols == ("SOLUSDT",)


def test_explicit_unsupported_never_reaches_factory_or_evaluator(db, monkeypatch):
    from app.workers import watcher_paper

    market = PublicMarket(unsupported={"ETHUSDT"})
    target = PaperScanTarget(ORG, uuid4(), uuid4(), uuid4(), uuid4(), "a" * 64, "v1", "ETHUSDT")
    worker, factory, _ = runtime(db, market, target_loader=lambda _s: (target,))

    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported symbol reached evaluator")

    monkeypatch.setattr(watcher_paper, "build_fusion_evaluation_service", forbidden)
    report = worker.run_cycle()
    assert report.scans[0].reason_code == "unsupported_contract"
    assert "ETHUSDT" not in factory._entries
    assert not any(s == "ETHUSDT" and p.endswith("klines") for _, p, s in market.calls)
    row = next(r for r in worker.symbol_status.snapshot() if r.symbol == "ETHUSDT")
    assert row.error_state == "unsupported_contract" and row.last_successful_scan is None


@pytest.mark.parametrize("enabled,refused", [(False, False), (True, True)])
def test_disabled_and_refused_never_advance_success(db, enabled, refused):
    market = PublicMarket()
    worker, _, _ = runtime(db, market, enabled=enabled)
    if refused:
        worker._activation_gate = lambda: "activation_refused"
    worker.run_cycle()
    assert not any(p.endswith(("klines", "kline")) for _, p, _ in market.calls)
    assert all(r.last_successful_scan is None for r in worker.symbol_status.snapshot())


def test_kill_switch_blocks_read_only_probes_without_mutation(db):
    market = PublicMarket()
    worker, _, _ = runtime(db, market)
    worker._kill_switch_probe = lambda _org: True
    worker.run_cycle()
    assert not any(p.endswith("klines") for _, p, _ in market.calls)
    assert all(
        r.error_state == "kill_switch_active" and r.last_successful_scan is None
        for r in worker.symbol_status.snapshot()
    )


def test_one_market_failure_does_not_block_other_symbols(db):
    market = PublicMarket(fail={"ETHUSDT"})
    worker, _, _ = runtime(db, market)
    worker.run_cycle()
    rows = {r.symbol: r for r in worker.symbol_status.snapshot()}
    assert rows["ETHUSDT"].error_state == "provider_unreachable"
    assert rows["ETHUSDT"].last_successful_scan is None
    assert all(rows[s].last_successful_scan for s in DEFAULT_WATCHLIST_SYMBOLS if s != "ETHUSDT")


def test_symbol_specific_failover_and_wrong_instrument(db):
    market = PublicMarket(failover={"ETHUSDT"})
    worker, factory, _ = runtime(db, market, secondary="bybit_usdt_perpetual")
    worker.run_cycle()
    eth, _, _, _ = factory.composition("ETHUSDT")
    btc, _, _, _ = factory.composition("BTCUSDT")
    assert eth.using_secondary and not btc.using_secondary
    assert eth.active_instrument().provider_symbol == "ETHUSDT"
    rows = {r.symbol: r for r in worker.symbol_status.snapshot()}
    assert rows["ETHUSDT"].market_source == "bybit-usdt-perpetual"
    assert rows["BTCUSDT"].market_source == "binance-usdm-perpetual"
    market.wrong = True
    with pytest.raises(WrongInstrumentError):
        factory.probe("ETHUSDT")


def _kline_hosts(market: PublicMarket) -> list[str]:
    return [
        host
        for host, path, _symbol in market.calls
        if path.endswith("/klines") or path.endswith("/kline")
    ]


def test_failover_probe_uses_binance_primary_then_bybit() -> None:
    """A healthy probe reads Binance. A primary failure switches the probe to Bybit."""

    healthy = PublicMarket()
    factory = _market_factory(
        settings(perpetual_evidence_secondary_source="bybit_usdt_perpetual"),
        healthy.transport,
        healthy.clock,
    )
    factory.discovery(ContractBook(()), ["ETHUSDT"])
    probed = factory.probe("ETHUSDT")
    source, _, _, _ = factory.composition("ETHUSDT")
    assert isinstance(source, FailoverPerpetualSource)
    assert source.using_secondary is False
    assert source.active_instrument().venue is VenueId.BINANCE
    assert probed.provider == "binance-usdm-perpetual"
    assert probed.freshness == "fresh_closed_candle"
    assert _kline_hosts(healthy) == ["fapi.binance.com", "fapi.binance.com"]

    failed = PublicMarket(failover={"ETHUSDT"})
    failed_factory = _market_factory(
        settings(perpetual_evidence_secondary_source="bybit_usdt_perpetual"),
        failed.transport,
        failed.clock,
    )
    failed_factory.discovery(ContractBook(()), ["ETHUSDT"])
    switched = failed_factory.probe("ETHUSDT")
    failed_source, _, _, _ = failed_factory.composition("ETHUSDT")
    assert isinstance(failed_source, FailoverPerpetualSource)
    assert failed_source.using_secondary is True
    assert failed_source.active_instrument().venue is VenueId.BYBIT
    assert switched.provider == "bybit-usdt-perpetual"
    assert _kline_hosts(failed) == ["fapi.binance.com", "api.bybit.com"]


def test_stale_probe_cannot_report_success():
    market = PublicMarket(stale=True)
    factory = _market_factory(settings(), market.transport, market.clock)
    factory.discovery(ContractBook(()), ["ETHUSDT"])
    with pytest.raises(StaleEvidenceError):
        factory.probe("ETHUSDT")


def test_discovery_unreachable_is_not_unsupported_and_retries():
    now = [0.0]
    market = PublicMarket()
    failed = [True]

    def handler(request):
        return httpx.Response(503) if failed[0] else market.handle(request)

    factory = _market_factory(settings(), httpx.MockTransport(handler), market.clock)
    factory.discovery.clock = lambda: now[0]
    factory.discovery(ContractBook(()), ["ETHUSDT"])
    with pytest.raises(ContractUnavailableError, match="provider_unreachable"):
        factory.composition("ETHUSDT")
    failed[0], now[0] = False, 61.0
    factory.discovery(ContractBook(()), ["ETHUSDT"])
    assert factory.probe("ETHUSDT").symbol == "ETHUSDT"


def test_old_revision_and_stale_status_are_pending(db):
    now = datetime.now(UTC)
    with db() as session:
        repo = WatcherWatchlistRepository(session)
        cfg = repo.load(ORG)
        book = SymbolStatusBook()
        rows = book.project(cfg, source_mode="binance_usdm")
        repo.publish(ORG, cfg, rows, observed_at=now - timedelta(seconds=100))
        session.commit()
    with db() as session:
        repo = WatcherWatchlistRepository(session)
        _, rows = repo.status(ORG, source_mode="binance_usdm", now=now, max_age_seconds=90)
        assert all(row["observed_at"] is None for row in rows)
        repo.replace(ORG, [("BTCUSDT", True)], expected_revision=0)
        session.commit()
        _, rows = repo.status(ORG, source_mode="binance_usdm", now=now, max_age_seconds=1000)
        assert rows[0]["setup_state"] == "pending" and rows[0]["observed_at"] is None


def test_authenticated_api_enforces_organization_configuration_and_status(db):
    cfg = settings(perpetual_evidence_source="replay")
    app = create_app(settings=cfg)

    def session():
        with db() as value:
            yield value

    app.dependency_overrides[get_session] = session
    with TestClient(app) as client:
        assert client.get("/watcher/watchlist").status_code == 401
        accounts = []
        for name in ("tenant-one", "tenant-two"):
            registered = client.post(
                "/auth/register",
                json={
                    "email": f"{name}@test.example",
                    "password": "secure-password-1",
                    "organization_name": name,
                },
            )
            assert registered.status_code == 201, registered.text
            accounts.append(
                {"Authorization": "Bearer " + registered.json()["tokens"]["access_token"]}
            )
        a, b = accounts
        saved = client.put(
            "/watcher/watchlist",
            headers=a,
            json={"revision": 0, "slots": [{"symbol": "SOLUSDT", "enabled": True}]},
        )
        assert saved.status_code == 200, saved.text
        assert client.get("/watcher/watchlist", headers=a).json()["slots"][0]["symbol"] == "SOLUSDT"
        assert client.get("/watcher/watchlist", headers=b).json()["revision"] == 0
        assert (
            client.put(
                "/watcher/watchlist",
                headers=b,
                json={"revision": 0, "slots": [], "organization_id": str(ORG)},
            ).status_code
            == 422
        )
        with db() as session:
            org = session.scalar(select(Organization.id).where(Organization.name == "tenant-one"))
            repo = WatcherWatchlistRepository(session)
            config = repo.load(org)
            book = SymbolStatusBook()
            book.project(config, source_mode="binance_usdm")
            book.record_scan(
                symbol="SOLUSDT",
                succeeded=True,
                setup_state="candidate",
                freshness="fresh",
                strategy_matches=["private-tenant-a-strategy"],
                alert_state="paper_candidate",
                error_state=None,
                scanned_at=datetime.now(UTC),
            )
            repo.publish(
                org,
                config,
                book.snapshot(),
                observed_at=datetime.now(UTC),
                runtime={
                    "candidates_created": 7,
                    "scopes": [
                        {
                            "scan_scope": "private-tenant-a-strategy",
                            "symbol": "SOLUSDT",
                            "health_state": "succeeded",
                            "last_reason_code": "completed",
                            "candidate_ids": ["private-candidate-a"],
                        }
                    ],
                },
            )
            session.commit()
        assert (
            "private-tenant-a-strategy" in client.get("/watcher/watchlist/status", headers=a).text
        )
        other = client.get(
            "/watcher/watchlist/status", headers={**b, "X-Organization-ID": str(org)}
        )
        assert other.status_code == 200
        assert "private-tenant-a-strategy" not in other.text and "paper_candidate" not in other.text
        own_runtime = client.get("/watcher/paper-runtime/status", headers=a)
        other_runtime = client.get("/watcher/paper-runtime/status", headers=b)
        assert own_runtime.json()["candidates_created"] == 7
        assert own_runtime.json()["symbols"] == ["SOLUSDT"]
        assert other_runtime.json()["candidates_created"] == 0
        assert "SOLUSDT" not in other_runtime.json()["symbols"]
        assert "private-candidate-a" not in other_runtime.text
        assert "private-tenant-a-strategy" not in other_runtime.text
        assert (
            client.put(
                "/watcher/watchlist", headers=a, json={"revision": 0, "slots": []}
            ).status_code
            == 409
        )
    app.dependency_overrides.clear()


def test_runtime_summary_is_shared_tenant_scoped_and_freshness_fenced(db):
    worker, factory, _ = runtime(db, PublicMarket())
    for _ in range(6):
        worker.run_cycle()
    with db() as session:
        repo = WatcherWatchlistRepository(session)
        now = datetime.now(UTC)
        own = repo.runtime_status(ORG, now=now, max_age_seconds=90)
        other = repo.runtime_status(OTHER, now=now, max_age_seconds=90)
        assert own["cycles_completed"] == other["cycles_completed"] == 3
        assert own["candidates_created"] == other["candidates_created"] == 0
        assert repo.runtime_status(ORG, now=now + timedelta(seconds=91), max_age_seconds=90) is None
        repo.replace(ORG, [("BTCUSDT", True)], expected_revision=0)
        session.commit()
        assert repo.runtime_status(ORG, now=now, max_age_seconds=90) is None
        assert repo.runtime_status(OTHER, now=now, max_age_seconds=90) is not None
        assert len(list(session.scalars(select(WatcherSymbolStatusRow)))) == 6
    assert len(factory._entries) <= 5
    assert len(factory.discovery._cache) <= 10
    assert len(factory._reads) <= 5
    assert len(worker.history.completed) == 5 and worker.history.held_symbol is None
    assert worker.history.peak_in_flight == 1


def test_noop_without_probe_and_empty_config_cannot_advance_scan_success(db):
    market = PublicMarket()
    worker, _, _ = runtime(db, market)
    worker._symbol_probe = None
    assert worker.run_cycle().reason_code == "idle"
    assert all(row.last_successful_scan is None for row in worker.symbol_status.snapshot())
    with db() as session:
        WatcherWatchlistRepository(session).replace(ORG, [], expected_revision=0)
        session.commit()
    worker.run_cycle()
    assert worker.run_cycle().reason_code == "idle"
    assert worker.symbol_status.snapshot() == ()
    assert not any(p.endswith(("klines", "kline")) for _, p, _ in market.calls)


def test_unverified_secondary_never_reads_market():
    market = PublicMarket(failover={"ETHUSDT"})

    def handle(request):
        if request.url.path.endswith("instruments-info"):
            return httpx.Response(
                200, json={"retCode": 0, "result": {"category": "linear", "list": []}}
            )
        return market.handle(request)

    factory = _market_factory(
        settings(perpetual_evidence_secondary_source="bybit_usdt_perpetual"),
        httpx.MockTransport(handle),
        market.clock,
    )
    factory.discovery(ContractBook(()), ["ETHUSDT"])
    with pytest.raises(ContractUnavailableError, match="unsupported_contract"):
        factory.probe("ETHUSDT")
    assert not any(path.endswith("/kline") for _, path, _ in market.calls)


def test_mixed_strategy_and_probe_work_follow_configured_order(db, monkeypatch):
    from app.workers.watcher_paper import WatcherPaperScanReport

    market = PublicMarket()
    target = PaperScanTarget(ORG, uuid4(), uuid4(), uuid4(), uuid4(), "a" * 64, "v1", "ETHUSDT")
    worker, factory, _ = runtime(db, market, target_loader=lambda _s: (target,))
    order = []
    original = factory.probe
    worker._symbol_probe = lambda symbol: (order.append(symbol), original(symbol))[1]

    def scan(session, selected):
        order.append(selected.symbol)
        return WatcherPaperScanReport(
            organization_id=ORG,
            scan_scope=selected.scan_scope,
            symbol=selected.symbol,
            status="skipped",
            reason_code="no_setup",
            replayed=False,
            published=False,
            candidate_ids=(),
            kill_switch_active=False,
        )

    monkeypatch.setattr(worker, "_scan_one", scan)
    worker.run_cycle()
    assert order == list(DEFAULT_WATCHLIST_SYMBOLS)
    assert (
        next(
            row for row in worker.symbol_status.snapshot() if row.symbol == "ETHUSDT"
        ).last_successful_scan
        is None
    )


@pytest.mark.parametrize("source", ["binance_usdm", "bybit_usdt_perpetual"])
def test_all_five_canonical_evidence_ports_have_their_own_monitor(source):
    market = PublicMarket()
    factory = _market_factory(
        settings(perpetual_evidence_source=source), market.transport, market.clock
    )
    factory.discovery(ContractBook(()), DEFAULT_WATCHLIST_SYMBOLS)
    monitors = []
    for symbol in DEFAULT_WATCHLIST_SYMBOLS:
        evidence = factory(None, InMemoryWatcherStore(), symbol)
        monitor = evidence._monitor._monitor
        assert evidence._symbol == symbol
        assert monitor.enabled_symbols() == (symbol,)
        assert evidence._assembler._source is monitor._source
        monitors.append(monitor)
    assert len({id(monitor) for monitor in monitors}) == 5


@pytest.mark.parametrize("wrong_instrument", [False, True])
def test_failed_secondary_probe_retains_actual_provider_and_error(db, wrong_instrument):
    market = PublicMarket(failover={"ETHUSDT"}, wrong=wrong_instrument)

    def handle(request):
        if (
            not wrong_instrument
            and request.url.path == "/v5/market/kline"
            and request.url.params["symbol"] == "ETHUSDT"
        ):
            return httpx.Response(503)
        return market.handle(request)

    market.transport = httpx.MockTransport(handle)
    worker, factory, _ = runtime(db, market, secondary="bybit_usdt_perpetual")
    worker.run_cycle()
    rows = {row.symbol: row for row in worker.symbol_status.snapshot()}
    failed = rows["ETHUSDT"]
    assert factory.provider_for("ETHUSDT") == "bybit-usdt-perpetual"
    assert failed.market_source == "bybit-usdt-perpetual"
    assert failed.last_successful_scan is None and failed.last_failed_scan is not None
    assert failed.error_state == (
        "wrong_instrument" if wrong_instrument else "provider_unreachable"
    )
    assert rows["BTCUSDT"].market_source == "binance-usdm-perpetual"
    assert rows["BTCUSDT"].last_successful_scan is not None


def test_foreign_target_is_rejected_before_evidence_and_candidate_creation(db):
    foreign = PaperScanTarget(OTHER, uuid4(), uuid4(), uuid4(), uuid4(), "a" * 64, "v1", "BTCUSDT")
    worker, _, candidates = runtime(db, PublicMarket(), target_loader=lambda _s: (foreign,))

    def forbidden(*_args):
        pytest.fail("Foreign target reached evidence acquisition")

    worker._evidence_factory = forbidden
    worker._symbol_probe = None
    report = worker.run_cycle()  # ORG's turn, with an injected OTHER target
    assert report.reason_code == "idle" and report.scans == ()
    assert candidates.list_for_organization(ORG) == ((), 0)
    assert candidates.list_for_organization(OTHER) == ((), 0)


@pytest.mark.parametrize(
    "unreachable,expected", [(False, "unsupported_contract"), (True, "provider_unreachable")]
)
def test_strategy_secondary_refusal_keeps_reason_without_market_acquisition(unreachable, expected):
    from app.watcher.memory import FakeClock
    from app.workers.watcher_paper import WatcherPaperRuntime
    from app.workers.watcher_watchlist import replace_watchlist

    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith("exchangeInfo"):
            return httpx.Response(
                200,
                json={
                    "futuresType": "U_MARGINED",
                    "symbols": [
                        {
                            "symbol": "ETHUSDT",
                            "baseAsset": "ETH",
                            "quoteAsset": "USDT",
                            "contractType": "PERPETUAL",
                            "status": "TRADING",
                        }
                    ],
                },
            )
        if request.url.path.endswith("instruments-info"):
            if unreachable:
                return httpx.Response(503)
            return httpx.Response(
                200, json={"retCode": 0, "result": {"category": "linear", "list": []}}
            )
        if request.url.host == "fapi.binance.com":
            return httpx.Response(451)
        raise AssertionError("Ineligible secondary must never acquire market data")

    config = settings(perpetual_evidence_secondary_source="bybit_usdt_perpetual")
    clock = _InstantClock()
    factory = SymbolMarketFactory(
        config, transport=httpx.MockTransport(handler), clock=clock, sleeper=clock.sleep
    )
    org = uuid4()
    target = PaperScanTarget(org, uuid4(), uuid4(), uuid4(), uuid4(), "a" * 64, "v1", "ETHUSDT")
    candidates = InMemoryCandidateRepository()
    worker = WatcherPaperRuntime(
        store=InMemoryWatcherStore(),
        lifecycle=CandidateLifecycleService(repository=candidates, clock=BoundEvaluationClock()),
        clock=FakeClock(datetime.now(UTC)),
        enabled=True,
        watchlist_mode=True,
        watchlist=replace_watchlist([("ETHUSDT", True)]),
        symbols=["ETHUSDT"],
        evidence_factory=factory,
        history_source=factory,
        contract_discoverer=factory.discovery,
        target_loader=lambda _s: (target,),
        kill_switch_probe=lambda _o: False,
        settings=config,
    )
    try:
        report = worker.run_cycle()
        row = worker.symbol_status.snapshot()[0]
        assert report.scans[0].status == "failed"
        assert report.scans[0].reason_code == row.error_state == expected
        assert row.market_source == "bybit-usdt-perpetual"
        assert row.last_successful_scan is None
        assert candidates.list_for_organization(org)[1] == 0
        assert len(calls) == 3
    finally:
        factory.retain(())


@pytest.mark.parametrize("market_read", [True, False])
def test_real_orchestrator_replay_does_not_inflate_durable_scan_counts(
    db, monkeypatch, market_read
):
    """Keep actual scheduling/replay and SQL accumulation; stub only evaluation input."""
    from app.watcher.contracts import EvaluationOutcome, EvaluationStatus
    from app.watcher.memory import FakeClock
    from app.workers.watcher_paper import WatcherPaperRuntime

    clock = FakeClock(datetime(2026, 9, 30, 12, 0, 10, tzinfo=UTC))
    store = InMemoryWatcherStore()
    candidate_id = UUID(int=999)
    target = PaperScanTarget(ORG, uuid4(), uuid4(), uuid4(), uuid4(), "a" * 64, "v1", "BTCUSDT")
    with db() as session:
        repo = WatcherWatchlistRepository(session)
        repo.replace(ORG, [("BTCUSDT", True)], expected_revision=0)
        repo.replace(OTHER, [("BTCUSDT", False)], expected_revision=0)
        session.commit()

    class Inputs:
        reads = 0
        evaluations = 0

        def __call__(self, session, watcher_store, symbol):
            assert session is not None and watcher_store is store and symbol == "BTCUSDT"
            return self

        def read_count(self, symbol):
            return self.reads

        def provider_for(self, symbol):
            return "binance_usdm"

    inputs = Inputs()

    class Evaluator:
        discussion_snapshot = None

        def evaluate(self, command):
            inputs.evaluations += 1
            inputs.reads += int(market_read)
            return EvaluationOutcome(
                command_id=command.command_id,
                request_hash=command.request_hash,
                evaluation_input_hash=command.evaluation_input_hash,
                status=EvaluationStatus.SUCCEEDED,
                reason_code="confirmed_setup",
                candidate_ids=(candidate_id,),
                evidence_validity_token="b" * 64,
            )

    monkeypatch.setattr(
        "app.workers.watcher_paper.build_fusion_evaluation_service", lambda **_kwargs: Evaluator()
    )

    def make_worker(worker_id):
        return WatcherPaperRuntime(
            store=store,
            lifecycle=CandidateLifecycleService(
                repository=InMemoryCandidateRepository(), clock=BoundEvaluationClock()
            ),
            clock=clock,
            enabled=True,
            worker_id=worker_id,
            session_factory=db,
            tenant_watchlists=True,
            watchlist_mode=True,
            symbols=["BTCUSDT"],
            evidence_factory=inputs,
            target_loader=lambda _session: (target,),
            kill_switch_probe=lambda _org: False,
            evaluation_observer_factory=lambda _session, _target: None,
            settings=settings(),
        )

    def durable():
        with db() as session:
            repo = WatcherWatchlistRepository(session)
            return (
                repo.runtime_status(ORG, now=clock.now(), max_age_seconds=90),
                repo.previous(ORG)[0].last_successful_scan,
            )

    worker = make_worker("first-worker")
    initial = worker.run_cycle()
    assert initial.scans[0].market_read_completed is market_read
    assert not initial.scans[0].replayed and initial.candidates_created == 1
    first_status, first_timestamp = durable()
    assert first_status["scans_succeeded"] == int(market_read)
    assert (first_timestamp is not None) is market_read
    clock.advance(seconds=15)
    assert worker.run_cycle().scans == ()  # OTHER's disabled configuration
    replay = worker.run_cycle()
    assert replay.scans[0].replayed and not replay.scans[0].market_read_completed
    assert replay.scans[0].candidate_ids == () and replay.candidates_created == 0
    assert worker.snapshot().scans_succeeded == int(market_read)
    assert worker.snapshot().scans_skipped == 2 - int(market_read)
    clock.advance(seconds=31)  # Expire the original worker's lease.
    restarted = make_worker("restarted-worker")
    replay_after_restart = restarted.run_cycle()
    assert replay_after_restart.scans[0].replayed
    assert replay_after_restart.candidates_created == 0
    assert restarted.snapshot().scans_succeeded == 0
    status, timestamp = durable()
    assert status["cycles_completed"] == 3
    assert status["scans_succeeded"] == int(market_read)
    assert status["scans_skipped"] == 3 - int(market_read)
    assert status["candidates_created"] == 1
    assert timestamp == first_timestamp
    assert inputs.evaluations == 1 and inputs.reads == int(market_read)
    with db() as session:
        other = WatcherWatchlistRepository(session).runtime_status(
            OTHER, now=clock.now(), max_age_seconds=90
        )
        assert other["scans_succeeded"] == other["candidates_created"] == 0
