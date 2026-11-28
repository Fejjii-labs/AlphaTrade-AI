"""The worker reports which strategy families and timeframes it trades."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import ExchangeMode, Settings
from app.db.worker_runtime_state import WORKER_EFFECTIVE_COMPONENT, WorkerRuntimeStateRow
from app.persistence.worker_runtime_state import (
    WorkerRuntimeWrite,
    load_worker_trading_scope,
    project_worker_trading_scope,
    publish_worker_runtime_state,
)
from app.services.worker_trading_scope import trading_scope_tokens

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def staging_scope_settings(**overrides: object) -> Settings:
    """The staging demo shape: D-line and Nested on 1h, SFP paused."""

    values: dict[str, object] = {
        "governed_blofin_demo_enabled": True,
        "governed_demo_data_mode_enabled": True,
        "risk_model_v2_enabled": True,
        "governed_demo_partial_exits_enabled": True,
        "exchange_mode": ExchangeMode.PAPER_EXCHANGE_DEMO,
        "enable_real_trading": False,
        "real_trading_enabled": False,
        "governed_demo_dline_timeframes": ["1h"],
        "governed_demo_nested_timeframes": ["1h"],
        "governed_demo_sfp_enabled": False,
    }
    values.update(overrides)
    return Settings(environment="local").model_copy(update=values)


def _factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    WorkerRuntimeStateRow.__table__.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _write(at: datetime, scope: tuple[str, ...] | None) -> WorkerRuntimeWrite:
    return WorkerRuntimeWrite(
        worker_id="paper-worker",
        heartbeat_at=at,
        autonomous_internal_paper_env_ceiling=False,
        watcher_orchestration_enabled=False,
        watcher_paper_staging_activation=False,
        telegram_paper_activation_armed=False,
        telegram_interaction_enabled=False,
        telegram_alerts_enabled=False,
        automatic_telegram_delivery_enabled=False,
        preserve_cycle=False,
        watchlist=("BTCUSDT",),
        trading_scope=scope,
    )


def test_tokens_follow_the_worker_demo_settings() -> None:
    assert trading_scope_tokens(staging_scope_settings()) == ("d_line:1h", "nested:1h", "sfp:off")
    assert trading_scope_tokens(staging_scope_settings(governed_blofin_demo_enabled=False)) == ()
    # D-line needs demo data mode and demo partial exits, as the demo loop refuses otherwise.
    for flag in ("governed_demo_partial_exits_enabled", "governed_demo_data_mode_enabled"):
        tokens = trading_scope_tokens(staging_scope_settings(**{flag: False}))
        assert tokens[0] == "d_line:off"
    wide = staging_scope_settings(
        governed_demo_dline_timeframes=["30m", "1h", "1h", "bogus"],
        governed_demo_nested_timeframes=[],
        governed_demo_sfp_enabled=True,
    )
    assert trading_scope_tokens(wide) == ("d_line:30m", "d_line:1h", "nested:off", "sfp:any")


def test_scope_row_is_published_with_the_heartbeat_and_projected_when_fresh() -> None:
    factory = _factory()
    publish_worker_runtime_state(
        factory, _write(NOW, ("d_line:1h", "nested:1h", "sfp:off", "nope", "x:1h"))
    )
    with factory() as session:
        effective = session.get(WorkerRuntimeStateRow, WORKER_EFFECTIVE_COMPONENT)
        assert effective is not None and effective.watchlist == ["BTCUSDT"]
        scope = load_worker_trading_scope(session, now=NOW, stale_after_seconds=90)
    assert scope is not None
    assert scope.reported_at == NOW
    assert [f.model_dump() for f in scope.families] == [
        {"family": "d_line", "trading": True, "timeframes": ["1h"]},
        {"family": "nested", "trading": True, "timeframes": ["1h"]},
        {"family": "sfp", "trading": False, "timeframes": []},
    ]
    with factory() as session:
        old = load_worker_trading_scope(
            session, now=NOW + timedelta(seconds=91), stale_after_seconds=90
        )
    assert old is None


def test_cycle_publish_keeps_the_scope_and_an_empty_scope_is_unknown() -> None:
    factory = _factory()
    publish_worker_runtime_state(factory, _write(NOW, ("nested:any",)))
    later = NOW + timedelta(seconds=10)
    # The watcher cycle publishes without a scope: the scope row is left alone.
    publish_worker_runtime_state(factory, _write(later, None))
    with factory() as session:
        scope = load_worker_trading_scope(session, now=later, stale_after_seconds=90)
    assert scope is not None
    assert scope.reported_at == NOW
    assert scope.families[0].timeframes is None
    publish_worker_runtime_state(factory, _write(later, ()))
    with factory() as session:
        assert load_worker_trading_scope(session, now=later, stale_after_seconds=90) is None


def test_projection_refuses_missing_naive_or_malformed_rows() -> None:
    assert project_worker_trading_scope(None, now=NOW, stale_after_seconds=90) is None
    future = SimpleNamespace(heartbeat_at=NOW + timedelta(minutes=5), watchlist=["sfp:any"])
    assert project_worker_trading_scope(future, now=NOW, stale_after_seconds=90) is None
    junk = SimpleNamespace(heartbeat_at=NOW, watchlist="d_line:1h")
    assert project_worker_trading_scope(junk, now=NOW, stale_after_seconds=90) is None
    both = SimpleNamespace(heartbeat_at=NOW, watchlist=["d_line:1h", "d_line:off"])
    projected = project_worker_trading_scope(both, now=NOW, stale_after_seconds=90)
    assert projected is not None
    assert projected.families[0].trading is False
