"""Live read-only first-slice evidence pipeline: fail-closed USD-M assembly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest

from app.analysis.wilder_atr_v1 import FINALITY_POLICY_VERSION
from app.core.config import Settings
from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.evidence_pipeline.canonical import (
    FIRST_SLICE_READ_STRATEGY_VERSION_ID,
    first_slice_read_policy,
    is_first_slice_read_projection,
)
from app.evidence_pipeline.current_price import quote_current_price
from app.evidence_pipeline.types import CurrentPricePresentation
from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.adapters.replay import ReplayPerpetualSource
from app.market_contracts.catalog import default_perpetual_catalog
from app.market_contracts.cvd import first_slice_baseline_open
from app.market_contracts.enums import DataCompleteness, SourceFamily
from app.market_contracts.errors import (
    FormingCandleError,
    GapDetectedError,
    IncompleteTradeWindowError,
    IncompleteWarmUpError,
    RegionalProviderFailureError,
    SpotFallbackRejectedError,
    StaleEvidenceError,
    WrongInstrumentError,
    WrongMarketError,
    WrongSourceError,
)
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.freshness import (
    FIRST_SLICE_FRESHNESS_POLICY_VERSION,
    live_confirmation_window_open,
)
from app.market_contracts.hashing import with_content_hash
from app.market_contracts.identity import (
    binance_usdm_btcusdt,
    binance_usdm_perpetual,
    interval_timedelta,
)
from app.market_contracts.replay_fixtures import canonical_first_slice_fixture
from app.schemas.common import Timeframe
from app.schemas.strategy_pattern_spec import canonical_first_slice_authored_spec
from app.signal_fusion.adapters import evidence_window_from_assessment_command
from app.signal_fusion.enums import EvidenceRole, SetupIdentityKind
from app.signal_fusion.policy import (
    DEFAULT_FUSION_POLICY_VERSION,
    FusionThresholds,
    build_fusion_policy,
    first_slice_role_timeframes,
)
from app.signal_fusion.strategy_evaluation_policy import executable_policy_from_fusion_policy
from app.signal_fusion.types import ExecutableSetupRef, RuleWeight
from app.watcher.contracts import (
    EvaluationCommand,
    EvaluationMode,
    ScanRequest,
    ScanTrigger,
)
from app.watcher.errors import WatcherEvidenceUnavailableError
from app.watcher.hashing import evaluation_input_hash as hash_evaluation_input
from app.watcher.hashing import scan_request_hash
from tests.support.phase5_market import (
    EVALUATED_AT,
    RECORDED_FIXTURE_GRACE,
    TRIGGER_OPEN,
    eth_instrument,
    identity,
    spot_identity,
    trade,
)
from tests.support.phase6_fusion import ORG_ID

OTHER_ORG = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _assembler(*, replay: bool = True, source: object | None = None) -> FirstSliceEvidenceAssembler:
    resolved = source if source is not None else ReplayPerpetualSource()
    return FirstSliceEvidenceAssembler(resolved, replay=replay)  # type: ignore[arg-type]


def test_replay_fresh_assembly_is_complete_and_not_a_live_mark() -> None:
    left = _assembler().assemble(organization_id=ORG_ID)
    assert left.completeness.cvd is DataCompleteness.COMPLETE
    assert left.completeness.ohlcv_15m is DataCompleteness.COMPLETE
    assert left.completeness.signed_flow is DataCompleteness.COMPLETE
    assert left.current_price.usable_as_current_market_price is False
    assert left.current_price.presentation is CurrentPricePresentation.REPLAY_FIXTURE
    assert left.current_price.is_mock is True
    assert left.current_price.is_live is False
    assert left.current_price.fallback_used is False
    assert str(left.current_price.price) != "47326"
    assert str(left.current_price.price) != "65000"
    assert left.identity.source.family is SourceFamily.REPLAY_FIXTURE
    assert left.evidence_window.content_hash == left.evidence_window_hash
    rebuilt = evidence_window_from_assessment_command(left.assessment_command)
    assert rebuilt.content_hash == left.evidence_window_hash


def test_source_identity_is_usd_m_replay_not_spot() -> None:
    assembled = _assembler().assemble(organization_id=ORG_ID)
    identity = assembled.identity
    assert identity.venue.value == "binance"
    assert identity.market_type.value == "perpetual"
    assert identity.instrument.provider_symbol == "BTCUSDT"
    assert identity.instrument.product_family.value == "usdm_futures"
    assert identity.source.family is SourceFamily.REPLAY_FIXTURE
    assert identity.provenance.fallback_used is False
    assert identity.provenance.is_live is False
    assert identity.provenance.is_mock is True
    assert assembled.current_price.source_family is SourceFamily.REPLAY_FIXTURE
    assert assembled.completeness.cvd is DataCompleteness.COMPLETE
    assert assembled.completeness.cvd_content_hash is not None
    assert assembled.completeness.coverage_content_hash is not None
    assert assembled.completeness.signed_flow_content_hash is not None


def test_duplicate_and_restart_converge_on_the_same_window_hash() -> None:
    first = FirstSliceEvidenceAssembler(ReplayPerpetualSource(), replay=True)
    second = FirstSliceEvidenceAssembler(ReplayPerpetualSource(), replay=True)
    left = first.assemble(organization_id=ORG_ID)
    right = second.assemble(organization_id=ORG_ID)
    again = first.assemble(organization_id=ORG_ID)
    assert left.evidence_window_hash == right.evidence_window_hash == again.evidence_window_hash
    assert left.completeness.coverage_content_hash == right.completeness.coverage_content_hash


def test_tenant_boundaries_fork_canonical_identity() -> None:
    assembler = _assembler()
    left = assembler.assemble(organization_id=ORG_ID)
    right = assembler.assemble(organization_id=OTHER_ORG)
    assert left.evidence_window_hash != right.evidence_window_hash
    assert left.assessment_command.organization_id == ORG_ID
    assert right.assessment_command.organization_id == OTHER_ORG
    trigger_hashes = [
        item.content_hash
        for item, role in zip(
            left.assessment_command.public_observations,
            left.assessment_command.selected_roles,
            strict=True,
        )
        if role.value == "trigger_ohlcv"
    ]
    other_hashes = [
        item.content_hash
        for item, role in zip(
            right.assessment_command.public_observations,
            right.assessment_command.selected_roles,
            strict=True,
        )
        if role.value == "trigger_ohlcv"
    ]
    assert trigger_hashes == other_hashes


def _role_hashes(assembled: object) -> dict[str, str]:
    command = assembled.assessment_command  # type: ignore[attr-defined]
    return {
        role.value: observation.content_hash
        for observation, role in zip(
            command.public_observations,
            command.selected_roles,
            strict=True,
        )
    }


def _corrected_trade_source() -> ReplayPerpetualSource:
    fixture = canonical_first_slice_fixture()
    trades = list(fixture["trades"])
    last = trades[-1]
    new_qty = last.quantity + Decimal("0.01")
    updated = last.model_copy(
        update={
            "quantity": new_qty,
            "quote_quantity": last.price * new_qty * last.instrument.contract_multiplier,
        }
    )
    trades[-1] = with_content_hash(updated, extra_exclude=frozenset({"source_connection_id"}))
    return ReplayPerpetualSource(
        bars_15m=list(fixture["bars_15m"]),
        bars_4h=list(fixture["bars_4h"]),
        trades=trades,
    )


def test_trade_correction_changes_cvd_identity_with_unchanged_candles() -> None:
    baseline = _assembler().assemble(organization_id=ORG_ID)
    corrected_source = _corrected_trade_source()
    corrected = _assembler(source=corrected_source).assemble(organization_id=ORG_ID)
    again = _assembler(source=_corrected_trade_source()).assemble(organization_id=ORG_ID)
    assert baseline.trigger_bar.content_hash == corrected.trigger_bar.content_hash
    assert baseline.context_bar.content_hash == corrected.context_bar.content_hash
    assert baseline.cvd.content_hash != corrected.cvd.content_hash
    assert baseline.signed_flow.content_hash != corrected.signed_flow.content_hash
    assert baseline.evidence_window_hash != corrected.evidence_window_hash
    baseline_roles = _role_hashes(baseline)
    corrected_roles = _role_hashes(corrected)
    assert baseline_roles["trigger_ohlcv"] == corrected_roles["trigger_ohlcv"]
    assert baseline_roles["cvd_window"] != corrected_roles["cvd_window"]
    assert corrected.evidence_window_hash == again.evidence_window_hash
    assert corrected.cvd.content_hash == again.cvd.content_hash


def test_live_confirmation_window_is_ten_seconds_after_close() -> None:
    close = datetime(2026, 1, 15, 16, 15, tzinfo=UTC)
    assert live_confirmation_window_open(closed_interval_end=close, evaluated_at=close) is True
    assert (
        live_confirmation_window_open(
            closed_interval_end=close,
            evaluated_at=close + timedelta(seconds=10),
        )
        is True
    )
    assert (
        live_confirmation_window_open(
            closed_interval_end=close,
            evaluated_at=close + timedelta(seconds=11),
        )
        is False
    )


def test_live_window_still_enforces_ten_second_last_trade_age() -> None:
    with pytest.raises(StaleEvidenceError):
        _assembler().assemble(
            organization_id=ORG_ID,
            evaluated_at=EVALUATED_AT + timedelta(seconds=4),
        )


def test_closed_historical_evidence_later_in_the_next_interval() -> None:
    later = _assembler().assemble(
        organization_id=ORG_ID,
        evaluated_at=EVALUATED_AT + timedelta(minutes=5),
    )
    just_after_window = _assembler().assemble(
        organization_id=ORG_ID,
        evaluated_at=EVALUATED_AT + timedelta(seconds=6),
    )
    assert later.current_price is None
    assert later.completeness.cvd is DataCompleteness.COMPLETE
    assert just_after_window.completeness.cvd is DataCompleteness.COMPLETE
    assert later.identity.provenance.is_live is False
    assert later.identity.source.family is SourceFamily.REPLAY_FIXTURE


def test_partial_ohlcv_fails_closed() -> None:
    fixture = canonical_first_slice_fixture()
    source = ReplayPerpetualSource(bars_15m=list(fixture["bars_15m"])[:40])
    with pytest.raises(FormingCandleError):
        _assembler(source=source).assemble(organization_id=ORG_ID)


def test_partial_cvd_trades_fail_closed() -> None:
    fixture = canonical_first_slice_fixture()
    trades = list(fixture["trades"])
    hole = trades[:20] + trades[24:]
    source = ReplayPerpetualSource(trades=hole)
    with pytest.raises((IncompleteTradeWindowError, IncompleteWarmUpError, GapDetectedError)):
        _assembler(source=source).assemble(organization_id=ORG_ID)


def test_wrong_symbol_fails_closed() -> None:
    with pytest.raises(WrongInstrumentError, match="ETHUSDT"):
        _assembler().assemble(organization_id=ORG_ID, symbol="ETHUSDT")


def test_catalog_can_register_another_symbol_without_enabling_it_by_default() -> None:
    catalog = default_perpetual_catalog()
    assert catalog.require("BTCUSDT").provider_symbol == "BTCUSDT"
    with pytest.raises(WrongInstrumentError):
        catalog.require("ETHUSDT")
    extended = catalog.extend(binance_usdm_perpetual("ETHUSDT"))
    assert extended.require("ETHUSDT").provider_symbol == "ETHUSDT"
    with pytest.raises(WrongInstrumentError):
        catalog.require("ETHUSDT")


def test_replay_rejects_live_source_identity() -> None:
    source = ReplayPerpetualSource()
    live = first_slice_identity(timeframe=Timeframe.M15, replay=False, is_live=True)
    with pytest.raises(WrongSourceError):
        quote_current_price(
            source,
            identity=live,
            instrument=binance_usdm_btcusdt(),
            evaluated_at=EVALUATED_AT,
            connection_id=uuid4(),
            replay=False,
        )


def test_empty_freshness_window_does_not_fabricate_a_price() -> None:
    early = trade(
        sequence=1,
        price="100000",
        quantity="0.01",
        buyer_is_maker=True,
        event_time=EVALUATED_AT - timedelta(hours=1),
    )
    source = ReplayPerpetualSource(trades=[early])
    with pytest.raises(IncompleteWarmUpError, match="fabricate"):
        quote_current_price(
            source,
            identity=identity(),
            instrument=binance_usdm_btcusdt(),
            evaluated_at=EVALUATED_AT,
            connection_id=uuid4(),
            replay=True,
        )


def _kline_row(bar: object) -> list[object]:
    open_ms = int(bar.interval_start.timestamp() * 1000)  # type: ignore[attr-defined]
    close_ms = int(bar.interval_end.timestamp() * 1000) - 1  # type: ignore[attr-defined]
    return [
        open_ms,
        str(bar.open),  # type: ignore[attr-defined]
        str(bar.high),  # type: ignore[attr-defined]
        str(bar.low),  # type: ignore[attr-defined]
        str(bar.close),  # type: ignore[attr-defined]
        str(bar.base_volume),  # type: ignore[attr-defined]
        close_ms,
        str(bar.quote_volume),  # type: ignore[attr-defined]
        bar.trade_count or 4,  # type: ignore[attr-defined]
        "0",
        "0",
        "0",
    ]


def _agg_row(trade: object) -> dict[str, object]:
    event_ms = int(trade.event_timestamp.timestamp() * 1000)  # type: ignore[attr-defined]
    return {
        "a": trade.sequence,  # type: ignore[attr-defined]
        "p": str(trade.price),  # type: ignore[attr-defined]
        "q": str(trade.quantity),  # type: ignore[attr-defined]
        "T": event_ms,
        "m": trade.aggressor_side.value == "sell",  # type: ignore[attr-defined]
    }


def _fixture_usdm_handler(
    *,
    ping_status: int = 200,
    drop_prefix: int = 0,
    omit_trades: bool = False,
) -> httpx.MockTransport:
    fixture = canonical_first_slice_fixture()
    klines_15m = [_kline_row(bar) for bar in fixture["bars_15m"]]
    klines_4h = [_kline_row(bar) for bar in fixture["bars_4h"]]
    trades = [_agg_row(trade) for trade in fixture["trades"][drop_prefix:]]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            return httpx.Response(405, json={"msg": "method not allowed"})
        path = request.url.path
        params = dict(request.url.params.items())
        if path.endswith("/fapi/v1/ping"):
            return httpx.Response(ping_status, json={})
        if path.endswith("/fapi/v1/klines"):
            interval = params.get("interval")
            payload = klines_15m if interval == "15m" else klines_4h
            return httpx.Response(200, json=payload)
        if path.endswith("/fapi/v1/aggTrades"):
            if omit_trades:
                return httpx.Response(200, json=[])
            rows = list(trades)
            from_id = params.get("fromId")
            start = params.get("startTime")
            end = params.get("endTime")
            if from_id is not None:
                rows = [row for row in rows if int(row["a"]) >= int(from_id)]
            elif start is not None and end is not None:
                rows = [row for row in rows if int(start) <= int(row["T"]) <= int(end)]
            return httpx.Response(200, json=rows[:1000])
        return httpx.Response(404, json={"msg": "missing"})

    return httpx.MockTransport(handler)


def test_recorded_usdm_fixture_assembles_live_identity() -> None:
    source = BinanceUsdmPerpetualSource(
        base_url="https://fapi.binance.com",
        transport=_fixture_usdm_handler(),
        settlement_grace=RECORDED_FIXTURE_GRACE,
    )
    assembled = FirstSliceEvidenceAssembler(source, replay=False).assemble(
        organization_id=ORG_ID,
        evaluated_at=EVALUATED_AT,
    )
    assert assembled.replay is False
    assert assembled.identity.provenance.is_live is True
    assert assembled.identity.provenance.is_mock is False
    assert assembled.identity.source.family is SourceFamily.BINANCE_USDM_FUTURES_PUBLIC
    assert assembled.current_price.usable_as_current_market_price is True
    assert assembled.current_price.presentation is CurrentPricePresentation.LIVE_MARK
    assert assembled.completeness.cvd is DataCompleteness.COMPLETE
    trigger = assembled.trigger_bar
    assert first_slice_baseline_open(trigger) < trigger.interval_end


def test_provider_outage_fails_closed_without_spot_fallback() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(451, json={"msg": "unavailable for legal reasons"})

    source = BinanceUsdmPerpetualSource(
        base_url="https://fapi.binance.com",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RegionalProviderFailureError):
        FirstSliceEvidenceAssembler(source, replay=False).assemble(
            organization_id=ORG_ID,
            evaluated_at=EVALUATED_AT,
        )


def test_live_adapter_default_catalog_rejects_eth() -> None:
    source = BinanceUsdmPerpetualSource(
        base_url="https://fapi.binance.com",
        transport=_fixture_usdm_handler(),
    )
    with pytest.raises(WrongInstrumentError, match="ETHUSDT"):
        source.fetch_closed_ohlcv(
            identity=first_slice_identity(
                timeframe=Timeframe.M15,
                replay=False,
                is_live=True,
                instrument=eth_instrument(),
            ),
            instrument=eth_instrument(),
            timeframe=Timeframe.M15,
            min_final_bars=1,
            evaluated_at=EVALUATED_AT,
        )


def test_live_adapter_accepts_eth_only_when_catalog_is_extended() -> None:
    catalog = default_perpetual_catalog().extend(binance_usdm_perpetual("ETHUSDT"))
    source = BinanceUsdmPerpetualSource(
        base_url="https://fapi.binance.com",
        transport=_fixture_usdm_handler(),
        catalog=catalog,
        settlement_grace=RECORDED_FIXTURE_GRACE,
    )
    eth = binance_usdm_perpetual("ETHUSDT")
    eth_identity = first_slice_identity(
        timeframe=Timeframe.M15,
        replay=False,
        is_live=True,
        instrument=eth,
    )
    series = source.fetch_closed_ohlcv(
        identity=eth_identity,
        instrument=eth,
        timeframe=Timeframe.M15,
        min_final_bars=1,
        evaluated_at=EVALUATED_AT,
    )
    assert series.identity.instrument.provider_symbol == "ETHUSDT"


def test_spot_identity_cannot_enter_the_assembler() -> None:
    source = ReplayPerpetualSource()
    with pytest.raises(
        (WrongSourceError, SpotFallbackRejectedError, WrongInstrumentError, WrongMarketError)
    ):
        source.fetch_closed_ohlcv(
            identity=spot_identity(),
            instrument=spot_identity().instrument,
            timeframe=Timeframe.M15,
            min_final_bars=1,
            evaluated_at=EVALUATED_AT,
        )


def test_policy_org_mismatch_fails_closed() -> None:
    assembler = _assembler()
    with pytest.raises(WrongSourceError, match="organization_id"):
        assembler.assemble(organization_id=ORG_ID, policy=first_slice_read_policy(OTHER_ORG))


def _evaluation_command(organization_id: UUID) -> EvaluationCommand:
    request = ScanRequest(
        organization_id=organization_id,
        scan_scope="first-slice-btcusdt-15m",
        policy_id=uuid4(),
        policy_version=1,
        policy_content_hash="ab" * 32,
        watchlist_item_ids=(uuid4(),),
        idempotency_key=f"manual:{organization_id}",
    )
    request_hash = scan_request_hash(request)
    return EvaluationCommand(
        command_id=uuid4(),
        request=request,
        request_hash=request_hash,
        evaluation_input_hash=hash_evaluation_input(request),
        mode=EvaluationMode.PREVIEW,
        trigger=ScanTrigger.MANUAL,
        correlation_id=uuid4(),
    )


def _non_placeholder_executable(organization_id: UUID):
    policy = build_fusion_policy(
        policy_version=DEFAULT_FUSION_POLICY_VERSION,
        organization_id=organization_id,
        strategy_version_id=uuid4(),
        executable_setup=ExecutableSetupRef(
            setup_definition_id=uuid4(),
            kind=SetupIdentityKind.COMPILED_SETUP_DEFINITION,
            content_hash="ab" * 32,
        ),
        required_roles=(
            EvidenceRole.TRIGGER_OHLCV,
            EvidenceRole.CONTEXT_OHLCV,
            EvidenceRole.CVD_WINDOW,
        ),
        thresholds=FusionThresholds(
            confirmation_score=Decimal("1"),
            weights=(RuleWeight(rule_id="mandatory_evidence", weight=Decimal("1")),),
        ),
        freshness_policy_version=FIRST_SLICE_FRESHNESS_POLICY_VERSION,
        finality_policy_version=FINALITY_POLICY_VERSION,
        role_timeframes=first_slice_role_timeframes(),
    )
    return executable_policy_from_fusion_policy(
        policy,
        strategy_id=uuid4(),
        strategy_version_content_hash="cd" * 32,
        authored_spec=canonical_first_slice_authored_spec(),
    )


def test_watcher_port_requires_executable_policy_and_stays_inactive() -> None:
    settings = Settings(
        environment="local",
        execution_mode="paper",
        enable_real_trading=False,
        watcher_orchestration_enabled=False,
        perpetual_evidence_source="replay",
    )
    assert settings.watcher_orchestration_enabled is False
    port = AssemblingWatcherScanEvidence(_assembler())
    assert port.load(_evaluation_command(ORG_ID)) is None


def test_watcher_port_binds_tenant_executable_policy() -> None:
    executable = _non_placeholder_executable(ORG_ID)
    port = AssemblingWatcherScanEvidence(
        _assembler(),
        executable_resolver=lambda _command: executable,
    )
    snapshot = port.load(_evaluation_command(ORG_ID))
    assert snapshot is not None
    assert snapshot.organization_id == ORG_ID
    assert snapshot.executable_policy.content_hash == executable.content_hash
    assert snapshot.policy.strategy_version_id == executable.strategy_version_id
    assert snapshot.evidence.snapshot is not None
    assert not is_first_slice_read_projection(
        strategy_version_id=snapshot.executable_policy.strategy_version_id,
        setup_definition_id=snapshot.executable_policy.compiled_setup_definition_id,
    )


def test_watcher_port_refuses_read_projection_placeholders() -> None:
    placeholder_policy = first_slice_read_policy(ORG_ID)
    placeholder = executable_policy_from_fusion_policy(
        placeholder_policy,
        strategy_id=uuid4(),
        strategy_version_content_hash=placeholder_policy.executable_setup.content_hash,
        authored_spec=canonical_first_slice_authored_spec(),
    )
    assert is_first_slice_read_projection(
        strategy_version_id=FIRST_SLICE_READ_STRATEGY_VERSION_ID,
        setup_definition_id=placeholder.compiled_setup_definition_id,
    )
    port = AssemblingWatcherScanEvidence(
        _assembler(),
        executable_resolver=lambda _command: placeholder,
    )
    assert port.load(_evaluation_command(ORG_ID)) is None


def test_watcher_port_fails_closed_on_stale_evidence() -> None:
    class _StaleAssembler(FirstSliceEvidenceAssembler):
        def assemble(self, **_kwargs: object) -> object:  # type: ignore[override]
            raise StaleEvidenceError("stale")

    port = AssemblingWatcherScanEvidence(
        _StaleAssembler(ReplayPerpetualSource(), replay=True),
        executable_resolver=lambda _command: _non_placeholder_executable(ORG_ID),
    )
    with pytest.raises(WatcherEvidenceUnavailableError) as exc:
        port.load(_evaluation_command(ORG_ID))
    assert exc.value.reason_code == "stale_evidence"


def test_defaults_stay_replay_and_paper() -> None:
    settings = Settings(environment="local")
    assert settings.perpetual_evidence_source == "replay"
    assert settings.execution_mode.value == "paper"
    assert settings.enable_real_trading is False
    assert settings.watcher_orchestration_enabled is False


def test_interval_helper_still_matches_15m() -> None:
    assert interval_timedelta(Timeframe.M15) == timedelta(minutes=15)
    assert TRIGGER_OPEN.tzinfo is UTC
    assert EVALUATED_AT > TRIGGER_OPEN
