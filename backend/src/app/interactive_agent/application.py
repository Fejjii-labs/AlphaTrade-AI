"""Confirmed applications through existing domain authorities and the paper gateway."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError, QuotaExceededError
from app.db.models import Conversation
from app.interactive_agent.action_registry import Tool
from app.interactive_agent.actions import (
    PaperExecutionInput,
    PaperTradeInput,
    StrategyInput,
    WatcherChangeInput,
)
from app.interactive_agent.contracts import StructuredActionProposal
from app.repositories.conversations import StrategyConversationProposalRepository
from app.repositories.watcher_watchlist import WatcherWatchlistRepository
from app.schemas.agent_paper import AgentPaperConfirmation, AgentPaperResult
from app.schemas.backtest import BacktestRunCreate
from app.schemas.common import ConversationMessageRole, StrategyProposalStatus, StrictModel
from app.schemas.pretrade import PreTradeAnalyzeRequest
from app.schemas.rag import IngestDocumentRequest
from app.schemas.watcher_assignments import WatcherAssignmentReplace
from app.schemas.watcher_watchlist import WatcherWatchlistReplace
from app.services.agent_paper_execution import AgentPaperExecutionService
from app.services.audit_service import AuditService
from app.services.backtest_service import BacktestService
from app.services.canonical_serialization import canonical_sha256
from app.services.conversation_service import ConversationService
from app.services.quota_service import QuotaService
from app.services.rag_service import build_rag_service
from app.services.strategy_proposal_service import StrategyProposalService
from app.services.usage_service import UsageService


def apply_confirmed_action(
    session: Session,
    *,
    proposal: StructuredActionProposal,
    conversation: Conversation,
    tool: Tool,
    inputs: StrictModel,
    settings: Settings,
    paper_execution: AgentPaperExecutionService | None = None,
) -> tuple[UUID | None, dict[str, Any]]:
    """The caller holds the transcript lock and owns the database commit.

    Receipts live outside the immutable proposal payload. No receipt alters the
    reviewed content hash or grants Candidate, risk, sizing or order authority.
    """
    if proposal.payload.get("missing_fields"):
        return None, {
            "reason": "missing_inputs",
            "missing_fields": proposal.payload["missing_fields"],
        }
    if isinstance(inputs, PaperExecutionInput):
        if paper_execution is None:
            raise ConflictError("Canonical paper execution authority is unavailable.")
        presented = AgentPaperResult.model_validate(proposal.payload.get("paper_execution"))
        result = paper_execution.confirm_presented(
            presented,
            AgentPaperConfirmation(
                revision_id=presented.plan.revision_id,
                plan_content_hash=presented.plan.content_hash,
            ),
            organization_id=conversation.organization_id,
            user_id=conversation.user_id,
            conversation_id=conversation.id,
        )
        return result.paper_action_id if result.stage == "executed" else None, {
            "record_type": "canonical_paper_execution",
            **result.model_dump(mode="json"),
        }
    if proposal.payload.get("ingest_request"):
        audit = AuditService(session, strict_mode=settings.observability_strict_mode)
        quota = QuotaService(session, audit_service=audit).check_feature(
            conversation.organization_id,
            "rag_ingest",
            request_id=str(proposal.proposal_id),
            user_id=conversation.user_id,
        )
        if quota.hard_blocked:
            raise QuotaExceededError(quota.message)
        ingest_request = IngestDocumentRequest.model_validate(proposal.payload["ingest_request"])
        if (
            ingest_request.organization_id != conversation.organization_id
            or ingest_request.user_id != conversation.user_id
        ):
            raise ConflictError("Knowledge ingestion scope does not match the conversation.")
        rag = build_rag_service(
            settings,
            session,
            audit_service=audit,
            usage_service=UsageService(session, strict_mode=settings.observability_strict_mode),
        )
        ingested = rag.ingest(ingest_request, commit=False)
        return ingested.document_id, {
            "record_type": "documents",
            **ingested.model_dump(mode="json"),
            "provenance": proposal.payload.get("provenance"),
            "evidence_links_persisted": proposal.payload.get("evidence_document_ids", []),
        }
    if isinstance(inputs, WatcherAssignmentReplace):
        from app.services.watcher_assignment_service import WatcherAssignmentService

        assignment_replacement = WatcherAssignmentReplace.model_validate(
            proposal.payload["assignment_replace_request"]
        )
        if assignment_replacement != inputs:
            raise ConflictError("Monitoring proposal snapshot does not match reviewed input.")
        assignment_result = WatcherAssignmentService(session).replace(
            organization_id=conversation.organization_id,
            user_id=conversation.user_id,
            body=assignment_replacement,
        )
        return conversation.organization_id, {
            "record_type": "watcher_assignments",
            "configuration_revision": assignment_result.revision,
            "monitoring_only": True,
            "configuration_changed": True,
            "trading_activated": False,
            "execution_attempted": False,
        }
    if isinstance(inputs, WatcherChangeInput):
        if not proposal.payload.get("replace_request"):
            return None, {"reason": "unsupported_watcher_request"}
        replacement = WatcherWatchlistReplace.model_validate(proposal.payload["replace_request"])
        config = WatcherWatchlistRepository(session).replace(
            conversation.organization_id,
            [(slot.symbol, slot.enabled) for slot in replacement.slots],
            expected_revision=replacement.revision,
        )
        return conversation.organization_id, {
            "record_type": "watcher_watchlists",
            "organization_id": str(conversation.organization_id),
            "configuration_revision": config.revision,
            "configuration_changed": True,
        }
    if tool.name in {"strategy.create", "strategy.refinement"}:
        linked = proposal.linked_strategy_proposal_id
        if linked is None:
            return None, {"reason": "missing_strategy_draft"}
        row = StrategyConversationProposalRepository(session).get_scoped_for_update(
            linked,
            organization_id=conversation.organization_id,
            user_id=conversation.user_id,
            conversation_id=conversation.id,
        )
        if row is not None:
            session.refresh(row)
        current = StrategyProposalService(session).get(
            linked,
            organization_id=conversation.organization_id,
            user_id=conversation.user_id,
            conversation_id=conversation.id,
        )
        if (
            row is None
            or row.status is not StrategyProposalStatus.DRAFT
            or canonical_sha256(
                current.model_dump(mode="json", exclude={"created_at", "updated_at"})
            )
            != canonical_sha256(proposal.payload.get("strategy_preview_snapshot") or {})
        ):
            raise ConflictError("Strategy draft changed; draft a new Agent proposal.")
        # Persisting a refinement proposal is complete; applying its version is
        # still the separate StrategyProposalService confirmation boundary.
        return linked, {
            "record_type": "strategy_conversation_proposals",
            "status": "draft",
            "version_activated": False,
            "strategy_version_written": False,
            "authority_mutated": False,
        }
    if tool.name == "strategy.request_validation" and isinstance(inputs, StrategyInput):
        raw = proposal.payload.get("backtest_request")
        target = proposal.payload.get("strategy_id")
        if raw is None or target is None:
            return None, {"reason": "no_suitable_job_inputs", "scheduled": False}
        backtest_request = BacktestRunCreate.model_validate(raw).model_copy(
            update={"idempotency_key": f"agent:{conversation.user_id}:{proposal.proposal_id}"}
        )
        run = BacktestService(session, settings).create(
            UUID(target),
            backtest_request,
            organization_id=conversation.organization_id,
            user_id=conversation.user_id,
            execute_inline=False,
        )
        return run.id, {
            "record_type": "backtest_runs",
            "request_created": True,
            "validation_ran": False,
            "replay_ran": False,
            "backtest": run.model_dump(mode="json"),
            "status_path": f"/backtests/{run.id}",
        }
    if isinstance(inputs, PaperTradeInput):
        if inputs.trade_proposal_id is not None:
            return None, {
                "record_type": "trade_proposals",
                "trade_proposal_id": str(inputs.trade_proposal_id),
                "reason": "requires_canonical_execution_gates",
            }
        if inputs.pretrade is None:
            return None, {"reason": "missing_pretrade_inputs"}
        from app.core.dependencies import get_market_data_service
        from app.services.pretrade_analysis_service import PreTradeAnalysisService
        from app.services.strategy_service import StrategyService

        market = get_market_data_service(settings, StrategyService())
        pretrade_request = PreTradeAnalyzeRequest(
            organization_id=conversation.organization_id,
            user_id=conversation.user_id,
            **inputs.pretrade.model_dump(),
        )
        analysis = PreTradeAnalysisService(session, market).analyze(pretrade_request)
        message = ConversationService(session).append_message(
            conversation=conversation,
            role=ConversationMessageRole.ASSISTANT,
            intent="paper_pretrade_analysis",
            content="Canonical advisory pretrade analysis completed; execution requires its gates.",
            payload={
                "agent_proposal_id": str(proposal.proposal_id),
                "pretrade_request": pretrade_request.model_dump(mode="json"),
                "pretrade_result": analysis.model_dump(mode="json"),
                "stated_trade": {
                    key: proposal.payload.get(key) for key in ("entry", "stop", "targets")
                },
                "risk_assessed": False,
                "execution_attempted": False,
            },
        )
        return message.id, {
            "record_type": "conversation_messages",
            "operation": "advisory_pretrade_analysis",
            "final_recommendation": analysis.final_recommendation.value,
            "risk_state": "not_assessed",
            "execution_attempted": False,
        }
    return None, {"reason": "no_suitable_canonical_authority"}
