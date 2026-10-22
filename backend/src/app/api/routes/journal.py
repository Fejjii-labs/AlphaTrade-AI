"""Trade journal API."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Form, Query, Response, UploadFile

from app.core.auth import TenantDep
from app.core.dependencies import (
    HumanVsSystemServiceDep,
    JournalAttachmentServiceDep,
    JournalExcursionReplayServiceDep,
    JournalImportServiceDep,
    JournalServiceDep,
    JournalStatisticsServiceDep,
    JournalTradeServiceDep,
    LessonCandidateServiceDep,
    SessionDep,
    SetupEvidenceServiceDep,
)
from app.core.errors import NotFoundError
from app.schemas.backtest import (
    JournalComparisonFilters,
    JournalComparisonResponse,
    SetupEvidenceResponse,
)
from app.schemas.common import (
    JournalEntryMethod,
    JournalTradeSource,
    JournalTradeStatus,
    MarketRegime,
)
from app.schemas.human_vs_system import DisciplineAnalysis
from app.schemas.journal import (
    JournalEntry,
    JournalEntryCreate,
    JournalEntryPrefill,
    JournalEntryUpdate,
    PaginatedJournalEntries,
)
from app.schemas.journal_attachments import (
    JournalTradeAttachmentList,
    JournalTradeAttachmentRead,
)
from app.schemas.journal_excursion_replay import (
    JournalExcursionBatchReplayRequest,
    JournalExcursionBatchReplayResult,
    JournalExcursionReplayRequest,
    JournalExcursionReplayResult,
)
from app.schemas.journal_import import (
    JournalImportBatchRead,
    JournalImportRequest,
    JournalImportResult,
    PaginatedJournalImportBatches,
)
from app.schemas.journal_statistics import (
    ExecutionActor,
    JournalStatsFilters,
    JournalStatsGroupBy,
    JournalStatsResponse,
    TradeRuleCompliance,
)
from app.schemas.journal_trades import (
    JournalTradeAnnotation,
    JournalTradeCreate,
    JournalTradeDetail,
    JournalTradeEvidenceCreate,
    JournalTradeEvidenceRead,
    JournalTradeObservationCreate,
    JournalTradeObservationRead,
    JournalTradeRead,
    JournalTradeRuleCheckCreate,
    JournalTradeRuleCheckRead,
    JournalTradeUpdate,
    PaginatedJournalTrades,
)
from app.schemas.lesson import LessonCandidate
from app.security.rbac import ReaderDep, TraderDep
from app.services.manual_demo_history import ManualDemoHistoryService

router = APIRouter(prefix="/journal", tags=["journal"])

_ATTACHMENT_NOT_FOUND_MSG = "Attachment not found."
_JOURNAL_ENTRY_NOT_FOUND_MSG = "Journal entry not found."


def _require_journal_trade_owner(trade: JournalTradeRead, tenant: TenantDep) -> None:
    if trade.user_id != tenant.user_id:
        raise NotFoundError("Journal trade not found.")


def _load_owned_journal_trade(
    journal_trade_id: uuid.UUID,
    tenant: TenantDep,
    service: JournalTradeServiceDep,
) -> JournalTradeRead:
    trade = service.get(journal_trade_id, organization_id=tenant.organization_id)
    _require_journal_trade_owner(trade, tenant)
    return trade


def _load_owned_journal_entry(
    journal_entry_id: uuid.UUID,
    tenant: TenantDep,
    journal_service: JournalServiceDep,
) -> JournalEntry:
    entry = journal_service.get(journal_entry_id)
    if entry.organization_id != tenant.organization_id or entry.user_id != tenant.user_id:
        raise NotFoundError(_JOURNAL_ENTRY_NOT_FOUND_MSG)
    return entry


def _load_owned_attachment(
    attachment_id: uuid.UUID,
    tenant: TenantDep,
    attachment_service: JournalAttachmentServiceDep,
    trade_service: JournalTradeServiceDep,
) -> JournalTradeAttachmentRead:
    try:
        meta = attachment_service.get_attachment(
            attachment_id, organization_id=tenant.organization_id
        )
    except NotFoundError:
        raise NotFoundError(_ATTACHMENT_NOT_FOUND_MSG) from None
    try:
        _load_owned_journal_trade(meta.journal_trade_id, tenant, trade_service)
    except NotFoundError:
        raise NotFoundError(_ATTACHMENT_NOT_FOUND_MSG) from None
    return meta


@router.post("/entries", response_model=JournalEntry, summary="Create journal entry")
async def create_journal_entry(
    body: JournalEntryCreate,
    tenant: TraderDep,
    journal_service: JournalServiceDep,
    session: SessionDep,
) -> JournalEntry:
    payload = body.model_copy(
        update={"organization_id": tenant.organization_id, "user_id": tenant.user_id}
    )
    result = journal_service.create(payload)
    session.commit()
    return result


@router.get(
    "/prefill",
    response_model=JournalEntryPrefill,
    summary="Prefill journal from proposal or position",
)
async def prefill_journal_entry(
    tenant: TenantDep,
    journal_service: JournalServiceDep,
    linked_proposal_id: uuid.UUID | None = Query(default=None),
    linked_position_id: uuid.UUID | None = Query(default=None),
) -> JournalEntryPrefill:
    if linked_proposal_id is None and linked_position_id is None:
        from app.core.errors import ValidationAppError

        raise ValidationAppError("Provide linked_proposal_id or linked_position_id.")
    return journal_service.prefill(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        linked_proposal_id=linked_proposal_id,
        linked_position_id=linked_position_id,
    )


@router.get("/entries", response_model=PaginatedJournalEntries, summary="List journal entries")
async def list_journal_entries(
    tenant: TenantDep,
    journal_service: JournalServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedJournalEntries:
    items, total = journal_service.list_entries(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        limit=limit,
        offset=offset,
    )
    return PaginatedJournalEntries(items=items, total=total, limit=limit, offset=offset)


@router.get("/entries/{journal_entry_id}", response_model=JournalEntry)
async def get_journal_entry(
    journal_entry_id: uuid.UUID,
    tenant: TenantDep,
    journal_service: JournalServiceDep,
) -> JournalEntry:
    return _load_owned_journal_entry(journal_entry_id, tenant, journal_service)


@router.patch("/entries/{journal_entry_id}", response_model=JournalEntry)
async def update_journal_entry(
    journal_entry_id: uuid.UUID,
    body: JournalEntryUpdate,
    tenant: TraderDep,
    journal_service: JournalServiceDep,
    session: SessionDep,
) -> JournalEntry:
    _load_owned_journal_entry(journal_entry_id, tenant, journal_service)
    result = journal_service.update(journal_entry_id, body)
    session.commit()
    return result


@router.delete("/entries/{journal_entry_id}", status_code=204)
async def delete_journal_entry(
    journal_entry_id: uuid.UUID,
    tenant: TraderDep,
    journal_service: JournalServiceDep,
    session: SessionDep,
) -> None:
    _load_owned_journal_entry(journal_entry_id, tenant, journal_service)
    journal_service.delete(journal_entry_id)
    session.commit()


@router.get(
    "/entries/{journal_entry_id}/discipline-analysis",
    response_model=DisciplineAnalysis,
    summary="Discipline analysis for journal entry",
)
async def journal_discipline_analysis(
    journal_entry_id: uuid.UUID,
    tenant: TraderDep,
    journal_service: JournalServiceDep,
    hvs_service: HumanVsSystemServiceDep,
) -> DisciplineAnalysis:
    _load_owned_journal_entry(journal_entry_id, tenant, journal_service)
    return hvs_service.analyze_discipline(
        journal_entry_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )


# --------------------------------------------------------------------------- #
# Canonical journal trades (AT-030 — record-only, no execution authority)
# --------------------------------------------------------------------------- #


@router.post(
    "/trades",
    response_model=JournalTradeRead,
    status_code=201,
    summary="Create canonical journal trade",
)
async def create_journal_trade(
    body: JournalTradeCreate,
    tenant: TraderDep,
    service: JournalTradeServiceDep,
    session: SessionDep,
) -> JournalTradeRead:
    result = service.create(
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post(
    "/trades/from-position/{position_id}",
    response_model=JournalTradeRead,
    status_code=201,
    summary="Create journal trade prefilled from a paper position",
)
async def create_journal_trade_from_position(
    position_id: uuid.UUID,
    tenant: TraderDep,
    service: JournalTradeServiceDep,
    session: SessionDep,
) -> JournalTradeRead:
    result = service.create_from_position(
        position_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post(
    "/trades/from-paper-trade/{paper_trade_id}",
    response_model=JournalTradeRead,
    status_code=201,
    summary="Create journal trade prefilled from a paper-validation trade",
)
async def create_journal_trade_from_paper_trade(
    paper_trade_id: uuid.UUID,
    tenant: TraderDep,
    service: JournalTradeServiceDep,
    session: SessionDep,
) -> JournalTradeRead:
    result = service.create_from_paper_trade(
        paper_trade_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.get(
    "/trades",
    response_model=PaginatedJournalTrades,
    summary="List canonical journal trades",
)
async def list_journal_trades(
    tenant: ReaderDep,
    service: JournalTradeServiceDep,
    source: JournalTradeSource | None = Query(default=None),
    status: JournalTradeStatus | None = Query(default=None),
    symbol: str | None = Query(default=None, max_length=30),
    user_strategy_id: uuid.UUID | None = Query(default=None),
    setup_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedJournalTrades:
    items, total = service.list_trades(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        source=source,
        status=status,
        symbol=symbol,
        user_strategy_id=user_strategy_id,
        setup_id=setup_id,
        limit=limit,
        offset=offset,
    )
    return PaginatedJournalTrades(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/statistics",
    response_model=JournalStatsResponse,
    summary="Grouped statistics over canonical journal trades (AT-031)",
)
async def journal_trade_statistics(
    tenant: ReaderDep,
    service: JournalStatisticsServiceDep,
    group_by: JournalStatsGroupBy = Query(default=JournalStatsGroupBy.OVERALL),
    source: JournalTradeSource | None = Query(default=None),
    entry_method: JournalEntryMethod | None = Query(default=None),
    symbol: str | None = Query(default=None, max_length=30),
    timeframe: str | None = Query(default=None, max_length=8),
    market_regime: MarketRegime | None = Query(default=None),
    setup_id: uuid.UUID | None = Query(default=None),
    user_strategy_id: uuid.UUID | None = Query(default=None),
    strategy_version_id: uuid.UUID | None = Query(default=None),
    rule_compliance: TradeRuleCompliance | None = Query(default=None),
    execution_actor: ExecutionActor | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> JournalStatsResponse:
    """Deterministic, record-only aggregates over the caller's closed journal trades."""
    filters = JournalStatsFilters(
        source=source,
        entry_method=entry_method,
        symbol=symbol,
        timeframe=timeframe,
        market_regime=market_regime,
        setup_id=setup_id,
        user_strategy_id=user_strategy_id,
        strategy_version_id=strategy_version_id,
        rule_compliance=rule_compliance,
        execution_actor=execution_actor,
        date_from=date_from,
        date_to=date_to,
    )
    return service.compute(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        group_by=group_by,
        filters=filters,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/comparison",
    response_model=JournalComparisonResponse,
    summary=(
        "Compare human / paper-system / backtest cohorts with decision quality (AT-034/AT-036)"
    ),
)
async def journal_comparison(
    tenant: ReaderDep,
    service: JournalStatisticsServiceDep,
    strategy_id: uuid.UUID | None = Query(default=None),
    strategy_version_id: uuid.UUID | None = Query(default=None),
    setup_id: uuid.UUID | None = Query(default=None),
    symbol: str | None = Query(default=None, max_length=30),
    timeframe: str | None = Query(default=None, max_length=8),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    market_regime: MarketRegime | None = Query(default=None),
    entry_method: JournalEntryMethod | None = Query(default=None),
    source: JournalTradeSource | None = Query(default=None),
    breakdown_limit: int = Query(default=10, ge=1, le=50),
) -> JournalComparisonResponse:
    filters = JournalComparisonFilters(
        strategy_id=strategy_id,
        strategy_version_id=strategy_version_id,
        setup_id=setup_id,
        symbol=symbol,
        timeframe=timeframe,
        date_from=date_from,
        date_to=date_to,
        market_regime=market_regime,
        entry_method=entry_method,
        source=source,
    )
    return service.compare_cohorts(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        filters=filters,
        breakdown_limit=breakdown_limit,
    )


