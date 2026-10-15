"""GET-only HTTP client for public perpetual market data.

POST/PUT/PATCH/DELETE and any order, position, or account path are rejected.
Only approved Binance USD-M HTTPS hosts may be used. Spot, Coin-M, arbitrary
hosts, and plain HTTP cannot be labelled as USD-M perpetual evidence.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

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
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._allowed_hosts = allowed_hosts or APPROVED_BINANCE_USDM_REST_HOSTS
        self._allowed_paths = allowed_paths or ALLOWED_PATHS
        self._failure_label = failure_label
        self._assert_base_url()
        self._owns_client = client is None
        self._max_retries = max_retries
        self._max_backoff_seconds = max_backoff_seconds
        self._sleep = sleeper if sleeper is not None else time.sleep
        self._progress_interval_seconds = max(progress_interval_seconds, 0.01)
        self._budget = budget or SlidingWeightBudget(
            limit=weight_per_minute,
            max_wait_seconds=max(max_backoff_seconds, 60.0),
        )
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
        weight = request_weight(normalized_path, params)
        attempts = self._max_retries + 1
        last_retry_after: float | None = None
        for attempt in range(attempts):
            self._budget.acquire(weight, sleeper=self._sleep)
            try:
                response = self._exchange_get(url, params)
            except httpx.TransportError as exc:
                record_request(weight=weight, retry=attempt > 0)
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
                record_request(weight=weight, rate_limited=True, retry=attempt > 0)
                if attempt + 1 >= attempts:
                    raise RateLimitedError(
                        f"{self._failure_label} source rate-limited the read-only client.",
                        retry_after_seconds=last_retry_after,
                    )
                delay = bounded_backoff_seconds(
                    attempt=attempt,
                    retry_after_seconds=last_retry_after,
                    max_backoff_seconds=self._max_backoff_seconds,
                )
                sleep_with_progress(delay, sleeper=self._sleep)
                continue
            if response.status_code == UPSTREAM_BAN_STATUS:
                retry_after = _retry_after_seconds(response)
                record_request(weight=weight, rate_limited=True, retry=attempt > 0)
                if not self._upstream_ban_retry_allowed(attempt, retry_after):
                    raise UpstreamBanError(
                        f"{self._failure_label} source temporarily banned this client (HTTP 418).",
                        retry_after_seconds=retry_after,
                    )
                delay = bounded_backoff_seconds(
                    attempt=attempt,
                    retry_after_seconds=retry_after,
                    max_backoff_seconds=self._max_backoff_seconds,
                )
                sleep_with_progress(delay, sleeper=self._sleep)
                continue
            if response.status_code in REGIONAL_STATUS_CODES:
                record_request(weight=weight)
                raise RegionalProviderFailureError(
                    f"{self._failure_label} source rejected the runtime region "
                    f"(HTTP {response.status_code})."
                )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                record_request(weight=weight)
                raise RegionalProviderFailureError(
                    f"{self._failure_label} source returned HTTP {response.status_code}."
                ) from exc
            record_request(weight=weight, retry=attempt > 0)
            return response.json()
        raise RateLimitedError(
            f"{self._failure_label} source rate-limited the read-only client.",
            retry_after_seconds=last_retry_after,
        )

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
        if hook is None:
            return self._client.get(url, params=query)
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
            return self._client.get(url, params=query)
        finally:
            stop.set()
            thread.join(timeout=self._progress_interval_seconds)

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
