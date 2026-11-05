"""Replay determinism, leakage, accounting and existing-authority integration."""

from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal, Inexact, localcontext
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.db.models import (
    BacktestDataset,
    BacktestRun,
    BacktestTrade,
    HistoricalCandle,
    JournalTrade,
    Organization,
    User,
    UserStrategy,
    UserStrategyVersion,
)
from app.schemas.backtest import BacktestAssumptions
from app.schemas.common import BacktestRunStatus, BacktestSplitLabel, Timeframe
from app.schemas.nested_continuation import NestedContinuationSpec
from app.schemas.strategy_replay import ReplayComparisonRequest, ReplayWindows, StrategyReplayCreate
from app.services.audit_service import AuditService
from app.services.backtest_hashing import dataset_content_hash
from app.services.backtest_journal_service import BacktestJournalService
from app.services.backtest_service import BacktestService
from app.services.strategy_replay_adapter import NestedReplayAdapter
from app.services.strategy_replay_engine import StrategyReplayEngine
from app.services.strategy_replay_service import StrategyReplayService
from app.strategy_brain.service import create_template
from tests.test_strategy_brain_nested import PRICES, START, bars
from tests.test_watcher_paper_runtime import _sqlite_factory

STEP = timedelta(minutes=15)


def candle_rows(prices=None):
    series = bars(prices or [*PRICES, *PRICES])
    return [
        HistoricalCandle(
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="15m",
            open_time=b.interval_start,
            close_time=b.interval_end - timedelta(seconds=1),
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.base_volume,
            source="binance",
            is_stale=False,
        )
        for b in series
    ]


@pytest.fixture
def store(settings):
    factory = _sqlite_factory()
    org, user = uuid4(), uuid4()
    with factory() as session:
        session.add(Organization(id=org, name="replay"))
        session.add(User(id=user, email="replay@test.example", hashed_password="unused"))
        session.flush()
        template = create_template(
            session,
            organization_id=org,
            user_id=user,
            spec=NestedContinuationSpec(symbol="BTCUSDT"),
        )
        rows = candle_rows()
        session.add_all(rows)
        session.flush()
        session.expire_all()
        dataset = BacktestDataset(
            symbol="BTCUSDT",
            exchange="binance",
            timeframe="15m",
            start_date=START.date(),
            end_date=START.date(),
            candle_count=len(rows),
            first_open_time=rows[0].open_time,
            last_open_time=rows[-1].open_time,
            gap_count=0,
            stale_count=0,
            source_counts={"binance": len(rows)},
            dataset_hash=dataset_content_hash(rows),
        )
        session.add(dataset)
        session.commit()
        request = StrategyReplayCreate(
            strategy_version_id=template["version_id"],
            dataset_id=dataset.id,
            windows=ReplayWindows(
                training_start=START,
                training_end=START + STEP * len(PRICES),
                evaluation_start=START + STEP * len(PRICES),
                evaluation_end=START + STEP * len(rows),
                as_of=START + STEP * len(rows),
            ),
            assumptions=BacktestAssumptions(timeframe="15m", risk_per_trade_pct=Decimal("0.1")),
            idempotency_key="replay-001",
        )
        backtests = BacktestService(session, settings)
        service = StrategyReplayService(session, backtests)
        yield session, org, user, request, backtests, service
    factory.kw["bind"].dispose()


def create(store, request=None):
    session, org, user, original, _backtests, service = store
    queued = service.create(request or original, organization_id=org, user_id=user)
    session.commit()
    return queued


def complete(store, request=None):
    queued = create(store, request)
    session, org, _, _, backtests, _ = store
    result = backtests.execute_run(queued.id, organization_id=org)
    session.commit()
    assert result.status is BacktestRunStatus.COMPLETED, result.error_message
    return result


