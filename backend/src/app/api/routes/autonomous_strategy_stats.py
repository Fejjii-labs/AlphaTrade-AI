"""Per-strategy trade stats for the Autonomous Trading page (read-only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.dependencies import SessionDep
from app.schemas.autonomous_strategy_stats import AutonomousStrategyStatsView
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import ReaderDep
from app.services.autonomous_strategy_stats import AutonomousStrategyStatsService

router = APIRouter(prefix="/autonomous", tags=["autonomous-trading"])

_READ = Depends(
    tenant_rate_limit_dependency(
        "autonomous:strategy-stats:read", limit=120, window_seconds=3600, user_limit=120
    )
)


@router.get(
    "/strategy-stats",
    response_model=AutonomousStrategyStatsView,
    summary="Trades and stats per strategy",
    dependencies=[_READ],
)
async def get_autonomous_strategy_stats(
    tenant: ReaderDep,
    session: SessionDep,
) -> AutonomousStrategyStatsView:
    return AutonomousStrategyStatsService(session).view(
        organization_id=tenant.organization_id, user_id=tenant.user_id
    )
