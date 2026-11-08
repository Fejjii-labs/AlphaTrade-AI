"""Shared guards and helpers for owner-scoped BloFin demo exchange routes."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from app.core.config import Environment, Settings
from app.core.errors import (
    ExchangeDemoInactiveError,
    ExchangeProviderError,
    ForbiddenError,
    TradingPolicyError,
)
from app.guardrails.redaction import redact_text
from app.providers.exchange.base import ExchangeAccountProvider, ExchangeExecutionProvider
from app.providers.exchange.errors import ExchangeError
from app.providers.exchange.factory import (
    resolve_exchange_execution_provider,
    resolve_exchange_provider,
)


def ensure_demo_exchange_access(settings: Settings) -> None:
    """Refuse demo probes when real trading is enabled or demo mode is inactive."""
    if settings.real_trading_enabled:
        raise TradingPolicyError(
            "Real trading is disabled in this environment.",
            details={"execution_mode": settings.execution_mode.value},
        )
    if not settings.exchange_demo_active or not settings.blofin_demo_configured:
        raise ExchangeDemoInactiveError(
            "BloFin demo exchange is not active.",
            details={"exchange_mode": settings.exchange_mode.value},
        )


def ensure_demo_account_organization(settings: Settings, organization_id: uuid.UUID) -> None:
    """Refuse shared demo-account reads/actions for any org except the pinned one.

    The BloFin demo credentials are one global venue account, so balances,
    positions and orders belong to ``GOVERNED_BLOFIN_DEMO_ORGANIZATION_ID`` only
    (same pin as ``manual_demo_service``). Unpinned is tolerated in ``local``
    only; elsewhere it fails closed.
    """
    configured = settings.governed_blofin_demo_organization_id.strip()
    if not configured:
        if settings.environment is Environment.LOCAL:
            return
        raise ForbiddenError(
            "BloFin demo account access requires a pinned demo organization.",
            details={"reason": "demo_organization_unpinned"},
        )
    try:
        pinned = uuid.UUID(configured)
    except ValueError:
        pinned = None
    if pinned != organization_id:
        raise ForbiddenError(
            "BloFin demo account is restricted to the configured demo workspace.",
            details={"reason": "demo_organization_mismatch"},
        )


def get_demo_account_provider(settings: Settings) -> ExchangeAccountProvider:
    """Return the wired demo account provider or raise a safe inactive error."""
    ensure_demo_exchange_access(settings)
    resolved = resolve_exchange_provider(settings)
    if resolved.account is None:
        raise ExchangeDemoInactiveError(
            "BloFin demo account provider is not available.",
            details={"exchange_mode": settings.exchange_mode.value},
        )
    return resolved.account


def get_demo_execution_provider(settings: Settings) -> ExchangeExecutionProvider:
    """Return the wired demo execution provider or raise a safe inactive error."""
    ensure_demo_exchange_access(settings)
    provider = resolve_exchange_execution_provider(settings)
    if provider is None:
        raise ExchangeDemoInactiveError(
            "BloFin demo execution provider is not available.",
            details={"exchange_mode": settings.exchange_mode.value},
        )
    return provider


def run_demo_provider_call[T](label: str, fn: Callable[[], T]) -> T:
    """Execute a provider call and map venue errors to redacted API errors."""
    try:
        return fn()
    except ExchangeError as exc:
        raise ExchangeProviderError(
            f"BloFin demo {label} unavailable.",
            details={"error": redact_text(str(exc))[:200]},
        ) from exc
