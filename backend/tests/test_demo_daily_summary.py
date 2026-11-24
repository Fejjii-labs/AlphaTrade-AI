"""Once-a-day governed BloFin demo Telegram summary."""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

import app.services.demo_daily_summary as summary
from app.core.config import Settings
from app.db import models
from app.db.position_exit_events import PositionExitEventRow
from app.providers.alert_delivery.base import AlertDeliveryResult
from app.providers.alert_delivery.telegram import TelegramAlertDeliveryProvider
from app.schemas import common
from app.services.safety_alerts import reset_safety_alert_dedup

D = Decimal
DUE = datetime(2026, 11, 24, 0, 16, tzinfo=UTC)
LONG, SHORT = common.TradeDirection.LONG, common.TradeDirection.SHORT
OPEN, CLOSED = common.JournalTradeStatus.OPEN, common.JournalTradeStatus.CLOSED


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def _settings(org: UUID, account: UUID | str, *, on: bool = True) -> Settings:
    return Settings(
        _env_file=None,
        telegram_bot_token="123456:simulated-token",
        telegram_chat_id="424242",
        telegram_safety_alert_allowlist=["demo_daily_summary"] if on else [],
        governed_blofin_demo_organization_id=str(org),
        governed_blofin_demo_account_id=str(account),
    )


def test_text_counting_and_r() -> None:
    day = summary.DemoDaySummary(
        day=date(2026, 11, 23),
        opened=(("BTCUSDT", "D-line v1"),),
        tp_fills=(("TP1", 2), ("TP2", 1)),
        closed=(("BTCUSDT", "D-line v1", D("1.85")), ("SOLUSDT", "s", None)),
        still_open=tuple(f"C{i}" for i in range(17)),
    )
    lines = summary.format_demo_daily_summary_text(day).splitlines()
    assert lines[1:8] == [
        "Opened: 1",
        "- BTCUSDT (D-line v1)",
        "TP fills: 3 (TP1 2, TP2 1)",
        "Closed: 2",
        "- BTCUSDT (D-line v1): +1.85R",
        "- SOLUSDT (s): R unknown",
        "Net R (gross, before fees): +1.85R (1 without R)",
    ]
    assert lines[8].startswith("Still open: 17 (C0, C1,") and lines[8].endswith(", and 2 more)")
    quiet = summary.format_demo_daily_summary_text(summary.DemoDaySummary(day=day.day))
    assert quiet.splitlines()[1:] == [
        "No demo trades opened or closed.",
        "TP fills: 0",
        "Still open: 0",
    ]
    a, b = uuid4(), uuid4()
    assert summary.count_tp_fills([(a, 1), (a, 1), (b, 1), (a, 3)]) == (("TP1", 2), ("TP3", 1))
    assert summary.final_r(LONG, D("100"), D("95"), D("109.25")) == D("1.85")
    assert summary.final_r(LONG, D("100"), D("101"), D("110")) is None
    assert summary.final_r(SHORT, D("200"), D("204"), D("206")) == D("-1.50")


TOUCHED: list[str] = []


def _no_db() -> Any:
    TOUCHED.append("db")  # the sender swallows errors, so record the call instead
    raise AssertionError("the database must not be read")


def test_off_unscoped_or_before_00_15_reads_nothing() -> None:
    org, account, factory = uuid4(), uuid4(), _no_db
    TOUCHED.clear()
    summary.reset_demo_daily_summary_memo()
    send = summary.send_demo_daily_summary_if_due
    assert not send(_settings(org, account, on=False), factory, now=DUE)  # type: ignore[arg-type]
    assert not send(_settings(org, ""), factory, now=DUE)  # type: ignore[arg-type]
    early = DUE - timedelta(minutes=2)
    assert not send(_settings(org, account), factory, now=early)  # type: ignore[arg-type]
    assert TOUCHED == []


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    url = os.getenv("PHASE1_POSTGRES_URL") or os.getenv("AT028_POSTGRES_URL")
    if not url:
        pytest.skip("Requires a disposable PHASE1_POSTGRES_URL or AT028_POSTGRES_URL")
    from tests.support.postgres_template import clone_database, drop_clone

    engine = clone_database(url)
    w = SimpleNamespace(factory=sessionmaker(bind=engine), org=uuid4(), user=uuid4(), sent=[])
    w.account, w.release, w.delivered = uuid4(), threading.Event(), threading.Event()
    w.entered = threading.Event()
    w.release.set()
    with w.factory() as session:
        session.add(models.Organization(id=w.org, name="Synthetic demo org"))
        session.add(models.User(id=w.user, email=f"{w.user.hex}@example.com", hashed_password="x"))
        strategy = models.UserStrategy(
            organization_id=w.org,
            user_id=w.user,
            name="D-line",
            setup_type=common.StrategyId.HTF_TREND_PULLBACK,
        )
        session.add(strategy)
        session.flush()
        version = models.UserStrategyVersion(strategy_id=strategy.id, version=1, card={})
        version.content_hash = "a" * 64
        session.add(version)
        session.commit()
        w.version = version.id

    def _deliver(_self: Any, payload: Any, **_kwargs: Any) -> AlertDeliveryResult:
        w.entered.set()
        w.release.wait(timeout=10)
        w.sent.append((payload.message, threading.current_thread().name))
        w.delivered.set()
        return AlertDeliveryResult(success=True, channel=common.AlertDeliveryChannel.TELEGRAM)

    monkeypatch.setattr(TelegramAlertDeliveryProvider, "deliver", _deliver)
    summary.reset_demo_daily_summary_memo()
    reset_safety_alert_dedup()
    yield w
    w.release.set()
    summary.reset_demo_daily_summary_memo()
    reset_safety_alert_dedup()
    drop_clone(engine)


