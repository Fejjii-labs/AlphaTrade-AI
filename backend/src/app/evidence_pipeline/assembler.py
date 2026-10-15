"""Assemble first-slice USD-M evidence into CanonicalEvidenceWindowV1."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from uuid import UUID, uuid5

from app.evidence_pipeline.acquisition import ScanAcquisition
from app.evidence_pipeline.canonical import (
    build_first_slice_assessment_command,
    first_slice_read_policy,
    timeframe_identity,
)
from app.evidence_pipeline.current_price import quote_current_price
from app.evidence_pipeline.market_intelligence import (
    DERIVATIVE_ROLES,
    ORDER_FLOW_ROLES,
    read_market_intelligence,
    read_order_flow,
)
from app.evidence_pipeline.setup_lifetime import (
    SetupLifetimePort,
    SetupLifetimeStore,
    SetupTriggerPin,
    lifetime_key_from_policy,
    setup_trigger_lifetime_elapsed,
    utc_trigger_end,
)
from app.evidence_pipeline.types import (
    AssembledCanonicalEvidence,
    CompletenessReport,
    CurrentPricePresentation,
    CurrentPriceQuote,
    EvidenceClockReport,
)
from app.market_contracts.adapters.protocol import PerpetualMarketSource
from app.market_contracts.catalog import (
    PerpetualInstrumentCatalog,
    default_perpetual_catalog,
    instrument_for_source,
)
from app.market_contracts.coverage import require_complete_window_coverage
from app.market_contracts.cursor import TradeStreamAssembler, TradeStreamSnapshot
from app.market_contracts.cvd import (
    FIRST_SLICE_CVD_LOOKBACK_BARS,
    first_slice_baseline_open,
    first_slice_cvd_window,
    require_cvd_stream_proof,
    snapshot_terminal_event_time,
    snapshot_terminal_price,
)
from app.market_contracts.derivatives import (
    DerivativeMetric,
    DerivativeObservation,
    refresh_derivative_observation,
    require_derivative_observations,
)
from app.market_contracts.enums import DataCompleteness, Finality, FreshnessState, MarketType
from app.market_contracts.errors import (
    EvidenceSourceSwitchRequiredError,
    FallbackForbiddenError,
    FormingCandleError,
    IncompleteWarmUpError,
    MarketContractError,
    SpotFallbackRejectedError,
    StaleEvidenceError,
    WrongSourceError,
)
from app.market_contracts.evidence_diagnostics import (
    DiagnosticReason,
    DiagnosticStatus,
    EvidenceComponentDiagnostic,
    EvidenceDiagnostics,
)
from app.market_contracts.evidence_diagnostics import (
    EvidenceComponent as Component,
)
from app.market_contracts.first_slice import (
    FIRST_SLICE_MIN_FINAL_4H,
    FIRST_SLICE_MIN_FINAL_15M,
    canonical_first_slice_clock,
    first_slice_identity,
    first_slice_spec,
)
from app.market_contracts.flow import bar_signed_quote_flow
from app.market_contracts.freshness import (
    evaluate_freshness,
    first_slice_freshness_policy,
    live_confirmation_window_open,
)
from app.market_contracts.identity import EvidenceMarketIdentity, InstrumentIdentity
from app.market_contracts.observation import (
    observation_from_derivative,
    observation_from_order_flow,
)
from app.market_contracts.ohlcv import ClosedOhlcvSeries, OhlcvBar, require_closed_series
from app.market_contracts.order_flow import OrderFlowObservation, require_order_flow
from app.schemas.common import Timeframe
from app.signal_fusion.adapters import evidence_window_from_assessment_command
from app.signal_fusion.enums import EvidenceAdapterKind, EvidenceRole
from app.signal_fusion.first_slice_types import (
    FIRST_SLICE_EXPIRY_BARS,
    FirstSliceEvidenceBundle,
    ManualResistanceEvidence,
)
from app.signal_fusion.observation import TenantExternalAssertion
from app.signal_fusion.policy import FusionPolicy
from app.signal_fusion.swings import most_recent_confirmed_swing_high
from app.signal_fusion.types import ManualLevelRevisionRef

_CONNECTION_NAMESPACE = UUID("a0640000-1111-4000-8000-000000000064")


class FirstSliceEvidenceAssembler:
    """Read-only assembler. BTCUSDT is the enabled first slice; catalog can grow."""

    def __init__(
        self,
        source: PerpetualMarketSource,
        *,
        replay: bool,
        catalog: PerpetualInstrumentCatalog | None = None,
        lifetime: SetupLifetimePort | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._diagnostics: tuple[EvidenceComponentDiagnostic, ...] = ()
        self._source = source
        self._replay = replay
        self._catalog = catalog if catalog is not None else default_perpetual_catalog()
        self._clock = clock
        self._lifetime: SetupLifetimePort = (
            lifetime if lifetime is not None else SetupLifetimeStore()
        )

    def assemble(
        self,
        *,
        organization_id: UUID,
        symbol: str = first_slice_spec().symbol,
        evaluated_at: datetime | None = None,
        policy: FusionPolicy | None = None,
        adapter_kind: EvidenceAdapterKind = EvidenceAdapterKind.DETECTOR,
        tenant_assertions: tuple[TenantExternalAssertion, ...] = (),
        manual_level_revision: ManualLevelRevisionRef | None = None,
        resistances: tuple[ManualResistanceEvidence, ...] = (),
        connection_id: UUID | None = None,
        setup_trigger_end: datetime | None = None,
    ) -> AssembledCanonicalEvidence:
        acquisition = ScanAcquisition(
            cutoff_at=evaluated_at or self._default_clock(),
            clock=self._default_clock
            if not self._replay and (evaluated_at is None or self._clock is not None)
            else None,
        )
        diagnostics = EvidenceDiagnostics(self._source, symbol, acquisition.cutoff_at)
        self._diagnostics = ()
        try:
            return self._assemble_diagnosed(
                diagnostics=diagnostics,
                operation=lambda: self._assemble_once(
                    diagnostics=diagnostics,
                    organization_id=organization_id,
                    symbol=symbol,
                    acquisition=acquisition,
                    policy=policy,
                    adapter_kind=adapter_kind,
                    tenant_assertions=tenant_assertions,
                    manual_level_revision=manual_level_revision,
                    resistances=resistances,
                    connection_id=connection_id,
                    setup_trigger_end=setup_trigger_end,
                ),
            )
        finally:
            self._diagnostics = diagnostics.items

    @property
    def diagnostics(self) -> tuple[EvidenceComponentDiagnostic, ...]:
        return self._diagnostics

    def _assemble_diagnosed(
        self,
        *,
        diagnostics: EvidenceDiagnostics,
        operation: Callable[[], AssembledCanonicalEvidence],
    ) -> AssembledCanonicalEvidence:
        for attempt in range(2):
            diagnostics.attempt = attempt + 1
            diagnostics.identity = None
            try:
                return diagnostics.run(
                    Component.CONTRACT,
                    operation,
                    success=False,
                )
            except EvidenceSourceSwitchRequiredError:
                diagnostics.failover_attempted = True
                diagnostics.record(
                    Component.FAILOVER,
                    status=DiagnosticStatus.SWITCH_REQUIRED,
                    reason=DiagnosticReason.SWITCH_REQUIRED,
                )
                if attempt == 1:
                    raise
        raise WrongSourceError("Perpetual evidence source switch did not settle.")

    def _assemble_once(
        self,
        *,
        diagnostics: EvidenceDiagnostics,
        organization_id: UUID,
        symbol: str,
        acquisition: ScanAcquisition,
        policy: FusionPolicy | None,
        adapter_kind: EvidenceAdapterKind,
        tenant_assertions: tuple[TenantExternalAssertion, ...],
        manual_level_revision: ManualLevelRevisionRef | None,
        resistances: tuple[ManualResistanceEvidence, ...],
        connection_id: UUID | None,
        setup_trigger_end: datetime | None,
    ) -> AssembledCanonicalEvidence:
        with diagnostics.stage(Component.INSTRUMENT) as probe:
            instrument = instrument_for_source(self._source, self._catalog, symbol)
            clock = acquisition.cutoff_at
            bound_policy = policy or first_slice_read_policy(organization_id)
            if EvidenceRole.ORDER_BOOK in bound_policy.required_roles:
                raise MarketContractError(
                    "required_order_book:historical_snapshot_coverage_unavailable"
                )
            if bound_policy.organization_id != organization_id:
                raise WrongSourceError(
                    "Fusion policy organization_id does not match the caller tenant."
                )
            lifetime_key = lifetime_key_from_policy(
                organization_id=organization_id,
                symbol=instrument.provider_symbol,
                timeframe=Timeframe.M15,
                strategy_version_id=bound_policy.strategy_version_id,
                compiled_setup_definition_id=bound_policy.executable_setup.setup_definition_id,
                compiled_content_hash=bound_policy.executable_setup.content_hash,
            )
            pinned_trigger_end = setup_trigger_end
            if pinned_trigger_end is None:
                pinned_trigger_end = self._lifetime.active_trigger_end(lifetime_key)
            trigger_identity = first_slice_identity(
                timeframe=Timeframe.M15,
                replay=self._replay,
                is_live=not self._replay,
                instrument=instrument,
            )
            context_identity = timeframe_identity(trigger_identity, Timeframe.H4)
            self._assert_live_contract(trigger_identity)
            diagnostics.identity = trigger_identity

        min_15m = FIRST_SLICE_MIN_FINAL_15M
        if pinned_trigger_end is not None:
            min_15m = FIRST_SLICE_MIN_FINAL_15M + FIRST_SLICE_EXPIRY_BARS + 2
        with diagnostics.stage(Component.OHLCV_15M, timeframe=Timeframe.M15) as probe:
            try:
                series_15m = self._source.fetch_closed_ohlcv(
                    identity=trigger_identity,
                    instrument=instrument,
                    timeframe=Timeframe.M15,
                    min_final_bars=min_15m,
                    evaluated_at=clock,
                )
            except FormingCandleError:
                if pinned_trigger_end is None:
                    raise
                series_15m = self._source.fetch_closed_ohlcv(
                    identity=trigger_identity,
                    instrument=instrument,
                    timeframe=Timeframe.M15,
                    min_final_bars=FIRST_SLICE_MIN_FINAL_15M,
                    evaluated_at=clock,
                )
            probe.observe(series_15m)
        with diagnostics.stage(Component.OHLCV_4H, timeframe=Timeframe.H4) as probe:
            series_4h = self._source.fetch_closed_ohlcv(
                identity=context_identity,
                instrument=instrument,
                timeframe=Timeframe.H4,
                min_final_bars=FIRST_SLICE_MIN_FINAL_4H,
                evaluated_at=clock,
            )
            probe.observe(series_4h)
        with diagnostics.stage(Component.TRIGGER) as probe:
            try:
                trigger, subsequent, series_15m = _trigger_and_subsequent(
                    series_15m,
                    identity=trigger_identity,
                    evaluated_at=clock,
                    setup_trigger_end=pinned_trigger_end,
                )
            except FormingCandleError:
                if (
                    pinned_trigger_end is None
                    or not series_15m.bars
                    or series_15m.bars[-1].interval_end > utc_trigger_end(clock)
                    or any(
                        bar.interval_end == utc_trigger_end(pinned_trigger_end)
                        for bar in series_15m.bars
                    )
                    or not setup_trigger_lifetime_elapsed(
                        pinned_trigger_end,
                        latest_closed_end=series_15m.bars[-1].interval_end,
                    )
                ):
                    raise
                # An explicit historical request must not retire a different,
                # valid current pin that happens to share the same semantic key.
                if self._lifetime.active_trigger_end(lifetime_key) == utc_trigger_end(
                    pinned_trigger_end
                ):
                    self._lifetime.expire(lifetime_key)
                diagnostics.record(
                    Component.TRIGGER,
                    timeframe=Timeframe.M15,
                    status=DiagnosticStatus.UNAVAILABLE,
                    reason=DiagnosticReason.STALE,
                    source_time=utc_trigger_end(pinned_trigger_end),
                    historical=True,
                )
                # Retry selection once over the already validated FINAL series.
                # Current forming candles and incomplete acquisition still fail closed.
                trigger, subsequent, series_15m = _trigger_and_subsequent(
                    series_15m,
                    identity=trigger_identity,
                    evaluated_at=clock,
                    setup_trigger_end=None,
                )
            context = _context_bar(series_4h, trigger)
        window_start = first_slice_baseline_open(trigger)
        window_end = trigger.interval_end
        lineage = connection_id or uuid5(
            _CONNECTION_NAMESPACE,
            f"{instrument.instrument_id}:{self._source.name}:"
            f"{window_start.isoformat()}:{window_end.isoformat()}",
        )
        with diagnostics.stage(Component.TRADES, timeframe=Timeframe.M15) as probe:
            snapshot = self._load_trade_snapshot(
                identity=trigger_identity,
                instrument=instrument,
                window_start=window_start,
                window_end=window_end,
                lineage=lineage,
                clock=clock,
            )
            probe.source_time = snapshot_terminal_event_time(snapshot)
            probe.historical = True
            probe.trade_freshness(clock)
        with diagnostics.stage(Component.COVERAGE, timeframe=Timeframe.M15):
            require_cvd_stream_proof(snapshot)
            require_complete_window_coverage(
                snapshot.coverage,
                identity=trigger_identity,
                lineage_id=snapshot.cursor.connection_identity,
                trades=snapshot.trades,
                required_start=window_start,
                required_end=window_end,
                released=snapshot.released_tape,
            )
        live_window = live_confirmation_window_open(
            closed_interval_end=trigger.interval_end,
            evaluated_at=clock,
        )
        with diagnostics.stage(Component.CVD, timeframe=Timeframe.M15) as probe:
            cvd = first_slice_cvd_window(
                identity=trigger_identity,
                series_15m=series_15m,
                snapshot=snapshot,
                created_at=clock,
                require_live_freshness=live_window,
            )
            probe.observe(cvd, historical=not live_window)
            probe.trade_freshness(clock)
        with diagnostics.stage(Component.SIGNED_FLOW, timeframe=Timeframe.M15) as probe:
            signed_flow = bar_signed_quote_flow(
                identity=trigger_identity,
                bar=trigger,
                snapshot=snapshot,
                evaluated_at=clock,
                require_live_freshness=live_window,
            )
            probe.observe(signed_flow, historical=not live_window)
            probe.trade_freshness(clock)
        with diagnostics.stage(Component.FRESHNESS) as probe:
            freshness = evaluate_freshness(
                source_time=cvd.event_time_max or snapshot_terminal_event_time(snapshot),
                evaluated_at=clock,
                policy=first_slice_freshness_policy(),
                require_fresh=live_window,
            )
            probe.observe(freshness, historical=not live_window)
        current: CurrentPriceQuote | None
        with diagnostics.stage(Component.PRICE) as probe:
            try:
                current = quote_current_price(
                    self._source,
                    identity=trigger_identity,
                    instrument=instrument,
                    evaluated_at=clock,
                    connection_id=lineage,
                    replay=self._replay,
                )
            except (StaleEvidenceError, IncompleteWarmUpError) as exc:
                probe.unavailable(exc)
                if live_window:
                    raise
                current = None
            if current is not None:
                probe.observe(current)
        with diagnostics.stage(Component.RESISTANCE, timeframe=Timeframe.H4) as probe:
            selected_revision = manual_level_revision or _nearest_resistance_ref(
                series_15m=series_15m,
                trigger=trigger,
                identity=trigger_identity,
                resistances=resistances,
            )
            if selected_revision is None:
                probe.status = DiagnosticStatus.UNAVAILABLE
                probe.reason = DiagnosticReason.MISSING
        command = build_first_slice_assessment_command(
            organization_id=organization_id,
            policy=bound_policy,
            trigger=trigger,
            context=context,
            trigger_identity=trigger_identity,
            context_identity=context_identity,
            evaluated_at=clock,
            freshness_state=freshness.state,
            adapter_kind=adapter_kind,
            cvd=cvd,
            signed_flow=signed_flow,
            coverage=snapshot.coverage,
            tenant_assertions=tenant_assertions,
            manual_level_revision=selected_revision,
        )
        required_metrics = tuple(
            DERIVATIVE_ROLES[role]
            for role in bound_policy.required_roles
            if role in DERIVATIVE_ROLES
        )

        def complete_acquisition() -> datetime:
            diagnostics.evaluated_at = acquisition.complete()
            return diagnostics.evaluated_at

        intelligence = tuple(
            diagnostics.run(
                Component(metric.value),
                partial(
                    _required_derivative,
                    self._source,
                    metric=metric,
                    identity=trigger_identity,
                    instrument=instrument,
                    observed_at=clock,
                    acquisition_clock=complete_acquisition
                    if acquisition.clock is not None
                    else None,
                ),
                timeframe=Timeframe.M5
                if trigger_identity.venue.value == "bybit" and metric.value == "open_interest"
                else None,
            )
            for metric in required_metrics
        )
        for component in (Component.OPEN_INTEREST, Component.FUNDING):
            if component.value not in {metric.value for metric in required_metrics}:
                diagnostics.record(
                    component,
                    status=DiagnosticStatus.NOT_REQUIRED,
                    reason=DiagnosticReason.NOT_REQUIRED,
                )
        if intelligence:
            command = command.model_copy(
                update={
                    "mandatory_evidence_roles": tuple(
                        dict.fromkeys(
                            (*command.mandatory_evidence_roles, *bound_policy.required_roles)
                        )
                    ),
                    "public_observations": command.public_observations
                    + tuple(observation_from_derivative(item) for item in intelligence),
                    "selected_roles": command.selected_roles
                    + tuple(
                        role for role in bound_policy.required_roles if role in DERIVATIVE_ROLES
                    ),
                }
            )
        order_flow = None
        flow_roles = tuple(role for role in bound_policy.required_roles if role in ORDER_FLOW_ROLES)
        if flow_roles:
            with diagnostics.stage(Component.ORDER_FLOW, timeframe=Timeframe.M5) as probe:
                order_flow = read_order_flow(
                    self._source,
                    identity=trigger_identity,
                    instrument=instrument,
                    observed_at=clock,
                    window_end=trigger.interval_end,
                )
                _require_order_flow_diagnosed(
                    order_flow, identity=trigger_identity, evaluated_at=clock
                )
                probe.observe(order_flow, historical=True)
            command = command.model_copy(
                update={
                    "mandatory_evidence_roles": tuple(
                        dict.fromkeys((*command.mandatory_evidence_roles, *flow_roles))
                    ),
                    "public_observations": command.public_observations
                    + tuple(
                        observation_from_order_flow(order_flow, cvd=role is EvidenceRole.CVD_5M)
                        for role in flow_roles
                    ),
                    "selected_roles": command.selected_roles + flow_roles,
                }
            )
        if not flow_roles:
            diagnostics.record(
                Component.ORDER_FLOW,
                timeframe=Timeframe.M5,
                status=DiagnosticStatus.NOT_REQUIRED,
                reason=DiagnosticReason.NOT_REQUIRED,
            )
        # All required facts share the final independent evaluation clock. Event
        # selection remains bounded by the original scan cutoff, including when
        # acquisition crosses a candle boundary. Do not move that cutoff forward.
        clock = complete_acquisition()
        refreshed = []
        for item in intelligence:
            with diagnostics.stage(Component(item.metric.value), success=False):
                require_derivative_observations(
                    (item,),
                    required_metrics=(item.metric,),
                    identity=trigger_identity,
                    evaluated_at=clock,
                    event_cutoff_at=acquisition.cutoff_at,
                )
                refreshed.append(refresh_derivative_observation(item, clock))
        intelligence = tuple(refreshed)
        if order_flow is not None:
            _require_order_flow_diagnosed(order_flow, identity=trigger_identity, evaluated_at=clock)
        # Recheck live freshness after the potentially delayed acquisition. A
        # valid historical closed window must not be presented as a fresh mark.
        with diagnostics.stage(Component.FRESHNESS, success=False):
            freshness = evaluate_freshness(
                source_time=cvd.event_time_max or snapshot_terminal_event_time(snapshot),
                evaluated_at=clock,
                policy=first_slice_freshness_policy(),
                require_fresh=live_window,
            )
        if current is not None and clock != acquisition.cutoff_at:
            with diagnostics.stage(Component.PRICE, success=False):
                quote_freshness = evaluate_freshness(
                    source_time=current.source_time,
                    evaluated_at=clock,
                    policy=first_slice_freshness_policy(),
                    require_fresh=live_window,
                )
                current = current.model_copy(
                    update={
                        "freshness": quote_freshness,
                        "usable_as_current_market_price": current.usable_as_current_market_price
                        and quote_freshness.state in {FreshnessState.FRESH, FreshnessState.AGING},
                        "presentation": CurrentPricePresentation.STALE
                        if quote_freshness.state is FreshnessState.STALE
                        else current.presentation,
                    }
                )
        # Replace the derivative envelopes with final-age metadata, preserving
        # original collected_at/receive_time and the immutable provider hashes.
        if intelligence:
            envelopes = {x.content_hash: observation_from_derivative(x) for x in intelligence}
            command = command.model_copy(
                update={
                    "public_observations": tuple(
                        envelopes.get(observation.payload_content_hash, observation)
                        for observation in command.public_observations
                    )
                }
            )
        window = diagnostics.run(
            Component.CONTRACT, lambda: evidence_window_from_assessment_command(command)
        )
        bundle = FirstSliceEvidenceBundle(
            bars_15m=tuple(series_15m.bars),
            bars_4h=tuple(series_4h.bars),
            snapshot=snapshot,
            subsequent_final_15m=subsequent,
            resistances=resistances,
            market_intelligence=intelligence,
            order_flow=order_flow,
        )
        completeness = CompletenessReport(
            ohlcv_15m=DataCompleteness.COMPLETE,
            ohlcv_4h=DataCompleteness.COMPLETE,
            cvd=cvd.data_completeness,
            signed_flow=DataCompleteness.COMPLETE,
            coverage_content_hash=snapshot.coverage.content_hash,
            cvd_content_hash=cvd.content_hash,
            signed_flow_content_hash=signed_flow.content_hash,
        )
        remaining = max(0, FIRST_SLICE_EXPIRY_BARS - len(subsequent))
        expired = len(subsequent) >= FIRST_SLICE_EXPIRY_BARS
        stream_fresh = freshness.state in {FreshnessState.FRESH, FreshnessState.AGING}
        if not live_window:
            stream_fresh = bool(snapshot.coverage.content_hash)
        clocks = EvidenceClockReport(
            evidence_cutoff_at=acquisition.cutoff_at,
            acquisition_completed_at=clock,
            quote_source_time=None if current is None else current.source_time,
            quote_fresh=current is not None and current.usable_as_current_market_price,
            trade_stream_event_time_max=cvd.event_time_max,
            market_stream_fresh=stream_fresh,
            live_confirmation_window_open=live_window,
            trigger_finality=trigger.finality,
            trigger_interval_end=trigger.interval_end,
            historical_closed_evidence=not live_window,
            closed_evidence_valid=trigger.finality is Finality.FINAL,
            subsequent_final_15m_count=len(subsequent),
            setup_expired=expired,
            setup_lifetime_remaining_bars=remaining,
            setup_trigger_bar_hash=trigger.content_hash,
        )
        stored_pin = SetupTriggerPin(
            trigger_end=utc_trigger_end(trigger.interval_end),
            trigger_bar_hash=trigger.content_hash,
            expired=expired,
        )
        self._lifetime.remember(lifetime_key, stored_pin)
        if expired:
            self._lifetime.expire(lifetime_key)
        return AssembledCanonicalEvidence(
            organization_id=organization_id,
            replay=self._replay,
            evaluated_at=clock,
            identity=trigger_identity,
            trigger_bar=trigger,
            context_bar=context,
            series_15m=series_15m,
            series_4h=series_4h,
            cvd=cvd,
            signed_flow=signed_flow,
            current_price=current,
            completeness=completeness,
            freshness_state=freshness.state,
            clocks=clocks,
            bundle=bundle,
            assessment_command=command,
            evidence_window=window,
            evidence_window_hash=window.content_hash,
            connection_id=lineage,
            evaluation_mark=snapshot_terminal_price(snapshot),
        )

    def _load_trade_snapshot(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        window_start: datetime,
        window_end: datetime,
        lineage: UUID,
        clock: datetime,
    ) -> TradeStreamSnapshot:
        """Load the scan window. Binance reduces it; replay keeps the trade list."""

        reduce = getattr(self._source, "reduce_ordered_trades", None)
        if callable(reduce):
            reduced = reduce(
                identity=identity,
                instrument=instrument,
                start=window_start,
                end=window_end,
                source_connection_id=lineage,
                receive_at=clock,
            )
            if not isinstance(reduced, TradeStreamSnapshot):
                raise WrongSourceError("Trade reduction did not return a snapshot.")
            return reduced
        batch = self._source.fetch_ordered_trades(
            identity=identity,
            instrument=instrument,
            start=window_start,
            end=window_end,
            source_connection_id=lineage,
            receive_at=clock,
        )
        assembler = TradeStreamAssembler(
            identity,
            connected_at=clock,
            connection_identity=lineage,
            expected_contiguous_count=len(batch.trades),
        )
        return assembler.ingest_batch(batch, observed_at=clock)

    def _default_clock(self) -> datetime:
        if self._clock is not None:
            return self._clock()
        if self._replay:
            return canonical_first_slice_clock().evaluated_at
        return datetime.now(UTC)

    def _assert_live_contract(self, identity: EvidenceMarketIdentity) -> None:
        if identity.market_type is not MarketType.PERPETUAL:
            raise SpotFallbackRejectedError("Spot market data cannot satisfy perpetual evidence.")
        if identity.provenance.fallback_used:
            raise FallbackForbiddenError("Assembled perpetual evidence cannot record fallback.")
        if self._replay and identity.provenance.is_live:
            raise WrongSourceError("Replay assembly cannot claim live provenance.")
        if not self._replay and (identity.provenance.is_mock or not identity.provenance.is_live):
            raise WrongSourceError("Live assembly cannot use mock or non-live provenance.")


def _nearest_resistance_ref(
    *,
    series_15m: ClosedOhlcvSeries,
    trigger: OhlcvBar,
    identity: EvidenceMarketIdentity,
    resistances: tuple[ManualResistanceEvidence, ...],
) -> ManualLevelRevisionRef | None:
    """Bind the command to the same nearest 4h resistance the evaluator will select."""

    if not resistances:
        return None
    window_start = first_slice_baseline_open(trigger, FIRST_SLICE_CVD_LOOKBACK_BARS)
    swing = most_recent_confirmed_swing_high(
        series_15m.bars,
        window_start=window_start,
        before_bar=trigger,
    )
    if swing is None:
        return None
    eligible = [
        item
        for item in resistances
        if item.valid
        and item.timeframe is Timeframe.H4
        and item.effective_at < trigger.interval_start
        and item.venue is identity.venue
        and item.market_type is identity.market_type
        and item.instrument_id == identity.instrument.instrument_id
    ]
    if not eligible:
        return None
    eligible.sort(
        key=lambda item: (
            abs(item.price - swing.price),
            str(item.ref.level_id),
            item.ref.revision_number,
        )
    )
    return eligible[0].ref


def _last_final_bar(series: ClosedOhlcvSeries) -> OhlcvBar:
    if not series.bars:
        raise FormingCandleError("Closed OHLCV series is empty.")
    return series.bars[-1]


def _trigger_and_subsequent(
    series: ClosedOhlcvSeries,
    *,
    identity: EvidenceMarketIdentity,
    evaluated_at: datetime,
    setup_trigger_end: datetime | None,
) -> tuple[OhlcvBar, tuple[OhlcvBar, ...], ClosedOhlcvSeries]:
    """Pin the setup trigger; later closed 15m bars are subsequent, not a new trigger."""

    if not series.bars:
        raise FormingCandleError("Closed OHLCV series is empty.")
    if setup_trigger_end is None:
        return series.bars[-1], (), series
    wanted = setup_trigger_end.astimezone(UTC) if setup_trigger_end.tzinfo else setup_trigger_end
    matches = [bar for bar in series.bars if bar.interval_end == wanted]
    if not matches:
        raise FormingCandleError("Pinned setup trigger bar is not in the closed 15m series.")
    trigger = matches[-1]
    index = series.bars.index(trigger)
    subsequent = tuple(
        bar
        for bar in series.bars[index + 1 :]
        if bar.finality is Finality.FINAL and bar.provider_complete
    )
    through_trigger = list(series.bars[: index + 1])
    min_required = FIRST_SLICE_MIN_FINAL_15M
    if len(through_trigger) < FIRST_SLICE_CVD_LOOKBACK_BARS + 1:
        raise IncompleteWarmUpError(
            "Pinned setup trigger does not retain the required closed 15m warmup."
        )
    if len(through_trigger) < min_required:
        min_required = len(through_trigger)
    pinned = require_closed_series(
        through_trigger,
        identity=identity,
        timeframe=Timeframe.M15,
        evaluated_at=evaluated_at,
        min_bars=min_required,
    )
    return trigger, subsequent, pinned


def _context_bar(series: ClosedOhlcvSeries, trigger: OhlcvBar) -> OhlcvBar:
    eligible = [bar for bar in series.bars if bar.interval_end <= trigger.interval_end]
    if not eligible:
        raise FormingCandleError("No final 4h context bar is closed at or before the 15m trigger.")
    return eligible[-1]


def _availability_reason(item: DerivativeObservation | OrderFlowObservation) -> DiagnosticReason:
    # Only finite contract values, never provider reason text.
    for error_type, reason in (
        ("RateLimitedError", DiagnosticReason.RATE_LIMITED),
        ("UpstreamBanError", DiagnosticReason.UPSTREAM_BAN),
        ("RegionalProviderFailureError", DiagnosticReason.REGIONAL_FAILURE),
        ("WrongInstrumentError", DiagnosticReason.WRONG_INSTRUMENT),
        ("WrongMarketError", DiagnosticReason.WRONG_MARKET),
        ("WrongSourceError", DiagnosticReason.WRONG_SOURCE),
        ("SpotFallbackRejectedError", DiagnosticReason.SPOT_REJECTED),
        ("FallbackForbiddenError", DiagnosticReason.FALLBACK_FORBIDDEN),
        ("StaleEvidenceError", DiagnosticReason.STALE),
        ("IncompleteTradeWindowError", DiagnosticReason.COVERAGE_INCOMPLETE),
        ("GapDetectedError", DiagnosticReason.GAP),
        ("DuplicateDataError", DiagnosticReason.DUPLICATE),
        ("OutOfOrderTradesError", DiagnosticReason.OUT_OF_ORDER),
        ("UnknownAggressorError", DiagnosticReason.UNKNOWN_AGGRESSOR),
        ("UnsupportedTradeContractError", DiagnosticReason.UNSUPPORTED),
    ):
        if item.reason in {f"provider_failure:{error_type}", f"trade_print_failure:{error_type}"}:
            return reason
    return {
        "MISSING": DiagnosticReason.MISSING,
        "UNSUPPORTED": DiagnosticReason.UNSUPPORTED,
        "INCOMPLETE": DiagnosticReason.INCOMPLETE,
        "STALE": DiagnosticReason.STALE,
    }.get(item.availability.value, DiagnosticReason.INVALID_CONTRACT)


def _required_derivative(
    source: PerpetualMarketSource,
    *,
    metric: DerivativeMetric,
    identity: EvidenceMarketIdentity,
    instrument: InstrumentIdentity,
    observed_at: datetime,
    acquisition_clock: Callable[[], datetime] | None = None,
) -> DerivativeObservation:
    observations = read_market_intelligence(
        source,
        identity=identity,
        instrument=instrument,
        observed_at=observed_at,
        metrics=(metric,),
        acquisition_clock=acquisition_clock,
    )
    try:
        require_derivative_observations(
            observations,
            required_metrics=(metric,),
            identity=identity,
            evaluated_at=acquisition_clock() if acquisition_clock is not None else observed_at,
            event_cutoff_at=observed_at,
        )
    except Exception as exc:
        if observations and observations[0].availability.value != "AVAILABLE":
            # Preserve the original exception type while attaching diagnostic metadata.
            setattr(exc, "canonical_reason", _availability_reason(observations[0]))  # noqa: B010
        raise
    return observations[0]


def _require_order_flow_diagnosed(
    item: OrderFlowObservation | None,
    *,
    identity: EvidenceMarketIdentity,
    evaluated_at: datetime,
) -> None:
    try:
        require_order_flow(item, identity=identity, evaluated_at=evaluated_at)
    except Exception as exc:
        if item is not None and item.availability.value != "AVAILABLE":
            setattr(exc, "canonical_reason", _availability_reason(item))  # noqa: B010
        raise
