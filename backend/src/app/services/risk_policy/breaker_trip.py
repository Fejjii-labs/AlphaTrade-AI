"""Call ``record_breaker`` from server figures.

This module writes the informational drawdown, daily-loss and loss-streak
figures for an active profile. None of them refuses or pauses new trades;
the peak latch and both pauses were removed (AT-ADR-152). Each persist also
runs the account daily equity stop, the only loss rule that halts trading.
It does not place an order.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.risk_policy.breakers import (
    BreakerConfig,
    BreakerSnapshot,
    BreakerVerdict,
    evaluate_breakers,
)
from app.services.risk_policy.profile import RiskPolicyProfile


class BreakerRecorder(Protocol):
    def record_breaker(
        self,
        *,
        organization_id: UUID,
        account_id: UUID,
        equity_peak: Decimal,
        consecutive_losses: int,
        realized_pnl: Decimal,
        unrealized_pnl: Decimal,
        daily_paused_until: datetime | None,
        consecutive_paused_until: datetime | None,
        drawdown_latched: bool,
        now: datetime,
    ) -> object: ...


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def streak_after_expiry(
    consecutive_losses: int,
    paused_until: datetime | None,
    now: datetime,
) -> int:
    """An expired pause ends the streak. A missing pause does not.

    The count stays at the limit while the pause is in force, so the next
    scan would re-trip forever unless the expiry clears it.
    """

    if paused_until is None:
        return consecutive_losses
    if _as_utc(paused_until) <= _as_utc(now):
        return 0
    return consecutive_losses


def profile_close_consecutive(
    current: int,
    net_pnl: Decimal,
    *,
    pause_in_force: bool = False,
) -> int:
    """A loss increments the streak. A win resets it only when no pause is in force.

    An unexpired consecutive pause is not a win. Ending that pause stays on
    ``reset_consecutive_losses``. This does not reset a drawdown latch and
    does not place an order.
    """

    if not isinstance(net_pnl, Decimal) or not net_pnl.is_finite() or net_pnl < 0:
        return current + 1
    if pause_in_force:
        return current
    return 0


def trip_breakers(
    recorder: BreakerRecorder,
    *,
    organization_id: UUID,
    account_id: UUID,
    config: BreakerConfig,
    snapshot: BreakerSnapshot,
    now: datetime,
) -> BreakerVerdict:
    """Evaluate one snapshot and persist its figures. Writes no pause and no latch.

    The peak drawdown latch, the daily-loss pause and the consecutive-loss
    pause were removed (AT-ADR-152). The stored pause columns and the latch
    are written cleared, so a legacy pause or latch ends at the next persist.
    """

    verdict = evaluate_breakers(config, snapshot)
    recorder.record_breaker(
        organization_id=organization_id,
        account_id=account_id,
        equity_peak=verdict.equity_peak,
        consecutive_losses=snapshot.consecutive_losses,
        realized_pnl=snapshot.realized_pnl_today,
        unrealized_pnl=snapshot.unrealized_pnl_today,
        daily_paused_until=None,
        consecutive_paused_until=None,
        drawdown_latched=False,
        now=now,
    )
    return verdict


def persist_profile_breakers(
    session: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    account_id: UUID,
    profile: RiskPolicyProfile,
    now: datetime,
    consecutive_losses: int | None = None,
    settings: Settings | None = None,
) -> bool:
    """Sync daily figures, then run the equity stop. Returns whether new trades are refused.

    Only the account daily equity stop refuses here, by arming the kill
    switch at most once per UTC day; only ``KillSwitchService.deactivate``
    turns it off. Unusable risk-day equity also refuses this call (fail
    closed) without latching anything.
    """

    from app.services.audit_service import AuditService
    from app.services.risk.daily_risk_accounting import DailyRiskAccounting
    from app.services.risk.settings_service import RiskSettingsService
    from app.services.risk_policy.persistence import RiskPolicyService

    daily = DailyRiskAccounting(
        session,
        RiskSettingsService(session, AuditService(session)),
        clock=lambda: now,
    ).sync_from_portfolio(organization_id=organization_id, user_id=user_id)
    equity = daily.account_equity
    if not isinstance(equity, Decimal) or not equity.is_finite() or equity <= 0:
        # A close that ruins the book still records the streak and fails closed.
        return _record_ruined_equity(
            session,
            organization_id=organization_id,
            user_id=user_id,
            account_id=account_id,
            now=now,
            consecutive_losses=consecutive_losses,
            realized_pnl=daily.realized_pnl,
            unrealized_pnl=daily.unrealized_pnl,
            settings=settings,
        )
    service = RiskPolicyService(session, AuditService(session))
    row = service.load_breaker(organization_id=organization_id, account_id=account_id)
    peak = equity
    if row is not None and isinstance(row.equity_peak, Decimal) and row.equity_peak > 0:
        peak = row.equity_peak
    stored = 0 if row is None else int(row.consecutive_losses)
    consecutive = stored if consecutive_losses is None else consecutive_losses
    realized = daily.realized_pnl
    unrealized = daily.unrealized_pnl
    reference = equity - realized - unrealized
    if reference <= 0:
        reference = equity
    trip_breakers(
        service,
        organization_id=organization_id,
        account_id=account_id,
        config=BreakerConfig(
            daily_loss_limit_percent=profile.daily_loss_limit_percent,
            drawdown_breaker_percent=profile.drawdown_breaker_percent,
            consecutive_loss_limit=profile.consecutive_loss_limit,
        ),
        snapshot=BreakerSnapshot(
            equity=equity,
            equity_peak=peak,
            reference_equity=reference,
            realized_pnl_today=realized,
            unrealized_pnl_today=unrealized,
            consecutive_losses=consecutive,
            drawdown_latched=False,
            now=now,
        ),
        now=now,
    )
    return _enforce_equity_stop(
        session,
        organization_id=organization_id,
        user_id=user_id,
        account_id=account_id,
        now=now,
        settings=settings,
    )


def _finite_money(value: object) -> Decimal:
    if isinstance(value, Decimal) and value.is_finite():
        return value
    return Decimal("0")


def _record_ruined_equity(
    session: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    account_id: UUID,
    now: datetime,
    consecutive_losses: int | None,
    realized_pnl: object,
    unrealized_pnl: object,
    settings: Settings | None,
) -> bool:
    """Fail closed when risk-day equity is missing, non-finite, or not positive.

    The loss streak and figures are written; nothing latches. The account
    daily equity stop then runs on its own UTC equity reading, which arms
    the kill switch when that reading is unusable. This call refuses.
    """

    from app.services.audit_service import AuditService
    from app.services.risk_policy.persistence import RiskPolicyService

    service = RiskPolicyService(session, AuditService(session))
    row = service.load_breaker(organization_id=organization_id, account_id=account_id)
    stored = 0 if row is None else int(row.consecutive_losses)
    peak = Decimal("1")
    stored_peak = None if row is None else row.equity_peak
    if isinstance(stored_peak, Decimal) and stored_peak.is_finite() and stored_peak > 0:
        peak = stored_peak
    service.record_breaker(
        organization_id=organization_id,
        account_id=account_id,
        equity_peak=peak,
        consecutive_losses=stored if consecutive_losses is None else consecutive_losses,
        realized_pnl=_finite_money(realized_pnl),
        unrealized_pnl=_finite_money(unrealized_pnl),
        daily_paused_until=None,
        consecutive_paused_until=None,
        drawdown_latched=False,
        now=now,
    )
    _enforce_equity_stop(
        session,
        organization_id=organization_id,
        user_id=user_id,
        account_id=account_id,
        now=now,
        settings=settings,
    )
    return True


def _enforce_equity_stop(
    session: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    account_id: UUID,
    now: datetime,
    settings: Settings | None,
) -> bool:
    """Run the account daily equity stop on its own UTC equity reading.

    The stop does not use the risk-day ``account_equity`` the pauses above
    read: that figure's realized PnL follows the user's timezone.
    """

    from app.services.risk_policy.equity_stop import enforce_account_equity_stop_from_books

    return enforce_account_equity_stop_from_books(
        session,
        organization_id=organization_id,
        user_id=user_id,
        account_id=account_id,
        now=now,
        settings=settings,
    )
