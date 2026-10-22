"""Authenticated application WS proxy; no browser provider credential or free-form tools."""

from __future__ import annotations

import asyncio
import contextlib
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from openai import AsyncOpenAI
from pydantic import ValidationError

from app.agent_voice.accounting import VoiceLedger
from app.agent_voice.config import VoiceUnavailableError, load_limits
from app.agent_voice.contracts import VoiceConfig, VoiceStart
from app.agent_voice.live_config import load_live_limits
from app.agent_voice.live_socket import live_voice
from app.agent_voice.service import RealtimeSession
from app.core.config import get_settings
from app.core.dependencies import SettingsDep
from app.core.errors import AppError
from app.db.session import get_session_factory
from app.security.rbac import TraderDep

router = APIRouter(prefix="/agent/voice", tags=["agent-voice"])


@router.get("/config", response_model=VoiceConfig)
def voice_config(tenant: TraderDep, settings: SettingsDep) -> VoiceConfig:
    try:
        limits = (
            load_live_limits(settings) if settings.agent_live_enabled else load_limits(settings)
        )
        if limits.organization_id != str(tenant.organization_id):
            raise VoiceUnavailableError("voice_budget_tenant_mismatch")
    except VoiceUnavailableError as exc:
        return VoiceConfig(
            available=False,
            reason=str(exc),
            provider="gpt_live" if settings.agent_live_enabled else "realtime",
        )
    return VoiceConfig(
        available=True,
        maximum_seconds=limits.maximum_seconds,
        maximum_turns=limits.maximum_turns,
        provider="gpt_live" if settings.agent_live_enabled else "realtime",
    )


router.add_api_websocket_route("/live", live_voice)


@router.websocket("/realtime")
async def realtime_voice(websocket: WebSocket) -> None:
    settings = get_settings()
    if websocket.headers.get("origin") not in settings.cors_origins:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    session: RealtimeSession | None = None
    ledger: VoiceLedger | None = None
    tasks: list[asyncio.Task[None]] = []
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=5)
        if len(raw.encode()) > 12288:
            raise VoiceUnavailableError("voice_start_too_large")
        start = VoiceStart.model_validate(json.loads(raw))
        limits = load_limits(settings)
        if settings.openai_base_url.rstrip("/") != "https://api.openai.com/v1":
            raise VoiceUnavailableError("approved_openai_origin_unavailable")
        ledger = VoiceLedger(
            get_session_factory(), settings, limits, start.access_token, start.conversation_id
        )
        await asyncio.wait_for(asyncio.to_thread(ledger.reserve), timeout=10)
        # Official SDK connection helper has no reconnect callback and max_retries=0.
        async with contextlib.AsyncExitStack() as stack:
            client = await stack.enter_async_context(
                AsyncOpenAI(
                    api_key=settings.openai_api_key,
                    base_url=settings.openai_base_url,
                    max_retries=0,
                    timeout=10,
                )
            )
            upstream = await asyncio.wait_for(
                stack.enter_async_context(
                    client.realtime.connect(
                        model=limits.realtime.model,
                        websocket_connection_options={"max_size": 131072},
                    )
                ),
                timeout=10,
            )
            session = RealtimeSession(ledger, start, upstream, websocket.send_json)
            await session.start_session()

            async def from_browser() -> None:
                while True:
                    message = await websocket.receive_text()
                    if len(message.encode()) > 35000:
                        raise VoiceUnavailableError("voice_event_too_large")
                    if not await session.client(json.loads(message)):
                        return

            async def from_provider() -> None:
                async for event in upstream:
                    await session.provider(event.model_dump(mode="json", exclude_none=True))

            async def authorization() -> None:
                while True:
                    await asyncio.sleep(2)
                    await asyncio.to_thread(ledger.update, lambda _row, _data: None)
                    if session.tool_task and session.tool_task.done():
                        failure = session.tool_task.exception()
                        if failure is not None:
                            raise failure
                    if session.response_request and session.response_request.done():
                        failure = session.response_request.exception()
                        if failure is not None:
                            raise failure

            async with asyncio.timeout(limits.maximum_seconds):
                tasks = [
                    asyncio.create_task(from_browser()),
                    asyncio.create_task(from_provider()),
                    asyncio.create_task(authorization()),
                ]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
    except WebSocketDisconnect:
        pass
    except (AppError, VoiceUnavailableError, ValidationError, ValueError, TimeoutError) as exc:
        reason = str(exc) if isinstance(exc, VoiceUnavailableError) else "voice_session_unavailable"
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_json({"type": "session.error", "reason": reason})
    except Exception:
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.send_json(
                {"type": "session.error", "reason": "voice_provider_unavailable"}
            )
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if session:
            await session.stop()
        elif ledger:
            await asyncio.to_thread(ledger.close)
        with contextlib.suppress(RuntimeError):
            await websocket.close()
