"""Provider status endpoint.

Exposes the aggregated status of every registered provider so operators and the
frontend ``ProviderStatusCard`` can show live/degraded/fallback/mock state.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from app.core.dependencies import ProviderRegistryDep
from app.schemas.health import ProviderStatusResponse

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("/status", response_model=ProviderStatusResponse, summary="Provider status")
def provider_status(registry: ProviderRegistryDep) -> ProviderStatusResponse:
    # Plain ``def``: FastAPI runs it in the threadpool, so a cold-cache probe
    # can never block the event loop. Statuses come from the TTL cache.
    return ProviderStatusResponse(
        generated_at=datetime.now(UTC),
        providers=registry.cached_statuses(),
    )
