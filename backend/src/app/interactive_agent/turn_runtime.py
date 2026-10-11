"""Interactive turn phases: prepare, provider, finalize, capture, receipt commit."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from app.agent_capture.contracts import CaptureStatus, SavedEntry
from app.agent_capture.service import CaptureService, CaptureSnapshot, uploaded_text
from app.core.config import Settings
from app.core.errors import NotFoundError
from app.db.models import ConversationMessage
from app.evidence_pipeline.service import CanonicalEvidenceService
from app.interactive_agent.canonical_market import CanonicalPerpetualQuoteReader
from app.interactive_agent.contracts import (
    PAYLOAD_KEY,
    AgentTurnRequest,
    AgentTurnResult,
    KnowledgeHit,
    MarketQuoteView,
    TurnOperation,
)
from app.interactive_agent.conversation import (
    MODEL_REPLY_UNAVAILABLE,
    ModelConversationalResponder,
    compose_visible_reply,
)
from app.interactive_agent.parsing import extract_symbol
from app.interactive_agent.service import InteractiveAgentService
from app.interactive_agent.vector_adapter import AgentVectorAdapter, DetachedUsage
from app.providers.embeddings import EmbeddingsProvider
from app.providers.llm import LLMProvider
from app.services.turn_coordinator import (
    RESERVATION,
    TurnCoordinator,
    TurnReservation,
    transcript_revision,
)

logger = structlog.get_logger(__name__)


class DeferredResponder:
    """Collect immutable model inputs during SQL preparation."""

    def __init__(self) -> None:
        self.inputs: dict[str, Any] | None = None

    def compose(self, **kwargs: Any) -> str:
        self.inputs = kwargs
        return MODEL_REPLY_UNAVAILABLE


class PreparedVectorHits:
    def __init__(
        self, hits: list[KnowledgeHit], notes: list[str], *, allow_sql_fallback: bool = True
    ) -> None:
        self.hits, self.notes = hits, notes
        self.allow_sql_fallback = allow_sql_fallback

    def search(self, **kwargs: Any) -> list[KnowledgeHit]:
        return self.hits[: kwargs["limit"]]


class PreparedQuote:
    def __init__(self, reader: CanonicalPerpetualQuoteReader, symbol: str) -> None:
        self.error: Exception | None = None
        self.value = None
        try:
            self.value = reader.quote(symbol)
        except Exception as exc:
            self.error = exc

    def quote(self, symbol: str) -> MarketQuoteView:
        if self.error:
            raise self.error
        if self.value is None or self.value.symbol != symbol:
            raise NotFoundError("Prepared market evidence is unavailable for this symbol.")
        return self.value


def store_capture_receipt(session: Session, result: AgentTurnResult) -> None:
    assistant = session.get(ConversationMessage, result.assistant_message_id)
    if assistant is None:
        raise NotFoundError("Assistant reply no longer exists.")
    detail = dict(assistant.payload.get(PAYLOAD_KEY) or {})
    detail["model_usage"] = result.model_usage
    detail["capture"] = {
        "saved_entries": [e.model_dump(mode="json") for e in result.saved_entries],
        "status": result.capture_status,
        "error": result.capture_error,
        "clarification": result.capture_clarification,
        "source_message_id": str(result.user_message_id),
        "model_usage": result.model_usage,
    }
    assistant.payload = {**assistant.payload, PAYLOAD_KEY: detail}


def capture_in_phases(
    sessions: Callable[[], Session],
    settings: Settings,
    reservation: TurnReservation,
    message_id: UUID,
    source_document_id: UUID | None = None,
    usage_sink: list[dict[str, Any]] | None = None,
    llm_provider: LLMProvider | None = None,
) -> tuple[list[SavedEntry], CaptureStatus, str | None, dict[str, Any]]:
    started = time.perf_counter()
    prepare_started = time.perf_counter()
    with sessions() as session:
        prepared = CaptureService(session, settings).prepare(
            organization_id=reservation.organization_id,
            user_id=reservation.user_id,
            conversation_id=reservation.conversation_id,
            message_id=message_id,
            source_document_id=source_document_id,
        )
        session.expunge_all()
    if not isinstance(prepared, CaptureSnapshot):
        return (*prepared, {})
    from app.agent_capture import service as capture_module

    prepare_ms = (time.perf_counter() - prepare_started) * 1000
    model = (
        capture_module.CaptureModel(settings, llm_provider=llm_provider)
        if llm_provider is not None
        else capture_module.CaptureModel(settings)
    )
    model_started = time.perf_counter()
    provider_ms, write_ms = 0.0, 0.0
    try:
        # No session/transaction or private-user lock remains during this call.
        plan = model.plan(
            organization_id=reservation.organization_id,
            user_id=reservation.user_id,
            conversation_id=reservation.conversation_id,
            content=prepared.content,
        )
        provider_ms = (time.perf_counter() - model_started) * 1000
        write_started = time.perf_counter()
        with sessions() as session:
            coordinator = TurnCoordinator(sessions)
            coordinator.guard(session, reservation)
            result = CaptureService(session, settings, model=model).apply(prepared, plan)
            session.commit()
        write_ms = (time.perf_counter() - write_started) * 1000
    finally:
        if model.last_usage and usage_sink is not None:
            usage_sink.append(model.last_usage)
        logger.info(
            "agent_capture_latency",
            turn_id=str(reservation.turn_id),
            capture_ms=round((time.perf_counter() - started) * 1000, 2),
            provider_ms=round(provider_ms or (time.perf_counter() - model_started) * 1000, 2),
            transaction_ms=[round(prepare_ms, 2), round(write_ms, 2)],
        )
    return (*result, model.last_usage)


def run_interactive(
    coordinator: TurnCoordinator,
    settings: Settings,
    reservation: TurnReservation,
    body: AgentTurnRequest,
    paper_factory: Callable[[Session], Any] | None = None,
    *,
    llm_provider: LLMProvider | None = None,
    supplemental_facts: str = "",
    external_retrieval: bool = True,
    embeddings_provider: EmbeddingsProvider | None = None,
    embedding_usage: DetachedUsage | None = None,
    retrieval_unavailable_note: str = (
        "Vector embedding unavailable: no verified embedding spending receipt for voice."
    ),
) -> dict[str, Any]:
    deferred = DeferredResponder()
    body = body.model_copy(update={"conversation_id": reservation.conversation_id})
    with coordinator.sessions() as session:
        coordinator.guard(session, reservation)
        if not supplemental_facts:
            from app.agent_voice.grounding import read_grounding

            supplemental_facts = read_grounding(
                session,
                settings,
                reservation.organization_id,
                reservation.user_id,
                body.message,
            )
        if body.source_document_id:
            if body.action is not None:
                from app.core.errors import ValidationAppError

                raise ValidationAppError("An upload cannot carry a tool action.")
            uploaded_text(
                session, body.source_document_id, reservation.organization_id, reservation.user_id
            )
    retrieval_started = time.perf_counter()
    from app.core.provider_policy import provider_fail_closed

    allow_fallback = not provider_fail_closed(settings)
    if not external_retrieval:
        vector = PreparedVectorHits(
            [],
            [retrieval_unavailable_note],
            allow_sql_fallback=allow_fallback,
        )
    else:
        try:
            adapter = AgentVectorAdapter(
                settings,
                coordinator.sessions,
                embeddings_provider=embeddings_provider,
                usage_service=embedding_usage,
            )
            hits = adapter.search(
                organization_id=reservation.organization_id,
                user_id=reservation.user_id,
                query=body.message,
                limit=5,
            )
            vector = PreparedVectorHits(hits, adapter.notes, allow_sql_fallback=allow_fallback)
        except Exception:
            vector = PreparedVectorHits(
                [],
                [
                    "Vector retrieval unavailable; policy permits bounded SQL fallback."
                    if allow_fallback
                    else "Vector retrieval unavailable; provider policy refuses fallback."
                ],
                allow_sql_fallback=allow_fallback,
            )
    from app.interactive_agent.classify import classify_turn
    from app.interactive_agent.contracts import AgentCapability

    symbol = (
        (extract_symbol(body.message) or body.symbol)
        if (classify_turn(body.message).capability is AgentCapability.MARKET_AND_PORTFOLIO)
        else None
    )
    quote = (
        PreparedQuote(
            CanonicalPerpetualQuoteReader(
                CanonicalEvidenceService(settings), reservation.organization_id
            ),
            symbol,
        )
        if symbol
        else None
    )
    logger.info(
        "agent_retrieval_latency",
        turn_id=str(reservation.turn_id),
        retrieval_ms=round((time.perf_counter() - retrieval_started) * 1000, 2),
    )
    prepare_started = time.perf_counter()
    with coordinator.sessions() as session:
        coordinator.guard(session, reservation)
        from app.interactive_agent.action_registry import route_action

        action = route_action(body) if body.source_document_id is None else None
        prepare_paper = action is not None and action.name == "paper_trade.prepare_execution"
        service = InteractiveAgentService(
            session,
            settings=settings,
            responder=deferred,
            vector_retriever=vector,
            market_reader=quote,
            paper_execution=paper_factory(session) if prepare_paper and paper_factory else None,
        )
        result = service.handle_turn(
            body,
            organization_id=reservation.organization_id,
            user_id=reservation.user_id,
            ordinary_capture=True,
        )
        warnings = service.pending_reply_warnings
        if deferred.inputs is None:
            row = session.get(ConversationMessage, reservation.turn_id)
            assert row is not None
            row.payload = {
                **row.payload,
                RESERVATION: {
                    **row.payload[RESERVATION],
                    "state": "capture",
                    "response": result.model_dump(mode="json"),
                },
            }
        session.commit()
        revision = transcript_revision(session, reservation.conversation_id)
    coordinator.transaction_ms.append((time.perf_counter() - prepare_started) * 1000)
    if deferred.inputs is not None:
        if supplemental_facts:
            deferred.inputs["factual_context"] = (
                supplemental_facts + "\n" + deferred.inputs["factual_context"]
            )
        responder = (
            ModelConversationalResponder(None, settings, llm_provider=llm_provider)
            if llm_provider is not None
            else ModelConversationalResponder(None, settings)
        )
        model_started = time.perf_counter()
        model_text = responder.compose(**deferred.inputs)
        logger.info(
            "agent_provider_latency",
            turn_id=str(reservation.turn_id),
            provider_ms=round((time.perf_counter() - model_started) * 1000, 2),
        )
        finalize_started = time.perf_counter()
        with coordinator.sessions() as session:
            reservation_row = coordinator.guard(session, reservation, revision=revision)
            assistant = session.get(ConversationMessage, result.assistant_message_id)
            assert assistant is not None
            if model_text != MODEL_REPLY_UNAVAILABLE:
                result.full_reply = model_text
                result.reply = compose_visible_reply(
                    model_text, deferred.inputs["factual_context"], required_warnings=warnings
                )
                result.limitations = [n for n in result.limitations if n != MODEL_REPLY_UNAVAILABLE]
            result.model_usage = [responder.last_usage] if responder.last_usage else []
            if supplemental_facts:
                result.recorded_evidence = deferred.inputs["factual_context"]
            result.capture_status = "unavailable"
            result.capture_error = (
                "Reply saved. Capture is pending; recover or retry capture if interrupted."
            )
            assistant.content = result.reply
            detail = dict(assistant.payload[PAYLOAD_KEY])
            detail.update(full_reply=result.full_reply, model_usage=result.model_usage)
            if supplemental_facts:
                detail["recorded_evidence"] = result.recorded_evidence
            assistant.payload = {**assistant.payload, PAYLOAD_KEY: detail}
            store_capture_receipt(session, result)
            reservation_row.payload = {
                **reservation_row.payload,
                RESERVATION: {
                    **reservation_row.payload[RESERVATION],
                    "state": "capture",
                    "response": result.model_dump(mode="json"),
                },
            }
            session.commit()
        coordinator.transaction_ms.append((time.perf_counter() - finalize_started) * 1000)
    # Durable reply is independently replayable even if capture fails/restarts.
    coordinator.finish(reservation, result.model_dump(mode="json"), state="capture")
    if result.operation is not TurnOperation.REFUSE and body.action is None:
        result.capture_source_message_id = result.user_message_id
        try:
            entries, status, clarification, _usage = capture_in_phases(
                coordinator.sessions,
                settings,
                reservation,
                result.user_message_id,
                body.source_document_id,
                usage_sink=result.model_usage,
                llm_provider=llm_provider,
            )
            result.saved_entries, result.capture_status = entries, status
            result.capture_clarification, result.capture_error = clarification, None
        except Exception as exc:
            logger.warning("agent_capture_failed", error_type=type(exc).__name__)
            result.saved_entries, result.capture_status = [], "failed"
            result.capture_error = (
                "Your reply is saved. Capture failed; retry capture when available."
            )
        with coordinator.sessions() as session:
            row = coordinator.guard(session, reservation)
            store_capture_receipt(session, result)
            row.payload = {
                **row.payload,
                RESERVATION: {
                    **row.payload[RESERVATION],
                    "state": "completed",
                    "response": result.model_dump(mode="json"),
                },
            }
            session.commit()
    return result.model_dump(mode="json")
