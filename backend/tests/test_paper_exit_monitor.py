"""Automatic internal-paper stop and target exits."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from app.core.config import Environment, Settings
from app.core.errors import ValidationAppError
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.adapters.replay import ReplayPerpetualSource
from app.market_contracts.errors import FormingCandleError
from app.market_contracts.ohlcv import require_closed_series
from app.schemas.common import Timeframe, TradeDirection
from app.schemas.execution_protocol import ClosePaperPlanHttpRequest, ClosePaperPlanRequest
from app.services.canonical_paper_execution import (
    paper_exit_audit_fields,
    profile_close_should_note,
)
from app.services.paper_exit_candles import (
    PaperExitCandleLoader,
    build_candle_source,
    candle_mode_for_trade,
    fetch_closed_bars_since,
    identity_for_trade,
)
from app.services.paper_exit_monitor import (
    EXIT_ACTOR,
    EXIT_CHANNEL,
    ExitBar,
    FetchedBars,
    OpenInternalPaperTrade,
    PaperExitCloser,
    classify_exit_origin,
    counts_for_adr109_calibration,
    exit_idempotency_key,
    exit_monitor_backfill,
    is_internal_paper_plan,
    open_internal_paper_statement,
    parse_open_trade,
    run_paper_exit_monitor,
)
from app.workers.paper_exit_monitor import PaperExitMonitorCycle, run_configured_exit_monitor
from app.workers.paper_worker import (
    CycleOutcome,
    PaperWorkerSupervisor,
    build_paper_worker_supervisor,
)
from tests.support.phase5_market import closed_bar, identity

ORG = UUID("10000000-0000-4000-8000-000000000001")
USER = UUID("20000000-0000-4000-8000-000000000002")
ACCOUNT = UUID("30000000-0000-4000-8000-000000000003")
REVISION = UUID("40000000-0000-4000-8000-000000000004")
COMMAND = UUID("50000000-0000-4000-8000-000000000005")
JOURNAL = UUID("60000000-0000-4000-8000-000000000006")
ENTRY = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


class RecordingCloser(PaperExitCloser):
    def __init__(self) -> None:
        self.requests: list[ClosePaperPlanRequest] = []

    def close(self, request: ClosePaperPlanRequest) -> SimpleNamespace:
        replayed = any(item.idempotency_key == request.idempotency_key for item in self.requests)
        if replayed:
            previous = next(
                item for item in self.requests if item.idempotency_key == request.idempotency_key
            )
            assert previous == request
        self.requests.append(request)
        return SimpleNamespace(replayed=replayed)


def _trade(
    *,
    direction: TradeDirection = TradeDirection.LONG,
    entry: Decimal = Decimal("100"),
    stop: Decimal = Decimal("95"),
    target: Decimal = Decimal("110"),
    entry_time: datetime = ENTRY,
    fees: Decimal = Decimal("0"),
    funding: Decimal = Decimal("0"),
    slippage: Decimal = Decimal("0"),
    command_id: UUID = COMMAND,
    journal_trade_id: UUID = JOURNAL,
    evidence_venue: str | None = None,
    evidence_source_family: str | None = None,
    evidence_is_live: bool | None = None,
    evidence_is_mock: bool | None = None,
) -> OpenInternalPaperTrade:
    return OpenInternalPaperTrade(
        journal_trade_id=journal_trade_id,
        organization_id=ORG,
        user_id=USER,
        account_id=ACCOUNT,
        revision_id=REVISION,
        command_id=command_id,
        symbol="BTCUSDT",
        timeframe=Timeframe.M15,
        direction=direction,
        entry_price=entry,
        entry_time=entry_time,
        stop=stop,
        target=target,
        fees=fees,
        funding=funding,
        slippage=slippage,
        evidence_venue=evidence_venue,
        evidence_source_family=evidence_source_family,
        evidence_is_live=evidence_is_live,
        evidence_is_mock=evidence_is_mock,
    )


def _bar(
    start: datetime,
    *,
    low: str,
    high: str,
    open_: str | None = None,
    close: str | None = None,
) -> ExitBar:
    low_d = Decimal(low)
    high_d = Decimal(high)
    open_d = Decimal(open_) if open_ is not None else (low_d + high_d) / Decimal("2")
    close_d = Decimal(close) if close is not None else open_d
    return ExitBar(
        interval_start=start,
        interval_end=start + timedelta(minutes=15),
        open=open_d,
        high=high_d,
        low=low_d,
        close=close_d,
        source_event_id=f"bar-{start.isoformat()}",
        content_hash="a" * 64,
    )


def _fetched(bars: tuple[ExitBar, ...], *, covers: bool = True) -> FetchedBars:
    return FetchedBars(bars=bars, covers_entry=covers, source_name="test-perpetual")


def _run(
    trade: OpenInternalPaperTrade,
    bars: tuple[ExitBar, ...],
    *,
    enabled: bool = True,
    dry_run: bool = False,
    covers: bool = True,
    closer: PaperExitCloser | None = None,
    evaluated_at: datetime = NOW,
) -> tuple[object, RecordingCloser]:
    recorder = closer if closer is not None else RecordingCloser()
    report = run_paper_exit_monitor(
        enabled=enabled,
        dry_run=dry_run,
        trades=(trade,),
        candles=lambda _trade: _fetched(bars, covers=covers),
        closer=recorder,
        evaluated_at=evaluated_at,
    )
    return report, recorder


def test_long_stop_hit_closes_at_the_stop_with_zero_internal_paper_costs() -> None:
    start = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
    report, closer = _run(_trade(), (_bar(start, low="94", high="101"),))
    assert report.status == "closed" and report.closed == 1
    request = closer.requests[0]
    assert request.exit_reason == "stop_hit"
    assert request.exit_price == Decimal("95")
    assert request.fees == Decimal("0")
    assert request.funding == Decimal("0")
    assert request.slippage == Decimal("0")
    assert request.occurred_at == start + timedelta(minutes=15)
    assert request.exit_channel == EXIT_CHANNEL
    assert request.exit_actor == EXIT_ACTOR
    assert request.exit_evidence is not None
    assert request.exit_evidence["same_bar_assumption"] == "none"
    assert request.exit_evidence["level"] == "stop"
    assert request.idempotency_key == exit_idempotency_key(COMMAND)


def test_short_target_hit_closes_at_the_target() -> None:
    start = datetime(2026, 10, 3, 0, 0, tzinfo=UTC)
    short = _trade(
        direction=TradeDirection.SHORT,
        entry=Decimal("100"),
        stop=Decimal("105"),
        target=Decimal("90"),
    )
    report, closer = _run(short, (_bar(start, low="89", high="99"),))
    assert report.closed == 1
    request = closer.requests[0]
    assert request.exit_reason == "target_hit"
    assert request.exit_price == Decimal("90")
    assert request.exit_evidence is not None
    assert request.exit_evidence["direction"] == "short"


@pytest.mark.parametrize(
    ("direction", "stop", "target"),
    [
        (TradeDirection.LONG, Decimal("95"), Decimal("110")),
        (TradeDirection.SHORT, Decimal("105"), Decimal("90")),
    ],
)
def test_same_bar_stop_and_target_assume_the_stop(
    direction: TradeDirection,
    stop: Decimal,
    target: Decimal,
) -> None:
    start = datetime(2026, 10, 4, 0, 0, tzinfo=UTC)
    trade = _trade(direction=direction, stop=stop, target=target)
    report, closer = _run(trade, (_bar(start, low="80", high="120"),))
    assert report.closed == 1
    request = closer.requests[0]
    assert request.exit_reason == "stop_hit"
    assert request.exit_price == stop
    assert request.exit_evidence is not None
    assert request.exit_evidence["same_bar_assumption"] == "stop_first"
    assert request.occurred_at == start + timedelta(minutes=15)


def test_no_hit_leaves_the_trade_open() -> None:
    start = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
    report, closer = _run(_trade(), (_bar(start, low="96", high="109"),))
    assert report.status == "idle"
    assert report.closed == 0
    assert closer.requests == []


def test_idempotent_rerun_replays_the_same_close() -> None:
    start = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
    bars = (_bar(start, low="94", high="101"),)
    closer = RecordingCloser()
    first, _ = _run(_trade(), bars, closer=closer)
    second, _ = _run(_trade(), bars, closer=closer)
    assert first.closed == 1 and first.replayed == 0
    assert second.closed == 0 and second.replayed == 1
    assert len(closer.requests) == 2
    assert closer.requests[0].idempotency_key == closer.requests[1].idempotency_key
    assert closer.requests[0].occurred_at == closer.requests[1].occurred_at


def test_flag_off_does_not_read_candles_or_close() -> None:
    def forbidden(_trade: OpenInternalPaperTrade) -> FetchedBars:
        raise AssertionError("flag off loaded candles")

    closer = RecordingCloser()
    report = run_paper_exit_monitor(
        enabled=False,
        dry_run=False,
        trades=(_trade(),),
        candles=forbidden,
        closer=closer,
        evaluated_at=NOW,
    )
    assert report.status == "disarmed"
    assert report.examined == 0
    assert closer.requests == []


def test_dry_run_logs_the_close_without_writing() -> None:
    start = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    class Boom(PaperExitCloser):
        def close(self, request: ClosePaperPlanRequest) -> SimpleNamespace:
            raise AssertionError("dry-run wrote a close")

    with capture_logs() as logs:
        report, _ = _run(
            _trade(),
            (_bar(start, low="94", high="101"),),
            dry_run=True,
            closer=Boom(),
        )
    assert report.status == "dry_run"
    assert report.would_close == 1
    assert report.closed == 0
    events = [entry["event"] for entry in logs]
    assert "paper_exit_monitor_would_close" in events
    recorded = next(entry for entry in logs if entry["event"] == "paper_exit_monitor_would_close")
    assert recorded["exit_reason"] == "stop_hit"
    assert recorded["exit_channel"] == EXIT_CHANNEL
    assert recorded["exit_actor"] == EXIT_ACTOR


def test_historical_crossing_closes_at_the_first_bar_not_a_later_one() -> None:
    """Models trades that had already crossed on 6 Oct and stayed open."""

    before = datetime(2026, 9, 30, 0, 0, tzinfo=UTC)
    crossing = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
    later = datetime(2026, 10, 7, 0, 0, tzinfo=UTC)
    bars = (
        _bar(before, low="80", high="130"),
        _bar(crossing, low="94", high="101"),
        _bar(later, low="90", high="120"),
    )
    report, closer = _run(_trade(), bars)
    assert report.closed == 1
    request = closer.requests[0]
    assert request.exit_reason == "stop_hit"
    assert request.exit_price == Decimal("95")
    assert request.occurred_at == crossing + timedelta(minutes=15)
    assert request.exit_evidence is not None
    assert request.exit_evidence["bar_interval_start"] == crossing.isoformat()
    assert request.exit_evidence["exit_origin"] == "backfill"
    assert request.exit_evidence["calibration_sample"] == "excluded"
    assert exit_monitor_backfill(request) is True
    assert counts_for_adr109_calibration(request) is False
    assert (
        profile_close_should_note(
            request,
            replayed=False,
            margin_assumption_id="paper-profile-isolated",
            net_pnl=Decimal("-5"),
        )
        is False
    )


def test_same_day_crossing_feeds_the_breaker_and_stays_eligible() -> None:
    """A stop touched on the evaluation UTC day is a live profile outcome."""

    start = datetime(2026, 10, 18, 11, 0, tzinfo=UTC)
    evaluated = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    _report, closer = _run(
        _trade(),
        (_bar(start, low="94", high="101"),),
        evaluated_at=evaluated,
    )
    request = closer.requests[0]
    assert request.exit_evidence is not None
    assert request.exit_evidence["exit_origin"] == "live"
    assert request.exit_evidence["calibration_sample"] == "eligible"
    assert exit_monitor_backfill(request) is False
    assert counts_for_adr109_calibration(request) is True
    assert profile_close_should_note(
        request,
        replayed=False,
        margin_assumption_id="paper-profile-isolated",
        net_pnl=Decimal("-5"),
    )
    assert (
        profile_close_should_note(
            request,
            replayed=False,
            margin_assumption_id="paper-internal-cash",
            net_pnl=Decimal("-5"),
        )
        is False
    )
    assert (
        profile_close_should_note(
            request,
            replayed=True,
            margin_assumption_id="paper-profile-isolated",
            net_pnl=Decimal("-5"),
        )
        is False
    )
    human = ClosePaperPlanRequest(
        organization_id=ORG,
        user_id=USER,
        account_id=ACCOUNT,
        revision_id=REVISION,
        command_id=COMMAND,
        exit_price=Decimal("95"),
        exit_reason="manual",
        occurred_at=evaluated,
        idempotency_key="human-close",
    )
    assert counts_for_adr109_calibration(human) is False
    assert profile_close_should_note(
        human,
        replayed=False,
        margin_assumption_id="paper-profile-isolated",
        net_pnl=Decimal("-5"),
    )


def test_naive_exit_time_is_labelled_backfill() -> None:
    naive = datetime(2026, 10, 18, 12, 0)
    assert classify_exit_origin(naive, NOW) == "backfill"
    assert classify_exit_origin(NOW, naive) == "backfill"


def test_trade_opened_at_bar_close_exits_on_the_next_bar() -> None:
    """Entry at interval_end makes the following bar the first eligible bar."""

    entry = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
    fill_bar = datetime(2026, 10, 5, 23, 45, tzinfo=UTC)
    next_bar = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
    trade = _trade(entry_time=entry)
    untouched, closer = _run(
        trade,
        (
            _bar(fill_bar, low="90", high="101"),
            _bar(next_bar, low="96", high="109"),
        ),
    )
    assert untouched.closed == 0
    assert closer.requests == []
    report, closed = _run(
        trade,
        (
            _bar(fill_bar, low="90", high="101"),
            _bar(next_bar, low="94", high="109"),
        ),
    )
    assert report.closed == 1
    request = closed.requests[0]
    assert request.exit_reason == "stop_hit"
    assert request.exit_price == Decimal("95")
    assert request.occurred_at == next_bar + timedelta(minutes=15)
    assert request.exit_evidence is not None
    assert request.exit_evidence["bar_interval_start"] == next_bar.isoformat()


def test_bar_containing_the_entry_is_not_a_hit() -> None:
    entry = datetime(2026, 10, 6, 0, 7, tzinfo=UTC)
    entry_bar = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
    later = datetime(2026, 10, 6, 0, 15, tzinfo=UTC)
    trade = _trade(entry_time=entry)
    report, closer = _run(
        trade,
        (
            _bar(entry_bar, low="90", high="101"),
            _bar(later, low="96", high="109"),
        ),
    )
    assert report.closed == 0
    assert closer.requests == []


def test_incomplete_history_does_not_close_a_visible_hit() -> None:
    start = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
    report, closer = _run(_trade(), (_bar(start, low="90", high="101"),), covers=False)
    assert report.closed == 0
    assert report.skipped == 1
    assert closer.requests == []


def test_plan_allowances_are_the_close_costs() -> None:
    trade = _trade(fees=Decimal("0.10"), funding=Decimal("0.20"), slippage=Decimal("0.30"))
    start = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
    _report, closer = _run(trade, (_bar(start, low="94", high="101"),))
    request = closer.requests[0]
    assert request.fees == Decimal("0.10")
    assert request.funding == Decimal("0.20")
    assert request.slippage == Decimal("0.30")


def test_manual_and_governed_demo_plans_are_not_internal_paper() -> None:
    assert is_internal_paper_plan(
        plan_authority="canonical",
        execution_venue="PAPER_INTERNAL",
        execution_policy_version="paper-internal-entry/v1",
    )
    assert not is_internal_paper_plan(
        plan_authority="manual_demo_test",
        execution_venue="BLOFIN_DEMO",
        execution_policy_version="manual-blofin-demo/v1",
    )
    assert not is_internal_paper_plan(
        plan_authority="canonical",
        execution_venue="BLOFIN_DEMO",
        execution_policy_version="governed-blofin-demo/v1",
    )
    governed = _parse_kwargs(
        execution_venue="BLOFIN_DEMO",
        execution_policy_version="governed-blofin-demo/v1",
    )
    assert parse_open_trade(**governed) is None
    assert parse_open_trade(**_parse_kwargs(plan_authority="manual_demo_test")) is None


def test_open_query_requires_canonical_internal_paper() -> None:
    compiled = str(open_internal_paper_statement().compile(compile_kwargs={"literal_binds": True}))
    assert "PAPER_INTERNAL" in compiled
    assert "paper-internal-entry/v1" in compiled
    assert "canonical" in compiled
    assert "BLOFIN_DEMO" not in compiled


def test_human_close_payload_has_no_monitor_identity() -> None:
    request = ClosePaperPlanRequest(
        organization_id=ORG,
        user_id=USER,
        account_id=ACCOUNT,
        revision_id=REVISION,
        command_id=COMMAND,
        exit_price=Decimal("95"),
        exit_reason="manual",
        occurred_at=NOW,
        idempotency_key="human-close",
    )
    assert paper_exit_audit_fields(request) == {}


def test_http_close_cannot_set_the_monitor_channel() -> None:
    with pytest.raises(ValidationError) as caught:
        ClosePaperPlanHttpRequest.model_validate(
            {"exit_channel": EXIT_CHANNEL, "exit_actor": EXIT_ACTOR}
        )
    locations = {error["loc"] for error in caught.value.errors()}
    assert ("exit_channel",) in locations
    assert ("exit_actor",) in locations


def test_monitor_fields_are_copied_when_present() -> None:
    request = ClosePaperPlanRequest(
        organization_id=ORG,
        user_id=USER,
        account_id=ACCOUNT,
        revision_id=REVISION,
        command_id=COMMAND,
        exit_price=Decimal("95"),
        exit_reason="stop_hit",
        occurred_at=NOW,
        idempotency_key=exit_idempotency_key(COMMAND),
        exit_channel=EXIT_CHANNEL,
        exit_actor=EXIT_ACTOR,
        exit_evidence={"level": "stop", "same_bar_assumption": "none"},
    )
    assert paper_exit_audit_fields(request)["exit_channel"] == EXIT_CHANNEL
    assert paper_exit_audit_fields(request)["exit_actor"] == EXIT_ACTOR


def test_already_closed_is_skipped() -> None:
    start = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)

    class Closed(PaperExitCloser):
        def close(self, request: ClosePaperPlanRequest) -> SimpleNamespace:
            raise ValidationAppError(
                "Paper trade is already closed.",
                details={"reason": "already_closed"},
            )

    report, _closer = _run(_trade(), (_bar(start, low="94", high="101"),), closer=Closed())
    assert report.closed == 0
    assert report.skipped == 1
    assert report.errors == 0


def test_candle_fetch_pages_back_to_the_entry() -> None:
    first = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
    far = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)
    delta = timedelta(minutes=15)
    market = identity()
    bars = [
        closed_bar(open_time=first + (delta * index), index=index, evaluated_at=far)
        for index in range(5)
    ]

    class PagingSource:
        name = "test-perpetual"

        def __init__(self) -> None:
            self.calls: list[int] = []

        def fetch_closed_ohlcv(
            self,
            *,
            identity: object,
            instrument: object,
            timeframe: Timeframe,
            min_final_bars: int,
            evaluated_at: datetime,
        ) -> object:
            self.calls.append(min_final_bars)
            eligible = [bar for bar in bars if bar.interval_end <= evaluated_at]
            if len(eligible) < min_final_bars:
                raise FormingCandleError("short history")
            selected = eligible[-min_final_bars:]
            return require_closed_series(
                selected,
                identity=market,
                timeframe=timeframe,
                evaluated_at=evaluated_at,
                min_bars=min_final_bars,
            )

    source = PagingSource()
    fetched = fetch_closed_bars_since(
        source,  # type: ignore[arg-type]
        identity=market,
        instrument=market.instrument,
        timeframe=Timeframe.M15,
        entry_time=first,
        evaluated_at=far,
    )
    assert fetched.covers_entry is True
    assert len(fetched.bars) == 5
    assert fetched.bars[0].interval_start == first
    assert len(source.calls) > 1


def test_replay_source_is_the_strategy_evidence_source() -> None:
    source = ReplayPerpetualSource()
    market, instrument = identity_for_trade(
        source,
        symbol="BTCUSDT",
        timeframe=Timeframe.M15,
        mode="replay",
    )
    assert market.provenance.is_live is False
    fetched = fetch_closed_bars_since(
        source,
        identity=market,
        instrument=instrument,
        timeframe=Timeframe.M15,
        entry_time=datetime(2026, 10, 1, tzinfo=UTC),
        evaluated_at=NOW,
    )
    assert fetched.source_name == source.name
    assert fetched.bars


def _live_btc(command_id: UUID, **overrides: object) -> OpenInternalPaperTrade:
    fields: dict[str, object] = {
        "command_id": command_id,
        "evidence_venue": "binance",
        "evidence_source_family": "binance_usdm_futures_public",
        "evidence_is_live": True,
        "evidence_is_mock": False,
    }
    fields.update(overrides)
    return _trade(**fields)  # type: ignore[arg-type]


def test_live_identity_uses_binance_while_the_process_default_is_replay() -> None:
    settings = Settings(_env_file=None)
    assert settings.perpetual_evidence_source == "replay"
    trade = _live_btc(COMMAND)
    mode, origin = candle_mode_for_trade(trade, settings)
    assert mode == "binance_usdm"
    assert origin == "trade"
    source = build_candle_source(settings, mode, origin)
    assert isinstance(source, BinanceUsdmPerpetualSource)
    market, _instrument = identity_for_trade(
        source,
        symbol="BTCUSDT",
        timeframe=Timeframe.M15,
        mode=mode,
    )
    assert market.provenance.is_live is True
    assert market.provenance.is_mock is False
    assert market.timeframe is Timeframe.M15
    assert market.instrument.provider_symbol == "BTCUSDT"


def test_replay_identity_stays_on_replay_in_staging() -> None:
    settings = Settings.model_construct(
        environment=Environment.STAGING,
        perpetual_evidence_source="replay",
    )
    trade = _trade(
        evidence_venue="binance",
        evidence_source_family="replay_fixture",
        evidence_is_live=False,
        evidence_is_mock=True,
    )
    mode, origin = candle_mode_for_trade(trade, settings)
    assert mode == "replay"
    assert origin == "trade"
    assert isinstance(build_candle_source(settings, mode, origin), ReplayPerpetualSource)


def test_staging_without_a_stored_identity_uses_binance_not_the_replay_default() -> None:
    settings = Settings.model_construct(
        environment=Environment.STAGING,
        perpetual_evidence_source="replay",
    )
    mode, origin = candle_mode_for_trade(_trade(), settings)
    assert mode == "binance_usdm"
    assert origin == "staging_live"
    built = build_candle_source(Settings(_env_file=None), mode, origin)
    assert isinstance(built, BinanceUsdmPerpetualSource)


def test_october_6_live_crossings_are_read_from_binance_not_replay() -> None:
    """Short 85111.4 / 85720.8 / 84714.1 and long 86073.1 / 85618.0 / 86881.1.

    Both open at the 6 Oct 00:00 bar close. The fill bar already touches a
    level and must not exit. The 00:00 bar is the first eligible bar.
    """

    settings = Settings(_env_file=None)
    entry = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
    fill_bar = datetime(2026, 10, 5, 23, 45, tzinfo=UTC)
    crossing = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
    short = _live_btc(
        COMMAND,
        direction=TradeDirection.SHORT,
        entry=Decimal("85111.4"),
        stop=Decimal("85720.8"),
        target=Decimal("84714.1"),
        entry_time=entry,
    )
    long = _live_btc(
        UUID("50000000-0000-4000-8000-000000000099"),
        direction=TradeDirection.LONG,
        entry=Decimal("86073.1"),
        stop=Decimal("85618.0"),
        target=Decimal("86881.1"),
        entry_time=entry,
        journal_trade_id=UUID("60000000-0000-4000-8000-000000000099"),
    )
    bars = (
        _bar(fill_bar, low="85000", high="86000"),
        _bar(crossing, low="85650", high="86881.1"),
    )

    class LiveBinance:
        name = "binance-usdm-perpetual"

        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def fetch_closed_ohlcv(self, **kwargs: object) -> SimpleNamespace:
            instrument = kwargs["instrument"]
            timeframe = kwargs["timeframe"]
            symbol = getattr(instrument, "provider_symbol", "")
            value = getattr(timeframe, "value", "")
            self.calls.append((str(symbol), str(value)))
            return SimpleNamespace(bars=bars)

    live = LiveBinance()
    loader = PaperExitCandleLoader(settings, sources={"binance_usdm": live})  # type: ignore[arg-type]
    report = run_paper_exit_monitor(
        enabled=True,
        dry_run=False,
        trades=(short, long),
        candles=lambda trade: loader(trade, evaluated_at=NOW),
        closer=(closer := RecordingCloser()),
        evaluated_at=NOW,
    )
    assert report.closed == 2
    assert live.calls == [("BTCUSDT", "15m")]
    by_reason = {item.exit_reason: item for item in closer.requests}
    short_close = by_reason["stop_hit"]
    long_close = by_reason["target_hit"]
    assert short_close.exit_price == Decimal("85720.8")
    assert long_close.exit_price == Decimal("86881.1")
    assert short_close.occurred_at == crossing + timedelta(minutes=15)
    assert long_close.occurred_at == crossing + timedelta(minutes=15)
    assert short_close.exit_evidence is not None
    assert short_close.exit_evidence["market_source"] == "binance-usdm-perpetual"
    assert short_close.exit_evidence["bar_interval_start"] == crossing.isoformat()
    assert short_close.exit_evidence["exit_origin"] == "backfill"
    assert long_close.exit_evidence is not None
    assert long_close.exit_evidence["exit_origin"] == "backfill"
    assert long_close.exit_evidence["calibration_sample"] == "excluded"
    assert counts_for_adr109_calibration(short_close) is False
    assert counts_for_adr109_calibration(long_close) is False
    journal = paper_exit_audit_fields(short_close)
    assert journal["exit_origin"] == "backfill"
    assert journal["calibration_sample"] == "excluded"


def test_disabled_cycle_does_not_open_a_session(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden() -> None:
        raise AssertionError("disabled monitor opened a session")

    monkeypatch.setattr("app.workers.paper_exit_monitor._session_factory", forbidden)
    outcome = PaperExitMonitorCycle(Settings(_env_file=None))()
    assert outcome.status == "disarmed"


def test_enabled_dry_run_reads_without_building_a_closer() -> None:
    class Factory:
        def __call__(self) -> SimpleNamespace:
            return SimpleNamespace(
                execute=lambda _statement: SimpleNamespace(all=list),
                rollback=lambda: None,
                close=lambda: None,
            )

    settings = Settings(
        _env_file=None,
        paper_exit_monitor_enabled=True,
        paper_exit_monitor_dry_run=True,
    )
    report = run_configured_exit_monitor(settings, session_factory=Factory())  # type: ignore[arg-type]
    assert report.status == "dry_run"
    assert report.examined == 0
    assert report.closed == 0


def test_supervisor_leaves_the_monitor_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.workers.paper_worker._build_armed_supervisor",
        lambda *_args: _supervisor(),
    )
    worker = build_paper_worker_supervisor(Settings(_env_file=None))
    assert worker.run_round().exit_monitor is None
    worker.close()


def test_supervisor_runs_the_monitor_when_the_flag_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.workers.paper_worker._build_armed_supervisor",
        lambda *_args: _supervisor(),
    )

    class Stub:
        def __init__(self, settings: Settings) -> None:
            assert settings.paper_exit_monitor_enabled is True

        def __call__(self) -> CycleOutcome:
            return CycleOutcome("running")

        def request_stop(self) -> None:
            return None

    monkeypatch.setattr("app.workers.paper_exit_monitor.PaperExitMonitorCycle", Stub)
    settings = Settings(_env_file=None, paper_exit_monitor_enabled=True)
    worker = build_paper_worker_supervisor(settings)
    health = worker.run_round()
    assert health.exit_monitor is not None
    assert health.exit_monitor.status == "running"
    worker.close()


def test_parse_reads_internal_paper_allowances() -> None:
    parsed = parse_open_trade(**_parse_kwargs())
    assert parsed is not None
    assert parsed.stop == Decimal("95")
    assert parsed.target == Decimal("110")
    assert parsed.fees == Decimal("0")
    assert parsed.funding == Decimal("0")
    assert parsed.slippage == Decimal("0")
    assert parsed.direction is TradeDirection.LONG


def _parse_kwargs(**overrides: object) -> dict[str, object]:
    payload = {
        "timeframe": "15m",
        "risk_and_exits": {
            "stop": {"value": "95", "unit": "USDT"},
            "targets": [
                {
                    "order": 1,
                    "price": {"value": "110", "unit": "USDT"},
                    "quantity_fraction": "1",
                }
            ],
            "runner": {"enabled": False},
            "fee_allowance": {"value": "0", "unit": "USDT"},
            "funding_allowance": {"value": "0", "unit": "USDT"},
            "slippage_allowance": {"value": "0", "unit": "USDT"},
        },
    }
    values: dict[str, object] = {
        "journal_trade_id": JOURNAL,
        "organization_id": ORG,
        "user_id": USER,
        "account_id": ACCOUNT,
        "command_id": COMMAND,
        "direction": TradeDirection.LONG,
        "entry_price": Decimal("100"),
        "entry_time": ENTRY,
        "journal_symbol": "BTCUSDT",
        "journal_timeframe": "15m",
        "plan_authority": "canonical",
        "execution_venue": "PAPER_INTERNAL",
        "execution_policy_version": "paper-internal-entry/v1",
        "execution_instrument": "BTCUSDT",
        "plan_organization_id": ORG,
        "plan_user_id": USER,
        "plan_account_id": ACCOUNT,
        "revision_id": REVISION,
        "semantic_payload": payload,
    }
    values.update(overrides)
    return values


def _supervisor() -> PaperWorkerSupervisor:
    return PaperWorkerSupervisor(
        watcher_cycle=lambda: CycleOutcome("running"),
        telegram_cycle=lambda: CycleOutcome("disarmed"),
        poll_seconds=0.01,
    )
