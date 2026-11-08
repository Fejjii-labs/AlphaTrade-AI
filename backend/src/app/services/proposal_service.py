"""Trade proposal persistence and lifecycle."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, TradingPolicyError, ValidationAppError
from app.core.operation_policy import PersistenceKind, assert_write_allowed
from app.db.canonical_trade_plans import PLAN_ROOT_ANALYSIS, PLAN_ROOT_CANONICAL
from app.db.models import TradeProposal as TradeProposalModel
from app.repositories.proposals import ProposalRepository
from app.repositories.trade_plans import TradePlanRevisionRepository
from app.schemas.agent import AgentState, Intent, OperationClass
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import (
    ActorType,
    AuditEventType,
    LossAcceptanceStatus,
    ProposalStatus,
    SafetyVerdict,
)
from app.schemas.proposal import (
    LossAcceptanceUpdate,
    ProposalStatusUpdate,
    TradeProposal,
    TradeProposalCreate,
)
from app.schemas.trade_plan import (
    TradePlanRevision,
    TradePlanRevisionCreate,
)
from app.services.audit_service import AuditService
from app.services.loss_acceptance_service import LossAcceptanceService
from app.services.mappers.proposal_mapper import exit_to_columns, proposal_to_schema
from app.services.mappers.trade_plan_mapper import trade_plan_revision_to_schema


class ProposalService:
    def __init__(self, session: Session, audit_service: AuditService) -> None:
        self._session = session
        self._repo = ProposalRepository(session)
        self._revisions = TradePlanRevisionRepository(session)
        self._audit = audit_service

    def create(self, data: TradeProposalCreate) -> TradeProposal:
        exit_cols = exit_to_columns(data.exit)
        status = ProposalStatus.PENDING_APPROVAL if data.approval_required else ProposalStatus.DRAFT
        row = TradeProposalModel(
            organization_id=data.organization_id,
            user_id=data.user_id,
            signal_id=data.signal_id,
            strategy_id=data.strategy_id,
            symbol=str(data.symbol),
            timeframe=data.timeframe.value,
            direction=data.direction,
            entry_price=data.entry_price,
            entry_low=data.entry_low,
            entry_high=data.entry_high,
            position_size=data.position_size,
            leverage=data.leverage,
            confidence=data.confidence,
            risk_level=data.risk_level,
            rationale=data.rationale,
            approval_required=data.approval_required,
            risk_result=data.risk_result.model_dump(mode="json") if data.risk_result else None,
            status=status,
            user_strategy_id=data.user_strategy_id,
            planned_loss_amount=data.planned_loss_amount,
            loss_acceptance_required=data.loss_acceptance_required,
            loss_acceptance_status=data.loss_acceptance_status,
            plan_root_kind=PLAN_ROOT_ANALYSIS,
            **exit_cols,
        )
        self._repo.add(row)
        self._record_audit(
            AuditEventType.TRADE_PROPOSAL_CREATED,
            organization_id=data.organization_id,
            user_id=data.user_id,
            resource_id=str(row.id),
            metadata={"symbol": str(data.symbol), "status": status.value},
        )
        return proposal_to_schema(row)

    def create_from_agent(self, agent: AgentState) -> TradeProposal | None:
        """Persist in-memory agent proposal when planning, never for READ_ONLY/analysis."""
        if agent.trade_proposal is None:
            return None
        decision = agent.intent_decision
        if decision is not None and decision.operation_class is not OperationClass.PLAN:
            return None
        if agent.intent is not Intent.PLAN_TRADE:
            return None
        assert_write_allowed(PersistenceKind.PROPOSAL, decision)
        if agent.safety_verdict is SafetyVerdict.BLOCK:
            return None
        proposal = agent.trade_proposal
        data = TradeProposalCreate(
            organization_id=proposal.organization_id,
            user_id=proposal.user_id,
            signal_id=proposal.signal_id,
            strategy_id=proposal.strategy_id,
            symbol=proposal.symbol,
            timeframe=proposal.timeframe,
            direction=proposal.direction,
            entry_price=proposal.entry_price,
            entry_low=proposal.entry_low,
            entry_high=proposal.entry_high,
            position_size=proposal.position_size,
            leverage=proposal.leverage,
            exit=proposal.exit,
            confidence=proposal.confidence,
            risk_level=agent.risk_level or proposal.risk_level,
            rationale=proposal.rationale,
            approval_required=agent.approval_required,
            risk_result=agent.risk_result,
        )
        return self.create(data)

    def get(
        self,
        proposal_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> TradeProposal:
        return proposal_to_schema(
            self._require_analysis_proposal(
                proposal_id,
                organization_id=organization_id,
                user_id=user_id,
            )
        )

    def list_proposals(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[TradeProposal], int]:
        rows, total = self._repo.list_proposals(
            organization_id=organization_id,
            user_id=user_id,
            limit=limit,
            offset=offset,
        )
        return [proposal_to_schema(row) for row in rows], total

    def update_status(
        self,
        proposal_id: uuid.UUID,
        update: ProposalStatusUpdate,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> TradeProposal:
        row = self._require_analysis_proposal(
            proposal_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        row.status = update.status
        row.updated_at = datetime.now(UTC)
        self._repo.add(row)
        self._record_audit(
            AuditEventType.TRADE_PROPOSAL_CREATED,
            organization_id=row.organization_id,
            user_id=row.user_id,
            resource_id=str(row.id),
            metadata={"action": "status_update", "status": update.status.value},
        )
        return proposal_to_schema(row)

    def update_loss_acceptance(
        self,
        proposal_id: uuid.UUID,
        update: LossAcceptanceUpdate,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> TradeProposal:
        from app.schemas.position_sizing import LossAcceptanceRequest

        row = self._require_analysis_proposal(
            proposal_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        planned = row.planned_loss_amount or update.planned_loss_amount
        result = LossAcceptanceService().evaluate(
            planned_loss_amount=planned,
            request=LossAcceptanceRequest(
                planned_loss_amount=update.planned_loss_amount,
                accepted=update.accepted,
            ),
        )
        row.loss_acceptance_status = LossAcceptanceStatus(result.status)
        row.planned_loss_amount = planned
        row.loss_acceptance_required = True
        row.updated_at = datetime.now(UTC)
        self._repo.add(row)
        return proposal_to_schema(row)

    def create_revision(
        self,
        proposal_id: uuid.UUID,
        data: TradePlanRevisionCreate,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> TradePlanRevision:
        """Legacy proposal/PVC path cannot mint canonical TradePlanRevision authority.

        Canonical plans are created only by ``CanonicalTradePlanService`` from an
        ACTIVE Candidate plus ELIGIBLE ActionEligibility.
        """
        del proposal_id, data, organization_id, user_id
        raise ValidationAppError(
            "ANALYSIS_ONLY_CANNOT_CREATE_EXECUTABLE_PLAN: "
            "authoritative plan construction and permission attestation are unavailable."
        )

    def get_revision(
        self,
        proposal_id: uuid.UUID,
        revision_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> TradePlanRevision:
        self._require_analysis_proposal(
            proposal_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        row = self._revisions.get_scoped(
            revision_id,
            plan_id=proposal_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        if row is None:
            raise NotFoundError("Trade plan revision not found")
        return trade_plan_revision_to_schema(row)

    def list_revisions(
        self,
        proposal_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> list[TradePlanRevision]:
        self._require_analysis_proposal(
            proposal_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        return [
            trade_plan_revision_to_schema(row)
            for row in self._revisions.list_for_plan_scoped(
                proposal_id,
                organization_id=organization_id,
                user_id=user_id,
            )
        ]

    def _require_analysis_proposal(
        self,
        proposal_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> TradeProposalModel:
        """Load a proposal for ProposalService trading authority.

        Tenant scope is applied before canonical-root rejection so a
        ``canonical_plan_root`` UUID is not an existence oracle across tenants.
        """
        row = self._repo.get_scoped(
            proposal_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        if row is None:
            raise NotFoundError("Trade proposal not found")
        self._reject_canonical_plan_root(row)
        return row

    def _reject_canonical_plan_root(self, row: TradeProposalModel) -> None:
        if row.plan_root_kind == "manual_demo_test":
            raise TradingPolicyError(
                "Manual demo tests require their explicit preview/confirmation service.",
                details={"reason": "manual_demo_not_proposal_authority"},
            )
        if row.plan_root_kind == PLAN_ROOT_CANONICAL:
            raise TradingPolicyError(
                "canonical_plan_root is not a ProposalService trading authority.",
                details={"reason": "canonical_plan_root_not_proposal_authority"},
            )

    def _record_audit(self, event_type: AuditEventType, **fields: object) -> None:
        self._audit.record(
            AuditRecordCreate(
                request_id="proposal-api",
                trace_id="proposal-api",
                event_type=event_type,
                resource_type="trade_proposal",
                resource_id=str(fields["resource_id"]),
                organization_id=fields["organization_id"],
                user_id=fields["user_id"],
                actor_type=ActorType.AGENT,
                metadata=fields.get("metadata", {}),
            )
        )
