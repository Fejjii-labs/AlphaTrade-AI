"""Cross-instance Binance gate: shared deadline (max wins) and the startup lease.

PostgreSQL tests run on an isolated schema migrated by alembic (a24binancegate001).
Coordinator logic tests use SQLite and a fake clock.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool
from structlog.testing import capture_logs

from app.core.config import Settings
from app.db.base import Base
from app.db.binance_rest_coordination import BinanceRestCoordinationRow
from app.market_contracts.adapters.binance_rest_gate import BinanceRestGate
from app.persistence.binance_rest_coordination import (
    load_gate_deadline,
    record_gate_deadline,
    release_market_lease,
    try_acquire_market_lease,
)
from app.workers.binance_gate_coordination import (
    BinanceGateCoordinator,
    start_binance_gate_coordination,
)
from tests.support.alembic_heads import (
    alembic_ancestry,
    alembic_script_directory,
    single_alembic_head,
)
from tests.support.postgres_persistence import POSTGRES_URL, requires_postgres

T0 = datetime(2026, 11, 4, 11, 17, tzinfo=UTC)
_A23 = "a23marketmonitor001"
_A24 = "a24binancegate001"


def _alembic_config() -> Config:
    config = Config()
    root = Path(__file__).resolve().parents[1]
    config.set_main_option("script_location", str(root / "src/app/db/migrations"))
    return config


@pytest.fixture(scope="module")
def pg_factory() -> Iterator[sessionmaker[Session]]:
    """Fresh schema migrated to head; dropped afterwards."""

    admin = create_engine(POSTGRES_URL, poolclass=NullPool, future=True)
    schema = "binance_gate_" + uuid4().hex
    isolated = make_url(POSTGRES_URL).update_query_dict({"options": f"-csearch_path={schema}"})
    patch = pytest.MonkeyPatch()
    patch.setenv("ALEMBIC_DATABASE_URL", isolated.render_as_string(hide_password=False))
    engine = create_engine(isolated, poolclass=NullPool, future=True)
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        command.upgrade(_alembic_config(), "head")
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        patch.undo()
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def pg(pg_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    with pg_factory() as session, session.begin():
        session.query(BinanceRestCoordinationRow).delete()
    return pg_factory


@pytest.fixture
def lite() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[BinanceRestCoordinationRow.__table__])
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_a24_is_on_the_single_head_and_revises_a23() -> None:
    script = alembic_script_directory()
    assert _A24 in alembic_ancestry(single_alembic_head(script))
    assert script.get_revision(_A24).down_revision == _A23


@requires_postgres
def test_a24_upgrade_downgrade_on_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    admin = create_engine(POSTGRES_URL, poolclass=NullPool, future=True)
    schema = "binance_gate_mig_" + uuid4().hex
    isolated = make_url(POSTGRES_URL).update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", isolated.render_as_string(hide_password=False))
    engine = create_engine(isolated, poolclass=NullPool, future=True)
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        command.upgrade(_alembic_config(), "head")
        assert "binance_rest_coordination" in inspect(engine).get_table_names()
        command.downgrade(_alembic_config(), _A23)
        assert "binance_rest_coordination" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


# --- store: shared deadline ---------------------------------------------------


@requires_postgres
def test_pg_deadline_keeps_the_max_and_never_downgrades_a_live_ban(
    pg: sessionmaker[Session],
) -> None:
    late = T0 + timedelta(minutes=18)
    early = T0 + timedelta(minutes=2)
    stored = record_gate_deadline(pg, kind="ban", until=late, writer="a", now=T0)
    assert (stored.kind, stored.until) == ("ban", late)
    # A shorter 429 from another instance neither shortens nor downgrades it.
    stored = record_gate_deadline(pg, kind="rate_limit", until=early, writer="b", now=T0)
    assert (stored.kind, stored.until) == ("ban", late)
    # A later 429 extends the deadline; the live ban kind stays.
    later = late + timedelta(minutes=1)
    stored = record_gate_deadline(pg, kind="rate_limit", until=later, writer="b", now=T0)
    assert (stored.kind, stored.until) == ("ban", later)
    loaded = load_gate_deadline(pg, now=T0)
    assert loaded is not None and (loaded.kind, loaded.until) == ("ban", later)
    with pg() as session:
        row = session.get(BinanceRestCoordinationRow, "binance_usdm")
        assert row is not None and row.gate_status_code == 429 and row.gate_updated_by == "b"


@requires_postgres
def test_pg_expired_deadline_is_replaced_and_not_loaded(pg: sessionmaker[Session]) -> None:
    record_gate_deadline(pg, kind="ban", until=T0 + timedelta(minutes=5), writer="a", now=T0)
    after = T0 + timedelta(minutes=6)
    assert load_gate_deadline(pg, now=after) is None
    stored = record_gate_deadline(
        pg, kind="rate_limit", until=after + timedelta(seconds=30), writer="a", now=after
    )
    assert stored.kind == "rate_limit"


@requires_postgres
def test_pg_concurrent_first_writers_do_not_conflict(pg: sessionmaker[Session]) -> None:
    errors: list[BaseException] = []
    start = threading.Barrier(4)

    def write(i: int) -> None:
        try:
            start.wait()
            record_gate_deadline(
                pg, kind="rate_limit", until=T0 + timedelta(seconds=10 + i), writer=str(i), now=T0
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert errors == []
    loaded = load_gate_deadline(pg, now=T0)
    assert loaded is not None and loaded.until == T0 + timedelta(seconds=13)


# --- store: lease ---------------------------------------------------------------


@requires_postgres
def test_pg_lease_blocks_until_expiry_or_release(pg: sessionmaker[Session]) -> None:
    assert try_acquire_market_lease(pg, owner="old", now=T0, ttl_seconds=90).acquired
    blocked = try_acquire_market_lease(pg, owner="new", now=T0, ttl_seconds=90)
    assert not blocked.acquired and blocked.holder == "old"
    assert blocked.expires_at == T0 + timedelta(seconds=90)
    # Renewal by the holder extends it.
    assert try_acquire_market_lease(
        pg, owner="old", now=T0 + timedelta(seconds=60), ttl_seconds=90
    ).acquired
    assert not try_acquire_market_lease(
        pg, owner="new", now=T0 + timedelta(seconds=149), ttl_seconds=90
    ).acquired
    # Exactly at expiry the lease is free.
    assert try_acquire_market_lease(
        pg, owner="new", now=T0 + timedelta(seconds=150), ttl_seconds=90
    ).acquired


@requires_postgres
def _db_now(factory: sessionmaker[Session]) -> datetime:
    with factory() as session:
        return session.execute(text("SELECT now()")).scalar_one()


@requires_postgres
def test_pg_lease_without_now_uses_the_database_clock(pg: sessionmaker[Session]) -> None:
    before = _db_now(pg)
    claim = try_acquire_market_lease(pg, owner="old", ttl_seconds=90)
    after = _db_now(pg)
    assert claim.acquired and claim.expires_at is not None
    assert before + timedelta(seconds=90) <= claim.expires_at <= after + timedelta(seconds=90)
    assert not try_acquire_market_lease(pg, owner="new", ttl_seconds=90).acquired
    # Expired by the database clock: reclaimable without any app clock.
    with pg() as session, session.begin():
        session.execute(
            text(
                "UPDATE binance_rest_coordination"
                " SET lease_expires_at = now() - interval '1 second'"
            )
        )
    assert try_acquire_market_lease(pg, owner="new", ttl_seconds=90).acquired


@requires_postgres
def test_pg_coordinator_lease_ignores_a_skewed_process_clock(
    pg: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workers import binance_gate_coordination

    # A process clock ten minutes fast must not stretch the lease by ten minutes.
    skewed = datetime.now(UTC) + timedelta(minutes=10)
    monkeypatch.setattr(binance_gate_coordination, "_utc_now", lambda: skewed)
    coordinator = BinanceGateCoordinator(
        BinanceRestGate(rng=lambda: 0.0), pg, owner="skewed", lease_ttl_seconds=90
    )
    coordinator._step_lease(starting=True)
    assert coordinator.lease_held
    with pg() as session:
        row = session.get(BinanceRestCoordinationRow, "binance_usdm")
        assert row is not None and row.lease_expires_at is not None
        expires = row.lease_expires_at
    db_now = _db_now(pg)
    assert db_now + timedelta(seconds=80) <= expires <= db_now + timedelta(seconds=90)


def test_pg_release_is_owner_checked(pg: sessionmaker[Session]) -> None:
    assert try_acquire_market_lease(pg, owner="old", now=T0, ttl_seconds=90).acquired
    assert not release_market_lease(pg, owner="intruder")
    assert not try_acquire_market_lease(pg, owner="new", now=T0, ttl_seconds=90).acquired
    assert release_market_lease(pg, owner="old")
    assert try_acquire_market_lease(pg, owner="new", now=T0, ttl_seconds=90).acquired


# --- coordinator ------------------------------------------------------------------


class Mono:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Wall:
    def __init__(self) -> None:
        self.at = T0

    def __call__(self) -> datetime:
        return self.at


def _gate(mono: Mono, wall: Wall) -> BinanceRestGate:
    return BinanceRestGate(
        clock=mono, wall_clock=lambda: wall.at.timestamp(), rng=lambda: 0.0, boot_ramp_seconds=0.0
    )


def _coordinator(
    factory: sessionmaker[Session],
    owner: str,
    mono: Mono,
    wall: Wall,
    *,
    gate: BinanceRestGate | None = None,
    max_wait: float = 240.0,
) -> tuple[BinanceGateCoordinator, BinanceRestGate]:
    gate = gate or _gate(mono, wall)
    coordinator = BinanceGateCoordinator(
        gate,
        factory,
        owner=owner,
        lease_ttl_seconds=90,
        heartbeat_seconds=20,
        max_wait_seconds=max_wait,
        now=wall,
        monotonic=mono,
    )
    return coordinator, gate


def _start_without_thread(coordinator: BinanceGateCoordinator) -> None:
    """``start`` minus the heartbeat thread: tests drive ``run_once``."""

    coordinator._gate.set_failure_listener(coordinator._on_gate_failure)
    coordinator._adopt_shared_deadline()
    coordinator._step_lease(starting=True)


def test_new_instance_waits_in_standby_until_old_releases(lite: sessionmaker[Session]) -> None:
    mono, wall = Mono(), Wall()
    old, old_gate = _coordinator(lite, "old", mono, wall)
    _start_without_thread(old)
    assert old.lease_held and old_gate.remaining() is None

    new, new_gate = _coordinator(lite, "new", mono, wall)
    _start_without_thread(new)
    assert not new.lease_held
    assert new_gate.remaining() == ("rate_limit", pytest.approx(240.0))

    mono.t += 10
    wall.at += timedelta(seconds=10)
    new.run_once()
    assert new_gate.holding  # old still holds the lease

    old.stop()  # SIGTERM path: no thread to join, releases the lease
    new.run_once()
    assert new.lease_held
    assert not new_gate.holding and new_gate.remaining() is None


def test_new_instance_takes_over_when_old_lease_expires(lite: sessionmaker[Session]) -> None:
    mono, wall = Mono(), Wall()
    old, _ = _coordinator(lite, "old", mono, wall)
    _start_without_thread(old)  # then crashes: no release
    new, new_gate = _coordinator(lite, "new", mono, wall)
    _start_without_thread(new)
    mono.t += 89
    wall.at += timedelta(seconds=89)
    new.run_once()
    assert new_gate.holding
    mono.t += 1
    wall.at += timedelta(seconds=1)
    new.run_once()
    assert new.lease_held and not new_gate.holding


def test_standby_is_bounded_by_max_wait(lite: sessionmaker[Session]) -> None:
    mono, wall = Mono(), Wall()
    old, _ = _coordinator(lite, "old", mono, wall)
    _start_without_thread(old)
    new, new_gate = _coordinator(lite, "new", mono, wall, max_wait=60.0)
    _start_without_thread(new)
    mono.t += 59.9
    assert new_gate.holding
    mono.t += 0.1
    wall.at += timedelta(seconds=60)
    old.run_once()  # old keeps renewing
    new.run_once()
    assert not new.lease_held
    assert not new_gate.holding and new_gate.remaining() is None


def test_zero_max_wait_never_holds(lite: sessionmaker[Session]) -> None:
    mono, wall = Mono(), Wall()
    old, _ = _coordinator(lite, "old", mono, wall)
    _start_without_thread(old)
    new, new_gate = _coordinator(lite, "new", mono, wall, max_wait=0.0)
    _start_without_thread(new)
    assert new_gate.remaining() is None


def test_ban_is_shared_with_a_new_instance_at_startup(lite: sessionmaker[Session]) -> None:
    mono, wall = Mono(), Wall()
    a, gate_a = _coordinator(lite, "a", mono, wall)
    _start_without_thread(a)
    seconds = gate_a.note_failure("ban", 1080.0)  # 18 min ban
    a.run_once()  # flushes the pending deadline
    b, gate_b = _coordinator(lite, "b", Mono(), wall)
    b._step_lease = lambda *, starting: None  # type: ignore[method-assign]
    _start_without_thread(b)
    closed = gate_b.remaining()
    assert closed is not None and closed[0] == "ban"
    assert closed[1] == pytest.approx(seconds, abs=1.0)
    assert gate_b.strikes == 0


def test_running_instance_adopts_peer_deadline_on_heartbeat(lite: sessionmaker[Session]) -> None:
    mono, wall = Mono(), Wall()
    a, gate_a = _coordinator(lite, "a", mono, wall)
    b, gate_b = _coordinator(lite, "b", mono, wall)
    _start_without_thread(a)
    _start_without_thread(b)
    gate_b.release_hold()
    gate_a.note_failure("rate_limit", 30.0)
    a.run_once()
    assert gate_b.remaining() is None
    b.run_once()
    assert gate_b.remaining() == ("rate_limit", pytest.approx(30.0))


def test_stop_flushes_a_pending_deadline_for_the_successor(lite: sessionmaker[Session]) -> None:
    mono, wall = Mono(), Wall()
    a, gate_a = _coordinator(lite, "a", mono, wall)
    _start_without_thread(a)
    gate_a.note_failure("ban", 600.0)
    a.stop()
    loaded = load_gate_deadline(lite, now=wall.at)
    assert loaded is not None and loaded.kind == "ban"
    assert not a.lease_held


def test_pending_deadline_keeps_max_and_ban(lite: sessionmaker[Session]) -> None:
    mono, wall = Mono(), Wall()
    a, _ = _coordinator(lite, "a", mono, wall)
    a._on_gate_failure("ban", 2000.0)
    a._on_gate_failure("rate_limit", 1500.0)
    assert a._pending == (2000.0, "ban")
    a._on_gate_failure("rate_limit", 2500.0)
    assert a._pending == (2500.0, "ban")


class BrokenFactory:
    def __call__(self) -> Session:
        raise RuntimeError("database unavailable")


def test_database_failures_fail_open_and_never_raise() -> None:
    mono, wall = Mono(), Wall()
    coordinator, gate = _coordinator(BrokenFactory(), "a", mono, wall)  # type: ignore[arg-type]
    coordinator.start()
    try:
        assert gate.remaining() is None  # no standby without a lease store
        gate.note_failure("rate_limit", 5.0)
        coordinator.run_once()  # write fails, stays pending, no raise
        assert coordinator._pending is not None
    finally:
        coordinator.stop()
    assert gate.remaining() == ("rate_limit", pytest.approx(5.0))


def test_db_failure_during_standby_releases_the_hold(lite: sessionmaker[Session]) -> None:
    mono, wall = Mono(), Wall()
    old, _ = _coordinator(lite, "old", mono, wall)
    _start_without_thread(old)
    new, new_gate = _coordinator(lite, "new", mono, wall)
    _start_without_thread(new)
    assert new_gate.holding
    new._factory = BrokenFactory()  # type: ignore[assignment]
    new.run_once()
    assert not new_gate.holding


def test_threaded_start_and_stop_release_the_lease(lite: sessionmaker[Session]) -> None:
    coordinator, gate = _coordinator(lite, "solo", Mono(), Wall())
    coordinator.start()
    try:
        assert coordinator.lease_held and gate.remaining() is None
    finally:
        coordinator.stop()
    assert not coordinator.lease_held
    assert try_acquire_market_lease(lite, owner="next", now=T0, ttl_seconds=90).acquired


def test_factory_is_off_for_replay_and_kill_switches(lite: sessionmaker[Session]) -> None:
    replay = Settings(perpetual_evidence_source="replay")
    assert start_binance_gate_coordination(replay, lite) is None
    off = Settings(
        perpetual_evidence_source="binance_usdm",
        binance_gate_persistence_enabled=False,
        binance_market_lease_enabled=False,
    )
    assert start_binance_gate_coordination(off, lite) is None


@pytest.mark.parametrize(
    ("heartbeat", "ttl", "expected"),
    [(300.0, 15, 5.0), (60.0, 90, 30.0), (20.0, 90, 20.0)],
)
def test_heartbeat_is_clamped_to_a_third_of_the_ttl(
    lite: sessionmaker[Session], heartbeat: float, ttl: int, expected: float
) -> None:
    """Config allows heartbeat 300 with TTL 15; renewals must still beat the TTL."""

    coordinator = BinanceGateCoordinator(
        BinanceRestGate(rng=lambda: 0.0),
        lite,
        owner="clamp",
        lease_ttl_seconds=ttl,
        heartbeat_seconds=heartbeat,
        now=Wall(),
        monotonic=Mono(),
    )
    waits: list[float] = []

    class OneWait:
        def wait(self, interval: float) -> bool:
            waits.append(interval)
            coordinator._stop.set()
            return True

        def set(self) -> None:
            pass

        def clear(self) -> None:
            pass

    coordinator._wake = OneWait()  # type: ignore[assignment]
    coordinator._run()
    assert waits == [expected]


def test_factory_logs_when_both_kill_switches_are_off(lite: sessionmaker[Session]) -> None:
    off = Settings(
        perpetual_evidence_source="binance_usdm",
        binance_gate_persistence_enabled=False,
        binance_market_lease_enabled=False,
    )
    with capture_logs() as logs:
        assert start_binance_gate_coordination(off, lite) is None
    disabled = [entry for entry in logs if entry["event"] == "binance_gate_coordination_disabled"]
    assert len(disabled) == 1
    assert disabled[0]["reason"] == "kill_switches_off"
    assert disabled[0]["binance_gate_persistence_enabled"] is False
    assert disabled[0]["binance_market_lease_enabled"] is False


def test_factory_with_one_switch_on_starts_and_does_not_log_disabled(
    lite: sessionmaker[Session],
) -> None:
    from app.market_contracts.adapters import binance_rest_gate

    binance_rest_gate.reset_process_binance_rest_gate()
    lease_only = Settings(
        perpetual_evidence_source="binance_usdm", binance_gate_persistence_enabled=False
    )
    with capture_logs() as logs:
        coordinator = start_binance_gate_coordination(lease_only, lite)
    try:
        assert coordinator is not None and coordinator.lease_held
        events = {entry["event"] for entry in logs}
        assert "binance_gate_coordination_disabled" not in events
        assert "binance_market_lease_acquired" in events
    finally:
        assert coordinator is not None
        coordinator.stop()
        binance_rest_gate.reset_process_binance_rest_gate()


def test_factory_starts_for_live_source(
    lite: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.market_contracts.adapters import binance_rest_gate

    binance_rest_gate.reset_process_binance_rest_gate()
    settings = Settings(perpetual_evidence_source="binance_usdm")
    coordinator = start_binance_gate_coordination(settings, lite)
    try:
        assert coordinator is not None and coordinator.lease_held
    finally:
        assert coordinator is not None
        coordinator.stop()
        binance_rest_gate.reset_process_binance_rest_gate()


# --- paper worker wiring -------------------------------------------------------------


class FakeCoordinator:
    def __init__(self) -> None:
        self.stops = 0

    def stop(self) -> None:
        self.stops += 1


def _stub_supervisor():  # type: ignore[no-untyped-def]
    from app.workers.paper_worker import CycleOutcome, PaperWorkerSupervisor

    return PaperWorkerSupervisor(
        watcher_cycle=lambda: CycleOutcome("running"),
        telegram_cycle=lambda: CycleOutcome("disarmed"),
        poll_seconds=0.01,
    )


def test_armed_boot_starts_coordination_first_and_close_releases_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.workers.paper_worker import build_paper_worker_supervisor
    from tests.test_blofin_activity_provider import readonly_settings

    order: list[str] = []
    fake = FakeCoordinator()

    def start(settings: Settings) -> FakeCoordinator:
        order.append("coordination")
        return fake

    def armed(*_: object):  # type: ignore[no-untyped-def]
        order.append("runtimes")
        return _stub_supervisor()

    monkeypatch.setattr(
        "app.workers.binance_gate_coordination.start_binance_gate_coordination", start
    )
    monkeypatch.setattr("app.workers.paper_worker._build_armed_supervisor", armed)
    worker = build_paper_worker_supervisor(readonly_settings())
    assert order == ["coordination", "runtimes"]
    assert fake.stops == 0
    worker.close()
    assert fake.stops == 1


def test_failed_armed_boot_still_releases_coordination(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workers.paper_worker import build_paper_worker_supervisor
    from tests.test_blofin_activity_provider import readonly_settings

    fake = FakeCoordinator()
    monkeypatch.setattr(
        "app.workers.binance_gate_coordination.start_binance_gate_coordination",
        lambda settings: fake,
    )

    def broken(*_: object) -> None:
        raise RuntimeError("boot failed")

    monkeypatch.setattr("app.workers.paper_worker._build_armed_supervisor", broken)
    with pytest.raises(RuntimeError):
        build_paper_worker_supervisor(readonly_settings())
    assert fake.stops == 1


def test_start_adopts_a_stored_ban_before_any_read(lite: sessionmaker[Session]) -> None:
    """The real ``start`` path: a ban stored by the old instance closes the new gate."""

    wall = Wall()
    record_gate_deadline(lite, kind="ban", until=T0 + timedelta(minutes=18), writer="old", now=T0)
    coordinator, gate = _coordinator(lite, "new", Mono(), wall)
    coordinator.start()
    try:
        closed = gate.remaining()
        assert closed is not None and closed[0] == "ban"
        assert closed[1] == pytest.approx(18 * 60, abs=1.0)
    finally:
        coordinator.stop()
