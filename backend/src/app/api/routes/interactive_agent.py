"""Interactive agent API. Turns propose. Confirm is a separate request.

Screenshot and voice routes return the unimplemented contract. They do not
accept image or audio bytes.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Header, Request

from app.core.dependencies import AuditServiceDep, SessionDep, SettingsDep
from app.evidence_pipeline.service import CanonicalEvidenceService
from app.interactive_agent.canonical_market import CanonicalPerpetualQuoteReader
from app.interactive_agent.contracts import (
    AgentCapabilityCatalog,
    AgentTurnRequest,
    AgentTurnResult,
    ProposalDecisionRequest,
    ProposalLifecycle,
    ScreenshotAnalysisContract,
    ScreenshotAnalysisRequest,
    StructuredActionProposal,
    VoiceInputRequest,
    VoiceIoContract,
    VoiceOutputRequest,
)
from app.interactive_agent.conversation import ModelConversationalResponder
from app.interactive_agent.proposals import find_proposal
from app.interactive_agent.service import InteractiveAgentService
from app.interactive_agent.turn_contracts import TURN_CONFLICT_RESPONSES
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import TraderDep
from app.services.agent_paper_execution import AgentPaperExecutionService
from app.services.turn_policy import TURN_DEPENDENCIES

router = APIRouter(prefix="/agent", tags=["agent"])
_knowledge_application_rate_limit = tenant_rate_limit_dependency(
    "knowledge:ingest", limit=20, window_seconds=3600, ip_limit=40, user_limit=20
)


def _service(
    session: SessionDep,
    settings: SettingsDep,
    organization_id: uuid.UUID,
    paper_execution: AgentPaperExecutionService | None = None,
) -> InteractiveAgentService:
    """Wire canonical evidence and the existing model. Neither path confirms."""
    return InteractiveAgentService(
        session,
        settings=settings,
        market_reader=CanonicalPerpetualQuoteReader(
            CanonicalEvidenceService(settings, session=session),
            organization_id,
        ),
        responder=ModelConversationalResponder(session, settings),
        paper_execution=paper_execution,
    )


def _paper_authority(
    request: Request, session: SessionDep, settings: SettingsDep
) -> AgentPaperExecutionService:
    from app.db.session import get_session_factory
    from app.runtime.canonical import ProductionCanonicalRuntime, build_production_canonical_runtime

    runtime = getattr(request.app.state, "canonical_runtime", None)
    if not isinstance(runtime, ProductionCanonicalRuntime):
        runtime = build_production_canonical_runtime(get_session_factory(), settings=settings)
    return AgentPaperExecutionService(
        session, settings, runtime, CanonicalEvidenceService(settings, session=session)
    )


@router.get("/capabilities", response_model=AgentCapabilityCatalog, summary="Agent capabilities")
async def agent_capabilities(
    tenant: TraderDep,
    session: SessionDep,
    settings: SettingsDep,
) -> AgentCapabilityCatalog:
    del tenant
    return InteractiveAgentService(session, settings=settings).catalog()


@router.post(
    "/turns",
    response_model=AgentTurnResult,
    summary="Run one agent turn",
    dependencies=TURN_DEPENDENCIES,
    responses=TURN_CONFLICT_RESPONSES,
)
async def agent_turn(
    body: AgentTurnRequest,
    tenant: TraderDep,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
    idempotency_key: str | None = Header(
        default=None, description="Stable UUID for turn recovery."
    ),
) -> AgentTurnResult:
    from functools import partial

    from starlette.concurrency import run_in_threadpool

    from app.interactive_agent.turn_runtime import run_interactive
    from app.services.turn_coordinator import TurnCoordinator, request_key

    key = request_key(idempotency_key)
    coordinator = TurnCoordinator.from_request_session(session)
    result = await run_in_threadpool(
        partial(
            coordinator.run,
            channel="interactive",
            key=key,
            body=body.model_dump(mode="json"),
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            conversation_id=body.conversation_id,
            strategy_id=body.strategy_id,
            work=lambda reservation: run_interactive(
                coordinator,
                settings,
                reservation,
                body,
                paper_factory=lambda phase_session: _paper_authority(
                    request, phase_session, settings
                ),
            ),
        )
    )
    return AgentTurnResult.model_validate(result)


@router.post(
    "/proposals/{proposal_id}/confirm",
    response_model=StructuredActionProposal,
    summary="Confirm one structured agent proposal",
)
async def confirm_agent_proposal(
    proposal_id: uuid.UUID,
    body: ProposalDecisionRequest,
    tenant: TraderDep,
    session: SessionDep,
    settings: SettingsDep,
    background_tasks: BackgroundTasks,
    request: Request,
    audit_service: AuditServiceDep,
) -> StructuredActionProposal:
    _, pending = find_proposal(
        session,
        conversation_id=body.conversation_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        proposal_id=proposal_id,
        lock=True,
    )
    if pending.status is ProposalLifecycle.PROPOSED and pending.payload.get("ingest_request"):
        _knowledge_application_rate_limit(
            request=request, tenant=tenant, session=session, audit_service=audit_service
        )
    service = (
        _service(
            session,
            settings,
            tenant.organization_id,
            paper_execution=_paper_authority(request, session, settings),
        )
        if (pending.payload.get("action") or {}).get("name") == "paper_trade.prepare_execution"
        else _service(session, settings, tenant.organization_id)
    )
    result = service.confirm(
        proposal_id,
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    if (
        service.confirmation_changed
        and result.application_result.get("record_type") == "backtest_runs"
    ):
        from app.api.routes.backtests import enqueue_backtest_if_needed
        from app.schemas.backtest import BacktestRun

        enqueue_backtest_if_needed(
            result=BacktestRun.model_validate(result.application_result["backtest"]),
            session=session,
            settings=settings,
            background_tasks=background_tasks,
        )
    return result


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=StructuredActionProposal,
    summary="Reject one structured agent proposal",
)
async def reject_agent_proposal(
    proposal_id: uuid.UUID,
    body: ProposalDecisionRequest,
    tenant: TraderDep,
    session: SessionDep,
    settings: SettingsDep,
) -> StructuredActionProposal:
    result = _service(session, settings, tenant.organization_id).reject(
        proposal_id,
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post(
    "/screenshots/analyze",
    response_model=ScreenshotAnalysisContract,
    summary="Screenshot analysis contract",
)
async def analyze_screenshot(
    body: ScreenshotAnalysisRequest,
    tenant: TraderDep,
    session: SessionDep,
    settings: SettingsDep,
) -> ScreenshotAnalysisContract:
    result = _service(session, settings, tenant.organization_id).screenshot_contract(
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post(
    "/voice/transcribe",
    response_model=VoiceIoContract,
    summary="Voice input contract",
)
async def transcribe_voice(
    body: VoiceInputRequest,
    tenant: TraderDep,
    session: SessionDep,
    settings: SettingsDep,
) -> VoiceIoContract:
    result = _service(session, settings, tenant.organization_id).voice_input_contract(
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post("/voice/speak", response_model=VoiceIoContract, summary="Voice output contract")
async def speak_voice(
    body: VoiceOutputRequest,
    tenant: TraderDep,
    session: SessionDep,
    settings: SettingsDep,
) -> VoiceIoContract:
    result = _service(session, settings, tenant.organization_id).voice_output_contract(
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result
