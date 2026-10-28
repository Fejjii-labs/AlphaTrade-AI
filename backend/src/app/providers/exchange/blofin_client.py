"""Authenticated BloFin *demo* REST client.

Safety-critical guarantees:

* Every request asserts the configured base URL is an allowlisted BloFin demo
  host (defense in depth on top of settings validation).
* Credentials are HMAC-signed and never logged; all error text is redacted.
* Transient GET failures are retried with bounded, jittered backoff. POST
  requests never retry; a lost response requires durable reconciliation.

This module performs no order placement; it is the transport used by the
read-only account/market-data providers and the governed demo execution provider.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from app.core.exchange_safety import assert_demo_host
from app.guardrails.redaction import redact_text
from app.providers.exchange.errors import (
    ExchangeAuthError,
    ExchangeError,
    ExchangeRateLimitError,
    ExchangeRequestError,
    ExchangeUnavailableError,
    VenueErrorDetails,
)
from app.providers.exchange.venue_diagnostics import endpoint_label

logger = structlog.get_logger(__name__)

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _first_order_error(data: Any) -> tuple[str | None, str | None]:
    """Return per-order ``code``/``msg`` from a BloFin batch ``data`` payload."""
    if isinstance(data, list) and data:
        row = data[0]
        if isinstance(row, dict):
            inner_code = row.get("code")
            inner_msg = row.get("msg")
            code = str(inner_code) if inner_code is not None else None
            msg = str(inner_msg) if inner_msg is not None else None
            return code, msg
    return None, None


def _http_venue_code(response: httpx.Response) -> str:
    """Keep a returned numeric venue code distinct from the HTTP status."""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            inner, _ = _first_order_error(payload.get("data"))
            code = inner or str(payload.get("code", ""))
            if code != "0" and code.isascii() and code.isdecimal() and 0 < len(code) <= 16:
                return code
    except (ValueError, TypeError):
        pass
    return str(response.status_code)  # Preserve legacy non-JSON HTTP error behavior.


def _signed_request_path(path: str, params: dict[str, Any] | None) -> str:
    """Return the request path used in the BloFin signature prehash.

    Signed GET requests must include the query string in ``path`` with stable
    parameter ordering (alphabetical by key).
    """
    if not params:
        return path
    ordered = sorted((str(key), str(value)) for key, value in params.items())
    query = urlencode(ordered)
    return f"{path}?{query}"


def _now_ms() -> str:
    return str(int(datetime.now(UTC).timestamp() * 1000))


class BloFinClient:
    """Minimal signed REST client for the BloFin demo venue."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        rate_limit_requests_per_second: int = 5,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], str] = _now_ms,
        nonce_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        jitter: float = 0.1,
    ) -> None:
        # Last-line guard: never construct a client against a non-demo host.
        assert_demo_host(base_url)
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._api_secret = api_secret.encode("utf-8")
        self._api_passphrase = api_passphrase
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._min_interval = 1.0 / max(rate_limit_requests_per_second, 1)
        self._transport = transport
        self._sleeper = sleeper
        self._clock = clock
        self._nonce_factory = nonce_factory
        self._jitter = jitter
        self._last_request_at: float = 0.0
        self._last_success_at: datetime | None = None
        self._last_error: str | None = None

    @property
    def last_success_at(self) -> datetime | None:
        return self._last_success_at

    @property
    def last_error(self) -> str | None:
        return self._redact(self._last_error) if self._last_error else None

    def _redact(self, message: str) -> str:
        # Venue/proxy failures can echo opaque credentials without field labels.
        for secret in (self._api_key, self._api_secret.decode("utf-8"), self._api_passphrase):
            if secret:
                message = message.replace(secret, "[REDACTED]")
        return redact_text(message)

    def _sign(self, *, method: str, path: str, timestamp: str, nonce: str, body: str) -> str:
        """BloFin signature: base64(hex(HMAC_SHA256(secret, path+method+ts+nonce+body)))."""
        prehash = f"{path}{method.upper()}{timestamp}{nonce}{body}"
        digest = hmac.new(self._api_secret, prehash.encode("utf-8"), hashlib.sha256).hexdigest()
        return base64.b64encode(digest.encode("utf-8")).decode("utf-8")

    def _auth_headers(self, *, method: str, path: str, body: str) -> dict[str, str]:
        timestamp = self._clock()
        nonce = self._nonce_factory()
        return {
            "ACCESS-KEY": self._api_key,
            "ACCESS-SIGN": self._sign(
                method=method, path=path, timestamp=timestamp, nonce=nonce, body=body
            ),
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-NONCE": nonce,
            "ACCESS-PASSPHRASE": self._api_passphrase,
            "Content-Type": "application/json",
        }

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_interval:
            self._sleeper(self._min_interval - elapsed)
        self._last_request_at = time.monotonic()

    def _client(self) -> httpx.Client:
        if self._transport is not None:
            return httpx.Client(
                base_url=self._base_url, timeout=self._timeout, transport=self._transport
            )
        return httpx.Client(base_url=self._base_url, timeout=self._timeout)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        signed: bool = False,
        before_send: Callable[[], None] | None = None,
    ) -> Any:
        """Execute a request and return the venue's ``data`` payload.

        Raises a typed :class:`ExchangeError` subclass on failure. The error
        message is redacted before being stored.
        """
        # Re-assert demo host on every call; configuration cannot drift to prod.
        assert_demo_host(self._base_url)
        # An absolute URL as ``path`` would override the base host; refuse it.
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            raise ExchangeRequestError("BloFin request path must be a relative '/' path.")

        body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
        sign_path = _signed_request_path(path, params)
        request_path = sign_path if params else path
        # A POST may have reached the venue despite a timeout/5xx. Only GET
        # retries are safe; durable command reconciliation owns order recovery.
        retry_limit = self._max_retries if method.upper() == "GET" else 0
        attempt = 0
        last_exc: Exception | None = None

        while attempt <= retry_limit:
            self._throttle()
            try:
                headers = (
                    self._auth_headers(method=method, path=sign_path, body=body_str)
                    if signed
                    else {}
                )
                with self._client() as client:
                    if before_send is not None:
                        before_send()
                    response = client.request(
                        method.upper(),
                        request_path,
                        params=None,
                        content=body_str if body_str else None,
                        headers=headers,
                    )
                return self._handle_response(response, method=method, path=path)
            except (ExchangeRateLimitError, ExchangeUnavailableError) as exc:
                last_exc = exc
                self._last_error = self._redact(str(exc))
                attempt += 1
                if attempt > retry_limit:
                    break
                self._backoff(attempt)
            except httpx.HTTPError as exc:
                last_exc = ExchangeUnavailableError(self._redact(str(exc)))
                self._last_error = self._redact(str(exc))
                attempt += 1
                if attempt > retry_limit:
                    break
                self._backoff(attempt)

        logger.warning(
            "blofin_request_failed",
            method=method,
            path=path,
            endpoint=endpoint_label(method, path),
            error_type=type(last_exc).__name__
            if last_exc is not None
            else "ExchangeUnavailableError",
        )
        endpoint = endpoint_label(method, path)
        details = VenueErrorDetails(endpoint_name=endpoint)
        if isinstance(last_exc, ExchangeError) and last_exc.details is not None:
            details = VenueErrorDetails(
                venue_error_code=last_exc.details.venue_error_code,
                venue_error_message=last_exc.details.venue_error_message,
                http_status=last_exc.details.http_status,
                endpoint_name=endpoint,
            )
        if last_exc is not None and isinstance(last_exc, ExchangeError):
            raise type(last_exc)(str(last_exc), details=details) from last_exc
        raise ExchangeUnavailableError(
            "BloFin request failed after retries.",
            details=details,
        )

    def _backoff(self, attempt: int) -> None:
        delay = (2 ** (attempt - 1)) * self._min_interval
        # Deterministic jitter (no RNG) keeps tests reproducible.
        self._sleeper(delay * (1 + self._jitter))

    def _handle_response(self, response: httpx.Response, *, method: str, path: str) -> Any:
        endpoint = endpoint_label(method, path)
        status = response.status_code
        if status == 429:
            raise ExchangeRateLimitError(
                "BloFin rate limit exceeded.",
                details=VenueErrorDetails(http_status=status, endpoint_name=endpoint),
            )
        if status in _RETRYABLE_STATUS:
            raise ExchangeUnavailableError(
                f"BloFin server error: HTTP {status}.",
                details=VenueErrorDetails(http_status=status, endpoint_name=endpoint),
            )
        if status in (401, 403):
            self._last_error = "BloFin authentication/permission failure."
            raise ExchangeAuthError(
                self._last_error,
                details=VenueErrorDetails(
                    venue_error_code=_http_venue_code(response),
                    venue_error_message=self._last_error,
                    http_status=status,
                    endpoint_name=endpoint,
                ),
            )
        if status >= 400:
            self._last_error = f"BloFin rejected request: HTTP {status}."
            raise ExchangeRequestError(
                self._last_error,
                details=VenueErrorDetails(
                    venue_error_code=_http_venue_code(response),
                    venue_error_message=self._last_error,
                    http_status=status,
                    endpoint_name=endpoint,
                ),
            )

        try:
            payload = response.json()
        except Exception as exc:  # malformed body
            raise ExchangeUnavailableError(
                f"BloFin returned non-JSON body: {self._redact(str(exc))}",
                details=VenueErrorDetails(http_status=status, endpoint_name=endpoint),
            ) from exc

        # BloFin envelope: {"code": "0", "msg": "...", "data": ...}; code 0 == ok.
        code = str(payload.get("code", "0"))
        if code != "0":
            inner_code, inner_msg = _first_order_error(payload.get("data"))
            venue_code = inner_code if inner_code is not None else code
            if inner_msg is not None:
                venue_msg = self._redact(inner_msg)
            else:
                venue_msg = self._redact(str(payload.get("msg", "")))
            details = VenueErrorDetails(
                venue_error_code=venue_code,
                venue_error_message=venue_msg or None,
                http_status=status,
                endpoint_name=endpoint,
            )
            if code in ("401", "403"):
                self._last_error = f"BloFin auth error {venue_code}: {venue_msg}"
                raise ExchangeAuthError(self._last_error, details=details)
            self._last_error = f"BloFin error {venue_code}: {venue_msg}"
            raise ExchangeRequestError(self._last_error, details=details)

        self._last_success_at = datetime.now(UTC)
        self._last_error = None
        return payload.get("data")
