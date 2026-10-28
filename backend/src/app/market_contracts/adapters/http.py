"""GET-only HTTP client for public perpetual market data.

POST/PUT/PATCH/DELETE and any order, position, or account path are rejected.
Only approved Binance USD-M HTTPS hosts may be used. Spot, Coin-M, arbitrary
hosts, and plain HTTP cannot be labelled as USD-M perpetual evidence.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
import structlog

from app.market_contracts.adapters.binance_rest_gate import (
    AGGTRADES_PATH,
    BinanceRestGate,
    process_binance_rest_gate,
)
from app.market_contracts.adapters.public_read_cache import ShortTtlSingleflight
from app.market_contracts.adapters.request_budget import (
    SlidingWeightBudget,
    bounded_backoff_seconds,
    record_request,
    request_weight,
    sleep_with_progress,
)
from app.market_contracts.errors import (
    NetworkMutationForbiddenError,
    RateLimitedError,
    RegionalProviderFailureError,
    RetrievalBudgetExceededError,
    SpotFallbackRejectedError,
    UnapprovedEvidenceHostError,
    UpstreamBanError,
    WrongMarketError,
)
from app.market_contracts.request_progress import current_progress_hook

ALLOWED_PATHS = frozenset(
    {
        "/fapi/v1/ping",
        "/fapi/v1/time",
        "/fapi/v1/exchangeInfo",
        "/fapi/v1/klines",
        "/fapi/v1/aggTrades",
        "/fapi/v1/openInterest",
        "/fapi/v1/fundingRate",
        "/fapi/v1/depth",
    }
)
APPROVED_BINANCE_USDM_REST_HOSTS = frozenset({"fapi.binance.com"})
SPOT_HOST_MARKERS = (
    "api.binance.com",
    "api1.binance.com",
    "api2.binance.com",
    "api3.binance.com",
    "api4.binance.com",
    "data-api.binance.vision",
)
COINM_HOST_MARKERS = ("dapi.binance.com",)
SPOT_PATH_PREFIXES = ("/api/v3/", "/api/v1/")
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE", "CONNECT", "TRACE"})
REGIONAL_STATUS_CODES = frozenset({401, 403, 451})
UPSTREAM_BAN_STATUS = 418
_PROGRESS_INTERVAL_SECONDS = 5.0
_SNIPPET_LIMIT = 200
_SECRET_MARKERS = (
    "api_key",
    "api-key",
    "secret",
    "authorization",
    "bearer ",
    "password",
    "token",
    "signature",
    "x-mbx-apikey",
    "key=",
)

logger = structlog.get_logger(__name__)

# Bounded chat reads run each GET on this pool so the caller can stop waiting
# at the deadline even when a slow body keeps the socket busy.
_BOUNDED_GET_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="bounded-market-get")


def deadline_clock() -> float:
    """The one clock for chat retrieval deadlines (reader and HTTP client)."""

    return time.monotonic()


@dataclass
class UpstreamPause:
    """Process-shared 418/429 quiet period. The next call waits without a request."""

    _until: float | None = None
    _kind: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _clock: Callable[[], float] = time.monotonic

    def remaining(self) -> tuple[str, float] | None:
        """Return ``(ban|rate_limit, seconds_left)`` while the pause is active."""

        with self._lock:
            if self._until is None or self._kind is None:
                return None
            left = self._until - self._clock()
            if left <= 0:
                self._until = None
                self._kind = None
                return None
            return self._kind, left

    def note(self, kind: str, seconds: float) -> None:
        """Extend the pause. A shorter new pause does not shorten an open one."""

        if seconds < 0:
            return
        until = self._clock() + seconds
        with self._lock:
            if self._until is None or until >= self._until:
                self._until = until
                self._kind = kind

    def clear(self) -> None:
        with self._lock:
            self._until = None
            self._kind = None


class SharedBanPauseView(UpstreamPause):
    """Honor a process-wide pause without leaking private 429 pauses into it.

    A chat client reads the shared gate, so an open 418 ban or worker 429 pause
    fails the chat read before any request. Its own 418 is a process fact and is
    recorded on the shared gate; its own 429 stays private to this client.
    """

    def __init__(self, shared: UpstreamPause) -> None:
        super().__init__()
        self._shared = shared

    def remaining(self) -> tuple[str, float] | None:
        shared = self._shared.remaining()
        if shared is not None and shared[0] == "ban":
            return shared
        local = super().remaining()
        return local if local is not None else shared

    def note(self, kind: str, seconds: float) -> None:
        if kind == "ban":
            self._shared.note(kind, seconds)
            return
        super().note(kind, seconds)

    def clear(self) -> None:
        super().clear()


class ReadOnlyHttpGetClient:
    """Allowlisted HTTPS GET client. Never sends credentials or mutations."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        allowed_hosts: frozenset[str] | None = None,
        allowed_paths: frozenset[str] | None = None,
        failure_label: str = "Preferred Binance USD-M",
        max_retries: int = 0,
        weight_per_minute: int = 1800,
        max_backoff_seconds: float = 30.0,
        sleeper: Callable[[float], None] | None = None,
        budget: SlidingWeightBudget | None = None,
        progress_interval_seconds: float = _PROGRESS_INTERVAL_SECONDS,
        pause: UpstreamPause | None = None,
        fail_fast_rate_limits: bool = False,
        deadline_monotonic: float | None = None,
        rest_gate: BinanceRestGate | None = None,
        aggtrades_request_cache: ShortTtlSingleflight | None = None,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._allowed_hosts = allowed_hosts or APPROVED_BINANCE_USDM_REST_HOSTS
        self._allowed_paths = allowed_paths or ALLOWED_PATHS
        self._failure_label = failure_label
        self._assert_base_url()
        self._owns_client = client is None
        self._max_retries = max_retries
        self._max_backoff_seconds = max_backoff_seconds
        self._timeout_seconds = timeout_seconds
        self._fail_fast_rate_limits = fail_fast_rate_limits
        self._deadline_monotonic = deadline_monotonic
        self._sleep = sleeper if sleeper is not None else time.sleep
        self._progress_interval_seconds = max(progress_interval_seconds, 0.01)
        self._budget = budget or SlidingWeightBudget(
            limit=weight_per_minute,
            max_wait_seconds=max(max_backoff_seconds, 60.0),
        )
        self._pause = pause or UpstreamPause()
        # Real network clients share one process gate, so a 418/429 seen by any
        # client stops every Binance REST call in this process. Clients built on
        # an injected transport or client (tests, harnesses) opt in explicitly.
        binance_host = (urlsplit(self._base_url).hostname or "").lower() in (
            APPROVED_BINANCE_USDM_REST_HOSTS
        )
        if rest_gate is None and transport is None and client is None and binance_host:
            rest_gate = process_binance_rest_gate()
        self._gate = rest_gate
        self._aggtrades_cache = aggtrades_request_cache
        self._wall_clock = wall_clock
        self._client = client or httpx.Client(
            base_url=self._base_url,
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=False,
            headers={"User-Agent": "AlphaTradeAI-perpetual-evidence/read-only"},
        )

    @property
    def budget(self) -> SlidingWeightBudget:
        return self._budget

    def ban_remaining_seconds(self) -> float | None:
        """Seconds left on an HTTP 418 pause. Rate-limit pauses are not bans."""

        paused = self._pause.remaining()
        if paused is None or paused[0] != "ban":
            return None
        return paused[1]

    def pause_remaining(self) -> tuple[str, float] | None:
        return self._pause.remaining()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def get_json(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        return self.request_json("GET", path, params=params)

    def request_json(
        self,
        method: str,
        path: str,
        params: Mapping[str, str | int] | None = None,
    ) -> Any:
        normalized_method = method.upper()
        if normalized_method in MUTATING_METHODS or normalized_method != "GET":
            raise NetworkMutationForbiddenError(
                f"Perpetual evidence adapter forbids HTTP {normalized_method}."
            )
        normalized_path = self._normalize_path(path)
        if normalized_path not in self._allowed_paths:
            raise NetworkMutationForbiddenError(
                f"Path {normalized_path} is not on the read-only perpetual allowlist."
            )
        url = urljoin(self._base_url + "/", normalized_path.lstrip("/"))
        self._assert_url(url)
        self._reject_if_paused()
        self._reject_if_gate_closed()
        if self._aggtrades_cache is not None and self._is_closed_aggtrades_window(
            normalized_path, params
        ):
            key = (normalized_path, tuple(sorted((params or {}).items())))
            return self._aggtrades_cache.get_or_load(
                key, lambda: self._send_get(url, normalized_path, params)
            )
        return self._send_get(url, normalized_path, params)

    def _send_get(
        self,
        url: str,
        normalized_path: str,
        params: Mapping[str, str | int] | None,
    ) -> Any:
        weight = request_weight(normalized_path, params)
        attempts = self._max_retries + 1
        last_retry_after: float | None = None
        for attempt in range(attempts):
            self._raise_if_past_deadline()
            self._reject_if_gate_closed()
            self._wait_for_spacing(normalized_path)
            self._budget.acquire(weight, sleeper=self._weight_sleeper())
            try:
                response = self._exchange_get(url, params)
            except httpx.TransportError as exc:
                record_request(weight=weight, retry=attempt > 0)
                self._log_http_failure(
                    status=None,
                    exception_type=type(exc).__name__,
                    response=None,
                    exc=exc,
                    path=normalized_path,
                )
                if attempt + 1 >= attempts:
                    raise RegionalProviderFailureError(
                        f"{self._failure_label} perpetual source is unreachable."
                    ) from exc
                delay = bounded_backoff_seconds(
                    attempt=attempt,
                    retry_after_seconds=None,
                    max_backoff_seconds=self._max_backoff_seconds,
                )
                sleep_with_progress(delay, sleeper=self._sleep)
                continue
            if response.status_code == 429:
                last_retry_after = _retry_after_seconds(response)
                self._log_http_failure(
                    status=response.status_code,
                    exception_type="HTTPStatusError",
                    response=response,
                    exc=None,
                    path=normalized_path,
                )
                record_request(weight=weight, rate_limited=True, retry=attempt > 0)
                gate_seconds = self._note_gate_failure("rate_limit", last_retry_after, response)
                delay = bounded_backoff_seconds(
                    attempt=attempt,
                    retry_after_seconds=last_retry_after,
                    max_backoff_seconds=self._max_backoff_seconds,
                )
                if gate_seconds is not None:
                    delay = max(delay, gate_seconds)
                if (
                    self._fail_fast_rate_limits
                    or attempt + 1 >= attempts
                    or delay > self._max_backoff_seconds
                ):
                    # A finished 429 belongs to the caller. Parking this process
                    # for Retry-After would block the next attempt after the
                    # in-call sleep already honored that header. Chat retrieval
                    # sets fail_fast so the first 429 does not sleep at all.
                    raise RateLimitedError(
                        f"{self._failure_label} source rate-limited the read-only client.",
                        retry_after_seconds=last_retry_after,
                    )
                self._pause.note("rate_limit", _pause_seconds(last_retry_after, delay))
                sleep_with_progress(delay, sleeper=self._sleep)
                continue
            if response.status_code == UPSTREAM_BAN_STATUS:
                retry_after = _retry_after_seconds(response)
                self._log_http_failure(
                    status=response.status_code,
                    exception_type="HTTPStatusError",
                    response=response,
                    exc=None,
                    path=normalized_path,
                )
                record_request(weight=weight, rate_limited=True, retry=attempt > 0)
                delay = bounded_backoff_seconds(
                    attempt=attempt,
                    retry_after_seconds=retry_after,
                    max_backoff_seconds=self._max_backoff_seconds,
                )
                # Hold the full Retry-After, not only the capped in-call sleep,
                # so the next call does not extend the ban.
                self._pause.note("ban", _pause_seconds(retry_after, delay))
                gate_seconds = self._note_gate_failure("ban", retry_after, response)
                if gate_seconds is not None:
                    delay = max(delay, gate_seconds)
                if (
                    self._fail_fast_rate_limits
                    or not self._upstream_ban_retry_allowed(attempt, retry_after)
                    or delay > self._max_backoff_seconds
                ):
                    raise UpstreamBanError(
                        f"{self._failure_label} source temporarily banned this client (HTTP 418).",
                        retry_after_seconds=retry_after,
                    )
                sleep_with_progress(delay, sleeper=self._sleep)
                continue
            if response.status_code in REGIONAL_STATUS_CODES:
                self._log_http_failure(
                    status=response.status_code,
                    exception_type="HTTPStatusError",
                    response=response,
                    exc=None,
                    path=normalized_path,
                )
                record_request(weight=weight)
                raise RegionalProviderFailureError(
                    f"{self._failure_label} source rejected the runtime region "
                    f"(HTTP {response.status_code})."
                )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                self._log_http_failure(
                    status=response.status_code,
                    exception_type=type(exc).__name__,
                    response=response,
                    exc=exc,
                    path=normalized_path,
                )
                record_request(weight=weight)
                raise RegionalProviderFailureError(
                    f"{self._failure_label} source returned HTTP {response.status_code}."
                ) from exc
            record_request(weight=weight, retry=attempt > 0)
            self._pause.clear()
            if self._gate is not None:
                self._gate.note_success(response.headers)
            return response.json()
        raise RateLimitedError(
            f"{self._failure_label} source rate-limited the read-only client.",
            retry_after_seconds=last_retry_after,
        )

    def _reject_if_gate_closed(self) -> None:
        """Refuse to send while the process-wide Binance gate is closed."""

        if self._gate is None:
            return
        closed = self._gate.remaining()
        if closed is None:
            return
        kind, left = closed
        if kind == "ban":
            raise UpstreamBanError(
                f"{self._failure_label} source temporarily banned this process (HTTP 418).",
                retry_after_seconds=left,
            )
        raise RateLimitedError(
            f"{self._failure_label} source is in a rate-limit backoff for this process.",
            retry_after_seconds=left,
        )

    def _note_gate_failure(
        self, kind: str, retry_after: float | None, response: httpx.Response
    ) -> float | None:
        if self._gate is None:
            return None
        self._gate.note_used_weight(response.headers)
        return self._gate.note_failure(kind, retry_after)

    def _wait_for_spacing(self, path: str) -> None:
        """Spread aggTrades reads (wider during boot). Chat reads do not queue."""

        if self._gate is None or self._fail_fast_rate_limits:
            return
        delay = self._gate.reserve_spacing(path)
        if delay > 0:
            sleep_with_progress(delay, sleeper=self._weight_sleeper())

    def _is_closed_aggtrades_window(
        self, path: str, params: Mapping[str, str | int] | None
    ) -> bool:
        """Only a time window that already ended has one fixed answer to share."""

        if path != AGGTRADES_PATH or not params or "endTime" not in params:
            return False
        try:
            end_ms = int(params["endTime"])
        except (TypeError, ValueError):
            return False
        return end_ms < int(self._wall_clock() * 1000) - 1000

    def _reject_if_paused(self) -> None:
        """Refuse another request during a 418 ban or an in-call 429 retry."""

        paused = self._pause.remaining()
        if paused is None:
            return
        kind, left = paused
        if kind == "ban":
            raise UpstreamBanError(
                f"{self._failure_label} source temporarily banned this client (HTTP 418).",
                retry_after_seconds=left,
            )
        raise RateLimitedError(
            f"{self._failure_label} source rate-limited the read-only client.",
            retry_after_seconds=left,
        )

    def _log_http_failure(
        self,
        *,
        status: int | None,
        exception_type: str,
        response: httpx.Response | None,
        exc: BaseException | None,
        path: str,
    ) -> None:
        """Log status, exception type, and a short body. Never headers or secrets."""

        if response is not None:
            snippet = _body_snippet(response)
        else:
            snippet = _sanitize_snippet("" if exc is None else str(exc))
        logger.warning(
            "perpetual_http_failure",
            http_status=status,
            exception_type=exception_type,
            body_snippet=snippet,
            path=path,
        )

    def _weight_sleeper(self) -> Callable[[float], None]:
        """Worker scans sleep inside the weight window. Chat does not."""

        if self._fail_fast_rate_limits or self._deadline_monotonic is not None:
            return self._refuse_weight_wait
        return self._sleep

    def _refuse_weight_wait(self, seconds: float) -> None:
        self._raise_if_past_deadline()
        if seconds <= 0:
            return
        if self._fail_fast_rate_limits:
            raise RateLimitedError(
                f"{self._failure_label} source rate-limited the read-only client.",
                retry_after_seconds=seconds,
            )
        remaining = self._seconds_left()
        if remaining is not None and seconds > remaining:
            raise RetrievalBudgetExceededError("market data unavailable")
        self._sleep(seconds)

    def _seconds_left(self) -> float | None:
        if self._deadline_monotonic is None:
            return None
        return self._deadline_monotonic - deadline_clock()

    def _raise_if_past_deadline(self) -> None:
        remaining = self._seconds_left()
        if remaining is not None and remaining <= 0:
            raise RetrievalBudgetExceededError("market data unavailable")

    def _request_timeout(self) -> float:
        """Cap one GET by the time still left in a chat retrieval budget."""

        self._raise_if_past_deadline()
        remaining = self._seconds_left()
        if remaining is None:
            return self._timeout_seconds
        return max(min(self._timeout_seconds, remaining), 0.001)

    def _upstream_ban_retry_allowed(self, attempt: int, retry_after: float | None) -> bool:
        """Retry a 418 only inside the existing attempt budget and ban window.

        A Retry-After longer than ``max_backoff_seconds`` means another request
        now would land during the ban. That request is not sent.
        """

        if attempt + 1 >= self._max_retries + 1:
            return False
        return retry_after is None or retry_after <= self._max_backoff_seconds

    def _exchange_get(
        self,
        url: str,
        params: Mapping[str, str | int] | None,
    ) -> httpx.Response:
        """GET while a bound lease hook can beat during the call, not only backoff."""

        hook = current_progress_hook()
        query = dict(params or {})
        timeout = self._request_timeout()
        if hook is None:
            return self._bounded_get(url, query, timeout)
        stop = threading.Event()

        def _pulse() -> None:
            # The hook is captured on the caller thread. A new thread does not
            # inherit the contextvar that bind_market_request_progress set.
            while not stop.wait(self._progress_interval_seconds):
                hook()

        hook()
        thread = threading.Thread(target=_pulse, name="binance-read-heartbeat", daemon=True)
        thread.start()
        try:
            return self._bounded_get(url, query, timeout)
        finally:
            stop.set()
            thread.join(timeout=self._progress_interval_seconds)

    def _bounded_get(self, url: str, query: dict[str, str | int], timeout: float) -> httpx.Response:
        """One GET. With a deadline, the whole request must finish inside it.

        httpx timeouts are per phase (connect, read, write, pool), so a body that
        trickles in can outlive them. With a deadline the GET runs on a helper
        thread and the caller stops waiting once the remaining budget is spent.
        """

        if self._deadline_monotonic is None:
            return self._client.get(url, params=query, timeout=timeout)
        future = _BOUNDED_GET_POOL.submit(self._client.get, url, params=query, timeout=timeout)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError as exc:
            future.cancel()
            raise RetrievalBudgetExceededError("market data unavailable") from exc

    def _normalize_path(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            parsed = urlsplit(path)
            self._assert_url(path)
            path = parsed.path
        if not path.startswith("/"):
            path = "/" + path
        for prefix in SPOT_PATH_PREFIXES:
            if path.startswith(prefix):
                raise SpotFallbackRejectedError(
                    "Spot REST paths cannot satisfy perpetual evidence."
                )
        return path

    def _assert_base_url(self) -> None:
        parsed = urlsplit(self._base_url)
        host = (parsed.hostname or "").lower()
        if not host:
            raise WrongMarketError("Perpetual evidence base URL is missing a host.")
        self._assert_scheme(parsed.scheme)
        self._assert_host(host)

    def _assert_url(self, url: str) -> None:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme:
            self._assert_scheme(parsed.scheme)
        self._assert_host(host)
        path = parsed.path or "/"
        for prefix in SPOT_PATH_PREFIXES:
            if path.startswith(prefix):
                raise SpotFallbackRejectedError(
                    "Spot REST paths cannot satisfy perpetual evidence."
                )

    def _assert_scheme(self, scheme: str) -> None:
        if scheme.lower() != "https":
            raise UnapprovedEvidenceHostError(
                "Binance USD-M perpetual evidence requires HTTPS; plain HTTP is forbidden."
            )

    def _assert_host(self, host: str) -> None:
        if any(host == marker or host.endswith("." + marker) for marker in SPOT_HOST_MARKERS):
            raise SpotFallbackRejectedError(
                "Spot host cannot be used as a Binance USD-M perpetual evidence source."
            )
        if any(host == marker or host.endswith("." + marker) for marker in COINM_HOST_MARKERS):
            raise WrongMarketError("Coin-M host cannot substitute for USD-M perpetual evidence.")
        if host not in self._allowed_hosts:
            raise UnapprovedEvidenceHostError(
                f"Host {host} is not an approved Binance USD-M HTTPS evidence identity."
            )


def _pause_seconds(retry_after: float | None, slept: float) -> float:
    """Hold the exchange Retry-After when it is present, otherwise the sleep we used."""

    if retry_after is not None and retry_after >= 0:
        return retry_after
    return max(slept, 0.0)


def _body_snippet(response: httpx.Response) -> str:
    try:
        raw = bytes(response.content[: _SNIPPET_LIMIT * 2])
    except Exception:
        return ""
    return _sanitize_snippet(raw.decode("utf-8", errors="replace"))


def _sanitize_snippet(text: str) -> str:
    collapsed = " ".join(text.replace("\x00", " ").split())
    lowered = collapsed.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return "[redacted]"
    return collapsed[:_SNIPPET_LIMIT]


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    token = raw.strip()
    if not token:
        return None
    try:
        parsed = float(token)
    except ValueError:
        return None
    if parsed < 0:
        return None
    return parsed
