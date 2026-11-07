"""Owner-only demo seed endpoint (staging/local only).

Pinned to the demo org (or first-time bootstrap when it does not exist yet);
a body password is honoured only in ``local``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.dependencies import SessionDep, SettingsDep
from app.schemas.demo import DemoSeedRequest, DemoSeedResponse
from app.security.rbac import OwnerDep
from app.services.demo_seed_service import DemoSeedService

router = APIRouter(prefix="/demo", tags=["demo"])


@router.post(
    "/seed",
    response_model=DemoSeedResponse,
    summary="Seed synthetic paper-only demo data (demo-org owner, staging/local)",
)
def seed_demo_data(
    tenant: OwnerDep,
    session: SessionDep,
    settings: SettingsDep,
    body: DemoSeedRequest | None = None,
) -> DemoSeedResponse:
    service = DemoSeedService(session, settings)
    result = service.seed(
        password=body.password if body else None,
        requester_user_id=tenant.user_id,
        requester_organization_id=tenant.organization_id,
    )
    return DemoSeedResponse(
        organization_id=result.organization_id,
        user_id=result.user_id,
        email=result.email,
        strategies_seeded=result.strategies_seeded,
        paper_runs_seeded=result.paper_runs_seeded,
        alerts_seeded=result.alerts_seeded,
        lessons_seeded=result.lessons_seeded,
        journals_seeded=result.journals_seeded,
    )
