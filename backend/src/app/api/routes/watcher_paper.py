"""Paper Watcher runtime status (monitoring only; no scans from HTTP)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.core.config import Settings
from app.core.dependencies import SessionDep, SettingsDep
from app.core.errors import ValidationAppError
from app.db.models import KillSwitchState
from app.repositories.watcher_watchlist import WatcherWatchlistRepository
from app.schemas.watcher_assignments import WatcherAssignmentReplace, WatcherAssignmentsRead
from app.schemas.watcher_paper import (
    WATCHER_PAPER_ACTIVATION_REQUIREMENTS,
    WatcherPaperRuntimeStatus,
)
from app.schemas.watcher_watchlist import (
    WatcherSymbolRuntimeRead,
    WatcherWatchlistConfigurationRead,
    WatcherWatchlistReplace,
    WatcherWatchlistSlotRead,
    WatcherWatchlistStatusRead,
)
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import ReaderDep, TraderDep
from app.services.watcher_assignment_service import WatcherAssignmentService
from app.workers.watcher_paper import paper_runtime_enabled
from app.workers.watcher_watchlist import WatchlistConfiguration, WatchlistValidationError

router = APIRouter(prefix="/watcher", tags=["watcher-paper"])

_READ_LIMIT = Depends(tenant_rate_limit_dependency("watcher:read", limit=120, window_seconds=3600))
_WRITE_LIMIT = Depends(tenant_rate_limit_dependency("watcher:write", limit=60, window_seconds=3600))


@router.get(
    "/paper-runtime/status",
    response_model=WatcherPaperRuntimeStatus,
    summary="Paper Watcher runtime status",
    dependencies=[_READ_LIMIT],
)
async def watcher_paper_runtime_status(
    request: Request,
    tenant: ReaderDep,
    settings: SettingsDep,
    session: SessionDep,
) -> WatcherPaperRuntimeStatus:
    now = datetime.now(UTC)
    repo = WatcherWatchlistRepository(session)
    config = repo.load(tenant.organization_id)
    runtime = repo.runtime_status(
        tenant.organization_id,
        now=now,
        max_age_seconds=max(90.0, settings.watcher_paper_poll_interval_seconds * 3),
    )
    status = WatcherPaperRuntimeStatus(
        enabled=paper_runtime_enabled(settings),
        running=False,
        symbols=list(config.enabled_symbols()),
        poll_interval_seconds=settings.watcher_paper_poll_interval_seconds,
        max_scopes_per_cycle=settings.watcher_paper_max_scopes_per_cycle,
        paper_only=True,
        real_trading_enabled=settings.real_trading_enabled,
        telegram_enabled=settings.telegram_interaction_enabled,
        kill_switch_active=_tenant_kill_switch_active(session, settings, tenant.organization_id),
        remaining_activation_requirements=list(WATCHER_PAPER_ACTIVATION_REQUIREMENTS),
    )
    # The same durable, revision/freshness-fenced authority as per-symbol status.
    # No API-process snapshot: it may belong to a different tenant or be absent
    # when the dedicated worker runs on another host.
    return WatcherPaperRuntimeStatus(**(status.model_dump() | (runtime or {})))


@router.get(
    "/watchlist",
    response_model=WatcherWatchlistConfigurationRead,
    summary="Paper Watcher watchlist configuration",
    dependencies=[_READ_LIMIT],
)
async def read_watcher_watchlist(
    request: Request,
    tenant: ReaderDep,
    settings: SettingsDep,
    session: SessionDep,
) -> WatcherWatchlistConfigurationRead:
    """Editable slots. Runtime scan state is not included."""

    return _configuration_read(WatcherWatchlistRepository(session).load(tenant.organization_id))


@router.put(
    "/watchlist",
    response_model=WatcherWatchlistConfigurationRead,
    summary="Replace the paper Watcher watchlist",
    dependencies=[_WRITE_LIMIT],
)
async def replace_watcher_watchlist(
    body: WatcherWatchlistReplace,
    request: Request,
    tenant: TraderDep,
    settings: SettingsDep,
    session: SessionDep,
) -> WatcherWatchlistConfigurationRead:
    """Persist five slots. Does not place orders or enable real trading."""

    if settings.real_trading_enabled or settings.enable_real_trading:
        raise ValidationAppError("Watcher watchlist changes stay paper-only.")
    try:
        updated = WatcherWatchlistRepository(session).replace(
            tenant.organization_id,
            [(slot.symbol, slot.enabled) for slot in body.slots],
            expected_revision=body.revision,
        )
        session.commit()
    except WatchlistValidationError as exc:
        session.rollback()
        raise ValidationAppError(str(exc)) from exc
    return _configuration_read(updated)


@router.get(
    "/watchlist/status",
    response_model=WatcherWatchlistStatusRead,
    summary="Per-symbol paper Watcher runtime status",
    dependencies=[_READ_LIMIT],
)
async def read_watcher_watchlist_status(
    request: Request,
    tenant: ReaderDep,
    settings: SettingsDep,
    session: SessionDep,
) -> WatcherWatchlistStatusRead:
    """Runtime status for the configured slots. This does not change the watchlist."""

    now = datetime.now(UTC)
    max_age = max(90.0, settings.watcher_paper_poll_interval_seconds * 3)
    config, rows = WatcherWatchlistRepository(session).status(
        tenant.organization_id,
        source_mode=settings.perpetual_evidence_source,
        now=now,
        max_age_seconds=max_age,
    )
    return WatcherWatchlistStatusRead(
        configuration_revision=config.revision,
        observed_at=now,
        stale_after_seconds=max_age,
        paper_only=True,
        real_trading_enabled=settings.real_trading_enabled,
        symbols=[WatcherSymbolRuntimeRead(**row) for row in rows],
    )


def _configuration_read(config: WatchlistConfiguration) -> WatcherWatchlistConfigurationRead:
    return WatcherWatchlistConfigurationRead(
        revision=config.revision,
        updated_at=config.updated_at,
        slots=[
            WatcherWatchlistSlotRead(
                position=slot.position,
                symbol=slot.symbol,
                enabled=slot.enabled,
            )
            for slot in config.slots
        ],
        paper_only=True,
    )


def _tenant_kill_switch_active(
    session: SessionDep, settings: Settings, organization_id: UUID
) -> bool:
    if settings.global_kill_switch_active:
        return True
    row = session.scalar(
        select(KillSwitchState).where(KillSwitchState.organization_id == organization_id)
    )
    return bool(row is not None and row.active)


@router.get("/assignments", response_model=WatcherAssignmentsRead, dependencies=[_READ_LIMIT])
def read_watcher_assignments(tenant: ReaderDep, session: SessionDep) -> WatcherAssignmentsRead:
    return WatcherAssignmentService(session).read(
        organization_id=tenant.organization_id, user_id=tenant.user_id
    )


@router.put("/assignments", response_model=WatcherAssignmentsRead, dependencies=[_WRITE_LIMIT])
def replace_watcher_assignments(
    body: WatcherAssignmentReplace, tenant: TraderDep, session: SessionDep, settings: SettingsDep
) -> WatcherAssignmentsRead:
    if settings.real_trading_enabled or settings.enable_real_trading:
        raise ValidationAppError("Monitoring assignment changes stay paper-only.")
    try:
        result = WatcherAssignmentService(session).replace(
            organization_id=tenant.organization_id, user_id=tenant.user_id, body=body
        )
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
