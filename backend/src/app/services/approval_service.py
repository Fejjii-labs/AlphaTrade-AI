"""Human decisions and exact plan-bound approval authorization issuance."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import (
    NotFoundError,
    PersistencePolicyError,
    TradingPolicyError,
    ValidationAppError,
)
from app.core.operation_policy import (
    PersistenceKind,
    assert_authorization_issuance_allowed,
    assert_write_allowed,
    get_operation_decision,
)
from app.db.canonical_trade_plans import PLAN_AUTHORITY_CANONICAL
from app.db.models import ApprovalAuthorization as ApprovalAuthorizationModel
from app.db.models import ApprovalRequest as ApprovalModel
from app.db.models import TradePlanRevision as TradePlanRevisionModel
from app.db.models import TradeProposal as TradeProposalModel
from app.repositories.approvals import (
    ApprovalAuthorizationRepository,
    ApprovalRepository,
    exchange_account_scope_key,
)
from app.repositories.proposals import ProposalRepository
from app.repositories.trade_plans import TradePlanRevisionRepository
from app.schemas.agent import OperationClass, RequestedAction
from app.schemas.approval import (
    ApprovalAuthorization,
    ApprovalAuthorizationAssertion,
    ApprovalAuthorizationIssuance,
    ApprovalDecisionRequest,
    ApprovalRequest,
)
from app.schemas.audit import AuditRecord, AuditRecordCreate
from app.schemas.common import (
    ActorType,
    ApprovalAction,
    ApprovalStatus,
    AuditEventType,
    ProposalStatus,
    RiskSeverity,
)
from app.schemas.trade_plan import (
    AuthorizationChannel,
    AuthorizationDecision,
    AuthorizationState,
    ExecutionMode,
    PlanOperation,
    TradePlanRevisionSemantic,
)
from app.services.approval_authorization_hash import verify_authorization_issuance_hash
from app.services.audit_service import AuditService
from app.services.autonomous_trading import AUTONOMOUS_ACTOR_TYPE
from app.services.canonical_serialization import canonical_sha256
from app.services.canonical_trade_plan import CanonicalTradePlanService
from app.services.canonical_trade_plan_errors import (
    CanonicalTradePlanImmutableError,
    CanonicalTradePlanNotFoundError,
)

_DEFAULT_AUTHORIZATION_TTL = timedelta(minutes=15)
CHANNEL_MISMATCH_REASON = "authorization_channel_mismatch"


def _channel_name(value: object) -> str | None:
    if value is None:
        return None
    raw = value.value if hasattr(value, "value") else value
    if not isinstance(raw, str) or not raw:
        return None
    return raw


def stored_issuance_matches(
    authorization: object,
    *,
    channel: AuthorizationChannel,
    actor_type: str,
) -> bool:
    """Whether a stored authorization is the issuance this caller requested.

    An object with no channel is not a stored row (a test double) and is left
    alone. A stored row always has a channel and an actor. Autonomous issuance
    matches only AUTONOMOUS plus the system actor. A human issuance matches its
    own channel and actor, and never an autonomous row.
    """

    stored_channel = _channel_name(getattr(authorization, "channel", None))
    if stored_channel is None:
        return True
    stored_actor = getattr(authorization, "actor_type", None)
    actor = stored_actor if isinstance(stored_actor, str) and stored_actor else None
    # A stored channel with no actor is not a match. Reuse requires both.
    if actor is None:
        return False
    requested_autonomous = (
        channel is AuthorizationChannel.AUTONOMOUS and actor_type == AUTONOMOUS_ACTOR_TYPE
    )
    autonomous_row = (
        stored_channel == AuthorizationChannel.AUTONOMOUS.value or actor == AUTONOMOUS_ACTOR_TYPE
    )
    if requested_autonomous:
        return (
            stored_channel == AuthorizationChannel.AUTONOMOUS.value
            and actor == AUTONOMOUS_ACTOR_TYPE
        )
    if autonomous_row:
        return False
    return stored_channel == channel.value and actor == actor_type


def human_execution_refused(authorization: object) -> bool:
    """A human caller must not execute an autonomous or system authorization."""

    stored_channel = _channel_name(getattr(authorization, "channel", None))
    if stored_channel is None:
        return False
    stored_actor = getattr(authorization, "actor_type", None)
    actor = stored_actor if isinstance(stored_actor, str) else None
    return stored_channel == AuthorizationChannel.AUTONOMOUS.value or actor == AUTONOMOUS_ACTOR_TYPE


def assert_human_may_execute(authorization: object) -> None:
    """Refuse a human claim of an autonomous authorization before it is consumed."""

    if human_execution_refused(authorization):
        raise TradingPolicyError(
            "A human execution cannot consume an autonomous authorization.",
            details={"reason": CHANNEL_MISMATCH_REASON},
        )


class ApprovalService:
    def __init__(
        self,
        session: Session,
        audit_service: AuditService,
        *,
        clock: Callable[[], datetime] | None = None,
        plans: CanonicalTradePlanService | None = None,
    ) -> None:
        self._session = session
        self._repo = ApprovalRepository(session)
        self._authorizations = ApprovalAuthorizationRepository(session)
        self._proposals = ProposalRepository(session)
        self._revisions = TradePlanRevisionRepository(session)
        self._audit = audit_service
        self._clock = clock or (lambda: datetime.now(UTC))
        self._plans = plans

    def create_for_proposal(
        self,
        *,
        proposal_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        risk_level: RiskSeverity,
        confidence: float,
        approval_reason: str | None = None,
    ) -> ApprovalRequest:
        """Preserve the legacy analysis-proposal approval workflow."""
        decision = get_operation_decision()
        if decision is not None:
            if decision.requested_action in {RequestedAction.REJECT, RequestedAction.SKIP}:
                raise TradingPolicyError(
                    "REJECT/SKIP cannot create or issue an authorization.",
                    details={"requested_action": decision.requested_action.value},
                )
            if decision.operation_class is OperationClass.READ_ONLY:
                raise PersistencePolicyError(
                    "READ_ONLY forbids approval persistence.",
                    details={"kind": PersistenceKind.APPROVAL.value},
                )
            assert_write_allowed(PersistenceKind.APPROVAL, decision)
        existing = self._repo.get_by_proposal(proposal_id)
        if existing is not None:
            return self._to_schema(existing)
        row = ApprovalModel(
            proposal_id=proposal_id,
            organization_id=organization_id,
            user_id=user_id,
            risk_level=risk_level,
            confidence=confidence,
            approval_reason=approval_reason,
        )
        self._repo.add(row)
        proposal = self._proposals.get_scoped(
            proposal_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        if proposal is not None:
            proposal.status = ProposalStatus.PENDING_APPROVAL
            self._proposals.add(proposal)
        self._record_audit(
            AuditEventType.APPROVAL_REQUIRED,
            organization_id=organization_id,
            user_id=user_id,
            resource_id=str(row.id),
            metadata={"proposal_id": str(proposal_id), "reason": approval_reason},
        )
        return self._to_schema(row)

    def create_for_plan_revision(
        self,
        *,
        revision_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        authorization_expires_at: datetime | None = None,
        approval_reason: str | None = None,
    ) -> ApprovalRequest:
        """Create one pending decision bound to the current immutable plan revision."""
        assert_write_allowed(PersistenceKind.APPROVAL, get_operation_decision())
        revision = self._revisions.get_scoped(
            revision_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        if revision is None:
            raise NotFoundError("Trade plan revision not found")
        proposal = self._proposals.get_scoped(
            revision.plan_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        if proposal is None or proposal.latest_plan_revision_id != revision.id:
            raise ValidationAppError(
                "Only the current plan revision can be submitted for approval."
            )
        existing = self._repo.get_by_revision(revision.id)
        if existing is not None:
            return self._to_schema(existing)

        now = self._aware(self._clock())
        valid_until = self._aware(revision.valid_until)
        expires_at = (
            self._aware(authorization_expires_at)
            if authorization_expires_at is not None
            else min(now + _DEFAULT_AUTHORIZATION_TTL, valid_until)
        )
        if expires_at <= now or expires_at > valid_until:
            raise ValidationAppError(
                "Authorization expiry must be future and within plan validity."
            )

        row = ApprovalModel(
            proposal_id=revision.plan_id,
            plan_revision_id=revision.id,
            organization_id=organization_id,
            user_id=user_id,
            risk_level=proposal.risk_level,
            confidence=proposal.confidence,
            approval_reason=approval_reason,
            authorization_expires_at=expires_at,
        )
        self._repo.add(row)
        proposal.status = ProposalStatus.PENDING_APPROVAL
        self._proposals.add(proposal)
        self._record_audit(
            AuditEventType.APPROVAL_REQUIRED,
            organization_id=organization_id,
            user_id=user_id,
            resource_id=str(row.id),
            metadata={
                "proposal_id": str(revision.plan_id),
                "revision_id": str(revision.id),
                "plan_content_hash": revision.content_hash,
                "correlation_id": str(revision.correlation_id),
            },
        )
        return self._to_schema(row)

    def get(self, approval_id: uuid.UUID) -> ApprovalRequest:
        row = self._repo.get(approval_id)
        if row is None:
            raise NotFoundError("Approval not found")
        return self._to_schema(row)

    def get_scoped(
        self,
        approval_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> ApprovalRequest:
        row = self._repo.get_scoped(
            approval_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        if row is None:
            raise NotFoundError("Approval not found")
        return self._to_schema(row)

    def get_by_proposal(self, proposal_id: uuid.UUID) -> ApprovalRequest | None:
        row = self._repo.get_by_proposal(proposal_id)
        return self._to_schema(row) if row is not None else None

    def list_approvals(
        self,
        *,
        organization_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        status: ApprovalStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ApprovalRequest], int]:
        rows, total = self._repo.list_approvals(
            organization_id=organization_id,
            user_id=user_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        return [self._to_schema(row) for row in rows], total

    def decide(
        self,
        approval_id: uuid.UUID,
        decision: ApprovalDecisionRequest,
        *,
        principal_organization_id: uuid.UUID | None = None,
        principal_user_id: uuid.UUID | None = None,
        channel: AuthorizationChannel = AuthorizationChannel.API,
        actor_type: str = "USER",
        audit_metadata: dict[str, object] | None = None,
    ) -> ApprovalRequest:
        row = self._repo.get(approval_id)
        if row is None:
            raise NotFoundError("Approval not found")
        if row.plan_revision_id is not None:
            self._require_exact_principal(
                row,
                organization_id=principal_organization_id,
                user_id=principal_user_id,
            )

        if row.status is ApprovalStatus.APPROVED and decision.action is ApprovalAction.APPROVE:
            if row.plan_revision_id is None:
                return self._to_schema(row)
            prior = self._authorizations.get_reusable_for_approval(row.id)
            issued = self.issue_authorization(
                approval_id=approval_id,
                decision=AuthorizationDecision.APPROVE,
                organization_id=row.organization_id,
                user_id=row.user_id,
                channel=channel,
                actor_type=actor_type,
                assertion=decision.authorization_assertion,
            )
            if prior is None or prior.id != issued.authorization_id:
                self._audit_decision(
                    row,
                    decision,
                    channel=channel,
                    actor_type=actor_type,
                    audit_metadata=audit_metadata,
                    authorization_id=issued.authorization_id,
                )
            return self._to_schema(row)
        if row.status is not ApprovalStatus.PENDING:
            raise ValidationAppError(
                "Approval is not pending",
                details={"current_status": row.status.value},
            )

        issued_authorization_id: uuid.UUID | None = None
        if decision.action is ApprovalAction.APPROVE and row.plan_revision_id is not None:
            if decision.modified_fields:
                raise ValidationAppError("APPROVE cannot modify immutable plan fields.")
            issued = self.issue_authorization(
                approval_id=approval_id,
                decision=AuthorizationDecision.APPROVE,
                organization_id=row.organization_id,
                user_id=row.user_id,
                channel=channel,
                actor_type=actor_type,
                assertion=decision.authorization_assertion,
            )
            issued_authorization_id = issued.authorization_id

        row.status = _action_to_status(decision.action)
        row.proposed_action = decision.action
        row.modified_fields = decision.modified_fields
        row.approval_reason = decision.reason
        row.decided_at = self._aware(self._clock())
        self._repo.add(row)

        proposal = self._proposals.get_scoped(
            row.proposal_id,
            organization_id=row.organization_id,
            user_id=row.user_id,
        )
        if proposal is not None:
            proposal.status = _proposal_status_for_action(decision.action)
            self._proposals.add(proposal)

        self._audit_decision(
            row,
            decision,
            channel=channel,
            actor_type=actor_type,
            audit_metadata=audit_metadata,
            authorization_id=issued_authorization_id,
        )
        return self._to_schema(row)

    def issue_authorization(
        self,
        *,
        approval_id: uuid.UUID,
        decision: AuthorizationDecision,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        channel: AuthorizationChannel,
        actor_type: str = "USER",
        assertion: ApprovalAuthorizationAssertion | None = None,
        manual_demo_confirmation_hash: str | None = None,
    ) -> ApprovalAuthorization:
        """Reuse a matching AVAILABLE row, or any CONSUMED row.

        An AVAILABLE row is reused only when its channel and actor are the
        issuance being requested. A leftover API, WEB, or TELEGRAM row is not
        an autonomous authorization, and an AUTONOMOUS row is not a human one.
        That AVAILABLE row is revoked, with an approval-decision audit, and a
        new binding is inserted. A CONSUMED row is reused so the revision still
        has exactly one ALLOW. REVOKED and EXPIRED rows are not reused.
        """
        operation_decision = get_operation_decision()
        if operation_decision is not None:
            assert_authorization_issuance_allowed(operation_decision)
            assert_write_allowed(PersistenceKind.AUTHORIZATION, operation_decision)
        if decision is not AuthorizationDecision.APPROVE:
            raise ValidationAppError("Only APPROVE can issue an authorization.")
        approval = self._repo.get_scoped(
            approval_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        if approval is None:
            raise NotFoundError("Approval not found")
        if approval.plan_revision_id is None or approval.authorization_expires_at is None:
            raise ValidationAppError("Legacy analysis approvals cannot authorize execution.")
        if approval.status not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
            raise ValidationAppError("Approval state cannot issue an authorization.")

        revision = self._revisions.get_scoped(
            approval.plan_revision_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        if revision is None:
            raise NotFoundError("Trade plan revision not found")
        proposal = self._proposals.get_scoped(
            revision.plan_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        if proposal is None or proposal.latest_plan_revision_id != revision.id:
            raise ValidationAppError("Plan revision has been superseded.")

        if revision.plan_authority == "manual_demo_test" and (
            manual_demo_confirmation_hash != revision.content_hash
        ):
            raise TradingPolicyError("Manual demo requires explicit exact-plan confirmation.")
        semantic = TradePlanRevisionSemantic.model_validate(revision.semantic_payload)
        computed_plan_hash = canonical_sha256(semantic)
        if computed_plan_hash != revision.content_hash:
            raise ValidationAppError("Plan content hash verification failed.")
        from app.services.planned_reward_risk import PlannedRewardRiskError, execution_reward_risk

        try:
            execution_reward_risk(semantic)
        except PlannedRewardRiskError as exc:
            raise TradingPolicyError(str(exc), details={"reason": exc.reason}) from exc
        if revision.plan_authority == PLAN_AUTHORITY_CANONICAL:
            if self._plans is None:
                raise TradingPolicyError(
                    "Canonical plan approval requires CanonicalTradePlanService.",
                    details={"reason": "canonical_runtime_unbound"},
                )
            try:
                self._plans.assert_approval_preserves_semantics(
                    organization_id=organization_id,
                    user_id=user_id,
                    revision_id=revision.id,
                    plan_content_hash=revision.content_hash,
                )
            except CanonicalTradePlanNotFoundError as exc:
                raise NotFoundError("Canonical trade plan revision is unknown.") from exc
            except CanonicalTradePlanImmutableError as exc:
                raise ValidationAppError(str(exc)) from exc
        self._validate_assertion(
            assertion,
            revision=revision,
            organization_id=organization_id,
            user_id=user_id,
        )

        accepted = self._accept_or_replace_reusable(
            self._authorizations.get_reusable_for_approval(approval.id),
            revision=revision,
            approval=approval,
            proposal=proposal,
            channel=channel,
            actor_type=actor_type,
        )
        if accepted is not None:
            return accepted
        accepted = self._accept_or_replace_reusable(
            self._authorizations.get_reusable_by_binding(
                organization_id=organization_id,
                account_id=revision.account_id,
                exchange_account_id=revision.exchange_account_id,
                revision_id=revision.id,
                plan_content_hash=revision.content_hash,
                operation=revision.operation,
            ),
            revision=revision,
            approval=approval,
            proposal=proposal,
            channel=channel,
            actor_type=actor_type,
        )
        if accepted is not None:
            return accepted

        now = self._aware(self._clock())
        expires_at = self._aware(approval.authorization_expires_at)
        if expires_at <= now or self._aware(revision.valid_until) <= now:
            raise ValidationAppError("Approval authorization has expired.")
        self._mark_plan_approval_approved(approval, proposal)
        return self._insert_authorization(
            approval=approval,
            revision=revision,
            organization_id=organization_id,
            user_id=user_id,
            channel=channel,
            actor_type=actor_type,
            expires_at=expires_at,
            now=now,
            attempts_remaining=1,
        )

    def get_authorization(
        self,
        authorization_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> ApprovalAuthorization:
        row = self._authorizations.get(authorization_id)
        if row is None or row.organization_id != organization_id or row.user_id != user_id:
            raise NotFoundError("Approval authorization not found")
        self._authorizations.expire_due(authorization_id, at=self._aware(self._clock()))
        self._session.refresh(row)
        return self._authorization_to_schema(row)

    def revoke_authorization(
        self,
        authorization_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        reason: str | None = None,
    ) -> ApprovalAuthorization:
        row = self._authorizations.get(authorization_id)
        if row is None or row.organization_id != organization_id or row.user_id != user_id:
            raise NotFoundError("Approval authorization not found")
        revoked = self._authorizations.revoke_available(
            authorization_id, at=self._aware(self._clock())
        )
        self._session.refresh(row)
        if reason is not None and revoked:
            channel = row.channel
            self._record_audit(
                AuditEventType.APPROVAL_DECISION,
                organization_id=row.organization_id,
                user_id=row.user_id,
                resource_id=str(row.id),
                actor_type=ActorType.SYSTEM if row.actor_type == "SYSTEM" else ActorType.USER,
                metadata={
                    "action": "revoke",
                    "reason": reason,
                    "authorization_id": str(row.id),
                    "channel": channel.value if hasattr(channel, "value") else str(channel),
                    "actor_type": row.actor_type,
                },
            )
        return self._authorization_to_schema(row)

    def is_authorization_available(
        self,
        authorization_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        account_id: uuid.UUID,
    ) -> bool:
        return (
            self._authorizations.get_available_scoped(
                authorization_id,
                organization_id=organization_id,
                user_id=user_id,
                account_id=account_id,
                at=self._aware(self._clock()),
            )
            is not None
        )

    def _accept_or_replace_reusable(
        self,
        existing: ApprovalAuthorizationModel | None,
        *,
        revision: TradePlanRevisionModel,
        approval: ApprovalModel,
        proposal: TradeProposalModel,
        channel: AuthorizationChannel,
        actor_type: str,
    ) -> ApprovalAuthorization | None:
        """Return a reusable row, or revoke a mismatched AVAILABLE row.

        ``None`` means the caller should insert. A CONSUMED row is returned
        unchanged. An AVAILABLE row is returned only when it matches.
        """

        if existing is None:
            return None
        self._validate_existing_authorization(existing, revision)
        matches = stored_issuance_matches(
            existing,
            channel=channel,
            actor_type=actor_type,
        )
        if existing.state is AuthorizationState.CONSUMED:
            self._mark_plan_approval_approved(approval, proposal)
            return self._authorization_to_schema(existing)
        now = self._aware(self._clock())
        if (
            existing.state is AuthorizationState.AVAILABLE
            and matches
            and self._aware(existing.expires_at) <= now
        ):
            self._authorizations.expire_due(existing.id, at=now)
            self._session.refresh(existing)
            return None
        if matches:
            self._mark_plan_approval_approved(approval, proposal)
            return self._authorization_to_schema(existing)
        if existing.state is AuthorizationState.AVAILABLE:
            self.revoke_authorization(
                existing.id,
                organization_id=existing.organization_id,
                user_id=existing.user_id,
                reason=CHANNEL_MISMATCH_REASON,
            )
        return None

    def _insert_authorization(
        self,
        *,
        approval: ApprovalModel,
        revision: TradePlanRevisionModel,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        channel: AuthorizationChannel,
        actor_type: str,
        expires_at: datetime,
        now: datetime,
        attempts_remaining: int,
    ) -> ApprovalAuthorization:
        authorization_id = uuid.uuid4()
        issuance = ApprovalAuthorizationIssuance(
            authorization_id=authorization_id,
            approval_request_id=approval.id,
            organization_id=organization_id,
            user_id=user_id,
            account_id=revision.account_id,
            exchange_account_id=revision.exchange_account_id,
            operation=PlanOperation.SUBMIT_ENTRY,
            execution_mode=ExecutionMode.PAPER,
            plan_id=revision.plan_id,
            revision_id=revision.id,
            plan_content_hash=revision.content_hash,
            execution_venue=revision.execution_venue,
            execution_instrument=revision.execution_instrument,
            verified_account_mode=revision.expected_account_mode,
            permission_attestation_id=revision.permission_attestation_id,
            permission_attestation_version=revision.permission_attestation_version,
            expires_at=expires_at,
            channel=channel,
            actor_type=actor_type,
            actor_id=user_id,
            correlation_id=revision.correlation_id,
            created_at=now,
        )
        row = ApprovalAuthorizationModel(
            id=authorization_id,
            approval_request_id=approval.id,
            organization_id=organization_id,
            user_id=user_id,
            account_id=revision.account_id,
            exchange_account_id=revision.exchange_account_id,
            exchange_account_scope_key=exchange_account_scope_key(revision.exchange_account_id),
            operation=issuance.operation,
            execution_mode=issuance.execution_mode,
            plan_id=revision.plan_id,
            revision_id=revision.id,
            plan_content_hash=revision.content_hash,
            execution_venue=revision.execution_venue,
            execution_instrument=revision.execution_instrument,
            verified_account_mode=revision.expected_account_mode,
            permission_attestation_id=revision.permission_attestation_id,
            permission_attestation_version=revision.permission_attestation_version,
            expires_at=expires_at,
            state=AuthorizationState.AVAILABLE,
            channel=channel,
            actor_type=issuance.actor_type,
            actor_id=user_id,
            correlation_id=revision.correlation_id,
            authorization_content_hash=canonical_sha256(issuance),
            created_at=now,
            updated_at=now,
        )
        try:
            with self._session.begin_nested():
                self._authorizations.add(row)
        except IntegrityError:
            concurrent = self._authorizations.get_reusable_by_binding(
                organization_id=organization_id,
                account_id=revision.account_id,
                exchange_account_id=revision.exchange_account_id,
                revision_id=revision.id,
                plan_content_hash=revision.content_hash,
                operation=revision.operation,
            )
            if concurrent is None:
                raise
            self._validate_existing_authorization(concurrent, revision)
            matches = stored_issuance_matches(
                concurrent,
                channel=channel,
                actor_type=actor_type,
            )
            if concurrent.state is AuthorizationState.CONSUMED or (
                matches and self._aware(concurrent.expires_at) > now
            ):
                return self._authorization_to_schema(concurrent)
            if matches:
                self._authorizations.expire_due(concurrent.id, at=now)
                self._session.refresh(concurrent)
                raise ValidationAppError("Approval authorization has expired.") from None
            if attempts_remaining <= 0:
                raise ValidationAppError(
                    "Authorization channel conflict.",
                    details={"reason": CHANNEL_MISMATCH_REASON},
                ) from None
            self.revoke_authorization(
                concurrent.id,
                organization_id=concurrent.organization_id,
                user_id=concurrent.user_id,
                reason=CHANNEL_MISMATCH_REASON,
            )
            return self._insert_authorization(
                approval=approval,
                revision=revision,
                organization_id=organization_id,
                user_id=user_id,
                channel=channel,
                actor_type=actor_type,
                expires_at=expires_at,
                now=now,
                attempts_remaining=attempts_remaining - 1,
            )
        return self._authorization_to_schema(row)

    @staticmethod
    def _require_exact_principal(
        row: ApprovalModel,
        *,
        organization_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
    ) -> None:
        if organization_id is None or user_id is None:
            raise ValidationAppError("Plan-bound approvals require an authenticated principal.")
        if row.organization_id != organization_id:
            raise ValidationAppError("Approval organization does not match the principal.")
        if row.user_id != user_id:
            raise ValidationAppError("Approval user does not match the principal.")

    def _mark_plan_approval_approved(
        self,
        approval: ApprovalModel,
        proposal: TradeProposalModel,
    ) -> None:
        if approval.status is ApprovalStatus.APPROVED:
            return
        approval.status = ApprovalStatus.APPROVED
        approval.proposed_action = ApprovalAction.APPROVE
        approval.decided_at = self._aware(self._clock())
        self._repo.add(approval)
        proposal.status = ProposalStatus.APPROVED
        self._proposals.add(proposal)

    @staticmethod
    def _validate_assertion(
        assertion: ApprovalAuthorizationAssertion | None,
        *,
        revision: TradePlanRevisionModel,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        if assertion is None:
            return
        expected = {
            "organization_id": organization_id,
            "user_id": user_id,
            "account_id": revision.account_id,
            "exchange_account_id": revision.exchange_account_id,
            "operation": revision.operation,
            "plan_id": revision.plan_id,
            "revision_id": revision.id,
            "plan_content_hash": revision.content_hash,
        }
        supplied = assertion.model_dump(mode="python")
        mismatches = sorted(key for key, value in expected.items() if supplied[key] != value)
        if mismatches:
            raise ValidationAppError(
                "Approval authorization assertion does not match the immutable plan.",
                details={"mismatched_fields": mismatches},
            )

    @staticmethod
    def _validate_existing_authorization(
        authorization: ApprovalAuthorizationModel,
        revision: TradePlanRevisionModel,
    ) -> None:
        expected = (
            revision.organization_id,
            revision.user_id,
            revision.account_id,
            revision.exchange_account_id,
            revision.plan_id,
            revision.id,
            revision.content_hash,
            revision.operation,
            revision.correlation_id,
        )
        actual = (
            authorization.organization_id,
            authorization.user_id,
            authorization.account_id,
            authorization.exchange_account_id,
            authorization.plan_id,
            authorization.revision_id,
            authorization.plan_content_hash,
            authorization.operation,
            authorization.correlation_id,
        )
        if actual != expected:
            raise ValidationAppError("Existing authorization binding is inconsistent.")
        if not verify_authorization_issuance_hash(
            ApprovalService._authorization_to_schema(authorization)
        ):
            raise ValidationAppError("Existing authorization issuance hash is invalid.")

    def _audit_decision(
        self,
        row: ApprovalModel,
        decision: ApprovalDecisionRequest,
        *,
        channel: AuthorizationChannel,
        actor_type: str,
        audit_metadata: dict[str, object] | None,
        authorization_id: uuid.UUID | None,
    ) -> None:
        metadata: dict[str, object] = {
            "action": decision.action.value,
            "proposal_id": str(row.proposal_id),
            "revision_id": str(row.plan_revision_id) if row.plan_revision_id else None,
            "modified_fields": decision.modified_fields or {},
            "authorization_issued": authorization_id is not None,
            "channel": channel.value,
            "actor_type": actor_type,
        }
        if authorization_id is not None:
            metadata["authorization_id"] = str(authorization_id)
        if audit_metadata is not None:
            metadata["autonomous_approval"] = audit_metadata
        audit_record = self._record_audit(
            AuditEventType.APPROVAL_DECISION,
            organization_id=row.organization_id,
            user_id=row.user_id,
            resource_id=str(row.id),
            actor_type=ActorType.SYSTEM if actor_type == "SYSTEM" else ActorType.USER,
            metadata=metadata,
        )
        if audit_record is not None:
            row.audit_event_id = audit_record.event_id
            self._repo.add(row)

    def _to_schema(self, row: ApprovalModel) -> ApprovalRequest:
        authorization = self._authorizations.get_reusable_for_approval(row.id)
        return ApprovalRequest(
            id=row.id,
            proposal_id=row.proposal_id,
            plan_revision_id=row.plan_revision_id,
            organization_id=row.organization_id,
            user_id=row.user_id,
            status=row.status,
            proposed_action=row.proposed_action,
            modified_fields=row.modified_fields,
            risk_level=row.risk_level,
            confidence=row.confidence,
            approval_reason=row.approval_reason,
            audit_event_id=row.audit_event_id,
            authorization_expires_at=row.authorization_expires_at,
            authorization=(
                self._authorization_to_schema(authorization) if authorization is not None else None
            ),
            created_at=row.created_at,
            decided_at=row.decided_at,
        )

    @staticmethod
    def _authorization_to_schema(row: ApprovalAuthorizationModel) -> ApprovalAuthorization:
        return ApprovalAuthorization(
            authorization_id=row.id,
            approval_request_id=row.approval_request_id,
            organization_id=row.organization_id,
            user_id=row.user_id,
            account_id=row.account_id,
            exchange_account_id=row.exchange_account_id,
            operation=row.operation,
            execution_mode=row.execution_mode,
            plan_id=row.plan_id,
            revision_id=row.revision_id,
            plan_content_hash=row.plan_content_hash,
            execution_venue=row.execution_venue,
            execution_instrument=row.execution_instrument,
            verified_account_mode=row.verified_account_mode,
            permission_attestation_id=row.permission_attestation_id,
            permission_attestation_version=row.permission_attestation_version,
            expires_at=row.expires_at,
            state=row.state,
            channel=row.channel,
            actor_type=row.actor_type,
            actor_id=row.actor_id,
            correlation_id=row.correlation_id,
            created_at=row.created_at,
            consumed_at=row.consumed_at,
            consumed_by_execution_command_id=row.consumed_by_execution_command_id,
            authorization_content_hash=row.authorization_content_hash,
        )

    def _record_audit(
        self,
        event_type: AuditEventType,
        **fields: object,
    ) -> AuditRecord | None:
        raw_actor = fields.get("actor_type", ActorType.USER)
        actor = raw_actor if isinstance(raw_actor, ActorType) else ActorType.USER
        metadata = fields.get("metadata", {})
        return self._audit.record(
            AuditRecordCreate(
                request_id="approval-api",
                trace_id="approval-api",
                event_type=event_type,
                resource_type="approval",
                resource_id=str(fields["resource_id"]),
                organization_id=fields["organization_id"],  # type: ignore[arg-type]
                user_id=fields["user_id"],  # type: ignore[arg-type]
                actor_type=actor,
                metadata=metadata if isinstance(metadata, dict) else {},
            )
        )

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def revoke_leftover_autonomous_authorizations(
    session: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    revision_id: uuid.UUID,
    reason: str,
    clock: Callable[[], datetime],
) -> None:
    """Revoke AVAILABLE AUTONOMOUS authorizations for one revision.

    An empty result does not construct an approval. A row that changes writes
    the same approval-decision audit as ``revoke_authorization``.
    """

    rows = list(
        session.scalars(
            select(ApprovalAuthorizationModel).where(
                ApprovalAuthorizationModel.organization_id == organization_id,
                ApprovalAuthorizationModel.user_id == user_id,
                ApprovalAuthorizationModel.revision_id == revision_id,
                ApprovalAuthorizationModel.state == AuthorizationState.AVAILABLE,
                ApprovalAuthorizationModel.channel == AuthorizationChannel.AUTONOMOUS,
            )
        ).all()
    )
    if not rows:
        return
    approval = ApprovalService(session, AuditService(session), clock=clock)
    for row in rows:
        approval.revoke_authorization(
            row.id,
            organization_id=organization_id,
            user_id=user_id,
            reason=reason,
        )


def _action_to_status(action: ApprovalAction) -> ApprovalStatus:
    mapping = {
        ApprovalAction.APPROVE: ApprovalStatus.APPROVED,
        ApprovalAction.REJECT: ApprovalStatus.REJECTED,
        ApprovalAction.MODIFY: ApprovalStatus.MODIFIED,
        ApprovalAction.PAUSE: ApprovalStatus.PAUSED,
        ApprovalAction.CANCEL: ApprovalStatus.CANCELLED,
        ApprovalAction.CLOSE: ApprovalStatus.CLOSED,
        ApprovalAction.NEEDS_MORE_ANALYSIS: ApprovalStatus.NEEDS_MORE_ANALYSIS,
    }
    return mapping[action]


def _proposal_status_for_action(action: ApprovalAction) -> ProposalStatus:
    if action is ApprovalAction.APPROVE:
        return ProposalStatus.APPROVED
    if action is ApprovalAction.REJECT:
        return ProposalStatus.REJECTED
    return ProposalStatus.PENDING_APPROVAL
