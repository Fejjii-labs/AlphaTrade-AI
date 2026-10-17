"""Authenticated stored account activity. HTTP requests never start synchronization."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import SessionDep, SettingsDep
from app.core.errors import ExchangeDemoInactiveError
from app.schemas.blofin_activity import ActivityPage
from app.security.rbac import ReaderDep
from app.services.blofin_activity_config import (
    BloFinActivitySettings,
    configured_scope,
    credential_binding,
    get_activity_settings,
)
from app.services.blofin_activity_service import LIMITATIONS, read_activity, require_organization

router = APIRouter(prefix="/exchange/blofin/activity", tags=["blofin-activity"])


@router.get("", response_model=ActivityPage)
def activity(
    tenant: ReaderDep,
    session: SessionDep,
    settings: SettingsDep,
    config: Annotated[BloFinActivitySettings, Depends(get_activity_settings)],
    kind: Literal["order", "fill"] = "fill",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> ActivityPage:
    try:
        scope = configured_scope(config)
    except ExchangeDemoInactiveError:
        # A paper page load asks for stored history even when activity pins are off.
        # That read is unverified, not a conflict with a demo session.
        return ActivityPage(
            organization_id=tenant.organization_id,
            account_uid="",
            identity_status="unverified",
            identity_error_code="activity_not_configured",
            items=[],
            coverage=[],
            freshness="unverified",
            limitations=LIMITATIONS,
            generated_at=datetime.now(UTC),
        )
    require_organization(scope, tenant.organization_id)
    return read_activity(
        session,
        scope=scope,
        binding=credential_binding(settings),
        kind=kind,
        limit=limit,
        cursor=cursor,
        stale_seconds=config.stale_seconds,
    )
