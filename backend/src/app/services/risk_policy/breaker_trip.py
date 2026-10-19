"""Call ``record_breaker`` from server figures.

Drawdown, consecutive-loss, and daily-loss pauses stay inert until something
writes them. This module is that writer for an active profile. It does not
reset a drawdown latch and it does not place an order.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
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


# Audited reason stored on the organization kill switch. A later scan does
# not replace it and does not clear the switch.
DRAWDOWN_KILL_REASON = (
    "Profile drawdown breaker tripped; new entries stay blocked until a human "
    "deactivates the kill switch"
)


def _pause_still_in_force(paused_until: datetime | None, now: datetime) -> bool:
    return paused_until is not None and _as_utc(paused_until) > _as_utc(now)


def trip_breakers(
    recorder: BreakerRecorder,
    *,
    organization_id: UUID,
    account_id: UUID,
    config: BreakerConfig,
    snapshot: BreakerSnapshot,
    now: datetime,
    existing_consecutive_paused_until: datetime | None = None,
    existing_daily_paused_until: datetime | None = None,
    cooldown_hours: int = 24,
) -> BreakerVerdict:
    """Evaluate one snapshot and persist it. A latched drawdown stays latched.

    The consecutive pause and the daily pause are each written once. A scan
    while either timestamp is still in the future does not move it and does
    not clear it. The daily pause ends at its stored expiry. The consecutive
    pause ends at its stored expiry or at ``reset_consecutive_losses``. After
    the consecutive pause expires the caller passes a cleared streak; this
    function does not invent a new pause for a count of zero.
    """

    verdict = evaluate_breakers(config, snapshot)
    existing_pause = existing_consecutive_paused_until
    pause_still_in_force = _pause_still_in_force(existing_pause, now)
    if verdict.consecutive_loss_active:
        consecutive_until = consecutive_pause_expiry(
            now,
            cooldown_hours=cooldown_hours,
            existing_paused_until=existing_pause,
        )
    elif pause_still_in_force:
        # A close or a scan cannot end an unexpired pause. Only the human
        # reset, or the expiry itself, does that.
        assert existing_pause is not None
        consecutive_until = _as_utc(existing_pause)
    else:
        consecutive_until = None
    daily_existing = existing_daily_paused_until
    if _pause_still_in_force(daily_existing, now):
        # Recovered PnL on the same UTC day does not end the daily pause.
        # There is no early reset. The stored timestamp is the expiry.
        assert daily_existing is not None
        daily_until = _as_utc(daily_existing)
    elif verdict.daily_loss_active:
        daily_until = next_utc_day(_as_utc(now))
    else:
        daily_until = None
    pause_held = daily_until is not None or consecutive_until is not None
    if pause_held and not verdict.refuse_new_trades:
        verdict = replace(verdict, refuse_new_trades=True)
    recorder.record_breaker(
        organization_id=organization_id,
        account_id=account_id,
        equity_peak=verdict.equity_peak,
        consecutive_losses=snapshot.consecutive_losses,
        realized_pnl=snapshot.realized_pnl_today,
        unrealized_pnl=snapshot.unrealized_pnl_today,
        daily_paused_until=daily_until,
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
    settings: Settings | None = None,
) -> bool:
    """Sync daily figures, then trip. Returns whether new trades are refused.

    A drawdown verdict arms the organization kill switch. A later scan does
    not deactivate it. Only ``KillSwitchService.deactivate`` does, and that
    is a human reset.
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
        return True
    service = RiskPolicyService(session, AuditService(session))
    row = service.load_breaker(organization_id=organization_id, account_id=account_id)
    peak = equity
    if row is not None and isinstance(row.equity_peak, Decimal) and row.equity_peak > 0:
        peak = row.equity_peak
    stored = 0 if row is None else int(row.consecutive_losses)
    existing_pause = None if row is None else getattr(row, "consecutive_paused_until", None)
    existing_daily = None if row is None else getattr(row, "daily_paused_until", None)
    if consecutive_losses is None:
        consecutive = streak_after_expiry(stored, existing_pause, now)
    else:
        consecutive = consecutive_losses
    kept_pause = existing_pause if _pause_still_in_force(existing_pause, now) else None
    kept_daily = existing_daily if _pause_still_in_force(existing_daily, now) else None
    # A paper or demo close may pass a reset count. While the pause is in
    # the future that count cannot fall. A loss, which is larger, still counts.
    if kept_pause is not None and consecutive < stored:
        consecutive = stored
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
        existing_daily_paused_until=kept_daily,
        cooldown_hours=profile.consecutive_loss_cooldown_hours,
    )
    if verdict.trip_kill_switch:
        _arm_drawdown_kill_switch(
            session,
            organization_id=organization_id,
            user_id=user_id,
            settings=settings,
        )
    return verdict.refuse_new_trades


def _arm_drawdown_kill_switch(
    session: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    settings: Settings | None,
) -> None:
    """Arm the existing organization switch. Already-on is a no-op.

    ``KillSwitchService.activate`` returns the current row without a new
    audit event when the switch is already active. This function never
    calls deactivate.
    """

    from app.core.config import get_settings
    from app.schemas.risk import KillSwitchMutationRequest
    from app.services.audit_service import AuditService
    from app.services.risk.kill_switch import KillSwitchService

    resolved = get_settings() if settings is None else settings
    KillSwitchService(session, AuditService(session), resolved).activate(
        organization_id=organization_id,
        actor_user_id=user_id,
        payload=KillSwitchMutationRequest(confirm=True, reason=DRAWDOWN_KILL_REASON),
    )
