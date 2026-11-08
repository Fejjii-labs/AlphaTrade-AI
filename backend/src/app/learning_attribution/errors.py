"""Record-only learning attribution errors. Never an execution authority."""

from __future__ import annotations

from app.core.errors import ConflictError, ForbiddenError, ValidationAppError


class LearningAttributionError(ValidationAppError):
    """Base typed failure for the learning attribution layer."""

    code = "learning_attribution_error"


class LearningAttributionConflictError(ConflictError):
    """Same source identity replayed with conflicting semantic content."""

    code = "learning_attribution_conflict"


class LearningAttributionIncompleteError(ConflictError):
    """Required Candidate, eligibility, or lineage is missing after ALLOW.

    Canonical paper execution must fail closed (or persist this explicit
    reason) rather than skip learning evidence.
    """

    code = "learning_attribution_incomplete"

    def __init__(
        self,
        message: str = "Canonical learning attribution cannot proceed without required lineage.",
        *,
        reason: str,
    ) -> None:
        super().__init__(message, details={"reason": reason})


class CrossTenantAttributionError(ForbiddenError):
    """Lineage organization does not match the attribution command.

    Internal-only integrity guard: it fires when server-built lineage disagrees
    with the command's organization, never on a client-supplied id lookup, and no
    HTTP route raises it. Client lookups must answer cross-org ids with 404
    (I10); do not surface this 403 from a route.
    """

    code = "cross_tenant_attribution"

    def __init__(self, message: str = "Cross-tenant attribution is refused.") -> None:
        super().__init__(message, details={"reason": "cross_tenant_attribution"})


class ExecutedOutcomeForbiddenError(LearningAttributionError):
    """REJECT/SKIP (or non-confirmed setup) cannot mint an executed trade outcome."""

    code = "executed_outcome_forbidden"


class MarketTruthMutationError(LearningAttributionError):
    """Learning facts attempted to rewrite SetupAssessment or evidence hashes."""

    code = "market_truth_immutable"


class NarrativeCannotRewriteFactsError(LearningAttributionError):
    """LLM/narrative text cannot override deterministic attribution facts."""

    code = "narrative_cannot_rewrite_facts"
