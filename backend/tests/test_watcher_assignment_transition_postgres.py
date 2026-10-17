"""Scoped explicit assignment transition; disposable PG and synthetic scans only."""

from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import Membership
from app.repositories.watcher_watchlist import WatcherWatchlistRepository
from app.schemas.common import MembershipRole
from app.services.watcher_assignment_service import (
    AssignmentCandidateFence,
    assignment_target_reason,
)
from app.watcher.errors import WatcherEvidenceUnavailableError
from tests.test_watcher_assignments_postgres import PEER, body, save
from tests.test_watcher_assignments_postgres import factory as factory
from tests.test_watcher_paper_runtime import (
    ORG,
    ORG_B,
    USER,
    USER_B,
    _runtime,
    _seed_approved_compiled,
)


def load(factory, *, org=ORG, symbols=("BTCUSDT", "ETHUSDT"), limit=25):
    runtime, _, _, _ = _runtime(factory)
    runtime._watchlist_mode = True
    runtime._organization_id = org
    runtime._symbols = symbols
    runtime._max_scopes_per_cycle = limit
    with factory() as db:
        return runtime._load_targets(db)


def test_first_assignment_preserves_unrelated_legacy_strategy_coverage(factory):
    from tests.test_sfp_detector import spec
    from tests.test_sfp_strategy_brain_runtime import approve

    with factory() as db:
        first, assigned = _seed_approved_compiled(db)
        other, unrelated = _seed_approved_compiled(db)
        eth = approve(db, ORG, USER, spec().model_copy(update={"symbol": "ETHUSDT"}))
        initial = load(factory)
        assert {target.strategy_version_id for target in initial} == {
            assigned,
            unrelated,
            eth.strategy_version_id,
        }
        save(db, body(db, [assigned]))
    targets = load(factory)
    assert {target.strategy_version_id for target in targets} == {
        assigned,
        unrelated,
        eth.strategy_version_id,
    }
    assert len(targets) == 3
    assert [
        target.assignment_id is not None for target in targets if target.strategy_id == first.id
    ] == [True]
    assert [target.assignment_id for target in targets if target.strategy_id == other.id] == [None]


@pytest.mark.parametrize("operation", ["disable", "remove", "empty"])
def test_disabled_or_removed_owned_binding_preserves_unrelated_coverage_after_restart(
    factory, operation
):
    with factory() as db:
        _, assigned = _seed_approved_compiled(db)
        _, retained = _seed_approved_compiled(db)
        _, unrelated = _seed_approved_compiled(db)
        original = body(db, [assigned, retained])
        save(db, original)
        rows = [] if operation == "empty" else [original.assignments[1]]
        if operation == "disable":
            rows.insert(0, original.assignments[0].model_copy(update={"monitoring_enabled": False}))
        save(
            db,
            original.model_copy(
                update={
                    "revision": 1,
                    "request_key": str(uuid4()),
                    "assignments": rows,
                }
            ),
        )
    targets = load(factory)
    expected = {unrelated} if operation == "empty" else {retained, unrelated}
    assert {target.strategy_version_id for target in targets} == expected
    assert len(targets) == len(expected)
    assert (
        next(target for target in targets if target.strategy_version_id == unrelated).assignment_id
        is None
    )
    if operation != "empty":
        assert (
            next(
                target for target in targets if target.strategy_version_id == retained
            ).assignment_id
            == original.assignments[1].id
        )
    assert load(factory) == targets  # New runtime and DB session retain the tombstone.


def test_first_empty_configuration_preserves_all_existing_inferred_coverage(factory):
    with factory() as db:
        _, first = _seed_approved_compiled(db)
        _, second = _seed_approved_compiled(db)
        save(db, body(db))
    targets = load(factory)
    assert {target.strategy_version_id for target in targets} == {first, second}
    assert all(target.assignment_id is None for target in targets)


def test_scoped_overrides_cannot_suppress_another_actor_or_tenant(factory):
    with factory() as db:
        _, owned = _seed_approved_compiled(db)
        _, peer = _seed_approved_compiled(db, org=ORG, user=PEER)
        _, foreign = _seed_approved_compiled(db, org=ORG_B, user=USER_B)
        original = body(db, [owned])
        save(db, original)
        save(db, body(db, revision=1))
    assert [target.strategy_version_id for target in load(factory)] == [peer]
    assert [target.strategy_version_id for target in load(factory, org=ORG_B)] == [foreign]


