"""Tenant-scoped canonical evidence reads. No Candidate mint, no Watcher start."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ValidationAppError
from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.evidence_pipeline.canonical import first_slice_read_policy
from app.evidence_pipeline.current_price import quote_current_price
from app.evidence_pipeline.http_schemas import (
    CanonicalCompletenessRead,
    CanonicalCurrentPriceRead,
    CanonicalEvidenceRead,
    CanonicalFreshnessRead,
    CanonicalSetupEvidenceRead,
    CanonicalSourceIdentityRead,
)
from app.evidence_pipeline.market_intelligence import (
    read_market_intelligence,
    read_order_book,
    read_order_flow,
)
from app.evidence_pipeline.setup_lifetime import SetupLifetimePort
from app.evidence_pipeline.types import (
    AssembledCanonicalEvidence,
    CurrentPricePresentation,
    CurrentPriceQuote,
)
from app.market_contracts.adapters.factory import (
    perpetual_source_is_replay,
    resolve_perpetual_evidence_source,
)
from app.market_contracts.adapters.protocol import PerpetualMarketSource
from app.market_contracts.catalog import (
    PerpetualInstrumentCatalog,
    default_perpetual_catalog,
    instrument_for_source,
)
from app.market_contracts.errors import (
    EvidenceSourceSwitchRequiredError,
    FormingCandleError,
    IncompleteWarmUpError,
    MarketContractError,
    RegionalProviderFailureError,
    SpotFallbackRejectedError,
    StaleEvidenceError,
    WrongInstrumentError,
    WrongSourceError,
)
from app.market_contracts.first_slice import canonical_first_slice_clock, first_slice_identity
from app.market_contracts.freshness import first_slice_freshness_policy
from app.market_contracts.identity import ADAPTER_VERSION
from app.persistence.setup_lifetime import SqlAlchemySetupLifetimeStore
from app.schemas.common import Timeframe

Clock = Callable[[], datetime]


def _clock_now() -> datetime:
    return datetime.now(UTC)


class CanonicalEvidenceService:
    """Assembles or quotes canonical USD-M evidence for authenticated tenants."""

    def __init__(
        self,
        settings: Settings,
        *,
        source: PerpetualMarketSource | None = None,
        catalog: PerpetualInstrumentCatalog | None = None,
        clock: Clock | None = None,
        transport: httpx.BaseTransport | None = None,
        session: Session | None = None,
        lifetime: SetupLifetimePort | None = None,
    ) -> None:
        self._settings = settings
        self._catalog = catalog if catalog is not None else default_perpetual_catalog()
        self._replay = perpetual_source_is_replay(settings)
        self._source = source or resolve_perpetual_evidence_source(
            settings, transport=transport, catalog=self._catalog
        )
        self._clock = clock or _clock_now
        store: SetupLifetimePort
        if lifetime is not None:
            store = lifetime
        elif session is not None:
            store = SqlAlchemySetupLifetimeStore(session)
        else:
            from app.evidence_pipeline.setup_lifetime import SetupLifetimeStore

            store = SetupLifetimeStore()
        self._assembler = FirstSliceEvidenceAssembler(
            self._source,
            replay=self._replay,
            catalog=self._catalog,
            lifetime=store,
            clock=self._clock,
        )

    def read(
        self,
        *,
        organization_id: UUID,
        symbol: str = "BTCUSDT",
    ) -> CanonicalEvidenceRead:
        # A source can switch during any read. Discard the entire old-venue
        # projection before retrying so quote, setup and OI/funding stay aligned.
        for attempt in range(2):
            try:
                initial_instrument = instrument_for_source(self._source, self._catalog, symbol)
            except WrongInstrumentError as exc:
                raise ValidationAppError(str(exc), code="unknown_perpetual_instrument") from exc
            try:
                result = self._read_once(organization_id=organization_id, symbol=symbol)
                active = instrument_for_source(self._source, self._catalog, symbol)
                if active.instrument_id == initial_instrument.instrument_id:
                    return result
            except EvidenceSourceSwitchRequiredError:
                if attempt == 1:
                    raise
        raise WrongSourceError("Canonical read source switch did not settle.")

    def _read_once(
        self,
        *,
        organization_id: UUID,
        symbol: str,
    ) -> CanonicalEvidenceRead:
        try:
            instrument = instrument_for_source(self._source, self._catalog, symbol)
        except WrongInstrumentError as exc:
            raise ValidationAppError(str(exc), code="unknown_perpetual_instrument") from exc

        evaluated_at = canonical_first_slice_clock().evaluated_at if self._replay else self._clock()
        identity = first_slice_identity(
            timeframe=Timeframe.M15,
            replay=self._replay,
            is_live=not self._replay,
            instrument=instrument,
        )
        source_read = CanonicalSourceIdentityRead(
            venue=identity.venue.value,
            market_type=identity.market_type.value,
            instrument_id=instrument.instrument_id,
            provider_symbol=instrument.provider_symbol,
            provider_name=identity.provenance.provider_name,
            source_family=identity.source.family.value,
            adapter_version=identity.source.adapter_version or ADAPTER_VERSION,
            is_live=identity.provenance.is_live,
            is_mock=identity.provenance.is_mock,
            fallback_used=False,
        )
        price_read, price_reason = self._quote(
            organization_id=organization_id,
            instrument_symbol=instrument.provider_symbol,
            evaluated_at=evaluated_at,
        )
        setup_read, setup_reason = self._setup(
            organization_id=organization_id,
            symbol=instrument.provider_symbol,
            evaluated_at=evaluated_at,
        )
        intelligence = read_market_intelligence(
            self._source,
            identity=identity,
            instrument=instrument,
            observed_at=evaluated_at,
            allow_later_observation=True,
        )
        order_flow = read_order_flow(
            self._source,
            identity=identity,
            instrument=instrument,
            observed_at=evaluated_at,
        )
        order_book = read_order_book(
            self._source,
            identity=identity,
            instrument=instrument,
            observed_at=evaluated_at,
            allow_later_observation=True,
        )
        context_evaluated_at = evaluated_at if self._replay else self._clock()
        unavailable = None
        if not price_read.usable_as_current_market_price:
            unavailable = price_reason or price_read.presentation
        if not setup_read.available and unavailable is None:
            unavailable = setup_reason
        return CanonicalEvidenceRead(
            organization_id=str(organization_id),
            symbol=instrument.provider_symbol,
            source=source_read,
            current_price=price_read,
            setup_evidence=setup_read,
            market_intelligence=intelligence,
            order_flow=order_flow,
            order_book=order_book,
            timestamps={
                "evaluated_at": evaluated_at,
                "market_context_evaluated_at": context_evaluated_at,
                "current_price_source_time": price_read.source_time,
                "trigger_interval_start": setup_read.trigger_interval_start,
                "trigger_interval_end": setup_read.trigger_interval_end,
            },
            unavailable_reason=unavailable,
        )

    def _quote(
        self,
        *,
        organization_id: UUID,
        instrument_symbol: str,
        evaluated_at: datetime,
    ) -> tuple[CanonicalCurrentPriceRead, str | None]:
        del organization_id
        for _attempt in range(2):
            instrument = instrument_for_source(self._source, self._catalog, instrument_symbol)
            identity = first_slice_identity(
                timeframe=Timeframe.M15,
                replay=self._replay,
                is_live=not self._replay,
                instrument=instrument,
            )
            try:
                quote = quote_current_price(
                    self._source,
                    identity=identity,
                    instrument=instrument,
                    evaluated_at=evaluated_at,
                    connection_id=UUID("a0640000-2222-4000-8000-000000000001"),
                    replay=self._replay,
                )
                return _price_from_quote(quote), None
            except EvidenceSourceSwitchRequiredError:
                continue
            except MarketContractError as exc:
                presentation, reason = _presentation_for_error(exc, replay=self._replay)
                return (
                    _unavailable_price(
                        identity_is_live=identity.provenance.is_live,
                        presentation=presentation,
                    ),
                    reason,
                )
        return (
            _unavailable_price(
                identity_is_live=False,
                presentation=CurrentPricePresentation.UNAVAILABLE,
            ),
            "source_switch_failed",
        )

    def _setup(
        self,
        *,
        organization_id: UUID,
        symbol: str,
        evaluated_at: datetime,
    ) -> tuple[CanonicalSetupEvidenceRead, str | None]:
        try:
            assembled = self._assembler.assemble(
                organization_id=organization_id,
                symbol=symbol,
                evaluated_at=evaluated_at,
                policy=first_slice_read_policy(organization_id),
            )
            return _setup_from_assembly(assembled), None
        except MarketContractError as exc:
            presentation, reason = _presentation_for_error(exc, replay=self._replay)
            empty = CanonicalCompletenessRead(
                ohlcv_15m="unknown",
                ohlcv_4h="unknown",
                cvd="unknown",
                signed_flow="unknown",
            )
            return (
                CanonicalSetupEvidenceRead(
                    available=False,
                    completeness=empty,
                    reason=reason or presentation.value,
                ),
                reason or presentation.value,
            )


def _price_from_quote(quote: CurrentPriceQuote) -> CanonicalCurrentPriceRead:
    return CanonicalCurrentPriceRead(
        usable_as_current_market_price=quote.usable_as_current_market_price,
        presentation=quote.presentation.value,
        price=str(quote.price),
        source_time=quote.source_time,
        venue_trade_id=quote.venue_trade_id,
        is_live=quote.is_live,
        is_mock=quote.is_mock,
        fallback_used=False,
        freshness=CanonicalFreshnessRead(
            policy_version=quote.freshness.policy_version,
            state=quote.freshness.state.value,
            evaluated_at=quote.freshness.evaluated_at,
            source_time=quote.freshness.source_time,
            age_seconds=str(quote.freshness.age_seconds),
            valid_until=quote.freshness.valid_until,
        ),
    )


def _unavailable_price(
    *, identity_is_live: bool, presentation: CurrentPricePresentation
) -> CanonicalCurrentPriceRead:
    policy = first_slice_freshness_policy()
    now = datetime.now(UTC)
    return CanonicalCurrentPriceRead(
        usable_as_current_market_price=False,
        presentation=presentation.value,
        price=None,
        source_time=None,
        venue_trade_id=None,
        is_live=identity_is_live,
        is_mock=not identity_is_live,
        fallback_used=False,
        freshness=CanonicalFreshnessRead(
            policy_version=policy.policy_version,
            state="unknown",
            evaluated_at=now,
        ),
    )


def _setup_from_assembly(assembled: AssembledCanonicalEvidence) -> CanonicalSetupEvidenceRead:
    if assembled.cvd is None or assembled.signed_flow is None:
        raise MarketContractError("Canonical first-slice read requires CVD and signed flow.")
    completeness = assembled.completeness
    return CanonicalSetupEvidenceRead(
        available=True,
        evidence_window_hash=assembled.evidence_window_hash,
        trigger_interval_start=assembled.trigger_bar.interval_start,
        trigger_interval_end=assembled.trigger_bar.interval_end,
        evaluated_at=assembled.evaluated_at,
        cvd_signed_quote_delta=str(assembled.cvd.signed_quote_delta),
        signed_flow_ratio=str(assembled.signed_flow.signed_flow_ratio),
        completeness=CanonicalCompletenessRead(
            ohlcv_15m=completeness.ohlcv_15m.value,
            ohlcv_4h=completeness.ohlcv_4h.value,
            cvd=completeness.cvd.value,
            signed_flow=completeness.signed_flow.value,
            coverage_content_hash=completeness.coverage_content_hash,
            cvd_content_hash=completeness.cvd_content_hash,
            signed_flow_content_hash=completeness.signed_flow_content_hash,
        ),
    )


def _presentation_for_error(
    exc: MarketContractError, *, replay: bool
) -> tuple[CurrentPricePresentation, str]:
    if isinstance(exc, WrongInstrumentError):
        return CurrentPricePresentation.WRONG_INSTRUMENT, "wrong_instrument"
    if isinstance(exc, SpotFallbackRejectedError):
        return CurrentPricePresentation.SPOT_REJECTED, "spot_rejected"
    if isinstance(exc, WrongSourceError):
        return CurrentPricePresentation.WRONG_SOURCE, "wrong_source"
    if isinstance(exc, RegionalProviderFailureError):
        return CurrentPricePresentation.PROVIDER_UNAVAILABLE, "provider_unavailable"
    if isinstance(exc, StaleEvidenceError):
        return CurrentPricePresentation.STALE, "stale"
    if isinstance(exc, FormingCandleError | IncompleteWarmUpError):
        return CurrentPricePresentation.INCOMPLETE, "incomplete"
    if replay:
        return CurrentPricePresentation.REPLAY_FIXTURE, "replay_fixture"
    return CurrentPricePresentation.UNAVAILABLE, "unavailable"
