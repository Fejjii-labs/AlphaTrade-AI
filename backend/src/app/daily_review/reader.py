"""Scoped SELECT adapter. No providers, writes, workers, LLM or delivery calls."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.daily_review.contracts import (
    DailyReview,
    PaperClose,
    ReviewClass,
    ReviewInput,
    ReviewItem,
    ReviewSource,
    ReviewTopic,
    ReviewWindow,
)
from app.daily_review.service import build_review
from app.db.models import (
    JournalTrade,
    JournalTradeObservation,
    LessonCandidate,
    RiskEvent,
    TradeJournal,
)
from app.db.paper_evaluation import PaperEvaluationObservationRow
from app.paper_evaluation.contracts import (
    DataQualityClass,
    PaperEvaluationStage,
)
from app.persistence.paper_evaluation_postgres import observation_from_row
from app.schemas.common import JournalTradeSource, JournalTradeStatus
from app.signal_fusion.enums import ActionEligibilityState, SetupAssessmentState


def _utc(value: datetime) -> datetime:
    # SQLite strips metadata from PostgreSQL-aware columns; DB timestamps are UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class DailyReviewService:
    """Caller authorizes scope and owns a consistent read transaction.

    Watcher/setup evidence is organization-wide. Journal, risk and PnL are user
    scoped. The adapter suppresses autoflush and never commits or persists reviews.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def review(
        self, *, organization_id: UUID, user_id: UUID, window: ReviewWindow, generated_at: datetime
    ) -> DailyReview:
        with self._session.no_autoflush:
            data = self._read(organization_id, user_id, window)
        return build_review(data, generated_at=generated_at)

    def _read(self, org: UUID, user: UUID, window: ReviewWindow) -> ReviewInput:
        items: list[ReviewItem] = []
        closes: list[PaperClose] = []

        def add(
            topic: ReviewTopic,
            code: str,
            ref: ReviewSource,
            classification: ReviewClass = ReviewClass.FACT,
            text: str | None = None,
            *,
            candidate_id: UUID | None = None,
            strategy_version_id: UUID | None = None,
        ) -> None:
            items.append(
                ReviewItem(
                    topic=topic,
                    classification=classification,
                    code=code,
                    text=text,
                    sources=(ref,),
                    candidate_id=candidate_id,
                    strategy_version_id=strategy_version_id,
                )
            )

        def source(row: Any, at: datetime) -> ReviewSource:
            return ReviewSource(
                record_type=row.__tablename__, record_id=str(row.id), occurred_at=_utc(at)
            )

        def rows(model: Any, clock: Any, private: bool = True) -> list[Any]:
            query = select(model).where(
                model.organization_id == org, clock >= window.start, clock < window.end
            )
            if private:
                query = query.where(model.user_id == user)
            return list(self._session.scalars(query).all())

        for row in rows(
            PaperEvaluationObservationRow, PaperEvaluationObservationRow.occurred_at, private=False
        ):
            fact = observation_from_row(row).model_copy(update={"narrative_explanation": None})
            ref = ReviewSource(
                record_type=row.__tablename__,
                record_id=str(fact.observation_id),
                occurred_at=fact.occurred_at,
                version=fact.source_event_version,
                content_hash=fact.content_hash,
                upstream_system=fact.source_system,
                upstream_event_id=fact.source_event_id,
            )
            if fact.stage == PaperEvaluationStage.WATCHER_SCAN:
                add(ReviewTopic.WATCHER, fact.scan_status or "unknown_scan_status", ref)
            if fact.stage == PaperEvaluationStage.SETUP_ASSESSMENT:
                add(
                    ReviewTopic.SETUP,
                    str(fact.assessment_state or "unknown_setup_state"),
                    ref,
                    candidate_id=fact.candidate_id,
                    strategy_version_id=fact.strategy_version_id,
                )
            if (
                fact.stage == PaperEvaluationStage.ELIGIBILITY
                and fact.eligibility_state == ActionEligibilityState.BLOCKED
            ):
                add(
                    ReviewTopic.BLOCKED,
                    fact.reason_code or "unrecorded_block_reason",
                    ref,
                    candidate_id=fact.candidate_id,
                    strategy_version_id=fact.strategy_version_id,
                )
            if fact.assessment_state == SetupAssessmentState.CONFIRMED_SETUP and (
                fact.rejected
                or fact.skipped
                or fact.eligibility_state == ActionEligibilityState.BLOCKED
            ):
                add(
                    ReviewTopic.MISSED,
                    "confirmed_not_taken",
                    ref,
                    ReviewClass.SYSTEM_INFERENCE,
                    "Recorded confirmed setup was rejected, skipped or blocked; "
                    "no counterfactual outcome is known.",
                    candidate_id=fact.candidate_id,
                    strategy_version_id=fact.strategy_version_id,
                )
            if fact.data_quality != DataQualityClass.FRESH or fact.replayed:
                add(
                    ReviewTopic.QUALITY, "replay" if fact.replayed else fact.data_quality.value, ref
                )
        query = select(JournalTrade).where(
            JournalTrade.organization_id == org,
            JournalTrade.user_id == user,
            JournalTrade.source.in_(
                (JournalTradeSource.PAPER_EXECUTION, JournalTradeSource.PAPER_VALIDATION)
            ),
            or_(
                (JournalTrade.entry_time >= window.start) & (JournalTrade.entry_time < window.end),
                (JournalTrade.exit_time >= window.start) & (JournalTrade.exit_time < window.end),
            ),
        )
        for trade in self._session.scalars(query).all():
            if (
                trade.status in (JournalTradeStatus.OPEN, JournalTradeStatus.CLOSED)
                and trade.entry_time
                and window.start <= _utc(trade.entry_time) < window.end
            ):
                add(ReviewTopic.PAPER_OPEN, "recorded_paper_entry", source(trade, trade.entry_time))
            if (
                trade.status == JournalTradeStatus.CLOSED
                and trade.exit_time
                and window.start <= _utc(trade.exit_time) < window.end
            ):
                ref = source(trade, trade.exit_time)
                add(ReviewTopic.PAPER_CLOSE, "recorded_paper_close", ref)
                closes.append(
                    PaperClose(
                        source=ref,
                        cohort=f"{trade.source.value}:{trade.account_id or 'unknown_account'}",
                        net_pnl=trade.net_pnl,
                    )
                )
        for row in rows(JournalTrade, JournalTrade.updated_at):
            add(
                ReviewTopic.JOURNAL,
                "canonical_journal_snapshot",
                source(row, row.updated_at),
                strategy_version_id=row.strategy_version_id,
            )
        for row in rows(RiskEvent, RiskEvent.event_at):
            add(
                ReviewTopic.RISK,
                row.rule_triggered.value,
                source(row, row.event_at),
                text=f"{row.severity.value}: {row.action_taken.value}",
            )
        for row in rows(TradeJournal, TradeJournal.updated_at):
            ref = source(row, row.updated_at)
            add(ReviewTopic.JOURNAL, "journal_snapshot", ref)
            for index, mistake in enumerate(row.mistakes or []):
                add(
                    ReviewTopic.MISTAKE,
                    f"recorded_mistake_{index}",
                    ref,
                    ReviewClass.USER_OBSERVATION,
                    str(mistake),
                )
            for code, text in (
                ("recorded_lesson", row.lessons),
                ("recorded_improvement_rule", row.improvement_rule),
            ):
                if text:
                    add(ReviewTopic.LESSON, code, ref, ReviewClass.USER_OBSERVATION, text)
        observations = self._session.scalars(
            select(JournalTradeObservation)
            .join(JournalTrade, JournalTrade.id == JournalTradeObservation.journal_trade_id)
            .where(
                JournalTradeObservation.organization_id == org,
                JournalTrade.organization_id == org,
                JournalTrade.user_id == user,
                JournalTradeObservation.created_at >= window.start,
                JournalTradeObservation.created_at < window.end,
            )
        ).all()
        for row in observations:
            topic = {
                "mistake": ReviewTopic.MISTAKE,
                "lesson": ReviewTopic.LESSON,
                "market": ReviewTopic.STRATEGY,
            }.get(row.category.value, ReviewTopic.JOURNAL)
            add(
                topic,
                row.category.value,
                source(row, row.created_at),
                ReviewClass.USER_OBSERVATION
                if row.recorded_by == user
                else ReviewClass.SYSTEM_INFERENCE,
                row.observation,
            )
        for row in rows(LessonCandidate, LessonCandidate.created_at):
            add(
                ReviewTopic.LESSON,
                f"lesson_candidate_{row.status}",
                source(row, row.created_at),
                ReviewClass.SYSTEM_INFERENCE,
                row.lesson_text,
            )
            if row.status == "pending_review":
                add(
                    ReviewTopic.LESSON,
                    "review_lesson_candidate",
                    source(row, row.created_at),
                    ReviewClass.RESEARCH_SUGGESTION,
                    "Review this recorded lesson candidate before changing strategy rules.",
                )
        return ReviewInput(
            organization_id=org,
            user_id=user,
            window=window,
            items=tuple(items),
            closes=tuple(closes),
            limitations=(
                "Watcher/setup activity covers organization-level paper-evaluation observations; "
                "recording coverage is not guaranteed.",
                "Forming setups are recorded watch/partial_match states; "
                "confirmation is not predicted.",
                "Journal reflections are stored observations, not independently verified facts; "
                "mutable journal snapshots have no complete edit history.",
                "Paper PnL uses canonical JournalTrade paper_execution/paper_validation closes, "
                "separated by source and account. Currency is not recorded here; no conversion "
                "or cross-account total is supplied.",
                "Counts describe review items, not unique candidates or trades across stages.",
            ),
        )
