"""Authenticated Live proxy with one provider reader and bounded finalization."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any, cast

from fastapi import WebSocket, WebSocketDisconnect
from openai import AsyncOpenAI
from openai.types.websocket_connection_options import WebSocketConnectionOptions
from pydantic import ValidationError

from app.agent_voice.config import VoiceUnavailableError
from app.agent_voice.contracts import VoiceStart
from app.agent_voice.live_accounting import LiveLedger
from app.agent_voice.live_config import CLOSE_SECONDS, load_live_limits
from app.agent_voice.live_service import LiveSession
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_session_factory


async def live_voice(websocket: WebSocket) -> None:
    settings = get_settings()
    if websocket.headers.get("origin") not in settings.cors_origins:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    ledger: LiveLedger | None = None
    session: LiveSession | None = None
    tasks: list[asyncio.Task[None]] = []
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=5)
        if len(raw.encode()) > 12288:
            raise VoiceUnavailableError("voice_start_too_large")
        start = VoiceStart.model_validate(json.loads(raw))
        limits = load_live_limits(settings)
        if settings.openai_base_url.rstrip("/") != "https://api.openai.com/v1":
            raise VoiceUnavailableError("approved_openai_origin_unavailable")
        ledger = LiveLedger(
            get_session_factory(), settings, limits, start.access_token, start.conversation_id
        )
        await asyncio.wait_for(asyncio.to_thread(ledger.reserve), timeout=10)
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
                    client.live.connect(
                        # SDK forwards options to websockets.connect. Its TypedDict
                        # omits the underlying, supported close_timeout option.
                        websocket_connection_options=cast(
                            WebSocketConnectionOptions, {"max_size": 131072, "close_timeout": 1}
                        ),
                        max_retries=0,
                    )
                ),
                timeout=10,
            )

            async def to_browser(event: dict[str, Any]) -> None:
                # Provider receipt collection continues after browser loss/logout.
                with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                    await asyncio.wait_for(websocket.send_json(event), timeout=1)

            session = LiveSession(ledger, start, upstream, to_browser)
            await session.start_session()
            deadline = asyncio.get_running_loop().time() + limits.maximum_seconds
            startup_deadline = asyncio.get_running_loop().time() + 10

            async def from_browser() -> None:
                while True:
                    message = await websocket.receive_text()
                    if len(message.encode()) > 35000:
                        raise VoiceUnavailableError("voice_event_too_large")
                    if not await session.client(json.loads(message)):
                        return

            async def from_provider() -> None:
                async for event in upstream:
                    try:
                        await session.provider(event.model_dump(mode="json", exclude_none=True))
                    except (
                        AppError,
                        VoiceUnavailableError,
                        ValidationError,
                        ValueError,
                        KeyError,
                        TypeError,
                        AttributeError,
                    ):
                        session.failure = VoiceUnavailableError("live_provider_event_unavailable")
                        await session.request_close()
                    if session.finalized.is_set():
                        return

            async def authorization() -> None:
                while True:
                    await asyncio.sleep(0.5)
                    now = asyncio.get_running_loop().time()
                    if (
                        session.closing
                        or now >= deadline
                        or (not session.ready and now >= startup_deadline)
                    ):
                        return
                    await asyncio.wait_for(
                        asyncio.to_thread(ledger.update, lambda _row, _data: None), timeout=2
                    )
                    for task in session.tasks:
                        if task.done():
                            task.result()

            browser = asyncio.create_task(from_browser())
            provider = asyncio.create_task(from_provider())
            guard = asyncio.create_task(authorization())
            tasks = [browser, provider, guard]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            failure: BaseException | None = session.failure
            for task in done:
                try:
                    task.result()
                except BaseException as exc:
                    failure = exc
            browser.cancel()
            guard.cancel()
            await asyncio.gather(browser, guard, return_exceptions=True)
            # Install final-event reader first; do not let a browser disconnect
            # discard final provider usage. No reconnect or automatic retry.
            if not session.finalized.is_set() and not provider.done():
                try:
                    elapsed = (
                        asyncio.get_running_loop().time() - session.close_started
                        if session.close_started is not None
                        else 0
                    )
                    async with asyncio.timeout(max(0, CLOSE_SECONDS - elapsed)):
                        await session.request_close()
                        await asyncio.shield(provider)
                except Exception:
                    pass
            await to_browser({"type": "session.closed", "finalized": session.finalized.is_set()})
            final_failure = failure or session.failure
            if final_failure is not None:
                raise final_failure
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
