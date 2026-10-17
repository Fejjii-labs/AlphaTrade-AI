"""ORM and generated PostgreSQL guards for frozen configuration and historical facts."""

from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.engine import Connection

from app.core.errors import ConflictError
from app.experiments.models import (
    ExperimentEventRow,
    ExperimentRow,
    ExperimentSampleRow,
    ExperimentVersionRow,
)

TRANSITIONS = {
    "draft": {"pending_approval"},
    "pending_approval": {"approved"},
    "approved": {"running"},
    "running": {"paused", "completed"},
    "paused": {"running", "completed"},
    "completed": {"promoted"},
    "promoted": set(),
}
LIFECYCLE = {
    "state",
    "revision",
    "submitted_at",
    "approved_at",
    "approved_by",
    "authorized_until",
    "started_at",
    "paused_at",
    "completed_at",
    "promoted_at",
    "promotion_version_id",
}
APPROVAL = {"approved_at", "approved_by", "authorized_until"}
STAMP = {
    "pending_approval": "submitted_at",
    "running": "started_at",
    "paused": "paused_at",
    "completed": "completed_at",
    "promoted": "promoted_at",
}


def _immutable(*_args: Any) -> None:
    raise ConflictError("Experiment history is append-only.", code="experiment_immutable")


def _update_version(_mapper: Any, _connection: Any, row: ExperimentVersionRow) -> None:
    attrs = inspect(row).attrs
    changed = {a.key for a in attrs if a.history.has_changes()}
    old_state = attrs.state.history.deleted[0] if attrs.state.history.deleted else row.state
    old_revision = (
        attrs.revision.history.deleted[0] if attrs.revision.history.deleted else row.revision
    )
    if (
        changed - LIFECYCLE
        or row.state not in TRANSITIONS.get(old_state, set())
        or row.revision != old_revision + 1
        or (changed & APPROVAL and (old_state, row.state) != ("pending_approval", "approved"))
        or (
            changed
            & (set(STAMP.values()) | {"promotion_version_id"})
            - ({STAMP[row.state]} if row.state in STAMP else set())
            - ({"promotion_version_id"} if row.state == "promoted" else set())
        )
        or (
            "started_at" in changed
            and attrs.started_at.history.deleted
            and attrs.started_at.history.deleted[0] is not None
        )
    ):
        _immutable()


def _insert_version(_mapper: Any, _connection: Any, row: ExperimentVersionRow) -> None:
    if (
        row.state != "draft"
        or row.revision != 0
        or any(getattr(row, name) is not None for name in LIFECYCLE - {"state", "revision"})
    ):
        _immutable()


def _update_root(_mapper: Any, _connection: Any, row: ExperimentRow) -> None:
    changed = {a.key for a in inspect(row).attrs if a.history.has_changes()}
    if changed - {"latest_version"}:
        _immutable()


