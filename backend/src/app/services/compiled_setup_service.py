"""Compile tenant strategy versions into immutable CompiledSetupDefinition rows."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.mutation_policy import confirmation_authorizes_mutation
from app.core.errors import NotFoundError, ValidationAppError
from app.db.models import StrategyConversationProposal, UserStrategy, UserStrategyVersion
from app.schemas.common import SetupCompileStatus, StrategyLifecycleState
from app.schemas.governed_learning import GOVERNED_LEARNING
from app.schemas.strategy_library import StrategyCard
from app.schemas.strategy_lifecycle import (
    CompiledSetupDefinitionRecord,
    CompileOutcome,
    StrategyLifecycleEventRecord,
)
from app.schemas.structured_rules import StructuredRules
from app.services.setup_ast_compiler import compile_from_authored, compile_pattern
from app.services.strategy_versioning import CrossTenantStrategyError, StrategyVersioningService

_COMPILABLE_STATES = frozenset(
    {
        StrategyLifecycleState.OBSERVATION,
        StrategyLifecycleState.HYPOTHESIS,
        StrategyLifecycleState.TESTING,
        StrategyLifecycleState.PAPER_ACTIVE,
        StrategyLifecycleState.DRAFT,
        StrategyLifecycleState.STRUCTURED,
        StrategyLifecycleState.HISTORICALLY_VALIDATED,
        StrategyLifecycleState.PAPER_VALIDATING,
        StrategyLifecycleState.REVIEW_REQUIRED,
        StrategyLifecycleState.APPROVED,
        StrategyLifecycleState.ACTIVE,
    }
)
_APPROVE_CONVERGE_STATES = frozenset(
    {StrategyLifecycleState.APPROVED, StrategyLifecycleState.ACTIVE}
)
_APPROVE_FROM_STATES = frozenset(
    {
        StrategyLifecycleState.OBSERVATION,
        StrategyLifecycleState.HYPOTHESIS,
        StrategyLifecycleState.TESTING,
        StrategyLifecycleState.PAPER_ACTIVE,
        StrategyLifecycleState.DRAFT,
        StrategyLifecycleState.STRUCTURED,
        StrategyLifecycleState.HISTORICALLY_VALIDATED,
        StrategyLifecycleState.PAPER_VALIDATING,
        StrategyLifecycleState.REVIEW_REQUIRED,
    }
)


class CompiledSetupService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._versions = StrategyVersioningService(session)

    def compile_version(
        self,
        strategy_version_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        alias: str | None = None,
        persist: bool = True,
        strategy_id: uuid.UUID | None = None,
    ) -> CompileOutcome:
        version, strategy = self._require_version(
            strategy_version_id,
            organization_id=organization_id,
            user_id=user_id,
            alias=alias,
            strategy_id=strategy_id,
        )
        state = self._version_state(version.id)
        if state not in _COMPILABLE_STATES:
            raise ValidationAppError(
                "This strategy version cannot be compiled for review in its current lifecycle."
            )
        card = StrategyCard.model_validate(version.card)
        rules = (
            StructuredRules.model_validate(version.structured_rules)
            if version.structured_rules
            else None
        )
        result = compile_from_authored(
            card=card,
            rules=rules,
            pattern_spec=version.pattern_spec,
            strategy_version_id=version.id,
            organization_id=organization_id,
            alias=alias,
        )
        if result.document is None:
            return CompileOutcome(
                status=SetupCompileStatus.NON_EXECUTABLE,
                failures=result.failures,
            )
        compiled_row = None
        if persist:
            compiled_row = self._versions.persist_compiled(
                organization_id=organization_id,
                user_id=user_id,
                strategy_id=strategy.id,
                strategy_version_id=version.id,
                compiler_version=result.document.compiler_version,
                grammar_version=result.document.grammar_version,
                compiled_ast=result.document.pattern.model_dump(mode="json"),
                content_hash=result.document.content_hash,
            )
            if state in {
                StrategyLifecycleState.OBSERVATION,
                StrategyLifecycleState.HYPOTHESIS,
                StrategyLifecycleState.TESTING,
                StrategyLifecycleState.PAPER_ACTIVE,
                StrategyLifecycleState.DRAFT,
                StrategyLifecycleState.STRUCTURED,
                StrategyLifecycleState.HISTORICALLY_VALIDATED,
                StrategyLifecycleState.PAPER_VALIDATING,
            }:
                self._versions.append_lifecycle(
                    organization_id=organization_id,
                    strategy_id=strategy.id,
                    strategy_version_id=version.id,
                    new_state=StrategyLifecycleState.REVIEW_REQUIRED,
                    actor_user_id=user_id,
                    reason="explicit compile/review of confirmed strategy version",
                    evidence_snapshot={"compiled_content_hash": result.document.content_hash},
                )
        return CompileOutcome(
            status=SetupCompileStatus.EXECUTABLE,
            compiled=(
                CompiledSetupDefinitionRecord.model_validate(compiled_row, from_attributes=True)
                if compiled_row is not None
                else None
            ),
            document=result.document,
            features=result.document.pattern.features,
        )

    def approve_version(
        self,
        strategy_version_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        confirm_message: str,
        strategy_id: uuid.UUID | None = None,
    ) -> StrategyLifecycleEventRecord:
        if not confirmation_authorizes_mutation(confirm_message):
            raise ValidationAppError(
                "Explicit confirmation is required to approve a compiled strategy version. "
                "Conversational confirmation of a draft is not approval."
            )
        version, strategy = self._require_version(
            strategy_version_id,
            organization_id=organization_id,
            user_id=user_id,
            strategy_id=strategy_id,
        )
        state = self._version_state(version.id)
        proposal = self._session.scalar(
            select(StrategyConversationProposal).where(
                StrategyConversationProposal.resulting_version_id == version.id,
                StrategyConversationProposal.organization_id == organization_id,
            )
        )
        if proposal is not None and GOVERNED_LEARNING in proposal.context_refs:
            raise ValidationAppError(
                "Governed candidates require explicit evidence-bound paper promotion approval."
            )
        if state in _APPROVE_CONVERGE_STATES:
            latest = self._versions.latest_lifecycle_event_for_version(version.id)
            if latest is None:
                raise ValidationAppError("Approved strategy version is missing lifecycle history.")
            return StrategyLifecycleEventRecord.model_validate(latest, from_attributes=True)
        if state not in _APPROVE_FROM_STATES:
            raise ValidationAppError(
                "Only reviewed compiled versions can be approved as evaluation policy."
            )
        compiled = self._versions.compiled_for_version(version.id, organization_id=organization_id)
        if compiled is None or compiled.compile_status is not SetupCompileStatus.EXECUTABLE:
            raise ValidationAppError(
                "Explicit executable compile/review is required before approval."
            )
        card = StrategyCard.model_validate(version.card)
        rules = (
            StructuredRules.model_validate(version.structured_rules)
            if version.structured_rules
            else None
        )
        result = compile_from_authored(
            card=card,
            rules=rules,
            pattern_spec=version.pattern_spec,
            strategy_version_id=version.id,
            organization_id=organization_id,
        )
        if result.document is None or result.document.content_hash != compiled.content_hash:
            raise ValidationAppError(
                "Stored compiled definition does not match the version under review."
            )
        event = self._versions.append_lifecycle(
            organization_id=organization_id,
            strategy_id=strategy.id,
            strategy_version_id=version.id,
            new_state=StrategyLifecycleState.APPROVED,
            actor_user_id=user_id,
            reason="explicit approval of executable compiled strategy version",
            evidence_snapshot={"compiled_content_hash": compiled.content_hash},
        )
        return StrategyLifecycleEventRecord.model_validate(event, from_attributes=True)

    def golden_first_slice(self) -> CompileOutcome:
        from app.services.setup_ast_compiler import first_slice_bearish_sweep_pattern

        result = compile_pattern(first_slice_bearish_sweep_pattern())
        return CompileOutcome(
            status=SetupCompileStatus.EXECUTABLE
            if result.document is not None
            else SetupCompileStatus.NON_EXECUTABLE,
            document=result.document,
            failures=result.failures,
            features=result.document.pattern.features if result.document else [],
        )

    def _require_version(
        self,
        strategy_version_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        alias: str | None = None,
        strategy_id: uuid.UUID | None = None,
    ) -> tuple[UserStrategyVersion, UserStrategy]:
        version = self._session.get(UserStrategyVersion, strategy_version_id)
        if version is None:
            raise NotFoundError("Strategy version not found.")
        try:
            strategy = self._versions.assert_tenant_version(
                version, organization_id=organization_id, alias=alias
            )
        except CrossTenantStrategyError:
            # Another org's version must be indistinguishable from a missing id.
            raise NotFoundError("Strategy version not found.") from None
        if strategy.user_id != user_id:
            raise NotFoundError("Strategy version not found.")
        if strategy_id is not None and strategy.id != strategy_id:
            raise NotFoundError("Strategy version not found.")
        return version, strategy

    def _version_state(self, strategy_version_id: uuid.UUID) -> StrategyLifecycleState:
        lifecycle = self._versions.latest_lifecycle_event_for_version(strategy_version_id)
        if lifecycle is None:
            return StrategyLifecycleState.DRAFT
        return lifecycle.new_state
