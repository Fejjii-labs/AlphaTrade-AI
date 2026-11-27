"""Month in review on the 1st's daily summary, with lessons saved for review."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select

import app.services.demo_daily_summary as summary
from app.db import models
from app.services.demo_monthly_lessons import format_month_lines, lessons_from
from tests.test_demo_daily_summary import (  # noqa: F401  (world is a fixture)
    CLOSED,
    _delivered,
    _settings,
    _trade,
    world,
)

D = Decimal


def test_rules_need_two_trades_and_a_clear_share() -> None:
    assert lessons_from({"stop_hunted": 1, "level_broke": 1}, []) == []
    lessons = lessons_from(
        {"stop_hunted": 3, "level_broke": 1, "gave_back": 2, "won": 1, "won_left_on_table": 2},
        [SimpleNamespace(family="D-line", timeframe="1h", trades=6, net_r=D("-2.5"))],
    )
    assert [lesson.mistake_type for lesson in lessons] == [
        "stops_too_tight",
        "gave_back_profit",
        "exited_too_early",
        "strategy_underperforming",
    ]
    assert lessons[0].text.startswith("Stops were hunted in 3 of 6 losses")
    assert "D-line 1h lost -2.50R over 6 trades" in lessons[-1].text


def test_month_lines() -> None:
    lines = format_month_lines(date(2026, 11, 30), {"won_left_on_table": 2, "won": 1}, [], [])
    assert lines[0] == "\U0001f5d3 Month in review: November 2026"
    assert "after 2 of 3 winning exits" in lines[2]
    assert lines[-1].startswith("\U0001f4a1 No clear pattern")


def test_the_firsts_page_adds_the_month_and_saves_lessons(world: SimpleNamespace) -> None:  # noqa: F811
    trades = []
    for i, reason in enumerate(["stop_hunted", "stop_hunted", "level_broke"]):
        trade = _trade(world, "BTCUSDT", datetime(2026, 11, 10 + i, 3, tzinfo=UTC),
                       strategy_version_id=world.version, status=CLOSED, exit_price=D("95"),
                       exit_time=datetime(2026, 11, 10 + i, 9, tzinfo=UTC),
                       tags=[f"review:{reason}"])  # fmt: skip
        trades.append(trade)
    settings = _settings(world.org, world.account).model_copy(
        update={"governed_blofin_demo_user_id": str(world.user)}
    )
    with world.factory() as session:
        session.add_all(trades)
        session.commit()
    first = datetime(2026, 12, 1, 0, 16, tzinfo=UTC)
    assert summary.send_demo_daily_summary_if_due(settings, world.factory, now=first)
    text = _delivered(world)
    assert "\U0001f5d3 Month in review: November 2026" in text
    assert "\U0001f50d Why trades lost: 2 stop hunted, 1 level broke" in text
    assert "1. Stops were hunted in 2 of 3 losses" in text
    with world.factory() as session:
        saved = session.scalars(select(models.LessonCandidate)).all()
        assert [row.mistake_type for row in saved] == ["stops_too_tight"]
        assert saved[0].status == "pending_review"
