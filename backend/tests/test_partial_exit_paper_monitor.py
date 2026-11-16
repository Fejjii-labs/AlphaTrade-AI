"""Internal paper partial exits in the exit monitor.

Covers the paper side of Griller conditions 2, 3, 5, 7 and 8: TP legs reduce
the position bar by bar, the breakeven stop applies from the bar after TP1,
the runner trails closed bars only, the monitor resumes from durable records
without repeating a reduction, single-target plans (Nested 1-2, SFP) keep the
existing full-close path, and the kill switch or a paused worker holds every
reduction.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.schemas.common import TradeDirection
from app.services.paper_exit_monitor import (
    ExitBar,
    FetchedBars,
    OpenInternalPaperTrade,
    PartialExitRecorder,
    PartialExitStep,
    decide_exit,
    decide_partial_exits,
    parse_open_trade,
    partial_close_request_for,
    run_paper_exit_monitor,
)
from app.services.partial_exits.position import (
    ExitFill,
    PositionExitState,
    StopMove,
    rebuild_position_state,
)
from tests.test_paper_exit_monitor import (
    COMMAND,
    ENTRY,
    RecordingCloser,
    _bar,
    _parse_kwargs,
)

STEP = timedelta(minutes=15)


def _ladder_payload(
    *,
    direction: TradeDirection = TradeDirection.LONG,
    runner: bool = True,
    runner_rule: str = "closed-candle-trail",
    fractions: tuple[str, ...] = ("0.5", "0.3"),
    fees: str = "0.5",
) -> dict[str, object]:
    long = direction is TradeDirection.LONG
    prices = ("110", "120") if long else ("90", "80")
    runner_fraction = Decimal("1") - sum(Decimal(fraction) for fraction in fractions)
    return {
        "timeframe": "15m",
        "instrument_rules": {
            "contract_multiplier": "1",
            "tick_size": "0.1",
            "lot_size": "0.001",
            "minimum_quantity": "0.001",
            "minimum_notional": "0",
        },
        "risk_and_exits": {
            "stop": {"value": "95" if long else "105", "unit": "USDT"},
            "targets": [
                {
                    "order": index,
                    "price": {"value": price, "unit": "USDT"},
                    "quantity_fraction": fraction,
                    "derivation": {"formula_id": "fixture", "formula_version": "1"},
                }
                for index, (price, fraction) in enumerate(zip(prices, fractions), start=1)
            ],
            "runner": {
                "enabled": runner,
                "activation_target_order": len(fractions) if runner else None,
                "remaining_quantity_fraction": str(runner_fraction) if runner else "0",
                "rule_id": runner_rule,
                "rule_version": "1",
                "expression": "lookback_bars=2",
            },
            "fee_allowance": {"value": fees, "unit": "USDT"},
            "funding_allowance": {"value": "0", "unit": "USDT"},
            "slippage_allowance": {"value": "0", "unit": "USDT"},
        },
    }


def _ladder_trade(
    *,
    direction: TradeDirection = TradeDirection.LONG,
    size: str = "1.000",
    **payload: object,
) -> OpenInternalPaperTrade:
    parsed = parse_open_trade(
        **_parse_kwargs(
            direction=direction,
            semantic_payload=_ladder_payload(direction=direction, **payload),  # type: ignore[arg-type]
        ),
        position_size=Decimal(size),
        partial_exits_enabled=True,
    )
    assert parsed is not None and parsed.partial is not None
    return parsed


def _bars(*rows: tuple[str, str], start: datetime = ENTRY) -> tuple[ExitBar, ...]:
    return tuple(
        _bar(start + STEP * index, low=low, high=high) for index, (low, high) in enumerate(rows)
    )


def _decide(
    trade: OpenInternalPaperTrade,
    bars: tuple[ExitBar, ...],
    *,
    state: PositionExitState | None = None,
    evaluated_at: datetime | None = None,
) -> PartialExitStep:
    assert trade.partial is not None
    return decide_partial_exits(
        trade,
        state if state is not None else trade.partial.open_state(),
        bars,
        source_name="test-perpetual",
        evaluated_at=evaluated_at or max(bar.interval_end for bar in bars),
    )


# Parsing: the flag gates multi-target plans; single-target parsing is unchanged.


def test_multi_target_plan_is_out_of_scope_when_the_flag_is_off() -> None:
    kwargs = _parse_kwargs(semantic_payload=_ladder_payload())
    assert parse_open_trade(**kwargs, position_size=Decimal("1.000")) is None
    assert (
        parse_open_trade(**kwargs, position_size=Decimal("1.000"), partial_exits_enabled=False)
        is None
    )


def test_flag_on_parses_the_ladder() -> None:
    trade = _ladder_trade()
    assert trade.partial is not None
    ladder = trade.partial.ladder
    assert [leg.quantity for leg in ladder.legs] == [
        Decimal("0.500"),
        Decimal("0.300"),
        Decimal("0.200"),
    ]
    assert trade.partial.cost_per_unit == Decimal("0.5")
    assert trade.target == Decimal("120")


@pytest.mark.parametrize(
    "payload",
    [
        {"runner_rule": "atr-chandelier"},
        {"fractions": ("0.5", "0.4")},
    ],
)
def test_unsupported_ladders_are_refused_even_with_the_flag_on(
    payload: dict[str, object],
) -> None:
    kwargs = _parse_kwargs(semantic_payload=_ladder_payload(**payload))  # type: ignore[arg-type]
    assert (
        parse_open_trade(**kwargs, position_size=Decimal("1.000"), partial_exits_enabled=True)
        is None
    )


def test_ladder_without_a_recorded_size_is_refused() -> None:
    kwargs = _parse_kwargs(semantic_payload=_ladder_payload())
    assert parse_open_trade(**kwargs, partial_exits_enabled=True) is None


@pytest.mark.parametrize(
    "formula", ["nested-measured-impulse", "sfp-nearest-opposing-structure", "paper-internal-1r"]
)
@pytest.mark.parametrize("direction", [TradeDirection.LONG, TradeDirection.SHORT])
def test_single_target_nested_and_sfp_plans_are_unchanged_by_the_flag(
    formula: str, direction: TradeDirection
) -> None:
    long = direction is TradeDirection.LONG
    payload = _parse_kwargs()["semantic_payload"]
    assert isinstance(payload, dict)
    payload = deepcopy(payload)
    risk = payload["risk_and_exits"]
    risk["stop"]["value"] = "95" if long else "105"
    risk["targets"][0]["price"]["value"] = "110" if long else "90"
    risk["targets"][0]["derivation"] = {"formula_id": formula, "formula_version": "1"}
    kwargs = _parse_kwargs(direction=direction, semantic_payload=payload)
    baseline = parse_open_trade(**kwargs)
    flagged = parse_open_trade(**kwargs, position_size=Decimal("1"), partial_exits_enabled=True)
    assert baseline is not None
    assert flagged == baseline
    assert flagged.partial is None
    bars = _bars(("96", "111") if long else ("89", "104"))
    decision = decide_exit(
        flagged, bars, source_name="test-perpetual", evaluated_at=bars[-1].interval_end
    )
    assert decision is not None and decision.reason == "target_hit"
    report = run_paper_exit_monitor(
        enabled=True,
        dry_run=False,
        trades=(flagged,),
        candles=lambda _trade: FetchedBars(bars=bars, covers_entry=True, source_name="t"),
        closer=(closer := RecordingCloser()),
        evaluated_at=bars[-1].interval_end,
        partial=_ExplodingRecorder(),
    )
    assert report.closed == 1
    assert closer.requests[0].exit_fills is None


# Bar-by-bar ladder decisions (conditions 2, 3, 5).


def test_tp1_reduces_and_breakeven_applies_from_the_next_bar_only() -> None:
    trade = _ladder_trade()
    # Bar 0 tags TP1 (110) and dips to 100.2, below the 100.5 breakeven but
    # above the 95 stop: no same-bar protection credit, so no stop exit.
    # Bar 1 trades through 100.5: the remaining 0.5 exits at breakeven.
    step = _decide(trade, _bars(("100.2", "110"), ("100.0", "105")))
    assert [(fill.kind, fill.leg_order, fill.quantity, fill.price) for fill in step.fills] == [
        ("target", 1, Decimal("0.500"), Decimal("110")),
        ("stop", None, Decimal("0.500"), Decimal("100.5")),
    ]
    assert [(move.key, move.new_stop) for move in step.stop_moves] == [
        ("breakeven", Decimal("100.5"))
    ]
    assert step.stop_moves[0].effective_at == ENTRY + STEP
    assert step.state.is_closed


def test_same_bar_stop_and_tp1_assumes_the_stop_for_the_whole_position() -> None:
    trade = _ladder_trade()
    step = _decide(trade, _bars(("94", "111")))
    assert [(fill.kind, fill.quantity, fill.price) for fill in step.fills] == [
        ("stop", Decimal("1.000"), Decimal("95"))
    ]
    assert step.stop_moves == ()


def test_one_bar_through_two_targets_fills_both_legs_in_order() -> None:
    trade = _ladder_trade()
    step = _decide(trade, _bars(("101", "121")))
    assert [(fill.leg_order, fill.quantity) for fill in step.fills] == [
        (1, Decimal("0.500")),
        (2, Decimal("0.300")),
    ]
    assert step.state.remaining_quantity == Decimal("0.200")
    assert not step.state.is_closed


def test_runner_trails_closed_bars_and_exits_on_its_stop() -> None:
    trade = _ladder_trade()
    bars = _bars(
        ("101", "121"),  # TP1 + TP2; runner active; trail from bars 0..0 needs 2 bars
        ("118", "125"),  # trail = min(101, 118) = 101 -> tighter than 100.5
        ("122", "128"),  # trail = min(118, 122) = 118
        ("117", "126"),  # touches 118: runner exits at 118
    )
    step = _decide(trade, bars)
    assert [(fill.kind, fill.quantity, fill.price) for fill in step.fills][-1] == (
        "runner_stop",
        Decimal("0.200"),
        Decimal("118"),
    )
    assert [move.new_stop for move in step.stop_moves] == [
        Decimal("100.5"),
        Decimal("101"),
        Decimal("118"),
    ]
    assert step.state.is_closed


def test_runner_ignores_a_forming_bar() -> None:
    trade = _ladder_trade()
    closed = _bars(("101", "121"), ("118", "125"))
    forming = _bar(ENTRY + STEP * 2, low="130", high="140")
    step = _decide(trade, (*closed, forming), evaluated_at=closed[-1].interval_end + STEP / 2)
    assert step.state.current_stop == Decimal("101")
    assert all(move.effective_at <= closed[-1].interval_end for move in step.stop_moves)


def test_short_ladder_mirrors_the_long_one() -> None:
    trade = _ladder_trade(direction=TradeDirection.SHORT)
    step = _decide(trade, _bars(("90", "99.8"), ("94", "100")))
    assert [(fill.kind, fill.quantity, fill.price) for fill in step.fills] == [
        ("target", Decimal("0.500"), Decimal("90")),
        ("stop", Decimal("0.500"), Decimal("99.5")),
    ]
    assert step.stop_moves[0].new_stop == Decimal("99.5")


def test_resuming_from_durable_records_does_not_repeat_a_reduction() -> None:
    trade = _ladder_trade()
    assert trade.partial is not None
    bars = _bars(("101", "110"), ("104", "108"))
    first = _decide(trade, bars)
    assert [fill.leg_order for fill in first.fills] == [1]
    restarted = rebuild_position_state(
        trade.partial.open_state(),
        fills=reversed(first.state.fills),
        stop_moves=first.state.stop_moves,
    )
    again = _decide(trade, bars, state=restarted)
    assert again.fills == ()
    assert again.stop_moves == ()
    later = _decide(trade, (*bars, _bar(ENTRY + STEP * 2, low="100.4", high="106")), state=restarted)
    assert [(fill.kind, fill.price) for fill in later.fills] == [("stop", Decimal("100.5"))]


def test_close_request_reconciles_per_fill_costs_and_pnl() -> None:
    trade = _ladder_trade(fees="0.7")
    step = _decide(trade, _bars(("101", "121"), ("118", "125"), ("122", "128"), ("117", "126")))
    request = partial_close_request_for(trade, step)
    assert request.exit_fills is not None
    assert sum(fill.quantity for fill in request.exit_fills) == Decimal("1.000")
    assert sum(fill.fees for fill in request.exit_fills) == request.fees == Decimal("0.7")
    assert [fill.fees for fill in request.exit_fills] == [
        Decimal("0.35"),
        Decimal("0.21"),
        Decimal("0.14"),
    ]
    assert request.exit_reason == "stop_hit"
    assert request.idempotency_key == f"paper-exit-monitor:{COMMAND}"
    weighted = sum(fill.price * fill.quantity for fill in request.exit_fills)
    assert request.exit_price == weighted / Decimal("1.000")


# Monitor wiring: recorder, dry run, kill switch and pause.


class FakeRecorder(PartialExitRecorder):
    def __init__(self) -> None:
        self.fills: dict[UUID, tuple[ExitFill, ...]] = {}
        self.moves: dict[UUID, tuple[StopMove, ...]] = {}
        self.steps: list[PartialExitStep] = []

    def load(self, trade: OpenInternalPaperTrade) -> tuple[tuple[ExitFill, ...], tuple[StopMove, ...]]:
        return self.fills.get(trade.command_id, ()), self.moves.get(trade.command_id, ())

    def record(self, trade: OpenInternalPaperTrade, step: PartialExitStep) -> object:
        self.steps.append(step)
        self.fills[trade.command_id] = step.state.fills
        self.moves[trade.command_id] = step.state.stop_moves
        return SimpleNamespace(replayed=False)


class _ExplodingRecorder(PartialExitRecorder):
    def load(self, trade: OpenInternalPaperTrade) -> tuple[tuple[ExitFill, ...], tuple[StopMove, ...]]:
        raise AssertionError("single-target trades never touch the partial recorder")

    def record(self, trade: OpenInternalPaperTrade, step: PartialExitStep) -> object:
        raise AssertionError("single-target trades never touch the partial recorder")


def _monitor(
    trade: OpenInternalPaperTrade,
    bars: tuple[ExitBar, ...],
    recorder: PartialExitRecorder | None,
    **kwargs: object,
) -> tuple[object, RecordingCloser]:
    closer = RecordingCloser()
    report = run_paper_exit_monitor(
        enabled=True,
        dry_run=bool(kwargs.pop("dry_run", False)),
        trades=(trade,),
        candles=lambda _trade: FetchedBars(bars=bars, covers_entry=True, source_name="t"),
        closer=closer,
        evaluated_at=bars[-1].interval_end,
        partial=recorder,
        **kwargs,  # type: ignore[arg-type]
    )
    return report, closer


def test_monitor_records_reductions_and_never_calls_the_single_closer() -> None:
    trade = _ladder_trade()
    recorder = FakeRecorder()
    bars = _bars(("101", "110"), ("104", "108"))
    report, closer = _monitor(trade, bars, recorder)
    assert report.reduced == 1  # type: ignore[attr-defined]
    assert report.closed == 0  # type: ignore[attr-defined]
    assert closer.requests == []
    assert len(recorder.steps) == 1
    report, _ = _monitor(trade, bars, recorder)
    assert report.reduced == 0  # type: ignore[attr-defined]
    assert len(recorder.steps) == 1


def test_monitor_without_a_recorder_refuses_ladder_trades() -> None:
    report, closer = _monitor(_ladder_trade(), _bars(("101", "121")), None)
    assert report.refused == 1  # type: ignore[attr-defined]
    assert closer.requests == []


def test_dry_run_does_not_record_reductions() -> None:
    recorder = FakeRecorder()
    report, _ = _monitor(_ladder_trade(), _bars(("101", "121")), recorder, dry_run=True)
    assert report.would_close == 1  # type: ignore[attr-defined]
    assert recorder.steps == []


@pytest.mark.parametrize("reason", ["kill_switch_active", "worker_paused"])
def test_kill_switch_or_pause_holds_every_reduction(reason: str) -> None:
    recorder = FakeRecorder()
    trade = _ladder_trade()
    report, _ = _monitor(
        trade,
        _bars(("101", "121")),
        recorder,
        partial_hold=lambda organization_id: reason,
    )
    assert recorder.steps == []
    assert report.held == 1  # type: ignore[attr-defined]
    released, _ = _monitor(
        trade, _bars(("101", "121")), recorder, partial_hold=lambda organization_id: None
    )
    assert released.reduced == 1  # type: ignore[attr-defined]


def test_partial_trade_dataclass_default_keeps_single_target_identity() -> None:
    trade = _ladder_trade()
    assert replace(trade, partial=None).partial is None