def test_suppressed_legacy_targets_do_not_consume_cycle_budget(factory):
    with factory() as db:
        _, managed = _seed_approved_compiled(db)
        _, other = _seed_approved_compiled(db)
        original = body(db, [managed])
        save(db, original)
        save(db, body(db, revision=1))
    (target,) = load(factory, limit=1)
    assert target.strategy_version_id == other
    assert len(load(factory, limit=0)) == 0


@pytest.mark.parametrize("operation", ["first_add", "pause", "remove", "coin_disable"])
def test_stale_target_cancels_before_evidence_and_publication(factory, operation):
    with factory() as db:
        _, version = _seed_approved_compiled(db)
        original = body(db, [version])
        if operation != "first_add":
            save(db, original)
    (target,) = load(factory)
    with factory() as writer:
        if operation == "first_add":
            save(writer, original)
        elif operation == "coin_disable":
            repo = WatcherWatchlistRepository(writer)
            config = repo.load(ORG)
            repo.replace(ORG, [(slot.symbol, False) for slot in config.slots], expected_revision=1)
            writer.commit()
        else:
            rows = (
                []
                if operation == "remove"
                else [original.assignments[0].model_copy(update={"monitoring_enabled": False})]
            )
            save(
                writer,
                original.model_copy(
                    update={
                        "revision": 1,
                        "request_key": str(uuid4()),
                        "assignments": rows,
                    }
                ),
            )
    runtime, _, _, _ = _runtime(
        factory,
        evidence_factory=lambda *args: pytest.fail("No provider read after stale authority"),
    )
    with factory() as fresh:
        assert assignment_target_reason(fresh, target) == "monitoring_configuration_changed"
        report = runtime._scan_target(fresh, target)
        assert report.status == "blocked" and report.candidate_ids == ()
        with (
            pytest.raises(WatcherEvidenceUnavailableError),
            AssignmentCandidateFence(fresh, target, None).bind_from_evaluation_command(None),
        ):
            pytest.fail("Stale inferred or assigned target cannot publish")


def test_inferred_overlay_revalidates_fresh_actor_membership(factory):
    with factory() as db:
        _seed_approved_compiled(db)
    (target,) = load(factory)
    with factory() as cached:
        member = cached.scalar(select(Membership).where(Membership.user_id == USER))
        assert member.role is MembershipRole.OWNER
        cached.commit()
        with factory() as writer:
            changed = writer.get(Membership, member.id)
            changed.role = MembershipRole.VIEWER
            writer.commit()
        assert assignment_target_reason(cached, target) == "strategy_binding_unavailable"
        foreign = replace(target, organization_id=ORG_B, user_id=USER_B)
        assert assignment_target_reason(cached, foreign) == "strategy_binding_unavailable"


def test_explicit_overlay_only_skips_execution_for_its_own_target(factory, monkeypatch):
    with factory() as db:
        _, assigned = _seed_approved_compiled(db)
        _, unrelated = _seed_approved_compiled(db)
        save(db, body(db, [assigned]))
    calls = []
    runtime, _, side_effects, _ = _runtime(factory)
    runtime._watchlist_mode = True
    runtime._tenant_watchlists = True

    def existing_continuation(session, target, report, evidence):
        assert target.assignment_id is None
        calls.append(target.strategy_version_id)
        return report

    monkeypatch.setattr(runtime, "_continue_paper_loop", existing_continuation)
    scans = runtime.run_cycle().scans
    assert len(scans) == 2 and all(scan.status == "succeeded" for scan in scans)
    assert calls == [unrelated]
    assert side_effects.unused


def test_first_assignment_during_load_keeps_original_revision_and_refuses_stale_target(
    factory, monkeypatch
):
    from app.workers import watcher_paper

    with factory() as db:
        _, version = _seed_approved_compiled(db)
        original = body(db, [version])
    loader = watcher_paper.list_watchlist_scan_targets

    def load_then_add(*args, **kwargs):
        targets = loader(*args, **kwargs)
        with factory() as writer:
            save(writer, original)
        return targets

    monkeypatch.setattr(watcher_paper, "list_watchlist_scan_targets", load_then_add)
    (target,) = load(factory)
    assert target.configuration_revision == 0
    with factory() as fresh:
        assert assignment_target_reason(fresh, target) == "monitoring_configuration_changed"


