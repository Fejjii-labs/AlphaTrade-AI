"""One shared database authority; every operation requires an organization key."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.db.watcher_watchlist import WatcherSymbolStatusRow, WatcherWatchlistRow
from app.workers.watcher_watchlist import (
    SymbolRuntimeStatus,
    SymbolStatusBook,
    WatchlistConfiguration,
    configuration_from_payload,
    default_watchlist,
    replace_watchlist,
)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class WatcherWatchlistRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def load(self, organization_id: UUID) -> WatchlistConfiguration:
        row = self.session.get(WatcherWatchlistRow, organization_id, populate_existing=True)
        if row is None:
            # The immutable revision-zero default is identical in every process.
            return default_watchlist(now=datetime(2026, 9, 30, tzinfo=UTC))
        return configuration_from_payload(
            {
                "slots": row.slots,
                "revision": row.revision,
                "updated_at": aware(row.updated_at).isoformat(),
            }
        )

    def replace(
        self, organization_id: UUID, slots: list[tuple[str, bool]], *, expected_revision: int
    ) -> WatchlistConfiguration:
        current = self.load(organization_id)
        if current.revision != expected_revision:
            raise ConflictError("Watchlist changed; reload before saving.")
        config = replace_watchlist(slots, revision=current.revision + 1)
        values = {
            "revision": config.revision,
            "slots": [asdict(s) for s in config.slots],
            "updated_at": config.updated_at,
        }
        row = self.session.get(WatcherWatchlistRow, organization_id)
        if row is None:
            # Lock the parent, shared by lazy config creation and status writes.
            self._lock_organization(organization_id)
            if (
                self.session.get(WatcherWatchlistRow, organization_id, populate_existing=True)
                is not None
            ):
                raise ConflictError("Watchlist changed; reload before saving.")
            self.session.add(WatcherWatchlistRow(organization_id=organization_id, **values))
            self.session.flush()
        else:
            result = self.session.execute(
                update(WatcherWatchlistRow)
                .where(
                    WatcherWatchlistRow.organization_id == organization_id,
                    WatcherWatchlistRow.revision == expected_revision,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                raise ConflictError("Watchlist changed; reload before saving.")
        self.session.execute(
            delete(WatcherSymbolStatusRow).where(
                WatcherSymbolStatusRow.organization_id == organization_id,
                WatcherSymbolStatusRow.symbol.not_in([s.symbol for s in config.slots]),
            )
        )
        self.session.flush()
        return config

    def _lock_organization(self, organization_id: UUID) -> None:
        from app.db.models import Organization

        self.session.execute(
            select(Organization.id).where(Organization.id == organization_id).with_for_update()
        ).one()

    def previous(self, organization_id: UUID) -> tuple[SymbolRuntimeStatus, ...]:
        rows = self.session.scalars(
            select(WatcherSymbolStatusRow)
            .where(WatcherSymbolStatusRow.organization_id == organization_id)
            .limit(5)
        )
        result = []
        for row in rows:
            payload = dict(row.payload)
            payload.pop("assignment_observations", None)
            for field in ("last_successful_scan", "last_failed_scan"):
                value = payload.get(field)
                payload[field] = datetime.fromisoformat(value) if value else None
            payload["strategy_matches"] = tuple(payload.get("strategy_matches", ()))
            result.append(SymbolRuntimeStatus(**payload))
        return tuple(result)

    def publish(
        self,
        organization_id: UUID,
        config: WatchlistConfiguration,
        rows: tuple[SymbolRuntimeStatus, ...],
        *,
        observed_at: datetime,
        runtime: dict | None = None,
        assignment_observations: dict[str, list[dict]] | None = None,
    ) -> bool:
        self._lock_organization(organization_id)
        current = self.session.scalar(
            select(WatcherWatchlistRow)
            .where(WatcherWatchlistRow.organization_id == organization_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if current is None:
            if config.revision != 0:
                return False
            current = WatcherWatchlistRow(
                organization_id=organization_id,
                revision=0,
                slots=[asdict(s) for s in config.slots],
                updated_at=config.updated_at,
            )
            self.session.add(current)
            self.session.flush()
        if current.revision != config.revision:
            return False
        prior = current.runtime_summary or {}
        previous_observed = prior.get("observed_at")
        if previous_observed and aware(datetime.fromisoformat(previous_observed)) > aware(
            observed_at
        ):
            return False
        if runtime is not None:
            summary = dict(runtime)
            previous_counts = prior.get("status", {})
            for key in (
                "cycles_completed",
                "scans_succeeded",
                "scans_failed",
                "scans_skipped",
                "scans_blocked",
                "candidates_created",
            ):
                summary[key] = previous_counts.get(key, 0) + summary.get(key, 0)
            current.runtime_summary = {
                "configuration_revision": config.revision,
                "observed_at": aware(observed_at).isoformat(),
                "status": summary,
            }
        eligible = {slot.symbol for slot in config.slots}
        for status in rows:
            if status.symbol not in eligible:
                continue
            row = self.session.get(WatcherSymbolStatusRow, (organization_id, status.symbol))
            if row is not None and aware(row.observed_at) > aware(observed_at):
                continue
            payload = asdict(status)
            for field in ("last_successful_scan", "last_failed_scan"):
                value = payload[field]
                payload[field] = aware(value).isoformat() if value else None
            if row is None:
                row = WatcherSymbolStatusRow(organization_id=organization_id, symbol=status.symbol)
                self.session.add(row)
            row.configuration_revision = config.revision
            row.observed_at = observed_at
            if assignment_observations is not None:
                payload["assignment_observations"] = assignment_observations.get(status.symbol, [])
            row.payload = payload
        self.session.flush()
        return True

    def status(
        self, organization_id: UUID, *, source_mode: str, now: datetime, max_age_seconds: float
    ) -> tuple[WatchlistConfiguration, list[dict]]:
        config = self.load(organization_id)
        defaults = SymbolStatusBook().project(config, source_mode=source_mode)
        found = {
            r.symbol: r
            for r in self.session.scalars(
                select(WatcherSymbolStatusRow)
                .where(WatcherSymbolStatusRow.organization_id == organization_id)
                .limit(5)
            )
        }
        output = []
        for slot, default in zip(config.slots, defaults, strict=True):
            row = found.get(slot.symbol)
            valid = (
                row is not None
                and row.configuration_revision == config.revision
                and (
                    timedelta(0)
                    <= aware(now) - aware(row.observed_at)
                    <= timedelta(seconds=max_age_seconds)
                )
            )
            payload = dict(row.payload) if valid else asdict(default)
            payload.pop("assignment_observations", None)
            if not valid:
                payload.update(
                    freshness="unknown",
                    setup_state="pending" if slot.enabled else "disabled",
                    error_state=None,
                    alert_state="none",
                    strategy_matches=[],
                )
            payload.update(
                position=slot.position,
                enabled=slot.enabled,
                configuration_revision=config.revision,
                observed_at=aware(row.observed_at) if valid else None,
            )
            output.append(payload)
        return config, output

    def runtime_status(
        self, organization_id: UUID, *, now: datetime, max_age_seconds: float
    ) -> dict | None:
        row = self.session.get(WatcherWatchlistRow, organization_id, populate_existing=True)
        if row is None or row.runtime_summary is None:
            return None
        snapshot = row.runtime_summary
        observed = aware(datetime.fromisoformat(snapshot["observed_at"]))
        if snapshot["configuration_revision"] != row.revision or not (
            timedelta(0) <= aware(now) - observed <= timedelta(seconds=max_age_seconds)
        ):
            return None
        return dict(snapshot["status"])
