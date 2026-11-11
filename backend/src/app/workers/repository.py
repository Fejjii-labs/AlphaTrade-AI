"""Persistence for worker heartbeats, scan runs, and setup detections."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models import MarketScanRun, SetupDetectionRecord, WorkerHeartbeat


class WorkerHeartbeatRepository:
    """Upsert-by-name access to a worker's single heartbeat row."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_name(self, worker_name: str) -> WorkerHeartbeat | None:
        stmt = select(WorkerHeartbeat).where(WorkerHeartbeat.worker_name == worker_name)
        return self._session.scalars(stmt).first()

    def set_paused(self, *, worker_name: str, paused: bool) -> WorkerHeartbeat:
        """Change control state without claiming a worker heartbeat or cycle status."""
        row = self.get_by_name(worker_name)
        if row is None:
            row = WorkerHeartbeat(
                worker_name=worker_name,
                status="unknown",
                # The schema requires a timestamp; epoch is deliberately stale.
                last_beat_at=datetime(1970, 1, 1, tzinfo=UTC),
                paused=paused,
            )
            self._session.add(row)
        else:
            row.paused = paused
        self._session.flush()
        return row

    def upsert(
        self,
        *,
        worker_name: str,
        status: str,
        last_beat_at: datetime,
        cycle_count: int | None = None,
        paused: bool | None = None,
        detail: str | None = None,
    ) -> WorkerHeartbeat:
        row = self.get_by_name(worker_name)
        if row is None:
            row = WorkerHeartbeat(
                worker_name=worker_name,
                status=status,
                last_beat_at=last_beat_at,
                cycle_count=cycle_count or 0,
                paused=bool(paused),
                detail=detail,
            )
            self._session.add(row)
        else:
            row.status = status
            row.last_beat_at = last_beat_at
            if cycle_count is not None:
                row.cycle_count = cycle_count
            if paused is not None:
                row.paused = paused
            if detail is not None:
                row.detail = detail
        self._session.flush()
        return row


class MarketScanRunRepository:
    """Append-only history of worker scan cycles (failures are dead-letters)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, run: MarketScanRun) -> MarketScanRun:
        self._session.add(run)
        self._session.flush()
        return run

    def list_recent(self, *, limit: int = 50) -> list[MarketScanRun]:
        stmt = select(MarketScanRun).order_by(desc(MarketScanRun.started_at)).limit(limit)
        return list(self._session.scalars(stmt).all())

    def list_dead_letters(self, *, limit: int = 50) -> list[MarketScanRun]:
        stmt = (
            select(MarketScanRun)
            .where(MarketScanRun.status == "failed")
            .order_by(desc(MarketScanRun.started_at))
            .limit(limit)
        )
        return list(self._session.scalars(stmt).all())


class SetupDetectionRepository:
    """Append-only persisted setup detections."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: SetupDetectionRecord) -> SetupDetectionRecord:
        self._session.add(record)
        self._session.flush()
        return record
