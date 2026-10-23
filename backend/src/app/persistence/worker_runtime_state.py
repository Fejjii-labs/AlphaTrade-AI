"""Publish and project the paper worker's effective flags.

A stored row is not a live worker. ``health_state`` is ``RUNNING`` only while
the heartbeat age is within the threshold. The API must not substitute its own
env for these flags once a row exists. Real trading stays false on the projection.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from app.db.worker_runtime_state import WORKER_EFFECTIVE_COMPONENT, WorkerRuntimeStateRow
from app.persistence.runtime_health import classify_heartbeat
from app.schemas.health import WorkerEffectiveObservation

_SYMBOL = re.compile(r"^[A-Z0-9]{2,32}$")
_MODE = re.compile(r"^(off|internal_paper|blofin_demo|refused:[a-z0-9_]{1,40})$")


@dataclass(frozen=True, slots=True)
class WorkerRuntimeWrite:
    """Fields the worker may publish. Watchlist symbols are public tickers."""

    worker_id: str
    heartbeat_at: datetime
    autonomous_internal_paper_env_ceiling: bool
    watcher_orchestration_enabled: bool
    watcher_paper_staging_activation: bool
    telegram_paper_activation_armed: bool
    telegram_interaction_enabled: bool
    telegram_alerts_enabled: bool
    automatic_telegram_delivery_enabled: bool
    preserve_cycle: bool = True
    autonomy_mode: str = "off"
    autonomous_internal_paper_db_enabled: bool = False
    last_cycle_at: datetime | None = None
    watchlist: tuple[str, ...] = ()
    organization_id: uuid.UUID | None = None


def publish_worker_runtime_state(
    session_factory: sessionmaker[Session],
    snapshot: WorkerRuntimeWrite,
) -> None:
    """Upsert the worker row. ``preserve_cycle`` keeps the last cycle details."""

    with session_factory() as session, session.begin():
        # SQLite has no row locks. Postgres locks the singleton so two workers
        # cannot clobber a cycle publish.
        lock = session.get_bind().dialect.name == "postgresql"
        row = session.get(
            WorkerRuntimeStateRow,
            WORKER_EFFECTIVE_COMPONENT,
            with_for_update=lock,
        )
        if row is None:
            session.add(_row_from_snapshot(snapshot))
            return
        _apply_snapshot(row, snapshot)


def load_worker_runtime_row() -> WorkerRuntimeStateRow | None:
    """The published row, or None when it is missing or the database cannot be read."""

    try:
        from app.db.session import get_session_factory

        with get_session_factory()() as session:
            return session.get(WorkerRuntimeStateRow, WORKER_EFFECTIVE_COMPONENT)
    except Exception:
        return None


def load_worker_runtime_from_session(session: Session) -> WorkerRuntimeStateRow | None:
    try:
        return session.get(WorkerRuntimeStateRow, WORKER_EFFECTIVE_COMPONENT)
    except Exception:
        return None


def project_worker_effective(
    row: object | None,
    *,
    now: datetime,
    stale_after_seconds: int,
) -> WorkerEffectiveObservation:
    """Project one row. A missing row is unavailable, not the API process env."""

    limit = stale_after_seconds
    if row is None:
        return WorkerEffectiveObservation(heartbeat_stale_after_seconds=limit)
    heartbeat_at = _aware(getattr(row, "heartbeat_at", None))
    health_state, age = classify_heartbeat(
        heartbeat_at,
        now=now,
        stale_after_seconds=limit,
    )
    ceiling = bool(getattr(row, "autonomous_internal_paper_env_ceiling", False))
    db_enabled = bool(getattr(row, "autonomous_internal_paper_db_enabled", False))
    mode = _mode(getattr(row, "autonomy_mode", "off"))
    effective = mode == "internal_paper" and ceiling and db_enabled
    last_cycle = _aware(getattr(row, "last_cycle_at", None))
    return WorkerEffectiveObservation(
        source="worker",
        health_state=health_state,
        stale=health_state != "RUNNING",
        heartbeat_at=heartbeat_at,
        heartbeat_age_seconds=age,
        heartbeat_stale_after_seconds=limit,
        last_cycle_at=last_cycle,
        autonomy_mode=mode,
        autonomous_internal_paper_env_ceiling=ceiling,
        autonomous_internal_paper_db_enabled=db_enabled,
        autonomous_internal_paper_effective=effective,
        watcher_orchestration_enabled=bool(getattr(row, "watcher_orchestration_enabled", False)),
        watcher_paper_staging_activation=bool(
            getattr(row, "watcher_paper_staging_activation", False)
        ),
        telegram_paper_activation_armed=bool(
            getattr(row, "telegram_paper_activation_armed", False)
        ),
        telegram_interaction_enabled=bool(getattr(row, "telegram_interaction_enabled", False)),
        telegram_alerts_enabled=bool(getattr(row, "telegram_alerts_enabled", False)),
        automatic_telegram_delivery_enabled=bool(
            getattr(row, "automatic_telegram_delivery_enabled", False)
        ),
        watchlist=_watchlist(getattr(row, "watchlist", ())),
        real_trading_enabled=False,
    )


def org_paper_effective(observation: WorkerEffectiveObservation, org_enabled: bool) -> bool:
    """Whether this organization's switch is live against the worker ceiling.

    A stale or missing heartbeat is not effective. A process-level refusal
    blocks every organization. Another organization's disarmed cycle does not.
    """

    if observation.source != "worker" or observation.health_state != "RUNNING":
        return False
    if not observation.autonomous_internal_paper_env_ceiling or not org_enabled:
        return False
    if observation.autonomy_mode == "internal_paper":
        return True
    if observation.autonomy_mode.startswith("refused:"):
        return False
    return observation.autonomy_mode == "off"


def sanitize_watchlist(symbols: object) -> tuple[str, ...]:
    return tuple(_watchlist(symbols))


def sanitize_autonomy_mode(mode: str) -> str:
    return _mode(mode)


def _row_from_snapshot(snapshot: WorkerRuntimeWrite) -> WorkerRuntimeStateRow:
    mode = _mode(snapshot.autonomy_mode)
    ceiling = bool(snapshot.autonomous_internal_paper_env_ceiling)
    db_enabled = bool(snapshot.autonomous_internal_paper_db_enabled)
    return WorkerRuntimeStateRow(
        component=WORKER_EFFECTIVE_COMPONENT,
        worker_id=snapshot.worker_id[:80],
        heartbeat_at=snapshot.heartbeat_at,
        last_cycle_at=snapshot.last_cycle_at,
        autonomy_mode=mode,
        autonomous_internal_paper_env_ceiling=ceiling,
        autonomous_internal_paper_db_enabled=db_enabled,
        autonomous_internal_paper_effective=mode == "internal_paper" and ceiling and db_enabled,
        watcher_orchestration_enabled=bool(snapshot.watcher_orchestration_enabled),
        watcher_paper_staging_activation=bool(snapshot.watcher_paper_staging_activation),
        telegram_paper_activation_armed=bool(snapshot.telegram_paper_activation_armed),
        telegram_interaction_enabled=bool(snapshot.telegram_interaction_enabled),
        telegram_alerts_enabled=bool(snapshot.telegram_alerts_enabled),
        automatic_telegram_delivery_enabled=bool(snapshot.automatic_telegram_delivery_enabled),
        watchlist=list(_watchlist(snapshot.watchlist)),
        organization_id=snapshot.organization_id,
    )


def _apply_snapshot(row: WorkerRuntimeStateRow, snapshot: WorkerRuntimeWrite) -> None:
    row.worker_id = snapshot.worker_id[:80]
    row.heartbeat_at = snapshot.heartbeat_at
    row.autonomous_internal_paper_env_ceiling = bool(snapshot.autonomous_internal_paper_env_ceiling)
    row.watcher_orchestration_enabled = bool(snapshot.watcher_orchestration_enabled)
    row.watcher_paper_staging_activation = bool(snapshot.watcher_paper_staging_activation)
    row.telegram_paper_activation_armed = bool(snapshot.telegram_paper_activation_armed)
    row.telegram_interaction_enabled = bool(snapshot.telegram_interaction_enabled)
    row.telegram_alerts_enabled = bool(snapshot.telegram_alerts_enabled)
    row.automatic_telegram_delivery_enabled = bool(snapshot.automatic_telegram_delivery_enabled)
    if snapshot.preserve_cycle:
        return
    mode = _mode(snapshot.autonomy_mode)
    db_enabled = bool(snapshot.autonomous_internal_paper_db_enabled)
    ceiling = bool(snapshot.autonomous_internal_paper_env_ceiling)
    row.autonomy_mode = mode
    row.autonomous_internal_paper_db_enabled = db_enabled
    row.autonomous_internal_paper_effective = mode == "internal_paper" and ceiling and db_enabled
    row.last_cycle_at = snapshot.last_cycle_at
    row.watchlist = list(_watchlist(snapshot.watchlist))
    row.organization_id = snapshot.organization_id


def _aware(value: object) -> datetime | None:
    """UTC-aware timestamp. SQLite returns naive values for timezone columns."""

    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value


def _watchlist(symbols: object) -> list[str]:
    if isinstance(symbols, str) or not isinstance(symbols, (list, tuple)):
        return []
    chosen: list[str] = []
    for item in symbols:
        if not isinstance(item, str):
            continue
        token = item.strip().upper()
        if _SYMBOL.fullmatch(token) and token not in chosen:
            chosen.append(token)
        if len(chosen) >= 64:
            break
    return chosen


def _mode(value: object) -> str:
    if not isinstance(value, str) or _MODE.fullmatch(value) is None:
        return "off"
    return value