def test_repeat_restart_hashes_and_existing_trade_journal_authorities(store):
    session, org, user, request, backtests, service = store
    original_version = deepcopy(session.get(UserStrategyVersion, request.strategy_version_id).card)
    run = complete(store)
    assert run.result.metrics.trade_count > 0
    assert run.result.replay["improvement_claim"] is False
    assert run.replay_config["pattern_spec"]["parameters"]["pivot_sensitivity"] == 2
    assert run.replay_config["replay_request"]["windows"]["training_start"]
    assert all(s["status"] == "insufficient_sample" for s in run.result.replay["samples"])
    session.expire_all()
    assert service.create(request, organization_id=org, user_id=user).id == run.id
    assert backtests.verify(run.id, organization_id=org, user_id=user).match
    repeated = complete(store, request.model_copy(update={"idempotency_key": "replay-again"}))
    assert run.result_hash == repeated.result_hash
    assert (
        session.scalar(select(func.count()).select_from(BacktestTrade))
        == 2 * run.result.metrics.trade_count
    )
    trade = backtests.list_trades(run.id, organization_id=org).items[0]
    assert trade.setup_id and trade.r_result is not None and trade.planned_targets
    report = BacktestJournalService(
        session, AuditService(session), backtests._settings
    ).journal_run(
        run.id,
        organization_id=org,
        user_id=user,
        dry_run=False,
    )
    session.commit()
    assert report.created_count == run.result.metrics.trade_count
    journal = session.scalar(
        select(JournalTrade).where(JournalTrade.linked_backtest_trade_id == trade.id)
    )
    assert journal.strategy_version_id == request.strategy_version_id
    assert journal.planned_targets
    assert session.get(UserStrategyVersion, request.strategy_version_id).card == original_version
    assert (
        session.get(UserStrategyVersion, request.strategy_version_id).backtest_status.value
        == "not_run"
    )


def test_next_open_and_no_confirmation_bar_excursion(store):
    run = complete(store)
    trades = run.result.trades
    candidates = run.result.replay["candidates"]
    for trade in trades:
        event = next(c for c in candidates if c["trade_sequence"] == trade.sequence)
        assert trade.entry_time == datetime.fromisoformat(event["detected_at"])
        assert trade.holding_bars >= 1
        assert trade.holding_seconds >= 0
        assert trade.r_result == trade.net_pnl / (
            abs(trade.entry_price - trade.stop_loss) * trade.size
        )
        assert trade.net_pnl == trade.gross_pnl - trade.fees - trade.funding_cost
        assert trade.slippage_cost > 0


@pytest.mark.parametrize("short", [False, True])
def test_causal_adapter_prefixes_no_future_pivot_leakage(short):
    series, adapter = bars(bearish=short), NestedReplayAdapter()
    spec = NestedContinuationSpec(
        symbol="BTCUSDT", direction="short" if short else "long"
    ).model_dump(mode="json")
    full = list(adapter.events(series, spec, BacktestSplitLabel.IN_SAMPLE))
    for length in range(1, len(series) + 1):
        prefix = list(adapter.events(series[:length], spec, BacktestSplitLabel.IN_SAMPLE))
        assert [(i, c.model_dump()) for i, c in prefix] == [
            (i, c.model_dump()) for i, c in full if i < length
        ]
    forming = spec["parameters"]
    assert forming["pivot_sensitivity"] == 2
    assert not any(c.state == "confirmed" for _, c in full if c.detected_at < START + STEP * 13)


def test_evaluation_prices_cannot_change_training_and_decimal_context(store):
    session, _org, _, _, backtests, _ = store
    queued = create(store)
    model = session.get(BacktestRun, queued.id)
    dataset = session.get(BacktestDataset, model.dataset_id)
    rows = backtests._load_dataset_candles(dataset)
    engine = StrategyReplayEngine(backtests._engine)
    first = engine.run(rows=rows, snapshot=model.config_snapshot)
    with localcontext() as ctx:
        ctx.prec = 10
        ctx.rounding = "ROUND_UP"
        ctx.traps[Inexact] = True
        repeated = engine.run(rows=rows, snapshot=model.config_snapshot)
    assert repeated.result_hash == first.result_hash
    changed = deepcopy(rows)
    for row in changed[len(PRICES) :]:
        row.open += 50
        row.high += 50
        row.low += 50
        row.close += 50
    second = engine.run(rows=changed, snapshot=model.config_snapshot)
    assert [t for t in first.trades if t.split_label == "in_sample"] == [
        t for t in second.trades if t.split_label == "in_sample"
    ]
    assert first.replay["samples"][0] == second.replay["samples"][0]
    assert all(
        t.exit_time <= START + STEP * len(PRICES)
        for t in first.trades
        if t.split_label == "in_sample"
    )


