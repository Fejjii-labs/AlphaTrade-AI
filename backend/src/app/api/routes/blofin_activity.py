"""Authenticated stored account activity. HTTP requests never start synchronization."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import SessionDep, SettingsDep
from app.schemas.blofin_activity import ActivityPage
from app.security.rbac import ReaderDep
from app.services.blofin_activity_config import (
    BloFinActivitySettings,
    configured_scope,
    credential_binding,
    get_activity_settings,
)
from app.services.blofin_activity_service import read_activity, require_organization

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
    scope = configured_scope(config)
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