def pg_guard_statements() -> tuple[str, ...]:
    """Render fixed SQL into the generated additive migration, not a mutable import."""
    allowed = ",".join("'" + name + "'" for name in sorted(LIFECYCLE))
    pairs = " OR ".join(
        f"(OLD.state = '{before}' AND NEW.state = '{after}')"
        for before, states in TRANSITIONS.items()
        for after in sorted(states)
    )
    stamp_guards = "\n".join(
        f"  IF NEW.{name} IS DISTINCT FROM OLD.{name} AND "
        f"NEW.state <> '{state}' THEN "
        f"RAISE EXCEPTION 'experiment_immutable:{name}'; END IF;"
        for state, name in STAMP.items()
    )
    return (
        """CREATE FUNCTION experiment_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION USING MESSAGE = 'experiment_immutable:' || TG_TABLE_NAME; END; $$""",
        f"""CREATE FUNCTION experiment_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'experiment_immutable:DELETE'; END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.state <> 'draft' OR NEW.revision <> 0 OR NEW.approved_at IS NOT NULL
       OR NEW.approved_by IS NOT NULL OR NEW.authorized_until IS NOT NULL
       OR NEW.started_at IS NOT NULL OR NEW.submitted_at IS NOT NULL
       OR NEW.paused_at IS NOT NULL OR NEW.completed_at IS NOT NULL
       OR NEW.promoted_at IS NOT NULL OR NEW.promotion_version_id IS NOT NULL THEN
      RAISE EXCEPTION 'experiment_immutable:initial_state';
    END IF;
    RETURN NEW;
  END IF;
  IF (to_jsonb(NEW) - ARRAY[{allowed}]) IS DISTINCT FROM
     (to_jsonb(OLD) - ARRAY[{allowed}]) THEN
    RAISE EXCEPTION 'experiment_immutable:configuration';
  END IF;
  IF NEW.revision <> OLD.revision + 1 OR NOT ({pairs}) THEN
    RAISE EXCEPTION 'experiment_immutable:lifecycle';
  END IF;
  IF (OLD.state <> 'pending_approval' OR NEW.state <> 'approved') AND
     ROW(NEW.approved_at, NEW.approved_by, NEW.authorized_until) IS DISTINCT FROM
     ROW(OLD.approved_at, OLD.approved_by, OLD.authorized_until) THEN
    RAISE EXCEPTION 'experiment_immutable:approval';
  END IF;
{stamp_guards}
  IF OLD.started_at IS NOT NULL AND NEW.started_at IS DISTINCT FROM OLD.started_at THEN
    RAISE EXCEPTION 'experiment_immutable:started_at';
  END IF;
  IF NEW.promotion_version_id IS DISTINCT FROM OLD.promotion_version_id
     AND NEW.state <> 'promoted' THEN
    RAISE EXCEPTION 'experiment_immutable:promotion_version_id';
  END IF;
  IF NEW.state IN ('approved','running','paused','completed','promoted') AND
     (NEW.approved_at IS NULL OR NEW.approved_by IS NULL OR NEW.authorized_until IS NULL
      OR NEW.authorized_until <= NEW.approved_at
      OR NEW.authorized_until > NEW.approved_at + interval '30 days') THEN
    RAISE EXCEPTION 'experiment_immutable:approval_envelope';
  END IF;
  RETURN NEW;
END; $$""",
        """CREATE FUNCTION experiment_root_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' OR
     (to_jsonb(NEW) - 'latest_version') IS DISTINCT FROM
     (to_jsonb(OLD) - 'latest_version') THEN
    RAISE EXCEPTION 'experiment_immutable:root';
  END IF;
  IF NEW.latest_version <> OLD.latest_version + 1 THEN
    RAISE EXCEPTION 'experiment_immutable:version_sequence';
  END IF;
  RETURN NEW;
END; $$""",
        "CREATE TRIGGER experiment_versions_guard BEFORE INSERT OR UPDATE OR DELETE "
        "ON experiment_versions FOR EACH ROW EXECUTE FUNCTION experiment_version_guard()",
        "CREATE TRIGGER experiments_guard BEFORE UPDATE OR DELETE "
        "ON experiments FOR EACH ROW EXECUTE FUNCTION experiment_root_guard()",
        *(
            f"CREATE TRIGGER {table}_guard BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION experiment_append_only()"
            for table in ("experiment_events", "experiment_samples")
        ),
    )


def install_pg_guards(connection: Connection) -> None:
    if connection.dialect.name == "postgresql":
        for statement in pg_guard_statements():
            connection.exec_driver_sql(statement)


def _after_create(_target: Any, connection: Connection, **_kw: Any) -> None:
    install_pg_guards(connection)


event.listen(ExperimentVersionRow, "before_insert", _insert_version)
event.listen(ExperimentVersionRow, "before_update", _update_version)
event.listen(ExperimentRow, "before_update", _update_root)
for _model in (ExperimentRow, ExperimentVersionRow, ExperimentEventRow, ExperimentSampleRow):
    event.listen(_model, "before_delete", _immutable)
for _model in (ExperimentEventRow, ExperimentSampleRow):
    event.listen(_model, "before_update", _immutable)
event.listen(ExperimentSampleRow.__table__, "after_create", _after_create)
