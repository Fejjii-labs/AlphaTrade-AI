"""Tenant-scoped reads and ordered root/version locks for experiment mutations."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.experiments.models import ExperimentRow, ExperimentSampleRow, ExperimentVersionRow
from app.security.tenant import TenantContext


class ExperimentRepository:
    def __init__(self, session: Session, tenant: TenantContext) -> None:
        self.session = session
        self.tenant = tenant

    def root(self, identity: UUID, *, lock: bool = False) -> ExperimentRow:
        statement = select(ExperimentRow).where(
            ExperimentRow.id == identity,
            ExperimentRow.organization_id == self.tenant.organization_id,
            ExperimentRow.user_id == self.tenant.user_id,
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        row = self.session.scalar(statement)
        if row is None:
            raise NotFoundError("Experiment not found.")
        return row

    def version(self, root_id: UUID, identity: UUID, *, lock: bool = False) -> ExperimentVersionRow:
        self.root(root_id, lock=lock)  # Lock order is always root, then version.
        statement = select(ExperimentVersionRow).where(
            ExperimentVersionRow.id == identity,
            ExperimentVersionRow.experiment_id == root_id,
            ExperimentVersionRow.organization_id == self.tenant.organization_id,
            ExperimentVersionRow.user_id == self.tenant.user_id,
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        row = self.session.scalar(statement)
        if row is None:
            raise NotFoundError("Experiment version not found.")
        return row

    def counts(self, row: ExperimentVersionRow) -> dict[str, int]:
        return dict(
            self.session.execute(
                select(ExperimentSampleRow.variant_key, func.count())
                .where(ExperimentSampleRow.version_id == row.id)
                .group_by(ExperimentSampleRow.variant_key)
            )
            .tuples()
            .all()
        )