def _delivered(w: SimpleNamespace) -> str:
    assert w.delivered.wait(timeout=5)
    w.delivered.clear()
    return str(w.sent[-1][0])


def _trade(w: SimpleNamespace, symbol: str, entry: datetime, **values: Any) -> Any:
    values = {"status": OPEN, "direction": LONG, "account_id": w.account} | values
    values.setdefault("planned_stop_price", D("95"))
    return models.JournalTrade(
        organization_id=w.org, user_id=w.user, source=common.JournalTradeSource.PAPER_EXECUTION,
        symbol=symbol, timeframe="15m", execution_lifecycle_id=uuid4(), entry_time=entry,
        entry_price=values.pop("entry_price", D("100")), **values,
    )  # fmt: skip


def _tp(
    w: SimpleNamespace, lifecycle: UUID, leg: int, at: datetime, venue: str = "BLOFIN_DEMO"
) -> Any:
    return PositionExitEventRow(
        organization_id=w.org, execution_lifecycle_id=lifecycle, venue=venue, event_key=uuid4().hex,
        event_kind="fill", fill_kind="target", leg_order=leg, quantity=D("0.1"), price=D("105"),
        occurred_at=at, evidence={},
    )  # fmt: skip


def _audit_rows(w: SimpleNamespace) -> int:
    with w.factory() as session:
        query = select(func.count()).where(models.AuditLog.resource_type == summary.AUDIT_RESOURCE)
        return int(session.scalar(query) or 0)


def test_seeded_day_with_an_open_a_tp_fill_and_closes(world: SimpleNamespace) -> None:
    done = {"status": CLOSED, "exit_price": D("101")}
    btc = _trade(world, "BTCUSDT", _at(23, 3), strategy_version_id=world.version, **done)
    btc.exit_time, btc.exit_price = _at(23, 18), D("109.25")
    xrp = _trade(world, "XRPUSDT", _at(22, 9), entry_price=D("1"), planned_stop_price=D("0.9"))
    xrp.status, xrp.exit_time, xrp.exit_price = CLOSED, _at(23, 1), D("0.95")
    ada = _trade(world, "ADAUSDT", _at(23, 5), account_id=uuid4())  # another account
    with world.factory() as session:
        session.add_all(
            [
                _trade(world, "SOLUSDT", _at(20, 9)),  # open since before the day
                xrp,
                btc,
                _trade(world, "ETHUSDT", _at(23, 20), strategy_label="SFP", direction=SHORT,
                       entry_price=D("200"), planned_stop_price=D("204")),
                # Closed after midnight: still open at the end of the day.
                _trade(world, "DOGEUSDT", _at(23, 23), exit_time=_at(24, 0), **done),
                ada,
                _trade(world, "BNBUSDT", _at(21, 1), exit_time=_at(22, 1), **done),  # day before
            ]
        )  # fmt: skip
        session.flush()
        session.add_all(
            [
                _tp(world, btc.execution_lifecycle_id, 1, _at(23, 10)),
                _tp(world, btc.execution_lifecycle_id, 1, _at(23, 10, 1)),  # TP1 second piece
                _tp(world, btc.execution_lifecycle_id, 2, _at(23, 14)),
                _tp(world, btc.execution_lifecycle_id, 3, _at(22, 14)),  # previous day
                _tp(world, btc.execution_lifecycle_id, 3, _at(23, 15), "PAPER_INTERNAL"),
                _tp(world, ada.execution_lifecycle_id, 4, _at(23, 16)),  # another account
            ]
        )
        session.commit()
    settings = _settings(world.org, world.account)
    assert summary.send_demo_daily_summary_if_due(settings, world.factory, now=DUE)
    assert _delivered(world).splitlines() == [
        "AlphaTrade DEMO daily summary, 2026-11-23 (UTC).",
        "Opened: 3",
        "- BTCUSDT (D-line v1)",
        "- ETHUSDT (SFP)",
        "- DOGEUSDT (unlabeled)",
        "TP fills: 2 (TP1 1, TP2 1)",
        "Closed: 2",
        "- XRPUSDT (unlabeled): -0.50R",
        "- BTCUSDT (D-line v1): +1.85R",
        "Net R (gross, before fees): +1.35R",
        "Still open: 3 (SOLUSDT, ETHUSDT, DOGEUSDT)",
    ]
    assert world.sent[0][1].startswith("telegram-alert")