@pytest.mark.parametrize(
    "damage,missing",
    [
        ("stale", "stale_candle"),
        ("gap", "candle_gap_or_duplicate"),
        ("future_close", "invalid_candle_close_time"),
        ("coverage", "window_coverage_missing"),
    ],
)
def test_missing_data_is_not_zero_performance(store, damage, missing):
    session, _, _, _, backtests, _ = store
    queued = create(store)
    model = session.get(BacktestRun, queued.id)
    rows = backtests._load_dataset_candles(session.get(BacktestDataset, model.dataset_id))
    if damage == "stale":
        rows[5].is_stale = True
    elif damage == "gap":
        rows.pop(5)
    elif damage == "future_close":
        rows[5].close_time += timedelta(days=1)
    else:
        rows.pop(0)
    result = StrategyReplayEngine(backtests._engine).run(rows=rows, snapshot=model.config_snapshot)
    assert result.replay["samples"][0]["status"] == "missing_data"
    assert missing in result.replay["missing_evidence"]
    assert not any(t.split_label == "in_sample" for t in result.trades)


def test_blocked_risk_and_missing_optional_evidence(store):
    request = store[3].model_copy(
        update={
            "assumptions": store[3].assumptions.model_copy(
                update={"risk_per_trade_pct": Decimal("5")}
            )
        }
    )
    run = complete(store, request)
    assert run.result.metrics.trade_count == 0
    blocked = [c for c in run.result.replay["candidates"] if c["state"] == "blocked"]
    assert blocked
    risk_blocked = [c for c in blocked if c["risk_decision"]]
    assert risk_blocked and all(c["risk_decision"]["action"] == "block" for c in risk_blocked)
    assert any(
        r["rule_id"] == "max_position_size"
        for c in risk_blocked
        for r in c["risk_decision"]["triggered_rules"]
    )
    assert "higher_timeframe" in run.result.replay["missing_evidence"]


def test_idempotency_fences_and_dataset_mutation_verification(store):
    session, org, user, request, backtests, service = store
    run = complete(store)
    with pytest.raises(ConflictError):
        service.create(
            request.model_copy(update={"minimum_sample": 31}), organization_id=org, user_id=user
        )
    with pytest.raises(NotFoundError):
        service.create(request, organization_id=uuid4(), user_id=user)
    candle = session.scalar(select(HistoricalCandle))
    candle.is_stale = True
    session.commit()
    verified = backtests.verify(run.id, organization_id=org, user_id=user)
    assert not verified.match and not verified.dataset_ok


@pytest.mark.parametrize(
    "timeframe", [Timeframe.M5, Timeframe.H1, Timeframe.H4, Timeframe.D1, Timeframe.W1]
)
def test_replay_cannot_reuse_validation_data_from_another_timeframe(store, timeframe):
    session, org, user, request, _, service = store
    template = create_template(
        session,
        organization_id=org,
        user_id=user,
        spec=NestedContinuationSpec(symbol="BTCUSDT", trigger_timeframe=timeframe),
    )
    session.commit()
    with pytest.raises(ValidationAppError, match="instrument/timeframe must match"):
        service.create(
            request.model_copy(update={"strategy_version_id": template["version_id"]}),
            organization_id=org,
            user_id=user,
        )


def test_comparison_exact_windows_versions_and_no_improvement_claim(store):
    session, org, user, request, _backtests, service = store
    baseline = complete(store)
    from app.schemas.common import StrategyChangeSource
    from app.services.strategy_versioning import StrategyVersioningService

    parent = session.get(UserStrategyVersion, request.strategy_version_id)
    changed_spec = NestedContinuationSpec(
        symbol="BTCUSDT", parameters={"relative_volume_threshold": "5"}
    )
    proposed = StrategyVersioningService(session).fork_semantic_update(
        session.get(UserStrategy, parent.strategy_id),
        parent=parent,
        card=parent.card,
        structured_rules=parent.structured_rules,
        lesson_source_metadata=parent.lesson_source_metadata,
        actor_user_id=user,
        source=StrategyChangeSource.PATTERN_SPEC,
        reason="Explicit research proposal",
        pattern_spec=changed_spec.model_dump(mode="json"),
    )
    session.commit()
    second = complete(
        store,
        request.model_copy(
            update={"strategy_version_id": proposed.id, "idempotency_key": "proposed"}
        ),
    )
    comparison_request = ReplayComparisonRequest(
        baseline_run_id=baseline.id, proposed_run_id=second.id
    )
    comparison = service.compare(comparison_request, organization_id=org)
    assert comparison.baseline_version_id != comparison.proposed_version_id
    assert baseline.strategy_id == second.strategy_id
    assert proposed.parent_version_id == request.strategy_version_id
    assert comparison.improvement_claim is False
    assert comparison.evaluation_net_pnl_delta is not None
    assert (
        comparison.comparison_hash
        == service.compare(comparison_request, organization_id=org).comparison_hash
    )
    with pytest.raises(NotFoundError):
        service.compare(comparison_request, organization_id=uuid4())
    third = complete(
        store,
        request.model_copy(
            update={
                "idempotency_key": "different-fees",
                "assumptions": request.assumptions.model_copy(update={"fees_bps": Decimal(6)}),
            }
        ),
    )
    with pytest.raises(ValidationAppError):
        service.compare(
            ReplayComparisonRequest(baseline_run_id=baseline.id, proposed_run_id=third.id),
            organization_id=org,
        )


