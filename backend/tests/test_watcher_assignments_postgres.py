"""Disposable PostgreSQL monitoring config authority; synthetic evidence only."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.db.models import Membership, Organization, User, UserStrategy, UserStrategyVersion
from app.db.session import get_session
from app.db.watcher_assignments import WatcherAssignmentReceiptRow, WatcherStrategyAssignmentRow
from app.db.watcher_watchlist import WatcherSymbolStatusRow
from app.main import create_app
from app.repositories.watcher_watchlist import WatcherWatchlistRepository
from app.schemas.common import MembershipRole, StrategyChangeSource
from app.schemas.watcher_assignments import WatcherAssignmentReplace, WatcherAssignmentWrite
from app.services.strategy_versioning import StrategyVersioningService
from app.services.watcher_assignment_service import (
    AssignmentCandidateFence,
    WatcherAssignmentService,
    assignment_configuration_exists,
    assignment_scan_targets,
    assignment_target_reason,
    version_snapshot,
)
from app.watcher.errors import WatcherEvidenceUnavailableError
from tests.test_watcher_paper_runtime import (
    ORG,
    ORG_B,
    USER,
    USER_B,
    _approve,
    _attach_spec,
    _create_strategy,
    _runtime,
    _seed_approved_compiled,
    _settings,
)

PEER = uuid4()


@pytest.fixture
def factory():
    url = os.environ.get("EXPERIMENT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Explicit disposable PostgreSQL required")
    from tests.support.postgres_template import clone_database, drop_clone

    engine = clone_database(url)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add_all(
            [
                Organization(id=ORG, name="Assignment A"),
                Organization(id=ORG_B, name="Assignment B"),
                User(id=USER, email="assign-a@test.example", hashed_password="unused"),
                User(id=USER_B, email="assign-b@test.example", hashed_password="unused"),
                User(id=PEER, email="assign-peer@test.example", hashed_password="unused"),
            ]
        )
        db.flush()
        db.add_all(
            [
                Membership(organization_id=ORG, user_id=USER, role=MembershipRole.OWNER),
                Membership(organization_id=ORG_B, user_id=USER_B, role=MembershipRole.OWNER),
                Membership(organization_id=ORG, user_id=PEER, role=MembershipRole.TRADER),
            ]
        )
        db.commit()
    try:
        yield factory
    finally:
        drop_clone(engine)


def item(db, version_id, *, enabled=True, identity=None):
    version = db.get(UserStrategyVersion, version_id)
    timeframes, parameters = version_snapshot(version)
    return WatcherAssignmentWrite(
        id=identity or uuid4(),
        symbol="BTCUSDT",
        strategy_version_id=version_id,
        monitoring_enabled=enabled,
        timeframes=timeframes,
        parameters=parameters,
    )


def body(db, versions=(), *, revision=0, request_key=None):
    return WatcherAssignmentReplace(
        revision=revision,
        request_key=request_key or str(uuid4()),
        assignments=[item(db, v) for v in versions],
    )


def save(db, value, *, org=ORG, user=USER):
    result = WatcherAssignmentService(db).replace(organization_id=org, user_id=user, body=value)
    db.commit()
    return result


def test_multiple_pinned_versions_reload_and_monitoring_toggle_preserve_siblings(factory):
    with factory() as db:
        _, one = _seed_approved_compiled(db)
        _, two = _seed_approved_compiled(db)
        original = WatcherWatchlistRepository(db).load(ORG)
        request = body(db, [one, two])
        first = save(db, request)
        assert first.configured and len(first.assignments) == 2
        assert all(row.eligible for row in first.assignments)
        assert WatcherWatchlistRepository(db).load(ORG).slots == original.slots
    with factory() as restarted:
        persisted = WatcherAssignmentService(restarted).read(organization_id=ORG, user_id=USER)
        assert {a.strategy_version_id for a in persisted.assignments} == {one, two}
        changed = WatcherAssignmentReplace(
            revision=1,
            request_key=str(uuid4()),
            assignments=[
                request.assignments[0].model_copy(update={"monitoring_enabled": False}),
                request.assignments[1],
            ],
        )
        saved = save(restarted, changed)
        assert {a.strategy_version_id: a.reason_code for a in saved.assignments} == {
            one: "monitoring_disabled",
            two: "ready",
        }
        targets, facts = assignment_scan_targets(
            restarted, organization_id=ORG, symbols=("BTCUSDT",), limit=25
        )
        assert [t.strategy_version_id for t in targets] == [two]
        assert targets[0].assignment_id == request.assignments[1].id
        assert (
            next(
                f for f in facts["BTCUSDT"] if f["assignment_id"] == str(request.assignments[0].id)
            )["reason_code"]
            == "monitoring_disabled"
        )
        assert WatcherWatchlistRepository(restarted).load(ORG).slots == original.slots


def test_foreign_tenant_and_other_user_private_versions_never_bind_or_leak(factory):
    with factory() as db:
        foreign, foreign_version = _seed_approved_compiled(db, org=ORG_B, user=USER_B)
        private, private_version = _seed_approved_compiled(db, user=PEER)
        save(db, body(db, [private_version]), user=PEER)
        visible = WatcherAssignmentService(db).read(organization_id=ORG, user_id=USER)
        assert visible.assignments == [] and visible.available_versions == []
        for version_id in (foreign_version, private_version):
            with pytest.raises(NotFoundError):
                save(db, body(db, [version_id], revision=1))
            db.rollback()
        empty = save(db, body(db, revision=1))
        assert empty.assignments == []
        peer = WatcherAssignmentService(db).read(organization_id=ORG, user_id=PEER)
        assert len(peer.assignments) == 1 and peer.assignments[0].strategy_name == private.name
        assert foreign.name not in visible.model_dump_json()


@pytest.mark.parametrize("change", ["timeframe", "parameter", "symbol"])
def test_configuration_cannot_rewrite_saved_version_semantics(factory, change):
    with factory() as db:
        _, version = _seed_approved_compiled(db)
        request = body(db, [version])
        value = request.assignments[0]
        if change == "timeframe":
            value = value.model_copy(update={"timeframes": ["1h"]})
        elif change == "symbol":
            value = value.model_copy(update={"symbol": "ETHUSDT"})
        else:
            value = value.model_copy(
                update={"parameters": {**value.parameters, "expiry_final_bars": 99}}
            )
        request = request.model_copy(update={"assignments": [value]})
        with pytest.raises(ValidationAppError):
            save(db, request)
        db.rollback()
        assert not assignment_configuration_exists(db, ORG)
        assert db.scalar(select(func.count()).select_from(WatcherStrategyAssignmentRow)) == 0


def test_exact_request_receipt_survives_restart_and_changed_key_payload_refuses(factory):
    with factory() as db:
        _, version = _seed_approved_compiled(db)
        request = body(db, [version])
        original = save(db, request)
    with factory() as restarted:
        replay = save(restarted, request)
        assert replay == original
        assert replay.revision == 1
        assert restarted.scalar(select(func.count()).select_from(WatcherAssignmentReceiptRow)) == 1
        assert restarted.scalar(select(func.count()).select_from(WatcherStrategyAssignmentRow)) == 1
        with pytest.raises(ConflictError):
            save(restarted, request.model_copy(update={"assignments": []}))
        restarted.rollback()


def test_concurrent_identical_request_converges_one_revision(factory):
    with factory() as db:
        _, version = _seed_approved_compiled(db)
        request = body(db, [version])

    def submit():
        with factory() as db:
            return save(db, request).model_dump(mode="json")

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: submit(), range(2)))
    assert values[0] == values[1]
    with factory() as db:
        assert WatcherWatchlistRepository(db).load(ORG).revision == 1
        assert db.scalar(select(func.count()).select_from(WatcherAssignmentReceiptRow)) == 1


def test_empty_saved_configuration_never_restores_legacy_strategy_inference(factory):
    from app.workers.watcher_paper import WatcherPaperCycleReport

    with factory() as db:
        _, version_id = _seed_approved_compiled(db)
        save(db, body(db, [version_id]))
        save(db, body(db, revision=1))
        assert assignment_configuration_exists(db, ORG)
    runtime, _, _, _ = _runtime(factory)
    runtime._watchlist_mode = True
    runtime._organization_id = ORG
    with factory() as db:
        runtime._watchlist = WatcherWatchlistRepository(db).load(ORG)
        assert runtime._load_targets(db) == ()
    runtime._project_watchlist_status()
    runtime._record_watchlist_scans(WatcherPaperCycleReport("idle", True, (), 0, False))
    states = {row.symbol: row.setup_state for row in runtime._symbol_status.snapshot()}
    assert states["BTCUSDT"] == "no_monitoring_assignment"
    assert {state for symbol, state in states.items() if symbol != "BTCUSDT"} == {"no_strategy"}


def test_exact_version_does_not_follow_new_current_draft(factory):
    with factory() as db:
        strategy, version_id = _seed_approved_compiled(db)
        save(db, body(db, [version_id]))
        version = db.get(UserStrategyVersion, version_id)
        next_version = StrategyVersioningService(db).fork_semantic_update(
            strategy,
            parent=version,
            card=version.card,
            structured_rules=version.structured_rules,
            lesson_source_metadata=version.lesson_source_metadata,
            actor_user_id=USER,
            source=StrategyChangeSource.PATTERN_SPEC,
            reason="New unapproved authored variant",
            pattern_spec={**version.pattern_spec, "expiry_final_bars": 3},
        )
        db.commit()
        assert strategy.current_version == next_version.version
        targets, _ = assignment_scan_targets(
            db, organization_id=ORG, symbols=("BTCUSDT",), limit=25
        )
        assert [target.strategy_version_id for target in targets] == [version_id]


def test_draft_and_missing_pattern_show_precise_unscannable_reason(factory):
    with factory() as db:
        created = _create_strategy(db)
        version = StrategyVersioningService(db).selected_version(db.get(UserStrategy, created.id))
        save(db, body(db, [version.id]))
        result = WatcherAssignmentService(db).read(organization_id=ORG, user_id=USER)
        assert result.assignments[0].reason_code == "authored_pattern_missing"
        targets, _ = assignment_scan_targets(
            db, organization_id=ORG, symbols=("BTCUSDT",), limit=25
        )
        assert targets == ()


def test_worker_consumes_authoritative_targets_and_never_enters_execution_loop(
    factory, monkeypatch
):
    with factory() as db:
        _, version_id = _seed_approved_compiled(db)
        request = body(db, [version_id])
        save(db, request)
    delivered = []
    runtime, _, side_effects, _ = _runtime(factory, scan_notification_hook=delivered.append)
    runtime._watchlist_mode = True
    runtime._tenant_watchlists = True
    monkeypatch.setattr(
        runtime, "_continue_paper_loop", lambda *args: pytest.fail("Monitoring must not execute")
    )
    result = runtime.run_cycle()
    assert len(result.scans) == 1 and result.scans[0].status == "succeeded", result
    assert len(delivered) == 1
    assert side_effects.unused
    with factory() as db:
        visible = WatcherAssignmentService(db).read(organization_id=ORG, user_id=USER)
        assert visible.assignments[0].strategy_version_id == version_id
        # Original observation time may be older than API now; it must not be presented as fresh.
        row = db.get(WatcherSymbolStatusRow, (ORG, "BTCUSDT"))
        fact = row.payload["assignment_observations"][0]
        assert fact["assignment_id"] == str(request.assignments[0].id)
        assert fact["status"] == "succeeded"


def test_pause_after_load_cancels_before_evidence_and_candidate_publish(factory):
    with factory() as db:
        _, version_id = _seed_approved_compiled(db)
        request = body(db, [version_id])
        save(db, request)
        (target,), _ = assignment_scan_targets(
            db, organization_id=ORG, symbols=("BTCUSDT",), limit=25
        )
    with factory() as writer:
        changed = request.model_copy(
            update={
                "revision": 1,
                "request_key": str(uuid4()),
                "assignments": [
                    request.assignments[0].model_copy(update={"monitoring_enabled": False})
                ],
            }
        )
        save(writer, changed)
    runtime, _, _, _ = _runtime(
        factory, evidence_factory=lambda *args: pytest.fail("No provider reads")
    )
    with factory() as stale:
        assert assignment_target_reason(stale, target) == "monitoring_configuration_changed"
        scan = runtime._scan_target(stale, target)
        assert scan.status == "blocked" and scan.reason_code == "monitoring_configuration_changed"
        with (
            pytest.raises(WatcherEvidenceUnavailableError),
            AssignmentCandidateFence(stale, target, None).bind_from_evaluation_command(None),
        ):
            pytest.fail("No publish after pause")


def test_retained_membership_cache_cannot_authorize_assignment_after_downgrade(factory):
    with factory() as cached:
        _, version_id = _seed_approved_compiled(cached)
        request = body(cached, [version_id])
        membership = cached.scalar(select(Membership).where(Membership.user_id == USER))
        assert membership.role is MembershipRole.OWNER
        cached.commit()
        with factory() as writer:
            row = writer.get(Membership, membership.id)
            row.role = MembershipRole.VIEWER
            writer.commit()
        with pytest.raises(ForbiddenError):
            save(cached, request)
        cached.rollback()
        assert not assignment_configuration_exists(cached, ORG)


def test_http_actual_login_scopes_assignment_and_rejects_viewer_writes(factory):
    app = create_app(settings=_settings())

    def sessions():
        with factory() as db:
            yield db

    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        assert client.get("/watcher/assignments").status_code == 401
        registered = client.post(
            "/auth/register",
            json={
                "email": "assignment-login@test.example",
                "password": "secure-password-1",
                "organization_name": "Actual assignment login",
            },
        )
        assert registered.status_code == 201, registered.text
        logged = client.post(
            "/auth/login",
            json={"email": "assignment-login@test.example", "password": "secure-password-1"},
        )
        assert logged.status_code == 200, logged.text
        headers = {"Authorization": "Bearer " + logged.json()["tokens"]["access_token"]}
        initial = client.get("/watcher/assignments", headers=headers)
        assert initial.status_code == 200 and not initial.json()["configured"]
        request = {"revision": 0, "request_key": "actual-login-empty", "assignments": []}
        first = client.put("/watcher/assignments", headers=headers, json=request)
        assert first.status_code == 200 and first.json()["configured"], first.text
        assert (
            client.put("/watcher/assignments", headers=headers, json=request).json() == first.json()
        )
        assert client.get("/watcher/assignments", headers=headers).json()["revision"] == 1
        with factory() as writer:
            membership = writer.scalar(
                select(Membership).where(
                    Membership.organization_id == logged.json()["organization"]["id"]
                )
            )
            membership.role = MembershipRole.VIEWER
            writer.commit()
        assert client.get("/watcher/assignments", headers=headers).status_code == 200
        assert (
            client.put(
                "/watcher/assignments",
                headers=headers,
                json={"revision": 1, "request_key": "viewer-refused", "assignments": []},
            ).status_code
            == 403
        )
        assert client.get("/watcher/assignments", headers=headers).json()["revision"] == 1
    app.dependency_overrides.clear()


def test_duplicate_and_invalid_snapshot_request_rejects_before_authority():
    row = WatcherAssignmentWrite(
        id=uuid4(),
        symbol="BTCUSDT",
        strategy_version_id=uuid4(),
        monitoring_enabled=True,
        timeframes=["15m"],
        parameters={},
    )
    with pytest.raises(ValidationError):
        WatcherAssignmentReplace(revision=0, request_key="dup", assignments=[row, row])
    with pytest.raises(ValidationError):
        row.model_copy(update={"timeframes": ["15m", "15m"]}).__class__.model_validate(
            {**row.model_dump(), "timeframes": ["15m", "15m"]}
        )


@pytest.mark.parametrize(
    "state,expected",
    [
        ("draft", "draft_not_executable"),
        ("disabled", "saved_strategy_disabled"),
        ("coin", "coin_disabled"),
        ("compile", "compiled_definition_missing"),
    ],
)
def test_enabled_assignments_show_precise_scan_inability(factory, state, expected):
    from app.schemas.common import StrategyLifecycleState

    with factory() as db:
        if state == "compile":
            created = _create_strategy(db)
            version = _attach_spec(db, created.id)
            strategy = db.get(UserStrategy, created.id)
            _approve(db, strategy, version.id, user_id=USER)
            db.commit()
            version_id = version.id
        else:
            strategy, version_id = _seed_approved_compiled(db)
        save(db, body(db, [version_id]))
        if state == "draft":
            StrategyVersioningService(db).append_lifecycle(
                organization_id=ORG,
                strategy_id=strategy.id,
                strategy_version_id=version_id,
                new_state=StrategyLifecycleState.DRAFT,
                actor_user_id=USER,
                reason="Explicit lifecycle refusal",
            )
        elif state == "disabled":
            strategy.enabled = False
        elif state == "coin":
            repo = WatcherWatchlistRepository(db)
            config = repo.load(ORG)
            repo.replace(
                ORG,
                [(s.symbol, False if s.symbol == "BTCUSDT" else s.enabled) for s in config.slots],
                expected_revision=1,
            )
        db.commit()
        result = WatcherAssignmentService(db).read(organization_id=ORG, user_id=USER)
        assert result.assignments[0].monitoring_enabled
        assert not result.assignments[0].eligible and result.assignments[0].reason_code == expected
        targets, facts = assignment_scan_targets(
            db, organization_id=ORG, symbols=("BTCUSDT",), limit=25
        )
        assert targets == () and facts["BTCUSDT"][0]["reason_code"] == expected


@pytest.mark.parametrize(
    "family,forming", [("nested", True), ("nested", False), ("sfp", True), ("sfp", False)]
)
def test_assigned_family_worker_mock_notification_policy_and_restart(
    factory, monkeypatch, family, forming
):
    from app.candidate_alerts.gateway import CandidateAlertGateway
    from app.paper_interaction.bridge import project_scan_report
    from app.telegram_paper_agent.contracts import PaperAlertRecipient
    from app.telegram_paper_agent.gateway import TelegramPaperAgent
    from app.telegram_security.clock import FrozenClock
    from app.telegram_security.protocol import TelegramSecurityProtocol
    from app.telegram_security.transport import FakeTelegramTransport
    from tests.support.telegram_security import BOT, CHAT, enroll
    from tests.test_sfp_strategy_brain_runtime import runtime_world
    from tests.test_strategy_brain_nested import nested_runtime_world

    org, user = uuid4(), uuid4()
    with factory() as db:
        db.add_all(
            [
                Organization(id=org, name="Assigned mock notification"),
                User(id=user, email=f"{user}@test.example", hashed_password="unused"),
            ]
        )
        db.commit()
        if family == "nested":
            runtime, _, template, now = nested_runtime_world(
                (db, org, user), monkeypatch, forming=forming
            )
            version_id = template["version_id"]
        else:
            runtime, _, _, now = runtime_world((db, org, user, factory), forming=forming)
            version_id = db.scalar(
                select(UserStrategyVersion.id)
                .join(UserStrategy)
                .where(UserStrategy.organization_id == org)
            )
        save(db, body(db, [version_id]), org=org, user=user)
    runtime._settings = runtime._settings.model_copy(
        update={"watcher_paper_organization_id": str(org)}
    )
    monkeypatch.setattr(
        runtime,
        "_continue_paper_loop",
        lambda *args: pytest.fail("No execution authority from assignments"),
    )
    clock, transport = FrozenClock(now), FakeTelegramTransport()
    protocol = TelegramSecurityProtocol.in_memory(enabled=True, clock=clock, transport=transport)
    _, binding_id = enroll(protocol, organization_id=org, user_id=user)
    recipient = PaperAlertRecipient(
        organization_id=org,
        user_id=user,
        account_id=uuid4(),
        binding_id=binding_id,
        bot_id=BOT,
        chat_id=CHAT,
    )
    gateway = CandidateAlertGateway(lifecycle=runtime._lifecycle, protocol=protocol, clock=clock)
    agent = TelegramPaperAgent(protocol=protocol, candidates=gateway, clock=clock, enabled=True)
    reports = []

    def hook(report):
        reports.append(report)
        project_scan_report(agent, report, recipient=recipient)

    runtime._scan_notification_hook = hook
    first = runtime.run_cycle()
    assert len(first.scans) == 1 and first.scans[0].status == "succeeded", first
    assert len(reports) == 1
    outbox = protocol.store.list_outbox(organization_id=org)
    if family == "nested" and forming:
        assert first.scans[0].candidate_ids == () and outbox == []
    else:
        assert len(outbox) >= 1
        delivered = protocol.deliver_pending()
        assert delivered and transport.sent
    snapshot = protocol.store.list_outbox(organization_id=org)
    # Independent gateway reconstruction retains the same durable outbox identity.
    gateway = CandidateAlertGateway(lifecycle=runtime._lifecycle, protocol=protocol, clock=clock)
    agent = TelegramPaperAgent(protocol=protocol, candidates=gateway, clock=clock, enabled=True)
    runtime._held_fencing_tokens.clear()
    runtime.run_cycle()
    assert protocol.store.list_outbox(organization_id=org) == snapshot
    assert protocol.execution_attempt_count == 0


def test_publish_lock_allows_own_research_fk_and_serializes_concurrent_pause(factory):
    from threading import Event
    from time import monotonic

    from sqlalchemy import event

    from app.db.models import MarketWatcherObservation
    from app.schemas.market_watcher import MarketWatcherObservationStatus

    with factory() as db:
        _, version_id = _seed_approved_compiled(db)
        request = body(db, [version_id])
        save(db, request)
        (target,), _ = assignment_scan_targets(
            db, organization_id=ORG, symbols=("BTCUSDT",), limit=25
        )
    at_lock = Event()

    def seen(_connection, _cursor, statement, _params, _context, _many):
        if "organizations.id" in statement and "FOR UPDATE" in statement:
            at_lock.set()

    engine = factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", seen)
    pause = request.model_copy(
        update={
            "revision": 1,
            "request_key": str(uuid4()),
            "assignments": [
                request.assignments[0].model_copy(update={"monitoring_enabled": False})
            ],
        }
    )

    def save_pause():
        with factory() as db:
            db.execute(text("SET LOCAL statement_timeout = '4000ms'"))
            return save(db, pause)

    try:
        with factory() as scan, ThreadPoolExecutor(max_workers=1) as pool:
            scan.add(
                MarketWatcherObservation(
                    organization_id=ORG,
                    symbol="BTCUSDT",
                    exchange="replay",
                    timeframe="15m",
                    observed_at=datetime.now(UTC),
                    data_freshness="replay",
                    status=MarketWatcherObservationStatus.FRESH,
                )
            )
            scan.flush()  # Real research FK holds KEY SHARE in this transaction.
            start = monotonic()
            with AssignmentCandidateFence(scan, target, None).bind_from_evaluation_command(None):
                assert monotonic() - start < 2
                future = pool.submit(save_pause)
                assert at_lock.wait(2)
                assert not future.done()
            scan.commit()
            assert future.result(timeout=4).revision == 2
        with factory() as restarted:
            assert assignment_target_reason(restarted, target) == "monitoring_configuration_changed"
    finally:
        event.remove(engine, "before_cursor_execute", seen)


@pytest.mark.parametrize("family", ["nested", "sfp"])
def test_assigned_expiry_retains_evidence_and_uses_existing_notification_policy(
    factory, monkeypatch, family
):
    from dataclasses import replace

    from app.candidate_alerts.gateway import CandidateAlertGateway
    from app.db.strategy_brain import BrainSetupRow
    from app.paper_interaction.bridge import project_scan_report
    from app.schemas.nested_continuation import EvidenceAvailability
    from app.schemas.telegram_policy import NotificationEventType
    from app.services.canonical_strategy_evaluation import resolve_executable_strategy_policy
    from app.strategy_brain.service import setup_view
    from app.strategy_brain.sfp_runtime.notifications import sfp_notification_summaries
    from app.strategy_brain.sfp_runtime.records import record_sfp_availability
    from app.telegram_paper_agent.contracts import PaperAlertRecipient
    from app.telegram_paper_agent.gateway import TelegramPaperAgent
    from app.telegram_security.clock import FrozenClock
    from app.telegram_security.protocol import TelegramSecurityProtocol
    from app.telegram_security.transport import FakeTelegramTransport
    from tests.support.telegram_security import BOT, CHAT, enroll
    from tests.test_sfp_strategy_brain_runtime import runtime_world
    from tests.test_strategy_brain_nested import nested_runtime_world

    org, user = uuid4(), uuid4()
    with factory() as db:
        db.add_all(
            [
                Organization(id=org, name="Assignment expiry"),
                User(id=user, email=f"{user}@test.example", hashed_password="unused"),
            ]
        )
        db.commit()
        if family == "nested":
            runtime, _, template, _ = nested_runtime_world((db, org, user), monkeypatch)
            version_id = template["version_id"]
        else:
            runtime, _, _, _ = runtime_world((db, org, user, factory), forming=True)
            version_id = db.scalar(
                select(UserStrategyVersion.id)
                .join(UserStrategy)
                .where(UserStrategy.organization_id == org)
            )
        save(db, body(db, [version_id]), org=org, user=user)
    runtime._settings = runtime._settings.model_copy(
        update={"watcher_paper_organization_id": str(org)}
    )
    monkeypatch.setattr(
        runtime, "_continue_paper_loop", lambda *args: pytest.fail("Monitoring cannot execute")
    )
    report = runtime.run_cycle().scans[0]
    assert report.status == "succeeded"
    with factory() as fresh:
        row = fresh.scalar(select(BrainSetupRow).where(BrainSetupRow.organization_id == org))
        setup_id, original_observed = row.id, row.observed_at
        expiry = row.expires_at + timedelta(seconds=1)
        if report.discussion:
            expiry = max(expiry, report.discussion.candidate.valid_until + timedelta(seconds=1))
        if family == "sfp":
            policy = resolve_executable_strategy_policy(
                fresh, organization_id=org, user_id=user, strategy_version_id=version_id
            )
            record_sfp_availability(
                fresh,
                executable=policy,
                availability=EvidenceAvailability.MISSING,
                reason_codes=("provider_unavailable",),
                evaluated_at=expiry,
                evidence_hash="c" * 64,
            )
            fresh.commit()
    clock, transport = FrozenClock(expiry), FakeTelegramTransport()
    protocol = TelegramSecurityProtocol.in_memory(enabled=True, clock=clock, transport=transport)
    _, binding_id = enroll(protocol, organization_id=org, user_id=user)
    recipient = PaperAlertRecipient(
        organization_id=org,
        user_id=user,
        account_id=uuid4(),
        binding_id=binding_id,
        bot_id=BOT,
        chat_id=CHAT,
    )
    for _ in range(2):
        with factory() as restarted:
            row = restarted.get(BrainSetupRow, setup_id)
            view = setup_view(restarted, row, now=expiry)
            assert view["state"] == "EXPIRED" and view["freshness"] != "AVAILABLE"
            assert row.observed_at == original_observed
            assert view["current_market_claim"] is False
            if family == "sfp":
                summaries = sfp_notification_summaries(
                    restarted, organization_id=org, strategy_version_id=version_id, now=expiry
                )
                assert len(summaries) == 1
                assert summaries[0].event_type is NotificationEventType.SFP_EXPIRED
                assert summaries[0].evidence_at == original_observed
                current = replace(
                    report,
                    sfp_alerts=summaries,
                    candidate_ids=(),
                    published=False,
                    discussion=None,
                )
            else:
                current = report
        gateway = CandidateAlertGateway(
            lifecycle=runtime._lifecycle, protocol=protocol, clock=clock
        )
        agent = TelegramPaperAgent(protocol=protocol, candidates=gateway, clock=clock, enabled=True)
        assert project_scan_report(agent, current, recipient=recipient) is None
    outbox = protocol.store.list_outbox(organization_id=org)
    if family == "nested":
        assert outbox == []  # Existing confirmed-alert policy suppresses stale/expired facts.
    else:
        assert len(outbox) == 1
        protocol.deliver_pending()
        assert len(transport.sent) == 1
        protocol.deliver_pending()
        assert len(transport.sent) == 1
    assert protocol.execution_attempt_count == 0