def test_sent_once_per_day_across_a_restart_and_again_the_next_day(world: SimpleNamespace) -> None:
    settings, send = _settings(world.org, world.account), summary.send_demo_daily_summary_if_due
    assert send(settings, world.factory, now=DUE)
    assert _delivered(world).splitlines()[1] == "No demo trades opened or closed."
    TOUCHED.clear()
    assert not send(settings, _no_db, now=DUE) and TOUCHED == []  # type: ignore[arg-type]
    summary.reset_demo_daily_summary_memo()  # a restart forgets in-process state
    reset_safety_alert_dedup()
    assert not send(settings, world.factory, now=DUE + timedelta(hours=20))
    time.sleep(0.2)
    assert len(world.sent) == 1 and _audit_rows(world) == 1
    assert send(settings, world.factory, now=DUE + timedelta(days=1))
    assert "2026-11-24 (UTC)" in _delivered(world) and _audit_rows(world) == 2


def _reconcile(world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, reasons: Any) -> Any:
    """Run the worker hook with a fake venue reconcile that records a 23:58 close late."""
    from app.workers.watcher_paper import reconcile_governed_demo_once

    def _reconcile_pending(_self: Any, session: Any) -> tuple[str, ...]:
        late = _trade(world, "BTCUSDT", _at(23, 3), status=CLOSED, exit_price=D("110"))
        late.exit_time = _at(23, 23, 58)
        session.add(late)
        session.commit()
        return tuple(reasons)

    loop = type("Loop", (), {"__init__": lambda *_a: None, "reconcile_pending": _reconcile_pending})
    monkeypatch.setattr("app.services.governed_blofin_demo.GovernedBloFinDemoLoop", loop)
    monkeypatch.setattr("app.runtime.canonical.ProductionCanonicalRuntime", object)
    settings = _settings(world.org, world.account).model_copy(
        update={"governed_blofin_demo_enabled": True}
    )
    clock: Any = SimpleNamespace(now=lambda: DUE)
    return reconcile_governed_demo_once(settings, world.factory, clock, object())


def test_worker_hook_runs_after_reconcile_and_does_not_wait_on_telegram(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.release.clear()
    started = time.monotonic()
    assert _reconcile(world, monkeypatch, ["demo_closed_reconciled"]) == ("demo_closed_reconciled",)
    assert time.monotonic() - started < 2
    assert world.entered.wait(timeout=5) and world.sent == []  # still inside the HTTP send
    world.release.set()
    text = _delivered(world)
    assert "- BTCUSDT (unlabeled): +2.00R" in text and "Still open: 0" in text


def test_worker_hook_waits_while_the_venue_was_not_read(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reconcile(world, monkeypatch, ["demo_closed_reconciled", "demo_reconciliation_unavailable"])
    time.sleep(0.2)
    assert world.sent == [] and _audit_rows(world) == 0


def test_racing_workers_queue_one_summary(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    both, collect = threading.Barrier(2), summary.collect_demo_day_summary

    def _slow_collect(*args: Any, **kwargs: Any) -> Any:
        # Without the lock both workers get here together; with it, one waits 1 s.
        with contextlib.suppress(threading.BrokenBarrierError):
            both.wait(timeout=1)
        return collect(*args, **kwargs)

    monkeypatch.setattr(summary, "collect_demo_day_summary", _slow_collect)
    settings, results = _settings(world.org, world.account), []

    def _worker() -> None:
        results.append(summary.send_demo_daily_summary_if_due(settings, world.factory, now=DUE))

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    _delivered(world)
    time.sleep(0.2)
    assert sorted(results) == [False, True] and len(world.sent) == 1 and _audit_rows(world) == 1
