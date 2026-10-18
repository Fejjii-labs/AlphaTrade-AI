"""Call ``record_breaker`` from server figures.

Drawdown, consecutive-loss, and daily-loss pauses stay inert until something
writes them. This module is that writer for an active profile. It does not
reset a drawdown latch and it does not place an order.
"""

from __future__ import annotations

from datetime import datetime
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


def trip_breakers(
    recorder: BreakerRecorder,
    *,
    organization_id: UUID,
    account_id: UUID,
    config: BreakerConfig,
    snapshot: BreakerSnapshot,
    now: datetime,
) -> BreakerVerdict:
    """Evaluate one snapshot and persist it. A latched drawdown stays latched."""

    verdict = evaluate_breakers(config, snapshot)
    recorder.record_breaker(
        organization_id=organization_id,
        account_id=account_id,
        equity_peak=verdict.equity_peak,
        consecutive_losses=snapshot.consecutive_losses,
        realized_pnl=snapshot.realized_pnl_today,
        unrealized_pnl=snapshot.unrealized_pnl_today,
        daily_paused_until=verdict.paused_until if verdict.daily_loss_active else None,
        consecutive_paused_until=(
            verdict.paused_until if verdict.consecutive_loss_active else None
        ),
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
    consecutive = stored if consecutive_losses is None else consecutive_losses
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
    )
    return verdict.refuse_new_trades
