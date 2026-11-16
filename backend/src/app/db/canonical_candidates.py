"""ORM models for canonical Phase 6 Candidate persistence.

Projections live in ``canonical_candidates``. Transition history and idempotency
bindings are append-only. Tenant identity is ``organization_id``; uniqueness is
the architecture §5 tuple plus its canonical hash. These tables are not wired
into FastAPI, workers, or feature flags.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.core.errors import ValidationAppError
from app.db.base import Base

_registered = False


class CanonicalCandidateHistoryImmutabilityError(ValidationAppError):
    code = "canonical_candidate_history_immutable"


class CanonicalCandidateRow(Base):
    __tablename__ = "canonical_candidates"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "candidate_id",
            name="uq_canonical_candidates_org_candidate",
        ),
        UniqueConstraint(
            "uniqueness_hash",
            name="uq_canonical_candidates_uniqueness_hash",
        ),
        UniqueConstraint(
            "organization_id",
            "strategy_version_id",
            "setup_definition_id",
            "fusion_policy_version",
            "direction",
            "evidence_venue",
            "evidence_market",
            "evidence_instrument",
            "timeframe",
            "evidence_window_hash",
            name="uq_canonical_candidates_semantic_key",
        ),
        CheckConstraint("transition_version >= 1", name="canonical_candidates_version_min"),
        CheckConstraint("length(uniqueness_hash) = 64", name="canonical_candidates_uniqueness_len"),
        CheckConstraint(
            "length(evidence_window_hash) = 64", name="canonical_candidates_window_hash_len"
        ),
        CheckConstraint("length(content_hash) = 64", name="canonical_candidates_content_hash_len"),
        Index("ix_canonical_candidates_org_state", "organization_id", "state"),
        Index("ix_canonical_candidates_created_at", "created_at"),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    uniqueness_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    setup_definition_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    fusion_policy_version: Mapped[str] = mapped_column(String(120), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_venue: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_market: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_instrument: Mapped[str] = mapped_column(String(80), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_window_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    assessment_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    executable_setup: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    evidence_identity: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    transition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class CanonicalCandidateCreationKeyRow(Base):
    __tablename__ = "canonical_candidate_creation_keys"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_canonical_candidate_creation_keys_org_key",
        ),
        CheckConstraint(
            "length(uniqueness_hash) = 64",
            name="canonical_candidate_creation_keys_hash_len",
        ),
        Index(
            "ix_canonical_candidate_creation_keys_candidate",
            "candidate_id",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    uniqueness_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("canonical_candidates.candidate_id", name="fk_cand_creation_keys_candidate"),
        nullable=False,
    )


class CanonicalCandidateTransitionRow(Base):
    __tablename__ = "canonical_candidate_transitions"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "transition_version",
            name="uq_canonical_candidate_transitions_version",
        ),
        UniqueConstraint(
            "organization_id",
            "transition_id",
            name="uq_canonical_candidate_transitions_org_id",
        ),
        CheckConstraint(
            "transition_version >= 1", name="canonical_candidate_transitions_version_min"
        ),
        CheckConstraint(
            "length(content_hash) = 64", name="canonical_candidate_transitions_hash_len"
        ),
        Index(
            "ix_canonical_candidate_transitions_org_candidate",
            "organization_id",
            "candidate_id",
            "transition_version",
        ),
    )

    transition_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("canonical_candidates.candidate_id", name="fk_cand_transitions_candidate"),
        nullable=False,
    )
    previous_state: Mapped[str] = mapped_column(String(32), nullable=False)
    new_state: Mapped[str] = mapped_column(String(32), nullable=False)
    transition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class CanonicalCandidateTransitionKeyRow(Base):
    __tablename__ = "canonical_candidate_transition_keys"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "candidate_id",
            "idempotency_key",
            name="uq_canonical_candidate_transition_keys_org_cand_key",
        ),
        Index(
            "ix_canonical_candidate_transition_keys_transition",
            "transition_id",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("canonical_candidates.candidate_id", name="fk_cand_tr_keys_candidate"),
        primary_key=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    transition_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "canonical_candidate_transitions.transition_id",
            name="fk_cand_tr_keys_transition",
        ),
        nullable=False,
    )


def _forbid_history_mutation(_mapper: Mapper[Any], _connection: Any, target: Any) -> None:
    raise CanonicalCandidateHistoryImmutabilityError(
        f"{type(target).__name__} rows are append-only and cannot be updated.",
        details={"model": type(target).__name__},
    )


def _forbid_history_delete(_mapper: Mapper[Any], _connection: Any, target: Any) -> None:
    raise CanonicalCandidateHistoryImmutabilityError(
        f"{type(target).__name__} rows are append-only and cannot be deleted.",
        details={"model": type(target).__name__},
    )


def register_canonical_candidate_immutability() -> None:
    """Idempotent listener registration. Called after ORM maps are defined."""

    global _registered
    if _registered:
        return
    for model in (
        CanonicalCandidateCreationKeyRow,
        CanonicalCandidateTransitionRow,
        CanonicalCandidateTransitionKeyRow,
    ):
        event.listen(model, "before_update", _forbid_history_mutation)
        event.listen(model, "before_delete", _forbid_history_delete)
    event.listen(CanonicalCandidateRow, "before_delete", _forbid_history_delete)
    _registered = True