@router.get(
    "/setup-evidence",
    response_model=SetupEvidenceResponse,
    summary="Advisory setup evidence tiers from backtest OOS + journal confirmation (AT-034)",
)
async def journal_setup_evidence(
    tenant: ReaderDep,
    service: SetupEvidenceServiceDep,
    strategy_id: uuid.UUID | None = Query(default=None),
    strategy_version_id: uuid.UUID | None = Query(default=None),
    setup_id: uuid.UUID | None = Query(default=None),
) -> SetupEvidenceResponse:
    return service.evaluate(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        strategy_id=strategy_id,
        strategy_version_id=strategy_version_id,
        setup_id=setup_id,
    )


@router.post(
    "/trades/replay-excursions",
    response_model=JournalExcursionBatchReplayResult,
    summary="Batch replay MFE/MAE from HistoricalCandle (AT-032)",
)
async def batch_replay_journal_trade_excursions(
    body: JournalExcursionBatchReplayRequest,
    tenant: TraderDep,
    service: JournalExcursionReplayServiceDep,
    session: SessionDep,
) -> JournalExcursionBatchReplayResult:
    """Deterministic, record-only excursion replay over eligible closed trades.

    Reads stored HistoricalCandle only — no live market I/O. Manual/system
    excursions are skipped unless ``overwrite_policy=force``.
    """
    result = service.replay_batch(
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post(
    "/trades/{journal_trade_id}/replay-excursions",
    response_model=JournalExcursionReplayResult,
    summary="Replay MFE/MAE from HistoricalCandle for one trade (AT-032)",
)
async def replay_journal_trade_excursions(
    journal_trade_id: uuid.UUID,
    body: JournalExcursionReplayRequest,
    tenant: TraderDep,
    service: JournalExcursionReplayServiceDep,
    session: SessionDep,
) -> JournalExcursionReplayResult:
    """Compute deterministic MFE/MAE / available profit from stored candles."""
    result = service.replay_trade(
        journal_trade_id,
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


# --------------------------------------------------------------------------- #
# Bulk import & attachments (AT-033 — record-only, no execution authority)
# --------------------------------------------------------------------------- #


@router.post(
    "/trades/import",
    response_model=JournalImportResult,
    summary="Bulk import journal trades with dedup, dry-run, and reconciliation (AT-033)",
)
async def import_journal_trades(
    body: JournalImportRequest,
    tenant: TraderDep,
    service: JournalImportServiceDep,
    session: SessionDep,
) -> JournalImportResult:
    """Validate rows individually; dedup by (org, external_ref) or fingerprint.

    ``mode=dry_run`` previews without persisting. ``mode=commit`` is
    all-or-nothing: invalid rows block the commit and nothing is written;
    duplicate rows are skipped idempotently, so re-running is always safe.
    """
    result = service.import_trades(
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.get(
    "/imports",
    response_model=PaginatedJournalImportBatches,
    summary="List committed journal import batches (AT-033)",
)
async def list_journal_import_batches(
    tenant: ReaderDep,
    service: JournalImportServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedJournalImportBatches:
    return service.list_batches(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/imports/{batch_id}",
    response_model=JournalImportBatchRead,
    summary="Get one journal import batch with its row report (AT-033)",
)
async def get_journal_import_batch(
    batch_id: uuid.UUID,
    tenant: ReaderDep,
    service: JournalImportServiceDep,
) -> JournalImportBatchRead:
    return service.get_batch(
        batch_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )


@router.post(
    "/trades/{journal_trade_id}/attachments",
    response_model=JournalTradeAttachmentRead,
    status_code=201,
    summary="Upload a binary attachment for a journal trade (AT-033)",
)
async def upload_journal_trade_attachment(
    journal_trade_id: uuid.UUID,
    file: UploadFile,
    tenant: TraderDep,
    trade_service: JournalTradeServiceDep,
    service: JournalAttachmentServiceDep,
    session: SessionDep,
    caption: str | None = Form(default=None, max_length=4000),
) -> JournalTradeAttachmentRead:
    """Size, MIME, and per-trade quota limits are enforced fail-closed."""
    _load_owned_journal_trade(journal_trade_id, tenant, trade_service)
    content = await file.read()
    result = service.add_attachment(
        journal_trade_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        filename=file.filename or "",
        content_type=file.content_type,
        content=content,
        caption=caption,
    )
    session.commit()
    return result


@router.get(
    "/trades/{journal_trade_id}/attachments",
    response_model=JournalTradeAttachmentList,
    summary="List attachments for a journal trade (AT-033)",
)
async def list_journal_trade_attachments(
    journal_trade_id: uuid.UUID,
    tenant: ReaderDep,
    trade_service: JournalTradeServiceDep,
    service: JournalAttachmentServiceDep,
) -> JournalTradeAttachmentList:
    _load_owned_journal_trade(journal_trade_id, tenant, trade_service)
    return service.list_attachments(
        journal_trade_id,
        organization_id=tenant.organization_id,
    )


@router.get(
    "/attachments/{attachment_id}/content",
    summary="Download attachment content (AT-033)",
    response_class=Response,
)
async def download_journal_trade_attachment(
    attachment_id: uuid.UUID,
    tenant: ReaderDep,
    trade_service: JournalTradeServiceDep,
    service: JournalAttachmentServiceDep,
) -> Response:
    meta = _load_owned_attachment(attachment_id, tenant, service, trade_service)
    content = service.read_content(attachment_id, organization_id=tenant.organization_id)
    safe_name = meta.filename.replace('"', "")
    return Response(
        content=content,
        media_type=meta.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete(
    "/attachments/{attachment_id}",
    status_code=204,
    summary="Delete an attachment (AT-033)",
)
async def delete_journal_trade_attachment(
    attachment_id: uuid.UUID,
    tenant: TraderDep,
    trade_service: JournalTradeServiceDep,
    service: JournalAttachmentServiceDep,
    session: SessionDep,
) -> None:
    _load_owned_attachment(attachment_id, tenant, service, trade_service)
    service.delete_attachment(
        attachment_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()


@router.get(
    "/trades/{journal_trade_id}",
    response_model=JournalTradeDetail,
    summary="Get journal trade with evidence, rule checks, and observations",
)
async def get_journal_trade(
    journal_trade_id: uuid.UUID,
    tenant: ReaderDep,
    service: JournalTradeServiceDep,
    session: SessionDep,
) -> JournalTradeDetail:
    detail = service.get_detail(journal_trade_id, organization_id=tenant.organization_id)
    _require_journal_trade_owner(detail.trade, tenant)
    if detail.trade.source == JournalTradeSource.MANUAL_DEMO_TEST:
        ref = detail.trade.external_ref or ""
        if ref.startswith("blofin-demo-history:"):
            return detail
        if detail.trade.execution_lifecycle_id is None:
            raise NotFoundError("Journal trade not found.")
        attempt = ManualDemoHistoryService(session).get(tenant, detail.trade.execution_lifecycle_id)
        if attempt.evidence.journal_trade_id != journal_trade_id:
            raise NotFoundError("Journal trade lineage mismatch.")
        detail.manual_demo = attempt
    return detail


@router.patch(
    "/trades/{journal_trade_id}",
    response_model=JournalTradeRead,
    summary="Update canonical journal trade",
)
async def update_journal_trade(
    journal_trade_id: uuid.UUID,
    body: JournalTradeUpdate,
    tenant: TraderDep,
    service: JournalTradeServiceDep,
    session: SessionDep,
) -> JournalTradeRead:
    trade = service.get(journal_trade_id, organization_id=tenant.organization_id)
    _require_journal_trade_owner(trade, tenant)
    result = service.update(
        journal_trade_id,
        body,
        organization_id=tenant.organization_id,
    )
    session.commit()
    return result


@router.delete(
    "/trades/{journal_trade_id}",
    status_code=204,
    summary="Delete canonical journal trade",
)
async def delete_journal_trade(
    journal_trade_id: uuid.UUID,
    tenant: TraderDep,
    service: JournalTradeServiceDep,
    session: SessionDep,
) -> None:
    trade = service.get(journal_trade_id, organization_id=tenant.organization_id)
    _require_journal_trade_owner(trade, tenant)
    service.delete(journal_trade_id, organization_id=tenant.organization_id)
    session.commit()


@router.post(
    "/trades/{journal_trade_id}/evidence",
    response_model=JournalTradeEvidenceRead,
    status_code=201,
    summary="Attach evidence to a journal trade",
)
async def add_journal_trade_evidence(
    journal_trade_id: uuid.UUID,
    body: JournalTradeEvidenceCreate,
    tenant: TraderDep,
    service: JournalTradeServiceDep,
    session: SessionDep,
) -> JournalTradeEvidenceRead:
    _load_owned_journal_trade(journal_trade_id, tenant, service)
    result = service.add_evidence(
        journal_trade_id,
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post(
    "/trades/{journal_trade_id}/rule-checks",
    response_model=JournalTradeRuleCheckRead,
    status_code=201,
    summary="Record a rule-compliance check for a journal trade",
)
async def add_journal_trade_rule_check(
    journal_trade_id: uuid.UUID,
    body: JournalTradeRuleCheckCreate,
    tenant: TraderDep,
    service: JournalTradeServiceDep,
    session: SessionDep,
) -> JournalTradeRuleCheckRead:
    _load_owned_journal_trade(journal_trade_id, tenant, service)
    result = service.add_rule_check(
        journal_trade_id,
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.patch(
    "/trades/{journal_trade_id}/annotation",
    response_model=JournalTradeRead,
    summary="Tag a mirrored BloFin demo trade with strategy, grade, and notes",
)
async def annotate_journal_trade(
    journal_trade_id: uuid.UUID,
    body: JournalTradeAnnotation,
    tenant: TraderDep,
    service: JournalTradeServiceDep,
    session: SessionDep,
) -> JournalTradeRead:
    trade = service.get(journal_trade_id, organization_id=tenant.organization_id)
    _require_journal_trade_owner(trade, tenant)
    result = service.annotate(
        journal_trade_id,
        body,
        organization_id=tenant.organization_id,
    )
    session.commit()
    return result


@router.post(
    "/trades/{journal_trade_id}/observations",
    response_model=JournalTradeObservationRead,
    status_code=201,
    summary="Record a behavioral observation for a journal trade",
)
async def add_journal_trade_observation(
    journal_trade_id: uuid.UUID,
    body: JournalTradeObservationCreate,
    tenant: TraderDep,
    service: JournalTradeServiceDep,
    session: SessionDep,
) -> JournalTradeObservationRead:
    _load_owned_journal_trade(journal_trade_id, tenant, service)
    result = service.add_observation(
        journal_trade_id,
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post(
    "/entries/{journal_entry_id}/discipline-analysis/candidates",
    response_model=LessonCandidate,
    summary="Create lesson candidate from discipline analysis",
)
async def create_discipline_lesson_candidate(
    journal_entry_id: uuid.UUID,
    tenant: TraderDep,
    journal_service: JournalServiceDep,
    hvs_service: HumanVsSystemServiceDep,
    lesson_service: LessonCandidateServiceDep,
    session: SessionDep,
    category: str = Query(..., description="Suggestion category, e.g. early_exit"),
) -> LessonCandidate:
    _load_owned_journal_entry(journal_entry_id, tenant, journal_service)
    candidate_id = hvs_service.create_discipline_candidate(
        journal_entry_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        category=category,
    )
    session.commit()
    return lesson_service.get(
        candidate_id, organization_id=tenant.organization_id, user_id=tenant.user_id
    )
