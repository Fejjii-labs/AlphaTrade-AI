"""Account-scoped safety epoch locked before risk accounting.

Lock order (architecture §24):
1. account_safety_epochs
2. account_risk_accounting_states
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import (
    AccountRiskAccountingState,
    AccountSafetyEpoch,
    ExecutionAccount,
    ExecutionCommand,
    ExecutionFillFact,
    ExecutionProjection,
    KillSwitchState,
    RiskReservation,
    TradePlanRevision,
    VenueSubmitEffect,
)
from app.repositories.execution_protocol import (
    AccountRiskAccountingRepository,
    AccountSafetyEpochRepository,
)
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import ActorType, AuditEventType, AuditResult, AuditSeverity
from app.schemas.execution_protocol import (
    ExecutionCommandOutcome,
    ExecutionReceiptState,
    RiskReservationReleaseReason,
    RiskReservationReleaseState,
    VenueSendDisposition,
    VenueSubmitEffectState,
)
from app.services.audit_service import AuditService
from app.services.execution_fills import adjust_symbol_map
from app.services.execution_integrity import (
    ensure_db_transaction,
    is_risk_accounting_unique_violation,
    is_safety_epoch_unique_violation,
)
from app.services.risk.settings_service import RiskSettingsService, normalize_timezone

logger = structlog.get_logger(__name__)

INTERNAL_PAPER_VENUE = "PAPER_INTERNAL"
# Longer than the 15-minute authorization TTL and a same-process paper fill,
# shorter than a trading day. A reservation opened late in the local day still
# counts after midnight. A crash that never fills is released on the next roll.
PAPER_UNFILLED_RESERVATION_MAX_AGE = timedelta(hours=6)
_TERMINAL_RECEIPT_STATES = frozenset(
    {
        ExecutionReceiptState.BLOCKED,
        ExecutionReceiptState.BLOCKED_BEFORE_DISPATCH,
        ExecutionReceiptState.REJECTED,
        ExecutionReceiptState.EXPIRED,
        ExecutionReceiptState.CANCELLED,
        ExecutionReceiptState.PARTIALLY_FILLED_CANCELLED,
        ExecutionReceiptState.ABSENCE_PROVEN,
    }
)
_warned_timezones: set[str] = set()


class SafetyEpochService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        risk_settings: RiskSettingsService,
    ) -> None:
        self._session = session
        self._settings = settings
        self._epochs = AccountSafetyEpochRepository(session)
        self._accounting = AccountRiskAccountingRepository(session)
        self._risk_settings = risk_settings

    def lock_epoch(
        self,
        *,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
    ) -> AccountSafetyEpoch:
        ensure_db_transaction(self._session)
        existing = self._epochs.get_for_update(
            organization_id=organization_id, account_id=account_id
        )
        if existing is not None:
            return existing
        row = AccountSafetyEpoch(
            organization_id=organization_id,
            account_id=account_id,
            epoch=1,
            blocking=self.organization_kill_active(organization_id),
            last_reason_code="initialized",
        )
        try:
            with self._session.begin_nested():
                self._epochs.add(row)
        except IntegrityError as exc:
            if not is_safety_epoch_unique_violation(exc):
                raise
        locked = self._epochs.get_for_update(organization_id=organization_id, account_id=account_id)
        if locked is None:
            raise RuntimeError("Safety epoch row missing after insert-or-lock.")
        return locked

    def lock_risk_accounting(
        self,
        *,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
        user_id: uuid.UUID,
        exposure_unit: str,
        now: datetime | None = None,
        roll_daily_trade_slots: bool = False,
    ) -> AccountRiskAccountingState:
        """Lock the account ledger. Paper callers may roll the trade-slot day.

        ``roll_daily_trade_slots`` is for the internal paper and BloFin demo
        venues (see ``paper_daily_trade_roll``). Other callers leave the
        counters untouched. The roll
        runs while this row lock is held, so two claims at a day boundary
        apply it once.
        """

        ensure_db_transaction(self._session)
        observed_at = _aware(now)
        existing = self._accounting.get_for_update(
            organization_id=organization_id, account_id=account_id
        )
        if existing is not None:
            if roll_daily_trade_slots:
                self._roll_daily_trade_slots(
                    existing,
                    organization_id=organization_id,
                    user_id=user_id,
                    now=observed_at,
                )
            return existing
        limits = self._default_limits(organization_id=organization_id, user_id=user_id)
        business_day = (
            self._business_day(organization_id=organization_id, user_id=user_id, now=observed_at)
            if roll_daily_trade_slots
            else None
        )
        row = AccountRiskAccountingState(
            organization_id=organization_id,
            account_id=account_id,
            reserved_notional=Decimal("0"),
            reserved_daily_loss=Decimal("0"),
            reserved_trade_slots=0,
            actual_notional=Decimal("0"),
            actual_daily_loss=Decimal("0"),
            actual_trade_count=0,
            symbol_reserved={},
            symbol_actual={},
            max_notional=limits["max_notional"],
            max_daily_loss=limits["max_daily_loss"],
            max_trade_slots=int(limits["max_trade_slots"]),
            max_symbol_notional=limits["max_symbol_notional"],
            daily_locked=False,
            exposure_unit=exposure_unit,
            trade_slot_business_date=business_day,
            version=1,
        )
        try:
            with self._session.begin_nested():
                self._accounting.add(row)
        except IntegrityError as exc:
            if not is_risk_accounting_unique_violation(exc):
                raise
        locked = self._accounting.get_for_update(
            organization_id=organization_id, account_id=account_id
        )
        if locked is None:
            raise RuntimeError("Risk accounting row missing after insert-or-lock.")
        if roll_daily_trade_slots:
            self._roll_daily_trade_slots(
                locked,
                organization_id=organization_id,
                user_id=user_id,
                now=observed_at,
            )
        return locked

    def advance_for_kill_activation(
        self,
        *,
        organization_id: uuid.UUID,
        at: datetime,
    ) -> None:
        ensure_db_transaction(self._session)
        self._ensure_epochs_for_accounts(organization_id)
        rows = self._epochs.list_for_organization_for_update(organization_id)
        for row in rows:
            row.epoch = int(row.epoch) + 1
            row.blocking = True
            row.last_reason_code = "kill_switch_activated"
            row.updated_at = at
        self._session.flush()

    def clear_blocking_for_kill_deactivation(
        self,
        *,
        organization_id: uuid.UUID,
        at: datetime,
    ) -> None:
        rows = self._epochs.list_for_organization_for_update(organization_id)
        for row in rows:
            row.blocking = False
            row.last_reason_code = "kill_switch_deactivated"
            row.updated_at = at
        self._session.flush()

    def organization_kill_active(self, organization_id: uuid.UUID) -> bool:
        if self._settings.global_kill_switch_active:
            return True
        row = self._session.scalar(
            select(KillSwitchState).where(KillSwitchState.organization_id == organization_id)
        )
        return bool(row is not None and row.active)

    def _ensure_epochs_for_accounts(self, organization_id: uuid.UUID) -> None:
        account_ids = list(
            self._session.scalars(
                select(ExecutionAccount.id)
                .where(ExecutionAccount.organization_id == organization_id)
                .order_by(ExecutionAccount.id.asc())
            ).all()
        )
        for account_id in account_ids:
            self.lock_epoch(organization_id=organization_id, account_id=account_id)

    def _roll_daily_trade_slots(
        self,
        accounting: AccountRiskAccountingState,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        now: datetime,
    ) -> None:
        """Drop completed trades from earlier days. Keep live paper reservations.

        ``actual_trade_count`` is the completed daily count. A reservation that
        is still in flight, or that already has a fill, keeps its slot.
        A paper reservation whose command is terminal, or that is older than
        ``PAPER_UNFILLED_RESERVATION_MAX_AGE`` and has no fill, is released
        here so a crash cannot hold the slot forever. ``max_trade_slots`` is
        copied from the user's current ``max_trades_per_day`` only when the
        business day moves forward. A row already stamped with that day, or a
        later one (another user in a timezone ahead), is a no-op, including a
        second claim that waited on this lock.
        """

        settings = self._risk_settings.get(organization_id=organization_id, user_id=user_id)
        today = paper_trading_day(now, settings.timezone)
        stamped = _as_date(accounting.trade_slot_business_date)
        # Forward-only. The ledger row is shared by the whole org account, but
        # the day comes from the claiming user's timezone. A user whose local
        # day is behind the stamped day must never reset the count backwards,
        # or alternating users could bypass the daily cap. ``max_trade_slots``
        # is likewise refreshed only on a forward roll.
        if stamped is None:
            # First roll on a row that was never stamped (created before #318,
            # or by a non-rolling caller). Its counters may hold today's
            # trades, so stamp today and keep them: zeroing here would let an
            # unstamped row bypass the cap once. The next forward day rolls.
            accounting.max_trade_slots = int(settings.max_trades_per_day)
            accounting.trade_slot_business_date = today
            accounting.version = int(accounting.version) + 1
            return
        if today <= stamped:
            return
        self._release_orphaned_paper_reservations(
            accounting,
            organization_id=organization_id,
            user_id=user_id,
            now=now,
        )
        accounting.actual_trade_count = 0
        accounting.max_trade_slots = int(settings.max_trades_per_day)
        accounting.trade_slot_business_date = today
        accounting.version = int(accounting.version) + 1

    def _release_orphaned_paper_reservations(
        self,
        accounting: AccountRiskAccountingState,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        now: datetime,
    ) -> None:
        """Release crashed paper reservations while the ledger row is locked.

        Demo reservations on the shared account are left alone. Rows locked by
        another transaction are skipped on PostgreSQL so a fill that already
        holds the reservation cannot deadlock against this roll.
        """

        for reservation in self._paper_reservations_for_roll(
            organization_id=organization_id, account_id=accounting.account_id
        ):
            cause = self._orphan_cause(reservation, now)
            if cause is None:
                continue
            slots = _release_reservation_remainder(accounting, reservation, now, cause)
            self._audit_orphan_release(
                reservation,
                organization_id=organization_id,
                user_id=user_id,
                now=now,
                cause=cause,
                slots_released=slots,
            )
            logger.warning(
                "paper_orphaned_reservation_released",
                reservation_id=str(reservation.id),
                command_id=str(reservation.command_id),
                organization_id=str(organization_id),
                cause=cause,
                slots_released=slots,
            )

    def _paper_reservations_for_roll(
        self, *, organization_id: uuid.UUID, account_id: uuid.UUID
    ) -> list[RiskReservation]:
        stmt = (
            select(RiskReservation)
            .where(
                RiskReservation.organization_id == organization_id,
                RiskReservation.account_id == account_id,
                RiskReservation.release_state != RiskReservationReleaseState.RELEASED,
            )
            .order_by(RiskReservation.id.asc())
        )
        bind = self._session.get_bind()
        if bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        return list(self._session.scalars(stmt).all())

    def _orphan_cause(self, reservation: RiskReservation, now: datetime) -> str | None:
        plan = self._session.get(TradePlanRevision, reservation.plan_revision_id)
        if plan is None or plan.execution_venue != INTERNAL_PAPER_VENUE:
            return None
        terminal = self._terminal_cause(reservation)
        if terminal is not None:
            return terminal
        if self._has_fill(reservation):
            return None
        if now - _aware(reservation.created_at) > PAPER_UNFILLED_RESERVATION_MAX_AGE:
            return "aged_unfilled"
        return None

    def _terminal_cause(self, reservation: RiskReservation) -> str | None:
        command = self._session.get(ExecutionCommand, reservation.command_id)
        if command is not None and command.outcome is ExecutionCommandOutcome.BLOCKED:
            return "blocked_command"
        projection = self._session.scalar(
            select(ExecutionProjection).where(
                ExecutionProjection.receipt_id == reservation.receipt_id
            )
        )
        if projection is not None and projection.state in _TERMINAL_RECEIPT_STATES:
            return f"terminal_receipt:{projection.state.value}"
        effect = self._session.scalar(
            select(VenueSubmitEffect).where(VenueSubmitEffect.command_id == reservation.command_id)
        )
        if effect is None:
            return None
        if effect.state is VenueSubmitEffectState.PROVEN_UNSENT:
            return "proven_unsent"
        if effect.reconciliation_disposition == VenueSendDisposition.REJECTED.value:
            return "venue_rejected"
        return None

    def _has_fill(self, reservation: RiskReservation) -> bool:
        if int(reservation.converted_trade_slots) > 0:
            return True
        found = self._session.scalar(
            select(ExecutionFillFact.id)
            .where(ExecutionFillFact.command_id == reservation.command_id)
            .limit(1)
        )
        return found is not None

    def _audit_orphan_release(
        self,
        reservation: RiskReservation,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        now: datetime,
        cause: str,
        slots_released: int,
    ) -> None:
        AuditService(self._session).record(
            AuditRecordCreate(
                request_id=f"paper-slot-roll:{reservation.id}",
                trace_id=f"paper-slot-roll:{reservation.command_id}",
                event_type=AuditEventType.PAPER_RESERVATION_RELEASED,
                resource_type="risk_reservation",
                resource_id=str(reservation.id),
                actor_type=ActorType.SYSTEM,
                action="paper_reservation_released",
                result=AuditResult.WARNING,
                severity=AuditSeverity.MEDIUM,
                user_id=user_id,
                organization_id=organization_id,
                metadata={
                    "cause": cause,
                    "command_id": str(reservation.command_id),
                    "release_reason": (
                        reservation.release_reason.value if reservation.release_reason else None
                    ),
                    "slots_released": slots_released,
                    "execution_venue": INTERNAL_PAPER_VENUE,
                },
                timestamp=now,
            )
        )

    def _business_day(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        now: datetime,
    ) -> date:
        settings = self._risk_settings.get(organization_id=organization_id, user_id=user_id)
        return paper_trading_day(now, settings.timezone)

    def _default_limits(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> dict[str, Decimal | int]:
        settings = self._risk_settings.get(organization_id=organization_id, user_id=user_id)
        max_notional = settings.default_account_balance
        max_daily_loss = settings.daily_loss_limit or settings.default_account_balance
        return {
            "max_notional": max_notional,
            "max_daily_loss": max_daily_loss,
            "max_trade_slots": int(settings.max_trades_per_day),
            "max_symbol_notional": max_notional,
        }


BLOFIN_DEMO_VENUE = "BLOFIN_DEMO"
DAILY_TRADE_ROLL_VENUES = frozenset({INTERNAL_PAPER_VENUE, BLOFIN_DEMO_VENUE})


def paper_daily_trade_roll(execution_venue: str | None) -> bool:
    """Trade-slot days roll on the internal paper and BloFin demo venues (#318).

    Demo shares the same roll: row lock, idempotent within a business day,
    open reservations keep their slots. Orphaned demo reservations are still
    released only by manual demo recovery, which checks venue truth first.
    """

    return execution_venue in DAILY_TRADE_ROLL_VENUES


def paper_trading_day(now: datetime, timezone_name: str) -> date:
    """Calendar date of ``now`` in the user's risk-settings timezone."""

    tz_name, fell_back = normalize_timezone(timezone_name)
    aware = _aware(now)
    if fell_back:
        _warn_timezone_fallback(timezone_name)
        return aware.astimezone(UTC).date()
    try:
        return aware.astimezone(ZoneInfo(tz_name)).date()
    except Exception:
        _warn_timezone_fallback(timezone_name)
        return aware.astimezone(UTC).date()


def _warn_timezone_fallback(timezone_name: str | None) -> None:
    key = (timezone_name or "")[:64]
    if key in _warned_timezones:
        return
    _warned_timezones.add(key)
    logger.warning(
        "paper_trading_timezone_fallback",
        timezone_name=key,
        fallback="UTC",
    )


def _release_reason_for(cause: str) -> RiskReservationReleaseReason:
    if "CANCELLED" in cause:
        return RiskReservationReleaseReason.UNUSED_REMAINDER_AFTER_CANCELLATION
    if cause in {"aged_unfilled", "terminal_receipt:EXPIRED"}:
        return RiskReservationReleaseReason.UNUSED_REMAINDER_AFTER_EXPIRY
    if "ABSENCE" in cause:
        return RiskReservationReleaseReason.UNUSED_REMAINDER_AFTER_ABSENCE
    if cause == "proven_unsent":
        return RiskReservationReleaseReason.UNUSED_REMAINDER_AFTER_PROVEN_UNSENT
    return RiskReservationReleaseReason.UNUSED_REMAINDER_AFTER_REJECTION


def _release_reservation_remainder(
    accounting: AccountRiskAccountingState,
    reservation: RiskReservation,
    now: datetime,
    cause: str,
) -> int:
    """Free unconverted slots and unused notional. Filled exposure stays."""

    unused = reservation.remaining_reserved_notional
    unconverted = max(
        0, int(reservation.daily_trade_allocation) - int(reservation.converted_trade_slots)
    )
    accounting.reserved_notional -= unused
    if accounting.reserved_notional < 0:
        accounting.reserved_notional = Decimal("0")
    if not reservation.converted_trade_slots:
        accounting.reserved_daily_loss -= reservation.daily_loss_allocation
        if accounting.reserved_daily_loss < 0:
            accounting.reserved_daily_loss = Decimal("0")
    accounting.reserved_trade_slots -= unconverted
    if accounting.reserved_trade_slots < 0:
        accounting.reserved_trade_slots = 0
    accounting.symbol_reserved = adjust_symbol_map(
        accounting.symbol_reserved, reservation.instrument, -unused
    )
    reservation.remaining_reserved_notional = Decimal("0")
    reservation.release_state = RiskReservationReleaseState.RELEASED
    reservation.release_reason = _release_reason_for(cause)
    reservation.updated_at = now
    if unused > 0 or unconverted > 0:
        accounting.version = int(accounting.version) + 1
    return unconverted


def _aware(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _as_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return None
