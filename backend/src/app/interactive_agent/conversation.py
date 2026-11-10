"""Conversational reply for an agent turn.

The existing model router writes the prose. That prose is not confirmation
and cannot change a proposal, journal row, or strategy version.
"""

from __future__ import annotations

import json
import re
import uuid
from decimal import Decimal
from typing import Any, Protocol

import structlog
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.prompt_registry import load_prompt_record
from app.interactive_agent.presentation import readable_number
from app.providers.factory import resolve_providers
from app.providers.llm import LLMMessage, LLMProvider, OpenAILLMProvider
from app.schemas.model_routing import (
    ModelCallerScope,
    ModelContextScope,
    ModelResourceType,
    ModelRoutingPurpose,
    ModelTaskRequest,
)
from app.services.model_call_telemetry import ModelCallTelemetryService
from app.services.model_router import ModelRouter

logger = structlog.get_logger(__name__)

MODEL_REPLY_UNAVAILABLE = "Conversational model reply is unavailable."
_FACTS_HEADER = "Recorded facts (not a confirmation):"
_REPLY_LIMIT = 4000
_PROSE_LIMIT = 3500
_FULL_REPLY_LIMIT = 16000
_SHORTENED = "\n\nFurther explanation is available in Stored evidence."
_STORAGE_SHORTENED = (
    "\n\nExplanation reached its storage limit; further model prose was not retained."
)
# Treat source markers/Markdown links as indivisible, including their punctuation.
_CITATION = r"\[[^\]\n]*\](?:\((?:[^()\n]|\([^()\n]*\))*\))?|【[^】\n]*】"
_PROSE_BOUNDARY = re.compile(rf"(?:{_CITATION})|[.!?][\"\u201d\u2019')]*(?=\s|$)")
_SOURCE_MARKER = re.compile(_CITATION)
_ABBREVIATIONS = frozenset({"e.g.", "i.e.", "etc.", "vs.", "mr.", "mrs.", "dr.", "fig."})

_SYSTEM_PROMPT = load_prompt_record("interactive_agent_system")
_SYSTEM = _SYSTEM_PROMPT.text


class ConversationalResponder(Protocol):
    """Produces assistant prose. It must not mutate domain records."""

    def compose(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        message: str,
        factual_context: str,
        history: tuple[LLMMessage, ...] = (),
    ) -> str: ...


