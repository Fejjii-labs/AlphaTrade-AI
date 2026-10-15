"""BloFin demo sync snapshot repository (AT-037)."""

from __future__ import annotations

import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db.models import BloFinDemoSyncSnapshot as SnapshotModel


class BloFinSyncRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, row: SnapshotModel) -> SnapshotModel:
        self._session.add(row)
        self._session.flush()
        return row

    def verified_uid_for_binding(self, organization_id: uuid.UUID, binding: str) -> str | None:
        return self._session.scalar(
            select(SnapshotModel.provenance["native_account_uid"].as_string())
            .where(
                SnapshotModel.organization_id == organization_id,
                SnapshotModel.provenance["connection_binding"].as_string() == binding,
                SnapshotModel.provenance["identity_status"].as_string() == "verified",
                SnapshotModel.provenance["environment"].as_string() == "demo",
            )
            .order_by(SnapshotModel.synced_at.desc(), SnapshotModel.id.desc())
            .limit(1)
        )

    def latest_for_connection(
        self,
        organization_id: uuid.UUID,
        *,
        native_uid: str,
        binding: str,
        successful_only: bool = False,
    ) -> SnapshotModel | None:
        stmt = (
            select(SnapshotModel)
            .where(
                SnapshotModel.organization_id == organization_id,
                SnapshotModel.provenance["environment"].as_string() == "demo",
                or_(
                    and_(
                        SnapshotModel.provenance["native_account_uid"].as_string() == native_uid,
                        SnapshotModel.provenance["identity_status"].as_string() == "verified",
                    ),
                    and_(
                        SnapshotModel.health_status == "unavailable",
                        SnapshotModel.provenance["connection_binding"].as_string() == binding,
                        SnapshotModel.balance_count == 0,
                        SnapshotModel.position_count == 0,
                    ),
                ),
            )
            .order_by(SnapshotModel.synced_at.desc(), SnapshotModel.id.desc())
            .limit(1)
        )
        if successful_only:
            stmt = stmt.where(SnapshotModel.health_status.in_(["ok", "degraded", "stale"]))
        return self._session.scalars(stmt).first()
