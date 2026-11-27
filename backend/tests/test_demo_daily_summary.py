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
        opened=(("BTC long", "D-line 1h"),),
        tp_fills=(("TP1", 2), ("TP2", 1)),
        closed=(
            ("BTC long", "D-line 1h", D("1.85"), D("1850")),
            ("ETH short", "Nested Continuation 1h", D("-1.06"), D("-1060")),
            ("SOL long", "Other 15m", None, None),
        ),
        still_open=tuple((f"C{i} long", "D-line 1h") for i in range(17)),
    )
    lines = summary.format_demo_daily_summary_text(day).splitlines()
    assert lines[:15] == [
        "\U0001f4ca AlphaTrade demo \u00b7 Mon 23 Nov 2026",
        "",
        "\U0001f7e2 Opened (1)",
        "\u2022 BTC long \u00b7 D-line 1h",
        "",
        "\U0001f3af Take-profits hit: 3 (TP1 \u00d72, TP2 \u00d71)",
        "",
        "\U0001f3c1 Closed (3)",
        "\u2705 BTC long \u00b7 D-line 1h \u00b7 +1.85R (\u2248 +$1,850)",
        "\u274c ETH short \u00b7 Nested Continuation 1h \u00b7 \u22121.06R (\u2248 \u2212$1,060)",
        "\u2754 SOL long \u00b7 Other 15m \u00b7 R unknown",
        "\U0001f4c8 Day result: +0.79R after fees (\u2248 +$790) \u00b7 1 win, 1 loss "
        "(1 without R)",
        "",
        "\u23f3 Still open (17)",
        "\u2022 C0 long \u00b7 D-line 1h",
    ]
    assert lines[-1] == "and 2 more"
    # No ids, hashes or versions anywhere.
    assert "v1" not in "\n".join(lines)
    quiet = summary.format_demo_daily_summary_text(summary.DemoDaySummary(day=day.day))
    assert quiet.splitlines()[1:] == [
        "",
        "No demo trades opened or closed today.",
        "",
        "\U0001f3af Take-profits hit: 0",
        "",
        "\u23f3 Still open (0)",
        "None",
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
    family = summary.strategy_family_name(common.StrategyId.HTF_TREND_PULLBACK)
    assert _delivered(world).splitlines() == [
        "\U0001f4ca AlphaTrade demo \u00b7 Mon 23 Nov 2026",
        "",
        "\U0001f7e2 Opened (3)",
        f"\u2022 BTC long \u00b7 {family} 15m",
        "\u2022 ETH short \u00b7 Other 15m",
        "\u2022 DOGE long \u00b7 Other 15m",
        "",
        "\U0001f3af Take-profits hit: 2 (TP1 \u00d71, TP2 \u00d71)",
        "",
        "\U0001f3c1 Closed (2)",
        "\u274c XRP long \u00b7 Other 15m \u00b7 \u22120.51R",
        f"\u2705 BTC long \u00b7 {family} 15m \u00b7 +1.82R",
        "\U0001f4c8 Day result: +1.31R after fees \u00b7 1 win, 1 loss",
        "",
        "\u23f3 Still open (3)",
        "\u2022 SOL long \u00b7 Other 15m",
        "\u2022 ETH short \u00b7 Other 15m",
        "\u2022 DOGE long \u00b7 Other 15m",
    ]
    assert world.sent[0][1].startswith("telegram-alert")


def test_sent_once_per_day_across_a_restart_and_again_the_next_day(world: SimpleNamespace) -> None:
    settings, send = _settings(world.org, world.account), summary.send_demo_daily_summary_if_due
    assert send(settings, world.factory, now=DUE)
    assert _delivered(world).splitlines()[2] == "No demo trades opened or closed today."
    TOUCHED.clear()
    assert not send(settings, _no_db, now=DUE) and TOUCHED == []  # type: ignore[arg-type]
    summary.reset_demo_daily_summary_memo()  # a restart forgets in-process state
    reset_safety_alert_dedup()
    assert not send(settings, world.factory, now=DUE + timedelta(hours=20))
    time.sleep(0.2)
    assert len(world.sent) == 1 and _audit_rows(world) == 1
    assert send(settings, world.factory, now=DUE + timedelta(days=1))
    assert "Tue 24 Nov 2026" in _delivered(world) and _audit_rows(world) == 2


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
    assert (
        "\u2705 BTC long \u00b7 Other 15m \u00b7 +1.97R" in text and "\u23f3 Still open (0)" in text
    )


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


def test_week_lines_net_r_after_fees_and_dollars() -> None:
    # Long 100 -> 110, stop 95: (10 - 210 * 0.0006) / 5 = 1.97R.
    assert summary.net_r_after_fees(LONG, D("100"), D("95"), D("110")) == D("1.97")
    # Short stopped out at 204: (-4 - 404 * 0.0006) / 4 = -1.06R.
    assert summary.net_r_after_fees(SHORT, D("200"), D("204"), D("204")) == D("-1.06")
    assert summary.net_r_after_fees(LONG, D("100"), D("100"), D("101")) is None
    rows = [
        summary.StrategyWeekRow("D-line", "30m", 3, 2, D("2.50"), D("2500")),
        summary.StrategyWeekRow("Nested Continuation", "1h", 2, 0, D("-2.12"), D("-2120.4"), 1),
    ]
    lines = summary.format_strategy_week_lines(
        rows, first_day=date(2026, 11, 16), last_day=date(2026, 11, 22)
    )
    assert lines == [
        "\U0001f4c5 Last 7 days by strategy (16 Nov \u2013 22 Nov)",
        "\u2022 D-line 30m: 3 trades, 2 won (67%), +2.50R, +0.83R per trade (\u2248 +$2,500)",
        "\u2022 Nested Continuation 1h: 2 trades, 0 won (0%), \u22122.12R, \u22122.12R per trade "
        "(\u2248 \u2212$2,120), 1 without R",
        "Week total: 5 trades, +0.38R after fees (\u2248 +$380)",
    ]
    empty = summary.format_strategy_week_lines(
        [], first_day=date(2026, 11, 16), last_day=date(2026, 11, 22)
    )
    assert empty[1] == "No demo trades closed."


def test_monday_page_adds_the_week_by_strategy(world: SimpleNamespace) -> None:
    """Sunday 22 Nov's summary, sent Monday: the 7 days 16-22 Nov by family and timeframe."""

    win = _trade(
        world,
        "BTCUSDT",
        _at(18, 3),
        strategy_version_id=world.version,
        status=CLOSED,
        exit_price=D("110"),
        planned_risk_amount=D("1000"),
        exit_time=_at(18, 9),
    )
    loss = _trade(
        world,
        "ETHUSDT",
        _at(22, 3),
        strategy_version_id=world.version,
        status=CLOSED,
        direction=SHORT,
        entry_price=D("200"),
        planned_stop_price=D("204"),
        exit_price=D("204"),
        planned_risk_amount=D("1000"),
        exit_time=_at(22, 9),
    )
    old = _trade(
        world,
        "BTCUSDT",
        _at(15, 3),
        strategy_version_id=world.version,
        status=CLOSED,
        exit_price=D("110"),
        planned_risk_amount=D("1000"),
        exit_time=_at(15, 23),
    )
    with world.factory() as session:
        session.add_all([win, loss, old])
        session.commit()
    settings = _settings(world.org, world.account)
    monday = datetime(2026, 11, 23, 0, 16, tzinfo=UTC)
    assert summary.send_demo_daily_summary_if_due(settings, world.factory, now=monday)
    text = _delivered(world)
    assert text.splitlines()[0] == "\U0001f4ca AlphaTrade demo \u00b7 Sun 22 Nov 2026"
    week = ("\U0001f4c5" + text.split("\n\n\U0001f4c5", 1)[1]).splitlines()
    family = summary.strategy_family_name(common.StrategyId.HTF_TREND_PULLBACK)
    assert week == [
        "\U0001f4c5 Last 7 days by strategy (16 Nov \u2013 22 Nov)",
        f"\u2022 {family} 15m: 2 trades, 1 won (50%), +0.91R, +0.46R per trade (\u2248 +$910)",
        "Week total: 2 trades, +0.91R after fees (\u2248 +$910)",
    ]
