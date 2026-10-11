"""Capture within a savepoint; failures retain the source conversation for retry."""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.agent_capture.contracts import CapturePlan, CaptureStatus, SavedEntry
from app.agent_capture.model import CaptureModel as CaptureModel
from app.agent_capture.store import entry_record, list_entries, require_entry, snapshot
from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.db.models import (
    AgentCaptureSource,
    AgentSavedEntry,
    Chunk,
    ConversationMessage,
    Document,
    JournalTrade,
    User,
)
from app.schemas.common import ConversationMessageRole
from app.services.conversation_service import ConversationService

logger = structlog.get_logger(__name__)


def uploaded_text(session: Session, document_id: UUID, organization_id: UUID, user_id: UUID) -> str:
    doc = session.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.organization_id == organization_id,
            Document.user_id == user_id,
        )
    )
    if doc is None:
        raise NotFoundError("Source document not found.")
    chunks = session.scalars(
        select(Chunk)
        .where(
            Chunk.document_id == doc.id,
            Chunk.organization_id == organization_id,
            Chunk.user_id == user_id,
        )
        .order_by(Chunk.ordinal)
    )
    text = "\n".join(c.content for c in chunks)
    if not text.strip():
        raise ValidationAppError("Source document has no readable text.")
    if len(text) > 100000:
        raise ValidationAppError(
            "This document exceeds the Agent reading limit. The original is retained; "
            "split it into shorter sources."
        )
    return text


def matching_entries(
    session: Session, organization_id: UUID, user_id: UUID, query: str
) -> list[SavedEntry]:
    from app.interactive_agent.parsing import query_tokens

    tokens = query_tokens(query)
    if not tokens:
        return []
    # Filter the entire private history before bounding candidates. An older matching
    # note must not disappear simply because 200 unrelated notes were added later.
    terms: list[ColumnElement[bool]] = []
    for token in sorted(tokens)[:12]:
        pattern = f"%{token}%"
        terms.extend(
            field.ilike(pattern)
            for field in (
                AgentSavedEntry.title,
                AgentSavedEntry.summary,
                AgentSavedEntry.original_text,
                AgentSavedEntry.category,
                cast(AgentSavedEntry.sources, String),
            )
        )
    rows = session.scalars(
        select(AgentSavedEntry)
        .where(
            AgentSavedEntry.organization_id == organization_id,
            AgentSavedEntry.user_id == user_id,
            AgentSavedEntry.undone.is_(False),
            or_(*terms),
        )
        .order_by(AgentSavedEntry.updated_at.desc(), AgentSavedEntry.id)
        .limit(200)
    )
    scored = [
        (
            len(
                tokens
                & query_tokens(
                    f"{row.title} {row.summary} {row.category} {row.original_text} "
                    f"{' '.join(row.sources.get('tags', []))}"
                )
            ),
            entry_record(row),
        )
        for row in rows
    ]
    return [
        entry for score, entry in sorted(scored, key=lambda item: item[0], reverse=True) if score
    ][:8]


@dataclass
class CaptureSnapshot:
    organization_id: UUID
    user_id: UUID
    conversation_id: UUID
    message_id: UUID
    document_id: UUID | None
    current: str
    digest: str
    history: list[ConversationMessage]
    revisions: dict[UUID, int]
    content: dict[str, Any]


