"""Fail-closed errors for perpetual market source contracts."""

from __future__ import annotations


class MarketContractError(ValueError):
    """Base error for evidence that cannot be used."""


class FormingCandleError(MarketContractError):
    """A forming (non-final) candle was supplied where FINAL is required."""


class SettlementGuardError(FormingCandleError):
    """The venue refused a read inside its post-close settlement guard."""


class WrongMarketError(MarketContractError):
    """Venue or market type is incompatible with the requested evidence contract."""


class WrongInstrumentError(MarketContractError):
    """Instrument identity does not match the requested canonical instrument."""


class WrongSourceError(MarketContractError):
    """Trade provider/source or retrieval lineage does not match its evidence identity."""


class GapDetectedError(MarketContractError):
    """A sequence or interval gap was detected in the selected evidence."""


class UnrecoverableGapError(GapDetectedError):
    """A gap remains after bounded reconnect backfill and must fail closed."""


class DuplicateDataError(MarketContractError):
    """The same natural event arrived with conflicting semantic content."""


class OutOfOrderTradesError(MarketContractError):
    """Trades arrived out of venue sequence without an explicit reconnect."""


class StaleEvidenceError(MarketContractError):
    """Evidence exceeded the versioned freshness policy."""


class UnknownAggressorError(MarketContractError):
    """Aggressor side or convention is missing, so signed flow is unusable."""


class IncompleteWarmUpError(MarketContractError):
    """CVD/flow was requested before contiguous warm-up completed."""


class SpotFallbackRejectedError(MarketContractError):
    """Spot (or other incompatible) market data cannot satisfy perpetual evidence."""


class RegionalProviderFailureError(MarketContractError):
    """The preferred perpetual source is unreachable in this runtime region."""


class RateLimitedError(MarketContractError):
    """Preferred source asked the client to back off (HTTP 429). Not an outage."""

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class UpstreamBanError(RateLimitedError):
    """Binance HTTP 418 IP ban. Temporary. Not a 401/403/451 regional block."""


class RetrievalBudgetExceededError(Exception):
    """A bounded read stopped because its wall-clock budget was spent.

    This is not a market-contract failure. Chat retrieval uses it so a long
    upstream wait cannot be turned into a partial quote and then continued.
    """


class NetworkMutationForbiddenError(MarketContractError):
    """The read-only adapter refused a non-GET or non-allowlisted path."""


class CursorRecoveryError(MarketContractError):
    """Reconnect recovery could not prove contiguous coverage."""


class FallbackForbiddenError(MarketContractError):
    """Silent fallback across providers, markets, or mock substitutes is forbidden."""


class IncompleteTradeWindowError(GapDetectedError):
    """Live aggTrade retrieval could not prove complete coverage of the requested window."""


class UnsupportedTradeContractError(MarketContractError):
    """The provider cannot verify this instrument as a supported trade product."""


class EmptyTradeWindowError(IncompleteTradeWindowError):
    """A drained supported trade window contains no prints."""


class UnapprovedEvidenceHostError(WrongMarketError):
    """Evidence host is not an approved public perpetual HTTPS identity."""


class EvidenceSourceSwitchRequiredError(MarketContractError):
    """Primary source failed. The next read must use the secondary identity.

    The failed primary payload is not returned. Callers rebind provenance
    before any secondary fetch.
    """

    def __init__(self, message: str, *, instrument: object) -> None:
        super().__init__(message)
        self.instrument = instrument


class ContractUnavailableError(RuntimeError):
    """Verified eligibility refusal; preserve its reason at the evidence boundary."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)
