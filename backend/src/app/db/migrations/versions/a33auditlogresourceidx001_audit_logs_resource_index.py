"""audit_logs index on resource type and id (audit M-8)

The governed demo loop looks up audit rows by organisation, resource type and
resource id every cycle; the table's only index was the event time.

Revision ID: a33auditlogresourceidx001
Revises: a32workerriskv2flag001
Create Date: 2026-11-27 11:40:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a33auditlogresourceidx001"
down_revision: str | None = "a32workerriskv2flag001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_logs_resource", "audit_logs", ["resource_type", "resource_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_resource", table_name="audit_logs")
