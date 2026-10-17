"""Page transactions converge by native identity and commit their cursor atomically."""

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.db.blofin_activity import BloFinActivityAccount, BloFinActivityCursor, BloFinActivityFact
from app.schemas.blofin_activity import NativeActivityFact
from app.services.blofin_activity_config import ActivityScope


class ActivityConflictError(ConflictError):
    code = "blofin_activity_identity_conflict"


def fact_hash(fact: NativeActivityFact) -> str:
    return hashlib.sha256(
        json.dumps(fact.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class BloFinActivityRepository:
    def __init__(self, session: Session, scope: ActivityScope) -> None:
        self.session, self.scope = session, scope

    def filters(self, model: Any) -> tuple[Any, ...]:
        return (
            model.organization_id == self.scope.organization_id,
            model.environment == self.scope.environment,
            model.account_uid == self.scope.account_uid,
        )

    def verify_binding(self, binding: str, now: datetime) -> None:
        # Safe first-writer convergence under the worker's account advisory lock.
        self.session.execute(
            insert(BloFinActivityAccount)
            .values(
                organization_id=self.scope.organization_id,
                environment=self.scope.environment,
                account_uid=self.scope.account_uid,
                credential_binding=binding,
                identity_verified_at=now,
            )
            .on_conflict_do_nothing()
        )
        account = self.session.get(BloFinActivityAccount, self.scope.key())
        assert account is not None
        account.credential_binding = binding
        account.identity_verified_at = now
        account.identity_error = None

    def cursor(self, kind: Literal["order", "fill"]) -> BloFinActivityCursor | None:
        return self.session.get(BloFinActivityCursor, (*self.scope.key(), kind))

    def persist_page(
        self,
        cursor: BloFinActivityCursor,
        facts: tuple[NativeActivityFact, ...],
        metadata: dict[str, dict[str, str | None]],
        now: datetime,
    ) -> None:
        if (cursor.organization_id, cursor.environment, cursor.account_uid) != self.scope.key():
            raise ActivityConflictError("Checkpoint belongs to a different native account scope.")
        next_cursor = facts[-1].native_id if facts else None
        if next_cursor and (next_cursor in cursor.seen_cursors or len(cursor.seen_cursors) >= 1000):
            raise ActivityConflictError(
                "Native pagination did not advance within the bounded scan."
            )
        for fact in facts:
            if fact.kind != cursor.kind:
                raise ActivityConflictError("Activity stream identity mismatch.")
            key = (*self.scope.key(), fact.kind, fact.native_id)
            existing = self.session.get(BloFinActivityFact, key)
            digest = fact_hash(fact)
            if existing is not None:
                if existing.content_hash != digest:
                    raise ActivityConflictError(
                        "Conflicting content for an existing native identity."
                    )
                continue
            # An order and its fills cannot silently disagree on instrument or side.
            related = self.session.scalars(
                select(BloFinActivityFact).where(
                    *self.filters(BloFinActivityFact),
                    BloFinActivityFact.order_id == fact.order_id,
                )
            )
            for other in related:
                if any(
                    other.payload[k] != getattr(fact, k)
                    for k in ("instrument", "side", "position_side")
                ):
                    raise ActivityConflictError("Native order and fill identities conflict.")
            instrument_metadata = metadata.get(fact.instrument)
            self.session.add(
                BloFinActivityFact(
                    organization_id=self.scope.organization_id,
                    environment=self.scope.environment,
                    account_uid=self.scope.account_uid,
                    kind=fact.kind,
                    native_id=fact.native_id,
                    order_id=fact.order_id,
                    occurred_at_ms=int(fact.occurred_at_ms),
                    payload=fact.model_dump(mode="json"),
                    content_hash=digest,
                    instrument_metadata=instrument_metadata,
                    metadata_observed_at=now if instrument_metadata else None,
                    first_observed_at=now,
                )
            )
            self.session.flush()
        if facts:
            assert next_cursor is not None
            cursor.native_cursor = next_cursor
            cursor.seen_cursors = [*cursor.seen_cursors, next_cursor]
        else:
            cursor.window_complete = True
            cursor.covered_begin_ms = (
                cursor.window_begin_ms
                if cursor.covered_begin_ms is None
                else min(cursor.covered_begin_ms, cursor.window_begin_ms)
            )
            cursor.covered_end_ms = cursor.window_end_ms
        cursor.last_successful_sync = now
        cursor.last_attempt_at = now
        cursor.last_error_code = None
        cursor.next_retry_at = None
        self.session.flush()
