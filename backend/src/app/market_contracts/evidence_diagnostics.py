"""Bounded, payload-free diagnostics. They are metadata, never evidence authority."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from typing import Literal, TypeVar

import httpx
import structlog
from pydantic import AwareDatetime, Field

from app.market_contracts import errors
from app.market_contracts.enums import FreshnessState, SourceFamily
from app.market_contracts.freshness import (
    FreshnessEvaluation,
    evaluate_freshness,
    first_slice_freshness_policy,
)
from app.market_contracts.identity import EvidenceMarketIdentity
from app.market_contracts.models import CanonicalModel
from app.market_contracts.ohlcv import ClosedOhlcvSeries
from app.schemas.common import Timeframe

MAX_COMPONENT_DIAGNOSTICS = 40
_T = TypeVar("_T")
_PROVIDERS = frozenset(
    {"binance-usdm-perpetual", "bybit-usdt-perpetual", "binance-usdm-perpetual-replay"}
)
logger = structlog.get_logger(__name__)


class EvidenceComponent(StrEnum):
    INSTRUMENT = "instrument_identity"
    OHLCV_15M = "ohlcv_15m"
    OHLCV_4H = "ohlcv_4h"
    TRIGGER = "trigger_context"
    TRADES = "aggregate_trades"
    COVERAGE = "coverage_proof"
    CVD = "cvd"
    SIGNED_FLOW = "signed_flow"
    FRESHNESS = "freshness"
    PRICE = "current_price"
    RESISTANCE = "manual_resistance"
    OPEN_INTEREST = "open_interest"
    FUNDING = "funding"
    ORDER_FLOW = "order_flow_5m"
    CONTRACT = "canonical_contract"
    MONITOR = "market_monitor"
    FAILOVER = "provider_failover"


class DiagnosticStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_REQUIRED = "not_required"
    SWITCH_REQUIRED = "switch_required"


class DiagnosticReason(StrEnum):
    OK = "ok"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"
    INCOMPLETE = "incomplete"
    STALE = "stale"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_BAN = "upstream_ban"
    REGIONAL_FAILURE = "regional_failure"
    PROXY_FAILURE = "proxy_failure"
    TRANSPORT_FAILURE = "transport_failure"
    WRONG_INSTRUMENT = "wrong_instrument"
    WRONG_MARKET = "wrong_market"
    WRONG_SOURCE = "wrong_source"
    SPOT_REJECTED = "spot_rejected"
    FALLBACK_FORBIDDEN = "fallback_forbidden"
    FORMING_CANDLE = "forming_candle"
    COVERAGE_INCOMPLETE = "coverage_incomplete"
    GAP = "gap"
    DUPLICATE = "duplicate"
    OUT_OF_ORDER = "out_of_order"
    UNKNOWN_AGGRESSOR = "unknown_aggressor"
    WARMUP_INCOMPLETE = "warmup_incomplete"
    CONTRACT_UNAVAILABLE = "contract_unavailable"
    INVALID_CONTRACT = "invalid_contract"
    UNEXPECTED_ERROR = "unexpected_error"
    SWITCH_REQUIRED = "switch_required"
    NOT_REQUIRED = "not_required"


class EvidenceComponentDiagnostic(CanonicalModel):
    schema_version: Literal["EvidenceComponentDiagnosticV1"] = "EvidenceComponentDiagnosticV1"
    component: EvidenceComponent
    provider: str = Field(
        pattern=r"^(binance-usdm-perpetual(-replay)?|bybit-usdt-perpetual|unknown)$"
    )
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,32}$")
    timeframe: Timeframe | None = None
    status: DiagnosticStatus
    reason_code: DiagnosticReason
    freshness: FreshnessState = FreshnessState.UNKNOWN
    source_time: AwareDatetime | None = None
    evaluated_at: AwareDatetime
    # Closed historical evidence is valid on its finality clock; never label it a live quote.
    historical: bool = False
    failover_attempted: bool = False
    attempt: int = Field(default=1, ge=1, le=2)
    source_family: SourceFamily | None = None
    instrument_id: str | None = Field(default=None, max_length=80)

    @property
    def watcher_reason(self) -> str:
        prefix = "" if self.component is EvidenceComponent.CONTRACT else "canonical_"
        return f"{prefix}{self.component.value}_{self.reason_code.value}"


def reason_for_exception(exc: Exception) -> DiagnosticReason:
    """Type allowlist only. Provider error messages/URLs/payloads never enter output."""
    explicit = getattr(exc, "canonical_reason", None)
    if isinstance(explicit, DiagnosticReason):
        return explicit
    if isinstance(exc, errors.EvidenceSourceSwitchRequiredError):
        cause = exc.__cause__
        return (
            reason_for_exception(cause)
            if isinstance(cause, Exception)
            else (DiagnosticReason.SWITCH_REQUIRED)
        )
    if isinstance(exc, errors.RegionalProviderFailureError):
        if isinstance(exc.__cause__, httpx.ProxyError):
            return DiagnosticReason.PROXY_FAILURE
        if isinstance(exc.__cause__, httpx.TransportError):
            return DiagnosticReason.TRANSPORT_FAILURE
    for error_type, reason in (
        (errors.UpstreamBanError, DiagnosticReason.UPSTREAM_BAN),
        (errors.RateLimitedError, DiagnosticReason.RATE_LIMITED),
        (errors.RegionalProviderFailureError, DiagnosticReason.REGIONAL_FAILURE),
        (errors.StaleEvidenceError, DiagnosticReason.STALE),
        (errors.WrongInstrumentError, DiagnosticReason.WRONG_INSTRUMENT),
        (errors.SpotFallbackRejectedError, DiagnosticReason.SPOT_REJECTED),
        (errors.WrongSourceError, DiagnosticReason.WRONG_SOURCE),
        (errors.WrongMarketError, DiagnosticReason.WRONG_MARKET),
        (errors.EmptyTradeWindowError, DiagnosticReason.MISSING),
        (errors.IncompleteTradeWindowError, DiagnosticReason.COVERAGE_INCOMPLETE),
        (errors.DuplicateDataError, DiagnosticReason.DUPLICATE),
        (errors.GapDetectedError, DiagnosticReason.GAP),
        (errors.OutOfOrderTradesError, DiagnosticReason.OUT_OF_ORDER),
        (errors.FormingCandleError, DiagnosticReason.FORMING_CANDLE),
        (errors.UnknownAggressorError, DiagnosticReason.UNKNOWN_AGGRESSOR),
        (errors.IncompleteWarmUpError, DiagnosticReason.WARMUP_INCOMPLETE),
        (errors.UnsupportedTradeContractError, DiagnosticReason.UNSUPPORTED),
        (errors.FallbackForbiddenError, DiagnosticReason.FALLBACK_FORBIDDEN),
        (errors.ContractUnavailableError, DiagnosticReason.CONTRACT_UNAVAILABLE),
        (errors.MarketContractError, DiagnosticReason.INVALID_CONTRACT),
        (ValueError, DiagnosticReason.INVALID_CONTRACT),
        (TypeError, DiagnosticReason.INVALID_CONTRACT),
        (KeyError, DiagnosticReason.INVALID_CONTRACT),
    ):
        if isinstance(exc, error_type):
            return reason
    return DiagnosticReason.UNEXPECTED_ERROR


def exception_class_name(exc: BaseException) -> str:
    """The nearest application or builtin class name, so ``invalid_contract`` and
    ``unexpected_error`` keep their cause. Never a third-party class name or text."""
    for cls in type(exc).__mro__:
        if cls.__module__ == "builtins" or cls.__module__.startswith("app."):
            return cls.__name__
    return "Exception"


def diagnostic_from_exception(exc: Exception) -> EvidenceComponentDiagnostic | None:
    item = getattr(exc, "canonical_component_diagnostic", None)
    return item if isinstance(item, EvidenceComponentDiagnostic) else None


def canonical_diagnostic_reason(reason_code: str) -> DiagnosticReason | None:
    """Recognize only this finite contract when projecting existing measurement categories."""
    for component in EvidenceComponent:
        prefix = "" if component is EvidenceComponent.CONTRACT else "canonical_"
        for reason in DiagnosticReason:
            if reason_code == f"{prefix}{component.value}_{reason.value}":
                return reason
    return None


PROVIDER_FAILURE_REASONS = frozenset(
    {
        DiagnosticReason.REGIONAL_FAILURE,
        DiagnosticReason.RATE_LIMITED,
        DiagnosticReason.UPSTREAM_BAN,
        DiagnosticReason.PROXY_FAILURE,
        DiagnosticReason.TRANSPORT_FAILURE,
    }
)


class EvidenceDiagnostics:
    """One assembly, at most two attempts. Never retains results, errors or trade tapes."""

    def __init__(self, source: object, symbol: str, evaluated_at: datetime) -> None:
        self.source = source
        token = symbol.strip().upper()
        self.symbol = token if re.fullmatch(r"[A-Z0-9]{2,32}", token) else "UNKNOWN"
        self.evaluated_at = evaluated_at
        self.attempt = 1
        self.failover_attempted = bool(getattr(source, "using_secondary", False))
        self.identity: EvidenceMarketIdentity | None = None
        self._items: list[EvidenceComponentDiagnostic] = []

    @property
    def items(self) -> tuple[EvidenceComponentDiagnostic, ...]:
        return tuple(self._items)

    def record(
        self,
        component: EvidenceComponent,
        *,
        timeframe: Timeframe | None = None,
        status: DiagnosticStatus = DiagnosticStatus.AVAILABLE,
        reason: DiagnosticReason = DiagnosticReason.OK,
        freshness: FreshnessState = FreshnessState.UNKNOWN,
        source_time: datetime | None = None,
        historical: bool = False,
        provider: str | None = None,
        exception_class: str | None = None,
    ) -> EvidenceComponentDiagnostic:
        name = provider or getattr(self.source, "name", "unknown")
        item = EvidenceComponentDiagnostic(
            component=component,
            provider=name if name in _PROVIDERS else "unknown",
            symbol=self.symbol,
            timeframe=timeframe,
            status=status,
            reason_code=reason,
            freshness=freshness,
            source_time=source_time,
            evaluated_at=self.evaluated_at,
            historical=historical,
            failover_attempted=self.failover_attempted,
            attempt=self.attempt,
            source_family=self.identity.source.family if self.identity is not None else None,
            instrument_id=self.identity.instrument.instrument_id
            if self.identity is not None
            else None,
        )
        self._items.append(item)
        del self._items[:-MAX_COMPONENT_DIAGNOSTICS]
        if status in {DiagnosticStatus.UNAVAILABLE, DiagnosticStatus.SWITCH_REQUIRED}:
            fields = item.model_dump(mode="json")
            if exception_class is not None:
                fields["exception_class"] = exception_class
            logger.warning("canonical_evidence_component", **fields)
        return item

    def run(
        self,
        component: EvidenceComponent,
        operation: Callable[[], _T],
        *,
        timeframe: Timeframe | None = None,
        success: bool = True,
    ) -> _T:
        with self.stage(component, timeframe=timeframe, success=success) as probe:
            value = operation()
            probe.observe(value)
            return value

    @contextmanager
    def stage(
        self,
        component: EvidenceComponent,
        *,
        timeframe: Timeframe | None = None,
        success: bool = True,
    ) -> Iterator[ComponentProbe]:
        provider = getattr(self.source, "name", "unknown")
        probe = ComponentProbe()
        try:
            yield probe
        except Exception as exc:
            if diagnostic_from_exception(exc) is not None:
                raise
            switched = isinstance(exc, errors.EvidenceSourceSwitchRequiredError)
            self.failover_attempted = self.failover_attempted or switched
            item = self.record(
                component,
                timeframe=timeframe,
                status=DiagnosticStatus.SWITCH_REQUIRED
                if switched
                else DiagnosticStatus.UNAVAILABLE,
                reason=reason_for_exception(exc),
                freshness=FreshnessState.STALE
                if reason_for_exception(exc) is DiagnosticReason.STALE
                else (FreshnessState.UNKNOWN),
                provider=provider,
                exception_class=exception_class_name(exc),
            )
            # Preserve existing exception types and all fail-closed validators.
            exc.canonical_component_diagnostic = item
            raise
        if success:
            self.record(
                component,
                timeframe=timeframe,
                provider=provider,
                status=probe.status,
                reason=probe.reason,
                freshness=probe.freshness,
                source_time=probe.source_time,
                historical=probe.historical,
            )


class ComponentProbe:
    """Stores only approved scalar metadata; never holds the observed object."""

    def __init__(self) -> None:
        self.status = DiagnosticStatus.AVAILABLE
        self.reason = DiagnosticReason.OK
        self.freshness = FreshnessState.UNKNOWN
        self.source_time: datetime | None = None
        self.historical = False

    def unavailable(self, exc: Exception) -> None:
        self.status = DiagnosticStatus.UNAVAILABLE
        self.reason = reason_for_exception(exc)
        if isinstance(exc, errors.StaleEvidenceError):
            self.freshness = FreshnessState.STALE

    def observe(self, value: object, *, historical: bool = False) -> None:
        self.historical = historical
        if isinstance(value, ClosedOhlcvSeries):
            self.source_time = value.bars[-1].interval_end
            self.freshness = FreshnessState.FRESH
            self.historical = True
        else:
            for field in ("source_time", "event_time_max", "event_time"):
                stamp = getattr(value, field, None)
                if isinstance(stamp, datetime):
                    self.source_time = stamp
                    break
            fresh = (
                value
                if isinstance(value, FreshnessEvaluation)
                else getattr(value, "freshness", None)
            )
            if isinstance(fresh, FreshnessEvaluation):
                self.freshness = fresh.state
                self.source_time = fresh.source_time

    def trade_freshness(self, evaluated_at: datetime) -> None:
        if self.source_time is not None:
            self.freshness = evaluate_freshness(
                source_time=self.source_time,
                evaluated_at=evaluated_at,
                policy=first_slice_freshness_policy(),
                require_fresh=False,
            ).state
