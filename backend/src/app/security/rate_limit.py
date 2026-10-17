"""Rate limiting with Redis backend and in-memory fallback."""

from __future__ import annotations

import ipaddress
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from typing import Annotated, Protocol

import structlog
from fastapi import Depends, Request

from app.core.auth import get_current_tenant
from app.core.config import Settings
from app.core.dependencies import AuditServiceDep, SessionDep
from app.core.errors import AppError
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import ActorType, AuditEventType, AuditResult, AuditSeverity
from app.security.tenant import TenantContext

logger = structlog.get_logger(__name__)


class RateLimitExceededError(AppError):
    status_code = 429
    code = "rate_limit_exceeded"


class RateLimiter(Protocol):
    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        """Raise RateLimitExceededError when the limit is exceeded."""


@dataclass
class InMemoryRateLimiter:
    """Sliding-window limiter suitable for tests and fallback mode."""

    _events: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))
    _lock: Lock = field(default_factory=Lock)

    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                raise RateLimitExceededError("Too many requests. Please try again later.")
            bucket.append(now)


class RedisRateLimiter:
    """Fixed-window Redis limiter with optional in-memory fallback."""

    def __init__(
        self,
        settings: Settings,
        fallback: InMemoryRateLimiter,
    ) -> None:
        self._settings = settings
        self._fallback = fallback
        self._redis = None
        self._using_redis = False
        if not settings.rate_limit_use_redis:
            return
        try:
            import redis

            client = redis.from_url(
                settings.redis_url,
                socket_connect_timeout=settings.redis_connect_timeout_seconds,
                socket_timeout=settings.redis_connect_timeout_seconds,
                decode_responses=True,
            )
            client.ping()
            self._redis = client
            self._using_redis = True
            logger.info("rate_limit_redis_enabled", backend="redis")
        except Exception as exc:
            logger.warning(
                "rate_limit_redis_unavailable",
                error_type=type(exc).__name__,
                fallback_allowed=settings.rate_limit_allow_in_memory_fallback,
            )
            if not settings.rate_limit_allow_in_memory_fallback:
                raise RuntimeError(
                    "Redis rate limiting is required but Redis is unavailable."
                ) from exc

    @property
    def using_redis(self) -> bool:
        return self._using_redis

    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        if self._redis is not None:
            try:
                self._check_redis(key, limit=limit, window_seconds=window_seconds)
                return
            except Exception as exc:
                logger.warning(
                    "rate_limit_redis_error",
                    key=key,
                    error_type=type(exc).__name__,
                    fallback_allowed=self._settings.rate_limit_allow_in_memory_fallback,
                )
                if not self._settings.rate_limit_allow_in_memory_fallback:
                    raise RateLimitExceededError(
                        "Rate limiting service temporarily unavailable."
                    ) from exc
        self._fallback.check(key, limit=limit, window_seconds=window_seconds)

    def _check_redis(self, key: str, *, limit: int, window_seconds: int) -> None:
        assert self._redis is not None
        redis_key = f"ratelimit:{key}"
        count = self._redis.incr(redis_key)
        if count == 1:
            self._redis.expire(redis_key, window_seconds)
        if count > limit:
            raise RateLimitExceededError("Too many requests. Please try again later.")


_limiter: RateLimiter | None = None


def reset_rate_limiter() -> None:
    global _limiter
    _limiter = None


def get_rate_limiter(settings: Settings | None = None) -> RateLimiter:
    global _limiter
    if _limiter is None:
        from app.core.config import get_settings

        settings = settings or get_settings()
        memory = InMemoryRateLimiter()
        _limiter = RedisRateLimiter(settings, memory)
    return _limiter


def _peer_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _request_settings(request: Request) -> Settings:
    app = request.scope.get("app")
    settings = getattr(getattr(app, "state", None), "settings", None)
    if isinstance(settings, Settings):
        return settings
    from app.core.config import get_settings

    return get_settings()


def client_ip(request: Request, settings: Settings | None = None) -> str:
    """Resolve the client IP with explicit proxy trust (AT-018).

    Only the rightmost ``trusted_proxy_hops`` entries of ``X-Forwarded-For``
    are trusted, because those were appended by our own reverse proxies. With
    zero trusted hops the header is ignored entirely, so a client can never
    spoof its rate-limit identity. Missing, short, or malformed header data
    conservatively falls back to the socket peer address.
    """
    if settings is None:
        settings = _request_settings(request)
    peer = _peer_ip(request)
    hops = settings.trusted_proxy_hops
    if hops <= 0:
        return peer
    entries = [
        entry.strip()
        for entry in request.headers.get("X-Forwarded-For", "").split(",")
        if entry.strip()
    ]
    if len(entries) < hops:
        # Fewer entries than trusted proxies: direct hit or misconfiguration.
        return peer
    candidate = entries[-hops]
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        logger.warning("client_ip_invalid_forwarded_entry", peer=peer)
        return peer
    return candidate


