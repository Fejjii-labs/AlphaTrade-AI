"""Trade journal persistence."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.models import TradeJournal as JournalModel
from app.repositories.documents import DocumentRepository
from app.repositories.journal import JournalRepository
from app.repositories.positions import PositionRepository
from app.repositories.proposals import ProposalRepository
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import ActorType, AuditEventType
from app.schemas.journal import (
    JournalEntry,
    JournalEntryCreate,
    JournalEntryPrefill,
    JournalEntryUpdate,
)
from app.services.audit_service import AuditService

if TYPE_CHECKING:
    from app.services.journal_rag_sync_service import JournalRagSyncService


class JournalService:
    def __init__(
        self,
        session: Session,
        audit_service: AuditService,
        *,
        rag_sync: JournalRagSyncService | None = None,
    ) -> None:
        self._repo = JournalRepository(session)
        self._proposals = ProposalRepository(session)
        self._positions = PositionRepository(session)
        self._documents = DocumentRepository(session)
        self._audit = audit_service
        self._rag_sync = rag_sync

    def create(self, data: JournalEntryCreate) -> JournalEntry:
        if data.organization_id is None or data.user_id is None:
            raise ValueError("organization_id and user_id are required")
        row = JournalModel(
            organization_id=data.organization_id,
            user_id=data.user_id,
            symbol=str(data.symbol),
            timeframe=data.timeframe.value,
            direction=data.direction,
            strategy_id=data.strategy_id,
            entry_rationale=data.entry_rationale,
            exit_rationale=data.exit_rationale,
            emotions=data.emotions,
            mistakes=data.mistakes,
            lessons=data.lessons,
            improvement_rule=data.improvement_rule,
            result=data.result,
            pnl=data.pnl,
            stress_score=data.stress_score,
            tags=data.tags,
            screenshot_refs=data.screenshot_refs,
            linked_proposal_id=data.linked_proposal_id,
            linked_position_id=data.linked_position_id,
        )
        self._repo.add(row)
        entry = _to_schema(row, documents=self._documents)
        self._record_audit(row, "create")
        if self._rag_sync is not None:
            self._rag_sync.sync_entry(entry)
        return entry

    def list_entries(
        self,
        *,
        organization_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[JournalEntry], int]:
        rows, total = self._repo.list_entries(
            organization_id=organization_id,
            user_id=user_id,
            limit=limit,
            offset=offset,
        )
        return [_to_schema(row, documents=self._documents) for row in rows], total

    def get(self, entry_id: uuid.UUID) -> JournalEntry:
        row = self._repo.get(entry_id)
        if row is None:
            raise NotFoundError("Journal entry not found")
        return _to_schema(row, documents=self._documents)

    def update(self, entry_id: uuid.UUID, data: JournalEntryUpdate) -> JournalEntry:
        row = self._repo.get(entry_id)
        if row is None:
            raise NotFoundError("Journal entry not found")
        updates = data.model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(row, key, value)
        self._repo.add(row)
        self._record_audit(row, "update")
        entry = _to_schema(row, documents=self._documents)
        if self._rag_sync is not None:
            self._rag_sync.sync_entry(entry)
        return entry

    def prefill(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        linked_proposal_id: uuid.UUID | None = None,
        linked_position_id: uuid.UUID | None = None,
    ) -> JournalEntryPrefill:
        """Build a journal draft from a linked proposal or paper position."""
        strategy_id = None
        symbol = "BTCUSDT"
        timeframe = "1h"
        direction = "long"
        entry_rationale = ""
        tags: list[str] = []

        if linked_proposal_id is not None:
            proposal = self._proposals.get_scoped(
                linked_proposal_id,
                organization_id=organization_id,
                user_id=user_id,
            )
            if proposal is None:
                raise NotFoundError("Trade proposal not found")
            strategy_id = proposal.strategy_id
            symbol = proposal.symbol
            timeframe = proposal.timeframe
            direction = proposal.direction
            entry_rationale = proposal.rationale
            tags = [f"setup:{proposal.strategy_id.value}"]

        if linked_position_id is not None:
            position = self._positions.get(linked_position_id)
            if position is None or position.organization_id != organization_id:
                raise NotFoundError("Position not found")
            strategy_id = strategy_id or position.strategy_id
            symbol = position.symbol
            direction = position.direction
            if position.risk_state.get("setup_type"):
                tags.append(f"setup:{position.risk_state['setup_type']}")
            if not entry_rationale:
                entry_rationale = (
                    f"Paper position {position.direction.value} {position.symbol} "
                    f"size {position.size} @ {position.entry_price}"
                )

        from app.schemas.common import Timeframe, TradeDirection

        return JournalEntryPrefill(
            symbol=symbol,
            timeframe=Timeframe(timeframe),
            direction=TradeDirection(direction if isinstance(direction, str) else direction.value),
            strategy_id=strategy_id,
            entry_rationale=entry_rationale or "Review this trade.",
            linked_proposal_id=linked_proposal_id,
            linked_position_id=linked_position_id,
            tags=tags,
        )

    def delete(self, entry_id: uuid.UUID) -> None:
        row = self._repo.get(entry_id)
        if row is None:
            raise NotFoundError("Journal entry not found")
        self._record_audit(row, "delete")
        self._repo.delete(row)

    def _record_audit(self, row: JournalModel, action: str) -> None:
        self._audit.record(
            AuditRecordCreate(
                request_id="journal-api",
                trace_id="journal-api",
                event_type=AuditEventType.JOURNAL_ENTRY_CREATED,
                resource_type="journal_entry",
                resource_id=str(row.id),
                organization_id=row.organization_id,
                user_id=row.user_id,
                actor_type=ActorType.USER,
                metadata={"action": action, "symbol": row.symbol},
            )
        )


def _rag_synced(session_repo: DocumentRepository, row: JournalModel) -> bool:
    doc = session_repo.get_by_source_uri(
        organization_id=row.organization_id,
        user_id=row.user_id,
        source_uri=f"journal://{row.id}",
    )
    return doc is not None


def _to_schema(row: JournalModel, *, documents: DocumentRepository | None = None) -> JournalEntry:
    from app.schemas.common import Timeframe

    rag_synced = _rag_synced(documents, row) if documents is not None else False
    return JournalEntry(
        id=row.id,
        organization_id=row.organization_id,
        user_id=row.user_id,
        symbol=row.symbol,
        timeframe=Timeframe(row.timeframe),
        direction=row.direction,
        strategy_id=row.strategy_id,
        entry_rationale=row.entry_rationale,
        exit_rationale=row.exit_rationale,
        emotions=row.emotions or [],
        mistakes=row.mistakes or [],
        lessons=row.lessons,
        improvement_rule=row.improvement_rule,
        result=row.result,
        pnl=row.pnl,
        stress_score=row.stress_score,
        tags=row.tags or [],
        screenshot_refs=row.screenshot_refs or [],
        linked_proposal_id=row.linked_proposal_id,
        linked_position_id=row.linked_position_id,
        rag_synced=rag_synced,
        created_at=row.created_at,
    )
