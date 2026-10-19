"""Canonical journal trade persistence (AT-030), statistics queries (AT-031),
and import/attachment persistence (AT-033)."""

from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ColumnElement, func, or_, select

from app.db.models import (
    JournalImportBatch,
    JournalTrade,
    JournalTradeAttachment,
    JournalTradeEvidence,
    JournalTradeObservation,
    JournalTradeRuleCheck,
)
from app.repositories.base import SQLAlchemyRepository
from app.schemas.common import (
    JournalEntryMethod,
    JournalTradeSource,
    JournalTradeStatus,
    MarketRegime,
    RuleComplianceStatus,
    TradeDirection,
    TradeResult,
)


@dataclass(frozen=True, slots=True)
class JournalTradeStatsRow:
    """Minimal per-trade projection used for deterministic statistics (AT-031/AT-036).

    Only recorded values are carried — no derived or fetched data. Loading a
    narrow projection instead of full ORM rows keeps the bounded statistics
    scan cheap even when trades hold long free-text fields.
    """

    id: uuid.UUID
    source: JournalTradeSource
    entry_method: JournalEntryMethod
    symbol: str
    timeframe: str
    market_regime: MarketRegime
    setup_id: uuid.UUID | None
    user_strategy_id: uuid.UUID | None
    strategy_version_id: uuid.UUID | None
    result: TradeResult
    net_pnl: Decimal | None
    gross_pnl: Decimal | None
    fees: Decimal | None
    funding: Decimal | None
    slippage: Decimal | None
    planned_risk_amount: Decimal | None
    mfe_amount: Decimal | None
    mae_amount: Decimal | None
    available_profit: Decimal | None
    realized_vs_available_pct: float | None
    # Decision-quality inputs (AT-036).
    direction: TradeDirection
    planned_entry_price: Decimal | None
    entry_price: Decimal | None
    exit_reason: str | None
    strategy_label: str | None


_ANNOTATION_EXCLUDED_FROM_STATS = frozenset({"tags", "notes", "grade"})


def _stats_columns() -> tuple[object, ...]:
    """Closed-trade columns for statistics. Annotation grade stays out."""
    return (
        JournalTrade.id,
        JournalTrade.source,
        JournalTrade.entry_method,
        JournalTrade.symbol,
        JournalTrade.timeframe,
        JournalTrade.market_regime,
        JournalTrade.setup_id,
        JournalTrade.user_strategy_id,
        JournalTrade.strategy_version_id,
        JournalTrade.result,
        JournalTrade.net_pnl,
        JournalTrade.gross_pnl,
        JournalTrade.fees,
        JournalTrade.funding,
        JournalTrade.slippage,
        JournalTrade.planned_risk_amount,
        JournalTrade.mfe_amount,
        JournalTrade.mae_amount,
        JournalTrade.available_profit,
        JournalTrade.realized_vs_available_pct,
        JournalTrade.direction,
        JournalTrade.planned_entry_price,
        JournalTrade.entry_price,
        JournalTrade.exit_reason,
        JournalTrade.strategy_label,
    )


def assert_stats_projection_excludes_annotation_grade() -> None:
    """Fail closed if a hand grade, note, or tag list enters statistics."""
    names = {column.key for column in _stats_columns()}  # type: ignore[attr-defined]
    names.update(JournalTradeStatsRow.__dataclass_fields__)
    leaked = names & _ANNOTATION_EXCLUDED_FROM_STATS
    if leaked:
        raise RuntimeError(
            "annotation grade is not a journal statistics input: " + ", ".join(sorted(leaked))
        )


