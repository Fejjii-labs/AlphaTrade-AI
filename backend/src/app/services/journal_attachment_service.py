"""Journal trade attachment service (AT-033 — Journal Completion).

Uploads binary evidence (screenshots, charts, PDFs) for journal trades with
strict, fail-closed limits from :class:`~app.core.config.Settings`: max size,
MIME whitelist, and per-trade quota. Every upload also creates a linked
``JournalTradeEvidence`` row (``ref='attachment:<id>'``) so attachments appear
in the existing evidence timeline. Tenant-scoped throughout; cross-tenant
lookups 404 (fail closed, no existence leak).

Unit of work (AT-ADR-008): the service flushes; the route commits.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import PurePosixPath, PureWindowsPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import NotFoundError, ValidationAppError
from app.db.models import JournalTrade, JournalTradeAttachment, JournalTradeEvidence
from app.repositories.journal_trades import (
    JournalTradeAttachmentRepository,
    JournalTradeRepository,
)
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import ActorType, AuditEventType, JournalEvidenceKind
from app.schemas.journal_attachments import (
    JournalTradeAttachmentList,
    JournalTradeAttachmentRead,
)
from app.services.audit_service import AuditService
from app.services.journal_attachment_storage import AttachmentStorage

_REQUEST_TAG = "journal-attachments-api"
_ATTACHMENT_REF_PREFIX = "attachment:"
_ATTACHMENT_NOT_FOUND_MSG = "Attachment not found."


class JournalAttachmentService:
    """Upload, list, stream, and delete journal trade attachments."""

    def __init__(
        self,
        session: Session,
        audit_service: AuditService,
        storage: AttachmentStorage,
        settings: Settings,
    ) -> None:
        self._session = session
        self._trades = JournalTradeRepository(session)
        self._attachments = JournalTradeAttachmentRepository(session)
        self._audit = audit_service
        self._storage = storage
        self._settings = settings

    # ------------------------------------------------------------------ #
    # Mutations
    # ------------------------------------------------------------------ #

    def add_attachment(
        self,
        trade_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        filename: str,
        content_type: str | None,
        content: bytes,
        caption: str | None = None,
    ) -> JournalTradeAttachmentRead:
        trade = self._get_trade(trade_id, organization_id=organization_id)
        normalized_type = self._validate_upload(
            trade, filename=filename, content_type=content_type, content=content
        )

        attachment = JournalTradeAttachment(
            journal_trade_id=trade.id,
            organization_id=organization_id,
            uploaded_by=user_id,
            filename=_sanitize_filename(filename),
            content_type=normalized_type,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            caption=caption,
        )
        self._storage.store(attachment, content)
        self._attachments.add(attachment)

        self._session.add(
            JournalTradeEvidence(
                journal_trade_id=trade.id,
                organization_id=organization_id,
                kind=_evidence_kind(normalized_type),
                ref=f"{_ATTACHMENT_REF_PREFIX}{attachment.id}",
                caption=caption,
                recorded_by=user_id,
            )
        )
        self._session.flush()
        self._record_audit(
            attachment,
            AuditEventType.JOURNAL_ATTACHMENT_ADDED,
            action="add_attachment",
            user_id=user_id,
        )
        return JournalTradeAttachmentRead.model_validate(attachment)

    def delete_attachment(
        self,
        attachment_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        attachment = self._get_attachment(attachment_id, organization_id=organization_id)
        evidence_ref = f"{_ATTACHMENT_REF_PREFIX}{attachment.id}"
        for evidence in self._session.scalars(
            select(JournalTradeEvidence).where(
                JournalTradeEvidence.organization_id == organization_id,
                JournalTradeEvidence.ref == evidence_ref,
            )
        ).all():
            self._session.delete(evidence)
        self._record_audit(
            attachment,
            AuditEventType.JOURNAL_ATTACHMENT_DELETED,
            action="delete_attachment",
            user_id=user_id,
        )
        self._storage.remove(attachment)
        self._attachments.delete(attachment)

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    def list_attachments(
        self,
        trade_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
    ) -> JournalTradeAttachmentList:
        trade = self._get_trade(trade_id, organization_id=organization_id)
        rows = self._attachments.list_for_trade(trade.id)
        return JournalTradeAttachmentList(
            items=[JournalTradeAttachmentRead.model_validate(row) for row in rows],
            total=len(rows),
        )

    def get_attachment(
        self,
        attachment_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
    ) -> JournalTradeAttachmentRead:
        """Attachment metadata only (no blob read)."""
        attachment = self._get_attachment(attachment_id, organization_id=organization_id)
        return JournalTradeAttachmentRead.model_validate(attachment)

    def read_content(
        self,
        attachment_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
    ) -> bytes:
        """Load attachment bytes after caller has authorized access."""
        attachment = self._get_attachment(attachment_id, organization_id=organization_id)
        return self._storage.retrieve(attachment)

    def get_content(
        self,
        attachment_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
    ) -> tuple[JournalTradeAttachmentRead, bytes]:
        attachment = self._get_attachment(attachment_id, organization_id=organization_id)
        return (
            JournalTradeAttachmentRead.model_validate(attachment),
            self._storage.retrieve(attachment),
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _get_trade(self, trade_id: uuid.UUID, *, organization_id: uuid.UUID) -> JournalTrade:
        trade = self._trades.get_scoped(trade_id, organization_id=organization_id)
        if trade is None:
            raise NotFoundError("Journal trade not found")
        return trade

    def _get_attachment(
        self, attachment_id: uuid.UUID, *, organization_id: uuid.UUID
    ) -> JournalTradeAttachment:
        attachment = self._attachments.get_scoped(attachment_id, organization_id=organization_id)
        if attachment is None:
            raise NotFoundError(_ATTACHMENT_NOT_FOUND_MSG)
        return attachment

    def _validate_upload(
        self,
        trade: JournalTrade,
        *,
        filename: str,
        content_type: str | None,
        content: bytes,
    ) -> str:
        """Fail-closed upload validation; returns the normalized content type."""
        if not _sanitize_filename(filename):
            raise ValidationAppError("A filename is required.")
        normalized = (content_type or "").split(";")[0].strip().lower()
        allowed = [t.strip().lower() for t in self._settings.journal_attachment_allowed_types]
        if not normalized or normalized not in allowed:
            raise ValidationAppError(
                f"Unsupported attachment content type. Allowed: {', '.join(sorted(allowed))}."
            )
        if len(content) == 0:
            raise ValidationAppError("Attachment content is empty.")
        max_bytes = self._settings.journal_attachment_max_bytes
        if len(content) > max_bytes:
            raise ValidationAppError(f"Attachment exceeds the maximum size of {max_bytes} bytes.")
        max_per_trade = self._settings.journal_attachment_max_per_trade
        if self._attachments.count_for_trade(trade.id) >= max_per_trade:
            raise ValidationAppError(
                f"Attachment limit reached ({max_per_trade} per journal trade)."
            )
        return normalized

    def _record_audit(
        self,
        attachment: JournalTradeAttachment,
        event_type: AuditEventType,
        *,
        action: str,
        user_id: uuid.UUID,
    ) -> None:
        self._audit.record(
            AuditRecordCreate(
                request_id=_REQUEST_TAG,
                trace_id=_REQUEST_TAG,
                event_type=event_type,
                resource_type="journal_trade_attachment",
                resource_id=str(attachment.id),
                organization_id=attachment.organization_id,
                user_id=user_id,
                actor_type=ActorType.USER,
                metadata={
                    "action": action,
                    "journal_trade_id": str(attachment.journal_trade_id),
                    "content_type": attachment.content_type,
                    "size_bytes": attachment.size_bytes,
                },
            )
        )


def _sanitize_filename(filename: str) -> str:
    """Basename only (both separator styles), trimmed to the column limit."""
    name = PureWindowsPath(PurePosixPath(filename.strip()).name).name
    return name[:255]


def _evidence_kind(content_type: str) -> JournalEvidenceKind:
    if content_type.startswith("image/"):
        return JournalEvidenceKind.SCREENSHOT
    return JournalEvidenceKind.FILE
