"""Read, propose, and apply grade-based risk profiles.

Apply accepts only an authenticated human access token. Agent and bot tokens
may propose. Neither path places an order or talks to BloFin.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Self
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field, model_validator

from app.core.config import Settings, get_settings
from app.core.dependencies import SessionDep
from app.core.errors import ForbiddenError
from app.db.models import BotRiskSettings
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import ActorType, AuditEventType, MembershipRole, StrictModel
from app.schemas.risk_policy import (
    RiskPolicyApplyRequest,
    RiskPolicyProposalResponse,
    RiskPolicyProposeRequest,
    RiskPolicyVersionResponse,
    RiskPolicyView,
)
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import ReaderDep, TraderDep
from app.security.tenant import TenantContext
from app.security.tokens import decode_access_token
from app.services.audit_service import AuditService
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.governance import (
    PolicyActor,
    PolicyChannel,
    channel_from_access_payload,
)
from app.services.risk_policy.persistence import RiskPolicyService
from app.services.risk_policy.profile import (
    Grade,
    PolicyKind,
    canonical_profile,
    profile_from_canonical,
)
from app.services.risk_policy.proof import decode_apply_proof

router = APIRouter(prefix="/policy", tags=["risk-policy"])
_bearer = HTTPBearer(auto_error=False)

_READ = Depends(
    tenant_rate_limit_dependency("risk:policy:read", limit=120, window_seconds=3600, user_limit=120)
)
_WRITE = Depends(
    tenant_rate_limit_dependency("risk:policy:write", limit=30, window_seconds=3600, user_limit=30)
)


def _actor(
    tenant: TenantContext,
    credentials: HTTPAuthorizationCredentials | None,
    settings: Settings,
) -> PolicyActor:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ForbiddenError("Missing bearer token.")
    payload = decode_access_token(credentials.credentials, settings)
    if payload.get("sub") != str(tenant.user_id):
        raise ForbiddenError("Token subject does not match the authenticated user.")
    if payload.get("org_id") != str(tenant.organization_id):
        raise ForbiddenError("Token organization does not match the authenticated user.")
    try:
        channel = channel_from_access_payload(payload)
    except RiskPolicyRejected as exc:
        raise ForbiddenError("Unrecognized risk-policy actor.") from exc
    return PolicyActor(actor_id=str(tenant.user_id), channel=channel, authenticated=True)


def _service(session: SessionDep) -> RiskPolicyService:
    return RiskPolicyService(session, AuditService(session))


def _kind(value: str) -> PolicyKind:
    try:
        return PolicyKind(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="kind_invalid") from exc


def _view(
    kind: PolicyKind,
    profile_row: tuple[object, object, object],
) -> RiskPolicyView:
    profile, active, scheduled = profile_row
    from app.db.risk_policy import RiskPolicyVersion
    from app.services.risk_policy.profile import RiskPolicyProfile

    if not isinstance(profile, RiskPolicyProfile):
        raise HTTPException(status_code=500, detail="profile_unreadable")
    active_row = active if isinstance(active, RiskPolicyVersion) else None
    scheduled_row = scheduled if isinstance(scheduled, RiskPolicyVersion) else None
    return RiskPolicyView(
        kind=kind.value,
        source="version" if active_row is not None else "placeholder",
        version_id=active_row.id if active_row is not None else None,
        version_number=active_row.version_number if active_row is not None else None,
        effective_at=active_row.effective_at if active_row is not None else None,
        change_class=active_row.change_class if active_row is not None else None,
        content_hash=active_row.content_hash if active_row is not None else None,
        profile=canonical_profile(profile),
        scheduled_version_number=(
            scheduled_row.version_number if scheduled_row is not None else None
        ),
        scheduled_effective_at=scheduled_row.effective_at if scheduled_row is not None else None,
        real_trading_enabled=False,
    )


@router.get("/profiles/{kind}", response_model=RiskPolicyView, dependencies=[_READ])
def read_risk_policy(
    kind: str,
    tenant: ReaderDep,
    session: SessionDep,
) -> RiskPolicyView:
    policy_kind = _kind(kind)
    return _view(
        policy_kind,
        _service(session).read(organization_id=tenant.organization_id, kind=policy_kind),
    )


@router.post(
    "/proposals",
    response_model=RiskPolicyProposalResponse,
    dependencies=[_WRITE],
)
def propose_risk_policy(
    payload: RiskPolicyProposeRequest,
    tenant: TraderDep,
    session: SessionDep,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> RiskPolicyProposalResponse:
    actor = _actor(tenant, credentials, settings)
    try:
        profile = profile_from_canonical(
            {
                "kind": payload.kind,
                "risk_percent_a_plus": format(payload.risk_percent_a_plus, "f"),
                "risk_percent_a": format(payload.risk_percent_a, "f"),
                "risk_percent_b_plus": format(payload.risk_percent_b_plus, "f"),
                "risk_percent_b": format(payload.risk_percent_b, "f"),
                "risk_percent_c": format(payload.risk_percent_c, "f"),
                "max_leverage": format(payload.max_leverage, "f"),
                "daily_loss_limit_percent": format(payload.daily_loss_limit_percent, "f"),
                "max_aggregate_open_risk_percent": format(
                    payload.max_aggregate_open_risk_percent, "f"
                ),
                "max_open_positions": str(payload.max_open_positions),
                "max_notional_per_trade_percent": format(
                    payload.max_notional_per_trade_percent, "f"
                ),
                "max_aggregate_notional_percent": format(
                    payload.max_aggregate_notional_percent, "f"
                ),
                "min_stop_atr_multiple": format(payload.min_stop_atr_multiple, "f"),
                "min_stop_absolute_floor": format(payload.min_stop_absolute_floor, "f"),
                "max_cost_share_of_r": format(payload.max_cost_share_of_r, "f"),
                "drawdown_breaker_percent": format(payload.drawdown_breaker_percent, "f"),
                "consecutive_loss_limit": str(payload.consecutive_loss_limit),
                "autonomous_internal_paper_enabled": (
                    "true" if payload.autonomous_internal_paper_enabled else "false"
                ),
                "autonomous_blofin_demo_enabled": (
                    "true" if payload.autonomous_blofin_demo_enabled else "false"
                ),
            }
        )
        row = _service(session).propose(
            organization_id=tenant.organization_id,
            actor=actor,
            profile=profile,
            reason=payload.reason,
            now=datetime.now(UTC),
        )
        session.commit()
    except RiskPolicyRejected as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return RiskPolicyProposalResponse(
        id=row.id,
        kind=row.kind,
        actor_channel=row.actor_channel,
        content_hash=row.content_hash,
        reason=row.reason,
    )


_APPLY_REFUSAL = {
    "apply_refuses_agent_token",
    "apply_refuses_service_token",
    "apply_refuses_bot_token",
    "apply_requires_human_proof",
    "apply_owner_mismatch",
    "apply_proof_missing",
    "apply_proof_expired",
    "apply_proof_not_fresh",
    "apply_proof_chat_mismatch",
    "apply_proof_proposal_mismatch",
}


@router.post(
    "/proposals/{proposal_id}/apply",
    response_model=RiskPolicyVersionResponse,
    dependencies=[_WRITE],
)
def apply_risk_policy(
    proposal_id: str,
    tenant: ReaderDep,
    session: SessionDep,
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
    body: RiskPolicyApplyRequest | None = None,
) -> RiskPolicyVersionResponse:
    from uuid import UUID

    del body
    actor = _actor(tenant, credentials, settings)
    try:
        proposal = UUID(proposal_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="proposal_id_invalid") from exc
    proof_token = request.headers.get("x-risk-policy-proof")
    now = datetime.now(UTC)
    proof = None
    if proof_token:
        try:
            proof = decode_apply_proof(proof_token, settings, proposal_id=proposal, now=now)
        except RiskPolicyRejected as exc:
            raise HTTPException(status_code=403, detail=exc.code) from exc
    try:
        row = _service(session).apply(
            organization_id=tenant.organization_id,
            proposal_id=proposal,
            actor=actor,
            proof=proof,
            owner_user_id=settings.risk_policy_owner_user_id,
            pinned_telegram_chat_id=settings.telegram_chat_id,
            now=now,
        )
        session.commit()
    except RiskPolicyRejected as exc:
        if exc.code in _APPLY_REFUSAL:
            session.commit()
            raise HTTPException(status_code=403, detail=exc.code) from exc
        session.rollback()
        raise HTTPException(status_code=422, detail=exc.code) from exc
    return RiskPolicyVersionResponse(
        id=row.id,
        kind=row.kind,
        version_number=row.version_number,
        change_class=row.change_class,
        effective_at=row.effective_at,
        actor_channel=row.actor_channel,
        content_hash=row.content_hash,
    )


class DemoBotRiskSettings(StrictModel):
    risk_per_trade_pct: Decimal = Field(gt=0, le=5, max_digits=5, decimal_places=2)
    daily_stop_pct: Decimal = Field(gt=0, le=5, max_digits=5, decimal_places=2)
    leverage: int = Field(strict=True, ge=1, le=10)
    margin_mode: Literal["isolated"]
    grade_risk_overrides: dict[Grade, Decimal] = Field(default_factory=dict)
    grades_gate_entries: bool = False

    @model_validator(mode="after")
    def check_daily_stop(self) -> Self:
        for percent in (self.risk_per_trade_pct, *self.grade_risk_overrides.values()):
            if not percent.is_finite() or not 0 < percent <= self.daily_stop_pct:
                raise ValueError("Risk per trade must be positive and at most the daily stop.")
        return self


def _demo_bot_settings(
    session: SessionDep,
    tenant: TenantContext,
    settings: Settings,
    *,
    lock: bool = False,
) -> BotRiskSettings:
    if (
        tenant.membership_role is not MembershipRole.OWNER
        or str(tenant.organization_id) != settings.governed_blofin_demo_organization_id
        or str(tenant.user_id) != settings.risk_policy_owner_user_id
    ):
        raise ForbiddenError("Only the configured demo risk owner may access bot settings.")
    row = session.get(BotRiskSettings, 1, with_for_update=lock)
    if row is None:
        raise HTTPException(status_code=503, detail="demo_risk_settings_missing")
    return row


@router.get("/demo-bot-settings", dependencies=[_READ])
def read_demo_bot_settings(
    tenant: TraderDep,
    session: SessionDep,
    settings: Settings = Depends(get_settings),
) -> DemoBotRiskSettings:
    return DemoBotRiskSettings.model_validate(
        _demo_bot_settings(session, tenant, settings), from_attributes=True
    )


@router.put("/demo-bot-settings", dependencies=[_WRITE])
def update_demo_bot_settings(
    payload: DemoBotRiskSettings,
    tenant: TraderDep,
    session: SessionDep,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> DemoBotRiskSettings:
    if _actor(tenant, credentials, settings).channel is not PolicyChannel.HUMAN:
        raise ForbiddenError("An authenticated human must edit demo risk settings.")
    row = _demo_bot_settings(session, tenant, settings, lock=True)
    before = DemoBotRiskSettings.model_validate(row, from_attributes=True)
    after = payload.model_dump(mode="json")
    row.risk_per_trade_pct = payload.risk_per_trade_pct
    row.daily_stop_pct = payload.daily_stop_pct
    row.leverage = payload.leverage
    row.margin_mode = payload.margin_mode
    row.grade_risk_overrides = {
        grade.value: str(percent) for grade, percent in payload.grade_risk_overrides.items()
    }
    row.grades_gate_entries = payload.grades_gate_entries
    request_id = uuid4().hex
    AuditService(session, strict_mode=True).record(
        AuditRecordCreate(
            request_id=request_id,
            trace_id=request_id,
            event_type=AuditEventType.RISK_SETTINGS_UPDATED,
            resource_type="bot_risk_settings",
            resource_id=str(row.id),
            actor_type=ActorType.USER,
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            metadata={"before": before.model_dump(mode="json"), "after": after},
        )
    )
    session.commit()
    return payload
