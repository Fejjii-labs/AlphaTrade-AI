"""Automatic review of closed bot demo trades (trade review, 27 Nov 2026)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.schemas.common import TradeDirection
from app.services.trade_review import LOOKAHEAD_BARS, Bar, bars_from_blofin, review_from_bars

D = Decimal
T0 = datetime(2026, 11, 26, tzinfo=UTC)
H = timedelta(hours=1)


def _bars(path: list[tuple[str, str]]) -> list[Bar]:
    return [Bar(T0 + i * H, D(hi), D(lo)) for i, (hi, lo) in enumerate(path)]


def _review(inside: list[tuple[str, str]], after: list[tuple[str, str]], exit_price: str,
            direction: TradeDirection = TradeDirection.LONG):  # fmt: skip
    long = direction is TradeDirection.LONG
    return review_from_bars(
        direction=direction,
        entry=D(100),
        stop=D(98) if long else D(102),
        exit_price=D(exit_price),
        entry_time=T0,
        exit_time=T0 + len(inside) * H,
        bars=_bars(inside + after),
        step=H,
    )


QUIET = [("100.5", "99.5")] * LOOKAHEAD_BARS


@pytest.mark.parametrize(
    "inside,after,exit_price,code",
    [
        ([("103", "99")], QUIET, "102", "won"),
        ([("103", "99")], [("106", "101"), *QUIET[1:]], "102", "won_left_on_table"),
        ([("102.5", "97.9")], QUIET, "98", "gave_back"),
        ([("101", "97.9")], [("103", "99"), *QUIET[1:]], "98", "stop_hunted"),
        ([("101", "97.9")], [("99", "95"), *QUIET[1:]], "98", "level_broke"),
        ([("101", "97.9")], QUIET, "98", "no_follow_through"),
    ],
)
def test_reason_codes(inside, after, exit_price, code) -> None:
    review = _review(inside, after, exit_price)
    assert review is not None and review.code == code
    assert review.summary.split(" Best")[0]


def test_r_values_and_short_mirror() -> None:
    review = _review([("103", "99")], QUIET, "102")
    assert (review.mfe_r, review.mae_r, review.result_r) == (D("1.50"), D("-0.50"), D("1.00"))
    short = _review([("101", "97")], QUIET, "98", TradeDirection.SHORT)
    assert short.code == "won" and short.mfe_r == D("1.50") and short.mae_r == D("-0.50")


def test_not_enough_candles_after_the_exit_or_no_risk_waits() -> None:
    assert _review([("103", "99")], QUIET[:-1], "102") is None
    assert review_from_bars(direction=TradeDirection.LONG, entry=D(100), stop=D(100),
                            exit_price=D(101), entry_time=T0, exit_time=T0 + H,
                            bars=_bars([("101", "99"), *QUIET]), step=H) is None  # fmt: skip


def test_blofin_rows_parse_and_skip_bad_rows() -> None:
    ms = int(T0.timestamp() * 1000)
    rows = [[str(ms + 3600000), "1", "3", "0.5", "2"], [str(ms), "1", "2", "0.9", "1.5"], ["x"]]
    bars = bars_from_blofin(rows)
    assert [(b.start, b.high, b.low) for b in bars] == [
        (T0, D(2), D("0.9")),
        (T0 + H, D(3), D("0.5")),
    ]


def test_weekly_loss_reasons_line() -> None:
    from app.services.demo_daily_summary import format_loss_reasons_line

    line = format_loss_reasons_line({"level_broke": 1, "stop_hunted": 2})
    assert line == "\U0001f50d Why trades lost: 2 stop hunted, 1 level broke"
