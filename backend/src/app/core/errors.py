"""Application error types and centralized FastAPI exception handlers.

The goal is to *fail clearly*: every error returns a consistent, typed JSON body
carrying the request id, and unexpected errors are logged with a stack trace but
never leak internal details to the client.
"""

from __future__ import annotations

import math
from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.market_contracts.errors import RateLimitedError, UpstreamBanError

logger = structlog.get_logger(__name__)


class AppError(Exception):
    """Base class for expected, handled application errors.

    Attributes:
        message: Human-readable, client-safe message.
        status_code: HTTP status to return.
        code: Stable machine-readable error code for clients/telemetry.
        details: Optional structured, client-safe context.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        self.details = details or {}


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ValidationAppError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_error"


class AuthError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class ConflictError(AppError):
    """Raised when a mutation conflicts with concurrent state (e.g. version mismatch)."""

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class JournalProjectionConflictError(ConflictError):
    """Same journal source identity with a different semantic content hash."""

    code = "journal_projection_conflict"


class IdempotencyConvergenceError(ConflictError):
    """Raised when concurrent idempotent writers could not converge within bounded retries."""

    code = "idempotency_convergence_exhausted"


class TradingPolicyError(AppError):
    """Raised when an action violates a hard trading-safety policy."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "trading_policy_violation"


class ExchangeDemoInactiveError(AppError):
    """Raised when a BloFin demo probe is requested outside demo mode."""

    status_code = status.HTTP_409_CONFLICT
    code = "exchange_demo_inactive"


class ExchangeProviderError(AppError):
    """Raised when a demo exchange provider call fails (redacted message only)."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "exchange_provider_error"


class QuotaExceededError(AppError):
    """Raised when an organization exceeds a hard usage quota."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "quota_exceeded"


class ServiceUnavailableError(AppError):
    """Raised when an optional integration is not configured or enabled."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"


class PersistencePolicyError(AppError):
    """Raised when a READ_ONLY (or other) operation class forbids a domain write."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "readonly_persistence_denied"


def _error_body(*, code: str, message: str, request: Request, details: dict | None = None) -> dict:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        body["error"]["request_id"] = request_id
    if details:
        body["error"]["details"] = details
    return body


_DEFAULT_RETRY_AFTER_SECONDS = 60
_MAX_RETRY_AFTER_SECONDS = 900


def _retry_after_header_value(seconds: float | None) -> int:
    """Whole seconds for a Retry-After header: finite, >= 1, capped at 900."""
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return _DEFAULT_RETRY_AFTER_SECONDS
    return max(1, min(_MAX_RETRY_AFTER_SECONDS, math.ceil(seconds)))


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers that produce consistent error envelopes."""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning("app_error", code=exc.code, status_code=exc.status_code, message=exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(
                code=exc.code, message=exc.message, request=request, details=exc.details
            ),
        )

    @app.exception_handler(RateLimitedError)
    async def _handle_upstream_rate_limit(request: Request, exc: RateLimitedError) -> JSONResponse:
        # Binance futures 429/418 (or our process-wide gate during backoff):
        # a temporary upstream condition, not a server bug -> 503 + Retry-After.
        retry_after = _retry_after_header_value(exc.retry_after_seconds)
        code = "upstream_banned" if isinstance(exc, UpstreamBanError) else "upstream_rate_limited"
        logger.warning("upstream_rate_limited", code=code, retry_after=retry_after)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": str(retry_after)},
            content=_error_body(
                code=code,
                message="Market data provider is temporarily rate limited. Retry later.",
                request=request,
                details={"retry_after_seconds": retry_after},
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Pydantic error dicts may carry non-JSON-native values (e.g. Decimal in
        # ``input``); encode them so invalid input yields a clean 422, not a 500.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_body(
                code="validation_error",
                message="Request validation failed.",
                request=request,
                details={"errors": jsonable_encoder(exc.errors())},
            ),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Log full detail server-side; return an opaque message to the client.
        logger.error("unhandled_exception", error_type=type(exc).__name__, exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body(
                code="internal_error",
                message="An unexpected error occurred.",
                request=request,
            ),
        )


# Shared 404 messages: "missing" and "not owned" must be byte-identical so the
# response body cannot be used as an existence oracle for another user's ids.
JOURNAL_TRADE_NOT_FOUND_MSG = "Journal trade not found."
JOURNAL_ENTRY_NOT_FOUND_MSG = "Journal entry not found."