@pytest.mark.parametrize("operation", ["first_add", "pause"])
def test_changed_authority_after_candidate_publish_cancels_continuation_and_notification(
    factory, monkeypatch, operation
):
    from app.workers import watcher_paper

    with factory() as db:
        _, version = _seed_approved_compiled(db)
        original = body(db, [version])
        if operation == "pause":
            save(db, original)
    (target,) = load(factory)
    runtime, _, side_effects, _ = _runtime(factory)
    delivered = []
    monkeypatch.setattr(runtime, "_notify_scan", delivered.append)
    monkeypatch.setattr(
        runtime, "_continue_paper_loop", lambda *args: pytest.fail("No stale continuation")
    )
    report_from_cycle = watcher_paper._report_from_cycle

    def publish_then_change(**kwargs):
        report = report_from_cycle(**kwargs)
        assert report.published and report.candidate_ids  # Valid historical publication occurred.
        request = (
            original
            if operation == "first_add"
            else original.model_copy(
                update={
                    "revision": 1,
                    "request_key": str(uuid4()),
                    "assignments": [
                        original.assignments[0].model_copy(update={"monitoring_enabled": False})
                    ],
                }
            )
        )
        with factory() as writer:
            save(writer, request)
        return report

    monkeypatch.setattr(watcher_paper, "_report_from_cycle", publish_then_change)
    with factory() as scan:
        result = runtime._scan_one_isolated(scan, target)
    assert result.status == "blocked" and result.reason_code == "monitoring_configuration_changed"
    assert not result.published and delivered == []
    assert side_effects.unused


def test_watchlist_disable_and_reenable_preserves_explicit_and_unrelated_bindings(factory):
    with factory() as db:
        _, assigned = _seed_approved_compiled(db)
        _, unrelated = _seed_approved_compiled(db)
        save(db, body(db, [assigned]))
        repo = WatcherWatchlistRepository(db)
        config = repo.load(ORG)
        repo.replace(ORG, [(slot.symbol, False) for slot in config.slots], expected_revision=1)
        db.commit()
    assert load(factory) == ()
    with factory() as reenable:
        repo = WatcherWatchlistRepository(reenable)
        config = repo.load(ORG)
        repo.replace(ORG, [(slot.symbol, True) for slot in config.slots], expected_revision=2)
        reenable.commit()
    targets = load(factory)
    assert {target.strategy_version_id for target in targets} == {assigned, unrelated}
    assert len(targets) == 2


def test_pinned_assignment_does_not_follow_new_approved_current_version_or_resume_on_removal(
    factory,
):
    from app.db.models import UserStrategyVersion
    from app.schemas.common import StrategyChangeSource
    from app.services.compiled_setup_service import CompiledSetupService
    from app.services.strategy_versioning import StrategyVersioningService
    from tests.test_watcher_paper_runtime import _approve

    with factory() as db:
        strategy, pinned = _seed_approved_compiled(db)
        save(db, body(db, [pinned]))
        prior = db.get(UserStrategyVersion, pinned)
        newer = StrategyVersioningService(db).fork_semantic_update(
            strategy,
            parent=prior,
            card=prior.card,
            structured_rules=prior.structured_rules,
            lesson_source_metadata=prior.lesson_source_metadata,
            pattern_spec={**prior.pattern_spec, "expiry_final_bars": 3},
            actor_user_id=USER,
            source=StrategyChangeSource.PATTERN_SPEC,
            reason="Independent explicit approval of later authored version",
        )
        db.flush()
        compiled = CompiledSetupService(db).compile_version(
            newer.id, organization_id=ORG, user_id=USER
        )
        assert compiled.compiled is not None
        _approve(db, strategy, newer.id, user_id=USER)
        db.commit()
        assert strategy.current_version == newer.version
    (target,) = load(factory)
    assert target.strategy_version_id == pinned and target.assignment_id is not None
    with factory() as remove:
        save(remove, body(remove, revision=1))
    assert load(factory) == ()


def test_portable_receipt_history_keeps_scoped_tombstone():
    from app.db.models import Organization, User
    from tests.test_watcher_paper_runtime import _sqlite_factory

    factory = _sqlite_factory()
    try:
        with factory() as db:
            db.add_all(
                [
                    Organization(id=ORG, name="Portable scoped history"),
                    User(id=USER, email="portable@test.example", hashed_password="unused"),
                ]
            )
            db.flush()
            db.add(Membership(organization_id=ORG, user_id=USER, role=MembershipRole.TRADER))
            db.commit()
            _, assigned = _seed_approved_compiled(db)
            _, unrelated = _seed_approved_compiled(db)
            save(db, body(db, [assigned]))
            save(db, body(db, revision=1))
        (target,) = load(factory)
        assert target.strategy_version_id == unrelated
        with factory() as fresh:
            assert assignment_target_reason(fresh, target) is None
    finally:
        factory.kw["bind"].dispose()
