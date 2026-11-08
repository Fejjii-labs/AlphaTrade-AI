"""AT-034 WS4 — HTTP integration seams for deterministic backtest v2.

End-to-end flows over TestClient with in-memory SQLite; no network. Complements
``test_at034_engine.py`` (engine unit) and ``test_at034_api.py`` (service/API unit)
without duplicating their coverage.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import floor
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    BacktestRun,
    HistoricalCandle,
    JournalTrade,
    Membership,
    Organization,
    User,
)
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import (
    BacktestRunStatus,
    EntryTriggerType,
    ExitRuleType,
    JournalTradeSource,
    MembershipRole,
    TradeDirection,
)
from app.security.passwords import hash_password
from app.security.rate_limit import reset_rate_limiter
from app.services.backtest_service import BacktestService

ORG_A = uuid.UUID("00000000-0000-0000-0000-000000034501")
ORG_B = uuid.UUID("00000000-0000-0000-0000-000000034502")
USER_A = uuid.UUID("00000000-0000-0000-0000-000000034511")
USER_B = uuid.UUID("00000000-0000-0000-0000-000000034512")
VIEWER_A = uuid.UUID("00000000-0000-0000-0000-000000034513")

_BASE: dict[str, Any] = {
    "environment": "local",
    "log_json": False,
    "execution_mode": "paper",
    "enable_real_trading": False,
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "at034-integration-test-secret-32ch",
    "rate_limit_use_redis": False,
    "access_token_denylist_use_redis": False,
    "provider_mode": "mock",
    "market_data_provider": "mock",
    "alert_delivery_enabled": False,
    "telegram_alerts_enabled": False,
    "worker_enabled": False,
    "market_watcher_enabled": False,
}


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    reset_rate_limiter()


def _settings(**overrides: Any) -> Settings:
    return Settings(**{**_BASE, **overrides})


def _pullback_price(i: int) -> float:
    if i < 30:
        return 100 + i * 0.5
    if i == 30:
        return 110.0
    if i == 31:
        return 100.0
    return 112.0 + (i - 32) * 0.3


def _seed_candles(
    session: Session,
    *,
    n: int = 120,
    price_fn: Any | None = None,
) -> list[HistoricalCandle]:
    """Module-level deterministic OHLCV seed (4h BTCUSDT synthetic)."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    step = timedelta(hours=4)
    rows: list[HistoricalCandle] = []
    for i in range(n):
        open_time = start + step * i
        close = (
            Decimal("100") + Decimal(str(i)) * Decimal("0.25")
            if price_fn is None
            else Decimal(str(price_fn(i)))
        )
        row = HistoricalCandle(
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="4h",
            open_time=open_time,
            close_time=open_time + step,
            open=close,
            high=close + Decimal("2"),
            low=close - Decimal("2"),
            close=close,
            volume=Decimal("10"),
            source="synthetic",
        )
        rows.append(row)
        session.add(row)
    session.flush()
    return rows


