"""Contracts and value objects for exchange providers (BloFin demo).

These protocols mirror the platform's existing provider style: small, typed
``Protocol`` interfaces plus a ``status()`` method that never raises. Market data
reuses :class:`app.providers.market_data.MarketDataProvider`; this module adds
account (read-only) and execution (demo, wired in a later slice) contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol, runtime_checkable

from app.providers.base import ProviderStatus
from app.schemas.common import OrderSide, OrderType


@dataclass(frozen=True)
class ExchangeInstrument:
    """A tradable instrument as reported by the venue."""

    symbol: str
    inst_id: str
    base_currency: str
    quote_currency: str
    instrument_type: str
    tick_size: Decimal | None = None
    lot_size: Decimal | None = None
    min_size: Decimal | None = None
    contract_size: Decimal | None = None
    active: bool = True


@dataclass(frozen=True)
class ExchangeBalance:
    """A single asset balance on the (demo) account."""

    asset: str
    total: Decimal
    available: Decimal
    equity: Decimal | None = None


@dataclass(frozen=True)
class ExchangePositionData:
    """An open position on the (demo) account."""

    symbol: str
    inst_id: str
    side: str
    size: Decimal
    entry_price: Decimal | None = None
    mark_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    leverage: Decimal | None = None


@dataclass(frozen=True)
class AccountPositionMode:
    """User position mode on the demo account (one-way vs hedge)."""

    position_mode: str


@dataclass(frozen=True)
class AccountLeverageInfo:
    """Configured leverage for an instrument and margin mode."""

    inst_id: str
    margin_mode: str
    leverage: Decimal
    position_side: str | None = None
    margin_mode_supplied: bool = True


@dataclass(frozen=True)
class AccountPermissions:
    """Permission scopes for the configured API key.

    ``can_withdraw``/``can_transfer`` MUST be false for a demo key the platform
    is willing to use. Startup refuses keys with money-movement scopes.
    """

    can_read: bool
    can_trade: bool
    can_withdraw: bool
    can_transfer: bool
    raw_scopes: tuple[str, ...] = field(default_factory=tuple)
    # Field *names* (never values) seen in the venue response, for redacted
    # startup diagnostics. Safe to log: contains no account data or secrets.
    response_keys: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ExchangeOrderRequest:
    """A demo order request (used by the execution provider in a later slice)."""

    symbol: str
    inst_id: str
    side: OrderSide
    order_type: OrderType
    size: Decimal
    price: Decimal | None = None
    reduce_only: bool = False
    client_order_id: str | None = None


@dataclass(frozen=True)
class ExchangeFill:
    """A single fill against a demo order."""

    fill_id: str
    order_id: str
    price: Decimal
    size: Decimal
    fee: Decimal
    fee_currency: str | None = None


@dataclass(frozen=True)
class ExchangeOrderResult:
    """Result of placing/querying a demo order."""

    exchange_order_id: str
    client_order_id: str | None
    status: str
    filled_size: Decimal
    average_price: Decimal | None
    fills: tuple[ExchangeFill, ...] = field(default_factory=tuple)
    position_mode: str | None = None
    position_side: str | None = None


@runtime_checkable
class ExchangeAccountProvider(Protocol):
    """Read-only access to instruments, balances, positions, and permissions."""

    name: str

    def get_instruments(self) -> list[ExchangeInstrument]: ...

    def get_balances(self) -> list[ExchangeBalance]: ...

    def get_positions(self) -> list[ExchangePositionData]: ...

    def get_account_permissions(self) -> AccountPermissions: ...

    def status(self) -> ProviderStatus: ...


@runtime_checkable
class ExchangeExecutionProvider(Protocol):
    """Demo-only order execution. Implemented in a later slice.

    Implementations must guard every call against a non-demo host and against
    ``real_trading_enabled`` before touching the network.
    """

    name: str

    def place_order(self, request: ExchangeOrderRequest) -> ExchangeOrderResult: ...

    def cancel_order(self, *, inst_id: str, exchange_order_id: str) -> None: ...

    def get_order(self, *, inst_id: str, exchange_order_id: str) -> ExchangeOrderResult: ...

    def status(self) -> ProviderStatus: ...
