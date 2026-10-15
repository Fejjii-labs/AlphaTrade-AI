"""Startup readiness checks for BloFin demo exchange connectivity.

Runs when ``exchange_mode=paper_exchange_demo`` to log a redacted posture line
and verify demo-host, TLS, credential, and key-scope invariants before serving
traffic. Never logs keys, secrets, passphrases, signed payloads, or headers.
"""

from __future__ import annotations

import structlog

from app.core.config import ExchangeMode, ExecutionMode, Settings
from app.core.exchange_safety import is_allowlisted_demo_host, validate_exchange_mode_settings
from app.providers.base import ProviderKind
from app.providers.exchange.errors import ExchangeError
from app.providers.registry import ProviderRegistry

logger = structlog.get_logger(__name__)


def exchange_demo_posture(settings: Settings) -> dict[str, object]:
    """Return a redaction-safe summary of the exchange demo posture."""
    rest_url = settings.blofin_demo_rest_base_url.strip()
    ws_url = settings.blofin_demo_ws_url.strip()
    return {
        "exchange_mode": settings.exchange_mode.value,
        "execution_mode": settings.execution_mode.value,
        "real_trading_enabled": settings.real_trading_enabled,
        "enable_real_trading": settings.enable_real_trading,
        "blofin_demo_enabled": settings.blofin_demo_enabled,
        "blofin_demo_configured": settings.blofin_demo_configured,
        "demo_rest_allowlisted": bool(rest_url and is_allowlisted_demo_host(rest_url)),
        "demo_ws_allowlisted": bool(not ws_url or is_allowlisted_demo_host(ws_url)),
    }


def _find_blofin_account(registry: ProviderRegistry):
    """Return the BloFin account provider when registered."""
    provider = registry.get("blofin-demo-account")
    if provider is not None and hasattr(provider, "get_account_permissions"):
        return provider
    for registered in registry.all():
        if getattr(registered, "name", None) == "blofin-demo-account" and hasattr(
            registered, "get_account_permissions"
        ):
            return registered
    return None


def _log_scope_diagnostics(permissions, *, endpoint: str) -> None:
    """Emit redaction-safe scope diagnostics.

    Logs only field *names*, normalized permission tokens, and derived boolean
    flags - never raw account data, balances, headers, signatures, or secrets.
    """
    logger.info(
        "exchange_demo_scope_diagnostics",
        endpoint=endpoint,
        http_status=200,  # probe returned a successful BloFin envelope (code 0)
        response_shape_keys=list(permissions.response_keys),
        normalized_permissions=list(permissions.raw_scopes),
        has_read=permissions.can_read,
        has_trade=permissions.can_trade,
        has_transfer_or_withdraw=bool(permissions.can_transfer or permissions.can_withdraw),
    )


def _verify_demo_key_scope(account) -> None:
    """Refuse withdraw/transfer scope; require trade scope when probe succeeds."""
    endpoint = getattr(account, "permissions_endpoint", "/api/v1/user/query-apikey")
    try:
        permissions = account.get_account_permissions()
    except ExchangeError as exc:
        # Redacted: typed exchange error text is already sanitized by the client.
        logger.warning(
            "exchange_demo_scope_probe_unavailable",
            endpoint=endpoint,
            error=str(exc)[:200],
        )
        return

    _log_scope_diagnostics(permissions, endpoint=endpoint)

    if permissions.can_withdraw or permissions.can_transfer:
        raise ValueError(
            "Refusing BloFin demo key: it carries withdrawal/transfer scope. "
            "Use a read/trade-only demo key."
        )
    if not permissions.can_trade:
        raise ValueError("Refusing BloFin demo key: trade scope is required (read + trade only).")


def run_exchange_demo_startup_check(settings: Settings, registry: ProviderRegistry) -> None:
    """Log redacted posture and assert demo invariants at startup.

    No-op unless ``exchange_mode=paper_exchange_demo``. Raises ``ValueError`` on
    unsafe configuration or a key with money-movement scope.
    """
    if settings.exchange_mode is not ExchangeMode.PAPER_EXCHANGE_DEMO:
        return

    posture = exchange_demo_posture(settings)
    logger.info("exchange_demo_readiness", **posture)

    # Settings validators already ran; re-assert as defense in depth.
    validate_exchange_mode_settings(settings)

    if settings.execution_mode is not ExecutionMode.PAPER:
        raise ValueError("exchange demo readiness: execution_mode must be paper")
    if settings.real_trading_enabled or settings.enable_real_trading:
        raise ValueError("exchange demo readiness: real trading must stay disabled")

    rest_url = settings.blofin_demo_rest_base_url.strip()
    if not rest_url or not is_allowlisted_demo_host(rest_url):
        raise ValueError("exchange demo readiness: demo REST host must be allowlisted over TLS")
    if not settings.blofin_demo_configured:
        raise ValueError("exchange demo readiness: BloFin demo credentials are required")

    ws_url = settings.blofin_demo_ws_url.strip()
    if ws_url and not is_allowlisted_demo_host(ws_url):
        raise ValueError("exchange demo readiness: demo WS host must be allowlisted over WSS")

    account = _find_blofin_account(registry)
    if account is not None:
        _verify_demo_key_scope(account)
    else:
        logger.warning("exchange_demo_account_provider_missing")


def exchange_provider_status(registry: ProviderRegistry):
    """Return the first exchange provider status, if any."""
    for status in registry.statuses():
        if status.kind is ProviderKind.EXCHANGE:
            return status
    return None
