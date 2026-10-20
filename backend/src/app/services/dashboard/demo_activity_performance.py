"""Attach demo-account win rate from activity-worker history.

Numbers stay empty when the activity worker is off, and they also stay empty
when it is on. fillPnl fee basis and a flat start are not proven. Manual
AlphaTrade demo counts are not a substitute for the whole demo account.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.blofin_activity import BloFinActivityAccount, BloFinActivityCursor
from app.schemas.dashboard_demo_account import (
    DashboardDemoAccount,
    DemoAccountPerformance,
)
from app.security.tenant import TenantContext
from app.services.blofin_activity_config import BloFinActivitySettings, get_activity_settings
from app.services.dashboard.activity_round_trips import METHOD

_DISABLED = (
    "Activity sync is disabled. Win rate and realized P&L for the whole demo "
    "account are withheld. Manual AlphaTrade trades are not a substitute."
)


def present_demo_account(
    account: DashboardDemoAccount,
    *,
    session: Session,
    settings: Settings,
    tenant: TenantContext,
) -> DashboardDemoAccount:
    """Stamp the configured refresh interval and activity performance."""
    account.refresh_interval_seconds = settings.blofin_demo_snapshot_sync_seconds
    apply_activity_performance(account, session=session, tenant=tenant)
    from app.services.blofin_demo_manual_mirror import publish_mirror_performance

    publish_mirror_performance(account, session=session, tenant=tenant, settings=settings)
    return account


def apply_activity_performance(
    account: DashboardDemoAccount,
    *,
    session: Session,
    tenant: TenantContext,
    config: BloFinActivitySettings | None = None,
) -> None:
    """Fill activity fields. Does not change manual AlphaTrade coverage."""
    settings = config if config is not None else get_activity_settings()
    performance = account.performance
    performance.pnl_method = METHOD
    if not settings.enabled:
        performance.activity_sync_status = "disabled"
        performance.activity_coverage = _DISABLED
        performance.win_rate = None
        performance.trade_count = None
        performance.wins = None
        performance.losses = None
        performance.realized_pnl = None
        performance.window_start = None
        performance.window_end = None
        performance.unresolved_round_trips = None
        return
    if settings.organization_id is None or not settings.expected_uid:
        _unavailable(account, "Activity sync is enabled without an organization and UID pin.")
        return
    if settings.organization_id != tenant.organization_id:
        _unavailable(account, "Activity sync is pinned to a different organization.")
        return
    uid = settings.expected_uid
    proof = session.get(BloFinActivityAccount, (tenant.organization_id, "demo", uid))
    if proof is None or proof.identity_verified_at is None or proof.identity_error:
        _unavailable(account, "Activity sync has not verified this demo account.")
        return
    cursor = session.get(BloFinActivityCursor, (tenant.organization_id, "demo", uid, "fill"))
    if cursor is None or cursor.last_successful_sync is None:
        _unavailable(account, "Activity sync has not stored fill history yet.")
        return
    # A complete cursor and a newest-row cap are not proof the account was flat
    # at the first retained fill, and fillPnl fee/funding basis is unverified.
    _withhold_unverified(
        performance,
        window_start=_millis(cursor.covered_begin_ms),
        window_end=_millis(cursor.covered_end_ms),
    )
    return


def _unavailable(account: DashboardDemoAccount, message: str) -> None:
    performance = account.performance
    performance.activity_sync_status = "unavailable"
    performance.activity_coverage = message
    performance.win_rate = None
    performance.trade_count = None
    performance.wins = None
    performance.losses = None
    performance.realized_pnl = None
    performance.window_start = None
    performance.window_end = None
    performance.unresolved_round_trips = None


def _withhold_unverified(
    performance: DemoAccountPerformance,
    *,
    window_start: datetime | None,
    window_end: datetime | None,
) -> None:
    """Keep headline numbers empty until fee basis and a flat start are proven."""
    performance.activity_sync_status = "partial"
    performance.activity_coverage = (
        "Win rate and realized P&L are withheld. fillPnl is not established "
        "as gross or net of fees or funding, and the fee sign is not "
        "independently verified. A stored fill cursor, including a complete "
        "lookback, is not proof that each position started flat."
    )
    performance.win_rate = None
    performance.trade_count = None
    performance.wins = None
    performance.losses = None
    performance.realized_pnl = None
    performance.unresolved_round_trips = None
    performance.window_start = window_start
    performance.window_end = window_end


def _millis(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)