class JournalTradeRepository(SQLAlchemyRepository[JournalTrade]):
    model = JournalTrade

    def list_trades(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        source: JournalTradeSource | None = None,
        status: JournalTradeStatus | None = None,
        symbol: str | None = None,
        user_strategy_id: uuid.UUID | None = None,
        setup_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[JournalTrade], int]:
        filters = [JournalTrade.organization_id == organization_id]
        if user_id is not None:
            filters.append(JournalTrade.user_id == user_id)
        if source is not None:
            filters.append(JournalTrade.source == source)
        if status is not None:
            filters.append(JournalTrade.status == status)
        if symbol is not None:
            filters.append(JournalTrade.symbol == symbol)
        if user_strategy_id is not None:
            filters.append(JournalTrade.user_strategy_id == user_strategy_id)
        if setup_id is not None:
            filters.append(JournalTrade.setup_id == setup_id)

        count_stmt = select(func.count()).select_from(JournalTrade).where(*filters)
        list_stmt = (
            select(JournalTrade)
            .where(*filters)
            .order_by(JournalTrade.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        total = int(self._session.scalar(count_stmt) or 0)
        return list(self._session.scalars(list_stmt).all()), total

    def get_scoped(
        self,
        trade_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
    ) -> JournalTrade | None:
        stmt = select(JournalTrade).where(
            JournalTrade.id == trade_id,
            JournalTrade.organization_id == organization_id,
        )
        return self._session.scalar(stmt)

    def find_by_link(
        self,
        *,
        organization_id: uuid.UUID,
        linked_position_id: uuid.UUID | None = None,
        linked_paper_trade_id: uuid.UUID | None = None,
    ) -> JournalTrade | None:
        stmt = select(JournalTrade).where(JournalTrade.organization_id == organization_id)
        if linked_position_id is not None:
            stmt = stmt.where(JournalTrade.linked_position_id == linked_position_id)
        if linked_paper_trade_id is not None:
            stmt = stmt.where(JournalTrade.linked_paper_trade_id == linked_paper_trade_id)
        return self._session.scalar(stmt.limit(1))

    def find_by_external_ref(
        self,
        *,
        organization_id: uuid.UUID,
        external_ref: str,
    ) -> JournalTrade | None:
        stmt = select(JournalTrade).where(
            JournalTrade.organization_id == organization_id,
            JournalTrade.external_ref == external_ref,
        )
        return self._session.scalar(stmt.limit(1))

    def existing_external_refs(
        self,
        *,
        organization_id: uuid.UUID,
        external_refs: list[str],
    ) -> dict[str, uuid.UUID]:
        """Map already-present external refs to their journal trade ids (AT-033).

        One bounded IN-query per import batch instead of a lookup per row.
        """
        if not external_refs:
            return {}
        stmt = select(JournalTrade.external_ref, JournalTrade.id).where(
            JournalTrade.organization_id == organization_id,
            JournalTrade.external_ref.in_(external_refs),
        )
        return {ref: trade_id for ref, trade_id in self._session.execute(stmt).all() if ref}

    def find_by_execution_lifecycle(
        self,
        *,
        organization_id: uuid.UUID,
        execution_lifecycle_id: uuid.UUID,
    ) -> JournalTrade | None:
        stmt = select(JournalTrade).where(
            JournalTrade.organization_id == organization_id,
            JournalTrade.execution_lifecycle_id == execution_lifecycle_id,
        )
        return self._session.scalar(stmt.limit(1))

    def find_by_linked_journal_entry(
        self,
        *,
        organization_id: uuid.UUID,
        linked_journal_entry_id: uuid.UUID,
    ) -> JournalTrade | None:
        stmt = select(JournalTrade).where(
            JournalTrade.organization_id == organization_id,
            JournalTrade.linked_journal_entry_id == linked_journal_entry_id,
        )
        return self._session.scalar(stmt.limit(1))

    def list_replay_candidates(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        symbol: str | None = None,
        overwrite_policy: str = "skip_protected",
        limit: int = 50,
    ) -> list[JournalTrade]:
        """Closed trades eligible for HistoricalCandle excursion replay (AT-032).

        Under ``skip_protected``, only rows with no excursion source or
        ``excursion_source='replay'`` are returned so manual/system values are
        never silently selected. ``force`` returns all closed trades with a
        usable window (entry/exit timestamps present).
        """
        filters: list[ColumnElement[bool]] = [
            JournalTrade.organization_id == organization_id,
            JournalTrade.user_id == user_id,
            JournalTrade.status == JournalTradeStatus.CLOSED,
            JournalTrade.entry_time.is_not(None),
            JournalTrade.exit_time.is_not(None),
            JournalTrade.entry_price.is_not(None),
        ]
        if symbol is not None:
            filters.append(JournalTrade.symbol == symbol)
        if overwrite_policy != "force":
            filters.append(
                or_(
                    JournalTrade.excursion_source.is_(None),
                    JournalTrade.excursion_source == "",
                    JournalTrade.excursion_source == "replay",
                )
            )
        stmt = (
            select(JournalTrade)
            .where(*filters)
            .order_by(JournalTrade.exit_time.asc(), JournalTrade.id.asc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt).all())

    # ------------------------------------------------------------------ #
    # Statistics queries (AT-031) — closed trades only, bounded scans
    # ------------------------------------------------------------------ #

    @staticmethod
    def _stats_filters(
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        source: JournalTradeSource | None = None,
        sources: Collection[JournalTradeSource] | None = None,
        entry_method: JournalEntryMethod | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        market_regime: MarketRegime | None = None,
        setup_id: uuid.UUID | None = None,
        user_strategy_id: uuid.UUID | None = None,
        strategy_version_id: uuid.UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[ColumnElement[bool]]:
        """Shared WHERE clauses for the statistics row and rule-check scans.

        Statistics cover CLOSED trades only: outcome metrics (expectancy, win
        rate, profit factor) are undefined for planned/open/cancelled rows.
        The date range applies to the effective trade time — exit time when
        recorded, else entry time, else the row creation time.
        """
        effective_time = func.coalesce(
            JournalTrade.exit_time, JournalTrade.entry_time, JournalTrade.created_at
        )
        filters: list[ColumnElement[bool]] = [
            JournalTrade.organization_id == organization_id,
            JournalTrade.user_id == user_id,
            JournalTrade.status == JournalTradeStatus.CLOSED,
        ]
        if sources is not None:
            filters.append(JournalTrade.source.in_(tuple(sources)))
        elif source is not None:
            filters.append(JournalTrade.source == source)
        if entry_method is not None:
            filters.append(JournalTrade.entry_method == entry_method)
        if symbol is not None:
            filters.append(JournalTrade.symbol == symbol)
        if timeframe is not None:
            filters.append(JournalTrade.timeframe == timeframe)
        if market_regime is not None:
            filters.append(JournalTrade.market_regime == market_regime)
        if setup_id is not None:
            filters.append(JournalTrade.setup_id == setup_id)
        if user_strategy_id is not None:
            filters.append(JournalTrade.user_strategy_id == user_strategy_id)
        if strategy_version_id is not None:
            filters.append(JournalTrade.strategy_version_id == strategy_version_id)
        if date_from is not None:
            filters.append(effective_time >= date_from)
        if date_to is not None:
            filters.append(effective_time <= date_to)
        return filters

    def fetch_stats_rows(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        source: JournalTradeSource | None = None,
        sources: Collection[JournalTradeSource] | None = None,
        entry_method: JournalEntryMethod | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        market_regime: MarketRegime | None = None,
        setup_id: uuid.UUID | None = None,
        user_strategy_id: uuid.UUID | None = None,
        strategy_version_id: uuid.UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        max_rows: int,
    ) -> tuple[list[JournalTradeStatsRow], bool]:
        """Fetch a bounded, deterministic projection of closed trades.

        Returns ``(rows, truncated)``. Ordering is stable (effective time,
        then id) so a truncated result always covers the same oldest window.
        Hand-assigned ``grade:`` tags, notes, and the tag list are not selected.
        """
        assert_stats_projection_excludes_annotation_grade()
        filters = self._stats_filters(
            organization_id=organization_id,
            user_id=user_id,
            source=source,
            sources=sources,
            entry_method=entry_method,
            symbol=symbol,
            timeframe=timeframe,
            market_regime=market_regime,
            setup_id=setup_id,
            user_strategy_id=user_strategy_id,
            strategy_version_id=strategy_version_id,
            date_from=date_from,
            date_to=date_to,
        )
        effective_time = func.coalesce(
            JournalTrade.exit_time, JournalTrade.entry_time, JournalTrade.created_at
        )
        stmt = (
            select(*_stats_columns())
            .where(*filters)
            .order_by(effective_time.asc(), JournalTrade.id.asc())
            .limit(max_rows + 1)
        )
        raw = self._session.execute(stmt).all()
        truncated = len(raw) > max_rows
        rows = [JournalTradeStatsRow(*row) for row in raw[:max_rows]]
        return rows, truncated

    def fetch_rule_check_status_pairs(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        source: JournalTradeSource | None = None,
        sources: Collection[JournalTradeSource] | None = None,
        entry_method: JournalEntryMethod | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        market_regime: MarketRegime | None = None,
        setup_id: uuid.UUID | None = None,
        user_strategy_id: uuid.UUID | None = None,
        strategy_version_id: uuid.UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[tuple[uuid.UUID, RuleComplianceStatus]]:
        """Distinct (journal_trade_id, rule status) pairs for the same trade scope.

        Grouped in SQL so the result is bounded by trades x distinct statuses
        (at most 5 per trade), never by the raw rule-check row count.
        """
        filters = self._stats_filters(
            organization_id=organization_id,
            user_id=user_id,
            source=source,
            sources=sources,
            entry_method=entry_method,
            symbol=symbol,
            timeframe=timeframe,
            market_regime=market_regime,
            setup_id=setup_id,
            user_strategy_id=user_strategy_id,
            strategy_version_id=strategy_version_id,
            date_from=date_from,
            date_to=date_to,
        )
        stmt = (
            select(JournalTradeRuleCheck.journal_trade_id, JournalTradeRuleCheck.status)
            .join(JournalTrade, JournalTradeRuleCheck.journal_trade_id == JournalTrade.id)
            .where(
                JournalTradeRuleCheck.organization_id == organization_id,
                *filters,
            )
            .group_by(JournalTradeRuleCheck.journal_trade_id, JournalTradeRuleCheck.status)
        )
        return [(row[0], row[1]) for row in self._session.execute(stmt).all()]


class JournalTradeEvidenceRepository(SQLAlchemyRepository[JournalTradeEvidence]):
    model = JournalTradeEvidence

    def list_for_trade(self, journal_trade_id: uuid.UUID) -> list[JournalTradeEvidence]:
        stmt = (
            select(JournalTradeEvidence)
            .where(JournalTradeEvidence.journal_trade_id == journal_trade_id)
            .order_by(JournalTradeEvidence.created_at.asc())
        )
        return list(self._session.scalars(stmt).all())


class JournalTradeRuleCheckRepository(SQLAlchemyRepository[JournalTradeRuleCheck]):
    model = JournalTradeRuleCheck

    def list_for_trade(self, journal_trade_id: uuid.UUID) -> list[JournalTradeRuleCheck]:
        stmt = (
            select(JournalTradeRuleCheck)
            .where(JournalTradeRuleCheck.journal_trade_id == journal_trade_id)
            .order_by(JournalTradeRuleCheck.created_at.asc())
        )
        return list(self._session.scalars(stmt).all())


class JournalTradeObservationRepository(SQLAlchemyRepository[JournalTradeObservation]):
    model = JournalTradeObservation

    def list_for_trade(self, journal_trade_id: uuid.UUID) -> list[JournalTradeObservation]:
        stmt = (
            select(JournalTradeObservation)
            .where(JournalTradeObservation.journal_trade_id == journal_trade_id)
            .order_by(JournalTradeObservation.created_at.asc())
        )
        return list(self._session.scalars(stmt).all())


class JournalImportBatchRepository(SQLAlchemyRepository[JournalImportBatch]):
    """Committed import batches for reconciliation history (AT-033)."""

    model = JournalImportBatch

    def list_scoped(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[JournalImportBatch], int]:
        filters = [
            JournalImportBatch.organization_id == organization_id,
            JournalImportBatch.user_id == user_id,
        ]
        count_stmt = select(func.count()).select_from(JournalImportBatch).where(*filters)
        list_stmt = (
            select(JournalImportBatch)
            .where(*filters)
            .order_by(JournalImportBatch.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        total = int(self._session.scalar(count_stmt) or 0)
        return list(self._session.scalars(list_stmt).all()), total

    def get_scoped(
        self,
        batch_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> JournalImportBatch | None:
        stmt = select(JournalImportBatch).where(
            JournalImportBatch.id == batch_id,
            JournalImportBatch.organization_id == organization_id,
            JournalImportBatch.user_id == user_id,
        )
        return self._session.scalar(stmt)


class JournalTradeAttachmentRepository(SQLAlchemyRepository[JournalTradeAttachment]):
    """Binary evidence attachments for journal trades (AT-033)."""

    model = JournalTradeAttachment

    def list_for_trade(self, journal_trade_id: uuid.UUID) -> list[JournalTradeAttachment]:
        stmt = (
            select(JournalTradeAttachment)
            .where(JournalTradeAttachment.journal_trade_id == journal_trade_id)
            .order_by(JournalTradeAttachment.created_at.asc())
        )
        return list(self._session.scalars(stmt).all())

    def count_for_trade(self, journal_trade_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(JournalTradeAttachment)
            .where(JournalTradeAttachment.journal_trade_id == journal_trade_id)
        )
        return int(self._session.scalar(stmt) or 0)

    def get_scoped(
        self,
        attachment_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
    ) -> JournalTradeAttachment | None:
        stmt = select(JournalTradeAttachment).where(
            JournalTradeAttachment.id == attachment_id,
            JournalTradeAttachment.organization_id == organization_id,
        )
        return self._session.scalar(stmt)
