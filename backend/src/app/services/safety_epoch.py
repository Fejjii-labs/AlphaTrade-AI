"""Account-scoped safety epoch locked before risk accounting.

Lock order (architecture §24):
1. account_safety_epochs
2. account_risk_accounting_states
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import (
    AccountRiskAccountingState,
    AccountSafetyEpoch,
    ExecutionAccount,
    KillSwitchState,
)
from app.repositories.execution_protocol import (
    AccountRiskAccountingRepository,
    AccountSafetyEpochRepository,
)
from app.services.execution_integrity import (
    ensure_db_transaction,
    is_risk_accounting_unique_violation,
    is_safety_epoch_unique_violation,
)
from app.services.risk.settings_service import RiskSettingsService, normalize_timezone

INTERNAL_PAPER_VENUE = "PAPER_INTERNAL"


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

        ``roll_daily_trade_slots`` is for the internal paper venue only. Demo
        and every other caller leave the lifetime counters untouched. The roll
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
        """Drop completed trades from earlier days. Keep open reservations.

        ``actual_trade_count`` is the completed daily count. ``reserved_trade_slots``
        is live unreleased allocation and is not cleared. ``max_trade_slots`` is
        copied from the user's current ``max_trades_per_day`` only when the
        business day changes. A row already stamped with that day is a no-op,
        including a second claim that waited on this lock.
        """

        settings = self._risk_settings.get(organization_id=organization_id, user_id=user_id)
        today = paper_trading_day(now, settings.timezone)
        if _as_date(accounting.trade_slot_business_date) == today:
            return
        accounting.actual_trade_count = 0
        accounting.max_trade_slots = int(settings.max_trades_per_day)
        accounting.trade_slot_business_date = today
        accounting.version = int(accounting.version) + 1

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


def paper_daily_trade_roll(execution_venue: str | None) -> bool:
    """Trade-slot days roll on the internal paper venue only."""

    return execution_venue == INTERNAL_PAPER_VENUE


def paper_trading_day(now: datetime, timezone_name: str) -> date:
    """Calendar date of ``now`` in the user's risk-settings timezone."""

    tz_name, _fallback = normalize_timezone(timezone_name)
    aware = _aware(now)
    try:
        return aware.astimezone(ZoneInfo(tz_name)).date()
    except Exception:
        return aware.astimezone(UTC).date()


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
