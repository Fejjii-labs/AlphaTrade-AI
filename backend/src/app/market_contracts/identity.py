"""Typed venue, market, instrument, source, and provider identities."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from pydantic import Field, field_validator, model_validator

from app.market_contracts.enums import (
    ContractStyle,
    MarketType,
    ProductFamily,
    SourceFamily,
    VenueId,
)
from app.market_contracts.errors import WrongInstrumentError, WrongMarketError
from app.market_contracts.models import CanonicalModel, PositiveCanonicalDecimal
from app.schemas.common import Timeframe

ADAPTER_VERSION = "binance-usdm-perpetual/v1"
# Live REST v3 admits a closed row 60 s after close (v2: 5 s) and confirms it twice.
# v1 replay and stored v2 receipts keep their own identity hashes.
BINANCE_REST_ADAPTER_VERSION = "binance-usdm-perpetual/v3"
AGGRESSOR_CONVENTION = "binance-usdm-aggtrade/buyer-is-maker/v1"
BYBIT_ADAPTER_VERSION = "bybit-usdt-perpetual/v1"
BYBIT_AGGRESSOR_CONVENTION = "bybit-linear-public-trade/taker-side/v1"
BYBIT_BTC_CONTRACT_MULTIPLIER = Decimal("1")
HASH_ALGORITHM_VERSION = "canonical-json-sha256/v1"
INTERVAL_SECONDS: dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M3: 180,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1800,
    Timeframe.H1: 3600,
    Timeframe.H2: 7200,
    Timeframe.H4: 14400,
    Timeframe.H6: 21600,
    Timeframe.H12: 43200,
    Timeframe.D1: 86400,
    Timeframe.D3: 259200,
    Timeframe.W1: 604800,
}


class InstrumentIdentity(CanonicalModel):
    """Canonical perpetual instrument identity; independent of provider symbol formatting."""

    venue: VenueId
    market_type: MarketType
    product_family: ProductFamily
    contract_style: ContractStyle
    instrument_id: str = Field(min_length=8, max_length=80)
    provider_symbol: str = Field(min_length=2, max_length=32)
    base_asset: str = Field(min_length=2, max_length=16)
    quote_asset: str = Field(min_length=2, max_length=16)
    settlement_asset: str = Field(min_length=2, max_length=16)
    contract_multiplier: PositiveCanonicalDecimal
    price_unit: str = Field(min_length=1, max_length=16)
    base_quantity_unit: str = Field(min_length=1, max_length=16)
    quote_quantity_unit: str = Field(min_length=1, max_length=16)

    @field_validator("provider_symbol", "base_asset", "quote_asset", "settlement_asset")
    @classmethod
    def _upper_token(cls, value: str) -> str:
        token = value.strip().upper()
        if not token.isalnum():
            raise ValueError("Asset and symbol tokens must be alphanumeric.")
        return token

    @model_validator(mode="after")
    def _linear_usdm_consistency(self) -> InstrumentIdentity:
        if self.market_type is MarketType.PERPETUAL and self.product_family is ProductFamily.SPOT:
            raise WrongMarketError("Spot product family cannot be labelled perpetual.")
        if (
            self.contract_style is ContractStyle.LINEAR
            and self.quote_asset != self.settlement_asset
        ):
            raise ValueError("Linear contracts must settle in the quote asset.")
        return self


class SourceIdentity(CanonicalModel):
    family: SourceFamily
    provider_name: str = Field(min_length=3, max_length=80)
    adapter_version: str = Field(min_length=3, max_length=80)
    aggressor_convention: str = Field(min_length=3, max_length=120)


class ProviderProvenance(CanonicalModel):
    """Read-only provider provenance attached to every evidence envelope."""

    provider_name: str = Field(min_length=3, max_length=80)
    source_family: SourceFamily
    adapter_version: str = Field(min_length=3, max_length=80)
    is_live: bool
    fallback_used: bool
    is_mock: bool
    regional_failure: bool = False
    detail: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _no_silent_fallback(self) -> ProviderProvenance:
        if self.fallback_used:
            raise ValueError("Perpetual evidence provenance cannot record a silent fallback.")
        if self.is_live and self.is_mock:
            raise ValueError("Live evidence cannot also be mock.")
        return self


class EvidenceMarketIdentity(CanonicalModel):
    """Full identity required on every first-slice evidence object."""

    venue: VenueId
    market_type: MarketType
    instrument: InstrumentIdentity
    timeframe: Timeframe | None = None
    source: SourceIdentity
    provenance: ProviderProvenance

    @model_validator(mode="after")
    def _instrument_matches_envelope(self) -> EvidenceMarketIdentity:
        inst = self.instrument
        if inst.venue != self.venue:
            raise WrongMarketError("Instrument venue does not match envelope venue.")
        if inst.market_type != self.market_type:
            raise WrongMarketError("Instrument market type does not match envelope market type.")
        return self


def interval_timedelta(timeframe: Timeframe) -> timedelta:
    try:
        seconds = INTERVAL_SECONDS[timeframe]
    except KeyError as exc:
        raise ValueError(f"Unsupported timeframe {timeframe.value}.") from exc
    return timedelta(seconds=seconds)


def canonical_instrument_id(
    *,
    venue: VenueId,
    product_family: ProductFamily,
    market_type: MarketType,
    symbol: str,
) -> str:
    return f"{venue.value}:{product_family.value}:{market_type.value}:{symbol.upper()}"


def require_linear_usdt_symbol(symbol: str) -> tuple[str, str]:
    """Return ``(symbol, base)`` for one alphanumeric linear USDT perpetual.

    The catalog and the contract book decide which identities may be scanned.
    This helper only rejects tokens that cannot be a linear USDT-margined perpetual.
    """

    token = symbol.strip().upper()
    if not token.isalnum():
        raise WrongInstrumentError("Perpetual symbols must be alphanumeric.")
    if not token.endswith("USDT") or len(token) < 7:
        raise WrongInstrumentError(
            "USD-M perpetual evidence requires a linear USDT-margined symbol."
        )
    base = token[:-4]
    if len(base) < 2 or len(base) > 16:
        raise WrongInstrumentError("USD-M perpetual base asset is missing.")
    return token, base


def binance_usdm_perpetual(symbol: str) -> InstrumentIdentity:
    """Linear Binance USD-M perpetual identity for an alphanumeric USDT symbol.

    First-slice runtime enablement is the catalog, not this factory. Unknown or
    non-USDT tokens fail closed here so callers cannot mint a spot-like identity.
    """
    token, base = require_linear_usdt_symbol(symbol)
    return InstrumentIdentity(
        venue=VenueId.BINANCE,
        market_type=MarketType.PERPETUAL,
        product_family=ProductFamily.USDM_FUTURES,
        contract_style=ContractStyle.LINEAR,
        instrument_id=canonical_instrument_id(
            venue=VenueId.BINANCE,
            product_family=ProductFamily.USDM_FUTURES,
            market_type=MarketType.PERPETUAL,
            symbol=token,
        ),
        provider_symbol=token,
        base_asset=base,
        quote_asset="USDT",
        settlement_asset="USDT",
        contract_multiplier=Decimal("1"),
        price_unit="USDT",
        base_quantity_unit=base,
        quote_quantity_unit="USDT",
    )


def binance_usdm_btcusdt() -> InstrumentIdentity:
    """Canonical first-slice instrument: Binance USD-M BTCUSDT perpetual."""
    return binance_usdm_perpetual("BTCUSDT")


def bybit_usdt_perpetual(symbol: str) -> InstrumentIdentity:
    """Bybit linear USDT perpetual. Trade size is base-coin quantity.

    The multiplier stays 1. A symbol is not scan-eligible until the contract
    book verifies that exact Bybit linear perpetual. This factory does not
    substitute another market.
    """

    token, base = require_linear_usdt_symbol(symbol)
    return InstrumentIdentity(
        venue=VenueId.BYBIT,
        market_type=MarketType.PERPETUAL,
        product_family=ProductFamily.USDM_FUTURES,
        contract_style=ContractStyle.LINEAR,
        instrument_id=canonical_instrument_id(
            venue=VenueId.BYBIT,
            product_family=ProductFamily.USDM_FUTURES,
            market_type=MarketType.PERPETUAL,
            symbol=token,
        ),
        provider_symbol=token,
        base_asset=base,
        quote_asset="USDT",
        settlement_asset="USDT",
        contract_multiplier=BYBIT_BTC_CONTRACT_MULTIPLIER,
        price_unit="USDT",
        base_quantity_unit=base,
        quote_quantity_unit="USDT",
    )


def bybit_usdt_perpetual_btcusdt() -> InstrumentIdentity:
    """Bybit linear USDT perpetual BTCUSDT. Trade size is base-coin quantity."""
    return bybit_usdt_perpetual("BTCUSDT")


def instrument_matches_requested_symbol(identity: EvidenceMarketIdentity, symbol: str) -> bool:
    """True when the envelope instrument is the requested symbol on its own venue.

    Binance and Bybit identities are built separately. A Binance payload cannot
    satisfy a Bybit request, and a different symbol cannot satisfy this one.
    """

    try:
        token, _base = require_linear_usdt_symbol(symbol)
    except WrongInstrumentError:
        return False
    if identity.venue is VenueId.BINANCE:
        expected = binance_usdm_perpetual(token)
    elif identity.venue is VenueId.BYBIT:
        expected = bybit_usdt_perpetual(token)
    else:
        return False
    return identity.instrument.instrument_id == expected.instrument_id


def require_perpetual(identity: EvidenceMarketIdentity) -> None:
    if identity.market_type is not MarketType.PERPETUAL:
        raise WrongMarketError(
            f"Perpetual evidence required; received market_type={identity.market_type.value}."
        )
    inst = identity.instrument
    if inst.market_type is not MarketType.PERPETUAL:
        raise WrongMarketError("Instrument is not a perpetual contract.")
    if inst.product_family is ProductFamily.SPOT:
        raise WrongMarketError("Spot product family is incompatible with perpetual evidence.")
    if inst.product_family is ProductFamily.COINM_FUTURES:
        raise WrongMarketError("Coin-M futures cannot substitute for USD-M perpetual evidence.")


def require_instrument(identity: EvidenceMarketIdentity, expected: InstrumentIdentity) -> None:
    if identity.instrument.instrument_id != expected.instrument_id:
        raise WrongInstrumentError(
            "Instrument mismatch: "
            f"expected {expected.instrument_id}, received {identity.instrument.instrument_id}."
        )
    if identity.instrument.provider_symbol != expected.provider_symbol:
        raise WrongInstrumentError(
            "Provider symbol mismatch: "
            f"expected {expected.provider_symbol}, "
            f"received {identity.instrument.provider_symbol}."
        )