def test_training_overlap_and_asof_rejected(store):
    windows = store[3].windows.model_dump()
    with pytest.raises(ValidationError):
        ReplayWindows(**{**windows, "evaluation_start": START})
    with pytest.raises(ValidationError):
        ReplayWindows(**{**windows, "as_of": START})


@pytest.mark.parametrize("short", [False, True])
def test_stop_first_slippage_excursions_and_gap_open_exit(store, short):
    from app.market_contracts.hashing import with_content_hash
    from app.schemas.common import TradeDirection
    from app.schemas.strategy_replay import ReplayCandidate
    from app.services.risk.engine import RiskEngine

    direction = TradeDirection.SHORT if short else TradeDirection.LONG
    sign = -1 if short else 1
    series = bars([100] * 5)
    candidate = ReplayCandidate(
        setup_id=uuid4(),
        detected_at=series[0].interval_end,
        split_label=BacktestSplitLabel.IN_SAMPLE,
        direction=direction,
        entry=Decimal(100),
        stop=Decimal(100 - sign * 5),
        targets=[Decimal(100 + sign * 10)],
        state="confirmed",
    )

    class SingleSignal:
        def events(self, bars, spec, label):
            yield 0, candidate.model_copy(deep=True)

    engine = StrategyReplayEngine(store[4]._engine, SingleSignal())
    assumptions = store[3].assumptions
    ambiguous = list(series)
    ambiguous[1] = with_content_hash(
        series[1].model_copy(update={"high": Decimal(120), "low": Decimal(80)})
    )
    trades, _, _, _, _ = engine._segment(
        tuple(ambiguous),
        {"symbol": "BTCUSDT", "direction": direction},
        BacktestSplitLabel.IN_SAMPLE,
        assumptions,
        RiskEngine(),
        0,
    )
    assert len(trades) == 1 and trades[0].exit_reason == "stop_loss"
    assert trades[0].exit_time == series[1].interval_end
    assert trades[0].holding_bars == 1
    assert trades[0].net_pnl == trades[0].gross_pnl - trades[0].fees
    assert trades[0].r_result < -1
    gap = list(series)
    gap_price = Decimal(100 - sign * 20)
    gap[2] = with_content_hash(
        series[2].model_copy(
            update={"open": gap_price, "close": gap_price, "high": Decimal(200), "low": Decimal(1)}
        )
    )
    trades, _, _, _, _ = engine._segment(
        tuple(gap),
        {"symbol": "BTCUSDT", "direction": direction},
        BacktestSplitLabel.IN_SAMPLE,
        assumptions,
        RiskEngine(),
        0,
    )
    assert trades[0].exit_reason == "stop_gap"
    assert trades[0].exit_time == gap[2].interval_start
    assert trades[0].mfe_price != 200 and trades[0].mae_price != 1
    assert trades[0].holding_bars == 1
    assert trades[0].holding_seconds == 900