class ModelConversationalResponder:
    """Route one general-agent completion through the existing model router."""

    def __init__(
        self,
        session: Session | None,
        settings: Settings,
        *,
        llm_provider: LLMProvider | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._llm_provider = llm_provider
        self.last_usage: dict[str, Any] = {}

    def compose(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID,
        message: str,
        factual_context: str,
        history: tuple[LLMMessage, ...] = (),
    ) -> str:
        try:
            llm = self._llm_provider or resolve_providers(self._settings).llm
            if self._session is not None and isinstance(llm, OpenAILLMProvider):
                logger.warning("interactive_agent_model_requires_detached_phase")
                return MODEL_REPLY_UNAVAILABLE
            router = ModelRouter.from_settings(
                llm,
                self._settings.model_copy(
                    update={
                        "llm_tier_a_model": self._settings.agent_reasoning_model,
                        "llm_tier_b_model": self._settings.agent_reasoning_model,
                        "model_router_fail_closed": True,
                    }
                ),
                # Coordinated HTTP turns supply an independent session factory.
                telemetry=ModelCallTelemetryService(self._session),
            )
            result = router.complete(
                ModelTaskRequest(
                    purpose=ModelRoutingPurpose.GENERAL_AGENT_SYNTHESIS,
                    context=ModelContextScope(
                        organization_id=organization_id,
                        user_id=user_id,
                        resource_type=ModelResourceType.CONVERSATION,
                        resource_id=conversation_id,
                        purpose=ModelRoutingPurpose.GENERAL_AGENT_SYNTHESIS,
                    ),
                    correlation_id=_correlation("reply"),
                    caller_organization_id=organization_id,
                    caller_user_id=user_id,
                    caller_scope=ModelCallerScope.USER,
                    caller_resource_type=ModelResourceType.CONVERSATION,
                    caller_resource_id=conversation_id,
                    temperature=0.0,
                    reasoning_effort=self._settings.agent_reasoning_effort,
                    max_output_tokens=self._settings.agent_max_output_tokens,
                ),
                [
                    LLMMessage(role="system", content=_SYSTEM),
                    *history,
                    LLMMessage(
                        role="user",
                        content=(
                            f"User message:\n{message.strip()}\n\n"
                            "Stored facts. Repeat unavailable or stale exactly. "
                            "Do not invent prices. Do not claim a record was confirmed.\n"
                            f"{factual_context[:16000]}"
                        ),
                    ),
                ],
            )
        except Exception:
            logger.warning("interactive_agent_model_reply_unavailable")
            return MODEL_REPLY_UNAVAILABLE
        logger.info(
            "interactive_agent_model_call",
            conversation_id=str(conversation_id),
            requested_model=result.decision.selected_model,
            resolved_model=result.resolved_model,
            fallback_used=result.fallback_used,
            unavailable=result.unavailable,
        )
        self.last_usage = {
            "model": result.resolved_model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "latency_ms": result.total_latency_ms,
            "cost": str(result.total_cost) if result.cost_source.value != "unavailable" else None,
            "cost_source": result.cost_source.value,
            "fallback_used": result.fallback_used,
            "unavailable": result.unavailable,
        }
        if result.unavailable or result.mutation_allowed or result.fallback_used:
            return MODEL_REPLY_UNAVAILABLE
        text = _prose(result.content)
        if not text:
            return MODEL_REPLY_UNAVAILABLE
        return text


def compose_visible_reply(
    model_text: str, factual: str, *, required_warnings: tuple[str, ...] = ()
) -> str:
    """Reserve prose space; the full evidence is stored separately in the transcript payload."""
    prose = present_prose(model_text.strip()) or MODEL_REPLY_UNAVAILABLE
    warnings = list(required_warnings)
    if re.search(r"(?:is|freshness|quality|=)\s*stale\b", factual, re.I):
        warnings.append("Stored evidence is stale; it cannot establish a current price.")
    if re.search(
        r"(?:is|freshness|quality|=)\s*(?:unavailable|missing|incomplete)\b", factual, re.I
    ) and not any(warning.startswith("Missing evidence:") for warning in required_warnings):
        warnings.append("Stored evidence is unavailable or incomplete; do not infer missing facts.")
    if "Current market conditions are unknown." in factual:
        warnings.append("Current market conditions are unknown.")
    if "Document guidance is reference data" in factual:
        warnings.append(
            "Document guidance is reference data; "
            "these passages do not establish approved settings."
        )
    warnings = list(dict.fromkeys(warnings))
    warning_text = "\n\n" + " ".join(warnings) if warnings else ""
    header = f"\n\n{_FACTS_HEADER}\n"
    lead = _bounded_prose(
        prose, limit=min(_PROSE_LIMIT, _REPLY_LIMIT - len(warning_text) - len(header) - 150)
    )
    absent = [warning for warning in warnings if warning not in lead]
    if absent:
        lead += "\n\n" + " ".join(absent)
    lead = re.sub(r"\s*\[K\d+(?:\s*,\s*K\d+)*\]", "", lead)
    budget = _REPLY_LIMIT - len(lead) - len(header)
    facts = factual.strip()
    if len(facts) > budget:
        note = "\nEvidence excerpt; full stored evidence is available in details."
        facts = facts[: budget - len(note)] + note
    return lead + header + facts


def _bounded_prose(text: str, *, limit: int = _PROSE_LIMIT, notice: str = _SHORTENED) -> str:
    """Retain complete sentences with attached citations within the prose budget."""
    text = text.strip()
    if len(text) <= limit:
        return text
    budget = limit - len(notice)
    end = 0
    for boundary in _PROSE_BOUNDARY.finditer(text):
        if boundary.start() >= budget:
            break
        if boundary.group().startswith(("[", "【")):
            continue
        word = text[: boundary.end()].rsplit(maxsplit=1)[-1].lower()
        if word in _ABBREVIATIONS:
            continue
        candidate = boundary.end()
        # A sentence followed by citations is one unit. If any source marker
        # crosses the budget, omit the sentence rather than leaving it uncited.
        while True:
            following = candidate
            while following < len(text) and text[following].isspace():
                following += 1
            source = _SOURCE_MARKER.match(text, following)
            if source is None:
                break
            candidate = source.end()
        if candidate > budget:
            break
        end = candidate
    return text[:end].rstrip() + notice if end else notice.strip()


def _prose(content: str) -> str:
    text = content.strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return _bounded_prose(
                text,
                limit=_FULL_REPLY_LIMIT,
                notice=_STORAGE_SHORTENED,
            )
        if isinstance(payload, dict):
            summary = payload.get("summary")
            if isinstance(summary, str) and summary.strip():
                return _bounded_prose(
                    summary,
                    limit=_FULL_REPLY_LIMIT,
                    notice=_STORAGE_SHORTENED,
                )
    return _bounded_prose(
        text,
        limit=_FULL_REPLY_LIMIT,
        notice=_STORAGE_SHORTENED,
    )


def present_prose(text: str) -> str:
    """Keep technical identities in the unchanged full reply/evidence, not prose."""
    text = re.sub(
        r"\b(?:content[_ ]?hash|hash)\s*[=:]?\s*[a-f0-9]{64}\b",
        "hash recorded in Stored evidence",
        text,
        flags=re.I,
    )
    text = re.sub(r"\b[a-f0-9]{64}\b", "the stored hash", text, flags=re.I)
    text = re.sub(
        r"(?<![\w./])(-?\d+\.\d{7,})(?!\w|\.\d)",
        lambda match: readable_number(Decimal(match.group(1)), places=6),
        text,
    )
    return re.sub(
        r"\b[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\b",
        "the linked record",
        text,
        flags=re.I,
    )


def _correlation(stage: str) -> str:
    from app.services.turn_context import current_turn

    turn = current_turn()
    return f"{turn.turn_id}:{stage}" if turn else str(uuid.uuid4())
