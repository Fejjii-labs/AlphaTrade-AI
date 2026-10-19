"""Bulk journal import service (AT-033 — Journal Completion).

Record-only: imported rows become canonical ``journal_trades`` with
``source=imported`` and ``entry_method=import``; nothing here touches the
execution path.

Semantics:

- Rows are validated individually so the response reports per-row outcomes
  (reconciliation UX) instead of failing the whole request on the first error.
- Deduplication is by ``(organization_id, external_ref)``. Rows without an
  explicit ``external_ref`` get a deterministic fingerprint over their
  identifying fields, so re-importing the same file is always idempotent.
  The partial unique index on ``journal_trades`` is the database backstop.
- Commits are all-or-nothing in one unit of work (service flushes, route
  commits — AT-ADR-008). A commit request containing invalid rows persists
  nothing and reports the errors; recovery is "fix rows and re-run", which
  duplicates make safe.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.models import JournalImportBatch, JournalTrade
from app.repositories.journal_trades import (
    JournalImportBatchRepository,
    JournalTradeRepository,
)
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import (
    ActorType,
    AuditEventType,
    JournalEntryMethod,
    JournalImportBatchStatus,
    JournalTradeSource,
    JournalTradeStatus,
    TradeResult,
)
from app.schemas.journal_import import (
    JournalImportBatchRead,
    JournalImportMode,
    JournalImportRequest,
    JournalImportResult,
    JournalImportRow,
    JournalImportRowOutcome,
    JournalImportRowResult,
    PaginatedJournalImportBatches,
)
from app.services.audit_service import AuditService
from app.services.risk.mirror_economics import MIRROR_REF_PREFIX, RECONCILED_TAG

_REQUEST_TAG = "journal-import-api"
_FINGERPRINT_PREFIX = "fp-sha256:"


class JournalImportService:
    """Validate, deduplicate, and (optionally) persist bulk journal imports."""

    def __init__(self, session: Session, audit_service: AuditService) -> None:
        self._session = session
        self._trades = JournalTradeRepository(session)
        self._batches = JournalImportBatchRepository(session)
        self._audit = audit_service

    # ------------------------------------------------------------------ #
    # Import
    # ------------------------------------------------------------------ #

    def import_trades(
        self,
        request: JournalImportRequest,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> JournalImportResult:
        parsed = _parse_rows(request.rows)
        reserved: dict[int, str] = {}
        for index, row, _errors in parsed:
            if row is None:
                continue
            message = _reserved_mirror_identity(row)
            if message is not None:
                reserved[index] = message
        refs = [
            _effective_ref(row)
            for index, row, _ in parsed
            if row is not None and index not in reserved
        ]
        existing = self._trades.existing_external_refs(
            organization_id=organization_id, external_refs=refs
        )

        commit = request.mode is JournalImportMode.COMMIT
        any_invalid = any(row is None for _, row, _ in parsed) or bool(reserved)
        # All-or-nothing: invalid rows downgrade a commit to a validation report.
        persist = commit and not any_invalid

        results: list[JournalImportRowResult] = []
        seen_in_batch: dict[str, uuid.UUID | None] = {}
        created = duplicates = invalid = 0

        for index, row, errors in parsed:
            if row is None:
                invalid += 1
                results.append(
                    JournalImportRowResult(
                        index=index,
                        outcome=JournalImportRowOutcome.INVALID,
                        errors=errors,
                    )
                )
                continue
            reserved_error = reserved.get(index)
            if reserved_error is not None:
                invalid += 1
                results.append(
                    JournalImportRowResult(
                        index=index,
                        outcome=JournalImportRowOutcome.INVALID,
                        errors=[reserved_error],
                    )
                )
                continue

            ref = _effective_ref(row)
            if ref in existing or ref in seen_in_batch:
                duplicates += 1
                results.append(
                    JournalImportRowResult(
                        index=index,
                        outcome=JournalImportRowOutcome.DUPLICATE,
                        external_ref=ref,
                        journal_trade_id=existing.get(ref) or seen_in_batch.get(ref),
                    )
                )
                continue

            seen_in_batch[ref] = None
            trade_id: uuid.UUID | None = None
            if persist:
                trade = self._build_trade(
                    row, ref, organization_id=organization_id, user_id=user_id
                )
                self._trades.add(trade)
                trade_id = trade.id
                seen_in_batch[ref] = trade.id
            created += 1
            results.append(
                JournalImportRowResult(
                    index=index,
                    outcome=(
                        JournalImportRowOutcome.CREATED
                        if persist
                        else JournalImportRowOutcome.WOULD_CREATE
                    ),
                    external_ref=ref,
                    journal_trade_id=trade_id,
                )
            )

        batch_id: uuid.UUID | None = None
        if persist:
            batch = JournalImportBatch(
                organization_id=organization_id,
                user_id=user_id,
                status=JournalImportBatchStatus.COMMITTED,
                source_label=request.source_label,
                total_rows=len(parsed),
                created_count=created,
                duplicate_count=duplicates,
                invalid_count=invalid,
                row_report=[r.model_dump(mode="json") for r in results],
            )
            self._batches.add(batch)
            batch_id = batch.id
            self._record_import_audit(batch, organization_id=organization_id, user_id=user_id)

        return JournalImportResult(
            mode=request.mode,
            committed=persist,
            batch_id=batch_id,
            total_rows=len(parsed),
            created_count=created,
            duplicate_count=duplicates,
            invalid_count=invalid,
            results=results,
        )

    # ------------------------------------------------------------------ #
    # Batch history
    # ------------------------------------------------------------------ #

    def list_batches(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedJournalImportBatches:
        rows, total = self._batches.list_scoped(
            organization_id=organization_id, user_id=user_id, limit=limit, offset=offset
        )
        return PaginatedJournalImportBatches(
            items=[JournalImportBatchRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_batch(
        self,
        batch_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> JournalImportBatchRead:
        row = self._batches.get_scoped(batch_id, organization_id=organization_id, user_id=user_id)
        if row is None:
            raise NotFoundError("Import batch not found")
        return JournalImportBatchRead.model_validate(row)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _build_trade(
        self,
        row: JournalImportRow,
        external_ref: str,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> JournalTrade:
        return JournalTrade(
            organization_id=organization_id,
            user_id=user_id,
            source=JournalTradeSource.IMPORTED,
            entry_method=JournalEntryMethod.IMPORT,
            status=row.status,
            symbol=str(row.symbol),
            exchange=row.exchange,
            timeframe=row.timeframe.value,
            strategy_label=row.strategy_label,
            direction=row.direction,
            entry_price=row.entry_price,
            entry_time=row.entry_time,
            exit_price=row.exit_price,
            exit_time=row.exit_time,
            exit_reason=row.exit_reason,
            size=row.size,
            leverage=row.leverage,
            fees=row.fees,
            funding=row.funding,
            slippage=row.slippage,
            gross_pnl=row.gross_pnl,
            net_pnl=row.net_pnl,
            result=_derive_result(row),
            notes=row.notes,
            tags=list(row.tags),
            external_ref=external_ref,
        )

    def _record_import_audit(
        self,
        batch: JournalImportBatch,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        self._audit.record(
            AuditRecordCreate(
                request_id=_REQUEST_TAG,
                trace_id=_REQUEST_TAG,
                event_type=AuditEventType.JOURNAL_IMPORT_COMPLETED,
                resource_type="journal_import_batch",
                resource_id=str(batch.id),
                organization_id=organization_id,
                user_id=user_id,
                actor_type=ActorType.USER,
                metadata={
                    "total_rows": batch.total_rows,
                    "created_count": batch.created_count,
                    "duplicate_count": batch.duplicate_count,
                    "invalid_count": batch.invalid_count,
                    "source_label": batch.source_label or "",
                },
            )
        )


# --------------------------------------------------------------------------- #
# Pure helpers (deterministic, unit-testable without a session)
# --------------------------------------------------------------------------- #


def _parse_rows(
    raw_rows: list[dict[str, object]],
) -> list[tuple[int, JournalImportRow | None, list[str]]]:
    """Validate each raw row independently; collect readable field errors."""
    parsed: list[tuple[int, JournalImportRow | None, list[str]]] = []
    for index, raw in enumerate(raw_rows):
        try:
            parsed.append((index, JournalImportRow.model_validate(raw), []))
        except ValidationError as exc:
            errors = [
                f"{'.'.join(str(loc) for loc in err['loc']) or 'row'}: {err['msg']}"
                for err in exc.errors()
            ]
            parsed.append((index, None, errors))
    return parsed


def _reserved_mirror_identity(row: JournalImportRow) -> str | None:
    if row.external_ref is not None and row.external_ref.startswith(MIRROR_REF_PREFIX):
        return "external_ref is reserved for the BloFin demo mirror."
    if RECONCILED_TAG in row.tags:
        return "The mirror reconciliation marker is reserved."
    return None


def _effective_ref(row: JournalImportRow) -> str:
    """Explicit external_ref, else deterministic identity fingerprint."""
    if row.external_ref:
        return row.external_ref
    return _fingerprint(row)


def _fingerprint(row: JournalImportRow) -> str:
    """Deterministic dedup key over the identifying fields of an import row.

    Normalization keeps equal trades equal across formats: symbols are already
    uppercased by the ``Symbol`` type, decimals are compared value-wise (no
    trailing-zero drift), and aware timestamps collapse to UTC.
    """
    parts = [
        str(row.symbol),
        row.direction.value,
        _normalize_dt(row.entry_time),
        _normalize_decimal(row.entry_price),
        _normalize_decimal(row.size),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{_FINGERPRINT_PREFIX}{digest}"


def _normalize_decimal(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return format(value.normalize(), "f")


def _normalize_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    if value.tzinfo is not None:
        value = value.astimezone(UTC)
    return value.isoformat()


def _derive_result(row: JournalImportRow) -> TradeResult:
    """Explicit result wins; closed rows fall back to the net-PnL sign."""
    if row.result is not TradeResult.OPEN:
        return row.result
    if row.status is not JournalTradeStatus.CLOSED or row.net_pnl is None:
        return TradeResult.OPEN
    if row.net_pnl > 0:
        return TradeResult.WIN
    if row.net_pnl < 0:
        return TradeResult.LOSS
    return TradeResult.BREAKEVEN
