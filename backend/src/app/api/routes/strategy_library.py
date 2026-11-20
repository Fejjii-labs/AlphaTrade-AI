"""Strategy library CRUD API (Slice 33)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Query

from app.core.dependencies import (
    BacktestServiceDep,
    PaperEligibilityServiceDep,
    PaperValidationServiceDep,
    SessionDep,
    SettingsDep,
    StrategyLibraryServiceDep,
    StrategyTestabilityServiceDep,
    StructuredRulesServiceDep,
    StructureFromTextServiceDep,
)
from app.schemas.backtest import BacktestRun, BacktestRunCreate, PaginatedBacktestRuns
from app.schemas.paper_eligibility import PaperEligibilityReport
from app.schemas.paper_validation import (
    PaperValidationRun,
    PaperValidationRunStart,
    PaperValidationSummary,
)
from app.schemas.strategy_library import (
    PaginatedUserStrategies,
    PaginatedUserStrategyVersions,
    StrategyLibraryCreate,
    UserStrategy,
    UserStrategyCreate,
    UserStrategyUpdate,
    UserStrategyVersion,
    UserStrategyVersionCreate,
)
from app.schemas.strategy_lifecycle import (
    CompileOutcome,
    StrategyLifecycleEventRecord,
    StrategyVersionApproveRequest,
)
from app.schemas.strategy_testability import StrategyTestability
from app.schemas.structured_rules import (
    StructuredRules,
    StructuredRulesPatch,
    StructuredRulesValidation,
    StructureFromTextRequest,
    StructureFromTextResponse,
)
from app.security.rbac import TraderDep

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.post("", response_model=UserStrategy, summary="Create user strategy")
def create_strategy(
    body: StrategyLibraryCreate,
    tenant: TraderDep,
    service: StrategyLibraryServiceDep,
    session: SessionDep,
) -> UserStrategy:
    payload = UserStrategyCreate(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        name=body.name,
        setup_type=body.setup_type,
        card=body.card,
        notes=body.notes,
        pattern_spec=body.pattern_spec,
    )
    result = service.create(payload)
    session.commit()
    return result


@router.get("", response_model=PaginatedUserStrategies, summary="List user strategies")
def list_strategies(
    tenant: TraderDep,
    service: StrategyLibraryServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedUserStrategies:
    items, total = service.list_strategies(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        limit=limit,
        offset=offset,
    )
    return PaginatedUserStrategies(items=items, total=total, limit=limit, offset=offset)


@router.get("/{strategy_id}", response_model=UserStrategy, summary="Get user strategy")
def get_strategy(
    strategy_id: uuid.UUID,
    tenant: TraderDep,
    service: StrategyLibraryServiceDep,
) -> UserStrategy:
    return service.get(strategy_id, organization_id=tenant.organization_id, user_id=tenant.user_id)


@router.patch("/{strategy_id}", response_model=UserStrategy, summary="Update user strategy")
def update_strategy(
    strategy_id: uuid.UUID,
    body: UserStrategyUpdate,
    tenant: TraderDep,
    service: StrategyLibraryServiceDep,
    session: SessionDep,
) -> UserStrategy:
    result = service.update(
        strategy_id,
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post(
    "/{strategy_id}/versions",
    response_model=UserStrategyVersion,
    summary="Create strategy version",
)
def create_strategy_version(
    strategy_id: uuid.UUID,
    body: UserStrategyVersionCreate,
    tenant: TraderDep,
    service: StrategyLibraryServiceDep,
    session: SessionDep,
) -> UserStrategyVersion:
    result = service.create_version(
        strategy_id,
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.get(
    "/{strategy_id}/versions",
    response_model=PaginatedUserStrategyVersions,
    summary="List strategy versions",
)
def list_strategy_versions(
    strategy_id: uuid.UUID,
    tenant: TraderDep,
    service: StrategyLibraryServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedUserStrategyVersions:
    items, total = service.list_versions(
        strategy_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        limit=limit,
        offset=offset,
    )
    return PaginatedUserStrategyVersions(items=items, total=total, limit=limit, offset=offset)


@router.post(
    "/{strategy_id}/backtests",
    response_model=BacktestRun,
    summary="Queue or run a strategy backtest (historical simulation)",
)
def create_backtest(
    strategy_id: uuid.UUID,
    body: BacktestRunCreate,
    tenant: TraderDep,
    service: BacktestServiceDep,
    session: SessionDep,
    settings: SettingsDep,
    background_tasks: BackgroundTasks,
) -> BacktestRun:
    from app.api.routes.backtests import enqueue_backtest_if_needed

    result = service.create(
        strategy_id,
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    enqueue_backtest_if_needed(
        result=result,
        session=session,
        settings=settings,
        background_tasks=background_tasks,
    )
    return result


@router.get(
    "/{strategy_id}/backtests",
    response_model=PaginatedBacktestRuns,
    summary="List backtest runs for strategy",
)
def list_backtests(
    strategy_id: uuid.UUID,
    tenant: TraderDep,
    service: BacktestServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedBacktestRuns:
    items, total = service.list_for_strategy(
        strategy_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        limit=limit,
        offset=offset,
    )
    return PaginatedBacktestRuns(items=items, total=total, limit=limit, offset=offset)


@router.post(
    "/{strategy_id}/paper-validation/start",
    response_model=PaperValidationRun,
    summary="Start paper validation tracking",
)
def start_paper_validation(
    strategy_id: uuid.UUID,
    tenant: TraderDep,
    service: PaperValidationServiceDep,
    session: SessionDep,
    body: PaperValidationRunStart | None = None,
) -> PaperValidationRun:
    result = service.start(
        strategy_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        payload=body,
    )
    session.commit()
    return result


@router.get(
    "/{strategy_id}/paper-eligibility",
    response_model=PaperEligibilityReport,
    summary="Paper eligibility gates and blockers (paper only)",
)
def get_paper_eligibility(
    strategy_id: uuid.UUID,
    tenant: TraderDep,
    service: PaperEligibilityServiceDep,
    session: SessionDep,
) -> PaperEligibilityReport:
    result = service.evaluate(
        strategy_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.get(
    "/{strategy_id}/paper-validation",
    response_model=PaperValidationSummary,
    summary="List paper validation runs",
)
def list_paper_validation(
    strategy_id: uuid.UUID,
    tenant: TraderDep,
    service: PaperValidationServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaperValidationSummary:
    return service.list_for_strategy(
        strategy_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{strategy_id}/paper-validation/{run_id}",
    response_model=PaperValidationRun,
    summary="Get paper validation run with metrics",
)
def get_paper_validation_run(
    strategy_id: uuid.UUID,
    run_id: uuid.UUID,
    tenant: TraderDep,
    service: PaperValidationServiceDep,
) -> PaperValidationRun:
    return service.get_run(
        strategy_id,
        run_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )


@router.get(
    "/{strategy_id}/testability",
    response_model=StrategyTestability,
    summary="Strategy testability score",
)
def get_strategy_testability(
    strategy_id: uuid.UUID,
    tenant: TraderDep,
    service: StrategyTestabilityServiceDep,
) -> StrategyTestability:
    return service.score(
        strategy_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )


@router.patch(
    "/{strategy_id}/structured-rules",
    response_model=StructuredRules,
    summary="Update structured rule blocks",
)
def patch_structured_rules(
    strategy_id: uuid.UUID,
    body: StructuredRulesPatch,
    tenant: TraderDep,
    service: StructuredRulesServiceDep,
    session: SessionDep,
) -> StructuredRules:
    result = service.patch(
        strategy_id,
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post(
    "/{strategy_id}/structured-rules/validate",
    response_model=StructuredRulesValidation,
    summary="Validate structured rules",
)
def validate_structured_rules(
    strategy_id: uuid.UUID,
    body: StructuredRules,
    tenant: TraderDep,
    service: StructuredRulesServiceDep,
) -> StructuredRulesValidation:
    _ = strategy_id
    _ = tenant
    return service.validate(body)


@router.post(
    "/{strategy_id}/structure-from-text",
    response_model=StructureFromTextResponse,
    summary="Draft structured rules from plain English",
)
def structure_from_text(
    strategy_id: uuid.UUID,
    body: StructureFromTextRequest,
    tenant: TraderDep,
    service: StructureFromTextServiceDep,
) -> StructureFromTextResponse:
    _ = strategy_id
    _ = tenant
    return service.draft(body)


@router.post(
    "/{strategy_id}/versions/{version_id}/compile",
    response_model=CompileOutcome,
    summary="Explicit compile/review of a confirmed strategy version",
)
def compile_strategy_version(
    strategy_id: uuid.UUID,
    version_id: uuid.UUID,
    tenant: TraderDep,
    session: SessionDep,
) -> CompileOutcome:
    from app.services.compiled_setup_service import CompiledSetupService

    result = CompiledSetupService(session).compile_version(
        version_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        strategy_id=strategy_id,
    )
    session.commit()
    return result


@router.post(
    "/{strategy_id}/versions/{version_id}/approve",
    response_model=StrategyLifecycleEventRecord,
    summary="Explicit approval of an executable compiled strategy version",
)
def approve_strategy_version(
    strategy_id: uuid.UUID,
    version_id: uuid.UUID,
    body: StrategyVersionApproveRequest,
    tenant: TraderDep,
    session: SessionDep,
) -> StrategyLifecycleEventRecord:
    from app.services.compiled_setup_service import CompiledSetupService

    result = CompiledSetupService(session).approve_version(
        version_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        confirm_message=body.confirm,
        strategy_id=strategy_id,
    )
    session.commit()
    return result