def _seed_pullback_candles(session: Session, *, n: int = 120) -> list[HistoricalCandle]:
    """EMA-pullback-friendly series that produces closed trades in integration runs."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    step = timedelta(hours=4)
    rows: list[HistoricalCandle] = []
    for i in range(n):
        open_time = start + step * i
        close = Decimal(str(_pullback_price(i)))
        row = HistoricalCandle(
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="4h",
            open_time=open_time,
            close_time=open_time + step,
            open=close,
            high=close + Decimal("1"),
            low=close - Decimal("1"),
            close=close,
            volume=Decimal("10"),
            source="synthetic",
        )
        if i == 30:
            row.low = Decimal("100")
            row.close = Decimal("110")
            row.high = Decimal("111")
        if i == 31:
            row.low = Decimal("95")
            row.close = Decimal("112")
            row.high = Decimal("113")
            row.open = Decimal("100")
        rows.append(row)
        session.add(row)
    session.flush()
    return rows


def _seed_holdout_candles(session: Session, *, n: int = 120) -> list[HistoricalCandle]:
    """Breakout entries in-sample (bar 30) and out-of-sample (bar 110)."""
    rows = _seed_candles(session, n=n)
    for idx in (30, 110):
        lookback = rows[max(0, idx - 20) : idx]
        prior_high = max(r.high for r in lookback)
        rows[idx].close = prior_high + Decimal("5")
        rows[idx].high = prior_high + Decimal("6")
        rows[idx].low = prior_high - Decimal("1")
        rows[idx].open = prior_high + Decimal("1")
    session.flush()
    return rows


def _seed_short_capable_candles(session: Session, *, n: int = 80) -> list[HistoricalCandle]:
    """Engineer liquidity-sweep SHORT entries with a subsequent drop for take-profit."""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    step = timedelta(hours=4)
    rows: list[HistoricalCandle] = []
    for i in range(n):
        open_time = start + step * i
        close = Decimal("100") + Decimal(str(i)) * Decimal("0.05")
        row = HistoricalCandle(
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="4h",
            open_time=open_time,
            close_time=open_time + step,
            open=close,
            high=close + Decimal("1"),
            low=close - Decimal("1"),
            close=close,
            volume=Decimal("10"),
            source="synthetic",
        )
        rows.append(row)
        session.add(row)
    idx = 35
    lookback = rows[max(0, idx - 15) : idx]
    swing_high = max(r.high for r in lookback)
    rows[idx].high = swing_high + Decimal("5")
    rows[idx].close = swing_high - Decimal("2")
    rows[idx].low = swing_high - Decimal("3")
    rows[idx].open = swing_high
    for j in range(idx + 1, n):
        rows[j].close = Decimal("88") - Decimal(str(j - idx)) * Decimal("0.1")
        rows[j].low = rows[j].close - Decimal("1")
        rows[j].high = rows[j].close + Decimal("1")
        rows[j].open = rows[j].close + Decimal("1")
    session.flush()
    return rows


@contextmanager
def _build_client(
    *,
    candle_seed: Any = _seed_candles,
    candle_kwargs: dict[str, Any] | None = None,
    **settings_overrides: object,
) -> Iterator[tuple[TestClient, sessionmaker[Session], Settings]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = _settings(**settings_overrides)

    with factory() as session:
        session.add(Organization(id=ORG_A, name="AT034 Int Org A"))
        session.add(Organization(id=ORG_B, name="AT034 Int Org B"))
        for user_id, email in (
            (USER_A, "at034-int-a@test.example"),
            (USER_B, "at034-int-b@test.example"),
            (VIEWER_A, "at034-int-viewer@test.example"),
        ):
            session.add(
                User(
                    id=user_id,
                    email=email,
                    hashed_password=hash_password("SecurePass123!", settings),
                    email_verified=True,
                )
            )
        session.flush()
        session.add(Membership(user_id=USER_A, organization_id=ORG_A, role=MembershipRole.OWNER))
        session.add(Membership(user_id=USER_B, organization_id=ORG_B, role=MembershipRole.TRADER))
        session.add(Membership(user_id=VIEWER_A, organization_id=ORG_A, role=MembershipRole.VIEWER))
        seed_kwargs = candle_kwargs or {}
        candle_seed(session, **seed_kwargs)
        session.commit()

    app = create_app(settings=settings)

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session

    with TestClient(app) as test_client:
        yield test_client, factory, settings

    app.dependency_overrides.clear()
    engine.dispose()


def _auth(client: TestClient, email: str) -> dict[str, str]:
    login = client.post("/auth/login", json={"email": email, "password": "SecurePass123!"})
    assert login.status_code == 200, login.text
    token = login.json()["tokens"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _card() -> dict[str, Any]:
    return {
        "strategy_name": "AT034 Integration",
        "market_type": "crypto_perp",
        "asset_universe": ["BTCUSDT"],
        "timeframes": ["4h"],
        "entry_conditions": ["Pullback to EMA"],
        "confirmation_conditions": ["RSI reset"],
        "invalidation": ["Close below swing"],
        "stop_loss": ["2% below entry"],
        "take_profit_plan": ["TP1 at 1R", "TP2 at 2R"],
        "runner_plan": [],
        "position_sizing": ["Max 1%"],
        "add_rules": [],
        "no_trade_rules": [],
        "backtest_rules": [],
        "success_criteria": ["Win rate > 45%"],
        "validation_status": "draft",
    }


def _structured_payload(
    *,
    direction: TradeDirection = TradeDirection.LONG,
    trigger: EntryTriggerType = EntryTriggerType.EMA_PULLBACK,
) -> dict[str, Any]:
    return {
        "entry_rules": [{"trigger_type": trigger.value, "direction": direction.value}],
        "exit_rules": [
            {"rule_type": ExitRuleType.FIXED_STOP.value, "value": "2"},
            {"rule_type": ExitRuleType.TP_MULTIPLE.value, "r_multiple": "1"},
            {"rule_type": ExitRuleType.TP_MULTIPLE.value, "r_multiple": "2"},
        ],
    }


def _create_strategy(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str = "Int Strat",
    structured: dict[str, Any] | None = None,
) -> str:
    create = client.post(
        "/strategies",
        json={"name": name, "setup_type": "htf_trend_pullback", "card": _card()},
        headers=headers,
    )
    assert create.status_code == 200, create.text
    strategy_id = create.json()["id"]
    patch = client.patch(
        f"/strategies/{strategy_id}/structured-rules",
        json=structured or _structured_payload(),
        headers=headers,
    )
    assert patch.status_code == 200, patch.text
    return strategy_id


def _assumptions(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "symbol": "BTCUSDT",
        "exchange": "binance",
        "timeframe": "4h",
        "start_date": "2024-01-01",
        "end_date": "2024-02-01",
        "initial_capital": "10000",
        "fees_bps": "4",
        "slippage_bps": "5",
        "risk_per_trade_pct": "1",
    }
    body.update(overrides)
    return body


def _run_backtest(
    client: TestClient,
    headers: dict[str, str],
    strategy_id: str,
    **assumption_overrides: Any,
) -> dict[str, Any]:
    resp = client.post(
        f"/strategies/{strategy_id}/backtests",
        json={"assumptions": _assumptions(**assumption_overrides)},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------------------- #
# Full flow + verify determinism
# --------------------------------------------------------------------------- #


def test_full_flow_create_verify_twice_deterministic() -> None:
    with _build_client(candle_seed=_seed_pullback_candles) as (client, factory, _settings_obj):
        headers = _auth(client, "at034-int-a@test.example")
        strategy_id = _create_strategy(client, headers, name="Verify Flow")
        run = _run_backtest(client, headers, strategy_id)
        assert run["status"] == "completed"
        assert run["result_hash"]
        assert run["config_hash"]
        assert run["dataset_id"]
        stored_hash = run["result_hash"]

        first = client.post(f"/backtests/{run['id']}/verify", headers=headers)
        assert first.status_code == 200, first.text
        first_body = first.json()
        assert first_body["match"] is True
        assert first_body["dataset_ok"] is True
        assert first_body["result_hash_stored"] == stored_hash
        assert first_body["result_hash_recomputed"] == stored_hash

        second = client.post(f"/backtests/{run['id']}/verify", headers=headers)
        assert second.status_code == 200, second.text
        second_body = second.json()
        assert second_body["match"] is True
        assert second_body["result_hash_recomputed"] == stored_hash

        with factory() as session:
            row = session.get(BacktestRun, uuid.UUID(run["id"]))
            assert row is not None
            assert row.result_hash == stored_hash
            assert row.config_hash == run["config_hash"]


# --------------------------------------------------------------------------- #
# Walk-forward holdout
# --------------------------------------------------------------------------- #


def test_walk_forward_holdout_oos_labels_and_boundary() -> None:
    with _build_client(candle_seed=_seed_holdout_candles, candle_kwargs={"n": 120}) as (
        client,
        _factory,
        _,
    ):
        headers = _auth(client, "at034-int-a@test.example")
        strategy_id = _create_strategy(
            client,
            headers,
            name="Holdout",
            structured=_structured_payload(
                direction=TradeDirection.LONG,
                trigger=EntryTriggerType.BREAKOUT,
            ),
        )
        run = _run_backtest(
            client,
            headers,
            strategy_id,
            end_date="2024-03-01",
            fees_bps="0",
            slippage_bps="0",
            split_config={"mode": "holdout", "oos_fraction": 0.3},
        )
        assert run["status"] == "completed"
        result = run["result"]
        assert result is not None
        assert result.get("split_metrics") is not None

        trades_resp = client.get(f"/backtests/{run['id']}/trades", headers=headers)
        assert trades_resp.status_code == 200, trades_resp.text
        trades = trades_resp.json()["items"]
        assert trades
        labels = {t["split_label"] for t in trades}
        assert "in_sample" in labels
        assert "out_of_sample" in labels
        if any(t["split_label"] == "out_of_sample" for t in trades):
            assert result.get("oos_metrics") is not None

        n_bars = 120
        boundary_idx = floor(n_bars * 0.7)
        boundary_time = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=4) * boundary_idx
        for trade in trades:
            if trade["split_label"] == "out_of_sample":
                entry_time = datetime.fromisoformat(trade["entry_time"].replace("Z", "+00:00"))
                if entry_time.tzinfo is None:
                    entry_time = entry_time.replace(tzinfo=UTC)
                assert entry_time >= boundary_time


# --------------------------------------------------------------------------- #
# Short trades
# --------------------------------------------------------------------------- #


def test_short_trades_present_with_correct_signs() -> None:
    with _build_client(candle_seed=_seed_short_capable_candles, candle_kwargs={"n": 80}) as (
        client,
        _factory,
        _,
    ):
        headers = _auth(client, "at034-int-a@test.example")
        strategy_id = _create_strategy(
            client,
            headers,
            name="Short Strat",
            structured={
                "entry_rules": [
                    {
                        "trigger_type": EntryTriggerType.LIQUIDITY_SWEEP.value,
                        "direction": TradeDirection.SHORT.value,
                    }
                ],
                "exit_rules": [
                    {"rule_type": ExitRuleType.FIXED_STOP.value, "value": "2"},
                    {"rule_type": ExitRuleType.TP_MULTIPLE.value, "r_multiple": "1"},
                ],
            },
        )
        run = _run_backtest(
            client,
            headers,
            strategy_id,
            end_date="2024-01-14",
            fees_bps="0",
            slippage_bps="0",
        )
        assert run["status"] == "completed"

        trades_resp = client.get(f"/backtests/{run['id']}/trades", headers=headers)
        assert trades_resp.status_code == 200, trades_resp.text
        shorts = [t for t in trades_resp.json()["items"] if t["direction"] == "short"]
        assert shorts, "expected at least one short trade from engineered dataset"
        for trade in shorts:
            entry = Decimal(trade["entry_price"])
            exit_price = Decimal(trade["exit_price"])
            net = Decimal(trade["net_pnl"])
            if entry > exit_price:
                assert net > 0
            elif entry < exit_price:
                assert net < 0
        winner = next((t for t in shorts if Decimal(t["net_pnl"]) > 0), None)
        assert winner is not None, "expected a winning short (entry > exit)"
        assert Decimal(winner["entry_price"]) > Decimal(winner["exit_price"])


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #


def test_cancel_queued_run_via_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.api.routes.backtests.enqueue_backtest_if_needed",
        lambda **_kwargs: None,
    )
    with _build_client(
        backtest_sync_max_bars=50,
        candle_seed=_seed_pullback_candles,
        candle_kwargs={"n": 120},
    ) as (
        client,
        _factory,
        _,
    ):
        headers = _auth(client, "at034-int-a@test.example")
        strategy_id = _create_strategy(client, headers, name="Cancel Queued")
        run = _run_backtest(client, headers, strategy_id)
        assert run["status"] == "queued"
        run_id = run["id"]

        cancel = client.post(f"/backtests/{run_id}/cancel", headers=headers)
        assert cancel.status_code == 200, cancel.text
        assert cancel.json()["status"] == "cancelled"

        detail = client.get(f"/backtests/{run_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["status"] == "cancelled"


def test_execute_run_on_cancel_requested_converges_to_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.api.routes.backtests.enqueue_backtest_if_needed",
        lambda **_kwargs: None,
    )
    with _build_client(
        backtest_sync_max_bars=50,
        candle_seed=_seed_pullback_candles,
        candle_kwargs={"n": 120},
    ) as (client, factory, settings):
        headers = _auth(client, "at034-int-a@test.example")
        strategy_id = _create_strategy(client, headers, name="Cancel Running")
        queued = _run_backtest(client, headers, strategy_id)
        assert queued["status"] == "queued"
        run_id = uuid.UUID(queued["id"])

        with factory() as session:
            row = session.get(BacktestRun, run_id)
            assert row is not None
            row.status = BacktestRunStatus.RUNNING
            row.started_at = datetime.now(UTC)
            session.flush()
            service = BacktestService(session, settings)
            service.cancel(run_id, organization_id=ORG_A, user_id=USER_A)
            session.commit()
            assert row.status == BacktestRunStatus.CANCEL_REQUESTED

        with factory() as session:
            row = session.get(BacktestRun, run_id)
            assert row is not None
            row.status = BacktestRunStatus.QUEUED
            session.commit()

        with factory() as session:
            service = BacktestService(session, settings)
            result = service.execute_run(run_id, organization_id=ORG_A)
            session.commit()
            assert result.status == BacktestRunStatus.CANCELLED

        detail = client.get(f"/backtests/{run_id}", headers=headers)
        assert detail.json()["status"] == "cancelled"


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #


def test_idempotency_key_convergence() -> None:
    with _build_client() as (client, _factory, _):
        headers = _auth(client, "at034-int-a@test.example")
        strategy_id = _create_strategy(client, headers, name="Idem Int")
        payload = {"assumptions": _assumptions(), "idempotency_key": "int-bt-key-1"}
        first = client.post(f"/strategies/{strategy_id}/backtests", json=payload, headers=headers)
        second = client.post(f"/strategies/{strategy_id}/backtests", json=payload, headers=headers)
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]

        third = client.post(
            f"/strategies/{strategy_id}/backtests",
            json={"assumptions": _assumptions(), "idempotency_key": "int-bt-key-2"},
            headers=headers,
        )
        assert third.status_code == 200
        assert third.json()["id"] != first.json()["id"]


# --------------------------------------------------------------------------- #
# Bulk journal + journal intelligence endpoints
# --------------------------------------------------------------------------- #


def test_bulk_journal_dry_run_commit_and_journal_endpoints() -> None:
    with _build_client(candle_seed=_seed_pullback_candles) as (client, factory, _):
        headers = _auth(client, "at034-int-a@test.example")
        strategy_id = _create_strategy(client, headers, name="Journal Int")
        run = _run_backtest(client, headers, strategy_id)
        run_id = run["id"]
        assert run["status"] == "completed"

        dry = client.post(
            f"/backtests/{run_id}/journal-trades",
            json={"dry_run": True},
            headers=headers,
        )
        assert dry.status_code == 200, dry.text
        dry_body = dry.json()
        assert dry_body["committed"] is False
        assert dry_body["created_count"] + dry_body["duplicate_count"] == dry_body["total_rows"]

        commit = client.post(
            f"/backtests/{run_id}/journal-trades",
            json={"dry_run": False},
            headers=headers,
        )
        assert commit.status_code == 200, commit.text
        commit_body = commit.json()
        assert commit_body["committed"] is True
        assert commit_body["created_count"] > 0

        again = client.post(
            f"/backtests/{run_id}/journal-trades",
            json={"dry_run": False},
            headers=headers,
        )
        assert again.status_code == 200
        assert again.json()["duplicate_count"] == again.json()["total_rows"]
        assert again.json()["created_count"] == 0

        with factory() as session:
            journal_rows = session.scalars(
                select(JournalTrade).where(
                    JournalTrade.organization_id == ORG_A,
                    JournalTrade.source == JournalTradeSource.BACKTEST,
                )
            ).all()
            assert journal_rows
            sample = journal_rows[0]
            assert sample.entry_method.value == "auto"
            assert sample.external_ref.startswith(f"backtest:{run_id}:")
            assert sample.linked_backtest_trade_id is not None
            bt_trade_id = sample.external_ref.split(":")[-1]
            bt_resp = client.get(f"/backtests/{run_id}/trades", headers=headers)
            bt_trade = next(t for t in bt_resp.json()["items"] if t["id"] == bt_trade_id)
            if bt_trade.get("mfe_amount") is not None:
                assert sample.mfe_amount is not None

        stats = client.get("/journal/statistics", params={"group_by": "source"}, headers=headers)
        assert stats.status_code == 200, stats.text
        buckets = {b["key"]: b for b in stats.json()["buckets"]}
        assert "backtest" in buckets
        assert buckets["backtest"]["metrics"]["trade_count"] > 0

        comparison = client.get("/journal/comparison", headers=headers)
        assert comparison.status_code == 200, comparison.text
        cohorts = {c["cohort"]: c for c in comparison.json()["cohorts"]}
        assert set(cohorts) == {"human", "paper_system", "backtest"}
        assert cohorts["backtest"]["metrics"]["trade_count"] > 0

        evidence = client.get(
            "/journal/setup-evidence",
            params={"strategy_id": strategy_id},
            headers=headers,
        )
        assert evidence.status_code == 200, evidence.text
        items = evidence.json()["items"]
        assert items
        assert items[0]["tier"] in {"tier1", "tier2", "tier3"}


# --------------------------------------------------------------------------- #
# Tenant isolation + RBAC
# --------------------------------------------------------------------------- #


def test_tenant_isolation_second_org_gets_404() -> None:
    with _build_client() as (client, _factory, _):
        owner_a = _auth(client, "at034-int-a@test.example")
        org_b = _auth(client, "at034-int-b@test.example")
        strategy_id = _create_strategy(client, owner_a, name="Tenant Int")
        run = _run_backtest(client, owner_a, strategy_id)
        run_id = run["id"]

        assert client.get(f"/backtests/{run_id}", headers=org_b).status_code == 404
        assert client.get(f"/backtests/{run_id}/trades", headers=org_b).status_code == 404
        assert client.post(f"/backtests/{run_id}/cancel", headers=org_b).status_code == 404
        assert client.post(f"/backtests/{run_id}/verify", headers=org_b).status_code == 404
        assert (
            client.post(
                f"/backtests/{run_id}/journal-trades",
                json={"dry_run": True},
                headers=org_b,
            ).status_code
            == 404
        )


def test_viewer_rbac_mutations_forbidden_reads_allowed() -> None:
    with _build_client() as (client, _factory, _):
        owner = _auth(client, "at034-int-a@test.example")
        viewer = _auth(client, "at034-int-viewer@test.example")
        strategy_id = _create_strategy(client, owner, name="RBAC Int")
        run = _run_backtest(client, owner, strategy_id)
        run_id = run["id"]

        assert (
            client.post(
                f"/strategies/{strategy_id}/backtests",
                json={"assumptions": _assumptions()},
                headers=viewer,
            ).status_code
            == 403
        )
        assert client.post(f"/backtests/{run_id}/cancel", headers=viewer).status_code == 403
        assert client.post(f"/backtests/{run_id}/verify", headers=viewer).status_code == 403
        assert (
            client.post(
                f"/backtests/{run_id}/journal-trades",
                json={"dry_run": True},
                headers=viewer,
            ).status_code
            == 403
        )

        # Backtest GET routes use TraderDep; journal comparison/evidence use ReaderDep.
        assert client.get(f"/backtests/{run_id}", headers=viewer).status_code == 403
        assert client.get(f"/backtests/{run_id}/trades", headers=viewer).status_code == 403
        assert client.get("/journal/comparison", headers=viewer).status_code == 200
        assert client.get("/journal/setup-evidence", headers=viewer).status_code == 200


def test_backtest_rejects_strategy_version_from_another_strategy_or_org() -> None:
    """H7: ``strategy_version_id`` must belong to the path strategy (and its org)."""
    with _build_client() as (client, factory, _):
        owner_a = _auth(client, "at034-int-a@test.example")
        trader_b = _auth(client, "at034-int-b@test.example")
        victim_strategy = _create_strategy(client, owner_a, name="Victim Strat")
        own_other_strategy = _create_strategy(client, owner_a, name="Own Other")
        attacker_strategy = _create_strategy(client, trader_b, name="Attacker Strat")

        from app.db.models import UserStrategyVersion

        with factory() as session:
            victim_version = session.scalar(
                select(UserStrategyVersion).where(
                    UserStrategyVersion.strategy_id == uuid.UUID(victim_strategy)
                )
            )
            assert victim_version is not None
            victim_version_id = victim_version.id
            before = (victim_version.backtest_status, victim_version.validation_status)

        cross_org = client.post(
            f"/strategies/{attacker_strategy}/backtests",
            json={
                "assumptions": _assumptions(),
                "strategy_version_id": str(victim_version_id),
            },
            headers=trader_b,
        )
        assert cross_org.status_code == 404, cross_org.text

        same_org_wrong_strategy = client.post(
            f"/strategies/{own_other_strategy}/backtests",
            json={
                "assumptions": _assumptions(),
                "strategy_version_id": str(victim_version_id),
            },
            headers=owner_a,
        )
        assert same_org_wrong_strategy.status_code == 404, same_org_wrong_strategy.text

        with factory() as session:
            assert session.scalar(select(BacktestRun)) is None
            row = session.get(UserStrategyVersion, victim_version_id)
            assert row is not None
            assert (row.backtest_status, row.validation_status) == before

        own = client.post(
            f"/strategies/{victim_strategy}/backtests",
            json={
                "assumptions": _assumptions(),
                "strategy_version_id": str(victim_version_id),
            },
            headers=owner_a,
        )
        assert own.status_code == 200, own.text


def test_backtest_cancel_is_limited_to_run_creator(monkeypatch: pytest.MonkeyPatch) -> None:
    """M15: an org colleague can read a run but cannot cancel it (404)."""
    monkeypatch.setattr(
        "app.api.routes.backtests.enqueue_backtest_if_needed",
        lambda **_kwargs: None,
    )
    colleague_id = uuid.UUID("00000000-0000-0000-0000-000000034514")
    with _build_client(
        backtest_sync_max_bars=50,
        candle_seed=_seed_pullback_candles,
        candle_kwargs={"n": 120},
    ) as (client, factory, settings):
        with factory() as session:
            session.add(
                User(
                    id=colleague_id,
                    email="at034-int-colleague@test.example",
                    hashed_password=hash_password("SecurePass123!", settings),
                    email_verified=True,
                )
            )
            session.flush()
            session.add(
                Membership(user_id=colleague_id, organization_id=ORG_A, role=MembershipRole.TRADER)
            )
            session.commit()
        owner = _auth(client, "at034-int-a@test.example")
        colleague = _auth(client, "at034-int-colleague@test.example")
        strategy_id = _create_strategy(client, owner, name="Cancel Scope")
        run = _run_backtest(client, owner, strategy_id)
        assert run["status"] == "queued"

        assert client.get(f"/backtests/{run['id']}", headers=colleague).status_code == 200
        refused = client.post(f"/backtests/{run['id']}/cancel", headers=colleague)
        assert refused.status_code == 404, refused.text
        assert client.get(f"/backtests/{run['id']}", headers=owner).json()["status"] == "queued"

        cancelled = client.post(f"/backtests/{run['id']}/cancel", headers=owner)
        assert cancelled.status_code == 200, cancelled.text
