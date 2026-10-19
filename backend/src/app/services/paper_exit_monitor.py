"""Stop and target exits for open canonical internal paper trades.

Decisions use closed candles supplied by the caller. A hit is recorded only
through :meth:`ExecutionService.close_canonical_paper_plan` (see the worker
adapter). This module does not update journal rows and does not call an
exchange. Manual BloFin demo trades and governed BloFin demo plans are outside
the query and the parser.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

import structlog
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.db.canonical_candidates import CanonicalCandidateRow
from app.db.canonical_trade_plans import (
    PLAN_AUTHORITY_CANONICAL,
    CanonicalTradePlanLineageRow,
)
from app.db.models import JournalTrade, TradePlanRevision
from app.guardrails.redaction import redact_text
from app.schemas.common import JournalTradeStatus, Timeframe, TradeDirection
from app.schemas.execution_protocol import ClosePaperPlanRequest

logger = structlog.get_logger(__name__)

INTERNAL_PAPER_VENUE = "PAPER_INTERNAL"
INTERNAL_PAPER_POLICY = "paper-internal-entry/v1"
EXIT_CHANNEL = "PAPER_EXIT_MONITOR"
EXIT_ACTOR = "paper_exit_monitor"
# A crossing on an earlier UTC day is a reconstruction. It is journaled and
# kept out of the consecutive-loss breaker, the daily lock, and ADR-109 samples.
EXIT_ORIGIN_BACKFILL = "backfill"
EXIT_ORIGIN_LIVE = "live"
CALIBRATION_EXCLUDED = "excluded"
CALIBRATION_ELIGIBLE = "eligible"
EXIT_REASON_STOP: Literal["stop_hit"] = "stop_hit"
EXIT_REASON_TARGET: Literal["target_hit"] = "target_hit"
SAME_BAR_ASSUMPTION = "stop_first"
SAME_BAR_NONE = "none"

ExitReason = Literal["stop_hit", "target_hit"]
_SKIP_CLOSE_REASONS = frozenset(
    {
        "already_closed",
        "idempotency_conflict",
        "demo_synthetic_close_forbidden",
    }
)


@dataclass(frozen=True, slots=True)
class ExitBar:
    """One closed candle the monitor is allowed to read."""

    interval_start: datetime
    interval_end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    source_event_id: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class FetchedBars:
    """Closed bars plus whether they reach back to the trade entry."""

    bars: tuple[ExitBar, ...]
    covers_entry: bool
    source_name: str


@dataclass(frozen=True, slots=True)
class OpenInternalPaperTrade:
    """One open canonical internal-paper fill with its immutable exit levels."""

    journal_trade_id: UUID
    organization_id: UUID
    user_id: UUID
    account_id: UUID
    revision_id: UUID
    command_id: UUID
    symbol: str
    timeframe: Timeframe
    direction: TradeDirection
    entry_price: Decimal
    entry_time: datetime
    stop: Decimal
    target: Decimal
    fees: Decimal
    funding: Decimal
    slippage: Decimal
    evidence_venue: str | None = None
    evidence_source_family: str | None = None
    evidence_is_live: bool | None = None
    evidence_is_mock: bool | None = None


@dataclass(frozen=True, slots=True)
class ExitDecision:
    """The first closed bar that touched a level. Both-in-bar chooses the stop."""

    trade: OpenInternalPaperTrade
    reason: ExitReason
    exit_price: Decimal
    occurred_at: datetime
    evidence: dict[str, str]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class MonitorReport:
    """One pass. ``disarmed`` performs no reads and no writes."""

    status: str
    examined: int = 0
    closed: int = 0
    replayed: int = 0
    would_close: int = 0
    skipped: int = 0
    errors: int = 0
    error: str = ""


class PaperExitCloser:
    """Records one close. The production adapter calls the paper close service."""

    def close(self, request: ClosePaperPlanRequest) -> object:
        raise NotImplementedError


def exit_idempotency_key(command_id: UUID) -> str:
    """One stable close identity per execution command."""

    return f"paper-exit-monitor:{command_id}"


def exit_monitor_backfill(request: ClosePaperPlanRequest) -> bool:
    """True when the monitor labelled this close as a historical reconstruction."""

    evidence = request.exit_evidence
    if not evidence:
        return False
    return evidence.get("exit_origin") == EXIT_ORIGIN_BACKFILL


def counts_for_adr109_calibration(request: ClosePaperPlanRequest) -> bool:
    """ADR-109 samples include a live monitor close and exclude a backfill.

    A human close has no monitor evidence, so it is not a monitor sample.
    This predicate does not write a row. The close path does not invent a
    walk-forward fold, so no calibration sample is inserted here.
    """

    if exit_monitor_backfill(request):
        return False
    evidence = request.exit_evidence
    if evidence is None:
        return False
    return evidence.get("calibration_sample") == CALIBRATION_ELIGIBLE


def classify_exit_origin(occurred_at: datetime, evaluated_at: datetime) -> str:
    """Label a crossing that happened before the evaluation UTC day as a backfill.

    The open October 6 trades close on the first armed run at those historical
    bars. A naive timestamp fails closed to ``backfill`` so it cannot move the
    breaker. A crossing on the evaluation UTC day is live.
    """

    if occurred_at.tzinfo is None or evaluated_at.tzinfo is None:
        return EXIT_ORIGIN_BACKFILL
    occurred = occurred_at.astimezone(UTC)
    evaluated = evaluated_at.astimezone(UTC)
    if occurred.date() < evaluated.date():
        return EXIT_ORIGIN_BACKFILL
    return EXIT_ORIGIN_LIVE


def is_internal_paper_plan(
    *,
    plan_authority: str,
    execution_venue: str,
    execution_policy_version: str,
) -> bool:
    """Canonical internal paper only. Demo and manual-demo plans are excluded."""

    return (
        plan_authority == PLAN_AUTHORITY_CANONICAL
        and execution_venue == INTERNAL_PAPER_VENUE
        and execution_policy_version == INTERNAL_PAPER_POLICY
    )


def open_internal_paper_statement() -> Select[
    tuple[JournalTrade, TradePlanRevision, dict[str, object]]
]:
    """Open journal rows joined to canonical internal-paper plan revisions."""

    return (
        select(JournalTrade, TradePlanRevision, CanonicalCandidateRow.evidence_identity)
        .join(TradePlanRevision, TradePlanRevision.id == JournalTrade.trade_plan_revision_id)
        .outerjoin(
            CanonicalTradePlanLineageRow,
            CanonicalTradePlanLineageRow.revision_id == TradePlanRevision.id,
        )
        .outerjoin(
            CanonicalCandidateRow,
            CanonicalCandidateRow.candidate_id == CanonicalTradePlanLineageRow.candidate_id,
        )
        .where(
            JournalTrade.status == JournalTradeStatus.OPEN,
            JournalTrade.execution_lifecycle_id.is_not(None),
            JournalTrade.trade_plan_revision_id.is_not(None),
            TradePlanRevision.plan_authority == PLAN_AUTHORITY_CANONICAL,
            TradePlanRevision.execution_venue == INTERNAL_PAPER_VENUE,
            TradePlanRevision.execution_policy_version == INTERNAL_PAPER_POLICY,
        )
        .order_by(JournalTrade.entry_time.asc())
    )


def load_open_internal_paper_trades(
    session: Session,
) -> tuple[tuple[OpenInternalPaperTrade, ...], int]:
    """Return parsed open trades and how many scoped rows were rejected."""

    rows = session.execute(open_internal_paper_statement()).all()
    trades: list[OpenInternalPaperTrade] = []
    rejected = 0
    for trade, plan, evidence_identity in rows:
        parsed = parse_open_trade(
            journal_trade_id=trade.id,
            organization_id=trade.organization_id,
            user_id=trade.user_id,
            account_id=trade.account_id,
            command_id=trade.execution_lifecycle_id,
            direction=trade.direction,
            entry_price=trade.entry_price,
            entry_time=trade.entry_time,
            journal_symbol=trade.symbol,
            journal_timeframe=trade.timeframe,
            plan_authority=plan.plan_authority,
            execution_venue=plan.execution_venue,
            execution_policy_version=plan.execution_policy_version,
            execution_instrument=plan.execution_instrument,
            plan_organization_id=plan.organization_id,
            plan_user_id=plan.user_id,
            plan_account_id=plan.account_id,
            revision_id=plan.id,
            semantic_payload=plan.semantic_payload,
            evidence_identity=evidence_identity,
        )
        if parsed is None:
            rejected += 1
            logger.info(
                "paper_exit_monitor_trade_rejected",
                journal_trade_id=str(trade.id),
            )
            continue
        trades.append(parsed)
    return tuple(trades), rejected


def parse_open_trade(
    *,
    journal_trade_id: UUID,
    organization_id: UUID,
    user_id: UUID,
    account_id: UUID | None,
    command_id: UUID | None,
    direction: TradeDirection,
    entry_price: Decimal | None,
    entry_time: datetime | None,
    journal_symbol: str,
    journal_timeframe: str,
    plan_authority: str,
    execution_venue: str,
    execution_policy_version: str,
    execution_instrument: str,
    plan_organization_id: UUID,
    plan_user_id: UUID,
    plan_account_id: UUID,
    revision_id: UUID,
    semantic_payload: object,
    evidence_identity: object | None = None,
) -> OpenInternalPaperTrade | None:
    """Build one monitor trade, or return None when the row is out of scope.

    Internal paper plans have one full-size target and a disabled runner.
    Anything else, including a BloFin venue or policy, is left untouched.
    """

    if not is_internal_paper_plan(
        plan_authority=plan_authority,
        execution_venue=execution_venue,
        execution_policy_version=execution_policy_version,
    ):
        return None
    if account_id is None or command_id is None or entry_price is None or entry_time is None:
        return None
    if entry_time.tzinfo is None:
        return None
    if (
        organization_id != plan_organization_id
        or user_id != plan_user_id
        or account_id != plan_account_id
    ):
        return None
    if not isinstance(semantic_payload, dict):
        return None
    symbol = execution_instrument.strip().upper()
    if not symbol or symbol != journal_symbol.strip().upper():
        return None
    payload_timeframe = semantic_payload.get("timeframe")
    if not isinstance(payload_timeframe, str) or payload_timeframe != journal_timeframe:
        return None
    try:
        timeframe = Timeframe(payload_timeframe)
    except ValueError:
        return None
    levels = _exit_levels(semantic_payload)
    if levels is None:
        return None
    stop, target, fees, funding, slippage = levels
    if direction is TradeDirection.LONG and not (stop < entry_price < target):
        return None
    if direction is TradeDirection.SHORT and not (target < entry_price < stop):
        return None
    if direction not in {TradeDirection.LONG, TradeDirection.SHORT}:
        return None
    venue, family, is_live, is_mock = evidence_flags(evidence_identity)
    return OpenInternalPaperTrade(
        journal_trade_id=journal_trade_id,
        organization_id=organization_id,
        user_id=user_id,
        account_id=account_id,
        revision_id=revision_id,
        command_id=command_id,
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        entry_price=entry_price,
        entry_time=entry_time,
        stop=stop,
        target=target,
        fees=fees,
        funding=funding,
        slippage=slippage,
        evidence_venue=venue,
        evidence_source_family=family,
        evidence_is_live=is_live,
        evidence_is_mock=is_mock,
    )


def evidence_flags(
    identity: object | None,
) -> tuple[str | None, str | None, bool | None, bool | None]:
    """Venue, source family, is_live, and is_mock from a stored evidence identity."""

    if not isinstance(identity, dict):
        return None, None, None, None
    venue = identity.get("venue")
    source = identity.get("source")
    provenance = identity.get("provenance")
    family = source.get("family") if isinstance(source, dict) else None
    if not isinstance(family, str) and isinstance(provenance, dict):
        recorded = provenance.get("source_family")
        family = recorded if isinstance(recorded, str) else None
    is_live = provenance.get("is_live") if isinstance(provenance, dict) else None
    is_mock = provenance.get("is_mock") if isinstance(provenance, dict) else None
    return (
        venue if isinstance(venue, str) else None,
        family if isinstance(family, str) else None,
        is_live if isinstance(is_live, bool) else None,
        is_mock if isinstance(is_mock, bool) else None,
    )


def decide_exit(
    trade: OpenInternalPaperTrade,
    bars: Sequence[ExitBar],
    *,
    source_name: str,
    evaluated_at: datetime,
) -> ExitDecision | None:
    """First closed bar that starts at or after the entry and touches a level.

    Live internal-paper entries are taken at a closed-bar boundary. The entry
    time is that bar's ``interval_end``, so the fill bar has an earlier
    ``interval_start`` and is not eligible. The next bar, whose
    ``interval_start`` equals the entry time, is the first bar that can exit
    the trade. A touch inside the fill bar is ignored. The same comparison
    also skips a partial bar when the fill is recorded inside it, because that
    bar's range is not known to have printed after the fill. When one eligible
    bar touches both levels, the stop is the exit and the evidence records
    ``stop_first``.
    """

    eligible = [
        bar
        for bar in bars
        if bar.interval_start >= trade.entry_time
        and bar.interval_end <= evaluated_at
        and bar.interval_start.tzinfo is not None
        and bar.interval_end.tzinfo is not None
    ]
    for bar in sorted(eligible, key=lambda item: item.interval_start):
        touch = _touch(trade, bar)
        if touch is None:
            continue
        stop_exit = touch in {"stop", "both"}
        reason: ExitReason = EXIT_REASON_STOP if stop_exit else EXIT_REASON_TARGET
        price = trade.stop if stop_exit else trade.target
        origin = classify_exit_origin(bar.interval_end, evaluated_at)
        evidence = _evidence(
            trade,
            bar,
            source_name=source_name,
            level="stop" if stop_exit else "target",
            assumption=SAME_BAR_ASSUMPTION if touch == "both" else SAME_BAR_NONE,
        )
        evidence["exit_origin"] = origin
        evidence["calibration_sample"] = (
            CALIBRATION_EXCLUDED if origin == EXIT_ORIGIN_BACKFILL else CALIBRATION_ELIGIBLE
        )
        return ExitDecision(
            trade=trade,
            reason=reason,
            exit_price=price,
            occurred_at=bar.interval_end,
            evidence=evidence,
            idempotency_key=exit_idempotency_key(trade.command_id),
        )
    return None


def close_request_for(decision: ExitDecision) -> ClosePaperPlanRequest:
    """The request ``POST /paper-plan/close`` would carry, plus the monitor identity."""

    trade = decision.trade
    return ClosePaperPlanRequest(
        organization_id=trade.organization_id,
        user_id=trade.user_id,
        account_id=trade.account_id,
        revision_id=trade.revision_id,
        command_id=trade.command_id,
        exit_price=decision.exit_price,
        fees=trade.fees,
        funding=trade.funding,
        slippage=trade.slippage,
        exit_reason=decision.reason,
        occurred_at=decision.occurred_at,
        idempotency_key=decision.idempotency_key,
        exit_channel=EXIT_CHANNEL,
        exit_actor=EXIT_ACTOR,
        exit_evidence=dict(decision.evidence),
    )


def run_paper_exit_monitor(
    *,
    enabled: bool,
    dry_run: bool,
    trades: Sequence[OpenInternalPaperTrade],
    candles: Callable[[OpenInternalPaperTrade], FetchedBars],
    closer: PaperExitCloser,
    evaluated_at: datetime,
    rejected: int = 0,
    shutdown: Callable[[], bool] | None = None,
) -> MonitorReport:
    """Examine open trades once. Flag off does not load candles or close."""

    if not enabled:
        return MonitorReport(status="disarmed")
    closed = 0
    replayed = 0
    would_close = 0
    skipped = rejected
    errors = 0
    error = ""
    ordered = sorted(
        trades,
        key=lambda trade: (trade.symbol, trade.timeframe.value, trade.entry_time),
    )
    for trade in ordered:
        if shutdown is not None and shutdown():
            break
        try:
            fetched = candles(trade)
        except Exception as exc:
            errors += 1
            error = error or "candle_failed"
            logger.warning(
                "paper_exit_monitor_candle_failed",
                journal_trade_id=str(trade.journal_trade_id),
                error=redact_text(str(exc))[:200],
            )
            continue
        if not fetched.covers_entry:
            skipped += 1
            logger.info(
                "paper_exit_monitor_history_incomplete",
                journal_trade_id=str(trade.journal_trade_id),
                symbol=trade.symbol,
                timeframe=trade.timeframe.value,
            )
            continue
        decision = decide_exit(
            trade,
            fetched.bars,
            source_name=fetched.source_name,
            evaluated_at=evaluated_at,
        )
        if decision is None:
            continue
        if dry_run:
            would_close += 1
            logger.info("paper_exit_monitor_would_close", **_decision_log(decision))
            continue
        try:
            result = closer.close(close_request_for(decision))
        except Exception as exc:
            reason = _close_skip_reason(exc)
            if reason is not None:
                skipped += 1
                logger.info(
                    "paper_exit_monitor_close_skipped",
                    journal_trade_id=str(trade.journal_trade_id),
                    reason=reason,
                )
                continue
            errors += 1
            error = error or "close_failed"
            logger.warning(
                "paper_exit_monitor_close_failed",
                journal_trade_id=str(trade.journal_trade_id),
                error=redact_text(str(exc))[:200],
            )
            continue
        if bool(getattr(result, "replayed", False)):
            replayed += 1
        else:
            closed += 1
        logger.info(
            "paper_exit_monitor_closed",
            replayed=bool(getattr(result, "replayed", False)),
            **_decision_log(decision),
        )
    if errors:
        status = "degraded"
    elif dry_run:
        status = "dry_run"
    elif closed or replayed:
        status = "closed"
    else:
        status = "idle"
    return MonitorReport(
        status=status,
        examined=len(ordered),
        closed=closed,
        replayed=replayed,
        would_close=would_close,
        skipped=skipped,
        errors=errors,
        error=error,
    )


def _exit_levels(
    payload: dict[str, object],
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal] | None:
    risk = payload.get("risk_and_exits")
    if not isinstance(risk, dict):
        return None
    runner = risk.get("runner")
    if not isinstance(runner, dict) or runner.get("enabled") is not False:
        return None
    targets = risk.get("targets")
    if not isinstance(targets, list) or len(targets) != 1 or not isinstance(targets[0], dict):
        return None
    target_row = targets[0]
    fraction = _decimal(target_row.get("quantity_fraction"))
    if fraction != Decimal("1"):
        return None
    stop_row = risk.get("stop")
    price_row = target_row.get("price")
    if not isinstance(stop_row, dict) or not isinstance(price_row, dict):
        return None
    stop = _positive(stop_row.get("value"))
    target = _positive(price_row.get("value"))
    fees = _non_negative(_amount_value(risk.get("fee_allowance")))
    funding = _non_negative(_amount_value(risk.get("funding_allowance")))
    slippage = _non_negative(_amount_value(risk.get("slippage_allowance")))
    if None in {stop, target, fees, funding, slippage}:
        return None
    assert stop is not None and target is not None
    assert fees is not None and funding is not None and slippage is not None
    return stop, target, fees, funding, slippage


def _amount_value(value: object) -> object:
    if isinstance(value, dict):
        return value.get("value")
    return None


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None
    return amount


def _positive(value: object) -> Decimal | None:
    amount = _decimal(value)
    if amount is None or amount <= 0:
        return None
    return amount


def _non_negative(value: object) -> Decimal | None:
    amount = _decimal(value)
    if amount is None or amount < 0:
        return None
    return amount


def _touch(trade: OpenInternalPaperTrade, bar: ExitBar) -> Literal["stop", "target", "both"] | None:
    if trade.direction is TradeDirection.LONG:
        stop_hit = bar.low <= trade.stop
        target_hit = bar.high >= trade.target
    else:
        stop_hit = bar.high >= trade.stop
        target_hit = bar.low <= trade.target
    if stop_hit and target_hit:
        return "both"
    if stop_hit:
        return "stop"
    if target_hit:
        return "target"
    return None


def _evidence(
    trade: OpenInternalPaperTrade,
    bar: ExitBar,
    *,
    source_name: str,
    level: str,
    assumption: str,
) -> dict[str, str]:
    return {
        "bar_interval_start": bar.interval_start.isoformat(),
        "bar_interval_end": bar.interval_end.isoformat(),
        "bar_open": format(bar.open, "f"),
        "bar_high": format(bar.high, "f"),
        "bar_low": format(bar.low, "f"),
        "bar_close": format(bar.close, "f"),
        "source_event_id": bar.source_event_id,
        "content_hash": bar.content_hash,
        "timeframe": trade.timeframe.value,
        "symbol": trade.symbol,
        "level": level,
        "same_bar_assumption": assumption,
        "market_source": source_name,
        "direction": trade.direction.value,
    }


def _decision_log(decision: ExitDecision) -> dict[str, object]:
    return {
        "journal_trade_id": str(decision.trade.journal_trade_id),
        "command_id": str(decision.trade.command_id),
        "exit_reason": decision.reason,
        "exit_price": format(decision.exit_price, "f"),
        "bar_interval_end": decision.occurred_at.isoformat(),
        "same_bar_assumption": decision.evidence["same_bar_assumption"],
        "exit_origin": decision.evidence.get("exit_origin", ""),
        "exit_channel": EXIT_CHANNEL,
        "exit_actor": EXIT_ACTOR,
    }


def _close_skip_reason(exc: Exception) -> str | None:
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        reason = details.get("reason")
        if isinstance(reason, str) and reason in _SKIP_CLOSE_REASONS:
            return reason
    name = type(exc).__name__
    if name == "JournalProjectionConflictError":
        return "close_conflict"
    return None