def _audit_rate_limit_violation(
    *,
    request: Request,
    session: SessionDep,
    audit_service: AuditServiceDep,
    scope: str,
    client_ip_value: str,
    tenant: TenantContext | None,
    severity: AuditSeverity,
) -> None:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    trace_id = getattr(request.state, "trace_id", request_id)
    # Dedicated durable write — avoid committing unrelated shared-session state.
    audit_service.record_durable_isolated(
        AuditRecordCreate(
            request_id=request_id,
            trace_id=trace_id,
            event_type=AuditEventType.RATE_LIMIT_EXCEEDED,
            resource_type="rate_limit",
            actor_type=ActorType.SYSTEM,
            action="rate_limit_block",
            result=AuditResult.BLOCKED,
            severity=severity,
            user_id=tenant.user_id if tenant else None,
            organization_id=tenant.organization_id if tenant else None,
            metadata={
                "scope": scope,
                "client_ip": client_ip_value,
            },
        )
    )
    _ = session  # request session intentionally not committed here
    logger.warning(
        "rate_limit_exceeded",
        scope=scope,
        client_ip=client_ip_value,
        user_id=str(tenant.user_id) if tenant else None,
        organization_id=str(tenant.organization_id) if tenant else None,
    )


def _enforce_limits(
    *,
    request: Request,
    session: SessionDep,
    audit_service: AuditServiceDep,
    scope: str,
    window_seconds: int,
    ip_limit: int,
    user_limit: int | None,
    tenant: TenantContext | None,
) -> None:
    limiter = get_rate_limiter()
    ip = client_ip(request)
    checks: list[tuple[str, int]] = [(f"{scope}:ip:{ip}", ip_limit)]
    if tenant is not None and user_limit is not None:
        checks.append((f"{scope}:user:{tenant.user_id}", user_limit))

    auth_scopes = {"auth:register", "auth:login", "auth:refresh"}
    severity = AuditSeverity.HIGH if scope in auth_scopes else AuditSeverity.MEDIUM

    for key, limit in checks:
        try:
            limiter.check(key, limit=limit, window_seconds=window_seconds)
        except RateLimitExceededError:
            _audit_rate_limit_violation(
                request=request,
                session=session,
                audit_service=audit_service,
                scope=scope,
                client_ip_value=ip,
                tenant=tenant,
                severity=severity,
            )
            raise


def public_rate_limit_dependency(
    scope: str,
    *,
    limit: int,
    window_seconds: int,
    ip_limit: int | None = None,
) -> Callable[..., None]:
    """IP-scoped rate limit for unauthenticated endpoints."""

    effective_ip_limit = ip_limit or limit

    def _dependency(
        request: Request,
        session: SessionDep,
        audit_service: AuditServiceDep,
    ) -> None:
        _enforce_limits(
            request=request,
            session=session,
            audit_service=audit_service,
            scope=scope,
            window_seconds=window_seconds,
            ip_limit=effective_ip_limit,
            user_limit=None,
            tenant=None,
        )

    return _dependency


def tenant_rate_limit_dependency(
    scope: str,
    *,
    limit: int,
    window_seconds: int,
    ip_limit: int | None = None,
    user_limit: int | None = None,
) -> Callable[..., None]:
    """IP- and user-scoped rate limit for authenticated endpoints."""

    effective_ip_limit = ip_limit or limit
    effective_user_limit = user_limit or limit

    def _dependency(
        request: Request,
        tenant: Annotated[TenantContext, Depends(get_current_tenant)],
        session: SessionDep,
        audit_service: AuditServiceDep,
    ) -> None:
        _enforce_limits(
            request=request,
            session=session,
            audit_service=audit_service,
            scope=scope,
            window_seconds=window_seconds,
            ip_limit=effective_ip_limit,
            user_limit=effective_user_limit,
            tenant=tenant,
        )

    return _dependency


def auth_register_rate_limit(
    request: Request,
    session: SessionDep,
    audit_service: AuditServiceDep,
) -> None:
    """IP budget for registration. Staging and production keep the default of 10."""

    settings = _request_settings(request)
    _enforce_limits(
        request=request,
        session=session,
        audit_service=audit_service,
        scope="auth:register",
        window_seconds=3600,
        ip_limit=settings.auth_register_rate_limit,
        user_limit=None,
        tenant=None,
    )


# Backward-compatible alias used by older route wiring.
def rate_limit_dependency(
    scope: str,
    *,
    limit: int,
    window_seconds: int,
) -> Callable[..., None]:
    return public_rate_limit_dependency(scope, limit=limit, window_seconds=window_seconds)