def test_canonical_daily_risk_observes_only_prior_realized_losses(store):
    from app.market_contracts.hashing import with_content_hash
    from app.schemas.strategy_replay import ReplayCandidate
    from app.services.risk.engine import RiskEngine
    from app.services.risk.limits import RiskLimits

    series = list(bars([100] * 5))
    series[1] = with_content_hash(series[1].model_copy(update={"low": Decimal(90)}))

    class TwoSignals:
        def events(self, bars, spec, label):
            for index in (0, 2):
                yield (
                    index,
                    ReplayCandidate(
                        setup_id=uuid4(),
                        detected_at=bars[index].interval_end,
                        split_label=label,
                        direction="long",
                        entry=Decimal(100),
                        stop=Decimal(95),
                        targets=[Decimal(110)],
                        state="confirmed",
                    ),
                )

    trades, candidates, _, _, _ = StrategyReplayEngine(store[4]._engine, TwoSignals())._segment(
        tuple(series),
        {"symbol": "BTCUSDT", "direction": "long"},
        BacktestSplitLabel.IN_SAMPLE,
        store[3].assumptions,
        RiskEngine(RiskLimits(max_daily_loss_pct=Decimal("0.001"))),
        0,
    )
    # The prior realized loss is observed as a warning, not a block, so the
    # second signal still enters (AT-ADR-152).
    assert len(trades) == 2 and trades[0].net_pnl < 0
    assert [candidate.state for candidate in candidates] == ["entered", "entered"]
    assert "max_daily_loss" in [
        r.rule_id.value for r in candidates[1].risk_decision.triggered_rules
    ]


def test_dataset_authority_hash_uses_database_decimals_across_reload(store):
    from types import SimpleNamespace

    from app.services.backtest_dataset_service import BacktestDatasetService

    session = store[0]
    # Insert a new venue window with integer Decimals still in the identity map.
    rows = candle_rows([101] * 4)
    for row in rows:
        row.exchange = "bybit"
    session.add_all(rows)
    session.flush()
    candle_service = SimpleNamespace(ensure_candles_for_backtest=lambda **kwargs: (rows, []))
    dataset, persisted, _ = BacktestDatasetService(session, candle_service).ensure_dataset(
        symbol="BTCUSDT",
        exchange="bybit",
        timeframe=store[3].assumptions.timeframe,
        start_date=START.date(),
        end_date=START.date(),
    )
    frozen_hash = dataset.dataset_hash
    session.commit()
    session.expire_all()
    assert dataset_content_hash(persisted) == frozen_hash


def test_queued_cancel_failed_dataset_and_observable_status(store):
    session, org, user, request, backtests, _ = store
    queued = create(store)
    cancelled = backtests.cancel(queued.id, organization_id=org, user_id=user)
    session.commit()
    assert cancelled.status is BacktestRunStatus.CANCELLED
    assert (
        backtests.execute_run(queued.id, organization_id=org).status is BacktestRunStatus.CANCELLED
    )
    queued = create(store, request.model_copy(update={"idempotency_key": "fail-dataset"}))
    candle = session.scalar(select(HistoricalCandle))
    candle.close_time += timedelta(seconds=7)
    session.commit()
    failed = backtests.execute_run(queued.id, organization_id=org)
    session.commit()
    assert failed.status is BacktestRunStatus.FAILED
    assert "content changed" in failed.error_message
    assert failed.result is None
    assert failed.started_at is not None and failed.finished_at is not None


