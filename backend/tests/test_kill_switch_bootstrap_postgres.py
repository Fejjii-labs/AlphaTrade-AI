"""Real authenticated ASGI requests and disposable PostgreSQL kill-switch bootstrap.

No provider/worker lifespan or exchange side effects are started. Contention and
timeouts below are test-only; the first request owns the actual new database row.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Iterator
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

import app.db.session as session_module
from app.core.config import Settings
from app.core.errors import TradingPolicyError
from app.db.base import Base
from app.db.models import AuditLog, KillSwitchState, Membership, Organization, User
from app.main import create_app
from app.schemas.common import AuditEventType, MembershipRole
from app.security.tokens import create_access_token
from app.services.audit_service import AuditService
from app.services.risk.kill_switch import KillSwitchService


@pytest.fixture
def postgres_world(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    raw_url = os.getenv("EXPERIMENT_TEST_POSTGRES_URL") or os.getenv("PHASE1_POSTGRES_URL")
    if not raw_url:
        pytest.skip("Requires disposable EXPERIMENT_TEST_POSTGRES_URL or PHASE1_POSTGRES_URL")
    url = make_url(raw_url)
    assert url.get_backend_name() == "postgresql", "Requires an explicit disposable PostgreSQL URL"
    schema = "kill_switch_test_" + uuid4().hex
    application_name = "kill-switch-" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = url.update_query_dict(
        {
            "options": f"-csearch_path={schema} -clock_timeout=2000ms -cstatement_timeout=4000ms",
            "application_name": application_name,
        }
    )
    engine = create_engine(isolated)
    control = SimpleNamespace(fail_next_commit=False, rollbacks=0)

    class ControlledSession(Session):
        def commit(self) -> None:
            if control.fail_next_commit:
                control.fail_next_commit = False
                raise RuntimeError("synthetic bootstrap commit failure")
            super().commit()

        def rollback(self) -> None:
            control.rollbacks += 1
            super().rollback()

    factory = sessionmaker(bind=engine, class_=ControlledSession, expire_on_commit=False)
    try:
        Base.metadata.create_all(engine)
        settings = Settings(
            _env_file=None,
            environment="local",
            log_json=False,
            execution_mode="paper",
            enable_real_trading=False,
            exchange_mode="paper_internal",
            provider_mode="mock",
            market_data_provider="mock",
            global_kill_switch_active=False,
            database_url=raw_url,
            jwt_secret="synthetic-pg-bootstrap-secret-32-bytes",
            rate_limit_use_redis=False,
            market_data_cache_use_redis=False,
            access_token_denylist_use_redis=False,
        )
        org_a, org_b = uuid4(), uuid4()
        identities = {
            "owner": (uuid4(), org_a, MembershipRole.OWNER),
            "trader": (uuid4(), org_a, MembershipRole.TRADER),
            "viewer": (uuid4(), org_a, MembershipRole.VIEWER),
            "other_owner": (uuid4(), org_b, MembershipRole.OWNER),
        }
        headers = {}
        with factory() as session:
            session.add_all(
                [
                    Organization(id=org_a, name="Synthetic A"),
                    Organization(id=org_b, name="Synthetic B"),
                ]
            )
            for name, (user_id, organization_id, _role) in identities.items():
                email = f"{name}@example.com"
                session.add(
                    User(
                        id=user_id,
                        email=email,
                        hashed_password="synthetic-unused",
                        is_active=True,
                        email_verified=True,
                    )
                )
                token, _ = create_access_token(
                    user_id=user_id,
                    organization_id=organization_id,
                    email=email,
                    settings=settings,
                )
                headers[name] = {"Authorization": f"Bearer {token}"}
            session.flush()
            session.add_all(
                Membership(user_id=user_id, organization_id=organization_id, role=role)
                for user_id, organization_id, role in identities.values()
            )
            session.commit()
            assert session.scalar(select(func.count()).select_from(KillSwitchState)) == 0
        # Keep the real get_session generator (including its close-only cleanup),
        # real JWT authentication, RBAC, rate limits and shared request-session DI.
        monkeypatch.setattr(session_module, "get_session_factory", lambda: factory)
        app = create_app(settings=settings)
        yield SimpleNamespace(
            app=app,
            engine=engine,
            admin=admin,
            application_name=application_name,
            factory=factory,
            control=control,
            settings=settings,
            org_a=org_a,
            org_b=org_b,
            identities=identities,
            headers=headers,
        )
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _client(world: SimpleNamespace) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=world.app, raise_app_exceptions=False),
        base_url="http://test",
    )


def _audits(world: SimpleNamespace) -> list[AuditEventType]:
    with world.factory() as session:
        return list(session.scalars(select(AuditLog.action).order_by(AuditLog.event_at)))


async def test_concurrent_empty_tenant_reads_commit_one_row_without_blocking_asgi(
    postgres_world: SimpleNamespace,
) -> None:
    world = postgres_world
    first_inserted = threading.Event()
    second_started = threading.Event()
    contention_seen = threading.Event()
    probe_done = threading.Event()
    release_first = threading.Event()
    insert_lock = threading.Lock()
    insert_count = 0
    watchdog_failures = []

    def before_insert(_conn, _cursor, statement, _parameters, _context, _many):
        nonlocal insert_count
        if statement.startswith("INSERT INTO kill_switch_states"):
            with insert_lock:
                insert_count += 1
                if insert_count == 2:
                    second_started.set()

    def hold_first_insert(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO kill_switch_states"):
            with insert_lock:
                first = insert_count == 1
            if first:
                first_inserted.set()
                if not release_first.wait(10):
                    raise AssertionError(
                        "Bootstrap contention watchdog did not release first INSERT"
                    )

    def contention_watchdog():
        try:
            if not first_inserted.wait(3):
                watchdog_failures.append("first authenticated request never inserted")
                return
            if not second_started.wait(2):
                watchdog_failures.append(
                    "second authenticated request could not start on the ASGI loop"
                )
                return
            deadline = time.monotonic() + 2
            with world.admin.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as connection:
                while time.monotonic() < deadline:
                    blocked = connection.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE application_name = :name AND wait_event_type = 'Lock' "
                            "AND query ILIKE '%insert%kill_switch_states%'"
                        ),
                        {"name": world.application_name},
                    )
                    if blocked:
                        contention_seen.set()
                        break
                    release_first.wait(0.01)
            if not contention_seen.is_set():
                watchdog_failures.append(
                    "second INSERT did not genuinely wait for a PostgreSQL lock"
                )
                return
            if not probe_done.wait(2):
                watchdog_failures.append(
                    "unrelated authenticated API did not finish while INSERT was blocked"
                )
        except Exception as exc:
            watchdog_failures.append(repr(exc))
        finally:
            # This OS thread also releases the old async implementation when its
            # synchronous INSERT hook freezes the event loop: bounded negative control.
            release_first.set()

    event.listen(world.engine, "before_cursor_execute", before_insert)
    event.listen(world.engine, "after_cursor_execute", hold_first_insert)
    watchdog = threading.Thread(target=contention_watchdog, daemon=True)
    requests = []
    with world.factory() as session:
        assert session.scalar(select(func.count()).select_from(KillSwitchState)) == 0
    watchdog.start()
    try:
        async with _client(world) as client:
            requests.append(
                asyncio.create_task(client.get("/risk/kill-switch", headers=world.headers["owner"]))
            )
            assert await asyncio.to_thread(first_inserted.wait, 4), watchdog_failures
            requests.append(
                asyncio.create_task(client.get("/risk/kill-switch", headers=world.headers["owner"]))
            )
            assert await asyncio.to_thread(contention_seen.wait, 4), watchdog_failures
            assert not requests[0].done() and not requests[1].done()
            started = time.monotonic()
            me = await asyncio.wait_for(
                client.get("/auth/me", headers=world.headers["owner"]), timeout=1
            )
            assert me.status_code == 200, me.text
            assert me.json()["organization"]["id"] == str(world.org_a)
            assert time.monotonic() - started < 1
            assert not release_first.is_set(), (
                "probe must finish while actual first INSERT remains uncommitted"
            )
            probe_done.set()
            results = await asyncio.wait_for(asyncio.gather(*requests), timeout=6)
            assert not watchdog_failures, watchdog_failures
            for response in results:
                assert response.status_code == 200, response.text
                state = response.json()
                assert state["organization_id"] == str(world.org_a)
                assert state["active"] is False
                assert state["version"] == 1
                assert state["reason"] is None
                assert state["activated_by"] is None
                assert state["activated_at"] is None
                assert state["deactivated_by"] is None
                assert state["deactivated_at"] is None
                assert state["global_active"] is False
                assert state["execution_blocked"] is False
            assert results[0].json() == results[1].json()
            with world.factory() as session:
                rows = list(session.scalars(select(KillSwitchState)))
                assert len(rows) == 1
                original_id = rows[0].id
            again = await client.get("/risk/kill-switch", headers=world.headers["owner"])
            assert again.status_code == 200
            assert again.json() == results[0].json()
            with world.factory() as session:
                assert session.scalar(select(KillSwitchState.id)) == original_id
            assert _audits(world) == []
    finally:
        release_first.set()
        if requests:
            await asyncio.wait_for(asyncio.gather(*requests, return_exceptions=True), timeout=6)
        await asyncio.to_thread(watchdog.join, 3)
        event.remove(world.engine, "before_cursor_execute", before_insert)
        event.remove(world.engine, "after_cursor_execute", hold_first_insert)


async def test_bootstrap_commit_failure_rolls_back_and_next_request_can_initialize(
    postgres_world: SimpleNamespace,
) -> None:
    world = postgres_world
    world.control.fail_next_commit = True
    async with _client(world) as client:
        failed = await client.get("/risk/kill-switch", headers=world.headers["owner"])
        assert failed.status_code == 500
        assert world.control.rollbacks == 1, (
            "endpoint must explicitly roll back its failed bootstrap transaction"
        )
        with world.factory() as session:
            assert session.scalar(select(func.count()).select_from(KillSwitchState)) == 0
        assert _audits(world) == []
        retry = await client.get("/risk/kill-switch", headers=world.headers["owner"])
        assert retry.status_code == 200
        assert retry.json()["active"] is False
        assert retry.json()["version"] == 1
        with world.factory() as session:
            assert session.scalar(select(func.count()).select_from(KillSwitchState)) == 1
        assert _audits(world) == []


async def test_operator_state_rbac_tenants_versions_audit_and_execution_remain_protected(
    postgres_world: SimpleNamespace,
) -> None:
    world = postgres_world
    owner = world.headers["owner"]
    async with _client(world) as client:
        unauthenticated = await client.get("/risk/kill-switch")
        assert unauthenticated.status_code == 401
        initial = await client.get("/risk/kill-switch", headers=world.headers["viewer"])
        assert initial.status_code == 200
        assert initial.json()["active"] is False
        assert initial.json()["version"] == 1
        assert _audits(world) == []
        activated = await client.post(
            "/risk/kill-switch/activate",
            headers=owner,
            json={"confirm": True, "reason": "synthetic incident", "expected_version": 1},
        )
        assert activated.status_code == 200, activated.text
        active = activated.json()
        assert active["active"] is True and active["execution_blocked"] is True
        assert active["version"] == 2
        assert active["reason"] == "synthetic incident"
        assert active["activated_by"] == str(world.identities["owner"][0])
        assert active["activated_at"] is not None
        assert _audits(world) == [AuditEventType.KILL_SWITCH_ACTIVATED]
        repeated = await client.get("/risk/kill-switch", headers=owner)
        assert repeated.status_code == 200
        assert repeated.json() == active, "reading must preserve existing operator state exactly"
        other = await client.get("/risk/kill-switch", headers=world.headers["other_owner"])
        assert other.status_code == 200
        assert other.json()["organization_id"] == str(world.org_b)
        assert other.json()["active"] is False and other.json()["version"] == 1
        for role in ("trader", "viewer"):
            denied = await client.post(
                "/risk/kill-switch/deactivate",
                headers=world.headers[role],
                json={"confirm": True, "reason": "forbidden", "expected_version": 2},
            )
            assert denied.status_code == 403
        unconfirmed = await client.post(
            "/risk/kill-switch/deactivate",
            headers=owner,
            json={"confirm": False, "reason": "unconfirmed", "expected_version": 2},
        )
        assert unconfirmed.status_code == 422
        stale = await client.post(
            "/risk/kill-switch/deactivate",
            headers=owner,
            json={"confirm": True, "reason": "stale", "expected_version": 1},
        )
        assert stale.status_code == 409
        unchanged = await client.get("/risk/kill-switch", headers=owner)
        assert unchanged.json() == active
        assert _audits(world) == [AuditEventType.KILL_SWITCH_ACTIVATED]
        with world.factory() as session:
            service = KillSwitchService(session, AuditService(session), world.settings)
            with pytest.raises(TradingPolicyError) as refused:
                service.assert_execution_allowed(
                    organization_id=world.org_a, user_id=world.identities["owner"][0]
                )
            assert refused.value.details["reason"] == "kill_switch_active"
            assert service.evaluate(organization_id=world.org_b).blocked is False
            session.rollback()
        assert _audits(world) == [
            AuditEventType.KILL_SWITCH_ACTIVATED,
            AuditEventType.KILL_SWITCH_TRIGGERED,
        ]
        deactivated = await client.post(
            "/risk/kill-switch/deactivate",
            headers=owner,
            json={"confirm": True, "reason": "synthetic resolution", "expected_version": 2},
        )
        assert deactivated.status_code == 200
        cleared = deactivated.json()
        assert cleared["active"] is False and cleared["execution_blocked"] is False
        assert cleared["version"] == 3
        assert cleared["deactivated_by"] == str(world.identities["owner"][0])
        assert cleared["deactivated_at"] is not None
        idempotent = await client.post(
            "/risk/kill-switch/deactivate",
            headers=owner,
            json={"confirm": True, "reason": "already cleared", "expected_version": 3},
        )
        assert idempotent.status_code == 200
        assert idempotent.json() == cleared
        assert _audits(world) == [
            AuditEventType.KILL_SWITCH_ACTIVATED,
            AuditEventType.KILL_SWITCH_TRIGGERED,
            AuditEventType.KILL_SWITCH_DEACTIVATED,
        ]
        with world.factory() as session:
            settings = world.settings.model_copy(update={"global_kill_switch_active": True})
            service = KillSwitchService(session, AuditService(session), settings)
            assert (
                service.evaluate(organization_id=world.org_a).reason_code
                == "global_kill_switch_active"
            )
            with pytest.raises(TradingPolicyError) as refused:
                service.assert_execution_allowed(organization_id=world.org_a)
            assert refused.value.details["reason"] == "global_kill_switch_active"
            assert (
                session.scalar(
                    select(KillSwitchState.active).where(
                        KillSwitchState.organization_id == world.org_a
                    )
                )
                is False
            )
            assert (
                session.scalar(
                    select(KillSwitchState.version).where(
                        KillSwitchState.organization_id == world.org_a
                    )
                )
                == 3
            )
            session.rollback()


def test_shared_service_bootstrap_keeps_transaction_owned_by_caller(
    postgres_world: SimpleNamespace,
) -> None:
    world = postgres_world
    with world.factory() as session:
        service = KillSwitchService(session, AuditService(session), world.settings)
        state = service.get_status(organization_id=world.org_a)
        assert state.active is False and state.version == 1
        assert session.in_transaction()
        with world.factory() as observer:
            assert observer.scalar(select(func.count()).select_from(KillSwitchState)) == 0
        session.rollback()
    with world.factory() as observer:
        assert observer.scalar(select(func.count()).select_from(KillSwitchState)) == 0
    assert _audits(world) == []
