"""Autonomous paper and BloFin-demo continuation policy.

Two modes exist, and both default off:

* internal paper (``autonomous_internal_paper_enabled``)
* BloFin demo (``autonomous_blofin_demo_enabled``)

Demo auto-approval is allowed only inside the configured account risk limits
(max position, total exposure, daily loss, and any trade-slot cap already
stored). The kill switch blocks every mode. Real-money settings never resolve
to an executable mode. Missing or contradictory mode evidence fails closed.

This module does not place orders. Callers still have to pass the existing
claim, reservation, and venue gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import KillSwitchState


class AutonomousMode(StrEnum):
    """Which autonomous continuation, if any, this process may run."""

    OFF = "off"
    INTERNAL_PAPER = "internal_paper"
    BLOFIN_DEMO = "blofin_demo"


AUTONOMOUS_APPROVAL_POLICY_VERSION = "at-adr-106/v1"
AUTONOMOUS_ACTOR_TYPE = "SYSTEM"


@dataclass(frozen=True)
class AutonomousModeResolution:
    """Resolved mode. ``refusal`` is set whenever the mode cannot be verified."""

    mode: AutonomousMode
    refusal: str | None


@dataclass(frozen=True)
class RiskLimitSnapshot:
    """Account limits and the reservation a demo auto-approval would charge."""

    max_notional: Decimal
    max_daily_loss: Decimal
    max_symbol_notional: Decimal
    max_trade_slots: int
    pending_notional: Decimal
    proposed_loss: Decimal
    instrument: str
    reserved_notional: Decimal = Decimal("0")
    reserved_daily_loss: Decimal = Decimal("0")
    reserved_trade_slots: int = 0
    actual_notional: Decimal = Decimal("0")
    actual_daily_loss: Decimal = Decimal("0")
    actual_trade_count: int = 0
    symbol_reserved: dict[str, str] | None = None
    symbol_actual: dict[str, str] | None = None
    daily_locked: bool = False
    trade_slots: int = 1


def resolve_autonomous_mode(settings: object) -> AutonomousModeResolution:
    """Return the only autonomous mode these settings prove, or a refusal.

    Real-money configuration is refused even when a flag is set. Both flags,
    a flag that disagrees with the exchange mode, or unreadable settings fail
    closed. Neither flag means the process is disarmed, which is not an error.
    """

    if settings is None:
        return AutonomousModeResolution(AutonomousMode.OFF, "autonomous_mode_unverified")
    execution_mode = _enum_value(getattr(settings, "execution_mode", _MISSING))
    exchange_mode = _enum_value(getattr(settings, "exchange_mode", _MISSING))
    enable_real = getattr(settings, "enable_real_trading", _MISSING)
    real_trading = getattr(settings, "real_trading_enabled", _MISSING)
    paper_flag = getattr(settings, "autonomous_internal_paper_enabled", _MISSING)
    demo_flag = getattr(settings, "autonomous_blofin_demo_enabled", _MISSING)
    if (
        execution_mode is None
        or exchange_mode is None
        or not isinstance(enable_real, bool)
        or not isinstance(real_trading, bool)
        or not isinstance(paper_flag, bool)
        or not isinstance(demo_flag, bool)
    ):
        return AutonomousModeResolution(AutonomousMode.OFF, "autonomous_mode_unverified")
    if enable_real or real_trading or execution_mode == "trade" or exchange_mode == "trade_live":
        return AutonomousModeResolution(AutonomousMode.OFF, "real_trading_blocked")
    environment = _enum_value(getattr(settings, "environment", None))
    if environment == "production" and (paper_flag or demo_flag):
        return AutonomousModeResolution(AutonomousMode.OFF, "autonomous_production_refused")
    if paper_flag and demo_flag:
        return AutonomousModeResolution(AutonomousMode.OFF, "autonomous_mode_ambiguous")
    if paper_flag:
        if execution_mode != "paper" or exchange_mode != "paper_internal":
            return AutonomousModeResolution(AutonomousMode.OFF, "autonomous_paper_unverified")
        return AutonomousModeResolution(AutonomousMode.INTERNAL_PAPER, None)
    if demo_flag:
        if execution_mode != "paper" or exchange_mode != "paper_exchange_demo":
            return AutonomousModeResolution(AutonomousMode.OFF, "autonomous_demo_unverified")
        return AutonomousModeResolution(AutonomousMode.BLOFIN_DEMO, None)
    return AutonomousModeResolution(AutonomousMode.OFF, None)


def autonomous_decision_metadata(
    *,
    mode: AutonomousMode,
    settings: object,
    limits: RiskLimitSnapshot | None,
    limits_note: str,
) -> dict[str, object]:
    """Identity recorded with an autonomous auto-approval.

    ``limits`` is the snapshot the approval used. Paper does not evaluate the
    account ledger at approval time; callers pass None and say so in
    ``limits_note``. Demo passes the snapshot that passed the account-limit check.
    """

    return {
        "actor": "system",
        "channel": "AUTONOMOUS",
        "mode": mode.value,
        "policy_version": AUTONOMOUS_APPROVAL_POLICY_VERSION,
        "flags": {
            "autonomous_internal_paper_enabled": getattr(
                settings, "autonomous_internal_paper_enabled", None
            ),
            "autonomous_blofin_demo_enabled": getattr(
                settings, "autonomous_blofin_demo_enabled", None
            ),
        },
        "environment": _enum_value(getattr(settings, "environment", None)),
        "limits_note": limits_note,
        "limits": _limit_payload(limits),
    }


def continuation_refusal(settings: object, *, expected: AutonomousMode) -> str | None:
    """Refuse continuation unless ``settings`` resolve exactly to ``expected``."""

    resolution = resolve_autonomous_mode(settings)
    if resolution.refusal is not None:
        return resolution.refusal
    if resolution.mode is AutonomousMode.OFF:
        if expected is AutonomousMode.INTERNAL_PAPER:
            return "autonomous_paper_disarmed"
        if expected is AutonomousMode.BLOFIN_DEMO:
            return "autonomous_demo_disarmed"
        return "autonomous_mode_disarmed"
    if resolution.mode is not expected:
        return "autonomous_mode_mismatch"
    return None


def kill_switch_refusal(
    settings: object,
    session: Session | None,
    organization_id: UUID | None,
    *,
    observed: bool | None,
) -> str | None:
    """Block when either switch is on, and fail closed when it cannot be read.

    ``observed`` is the watcher/caller report. A true report blocks even if the
    database row is inactive. A database read error, a missing session, or a
    non-boolean global flag is unverified and therefore blocked.
    """

    if observed is True:
        return "kill_switch_active"
    if observed is not None and not isinstance(observed, bool):
        return "kill_switch_unverified"
    if settings is None:
        return "kill_switch_unverified"
    global_active = getattr(settings, "global_kill_switch_active", _MISSING)
    if not isinstance(global_active, bool):
        return "kill_switch_unverified"
    if global_active:
        return "kill_switch_active"
    if session is None or organization_id is None:
        return "kill_switch_unverified"
    try:
        row = session.scalar(
            select(KillSwitchState).where(KillSwitchState.organization_id == organization_id)
        )
    except Exception:
        return "kill_switch_unverified"
    if row is not None and bool(row.active):
        return "kill_switch_active"
    return None


def configured_risk_refusal(snapshot: RiskLimitSnapshot | None) -> str | None:
    """Refuse demo auto-approval outside the configured account risk limits.

    The comparisons match the claim predicate: symbol notional is the position
    cap, total notional is exposure, and daily loss includes the amount this
    order would reserve. Unreadable numbers fail closed.
    """

    if snapshot is None:
        return "risk_limits_unverified"
    numbers = (
        snapshot.max_notional,
        snapshot.max_daily_loss,
        snapshot.max_symbol_notional,
        snapshot.pending_notional,
        snapshot.proposed_loss,
        snapshot.reserved_notional,
        snapshot.reserved_daily_loss,
        snapshot.actual_notional,
        snapshot.actual_daily_loss,
    )
    counts = (
        snapshot.max_trade_slots,
        snapshot.reserved_trade_slots,
        snapshot.actual_trade_count,
        snapshot.trade_slots,
    )
    if any(not isinstance(value, Decimal) or not value.is_finite() for value in numbers):
        return "risk_limits_unverified"
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts):
        return "risk_limits_unverified"
    if (
        snapshot.max_notional < 0
        or snapshot.max_daily_loss < 0
        or snapshot.max_symbol_notional < 0
        or snapshot.pending_notional <= 0
        or snapshot.proposed_loss < 0
        or snapshot.reserved_notional < 0
        or snapshot.reserved_daily_loss < 0
        or snapshot.actual_notional < 0
        or snapshot.actual_daily_loss < 0
        or snapshot.trade_slots < 1
        or not snapshot.instrument
    ):
        return "risk_limits_unverified"
    if snapshot.daily_locked:
        return "daily_loss_limit"
    try:
        symbol_reserved = _symbol_amount(snapshot.symbol_reserved, snapshot.instrument)
        symbol_actual = _symbol_amount(snapshot.symbol_actual, snapshot.instrument)
    except (InvalidOperation, ValueError, TypeError):
        return "risk_limits_unverified"
    if snapshot.pending_notional > snapshot.max_symbol_notional:
        return "max_position_size"
    remaining_symbol = snapshot.max_symbol_notional - symbol_reserved - symbol_actual
    if snapshot.pending_notional > remaining_symbol:
        return "max_position_size"
    if snapshot.pending_notional > snapshot.max_notional:
        return "max_exposure"
    remaining_notional = (
        snapshot.max_notional - snapshot.reserved_notional - snapshot.actual_notional
    )
    if snapshot.pending_notional > remaining_notional:
        return "max_exposure"
    remaining_loss = (
        snapshot.max_daily_loss - snapshot.reserved_daily_loss - snapshot.actual_daily_loss
    )
    if snapshot.proposed_loss > remaining_loss:
        return "daily_loss_limit"
    remaining_slots = (
        snapshot.max_trade_slots - snapshot.reserved_trade_slots - snapshot.actual_trade_count
    )
    if remaining_slots < snapshot.trade_slots:
        return "daily_trade_limit"
    return None


_MISSING = object()


def _limit_payload(snapshot: RiskLimitSnapshot | None) -> dict[str, object] | None:
    if snapshot is None:
        return None
    return {
        "max_notional": str(snapshot.max_notional),
        "max_daily_loss": str(snapshot.max_daily_loss),
        "max_symbol_notional": str(snapshot.max_symbol_notional),
        "max_trade_slots": snapshot.max_trade_slots,
        "pending_notional": str(snapshot.pending_notional),
        "proposed_loss": str(snapshot.proposed_loss),
        "instrument": snapshot.instrument,
        "reserved_notional": str(snapshot.reserved_notional),
        "reserved_daily_loss": str(snapshot.reserved_daily_loss),
        "reserved_trade_slots": snapshot.reserved_trade_slots,
        "actual_notional": str(snapshot.actual_notional),
        "actual_daily_loss": str(snapshot.actual_daily_loss),
        "actual_trade_count": snapshot.actual_trade_count,
        "symbol_reserved": dict(snapshot.symbol_reserved or {}),
        "symbol_actual": dict(snapshot.symbol_actual or {}),
        "daily_locked": snapshot.daily_locked,
        "trade_slots": snapshot.trade_slots,
    }


def _enum_value(value: object) -> str | None:
    if value is _MISSING or value is None:
        return None
    raw = getattr(value, "value", value)
    if not isinstance(raw, str):
        return None
    text = raw.strip().lower()
    return text or None


def _symbol_amount(book: dict[str, str] | None, instrument: str) -> Decimal:
    if book is None:
        return Decimal("0")
    raw = book.get(instrument, "0")
    amount = Decimal(str(raw))
    if not amount.is_finite() or amount < 0:
        raise ValueError("symbol exposure is not a usable amount")
    return amount
