"""Resolve the exchange provider set from settings.

Default and only-safe path is the mock exchange. The BloFin demo providers are
returned solely when ``exchange_mode=paper_exchange_demo`` with demo credentials
present. Any key carrying withdrawal/transfer scope is refused outright.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import structlog

from app.core.config import Settings
from app.core.execution_credentials import (
    blofin_execution_authorized,
    load_blofin_execution_credentials,
)
from app.core.paper_safety import (
    assert_execution_capable_composition_root,
    assert_permanent_paper_mode,
)
from app.providers.base import BaseMockProvider, Provider, ProviderKind
from app.providers.exchange.base import ExchangeAccountProvider, ExchangeExecutionProvider
from app.providers.exchange.blofin_account import BloFinAccountProvider
from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.blofin_execution import BloFinDemoExecutionProvider
from app.providers.exchange.blofin_market_data import BloFinMarketDataProvider
from app.providers.exchange.errors import ExchangeError
from app.providers.market_data import MarketDataProvider

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ResolvedExchange:
    """Resolved exchange capabilities for the current settings."""

    status_provider: Provider
    account: ExchangeAccountProvider | None
    market_data: MarketDataProvider | None
    execution: ExchangeExecutionProvider | None
    is_demo: bool


def build_blofin_client(
    settings: Settings, *, transport: httpx.BaseTransport | None = None
) -> BloFinClient:
    """Construct a signed BloFin demo client only after the execution gate opens.

    Paper isolation and incomplete gate combinations raise
    ``CredentialAccessDeniedError`` before any client or network call. Secret
    values are not included in the error.
    """
    credentials = load_blofin_execution_credentials(settings)
    return BloFinClient(
        base_url=settings.blofin_demo_rest_base_url.strip(),
        api_key=credentials.api_key,
        api_secret=credentials.api_secret,
        api_passphrase=credentials.api_passphrase,
        timeout_seconds=settings.blofin_request_timeout_seconds,
        max_retries=settings.blofin_max_retries,
        rate_limit_requests_per_second=settings.blofin_rate_limit_requests_per_second,
        transport=transport,
    )


def _mock_exchange(settings: Settings) -> ResolvedExchange:
    detail = (
        f"Paper/mock exchange only; real trading disabled "
        f"(execution_mode={settings.execution_mode.value})."
    )
    return ResolvedExchange(
        status_provider=BaseMockProvider("mock-exchange", ProviderKind.EXCHANGE, detail=detail),
        account=None,
        market_data=None,
        execution=None,
        is_demo=False,
    )


def resolve_exchange_provider(
    settings: Settings, *, transport: httpx.BaseTransport | None = None
) -> ResolvedExchange:
    """Return the mock exchange unless BloFin demo is fully configured.

    Raises ``ValueError`` if the demo API key carries money-movement scope.
    """
    assert_permanent_paper_mode(settings)
    if not blofin_execution_authorized(settings):
        return _mock_exchange(settings)

    client = build_blofin_client(settings, transport=transport)
    account = BloFinAccountProvider(client)
    market_data = BloFinMarketDataProvider(client)
    execution = BloFinDemoExecutionProvider(
        client,
        account,
        real_trading_enabled=settings.real_trading_enabled,
        exchange_demo_active=settings.exchange_demo_active,
    )

    _verify_no_money_movement(account)

    return ResolvedExchange(
        status_provider=account,
        account=account,
        market_data=market_data,
        execution=execution,
        is_demo=True,
    )


def resolve_exchange_execution_provider(
    settings: Settings, *, transport: httpx.BaseTransport | None = None
) -> ExchangeExecutionProvider | None:
    """Return a demo execution provider, or ``None`` when not in demo mode.

    The provider re-verifies safety (real trading disabled, demo host) on every
    call, so this resolver does not perform the network permission probe.
    """
    assert_execution_capable_composition_root(settings)
    if not blofin_execution_authorized(settings):
        return None
    if settings.real_trading_enabled:  # defense-in-depth; should be impossible
        return None
    client = build_blofin_client(settings, transport=transport)
    account = BloFinAccountProvider(client)
    return BloFinDemoExecutionProvider(
        client,
        account,
        real_trading_enabled=settings.real_trading_enabled,
        exchange_demo_active=settings.exchange_demo_active,
    )


def _verify_no_money_movement(account: BloFinAccountProvider) -> None:
    """Refuse a key with withdrawal/transfer scope; tolerate probe unavailability.

    A confirmed money-movement scope is a hard stop. If the venue cannot be
    reached to verify, we log and continue read-only (no execution is enabled in
    this slice; the demo execution provider re-verifies before placing orders).
    """
    try:
        permissions = account.get_account_permissions()
    except ExchangeError as exc:
        logger.warning("blofin_permission_probe_failed", error=str(exc)[:200])
        return

    if permissions.can_withdraw or permissions.can_transfer:
        raise ValueError(
            "Refusing BloFin demo key: it carries withdrawal/transfer scope. "
            "Use a read/trade-only demo key."
        )
