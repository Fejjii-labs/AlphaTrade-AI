"""Month in review for the governed BloFin demo (Sofien, 27 Nov 2026).

On the first day of a month, the daily summary (the page for the month's last day)
adds the month: results per strategy, why trades lost (from the automatic trade
reviews), how often profit was left on the table, and plain lessons from fixed
rules. Each lesson is also saved as a Lessons-section candidate (pending review),
so Sofien accepts or rejects it in the app; nothing changes a strategy by itself.

Rules (a lesson needs at least two trades behind it):
- stops hunted in >= 30% of losses   -> test a wider stop beyond the wick;
- real breaks in >= 40% of losses    -> look for stronger confirmation;
- +1R first in >= 30% of losses      -> test a bigger first take-profit at +1R;
- chop in >= 30% of losses           -> skip low-volatility periods;
- price ran on after >= 30% of wins  -> give the runner more room;
- a strategy below 0R over >= 5 trades -> review it against its backtest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JournalTrade
from app.schemas.common import (
    JournalTradeSource,
    JournalTradeStatus,
    LessonSeverity,
    LessonSourceType,
)


@dataclass(frozen=True, slots=True)
class Lesson:
    mistake_type: str
    text: str
    severity: LessonSeverity = LessonSeverity.MEDIUM


def month_review_tags(
    session: Session, *, organization_id: UUID, account_id: UUID, start: datetime, end: datetime
) -> dict[str, int]:
    """Counts of ``review:<code>`` tags on bot trades closed in ``[start, end)``."""

    counts: dict[str, int] = {}
    for tags in session.scalars(
        select(JournalTrade.tags).where(
            JournalTrade.organization_id == organization_id,
            JournalTrade.account_id == account_id,
            JournalTrade.source == JournalTradeSource.PAPER_EXECUTION,
            JournalTrade.status == JournalTradeStatus.CLOSED,
            JournalTrade.exit_time >= start,
            JournalTrade.exit_time < end,
        )
    ).all():
        for tag in tags or []:
            if str(tag).startswith("review:"):
                code = str(tag).removeprefix("review:")
                counts[code] = counts.get(code, 0) + 1
    return counts


_LOSS_CODES = ("stop_hunted", "level_broke", "gave_back", "no_follow_through")
_LOSS_RULES = (
    (
        "stop_hunted",
        0.3,
        "stops_too_tight",
        "Stops were hunted in {n} of {total} losses: price hit the stop, then moved our way. "
        "Test a wider stop (beyond the wick plus about 1 ATR) in a backtest before changing "
        "it live.",
        LessonSeverity.MEDIUM,
    ),
    (
        "level_broke",
        0.4,
        "entry_against_real_break",
        "{n} of {total} losses were real breaks: the level did not hold and price kept going. "
        "Look for stronger confirmation or higher-timeframe agreement before entering.",
        LessonSeverity.MEDIUM,
    ),
    (
        "gave_back",
        0.3,
        "gave_back_profit",
        "{n} of {total} losses were at +1R or more first, then hit the stop. Test a bigger "
        "first take-profit at +1R (per strategy, in a backtest) before changing it live.",
        LessonSeverity.MEDIUM,
    ),
    (
        "no_follow_through",
        0.3,
        "no_follow_through",
        "{n} of {total} losses went nowhere (chop). Consider skipping quiet, low-volatility "
        "periods for these setups.",
        LessonSeverity.LOW,
    ),
)


def lessons_from(reviews: dict[str, int], strategies: list[object]) -> list[Lesson]:
    """Rule-based lessons; ``strategies`` are ``StrategyWeekRow``-like rows."""

    losses = sum(reviews.get(code, 0) for code in _LOSS_CODES)
    wins = reviews.get("won", 0) + reviews.get("won_left_on_table", 0)
    out: list[Lesson] = []
    for code, threshold, mistake, template, severity in _LOSS_RULES:
        n = reviews.get(code, 0)
        if losses and n >= 2 and n / losses >= threshold:
            out.append(Lesson(mistake, template.format(n=n, total=losses), severity))
    ran = reviews.get("won_left_on_table", 0)
    if wins and ran >= 2 and ran / wins >= 0.3:
        out.append(
            Lesson(
                "exited_too_early",
                f"After {ran} of {wins} winning exits, price ran at least 1R further. "
                "Give the runner more room (for example a longer trail).",
                LessonSeverity.LOW,
            )
        )
    for row in strategies:
        trades = int(getattr(row, "trades", 0))
        net_r = getattr(row, "net_r", 0)
        if trades >= 5 and net_r < 0:
            name = f"{getattr(row, 'family', '')} {getattr(row, 'timeframe', '')}".strip()
            out.append(
                Lesson(
                    "strategy_underperforming",
                    f"{name} lost {net_r:.2f}R over {trades} trades this month. "
                    "Compare it with its backtest before the next month.",
                    LessonSeverity.HIGH,
                )
            )
    return out


def format_month_lines(
    month: date, reviews: dict[str, int], lessons: list[Lesson], strategy_lines: list[str]
) -> list[str]:
    lines = [f"\U0001f5d3 Month in review: {month:%B %Y}"]
    lines += strategy_lines[1:] if strategy_lines else ["No demo trades closed."]
    wins = reviews.get("won", 0) + reviews.get("won_left_on_table", 0)
    ran = reviews.get("won_left_on_table", 0)
    if ran:
        lines.append(f"\U0001f4b8 Price ran 1R+ further after {ran} of {wins} winning exits")
    if lessons:
        lines.append("\U0001f4a1 Lessons (saved in the app's Lessons section to accept or reject):")
        lines += [f"{i}. {lesson.text}" for i, lesson in enumerate(lessons, start=1)]
    else:
        lines.append("\U0001f4a1 No clear pattern this month (too few trades or mixed results).")
    return lines


def save_lessons(
    session: Session, *, organization_id: UUID, user_id: UUID, month: date, lessons: list[Lesson]
) -> None:
    """Pending lesson candidates in the app's Lessons section (accept or reject there)."""

    from app.schemas.lesson import LessonCandidateCreate
    from app.services.lesson_candidate_service import LessonCandidateService

    service = LessonCandidateService(session)
    for lesson in lessons:
        service.create(
            LessonCandidateCreate(
                source_type=LessonSourceType.AGENT,
                lesson_text=lesson.text,
                mistake_type=lesson.mistake_type,
                severity=lesson.severity,
                analysis_metadata={"source": "demo_monthly_lessons", "month": f"{month:%Y-%m}"},
            ),
            organization_id=organization_id,
            user_id=user_id,
        )
