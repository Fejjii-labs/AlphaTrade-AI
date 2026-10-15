"""Public market observation envelope for Phase 5 evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field

from app.market_contracts.coverage import TradeWindowCoverageProof
from app.market_contracts.cvd import CvdWindow
from app.market_contracts.derivatives import DerivativeObservation
from app.market_contracts.enums import Finality, FreshnessState, ObservationType, PrivacyClass
from app.market_contracts.flow import SignedQuoteFlow
from app.market_contracts.hashing import with_content_hash
from app.market_contracts.identity import EvidenceMarketIdentity
from app.market_contracts.models import CanonicalModel
from app.market_contracts.ohlcv import OhlcvBar, observation_id_for
from app.market_contracts.order_flow import OrderFlowObservation
from app.market_contracts.trades import TradeEvent
from app.schemas.nested_continuation import EvidenceAvailability


def observation_from_derivative(item: DerivativeObservation) -> PublicMarketObservation:
    """Bind only usable provider facts to the existing public evidence envelope."""
    if (
        item.availability is not EvidenceAvailability.AVAILABLE
        or item.event_time is None
        or item.freshness is None
        or item.collected_at is None
    ):
        raise ValueError("Unavailable OI/funding cannot satisfy public evidence roles.")
    source_event_id = f"{item.metric.value}:{item.content_hash}"
    return with_content_hash(
        PublicMarketObservation(
            observation_id=observation_id_for(source_event_id, finality=Finality.FINAL, revision=1),
            identity=item.identity,
            observation_type=ObservationType(item.metric.value),
            source_event_id=source_event_id,
            event_time=item.event_time,
            source_time=item.event_time,
            observed_at=item.observed_at,
            receive_time=item.collected_at,
            finality=Finality.FINAL,
            revision=1,
            freshness_state=item.freshness.state,
            payload_content_hash=item.content_hash,
            content_hash="0" * 64,
            recorded_at=item.observed_at,
        )
    )


class PublicMarketObservation(CanonicalModel):
    """Global public venue fact. No tenant owner."""

    observation_id: UUID
    identity: EvidenceMarketIdentity
    observation_type: ObservationType
    source_event_id: str = Field(min_length=1, max_length=120)
    interval_start: AwareDatetime | None = None
    interval_end: AwareDatetime | None = None
    event_time: AwareDatetime
    source_time: AwareDatetime
    observed_at: AwareDatetime
    receive_time: AwareDatetime
    finality: Finality
    revision: int = Field(ge=1)
    freshness_state: FreshnessState
    privacy_class: PrivacyClass = PrivacyClass.PUBLIC_MARKET_DATA
    payload_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_at: AwareDatetime


def observation_from_ohlcv(
    bar: OhlcvBar,
    *,
    identity: EvidenceMarketIdentity,
    observed_at: datetime,
    receive_time: datetime,
    freshness_state: FreshnessState,
) -> PublicMarketObservation:
    recorded = datetime.now(UTC)
    envelope = PublicMarketObservation(
        observation_id=observation_id_for(
            bar.source_event_id,
            finality=bar.finality,
            revision=bar.revision,
        ),
        identity=identity,
        observation_type=ObservationType.OHLCV,
        source_event_id=bar.source_event_id,
        interval_start=bar.interval_start,
        interval_end=bar.interval_end,
        event_time=bar.interval_start,
        source_time=bar.source_time,
        observed_at=observed_at.astimezone(UTC),
        receive_time=receive_time.astimezone(UTC),
        finality=bar.finality,
        revision=bar.revision,
        freshness_state=freshness_state,
        payload_content_hash=bar.content_hash,
        content_hash="0" * 64,
        recorded_at=recorded,
    )
    return with_content_hash(envelope)


def observation_from_trade(
    trade: TradeEvent,
    *,
    identity: EvidenceMarketIdentity,
    observed_at: datetime,
    freshness_state: FreshnessState,
) -> PublicMarketObservation:
    envelope = PublicMarketObservation(
        observation_id=trade.trade_event_id,
        identity=identity,
        observation_type=ObservationType.TRADE,
        source_event_id=trade.venue_trade_id,
        interval_start=None,
        interval_end=None,
        event_time=trade.event_timestamp,
        source_time=trade.event_timestamp,
        observed_at=observed_at.astimezone(UTC),
        receive_time=trade.receive_timestamp,
        finality=Finality.FINAL,
        revision=1,
        freshness_state=freshness_state,
        payload_content_hash=trade.content_hash,
        content_hash="0" * 64,
        recorded_at=observed_at.astimezone(UTC),
    )
    return with_content_hash(envelope)


_FLOW_OBSERVATION_NAMESPACE = UUID("c0de0000-f100-4000-8000-000000000064")


def observation_from_cvd(
    window: CvdWindow,
    *,
    observed_at: datetime,
    receive_time: datetime,
    freshness_state: FreshnessState,
) -> PublicMarketObservation:
    """Bind a hashed CvdWindow through the public observation envelope."""

    source_event_id = f"cvd:{window.content_hash}"
    envelope = PublicMarketObservation(
        observation_id=observation_id_for(source_event_id, finality=Finality.FINAL, revision=1),
        identity=window.identity,
        observation_type=ObservationType.CVD,
        source_event_id=source_event_id,
        interval_start=window.window_start,
        interval_end=window.window_end,
        event_time=window.window_start,
        source_time=window.event_time_max or window.window_end,
        observed_at=observed_at.astimezone(UTC),
        receive_time=receive_time.astimezone(UTC),
        finality=Finality.FINAL,
        revision=1,
        freshness_state=freshness_state,
        payload_content_hash=window.content_hash,
        content_hash="0" * 64,
        recorded_at=observed_at.astimezone(UTC),
    )
    return with_content_hash(envelope)


def observation_from_signed_flow(
    flow: SignedQuoteFlow,
    *,
    observed_at: datetime,
    receive_time: datetime,
    freshness_state: FreshnessState,
) -> PublicMarketObservation:
    """Bind hashed signed quote flow through the public observation envelope."""

    source_event_id = f"signed-flow:{flow.content_hash}"
    envelope = PublicMarketObservation(
        observation_id=uuid5(_FLOW_OBSERVATION_NAMESPACE, source_event_id),
        identity=flow.identity,
        observation_type=ObservationType.VOLUME,
        source_event_id=source_event_id,
        interval_start=flow.interval_start,
        interval_end=flow.interval_end,
        event_time=flow.interval_start,
        source_time=flow.event_time_max,
        observed_at=observed_at.astimezone(UTC),
        receive_time=receive_time.astimezone(UTC),
        finality=Finality.FINAL,
        revision=1,
        freshness_state=freshness_state,
        payload_content_hash=flow.content_hash,
        content_hash="0" * 64,
        recorded_at=observed_at.astimezone(UTC),
    )
    return with_content_hash(envelope)


def observation_from_coverage(
    proof: TradeWindowCoverageProof,
    *,
    observed_at: datetime,
    receive_time: datetime,
    freshness_state: FreshnessState,
) -> PublicMarketObservation:
    """Bind required trade-window coverage through the public observation envelope."""

    source_event_id = f"coverage:{proof.content_hash}"
    envelope = PublicMarketObservation(
        observation_id=observation_id_for(source_event_id, finality=Finality.FINAL, revision=1),
        identity=proof.identity,
        observation_type=ObservationType.TRADE,
        source_event_id=source_event_id,
        interval_start=proof.requested_start,
        interval_end=proof.requested_end,
        event_time=proof.requested_start,
        source_time=proof.actual_covered_end or proof.requested_end,
        observed_at=observed_at.astimezone(UTC),
        receive_time=receive_time.astimezone(UTC),
        finality=Finality.FINAL,
        revision=1,
        freshness_state=freshness_state,
        payload_content_hash=proof.content_hash,
        content_hash="0" * 64,
        recorded_at=observed_at.astimezone(UTC),
    )
    return with_content_hash(envelope)


def observation_from_order_flow(
    item: OrderFlowObservation,
    *,
    cvd: bool = False,
) -> PublicMarketObservation:
    if (
        item.availability is not EvidenceAvailability.AVAILABLE
        or item.event_time is None
        or item.freshness is None
    ):
        raise ValueError("Unavailable order flow cannot satisfy public evidence roles.")
    source_event_id = f"5m-{'cvd' if cvd else 'flow'}:{item.content_hash}"
    return with_content_hash(
        PublicMarketObservation(
            observation_id=observation_id_for(source_event_id, finality=Finality.FINAL, revision=1),
            identity=item.identity,
            observation_type=ObservationType.CVD if cvd else ObservationType.VOLUME,
            source_event_id=source_event_id,
            interval_start=item.window_start,
            interval_end=item.window_end,
            event_time=item.event_time,
            source_time=item.event_time,
            observed_at=item.observed_at,
            receive_time=item.observed_at,
            finality=Finality.FINAL,
            revision=1,
            freshness_state=item.freshness.state,
            payload_content_hash=item.content_hash,
            content_hash="0" * 64,
            recorded_at=item.observed_at,
        )
    )
