"""Workflow views linking proposals, approvals, and paper execution eligibility."""

from __future__ import annotations

import uuid

from app.core.errors import NotFoundError, TradingPolicyError
from app.schemas.workflow import ApprovalWorkflowView, ProposalWorkflowView
from app.services.approval_service import ApprovalService
from app.services.execution_eligibility import paper_execution_eligibility
from app.services.proposal_service import ProposalService


class WorkflowService:
    def __init__(
        self,
        proposal_service: ProposalService,
        approval_service: ApprovalService,
    ) -> None:
        self._proposals = proposal_service
        self._approvals = approval_service

    def get_proposal_workflow(
        self,
        proposal_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> ProposalWorkflowView:
        proposal = self._proposals.get(
            proposal_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        approval = self._approvals.get_by_proposal(proposal_id)
        can_execute, block_reason = paper_execution_eligibility(proposal, approval)
        return ProposalWorkflowView(
            proposal=proposal,
            approval=approval,
            can_execute_paper=can_execute,
            block_reason=block_reason,
        )

    def get_approval_workflow(
        self,
        approval_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> ApprovalWorkflowView:
        approval = self._approvals.get(approval_id)
        try:
            proposal = self._proposals.get(
                approval.proposal_id,
                organization_id=organization_id,
                user_id=user_id,
            )
        except NotFoundError:
            return ApprovalWorkflowView(
                approval=approval,
                proposal=None,
                can_execute_paper=False,
                block_reason="Linked proposal not found.",
            )
        except TradingPolicyError as exc:
            if exc.details.get("reason") != "canonical_plan_root_not_proposal_authority":
                raise
            return ApprovalWorkflowView(
                approval=approval,
                proposal=None,
                can_execute_paper=False,
                block_reason=(
                    "Canonical plans execute via EXECUTE_PAPER_PLAN, not the proposal workflow."
                ),
            )
        can_execute, block_reason = paper_execution_eligibility(proposal, approval)
        return ApprovalWorkflowView(
            approval=approval,
            proposal=proposal,
            can_execute_paper=can_execute,
            block_reason=block_reason,
        )
