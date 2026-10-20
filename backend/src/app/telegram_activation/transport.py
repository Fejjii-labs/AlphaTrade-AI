"""Outbound transport guards. Network stays off unless a caller permits it."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

import httpx

from app.providers.alert_delivery.telegram import telegram_http_send_timeout_seconds
from app.telegram_activation.errors import TelegramActivationError
from app.telegram_security.backoff import RATE_LIMITED_ERROR
from app.telegram_security.clock import Clock
from app.telegram_security.transport import TelegramTransport, TransportSendResult

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class SendLedgerEntry(Protocol):
    transport_message_id: str
    organization_id: UUID


class SendLedgerRecord:
    def __init__(self, *, organization_id: UUID, transport_message_id: str) -> None:
        self.organization_id = organization_id
        self.transport_message_id = transport_message_id


class SendLedger(Protocol):
    def get(self, *, bot_id: str, idempotency_key: str) -> SendLedgerRecord | None: ...

    def record(
        self,
        *,
        organization_id: UUID,
        bot_id: str,
        idempotency_key: str,
        transport_message_id: str,
        created_at: datetime,
    ) -> None: ...


class InMemorySendLedger:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], SendLedgerRecord] = {}

    def get(self, *, bot_id: str, idempotency_key: str) -> SendLedgerRecord | None:
        return self._rows.get((bot_id, idempotency_key))

    def record(
        self,
        *,
        organization_id: UUID,
        bot_id: str,
        idempotency_key: str,
        transport_message_id: str,
        created_at: datetime,
    ) -> None:
        del created_at
        key = (bot_id, idempotency_key)
        current = self._rows.get(key)
        if current is None:
            self._rows[key] = SendLedgerRecord(
                organization_id=organization_id,
                transport_message_id=transport_message_id,
            )
            return
        if (
            current.organization_id != organization_id
            or current.transport_message_id != transport_message_id
        ):
            raise TelegramActivationError(
                "Send ledger identity conflict.",
                reason="ledger_conflict",
            )


class OutboundRateLimiter:
    """Sliding window for outbound private messages. Uses the activation clock."""

    def __init__(self, clock: Clock, *, per_chat: int, window: timedelta) -> None:
        if per_chat < 1:
            raise ValueError("per_chat must be >= 1")
        self._clock = clock
        self._per_chat = per_chat
        self._window = window
        self._events: dict[str, list[datetime]] = {}

    def allow(self, chat_id: str) -> bool:
        return len(self._fresh(chat_id)) < self._per_chat

    def record(self, chat_id: str) -> None:
        self._fresh(chat_id).append(self._clock.now())

    def _fresh(self, chat_id: str) -> list[datetime]:
        now = self._clock.now()
        cutoff = now - self._window
        retained = [stamp for stamp in self._events.get(chat_id, []) if stamp > cutoff]
        self._events[chat_id] = retained
        return retained


class RefusingTelegramTransport:
    """Default outbound path. It cannot reach api.telegram.org."""

    def send_private_message(
        self,
        *,
        bot_id: str,
        chat_id: str,
        text: str,
        idempotency_key: str,
    ) -> TransportSendResult:
        del bot_id, chat_id, text, idempotency_key
        return TransportSendResult(ok=False, retryable=False, error_code="NETWORK_DISABLED")


class GuardedTelegramTransport:
    """Bind one bot and private chat, dedupe, then rate-limit before the inner send."""

    def __init__(
        self,
        *,
        inner: TelegramTransport,
        limiter: OutboundRateLimiter,
        ledger: SendLedger,
        organization_id: UUID,
        bot_id: str,
        chat_id: str,
        clock: Clock,
    ) -> None:
        self._inner = inner
        self._limiter = limiter
        self._ledger = ledger
        self._organization_id = organization_id
        self._bot_id = bot_id
        self._chat_id = chat_id
        self._clock = clock

    @property
    def refuses_network(self) -> bool:
        return isinstance(self._inner, RefusingTelegramTransport)

    def send_private_message(
        self,
        *,
        bot_id: str,
        chat_id: str,
        text: str,
        idempotency_key: str,
    ) -> TransportSendResult:
        if bot_id != self._bot_id or chat_id != self._chat_id:
            return TransportSendResult(ok=False, retryable=False, error_code="RECIPIENT_MISMATCH")
        existing = self._ledger.get(bot_id=bot_id, idempotency_key=idempotency_key)
        if existing is not None:
            if existing.organization_id != self._organization_id:
                return TransportSendResult(ok=False, retryable=False, error_code="LEDGER_CONFLICT")
            return TransportSendResult(ok=True, transport_message_id=existing.transport_message_id)
        if not self._limiter.allow(chat_id):
            return TransportSendResult(ok=False, retryable=True, error_code=RATE_LIMITED_ERROR)
        result = self._inner.send_private_message(
            bot_id=bot_id,
            chat_id=chat_id,
            text=text,
            idempotency_key=idempotency_key,
        )
        if not result.ok or result.transport_message_id is None:
            return result
        self._ledger.record(
            organization_id=self._organization_id,
            bot_id=bot_id,
            idempotency_key=idempotency_key,
            transport_message_id=result.transport_message_id,
            created_at=self._clock.now(),
        )
        self._limiter.record(chat_id)
        return result


def redact_secret(text: str, *secrets: str) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "***REDACTED***")
    return redacted


HttpPoster = Callable[..., object]


class HttpTelegramTransport:
    """Telegram sendMessage client. Refuses unless network_permitted is true.

    Callers in this slice keep ``network_permitted`` false. Tests inject
    ``http_post`` and never use a live token.
    """

    def __init__(
        self,
        *,
        token: str,
        timeout_seconds: float,
        network_permitted: bool,
        http_post: HttpPoster | None = None,
    ) -> None:
        self._token = token.strip()
        self._timeout = timeout_seconds
        self._permitted = network_permitted and bool(self._token)
        self._http_post = http_post if http_post is not None else httpx.post

    @property
    def network_permitted(self) -> bool:
        return self._permitted

    def send_private_message(
        self,
        *,
        bot_id: str,
        chat_id: str,
        text: str,
        idempotency_key: str,
    ) -> TransportSendResult:
        del bot_id, idempotency_key
        if not self._permitted:
            return TransportSendResult(ok=False, retryable=False, error_code="NETWORK_DISABLED")
        try:
            response = self._http_post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                timeout=telegram_http_send_timeout_seconds(self._timeout),
            )
        except Exception:
            return TransportSendResult(ok=False, retryable=True, error_code="TRANSPORT_FAILURE")
        status = _status_code(response)
        if status == 429:
            return TransportSendResult(ok=False, retryable=True, error_code=RATE_LIMITED_ERROR)
        if status in _RETRYABLE_STATUS:
            return TransportSendResult(ok=False, retryable=True, error_code="TRANSPORT_FAILURE")
        if status >= 400:
            return TransportSendResult(ok=False, retryable=False, error_code="TRANSPORT_REJECTED")
        message_id = _message_id(response)
        if message_id is None:
            return TransportSendResult(ok=False, retryable=True, error_code="TRANSPORT_FAILURE")
        return TransportSendResult(ok=True, transport_message_id=message_id)


def _status_code(response: object) -> int:
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    return 599


def _message_id(response: object) -> str | None:
    payload = getattr(response, "json", None)
    if not callable(payload):
        return None
    try:
        body = payload()
    except Exception:
        return None
    if not isinstance(body, dict) or body.get("ok") is not True:
        return None
    result = body.get("result")
    if not isinstance(result, dict):
        return None
    message_id = result.get("message_id")
    if isinstance(message_id, int) and not isinstance(message_id, bool):
        return str(message_id)
    return None


def new_ledger_id() -> UUID:
    return uuid4()
