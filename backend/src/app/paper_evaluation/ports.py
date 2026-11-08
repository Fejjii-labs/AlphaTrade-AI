"""Ports for paper-evaluation persistence and journal excursion facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.paper_evaluation.contracts import PaperEvaluationObservation
from app.schemas.common import TradeResult
from app.schemas.journal_statistics import TradeRuleCompliance


@dataclass(frozen=True, slots=True)
class JournalTradeMeasurement:
    """Narrow recorded journal values used by evaluation. Never live market I/O."""

    journal_trade_id: UUID
    mfe_amount: Decimal | None = None
    mae_amount: Decimal | None = None
    capture_pct: Decimal | None = None
    planned_risk_amount: Decimal | None = None
    net_pnl: Decimal | None = None
    result: TradeResult | None = None
    rule_compliance: TradeRuleCompliance | None = None
    closed_at: datetime | None = None


class PaperEvaluationStore(Protocol):
    def get(
        self,
        *,
        organization_id: UUID,
        observation_id: UUID,
    ) -> PaperEvaluationObservation | None: ...

    def find(
        self,
        *,
        organization_id: UUID,
        source_system: str,
        source_event_id: str,
        source_event_version: int,
    ) -> PaperEvaluationObservation | None: ...

    def put(self, observation: PaperEvaluationObservation) -> PaperEvaluationObservation: ...

    def list_for_organization(
        self, organization_id: UUID, *, limit: int | None = None
    ) -> tuple[PaperEvaluationObservation, ...]:
        """Oldest first; with ``limit``, only the newest ``limit`` rows (M10)."""
        ...


class JournalExcursionPort(Protocol):
    """Read-only journal MFE/MAE/rule facts. Not a JournalTrade writer."""

    def facts_for(
        self,
        *,
        organization_id: UUID,
        journal_trade_ids: tuple[UUID, ...],
    ) -> dict[UUID, JournalTradeMeasurement]: ...
