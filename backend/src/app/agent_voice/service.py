"""Responsive realtime protocol with canonical Agent delegation and server admission."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from app.agent_voice.accounting import VoiceLedger
from app.agent_voice.config import VoiceUnavailableError
from app.agent_voice.contracts import (
    VoiceAudio,
    VoiceCommit,
    VoiceEnd,
    VoiceInterrupt,
    VoiceStart,
    VoiceToolInput,
    VoiceTruncate,
)
from app.agent_voice.grounding import read_grounding
from app.agent_voice.protocol import TOOL_NAME, checked_audio, create_response, session_update
from app.agent_voice.retrieval import (
    VoiceEmbeddingsProvider,
    VoiceEmbeddingUsage,
    embedding_limitation,
)
from app.interactive_agent.contracts import AgentTurnRequest, AgentTurnResult
from app.interactive_agent.turn_runtime import run_interactive
from app.providers.base import ProviderHealth, ProviderStatus
from app.providers.factory import resolve_providers
from app.providers.llm import LLMCompletionRequest, LLMCompletionResult, LLMProvider
from app.services.turn_coordinator import TurnCoordinator


class VoiceReasoningProvider:
    """Preserves existing ModelRouter/capture and limits actual secondary attempts."""

    def __init__(self, provider: LLMProvider, ledger: VoiceLedger):
        self.provider, self.ledger = provider, ledger
        self.name, self.kind = provider.name, provider.kind

    def status(self) -> Any:
        return ProviderStatus(
            name=self.name,
            kind=self.kind,
            health=ProviderHealth.DEGRADED,
            is_mock=False,
            using_fallback=False,
            detail="Bounded configured route; live availability has not been probed.",
        )

    def complete(self, request: LLMCompletionRequest) -> LLMCompletionResult:
        index = self.ledger.reasoning_attempt(request)
        result = self.provider.complete(request)
        price = self.ledger.limits.reasoning
        if (
            result.model != price.model
            or result.fallback_used
            or result.input_tokens > price.maximum_input_tokens
            or result.output_tokens > self.ledger.settings.agent_max_output_tokens
        ):
            raise VoiceUnavailableError("voice_reasoning_response_outside_receipt")
        self.ledger.reasoning_done(index, result)
        return result


def shared_turn(ledger: VoiceLedger, key: str, body: dict[str, Any]) -> dict[str, Any]:
    """Independent SQL phases; never execute provider work on the ASGI loop."""
    request = AgentTurnRequest.model_validate(body)
    with ledger.sessions() as session:
        tenant = ledger.principal(session)
        facts = read_grounding(
            session, ledger.settings, tenant.organization_id, tenant.user_id, request.message
        )
    coordinator = TurnCoordinator(ledger.sessions)
    providers = resolve_providers(ledger.settings)
    provider = VoiceReasoningProvider(providers.llm, ledger)
    limitation = embedding_limitation(ledger)
    embeddings = (
        VoiceEmbeddingsProvider(providers.embeddings, ledger, key) if limitation is None else None
    )
    result = coordinator.run(
        channel="interactive",
        key=UUID(key),
        body=body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        conversation_id=request.conversation_id,
        strategy_id=request.strategy_id,
        work=lambda reservation: run_interactive(
            coordinator,
            ledger.settings,
            reservation,
            request,
            llm_provider=provider,
            supplemental_facts=facts,
            external_retrieval=embeddings is not None,
            embeddings_provider=embeddings,
            embedding_usage=VoiceEmbeddingUsage(ledger, key) if embeddings else None,
            retrieval_unavailable_note=limitation or "Vector embedding unavailable.",
        ),
    )
    return AgentTurnResult.model_validate(result).model_dump(mode="json")


class RealtimeSession:
    def __init__(
        self,
        ledger: VoiceLedger,
        start: VoiceStart,
        upstream: Any,
        send: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        turn: Callable[[VoiceLedger, str, dict[str, Any]], dict[str, Any]] = shared_turn,
    ):
        self.ledger, self.start, self.upstream, self.send, self.turn = (
            ledger,
            start,
            upstream,
            send,
            turn,
        )
        self.ready = False
        self.response_active = False
        self.response_grounded = False
        self.audio_item: str | None = None
        self.audio_bytes = 0
        self.context_bytes = len(json.dumps(session_update(ledger.limits)).encode())
        self.tool_task: asyncio.Task[None] | None = None
        self.accepts: dict[str, asyncio.Event] = {}
        self.response_finished = asyncio.Event()
        self.response_finished.set()
        self.queued_speech = False
        self.closed = False
        self.interruption = 0
        self.send_lock = asyncio.Lock()
        self.response_request: asyncio.Task[None] | None = None
        self.response_statuses: dict[str, str] = {}

    async def start_session(self) -> None:
        await self._provider_send(session_update(self.ledger.limits))

    async def _provider_send(self, event: dict[str, Any]) -> None:
        async with self.send_lock:
            if self.closed:
                raise VoiceUnavailableError("voice_session_closed")
            await asyncio.wait_for(self.upstream.send(event), timeout=10)

    async def _respond(self, *, grounded: bool = False) -> None:
        if self.response_active:
            raise VoiceUnavailableError("overlapping_provider_response")
        # Reserve the whole model output as future context, including tool arguments.
        # Audio output tokenization is not inferred from PCM duration.
        self.context_bytes += self.ledger.limits.maximum_output_tokens
        self._context_limit()
        await asyncio.to_thread(self.ledger.response)
        self.response_active, self.response_grounded = True, grounded
        self.response_finished.clear()
        await self._provider_send(create_response(self.ledger.limits, grounded=grounded))

    async def client(self, event: dict[str, Any]) -> bool:
        kind = event.get("type")
        if kind == "session.end":
            VoiceEnd.model_validate(event)
            return False
        if kind == "turn.accept":
            if set(event) != {"type", "request_key"}:
                raise VoiceUnavailableError("invalid_turn_receipt")
            accepted = self.accepts.get(str(event.get("request_key")))
            if accepted is None:
                raise VoiceUnavailableError("unexpected_turn_receipt")
            accepted.set()
            return True
        if not self.ready:
            raise VoiceUnavailableError("voice_session_not_ready")
        if kind == "audio.append":
            body = VoiceAudio.model_validate(event)
            audio = checked_audio(body.audio, 16384)
            await asyncio.to_thread(self.ledger.audio, len(audio))
            # Verified audio-token rate, not user supplied usage.
            self.context_bytes += (
                int(
                    Decimal(len(audio))
                    / Decimal(48000)
                    * self.ledger.limits.realtime.maximum_audio_input_tokens_per_second
                )
                + 1
            )
            self._context_limit()
            await self._provider_send({"type": "input_audio_buffer.append", "audio": body.audio})
        elif kind == "audio.commit":
            VoiceCommit.model_validate(event)
            await self._provider_send({"type": "input_audio_buffer.commit"})
            if self.tool_task and not self.tool_task.done():
                self.queued_speech = True
            else:
                await self._respond()
        elif kind == "response.interrupt":
            interruption = VoiceInterrupt.model_validate(event)
            await self.interrupt(interruption.item_id, interruption.audio_end_ms)
        elif kind == "playback.truncate":
            truncation = VoiceTruncate.model_validate(event)
            await self.truncate(truncation.item_id, truncation.audio_end_ms)
        else:
            raise VoiceUnavailableError("unsupported_client_event")
        return True

    def _context_limit(self) -> None:
        if self.context_bytes > self.ledger.limits.realtime.maximum_input_tokens:
            raise VoiceUnavailableError("voice_realtime_context_cap")

    async def interrupt(self, item_id: str | None = None, audio_end_ms: int = 0) -> None:
        self.interruption += 1
        if self.response_active:
            await self._provider_send({"type": "response.cancel"})
        await self.truncate(item_id, audio_end_ms)
        await self.send({"type": "playback.clear"})

    async def truncate(self, item_id: str | None, audio_end_ms: int) -> None:
        if item_id is not None:
            if item_id != self.audio_item or audio_end_ms > self.audio_bytes // 48:
                raise VoiceUnavailableError("invalid_playback_position")
            await self._provider_send(
                {
                    "type": "conversation.item.truncate",
                    "item_id": item_id,
                    "content_index": 0,
                    "audio_end_ms": audio_end_ms,
                }
            )

    async def provider(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "session.updated":
            effective = event.get("session", {})
            if effective.get("model") != self.ledger.limits.realtime.model:
                raise VoiceUnavailableError("provider_session_route_mismatch")
            if effective.get("max_output_tokens") != self.ledger.limits.maximum_output_tokens:
                raise VoiceUnavailableError("provider_output_limit_not_confirmed")
            audio = effective.get("audio", {})
            incoming, outgoing = audio.get("input", {}), audio.get("output", {})
            detection = incoming.get("turn_detection", {})
            if (
                detection.get("type") != "server_vad"
                or detection.get("create_response") is not False
                or detection.get("interrupt_response") is not False
                or incoming.get("transcription") is not None
                or incoming.get("format") != {"type": "audio/pcm", "rate": 24000}
                or outgoing.get("format") != {"type": "audio/pcm", "rate": 24000}
                or effective.get("truncation") != "disabled"
            ):
                raise VoiceUnavailableError("provider_audio_limits_not_confirmed")
            await asyncio.to_thread(self.ledger.activate)
            self.ready = True
            await self.send({"type": "session.ready", "session_id": str(self.ledger.id)})
        elif kind == "response.created":
            await asyncio.to_thread(self.ledger.response_started, str(event["response"]["id"]))
        elif kind == "response.done":
            await asyncio.to_thread(self.ledger.response_done, event["response"])
            self.response_active = False
            self.response_statuses[str(event["response"]["id"])] = str(
                event["response"].get("status", "unknown")
            )
            self.response_finished.set()
            await self.send({"type": "response.done"})
        elif kind == "input_audio_buffer.speech_started":
            await self.interrupt()
            await self.send({"type": "speech.started"})
        elif kind == "input_audio_buffer.speech_stopped":
            if self.tool_task and not self.tool_task.done():
                self.queued_speech = True
            else:
                # The upstream reader must remain free to consume cancellation.
                if self.response_request and not self.response_request.done():
                    raise VoiceUnavailableError("overlapping_voice_input")

                async def respond_after_cancel() -> None:
                    await asyncio.wait_for(self.response_finished.wait(), timeout=10)
                    await self._respond()

                self.response_request = asyncio.create_task(respond_after_cancel())
            await self.send({"type": "speech.stopped"})
        elif kind == "response.function_call_arguments.done":
            if event.get("name") != TOOL_NAME or self.response_grounded:
                raise VoiceUnavailableError("unsupported_provider_tool")
            if self.tool_task and not self.tool_task.done():
                raise VoiceUnavailableError("overlapping_provider_tool")
            try:
                body = VoiceToolInput.model_validate(json.loads(event.get("arguments", "")))
            except (ValueError, ValidationError) as exc:
                raise VoiceUnavailableError("invalid_provider_tool_arguments") from exc
            self.tool_task = asyncio.create_task(
                self._delegate(str(event["call_id"]), body, str(event.get("response_id", "")))
            )
        elif kind in {"response.output_audio.delta", "response.output_audio_transcript.delta"}:
            if not self.response_grounded:
                raise VoiceUnavailableError("ungrounded_provider_audio_refused")
            if kind == "response.output_audio.delta":
                delta = str(event.get("delta", ""))
                decoded = checked_audio(delta, 65536, maximum_bytes=65536)
                item_id = str(event["item_id"])
                if self.audio_item != item_id:
                    self.audio_item, self.audio_bytes = item_id, 0
                self.audio_bytes += len(decoded)
                await self.send({"type": "audio.delta", "audio": delta, "item_id": item_id})
            else:
                text = str(event.get("delta", ""))
                await self.send({"type": "reply.delta", "text": text})
            self._context_limit()
        elif kind == "error":
            # Never export raw provider errors or treat an error as non-billable.
            raise VoiceUnavailableError("realtime_provider_error")

    async def _delegate(self, call_id: str, request: VoiceToolInput, response_id: str) -> None:
        # SDK documents arguments.done on cancellation/incompletion too. Such a
        # partial utterance must never become an automatically saved Agent record.
        await asyncio.wait_for(self.response_finished.wait(), timeout=10)
        if self.response_statuses.get(response_id) != "completed":
            await self.send({"type": "input.interrupted"})
            return
        body = AgentTurnRequest(
            message=request.message,
            conversation_id=self.start.conversation_id,
            symbol=self.start.context.symbol,
            strategy_id=self.start.context.strategy_id,
        ).model_dump(mode="json")
        generation = self.interruption
        key, fresh = await asyncio.to_thread(self.ledger.tool, call_id, body)
        if not fresh:
            raise VoiceUnavailableError("voice_tool_already_pending_no_automatic_retry")
        accepted = asyncio.Event()
        self.accepts[key] = accepted
        await self.send({"type": "turn.pending", "request_key": key, "body": body})
        try:
            await asyncio.wait_for(accepted.wait(), timeout=10)
            result = await asyncio.to_thread(self.turn, self.ledger, key, body)
            # The original shared record survives disconnect/cancellation even when
            # this coroutine stopped waiting for the thread's final acknowledgement.
            await asyncio.to_thread(self.ledger.tool_done, call_id, result)
            await self.send({"type": "turn.result", "request_key": key, "result": result})
            serialized = json.dumps(
                {"canonical_reply": result["reply"], "limitations": result.get("limitations", [])}
            )
            self.context_bytes += len(request.message.encode()) + len(serialized.encode())
            self._context_limit()
            await asyncio.wait_for(self.response_finished.wait(), timeout=10)
            await self._provider_send(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": serialized,
                    },
                }
            )
            if self.queued_speech:
                self.queued_speech = False
                await self._respond()
            elif generation == self.interruption:
                await self._respond(grounded=True)
        except Exception:
            await self.send({"type": "turn.uncertain", "request_key": key})
            raise
        finally:
            self.accepts.pop(key, None)

    async def stop(self) -> None:
        self.closed = True
        if self.tool_task and not self.tool_task.done():
            self.tool_task.cancel()
            await asyncio.gather(self.tool_task, return_exceptions=True)
        if self.response_request:
            self.response_request.cancel()
            await asyncio.gather(self.response_request, return_exceptions=True)
        await asyncio.to_thread(self.ledger.close)
