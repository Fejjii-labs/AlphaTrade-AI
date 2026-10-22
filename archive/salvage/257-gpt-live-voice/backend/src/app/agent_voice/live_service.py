"""Full-duplex Live transport; all private work uses the existing Agent executor."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from pydantic import Field

from app.agent_voice.config import VoiceUnavailableError
from app.agent_voice.contracts import VoiceAudio, VoiceEnd, VoiceStart
from app.agent_voice.live_accounting import LiveLedger
from app.agent_voice.live_protocol import context_parts, session_start
from app.agent_voice.protocol import checked_audio
from app.agent_voice.service import shared_turn
from app.interactive_agent.contracts import AgentTurnRequest
from app.schemas.common import StrictModel


class TranscriptFragment(StrictModel):
    event_id: str = Field(min_length=1, max_length=128)
    delta: str = Field(max_length=8000)
    start_ms: int = Field(ge=0, le=3600000)
    end_ms: int = Field(ge=0, le=3600000)


class LiveSession:
    def __init__(
        self,
        ledger: LiveLedger,
        start: VoiceStart,
        upstream: Any,
        send: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        turn: Any = shared_turn,
    ):
        self.ledger, self.start, self.upstream, self.send, self.turn = (
            ledger,
            start,
            upstream,
            send,
            turn,
        )
        self.ready = False
        self.closing = False
        self.close_requested = False
        self.close_started: float | None = None
        self.transcript_changed = asyncio.Event()
        self.failure: VoiceUnavailableError | None = None
        self.finalized = asyncio.Event()
        self.provider_id: str | None = None
        self.output_muted = False
        self.interruption = 0
        self.fragments: list[dict[str, Any]] = []
        self.fragment_ids: set[str] = set()
        self.consumed: set[str] = set()
        self.delegations: set[str] = set()
        self.tool_task: asyncio.Task[None] | None = None
        self.tasks: list[asyncio.Task[None]] = []
        self.accepts: dict[str, asyncio.Event] = {}
        self.append_events: set[str] = set()
        self.send_lock = asyncio.Lock()

    async def start_session(self) -> None:
        await self._provider_send(
            session_start(await asyncio.wait_for(asyncio.to_thread(self.ledger.history), timeout=2))
        )

    async def _provider_send(self, event: dict[str, Any]) -> None:
        async with self.send_lock:
            await asyncio.wait_for(self.upstream.send(event), timeout=5)

    async def append(self, kind: str, content: str, delegation_id: str | None) -> None:
        for part in context_parts(content):
            if self.closing:
                return
            if len(self.append_events) >= 128:
                raise VoiceUnavailableError("live_context_ack_cap")
            event_id = str(uuid4())
            self.append_events.add(event_id)
            await self._provider_send(
                {
                    "type": kind,
                    "event_id": event_id,
                    "delegation_id": delegation_id,
                    "content": part,
                }
            )

    async def client(self, event: dict[str, Any]) -> bool:
        kind = event.get("type")
        if kind == "session.end":
            VoiceEnd.model_validate(event)
            return False
        if kind == "turn.accept":
            if (
                set(event) != {"type", "request_key"}
                or event.get("request_key") not in self.accepts
            ):
                raise VoiceUnavailableError("invalid_turn_receipt")
            self.accepts[event["request_key"]].set()
            return True
        if not self.ready or self.closing:
            raise VoiceUnavailableError("voice_session_not_ready")
        if kind == "audio.append":
            audio = VoiceAudio.model_validate(event)
            decoded = checked_audio(audio.audio, 16384)
            await asyncio.to_thread(self.ledger.audio, len(decoded))
            await self._provider_send({"type": "session.input_audio.append", "audio": audio.audio})
        elif kind == "response.interrupt" and set(event) == {"type"}:
            self.interruption += 1
            self.output_muted = True
            await self.send({"type": "playback.clear", "muted": True})
            await self.append(
                "session.instructions.append",
                "The user interrupted. Stop speaking and listen. "
                "Output is locally muted until the user resumes audio. "
                "Do not repeat the discarded speech.",
                None,
            )
        elif kind == "playback.resume" and set(event) == {"type"}:
            self.output_muted = False
            # This is an explicit application playback choice, not a provider ack
            # or claim that the model forgot discarded audio.
            await self.send({"type": "playback.ready"})
        else:
            raise VoiceUnavailableError("unsupported_client_event")
        return True

    async def provider(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if self.closing and kind not in {"session.usage.updated", "session.closed", "error"}:
            return
        if kind == "session.started":
            resolved = event.get("session", {})
            if (
                self.ready
                or resolved.get("model") != self.ledger.limits.voice_model
                or resolved.get("delegation", {}).get("type") != "client"
                or resolved.get("audio", {}).get("format") != {"type": "audio/pcm", "rate": 24000}
                or resolved.get("store") is not False
            ):
                raise VoiceUnavailableError("live_session_config_mismatch")
            self.provider_id = resolved["id"]
            await asyncio.to_thread(self.ledger.started, self.provider_id)
            self.ready = True
            await self.send({"type": "session.ready", "session_id": str(self.ledger.id)})
        elif kind in {"session.input_transcript.delta", "session.output_transcript.delta"}:
            if not self.ready:
                raise VoiceUnavailableError("live_transcript_before_start")
            fragment = TranscriptFragment.model_validate(
                {name: event[name] for name in ("event_id", "delta", "start_ms", "end_ms")}
            ).model_dump()
            if fragment["end_ms"] < fragment["start_ms"]:
                raise VoiceUnavailableError("invalid_live_transcript_interval")
            if fragment["event_id"] in self.fragment_ids:
                return
            self.fragment_ids.add(fragment["event_id"])
            role = "user" if kind == "session.input_transcript.delta" else "assistant"
            await asyncio.to_thread(self.ledger.transcript, role, fragment)
            if role == "user":
                self.fragments.append(fragment)
                self.transcript_changed.set()
                self.interruption += 1
                await self.send({"type": "playback.clear", "muted": False})
            if not self.closing:
                await self.send(
                    {
                        "type": "input.delta" if role == "user" else "reply.delta",
                        "text": fragment["delta"],
                        "start_ms": fragment["start_ms"],
                        "end_ms": fragment["end_ms"],
                    }
                )
        elif kind == "session.delegation.created":
            delegation = event.get("delegation", {})
            identifier = delegation.get("id")
            offset = event.get("offset_ms")
            if (
                not self.ready
                or delegation.get("target") != "client"
                or not isinstance(identifier, str)
                or not identifier
                or len(identifier) > 128
                or isinstance(offset, bool)
                or not isinstance(offset, int)
                or offset < 0
            ):
                raise VoiceUnavailableError("invalid_live_delegation")
            if identifier in self.delegations:
                return
            if self.closing:
                return
            if len(self.delegations) >= self.ledger.limits.maximum_turns:
                raise VoiceUnavailableError("voice_turn_cap_exhausted")
            self.delegations.add(identifier)
            previous = self.tool_task
            self.tool_task = asyncio.create_task(
                self._delegate(identifier, offset, self.interruption, previous)
            )
            self.tasks.append(self.tool_task)
        elif kind == "session.output_audio.delta":
            if not self.ready:
                raise VoiceUnavailableError("live_audio_before_start")
            delta = event.get("delta")
            if not isinstance(delta, str):
                raise VoiceUnavailableError("invalid_live_audio")
            checked_audio(delta, 65536, maximum_bytes=65536)
            if not self.closing and not self.output_muted:
                await self.send(
                    {"type": "audio.delta", "audio": delta, "item_id": self.provider_id}
                )
        elif kind == "session.usage.updated":
            await asyncio.to_thread(self.ledger.usage, event)
        elif kind == "session.closed":
            await asyncio.to_thread(self.ledger.usage, event, final=True)
            self.closing = True
            self.finalized.set()
        elif kind in {
            "session.instructions.appended",
            "session.thinking.appended",
            "session.commentary.appended",
        }:
            self.append_events.discard(str(event.get("client_event_id")))
        elif kind in {"error", "response.event", "session.updated"}:
            raise VoiceUnavailableError("live_provider_error")

    async def _delegate(
        self, identifier: str, offset: int, revision: int, previous: asyncio.Task[None] | None
    ) -> None:
        if previous:
            await previous
        if self.closing or revision != self.interruption:
            return

        # Live has no completed-turn event. Wait briefly only if no text is
        # available yet; this never treats silence or an offset as completeness.
        def observed() -> list[dict[str, Any]]:
            return [
                f
                for f in self.fragments
                if f["event_id"] not in self.consumed and f["start_ms"] <= offset
            ]

        fragments = observed()
        if not fragments:
            self.transcript_changed.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.transcript_changed.wait(), timeout=2)
            if self.closing:
                return
            fragments = observed()
            # A later request is not a late transcript for this delegation.
            if any(f["start_ms"] > offset for f in self.fragments):
                fragments = []
            revision = self.interruption
        message = "".join(f["delta"] for f in fragments)
        if not message.strip():
            await self.append(
                "session.commentary.append",
                "The request transcript is unavailable. "
                "Please repeat it or use the typed conversation.",
                identifier,
            )
            return
        if len(message) > 8000:
            raise VoiceUnavailableError("live_delegation_transcript_cap")
        self.consumed.update(f["event_id"] for f in fragments)
        body = AgentTurnRequest(
            message=message,
            conversation_id=self.start.conversation_id,
            symbol=self.start.context.symbol,
            strategy_id=self.start.context.strategy_id,
        ).model_dump(mode="json")
        key, fresh = await asyncio.to_thread(self.ledger.tool, identifier, body)
        if not fresh:
            raise VoiceUnavailableError("voice_tool_already_pending_no_automatic_retry")
        accepted = asyncio.Event()
        self.accepts[key] = accepted
        await self.send({"type": "turn.pending", "request_key": key, "body": body})
        try:
            await asyncio.wait_for(accepted.wait(), timeout=10)
            result = await asyncio.to_thread(self.turn, self.ledger, key, body)
            await asyncio.to_thread(self.ledger.tool_done, identifier, result)
            if not self.closing:
                await self.send({"type": "turn.result", "request_key": key, "result": result})
            if result.get("capture_status") == "unavailable" and "pending" in str(
                result.get("capture_error")
            ):
                raise VoiceUnavailableError("voice_canonical_capture_pending")
            if self.closing:
                return
            content = "Canonical Agent result (transcript may be incomplete):\n"
            limitations = result.get("limitations", [])
            if limitations:
                content += "Limitations: " + "; ".join(limitations) + "\n"
            content += result["reply"]
            if len(content.encode()) > 24000:
                raise VoiceUnavailableError("live_result_context_cap")
            # Discard superseded context from the voice model entirely: even a
            # thinking append can influence later spoken answers. The canonical
            # result remains durable and visible in its original conversation.
            if revision == self.interruption and not self.output_muted:
                await self.append("session.commentary.append", content, identifier)
        except Exception:
            if not self.closing:
                await self.send({"type": "turn.uncertain", "request_key": key})
            raise
        finally:
            self.accepts.pop(key, None)

    async def request_close(self) -> None:
        if self.close_requested or self.finalized.is_set():
            return
        self.closing = True
        self.close_requested = True
        self.close_started = asyncio.get_running_loop().time()
        await self._provider_send({"type": "session.close"})

    async def stop(self) -> None:
        self.closing = True
        for task in self.tasks:
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        # Cancelling a to_thread wait does not cancel an admitted canonical turn.
        # TurnCoordinator and original body/key remain the recovery authority.
        await asyncio.to_thread(self.ledger.close)
