"""Session ownership per LangGraph node and sessionless provider nodes."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.agents.runtime import AgentRuntime
from app.interactive_agent.vector_adapter import AgentVectorAdapter, DetachedUsage
from app.observability.emitters import ObservabilityEmitter
from app.schemas.audit import AuditRecord, AuditRecordCreate
from app.schemas.rag import RagQuery, RagSearchResponse
from app.services.audit_service import AuditService
from app.services.quota_service import QuotaCheckResult, QuotaService
from app.services.rag_service import RagService
from app.services.turn_coordinator import TurnCoordinator, TurnReservation

logger = structlog.get_logger(__name__)


def phase_evidence(evidence: Any, session: Session) -> Any:
    """Bind canonical lifetime storage to this phase, never the closed HTTP Session."""
    from app.evidence_pipeline.service import CanonicalEvidenceService

    if isinstance(evidence, CanonicalEvidenceService):
        return CanonicalEvidenceService(
            evidence._settings,
            source=evidence._source,
            catalog=evidence._catalog,
            clock=evidence._clock,
            session=session,
        )
    return evidence


_NETWORK_NODES = frozenset(
    {
        "context_retrieval",
        "market_context_retrieval",
        "indicator_calculation",
        "narrative_enhancement",
    }
)


class DetachedAudit(AuditService):
    def __init__(self, sessions: Callable[[], Session], strict_mode: bool) -> None:
        super().__init__(strict_mode=strict_mode)
        self.sessions = sessions
        self.strict_mode = strict_mode

    def record(self, data: AuditRecordCreate) -> AuditRecord | None:
        with self.sessions() as session:
            result = AuditService(session, strict_mode=self.strict_mode).record(data)
            session.commit()
            return result


class DetachedQuota(QuotaService):
    def __init__(self, sessions: Callable[[], Session]) -> None:
        self.sessions = sessions

    def check_feature(self, organization_id: UUID, feature: str, **kwargs: Any) -> QuotaCheckResult:
        with self.sessions() as session:
            result = QuotaService(session).check_feature(organization_id, feature, **kwargs)
            session.commit()
            return result


class AgentRagFacade(RagService):
    def __init__(self, adapter: AgentVectorAdapter) -> None:
        self.adapter = adapter

    def search(self, query: RagQuery, *, request_id: str | None = None) -> RagSearchResponse:
        return self.adapter.search_response(query)


def node_phases(
    seed: AgentRuntime,
    coordinator: TurnCoordinator,
    reservation: TurnReservation,
    revision: str,
    canonical_runtime: Any = None,
    canonical_evidence: Any = None,
) -> Callable[[str], AbstractContextManager[AgentRuntime]]:
    from app.services.agent_service import build_agent_service

    adapter = AgentVectorAdapter(seed.settings, coordinator.sessions)
    rag = AgentRagFacade(adapter)

    @contextmanager
    def phase(name: str) -> Iterator[AgentRuntime]:
        # Network-bearing nodes get no live session. Their quota/audit/retrieval
        # ports use short independent sessions and close them before returning.
        if name in _NETWORK_NODES:
            started = time.perf_counter()
            runtime = build_agent_service(settings=seed.settings).runtime
            runtime.model_router = seed.model_router
            runtime.narrative_service = seed.narrative_service
            runtime.rag_service = rag
            runtime.audit_service = DetachedAudit(
                coordinator.sessions, seed.settings.observability_strict_mode
            )
            runtime.usage_service = DetachedUsage(coordinator.sessions)
            runtime.quota_service = DetachedQuota(coordinator.sessions)
            runtime.observability = ObservabilityEmitter(
                runtime.audit_service, runtime.usage_service
            )
            from app.tools.registry import build_default_registry

            runtime.tool_registry = build_default_registry(
                seed.settings, rag_service=rag, market_data_service=runtime.market_data_service
            )
            try:
                yield runtime
            finally:
                logger.info(
                    "agent_network_phase_latency",
                    turn_id=str(reservation.turn_id),
                    phase=name,
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                )
            return
        started = time.perf_counter()
        with coordinator.sessions() as session, ExitStack() as stack:
            # Tool services can commit internally. Fence each transaction, not
            # merely graph completion, while retaining brief database phases.
            def guard_write(current: Session, *args: Any) -> None:
                with current.no_autoflush:
                    coordinator.guard(current, reservation, revision=revision)

            guard_write(session)
            event.listen(session, "before_flush", guard_write)
            event.listen(session, "before_commit", guard_write)
            if canonical_runtime is not None:
                stack.enter_context(canonical_runtime.bind_session(session))
            runtime = build_agent_service(
                settings=seed.settings,
                session=session,
                canonical_runtime=canonical_runtime,
                canonical_evidence=phase_evidence(canonical_evidence, session),
            ).runtime
            runtime.model_router = seed.model_router
            runtime.narrative_service = seed.narrative_service
            try:
                yield runtime
                session.commit()
            finally:
                event.remove(session, "before_flush", guard_write)
                event.remove(session, "before_commit", guard_write)
        coordinator.transaction_ms.append((time.perf_counter() - started) * 1000)

    return phase