class CaptureService:
    def __init__(self, session: Session, settings: Settings, *, model: CaptureModel | None = None):
        self.session = session
        self.model = model or CaptureModel(settings)

    def prepare(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        source_document_id: UUID | None = None,
    ) -> CaptureSnapshot | tuple[list[SavedEntry], CaptureStatus, str | None]:
        conversation = ConversationService(self.session).require(
            conversation_id, organization_id=organization_id, user_id=user_id
        )
        message = self.session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.id == message_id,
                ConversationMessage.conversation_id == conversation.id,
                ConversationMessage.organization_id == organization_id,
                ConversationMessage.user_id == user_id,
                ConversationMessage.role == ConversationMessageRole.USER,
            )
        )
        if message is None:
            raise NotFoundError("Source message not found.")
        document = (
            uploaded_text(self.session, source_document_id, organization_id, user_id)
            if source_document_id
            else ""
        )
        current = f"{message.content}\n{document}".strip()
        digest = hashlib.sha256(" ".join(current.split()).encode()).hexdigest()
        prior = self.session.scalar(
            select(AgentCaptureSource).where(
                AgentCaptureSource.organization_id == organization_id,
                AgentCaptureSource.user_id == user_id,
                AgentCaptureSource.source_hash == digest,
            )
        )
        if prior:
            rows = [
                require_entry(self.session, UUID(i), organization_id, user_id)
                for i in prior.entry_ids
            ]
            return (
                [entry_record(row) for row in rows if not row.undone],
                "saved" if any(not row.undone for row in rows) else "not_needed",
                None,
            )
        history = list(
            self.session.scalars(
                select(ConversationMessage)
                .where(
                    ConversationMessage.conversation_id == conversation.id,
                    ConversationMessage.organization_id == organization_id,
                    ConversationMessage.user_id == user_id,
                    ConversationMessage.role == ConversationMessageRole.USER,
                    ConversationMessage.id != message.id,
                )
                .order_by(ConversationMessage.created_at.desc())
                .limit(6)
            )
        )
        recent = list_entries(self.session, organization_id, user_id, limit=20).items
        relevant = matching_entries(self.session, organization_id, user_id, current)
        candidates = list({entry.id: entry for entry in [*recent, *relevant]}.values())
        content = {
            "current_user_content": message.content,
            "uploaded_reference_content": document,
            "prior_user_messages": [m.content[:2000] for m in reversed(history)],
            "existing_entries": [
                {
                    "id": str(e.id),
                    "category": e.category,
                    "title": e.title,
                    "summary": e.summary,
                    "draft": e.draft,
                }
                for e in candidates
            ],
        }
        return CaptureSnapshot(
            organization_id,
            user_id,
            conversation.id,
            message.id,
            source_document_id,
            current,
            digest,
            history,
            {e.id: e.revision for e in candidates},
            content,
        )

    def capture(self, **kwargs: Any) -> tuple[list[SavedEntry], CaptureStatus, str | None]:
        """Compatibility for pure injected planners; live reasoning uses separate phases.

        A caller-owned transaction cannot be ended here without risking unrelated
        writes. Production callers use prepare, close, model.plan, then apply.
        """
        from app.agent_capture.model import CaptureModel as ReasoningCaptureModel
        from app.agent_capture.model import CaptureUnavailableError

        prepared = self.prepare(**kwargs)
        if not isinstance(prepared, CaptureSnapshot):
            return prepared
        if isinstance(self.model, ReasoningCaptureModel):
            raise CaptureUnavailableError(
                "Capture is unavailable through a transaction-bound planner; use separate phases."
            )
        plan = self.model.plan(
            organization_id=prepared.organization_id,
            user_id=prepared.user_id,
            conversation_id=prepared.conversation_id,
            content=prepared.content,
        )
        return self.apply(prepared, plan)

    def apply(
        self, prepared: CaptureSnapshot, plan: CapturePlan
    ) -> tuple[list[SavedEntry], CaptureStatus, str | None]:
        ConversationService(self.session).require(
            prepared.conversation_id,
            organization_id=prepared.organization_id,
            user_id=prepared.user_id,
        )
        # Lock only the short dedupe/write phase; never the reasoning request.
        self.session.scalar(
            select(User.id).where(User.id == prepared.user_id).with_for_update(key_share=True)
        )
        prior = self.session.scalar(
            select(AgentCaptureSource).where(
                AgentCaptureSource.organization_id == prepared.organization_id,
                AgentCaptureSource.user_id == prepared.user_id,
                AgentCaptureSource.source_hash == prepared.digest,
            )
        )
        if prior:
            rows = [
                require_entry(self.session, UUID(i), prepared.organization_id, prepared.user_id)
                for i in prior.entry_ids
            ]
            return (
                [entry_record(r) for r in rows if not r.undone],
                ("saved" if any(not r.undone for r in rows) else "not_needed"),
                None,
            )
        if plan.clarification or any(e.confidence < 0.8 for e in plan.entries):
            return (
                [],
                "clarification",
                plan.clarification or "Which destination fits this contribution?",
            )
        if not plan.entries:
            return [], "not_needed", None
        self._validate(plan, prepared.current, prepared.history, set(prepared.revisions))
        for item in plan.entries:
            if item.target_entry_id:
                row = require_entry(
                    self.session,
                    item.target_entry_id,
                    prepared.organization_id,
                    prepared.user_id,
                    lock=True,
                )
                if row.undone or row.revision != prepared.revisions[item.target_entry_id]:
                    raise ConflictError(
                        "Saved entry changed during capture; retry from current context."
                    )
        return self._persist(
            plan,
            prepared.conversation_id,
            prepared.organization_id,
            prepared.user_id,
            prepared.message_id,
            prepared.document_id,
            prepared.current,
            prepared.digest,
            prepared.history,
        )

    def _validate(
        self,
        plan: CapturePlan,
        current: str,
        history: list[ConversationMessage],
        candidates: set[UUID],
    ) -> None:
        allowed = "\n".join([current, *(m.content for m in history)])
        seen_suggestions: set[str] = set()
        seen_targets: set[UUID] = set()
        for suggestion in plan.entries:
            fingerprint = suggestion.model_dump_json(exclude={"confidence"})
            if fingerprint in seen_suggestions:
                raise ValidationAppError("Capture contains a duplicate suggestion.")
            seen_suggestions.add(fingerprint)
            if suggestion.target_entry_id is not None:
                if suggestion.target_entry_id in seen_targets:
                    raise ValidationAppError("Capture cannot revise the same entry more than once.")
                seen_targets.add(suggestion.target_entry_id)
            if not all(q.strip() and q in allowed for q in suggestion.evidence_quotes):
                raise ValidationAppError("Capture quotes do not match the source.")
            if not any(q in current for q in suggestion.evidence_quotes):
                raise ValidationAppError("Capture does not support the current contribution.")
            if (
                suggestion.target_entry_id is not None
                and suggestion.target_entry_id not in candidates
            ):
                raise ValidationAppError("Capture target was not supplied as scoped context.")
            if suggestion.category == "strategies" and suggestion.draft is None:
                raise ValidationAppError("A strategy capture requires a structured draft.")

    def _persist(
        self,
        plan: CapturePlan,
        conversation_id: UUID,
        organization_id: UUID,
        user_id: UUID,
        message_id: UUID,
        document_id: UUID | None,
        current: str,
        digest: str,
        history: list[ConversationMessage],
    ) -> tuple[list[SavedEntry], CaptureStatus, None]:
        records = []
        for item in plan.entries:
            cited = [
                m for m in reversed(history) if any(q in m.content for q in item.evidence_quotes)
            ]
            contributed = "\n\n".join([*(m.content for m in cited), current])
            message_ids = [str(m.id) for m in cited] + [str(message_id)]
            if item.target_entry_id:
                row = require_entry(
                    self.session, item.target_entry_id, organization_id, user_id, lock=True
                )
                if row.undone:
                    raise ValidationAppError("Cannot revise an undone entry.")
                row.history = [*(row.history or []), snapshot(row)]
                row.revision += 1
                row.original_text = row.original_text + "\n\n" + contributed
                sources = dict(row.sources)
            else:
                row = AgentSavedEntry(
                    organization_id=organization_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    original_text=contributed,
                    revision=1,
                    history=[],
                    undone=False,
                )
                self.session.add(row)
                sources = {}
            row.category = item.category
            row.title = item.title
            row.summary = item.summary
            row.draft = item.draft.model_dump(mode="json") if item.draft else row.draft
            sources.update(
                {
                    "message_ids": list(
                        dict.fromkeys([*sources.get("message_ids", []), *message_ids])
                    ),
                    "tags": list(dict.fromkeys([*sources.get("tags", []), *item.tags]))[:20],
                    "evidence_quotes": item.evidence_quotes,
                    "provenance": "user_supplied_unverified",
                    "model_usage": getattr(self.model, "last_usage", {}),
                }
            )
            if document_id:
                sources["document_id"] = str(document_id)
            # Exact identifiers are linked only after a deterministic owner-scoped read.
            ids = {
                UUID(i)
                for i in re.findall(
                    r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", current
                )
            }
            if ids:
                trades = list(
                    self.session.scalars(
                        select(JournalTrade.id).where(
                            JournalTrade.id.in_(ids),
                            JournalTrade.organization_id == organization_id,
                            JournalTrade.user_id == user_id,
                        )
                    )
                )
                if len(trades) == 1:
                    sources["trade_id"] = str(trades[0])
            row.sources = sources
            row.updated_at = datetime.now(UTC)
            self.session.flush()
            records.append(entry_record(row))
        self.session.add(
            AgentCaptureSource(
                organization_id=organization_id,
                user_id=user_id,
                source_hash=digest,
                entry_ids=[str(e.id) for e in records],
            )
        )
        self.session.flush()
        return records, "saved", None
