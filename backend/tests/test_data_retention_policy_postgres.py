"""Approved data policy on PostgreSQL: real schema, real SQL, real foreign keys.

Covers the 7-day candidate purge (summary first), the 24-hour noise policies,
BloFin snapshot thinning, the 90-day audit window, dry run and keep-forever rows.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.models import (
    ApprovalRequest,
    JournalTrade,
    Organization,
    TradeProposal,
    User,
    UserStrategy,
    UserStrategyVersion,
)
from app.db.trade_candidate_summaries import TradeCandidateSummaryRow
from app.schemas.common import (
    JournalTradeSource,
    JournalTradeStatus,
    RiskSeverity,
    StrategyId,
    TradeDirection,
    TradeResult,
)
from app.services.db_retention import run_retention
from tests.support.postgres_persistence import POSTGRES_URL, requires_postgres

pytestmark = requires_postgres

NOW = datetime(2026, 11, 16, 2, 30, tzinfo=UTC)


@pytest.fixture
def pg_factory() -> Iterator[sessionmaker[Session]]:
    from tests.support.postgres_template import clone_database, drop_clone

    engine = clone_database(POSTGRES_URL)
    try:
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        drop_clone(engine)


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, retention_batch_size=100, **overrides)  # type: ignore[arg-type]


def _exec(factory: sessionmaker[Session], sql: str, rows: list[dict[str, object]]) -> None:
    with factory() as session:
        session.execute(text(sql), rows)
        session.commit()


def _ids(factory: sessionmaker[Session], sql: str) -> set[str]:
    with factory() as session:
        return {str(value) for value in session.execute(text(sql)).scalars().all()}


def _count(factory: sessionmaker[Session], table: str) -> int:
    with factory() as session:
        return int(session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())


class World:
    """One tenant with an SFP 15m strategy version."""

    def __init__(self, factory: sessionmaker[Session], *, timeframe: str = "15m") -> None:
        self.org = uuid4()
        self.user = uuid4()
        self.version = uuid4()
        with factory() as session:
            session.add(Organization(id=self.org, name=f"retention {self.org}"))
            session.add(User(id=self.user, email=f"r-{self.user}@example.com", hashed_password="x"))
            session.flush()
            strategy = UserStrategy(
                organization_id=self.org,
                user_id=self.user,
                name="SFP",
                setup_type=StrategyId.HTF_TREND_PULLBACK,
            )
            session.add(strategy)
            session.flush()
            self.strategy = strategy.id
            session.add(
                UserStrategyVersion(
                    id=self.version,
                    strategy_id=strategy.id,
                    version=1,
                    card={"strategy_name": "SFP"},
                    pattern_spec={
                        "kind": "swing_failure_pattern/v1",
                        "trigger_timeframe": timeframe,
                        "parameters": {
                            "level_lookback": 72,
                            "expiry_bars": 6,
                            "quality_lookback": 3,
                            "pivot_width": 2,
                        },
                    },
                )
            )
            session.commit()


def _candidate(
    factory: sessionmaker[Session], world: World, *, age: timedelta, symbol: str = "BTCUSDT"
) -> UUID:
    candidate_id = uuid4()
    created = NOW - age
    with factory() as session:
        session.execute(
            text(
                "INSERT INTO canonical_candidates (candidate_id, organization_id, "
                "uniqueness_hash, schema_version, strategy_version_id, setup_definition_id, "
                "fusion_policy_version, direction, evidence_venue, evidence_market, "
                "evidence_instrument, timeframe, evidence_window_hash, assessment_id, "
                "executable_setup, evidence_identity, state, created_at, valid_until, "
                "transition_version, idempotency_key, correlation_id, content_hash) VALUES "
                "(:id, :org, :h1, 'v1', :sv, :sd, 'f1', 'long', 'binance', 'perp', :sym, "
                "'15m', :h2, :a, '{}', '{}', 'expired', :at, :until, 2, :key, :corr, :h3)"
            ),
            {
                "id": candidate_id,
                "org": world.org,
                "h1": candidate_id.hex + candidate_id.hex,
                "h2": "b" * 64,
                "h3": "c" * 64,
                "sv": world.version,
                "sd": uuid4(),
                "a": uuid4(),
                "sym": symbol,
                "at": created,
                "until": created + timedelta(hours=1),
                "key": f"create-{candidate_id}",
                "corr": uuid4(),
            },
        )
        transition = uuid4()
        session.execute(
            text(
                "INSERT INTO canonical_candidate_transitions (transition_id, organization_id, "
                "candidate_id, previous_state, new_state, transition_version, reason_codes, "
                "occurred_at, correlation_id, idempotency_key, content_hash) VALUES "
                "(:tid, :org, :id, 'published', 'expired', 2, '[]', :at, :corr, :key, :h)"
            ),
            {
                "tid": transition,
                "org": world.org,
                "id": candidate_id,
                "at": created,
                "corr": uuid4(),
                "key": f"t-{candidate_id}",
                "h": "d" * 64,
            },
        )
        session.execute(
            text("INSERT INTO canonical_candidate_creation_keys VALUES (:org, :key, :h, :id)"),
            {"org": world.org, "key": f"create-{candidate_id}", "h": "e" * 64, "id": candidate_id},
        )
        session.execute(
            text(
                "INSERT INTO canonical_candidate_transition_keys "
                "(organization_id, candidate_id, idempotency_key, transition_id) "
                "VALUES (:org, :id, :key, :tid)"
            ),
            {"org": world.org, "id": candidate_id, "key": f"t-{candidate_id}", "tid": transition},
        )
        session.execute(
            text(
                "INSERT INTO action_eligibility_evaluations (eligibility_id, organization_id, "
                "user_id, account_id, candidate_id, candidate_revision, uniqueness_hash, "
                "evaluation_revision, state, paper_actionable, live_executable, content_hash, "
                "payload, created_at) VALUES (:eid, :org, :user, :acct, :id, 1, :h, 1, "
                "'blocked', false, false, :h2, '{}', :at)"
            ),
            {
                "eid": uuid4(),
                "org": world.org,
                "user": world.user,
                "acct": uuid4(),
                "id": candidate_id,
                "h": candidate_id.hex + "0" * 32,
                "h2": "f" * 64,
                "at": created,
            },
        )
        session.commit()
    return candidate_id


def _setup(
    factory: sessionmaker[Session],
    world: World,
    *,
    state: str,
    candidate_id: UUID | None = None,
    journal_trade_id: UUID | None = None,
    expires_in: timedelta = timedelta(hours=-1),
    plan: dict[str, str] | None = None,
) -> UUID:
    setup_id = uuid4()
    payload: dict[str, object] = {"family": "sfp", "timeframe": "15m"}
    if plan is not None:
        payload["trade_plan"] = plan
    _exec(
        factory,
        "INSERT INTO strategy_brain_setups (id, organization_id, strategy_id, "
        "strategy_version_id, symbol, state, observed_at, expires_at, payload, candidate_id, "
        "journal_trade_id) VALUES (:id, :org, :s, :v, 'BTCUSDT', :state, :at, :exp, :p, :c, :j)",
        [
            {
                "id": setup_id,
                "org": world.org,
                "s": world.strategy,
                "v": world.version,
                "state": state,
                "at": NOW - timedelta(days=9),
                "exp": NOW + expires_in,
                "p": json.dumps(payload),
                "c": candidate_id,
                "j": journal_trade_id,
            }
        ],
    )
    return setup_id


def _event(
    factory: sessionmaker[Session], world: World, setup_id: UUID, kind: str, age: timedelta
) -> UUID:
    event_id = uuid4()
    _exec(
        factory,
        "INSERT INTO strategy_brain_setup_events VALUES (:id, :org, :setup, :kind, :at, '{}')",
        [
            {
                "id": event_id,
                "org": world.org,
                "setup": setup_id,
                "kind": kind,
                "at": NOW - age,
            }
        ],
    )
    return event_id


def _notification(
    factory: sessionmaker[Session], world: World, candidate_id: UUID | None, age: timedelta
) -> UUID:
    intent = uuid4()
    _exec(
        factory,
        "INSERT INTO telegram_paper_notifications (intent_id, identity_hash, organization_id, "
        "user_id, account_id, kind, resource_type, resource_id, content_hash, "
        "telegram_revision_id, candidate_id, text, created_at) VALUES (:id, :h, :org, :user, "
        ":acct, 'candidate', 'candidate', :res, :h, :rev, :cand, 'setup', :at)",
        [
            {
                "id": intent,
                "h": intent.hex + intent.hex,
                "org": world.org,
                "user": world.user,
                "acct": uuid4(),
                "res": candidate_id or uuid4(),
                "rev": uuid4(),
                "cand": candidate_id,
                "at": NOW - age,
            }
        ],
    )
    return intent


def _candles(factory: sessionmaker[Session], start: datetime, bars: list[tuple[str, str]]) -> None:
    """15m candles after ``start``: (high, low) per bar."""

    rows = []
    for index, (high, low) in enumerate(bars):
        opened = start + timedelta(minutes=15 * index)
        rows.append(
            {
                "id": uuid4(),
                "open_time": opened,
                "close_time": opened + timedelta(minutes=15),
                "high": Decimal(high),
                "low": Decimal(low),
            }
        )
    _exec(
        factory,
        "INSERT INTO historical_candles (id, symbol, exchange, timeframe, open_time, "
        "close_time, open, high, low, close, volume, source, is_stale, created_at, updated_at) "
        "VALUES (:id, 'BTCUSDT', 'binance', '15m', :open_time, :close_time, 100, :high, :low, "
        "100, 1, 'test', false, now(), now())",
        rows,
    )


def _summary(factory: sessionmaker[Session], candidate_id: UUID) -> TradeCandidateSummaryRow | None:
    with factory() as session:
        return session.get(TradeCandidateSummaryRow, candidate_id)


def test_old_missed_candidate_is_summarized_then_pruned_with_its_rows(pg_factory) -> None:
    world = World(pg_factory)
    missed = _candidate(pg_factory, world, age=timedelta(days=8))
    plan = {"entry": "100", "stop": "95", "take_profit": "110"}
    setup = _setup(pg_factory, world, state="EXPIRED", candidate_id=missed, plan=plan)
    notified = _notification(pg_factory, world, missed, timedelta(days=8))
    _candles(
        pg_factory,
        NOW - timedelta(days=8),
        [("104", "97"), ("111", "99"), ("103", "90")],  # target before stop
    )
    fresh = _candidate(pg_factory, world, age=timedelta(days=2))
    fresh_note = _notification(pg_factory, world, fresh, timedelta(days=2))

    results = {r.policy: r for r in run_retention(pg_factory, _settings(), now=NOW, dry_run=False)}

    assert results["canonical_candidates"].summarized == 1
    assert results["canonical_candidates"].deleted == 1
    summary = _summary(pg_factory, missed)
    assert summary is not None
    assert summary.organization_id == world.org
    assert summary.symbol == "BTCUSDT"
    assert summary.strategy_version_id == world.version
    assert summary.direction == "long"
    assert summary.brain_setup_id == setup
    assert summary.taken is False
    assert summary.journal_trade_id is None
    assert summary.notified is True
    assert (summary.planned_entry, summary.planned_stop, summary.planned_target) == (
        Decimal("100"),
        Decimal("95"),
        Decimal("110"),
    )
    assert (summary.outcome, summary.outcome_source) == ("target_first", "candles")
    # Full rows are gone; the candidate is summarized forever.
    for table in (
        "canonical_candidates",
        "canonical_candidate_transitions",
        "canonical_candidate_creation_keys",
        "canonical_candidate_transition_keys",
        "action_eligibility_evaluations",
    ):
        remaining = _ids(pg_factory, f"SELECT candidate_id FROM {table}")
        assert str(missed) not in remaining, table
        assert str(fresh) in remaining, table
    notes = _ids(pg_factory, "SELECT intent_id FROM telegram_paper_notifications")
    assert notes == {str(fresh_note)}
    assert str(notified) not in notes
    assert _summary(pg_factory, fresh) is None
    # The Brain setup projection is kept (strategy history).
    assert _ids(pg_factory, "SELECT id FROM strategy_brain_setups") == {str(setup)}


def test_taken_candidate_is_summarized_with_its_trade_and_never_pruned(pg_factory) -> None:
    world = World(pg_factory)
    taken = _candidate(pg_factory, world, age=timedelta(days=10))
    with pg_factory() as session:
        trade = JournalTrade(
            organization_id=world.org,
            user_id=world.user,
            source=JournalTradeSource.PAPER_EXECUTION,
            status=JournalTradeStatus.CLOSED,
            symbol="BTCUSDT",
            timeframe="15m",
            direction=TradeDirection.LONG,
            result=TradeResult.WIN,
            net_pnl=Decimal("12.5"),
            planned_entry_price=Decimal("100"),
            planned_stop_price=Decimal("95"),
            planned_targets=[{"price": "110"}],
            candidate_id=taken,
        )
        session.add(trade)
        session.commit()
        trade_id = trade.id
    _setup(pg_factory, world, state="COMPLETED", candidate_id=taken, journal_trade_id=trade_id)

    run_retention(pg_factory, _settings(), now=NOW, dry_run=False)

    summary = _summary(pg_factory, taken)
    assert summary is not None
    assert summary.taken is True
    assert summary.journal_trade_id == trade_id
    assert (summary.outcome, summary.outcome_source) == ("win", "journal_trade")
    assert summary.realized_net_pnl == Decimal("12.5")
    assert summary.planned_entry == Decimal("100")
    # Trade history: the candidate and its decisions stay.
    assert str(taken) in _ids(pg_factory, "SELECT candidate_id FROM canonical_candidates")
    assert str(taken) in _ids(pg_factory, "SELECT candidate_id FROM action_eligibility_evaluations")
    assert _count(pg_factory, "journal_trades") == 1


def test_dry_run_deletes_nothing_and_writes_no_summary(pg_factory) -> None:
    world = World(pg_factory)
    missed = _candidate(pg_factory, world, age=timedelta(days=8))
    _notification(pg_factory, world, missed, timedelta(days=8))
    _exec(
        pg_factory,
        "INSERT INTO audit_logs (id, actor, actor_type, action, resource_type, result, "
        "severity, redacted_metadata, event_at, created_at, updated_at) VALUES (:id, 'sys', "
        "'SYSTEM', 'BLOFIN_DEMO_SYNC', 'x', 'SUCCESS', 'INFO', '{}', :at, :at, :at)",
        [{"id": uuid4(), "at": NOW - timedelta(days=100)}],
    )

    results = {r.policy: r for r in run_retention(pg_factory, _settings(), now=NOW)}

    assert results["canonical_candidates"].matched == 1
    assert results["canonical_candidates"].summarized == 0
    assert results["audit_logs"].matched == 1
    assert all(r.deleted == 0 and r.dry_run for r in results.values())
    assert _count(pg_factory, "trade_candidate_summaries") == 0
    assert _count(pg_factory, "canonical_candidates") == 1
    assert _count(pg_factory, "telegram_paper_notifications") == 1
    assert _count(pg_factory, "audit_logs") == 1


def test_audit_logs_keep_90_days_and_referenced_rows(pg_factory) -> None:
    rows = [
        {"id": uuid4(), "at": NOW - timedelta(days=91)},
        {"id": uuid4(), "at": NOW - timedelta(days=89)},
    ]
    _exec(
        pg_factory,
        "INSERT INTO audit_logs (id, actor, actor_type, action, resource_type, result, "
        "severity, redacted_metadata, event_at, created_at, updated_at) VALUES (:id, 'sys', "
        "'SYSTEM', 'BLOFIN_DEMO_SYNC', 'x', 'SUCCESS', 'INFO', '{}', :at, :at, :at)",
        rows,
    )

    run_retention(pg_factory, _settings(), now=NOW, dry_run=False)

    assert _ids(pg_factory, "SELECT id FROM audit_logs") == {str(rows[1]["id"])}


def test_audit_log_referenced_by_an_approval_outlives_the_window(pg_factory) -> None:
    # Griller PR 466 round 2, C2: the approvals keep-guard must hold on its own.
    world = World(pg_factory)
    referenced, unreferenced = uuid4(), uuid4()
    _exec(
        pg_factory,
        "INSERT INTO audit_logs (id, actor, actor_type, action, resource_type, result, "
        "severity, redacted_metadata, event_at, created_at, updated_at) VALUES (:id, 'sys', "
        "'SYSTEM', 'BLOFIN_DEMO_SYNC', 'x', 'SUCCESS', 'INFO', '{}', :at, :at, :at)",
        [
            {"id": referenced, "at": NOW - timedelta(days=200)},
            {"id": unreferenced, "at": NOW - timedelta(days=200)},
        ],
    )
    with pg_factory() as session:
        proposal = TradeProposal(
            organization_id=world.org,
            user_id=world.user,
            strategy_id=StrategyId.HTF_TREND_PULLBACK,
            symbol="BTCUSDT",
            timeframe="4h",
            direction=TradeDirection.LONG,
            entry_price=Decimal("60000"),
            position_size=Decimal("0.1"),
            leverage=Decimal("3"),
            stop_loss=Decimal("58000"),
            invalidation="close below 58000",
            confidence=0.7,
            risk_level=RiskSeverity.MEDIUM,
            rationale="retention fixture",
        )
        session.add(proposal)
        session.flush()
        session.add(
            ApprovalRequest(
                proposal_id=proposal.id,
                organization_id=world.org,
                user_id=world.user,
                risk_level=RiskSeverity.MEDIUM,
                confidence=0.7,
                audit_event_id=referenced,
            )
        )
        session.commit()

    run_retention(pg_factory, _settings(), now=NOW, dry_run=False)

    assert _ids(pg_factory, "SELECT id FROM audit_logs") == {str(referenced)}


def test_24_hour_policies_prune_noise_and_keep_decisions(pg_factory) -> None:
    world = World(pg_factory)
    old, fresh = timedelta(hours=30), timedelta(hours=2)
    done = _setup(pg_factory, world, state="EXPIRED")
    live = _setup(pg_factory, world, state="FORMING", expires_in=timedelta(hours=6))
    obs_done = _event(pg_factory, world, done, "system_observation", old)
    obs_live = _event(pg_factory, world, live, "system_observation", old)
    obs_new = _event(pg_factory, world, done, "system_observation", fresh)
    keep = {
        _event(pg_factory, world, done, kind, timedelta(days=400))
        for kind in ("paper_decision", "paper_trade_opened", "paper_trade_closed")
    }
    # Watcher events: errors no longer outlive the 24-hour window.
    _exec(
        pg_factory,
        "INSERT INTO watcher_observability_events (event_id, name, at, organization_id, fields) "
        "VALUES (:id, :name, :at, :org, '{}')",
        [
            {"id": uuid4(), "name": "watcher_scan_failed", "at": NOW - old, "org": world.org},
            {"id": uuid4(), "name": "watcher_scan_scheduled", "at": NOW - old, "org": world.org},
            {"id": uuid4(), "name": "watcher_scan_scheduled", "at": NOW - fresh, "org": world.org},
        ],
    )
    # Paper evaluation: scan noise 24 h, candidate stage 7 days, trade-linked forever.
    evaluations = [
        ("scan-old", "watcher_scan", old, None),
        ("scan-new", "watcher_scan", fresh, None),
        ("cand-2d", "candidate", timedelta(days=2), None),
        ("cand-8d", "candidate", timedelta(days=8), None),
        ("decision-old", "paper_decision", timedelta(days=400), None),
        ("trade-old", "candidate", timedelta(days=400), uuid4()),
    ]
    _exec(
        pg_factory,
        "INSERT INTO paper_evaluation_observations (observation_id, organization_id, stage, "
        "source_system, source_event_id, source_event_version, occurred_at, journal_trade_id, "
        "data_quality, replayed, plan_approved, rejected, skipped, filled, closed, "
        "executed_outcome, learning_venue_mode, live_executable, content_hash, payload, "
        "created_at, updated_at) VALUES (:id, :org, :stage, 'test', :src, 1, :at, :trade, "
        "'available', false, false, false, false, false, false, false, 'paper_internal', false, "
        ":h, '{}', :at, :at)",
        [
            {
                "id": uuid4(),
                "org": world.org,
                "stage": stage,
                "src": name,
                "at": NOW - age,
                "trade": trade,
                "h": "a" * 64,
            }
            for name, stage, age, trade in evaluations
        ],
    )
    # Public market receipts: only bars older than the SFP read horizon go.
    _exec(
        pg_factory,
        "INSERT INTO public_market_observations (identity_hash, observation_id, payload, "
        "bar_start) VALUES (:h, :id, '{}', :at)",
        [
            {"h": "a" * 64, "id": uuid4(), "at": NOW - timedelta(days=3)},
            {"h": "a" * 64, "id": uuid4(), "at": NOW - timedelta(hours=20)},
            {"h": "a" * 64, "id": uuid4(), "at": NOW - timedelta(hours=1)},
        ],
    )

    run_retention(pg_factory, _settings(), now=NOW, dry_run=False)

    events = _ids(pg_factory, "SELECT id FROM strategy_brain_setup_events")
    assert str(obs_done) not in events
    assert {str(obs_live), str(obs_new)} | {str(k) for k in keep} == events
    assert _count(pg_factory, "watcher_observability_events") == 1
    assert _ids(pg_factory, "SELECT source_event_id FROM paper_evaluation_observations") == {
        "scan-new",
        "cand-2d",
        "decision-old",
        "trade-old",
    }
    # 15m SFP horizon is 85 bars (21h15m) < 24 h, so the 24-hour window governs.
    assert _count(pg_factory, "public_market_observations") == 2


def test_public_market_window_widens_to_the_longest_sfp_read_horizon(pg_factory) -> None:
    World(pg_factory, timeframe="1h")  # 85 bars of 1h = 85 hours of history are re-read
    _exec(
        pg_factory,
        "INSERT INTO public_market_observations (identity_hash, observation_id, payload, "
        "bar_start) VALUES (:h, :id, '{}', :at)",
        [
            {"h": "a" * 64, "id": uuid4(), "at": NOW - timedelta(hours=90)},
            {"h": "a" * 64, "id": uuid4(), "at": NOW - timedelta(hours=60)},
        ],
    )

    results = {r.policy: r for r in run_retention(pg_factory, _settings(), now=NOW, dry_run=False)}

    assert results["public_market_observations"].cutoff <= NOW - timedelta(hours=85)
    assert _count(pg_factory, "public_market_observations") == 1


def _seed_blofin_snapshots(pg_factory) -> dict[str, dict[str, object]]:
    world_a, world_b, world_c = World(pg_factory), World(pg_factory), World(pg_factory)

    def snap(
        world: World, at: datetime, health: str = "ok", user: UUID | None = None
    ) -> dict[str, object]:
        return {
            "id": uuid4(),
            "org": world.org,
            "user": user,
            "at": at,
            "health": health,
            "prov": json.dumps({"connection_binding": "b1"}),
        }

    day5 = NOW - timedelta(days=5)
    rows = {
        "a-40d-1": snap(world_a, NOW - timedelta(days=40, hours=2)),
        "a-40d-2": snap(world_a, NOW - timedelta(days=40)),
        "a-5d-early": snap(world_a, day5.replace(hour=1)),
        "a-5d-mid": snap(world_a, day5.replace(hour=8)),
        "a-5d-late": snap(world_a, day5.replace(hour=20)),
        # Yesterday (UTC): one intra-day row older than 24h, one younger.
        "a-1d-old": snap(world_a, NOW - timedelta(days=1, hours=1)),
        "a-1d-recent": snap(world_a, NOW - timedelta(hours=20)),
        "a-1d-late": snap(world_a, NOW - timedelta(hours=10)),
        "a-now-down": snap(world_a, NOW - timedelta(minutes=5), "unavailable"),
        "b-only": snap(world_b, NOW - timedelta(days=60)),
        # Per-user latest readers: a user's only row survives other users' newer rows.
        "a-user-old": snap(world_a, NOW - timedelta(days=50), user=world_a.user),
        # Newest successful row is kept although it is an older intra-day row.
        "c-ok": snap(world_c, (NOW - timedelta(days=3)).replace(hour=1)),
        "c-down": snap(world_c, (NOW - timedelta(days=3)).replace(hour=5), "unavailable"),
        "c-now-down": snap(world_c, NOW - timedelta(minutes=5), "unavailable"),
    }
    _exec(
        pg_factory,
        "INSERT INTO blofin_demo_sync_snapshots (id, organization_id, user_id, synced_at, "
        "health_status, provider, exchange_mode, account_snapshot, positions_snapshot, "
        "market_context, provenance, is_stale, position_count, balance_count, created_at, "
        "updated_at) VALUES (:id, :org, :user, :at, :health, 'blofin_demo', 'demo', '{}', '{}', "
        "'{}', :prov, false, 0, 0, :at, :at)",
        list(rows.values()),
    )
    return rows


_BLOFIN_ALWAYS_KEPT = {
    "a-5d-late",
    "a-1d-recent",
    "a-1d-late",
    "a-now-down",
    "b-only",
    "a-user-old",
    "c-ok",
    "c-down",
    "c-now-down",
}


def test_blofin_snapshots_keep_one_per_utc_day_forever(pg_factory) -> None:
    rows = _seed_blofin_snapshots(pg_factory)

    run_retention(pg_factory, _settings(), now=NOW, dry_run=False)

    kept = _ids(pg_factory, "SELECT id FROM blofin_demo_sync_snapshots")
    # The 40-day-old daily row survives; intra-day rows older than 24h go.
    expected = _BLOFIN_ALWAYS_KEPT | {"a-40d-2"}
    assert kept == {str(rows[name]["id"]) for name in expected}


def test_blofin_snapshots_optional_horizon_drops_old_daily_rows(pg_factory) -> None:
    rows = _seed_blofin_snapshots(pg_factory)

    run_retention(pg_factory, _settings(retention_demo_snapshot_days=30), now=NOW, dry_run=False)

    kept = _ids(pg_factory, "SELECT id FROM blofin_demo_sync_snapshots")
    assert kept == {str(rows[name]["id"]) for name in _BLOFIN_ALWAYS_KEPT}


def test_keep_forever_tables_are_never_named_by_any_policy() -> None:
    from app.services.db_retention import KEEP_FOREVER_TABLES, pruned_tables

    pruned = pruned_tables(_settings())
    assert pruned.isdisjoint(KEEP_FOREVER_TABLES)
    assert {
        "strategy_brain_setups",
        "user_strategies",
        "user_strategy_versions",
        "setup_definitions",
        "compiled_setup_definitions",
        "conversations",
        "conversation_messages",
        "journal_trades",
        "journals",
        "paper_trades",
        "paper_trade_events",
        "orders",
        "exchange_fills",
        "trade_plan_revisions",
        "canonical_trade_plan_roots",
        "canonical_trade_plan_lineage",
        "documents",
        "chunks",
        "organizations",
        "users",
        "refresh_tokens",
        "trade_candidate_summaries",
        "telegram_security_outbox",
    } <= KEEP_FOREVER_TABLES


def test_vacuum_runs_only_on_retention_tables_and_reclaims(pg_factory) -> None:
    from app.services.db_retention import tables_with_deletes, vacuum_tables

    _exec(
        pg_factory,
        "INSERT INTO audit_logs (id, actor, actor_type, action, resource_type, result, "
        "severity, redacted_metadata, event_at, created_at, updated_at) VALUES (:id, 'sys', "
        "'SYSTEM', 'BLOFIN_DEMO_SYNC', 'x', 'SUCCESS', 'INFO', '{}', :at, :at, :at)",
        [{"id": uuid4(), "at": NOW - timedelta(days=200)} for _ in range(20)],
    )
    settings = _settings()
    results = run_retention(pg_factory, settings, now=NOW, dry_run=False)
    tables = tables_with_deletes(results)
    assert tables == ("audit_logs",)
    assert vacuum_tables(pg_factory, settings, tables) == {"audit_logs": "ok"}
    assert vacuum_tables(pg_factory, settings, tables, full=True) == {"audit_logs": "ok"}
    with pg_factory() as session:
        vacuumed = session.execute(
            text(
                "SELECT last_vacuum IS NOT NULL FROM pg_stat_user_tables "
                "WHERE relname = 'audit_logs'"
            )
        ).scalar_one()
    assert vacuumed is True
    with pytest.raises(ValueError, match="not a retention table"):
        vacuum_tables(pg_factory, settings, ("journal_trades",))


def test_worker_pass_vacuums_after_deleting_when_enabled(pg_factory, monkeypatch) -> None:
    from app.workers.db_retention import DbRetentionCycle

    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        "app.services.db_retention.vacuum_tables",
        lambda factory, settings, tables, full=False: calls.append(tuple(tables)) or {},
    )
    _exec(
        pg_factory,
        "INSERT INTO audit_logs (id, actor, actor_type, action, resource_type, result, "
        "severity, redacted_metadata, event_at, created_at, updated_at) VALUES (:id, 'sys', "
        "'SYSTEM', 'BLOFIN_DEMO_SYNC', 'x', 'SUCCESS', 'INFO', '{}', :at, :at, :at)",
        [{"id": uuid4(), "at": NOW - timedelta(days=200)}],
    )
    settings = _settings(
        retention_enabled=True, retention_dry_run=False, retention_vacuum_analyze=True
    )
    outcome = DbRetentionCycle(settings, session_factory=pg_factory, now=lambda: NOW)()
    assert outcome.status == "pruned"
    assert calls == [("audit_logs",)]


def test_taken_summary_picks_up_the_result_once_the_trade_closes(pg_factory) -> None:
    world = World(pg_factory)
    taken = _candidate(pg_factory, world, age=timedelta(days=9))
    with pg_factory() as session:
        trade = JournalTrade(
            organization_id=world.org,
            user_id=world.user,
            source=JournalTradeSource.PAPER_EXECUTION,
            status=JournalTradeStatus.OPEN,
            symbol="BTCUSDT",
            timeframe="15m",
            direction=TradeDirection.LONG,
            result=TradeResult.OPEN,
            candidate_id=taken,
        )
        session.add(trade)
        session.commit()
        trade_id = trade.id

    run_retention(pg_factory, _settings(), now=NOW, dry_run=False)
    summary = _summary(pg_factory, taken)
    assert summary is not None
    assert (summary.taken, summary.outcome, summary.outcome_source) == (True, None, "trade_open")

    with pg_factory() as session:
        closed = session.get(JournalTrade, trade_id)
        assert closed is not None
        closed.status = JournalTradeStatus.CLOSED
        closed.result = TradeResult.LOSS
        closed.net_pnl = Decimal("-4")
        session.commit()
    run_retention(pg_factory, _settings(), now=NOW + timedelta(days=1), dry_run=False)
    summary = _summary(pg_factory, taken)
    assert summary is not None
    assert (summary.outcome, summary.outcome_source) == ("loss", "journal_trade")
    assert summary.realized_net_pnl == Decimal("-4")
