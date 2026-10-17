"""Frozen lifecycle, independent samples and tenant boundaries on disposable PostgreSQL."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.errors import AppError, ConflictError, ForbiddenError, NotFoundError
from app.experiments.models import ExperimentEventRow, ExperimentVersionRow
from app.schemas.common import MembershipRole
from app.schemas.experiments import (
    ExperimentConfiguration,
    ExperimentCreate,
    ExperimentPromotion,
    ExperimentSampleCreate,
    ExperimentTransition,
    ExperimentVersionCreate,
)
from app.schemas.nested_continuation import NestedParameters
from tests.support.experiment_fixtures import World
from tests.support.experiment_fixtures import (
    experiment_engine as _experiment_engine,  # noqa: F401
)
from tests.support.experiment_fixtures import (
    experiment_world as _experiment_world,  # noqa: F401
)
from tests.test_sfp_detector import spec as sfp_spec


def test_lifecycle_is_versioned_and_never_activates_runtime(experiment_world):
    w = experiment_world
    draft = w.create()
    assert (draft.state, draft.revision, draft.sample_counts) == ("draft", 0, {"baseline": 0})
    assert draft.runtime_activated is False and draft.performance is None
    with pytest.raises(ConflictError):
        w.transition(draft, "start")
    submitted = w.transition(draft, "submit")
    approved = w.approve(submitted)
    running = w.transition(approved, "start")
    paused = w.transition(running, "pause")
    resumed = w.transition(paused, "start")
    assert resumed.started_at == running.started_at
    completed = w.transition(resumed, "complete")
    assert completed.completed_at > completed.started_at
    events = w.session.scalars(
        select(ExperimentEventRow).order_by(ExperimentEventRow.revision)
    ).all()
    assert [e.state for e in events] == [
        "draft",
        "pending_approval",
        "approved",
        "running",
        "paused",
        "running",
        "completed",
    ]
    assert len({e.configuration_hash for e in events}) == 1
    with pytest.raises(ConflictError):
        w.transition(completed, "start")
    with pytest.raises(ConflictError):
        w.service.transition(
            w.tenant,
            draft.experiment_id,
            draft.id,
            ExperimentTransition(action="submit", expected_revision=0),
        )


def test_approval_is_exact_owner_bounded_and_expiry_blocks_resume(experiment_world):
    w = experiment_world
    submitted = w.transition(w.create(), "submit")
    w.membership.role = MembershipRole.TRADER
    w.session.flush()
    with pytest.raises(ForbiddenError):
        w.approve(submitted)
    w.membership.role = MembershipRole.OWNER
    w.session.flush()
    for updates in (
        {"configuration_hash": "f" * 64},
        {"authorized_until": w.now - timedelta(seconds=1)},
        {"authorized_until": w.now + timedelta(days=31)},
        {"expected_revision": 0},
    ):
        with pytest.raises(ConflictError):
            w.approve(submitted, **updates)
    running = w.transition(w.approve(submitted), "start")
    paused = w.transition(running, "pause")
    w.now = paused.authorized_until
    with pytest.raises(ConflictError, match="expired"):
        w.transition(paused, "start")
    with pytest.raises(ConflictError):
        w.approve(paused)


def test_idempotent_create_preserves_identity_and_changed_request_conflicts(experiment_world):
    w = experiment_world
    body = ExperimentCreate(name="A", idempotency_key="same", configuration=w.configuration())
    a = w.service.create(w.tenant, body)
    b = w.service.create(w.tenant, body)
    assert a.id == b.id and a.configuration_hash == b.configuration_hash
    with pytest.raises(ConflictError):
        w.service.create(w.tenant, body.model_copy(update={"name": "B"}))


@pytest.mark.parametrize("state", ["draft", "running", "paused", "completed"])
def test_configuration_immutable_at_orm_and_sql_boundary(experiment_world, state):
    w = experiment_world
    v = w.create() if state == "draft" else w.running()
    if state == "paused":
        v = w.transition(v, "pause")
    if state == "completed":
        v = w.transition(v, "complete")
    with pytest.raises(ConflictError), w.session.begin_nested():
        row = w.session.get(ExperimentVersionRow, v.id)
        row.configuration = {**row.configuration, "mode": "validation"}
        w.session.flush()
    for statement in (
        "UPDATE experiment_versions SET configuration_hash = :hash WHERE id = :id",
        "DELETE FROM experiment_versions WHERE id = :id",
        "UPDATE experiment_events SET configuration_hash = :hash WHERE version_id = :id",
        "DELETE FROM experiment_events WHERE version_id = :id",
    ):
        with pytest.raises(DBAPIError, match="experiment_immutable"), w.session.begin_nested():
            w.session.execute(text(statement), {"id": v.id, "hash": "f" * 64})
    assert w.service.detail(w.tenant, v.experiment_id).versions[0].configuration == v.configuration


def test_sql_cannot_rewrite_start_time_during_legal_transition(experiment_world):
    w = experiment_world
    v = w.running()
    with (
        pytest.raises(DBAPIError, match="experiment_immutable:started_at"),
        w.session.begin_nested(),
    ):
        w.session.execute(
            text(
                "UPDATE experiment_versions SET state='paused', revision=revision+1, "
                "started_at=started_at - interval '1 day' WHERE id=:id"
            ),
            {"id": v.id},
        )


def test_account_strategy_and_read_isolation(experiment_world):
    w = experiment_world
    v = w.create()
    for tenant in (replace(w.tenant, organization_id=uuid4()), replace(w.tenant, user_id=uuid4())):
        assert w.service.list(tenant).total == 0
        with pytest.raises(NotFoundError):
            w.service.detail(tenant, v.experiment_id)
    bad_account = w.configuration(
        account={"source": "internal_simulation", "execution_account_id": uuid4()}
    )
    with pytest.raises(NotFoundError):
        w.create(bad_account)
    with pytest.raises(NotFoundError):
        w.create(w.configuration(strategy_id=uuid4()))
    w.account.enabled = False
    w.session.flush()
    with pytest.raises(ConflictError, match="enabled"):
        w.transition(v, "submit")


def test_parent_lineage_cannot_cross_experiment_or_tenant_in_sql(experiment_world):
    w = experiment_world
    v = w.create()
    foreign = World(w.session).create()
    original = w.session.get(ExperimentVersionRow, v.id)
    values = {column.name: getattr(original, column.name) for column in original.__table__.columns}
    values.update(id=uuid4(), sample_group_id=uuid4(), version=2, parent_version_id=foreign.id)
    with pytest.raises(IntegrityError), w.session.begin_nested():
        w.session.add(ExperimentVersionRow(**values))
        w.session.flush()


def test_new_configuration_uses_new_strategy_version_not_parameter_override(experiment_world):
    w = experiment_world
    config = w.configuration()
    parameters = {**config.variants[0].parameters, "pivot_sensitivity": 3}
    bad = config.model_dump()
    bad["variants"][0]["parameters"] = parameters
    with pytest.raises(ConflictError, match="immutable strategy"):
        w.create(ExperimentConfiguration.model_validate(bad))
    spec = w.spec.model_copy(update={"parameters": NestedParameters(pivot_sensitivity=3)})
    version = w.add_strategy_version(spec, 2)
    parent = w.running()
    new = w.configuration(
        strategy_version_id=version.id,
        variants=[
            {
                "key": "updated",
                "strategy_version_id": version.id,
                "parameters": spec.parameters.model_dump(mode="json"),
            }
        ],
    )
    child = w.service.fork(
        w.tenant,
        parent.experiment_id,
        ExperimentVersionCreate(parent_version_id=parent.id, configuration=new),
    )
    assert child.version == 2 and child.state == "draft"
    assert child.configuration_hash != parent.configuration_hash
    assert child.sample_group_id != parent.sample_group_id
    assert child.approved_at is None
    with pytest.raises(ConflictError, match="Universe"):
        w.create(w.configuration(symbols=["ETHUSDT"]))


@pytest.mark.parametrize(
    "field,value",
    [
        ("organization_id", uuid4()),
        ("execution_account_id", uuid4()),
        ("source", "blofin_demo"),
        ("version_id", uuid4()),
        ("configuration_hash", "f" * 64),
        ("sample_group_id", uuid4()),
        ("strategy_version_id", uuid4()),
        ("variant_key", "other"),
        ("kind", "setup_observation"),
        ("source_record_id", "other"),
    ],
)
def test_trusted_samples_require_every_attribution_binding(experiment_world, field, value):
    w = experiment_world
    v = w.running()
    w.resolver.overrides[field] = value
    if field == "source":
        w.resolver.overrides.update(
            native_uid="native-b",
            ownership_command_id=uuid4(),
            native_entry_order_id="entry",
            native_exit_order_id="exit",
        )
    with pytest.raises(ConflictError, match="does not belong"):
        w.sample(v)


def test_paused_window_closure_retries_and_append_only_samples(experiment_world):
    w = experiment_world
    v = w.running()
    sample = w.sample(v, "one")
    w.resolver.overrides.update(opened_at=sample.opened_at, completed_at=sample.completed_at)
    assert w.sample(v, "one").id == sample.id
    paused = w.transition(v, "pause")
    w.resolver.overrides.update(opened_at=v.started_at, completed_at=w.now)
    late = w.sample(paused, "late-close")
    assert late.id != sample.id
    w.resolver.overrides.clear()
    with pytest.raises(ConflictError, match="running interval"):
        w.sample(paused, "paused-open")
    for sql in (
        "UPDATE experiment_samples SET variant_key='other' WHERE id=:id",
        "DELETE FROM experiment_samples WHERE id=:id",
    ):
        with pytest.raises(DBAPIError, match="experiment_immutable"), w.session.begin_nested():
            w.session.execute(text(sql), {"id": sample.id})


@pytest.mark.parametrize("window", ["before_start", "after_expiry", "future_close"])
def test_sample_window_limits(experiment_world, window):
    w = experiment_world
    v = w.running()
    if window == "before_start":
        w.resolver.overrides["opened_at"] = v.started_at - timedelta(seconds=1)
    elif window == "after_expiry":
        w.now = v.authorized_until + timedelta(seconds=3)
    else:
        w.resolver.overrides["completed_at"] = w.now + timedelta(hours=1)
    with pytest.raises(ConflictError, match="running interval"):
        w.sample(v)


def test_source_adapter_unavailable_and_sample_maximum(experiment_world):
    w = experiment_world
    v = w.running(
        w.configuration(sample_target={"kind": "closed_trade", "minimum": 1, "maximum": 1})
    )
    w.service.source_resolver = None
    with pytest.raises(AppError) as exc:
        w.sample(v)
    assert exc.value.status_code == 503
    w.service.source_resolver = w.resolver
    w.sample(v)
    with pytest.raises(ConflictError, match="cap"):
        w.sample(v)


def test_promotion_requires_sample_and_creates_unapproved_fresh_validation(experiment_world):
    w = experiment_world
    v = w.running()
    w.sample(v, "exploration-only")
    parent = w.transition(v, "complete")
    body = ExperimentPromotion(expected_revision=parent.revision, variant_key="baseline")
    child = w.service.promote(w.tenant, parent.experiment_id, parent.id, body)
    assert child.configuration.mode == "validation" and child.version == 2
    assert child.state == "draft" and child.approved_at is None
    assert child.sample_group_id != parent.sample_group_id
    assert child.configuration_hash != parent.configuration_hash
    assert child.sample_counts == {"baseline": 0}
    assert child.configuration.variants == parent.configuration.variants
    assert w.service.promote(w.tenant, parent.experiment_id, parent.id, body).id == child.id
    child = w.transition(w.approve(w.transition(child, "submit")), "start")
    with pytest.raises(ConflictError, match="already assigned"):
        w.sample(child, "exploration-only")
    w.sample(child, "fresh-validation")
    child = w.transition(child, "complete")
    with pytest.raises(ConflictError, match="Only completed Exploration"):
        w.service.promote(
            w.tenant,
            child.experiment_id,
            child.id,
            ExperimentPromotion(expected_revision=child.revision, variant_key="baseline"),
        )


def test_insufficient_sample_prevents_promotion(experiment_world):
    w = experiment_world
    v = w.transition(w.running(), "complete")
    with pytest.raises(ConflictError, match="insufficient"):
        w.service.promote(
            w.tenant,
            v.experiment_id,
            v.id,
            ExperimentPromotion(expected_revision=v.revision, variant_key="baseline"),
        )


def test_sfp_preserves_observation_only_semantics(experiment_world):
    w = experiment_world
    w.spec = sfp_spec()
    w.strategy_version = w.add_strategy_version(w.spec, 2)
    with pytest.raises(ConflictError, match="no authorized automatic trade plan"):
        w.create()
    v = w.running(
        w.configuration(sample_target={"kind": "setup_observation", "minimum": 1, "maximum": 3})
    )
    assert w.sample(v).kind == "setup_observation"
    assert v.performance is None


@pytest.mark.parametrize(
    "update",
    [
        {"model_policy": {"mode": "advisory"}},
        {"model_policy": {"mode": "disabled", "max_calls": 1}},
        {"sample_target": {"kind": "closed_trade", "minimum": 10, "maximum": 1}},
        {"account": {"source": "blofin_demo", "execution_account_id": str(uuid4())}},
        {
            "account": {
                "source": "internal_simulation",
                "execution_account_id": str(uuid4()),
                "native_uid": "spoof",
            }
        },
    ],
)
def test_contract_rejects_unbounded_or_ambiguous_configuration(experiment_world, update):
    with pytest.raises(ValidationError):
        experiment_world.configuration(**update)


def test_http_sample_contract_cannot_accept_pnl_or_origin_claims():
    for extra in ({"pnl": "100"}, {"source": "blofin_demo"}, {"native_uid": "spoof"}):
        with pytest.raises(ValidationError):
            ExperimentSampleCreate.model_validate(
                {"variant_key": "baseline", "source_record_id": "one", **extra}
            )
