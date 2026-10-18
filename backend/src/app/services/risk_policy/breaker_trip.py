"""Call ``record_breaker`` from server figures.

Drawdown, consecutive-loss, and daily-loss pauses stay inert until something
writes them. This module is that writer for an active profile. It does not
reset a drawdown latch and it does not place an order.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.risk_policy.breakers import (
    BreakerConfig,
    BreakerSnapshot,
    BreakerVerdict,
    evaluate_breakers,
)
from app.services.risk_policy.clock import next_utc_day
from app.services.risk_policy.errors import RiskPolicyRejected
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


def consecutive_pause_expiry(
    now: datetime,
    *,
    cooldown_hours: int,
    existing_paused_until: datetime | None,
) -> datetime:
    """Set the pause once. A later scan keeps the stored expiry.

    The deadline is the later of the next UTC midnight and ``cooldown_hours``
    after the first trip. Default cooldown is 24 hours.
    """

    if now.tzinfo is None:
        raise RiskPolicyRejected("timestamp_must_be_aware")
    current = _as_utc(now)
    if existing_paused_until is not None and _as_utc(existing_paused_until) > current:
        return _as_utc(existing_paused_until)
    if (
        isinstance(cooldown_hours, bool)
        or not isinstance(cooldown_hours, int)
        or cooldown_hours < 1
    ):
        raise RiskPolicyRejected("consecutive_loss_cooldown_invalid")
    return max(next_utc_day(current), current + timedelta(hours=cooldown_hours))


def profile_close_consecutive(current: int, net_pnl: Decimal) -> int:
    """A loss increments the streak. A win or a flat close resets it.

    This does not reset a drawdown latch and does not place an order.
    """

    return 0 if net_pnl >= 0 else current + 1


def trip_breakers(
    recorder: BreakerRecorder,
    *,
    organization_id: UUID,
    account_id: UUID,
    config: BreakerConfig,
    snapshot: BreakerSnapshot,
    now: datetime,
    existing_consecutive_paused_until: datetime | None = None,
    cooldown_hours: int = 24,
) -> BreakerVerdict:
    """Evaluate one snapshot and persist it. A latched drawdown stays latched.

    The consecutive pause is written once. A scan while it is still in the
    future does not move it. After it expires the caller passes a cleared
    streak; this function does not invent a new pause for a count of zero.
    """

    verdict = evaluate_breakers(config, snapshot)
    if verdict.consecutive_loss_active:
        consecutive_until = consecutive_pause_expiry(
            now,
            cooldown_hours=cooldown_hours,
            existing_paused_until=existing_consecutive_paused_until,
        )
    else:
        consecutive_until = None
    recorder.record_breaker(
        organization_id=organization_id,
        account_id=account_id,
        equity_peak=verdict.equity_peak,
        consecutive_losses=snapshot.consecutive_losses,
        realized_pnl=snapshot.realized_pnl_today,
        unrealized_pnl=snapshot.unrealized_pnl_today,
        daily_paused_until=verdict.paused_until if verdict.daily_loss_active else None,
        consecutive_paused_until=consecutive_until,
        drawdown_latched=verdict.drawdown_active,
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
) -> bool:
    """Sync daily figures, then trip. Returns whether new trades are refused."""

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
        return True
    service = RiskPolicyService(session, AuditService(session))
    row = service.load_breaker(organization_id=organization_id, account_id=account_id)
    peak = equity
    if row is not None and isinstance(row.equity_peak, Decimal) and row.equity_peak > 0:
        peak = row.equity_peak
    stored = 0 if row is None else int(row.consecutive_losses)
    existing_pause = None if row is None else getattr(row, "consecutive_paused_until", None)
    if consecutive_losses is None:
        consecutive = streak_after_expiry(stored, existing_pause, now)
    else:
        consecutive = consecutive_losses
    kept_pause = (
        existing_pause
        if existing_pause is not None and _as_utc(existing_pause) > _as_utc(now)
        else None
    )
    latched = False if row is None else bool(row.drawdown_latched)
    realized = daily.realized_pnl
    unrealized = daily.unrealized_pnl
    reference = equity - realized - unrealized
    if reference <= 0:
        reference = equity
    verdict = trip_breakers(
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
            drawdown_latched=latched,
            now=now,
        ),
        now=now,
        existing_consecutive_paused_until=kept_pause,
        cooldown_hours=profile.consecutive_loss_cooldown_hours,
    )
    return verdict.refuse_new_trades