def test_replay_api_worker_background_verify_compare_and_rbac():
    from tests.test_at034_integration import ORG_A, USER_A, _auth, _build_client

    def seed(session):
        rows = candle_rows()
        session.add_all(rows)
        session.flush()
        session.expire_all()
        session.add(
            BacktestDataset(
                symbol="BTCUSDT",
                exchange="binance",
                timeframe="15m",
                start_date=START.date(),
                end_date=START.date(),
                candle_count=len(rows),
                first_open_time=rows[0].open_time,
                last_open_time=rows[-1].open_time,
                gap_count=0,
                stale_count=0,
                source_counts={"binance": len(rows)},
                dataset_hash=dataset_content_hash(rows),
            )
        )

    with _build_client(candle_seed=seed) as (client, factory, _settings):
        with factory() as session:
            template = create_template(
                session,
                organization_id=ORG_A,
                user_id=USER_A,
                spec=NestedContinuationSpec(symbol="BTCUSDT"),
            )
            dataset = session.scalar(select(BacktestDataset))
            request = StrategyReplayCreate(
                strategy_version_id=template["version_id"],
                dataset_id=dataset.id,
                windows=ReplayWindows(
                    training_start=START,
                    training_end=START + STEP * len(PRICES),
                    evaluation_start=START + STEP * len(PRICES),
                    evaluation_end=START + STEP * len(PRICES) * 2,
                    as_of=START + STEP * len(PRICES) * 2,
                ),
                assumptions=BacktestAssumptions(timeframe="15m", risk_per_trade_pct=Decimal("0.1")),
                idempotency_key="api-replay",
            )
            session.commit()
        auth = _auth(client, "at034-int-a@test.example")
        response = client.post(
            "/backtests/replays", json=request.model_dump(mode="json"), headers=auth
        )
        assert response.status_code == 201, response.text
        run_id = response.json()["id"]
        run = client.get(f"/backtests/{run_id}", headers=auth).json()
        assert run["status"] == "completed", run
        assert run["result"]["replay"]["improvement_claim"] is False
        assert client.post(f"/backtests/{run_id}/verify", headers=auth).json()["match"] is True
        comparison = client.post(
            "/backtests/replays/compare",
            json={"baseline_run_id": run_id, "proposed_run_id": run_id},
            headers=auth,
        )
        assert comparison.status_code == 200, comparison.text
        assert comparison.json()["improvement_claim"] is False
        other = _auth(client, "at034-int-b@test.example")
        assert client.get(f"/backtests/{run_id}", headers=other).status_code == 404
        assert (
            client.post(
                "/backtests/replays", json=request.model_dump(mode="json"), headers=other
            ).status_code
            == 404
        )
        viewer = _auth(client, "at034-int-viewer@test.example")
        assert (
            client.post(
                "/backtests/replays", json=request.model_dump(mode="json"), headers=viewer
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/backtests/replays/compare",
                json={"baseline_run_id": run_id, "proposed_run_id": run_id},
                headers=viewer,
            ).status_code
            == 403
        )


def test_running_replay_cancellation_is_partial_and_not_journalable(store):
    session, _, _, _, backtests, _ = store
    queued = create(store)
    model = session.get(BacktestRun, queued.id)
    rows = backtests._load_dataset_candles(session.get(BacktestDataset, model.dataset_id))
    result = StrategyReplayEngine(backtests._engine).run(
        rows=rows,
        snapshot=model.config_snapshot,
        should_cancel=lambda: True,
    )
    assert result.cancelled
    assert result.processed_bars == 0 and result.trades == []
    assert all(s["status"] == "cancelled" for s in result.replay["samples"])


def test_stored_result_and_config_tampering_cannot_verify_or_compare(store):
    session, org, user, _, backtests, service = store
    run = complete(store)
    row = session.get(BacktestRun, run.id)
    original = deepcopy(row.result)
    changed = deepcopy(original)
    changed["replay"]["missing_evidence"] = []
    row.result = changed
    session.commit()
    assert not backtests.verify(run.id, organization_id=org, user_id=user).match
    with pytest.raises(ValidationAppError):
        service.compare(
            ReplayComparisonRequest(baseline_run_id=run.id, proposed_run_id=run.id),
            organization_id=org,
        )
    row.result = original
    config = deepcopy(row.config_snapshot)
    config["pattern_spec"]["parameters"]["pivot_sensitivity"] = 3
    row.config_snapshot = config
    session.commit()
    assert not backtests.verify(run.id, organization_id=org, user_id=user).match


@pytest.mark.parametrize(
    "field,value",
    [
        ("start_date", "2026-09-01"),
        ("runner_trail_pct", "2"),
        ("sample_size", 100),
        ("split_config", {"mode": "holdout"}),
        ("funding_assumption", "bullish"),
    ],
)
def test_replay_rejects_unused_legacy_assumption_overrides(store, field, value):
    raw = store[3].model_dump(mode="json")
    raw["assumptions"][field] = value
    with pytest.raises(ValidationError):
        StrategyReplayCreate.model_validate(raw)


def test_missing_evaluation_data_comparison_delta_is_null(store):
    session, org, _, _, _, service = store
    row = session.scalar(
        select(HistoricalCandle).where(HistoricalCandle.open_time >= START + STEP * len(PRICES))
    )
    row.is_stale = True
    session.commit()
    run = complete(store)
    assert run.result.replay["samples"][1]["status"] == "missing_data"
    result = service.compare(
        ReplayComparisonRequest(baseline_run_id=run.id, proposed_run_id=run.id),
        organization_id=org,
    )
    assert result.evaluation_net_pnl_delta is None
    assert result.improvement_claim is False
