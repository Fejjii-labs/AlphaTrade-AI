"""Strategy Brain: explicit library proposals and bounded stored setup reads."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from fastapi import APIRouter, Depends
from pydantic import Field

from app.core.dependencies import SessionDep
from app.core.errors import ValidationAppError
from app.db.strategy_brain import BrainSetupEventRow
from app.schemas.common import StrategyLifecycleState, StrictModel
from app.schemas.nested_continuation import NESTED_KIND, NestedContinuationSpec
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import ReaderDep, TraderDep
from app.services.strategy_versioning import StrategyVersioningService
from app.strategy_brain.nested_preview import preview_nested_subscriptions
from app.strategy_brain.records import _insert_once
from app.strategy_brain.service import BrainFamily, create_template, details, overview
from app.strategy_brain.sfp.contracts import SFP_KIND, SfpSpec

router = APIRouter(
    prefix="/strategy-brain",
    tags=["strategy-brain"],
    dependencies=[
        Depends(tenant_rate_limit_dependency("strategy-brain", limit=120, window_seconds=3600))
    ],
)


@router.get("/overview")
async def read_overview(
    tenant: ReaderDep,
    session: SessionDep,
    symbol: str | None = None,
    family: BrainFamily | None = None,
    since: datetime | None = None,
) -> dict:
    return overview(
        session,
        organization_id=tenant.organization_id,
        symbol=symbol,
        family=family,
        since=since,
    )


@router.get("/setups/{setup_id}")
async def read_setup(setup_id: UUID, tenant: ReaderDep, session: SessionDep) -> dict:
    return details(session, organization_id=tenant.organization_id, setup_id=setup_id)


@router.post("/templates/nested", status_code=201)
async def propose_nested(
    body: NestedContinuationSpec, tenant: TraderDep, session: SessionDep
) -> dict:
    result = create_template(
        session, organization_id=tenant.organization_id, user_id=tenant.user_id, spec=body
    )
    session.commit()
    return result


class NestedSubscriptionPreviewRequest(StrictModel):
    baseline_version_id: UUID


@router.post("/templates/nested/preview")
async def preview_nested(
    body: NestedSubscriptionPreviewRequest, tenant: ReaderDep, session: SessionDep
) -> dict[str, Any]:
    return preview_nested_subscriptions(
        session,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        baseline_version_id=body.baseline_version_id,
    )


@router.post("/templates/sfp", status_code=201)
async def propose_sfp(body: SfpSpec, tenant: TraderDep, session: SessionDep) -> dict:
    result = create_template(
        session, organization_id=tenant.organization_id, user_id=tenant.user_id, spec=body
    )
    session.commit()
    return result


class ObservationRequest(StrictModel):
    idempotency_key: UUID
    observation: str = Field(min_length=1, max_length=2000)


@router.post("/setups/{setup_id}/observations")
async def user_observation(
    setup_id: UUID, body: ObservationRequest, tenant: TraderDep, session: SessionDep
) -> dict:
    details(session, organization_id=tenant.organization_id, setup_id=setup_id)
    identity = uuid5(setup_id, f"user:{tenant.user_id}:{body.idempotency_key}")
    payload = {
        "user_id": str(tenant.user_id),
        "observation": body.observation,
        "provenance": "user_belief",
        "changes_active_rules": False,
    }
    existing = session.get(BrainSetupEventRow, identity)
    if existing is not None and existing.payload != payload:
        raise ValidationAppError("Observation idempotency key reused with different content.")
    if existing is None:
        _insert_once(
            session,
            BrainSetupEventRow(
                id=identity,
                organization_id=tenant.organization_id,
                setup_id=setup_id,
                kind="user_observation",
                occurred_at=datetime.now(UTC),
                payload=payload,
            ),
        )
        session.flush()
        existing = session.get(BrainSetupEventRow, identity)
        if existing.payload != payload:
            raise ValidationAppError("Observation idempotency key reused with different content.")
        session.commit()
    return {"record_id": str(identity), **payload}


class LifecycleRequest(StrictModel):
    state: StrategyLifecycleState
    reason: str = Field(min_length=1, max_length=500)


@router.post("/strategies/{strategy_id}/lifecycle")
async def research_lifecycle(
    strategy_id: UUID, body: LifecycleRequest, tenant: TraderDep, session: SessionDep
) -> dict:
    service = StrategyVersioningService(session)
    strategy = service.require_strategy(
        strategy_id, organization_id=tenant.organization_id, user_id=tenant.user_id
    )
    version = service.selected_version(strategy)
    if (
        version is None
        or not version.pattern_spec
        or version.pattern_spec.get("kind") not in {NESTED_KIND, SFP_KIND}
    ):
        raise ValidationAppError("Not a registered Strategy Brain strategy version.")
    latest = service.latest_lifecycle_event_for_version(version.id)
    state = latest.new_state.value if latest else "draft"
    allowed = {
        "draft": {"observation"},
        "observation": {"hypothesis", "retired"},
        "hypothesis": {"testing", "retired"},
        "testing": {"paper_active", "retired"},
        "paper_active": {"paused", "retired"},
        "approved": {"active", "paused", "retired"},
        "active": {"paused", "retired"},
        "paused": {"testing", "retired"},
        "review_required": {"testing", "paused", "retired"},
    }
    if body.state.value not in allowed.get(state, set()):
        raise ValidationAppError(
            "Invalid lifecycle transition; approval uses the existing compile/review endpoint."
        )
    event = service.append_lifecycle(
        organization_id=tenant.organization_id,
        strategy_id=strategy.id,
        strategy_version_id=version.id,
        new_state=body.state,
        actor_user_id=tenant.user_id,
        reason=body.reason,
    )
    session.commit()
    return {
        "record_id": str(event.id),
        "state": event.new_state.value,
        "execution_permission": "existing_paper_gates_only",
    }
