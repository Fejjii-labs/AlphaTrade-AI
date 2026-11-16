"""Trailing runner (Griller condition 5): closed candles only, never widens."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.schemas.common import TradeDirection
from app.services.paper_exit_monitor import ExitBar
from app.services.partial_exits.runner import parse_runner_rule, trail_runner_stop

T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
STEP = timedelta(minutes=15)


def _bar(index: int, *, low: str, high: str) -> ExitBar:
    start = T0 + STEP * index
    return ExitBar(
        interval_start=start,
        interval_end=start + STEP,
        open=Decimal(low),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(high),
        source_event_id=f"bar-{index}",
        content_hash="b" * 64,
    )


RULE = parse_runner_rule(
    rule_id="closed-candle-trail", rule_version="1", expression="lookback_bars=2"
)


def test_unknown_runner_rules_are_refused() -> None:
    assert RULE is not None and RULE.lookback_bars == 2
    assert parse_runner_rule(rule_id="atr-chandelier", rule_version="1", expression="x") is None
    assert (
        parse_runner_rule(rule_id="closed-candle-trail", rule_version="2", expression="lookback_bars=2")
        is None
    )
    for expression in ("lookback_bars=0", "lookback_bars=abc", "lookback=2", "lookback_bars=2;x=1"):
        assert (
            parse_runner_rule(rule_id="closed-candle-trail", rule_version="1", expression=expression)
            is None
        )


def test_long_trail_uses_the_lowest_low_of_the_last_closed_bars() -> None:
    assert RULE is not None
    bars = (_bar(0, low="101", high="104"), _bar(1, low="103", high="106"), _bar(2, low="105", high="108"))
    stop = trail_runner_stop(
        rule=RULE,
        direction=TradeDirection.LONG,
        current_stop=Decimal("100.3"),
        bars=bars,
        evaluated_at=bars[-1].interval_end,
    )
    assert stop == Decimal("103")


def test_forming_bar_is_ignored() -> None:
    assert RULE is not None
    closed = (_bar(0, low="101", high="104"), _bar(1, low="103", high="106"))
    forming = _bar(2, low="110", high="112")
    evaluated_at = closed[-1].interval_end + timedelta(minutes=5)
    with_forming = trail_runner_stop(
        rule=RULE,
        direction=TradeDirection.LONG,
        current_stop=Decimal("100"),
        bars=(*closed, forming),
        evaluated_at=evaluated_at,
    )
    assert with_forming == Decimal("101")


def test_trail_never_widens() -> None:
    assert RULE is not None
    bars = (_bar(0, low="96", high="99"), _bar(1, low="97", high="99"))
    stop = trail_runner_stop(
        rule=RULE,
        direction=TradeDirection.LONG,
        current_stop=Decimal("100.3"),
        bars=bars,
        evaluated_at=bars[-1].interval_end,
    )
    assert stop == Decimal("100.3")


def test_short_trail_uses_highs_and_only_moves_down() -> None:
    assert RULE is not None
    bars = (_bar(0, low="90", high="96"), _bar(1, low="88", high="93"), _bar(2, low="85", high="91"))
    stop = trail_runner_stop(
        rule=RULE,
        direction=TradeDirection.SHORT,
        current_stop=Decimal("99.7"),
        bars=bars,
        evaluated_at=bars[-1].interval_end,
    )
    assert stop == Decimal("93")
    looser = trail_runner_stop(
        rule=RULE,
        direction=TradeDirection.SHORT,
        current_stop=Decimal("92"),
        bars=bars,
        evaluated_at=bars[-1].interval_end,
    )
    assert looser == Decimal("92")


@pytest.mark.parametrize("count", [0, 1])
def test_too_few_closed_bars_keep_the_stop(count: int) -> None:
    assert RULE is not None
    bars = tuple(_bar(index, low="105", high="108") for index in range(count))
    stop = trail_runner_stop(
        rule=RULE,
        direction=TradeDirection.LONG,
        current_stop=Decimal("100.3"),
        bars=bars,
        evaluated_at=T0 + STEP * 5,
    )
    assert stop == Decimal("100.3")
