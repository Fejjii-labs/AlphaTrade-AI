"""Deterministic setup grader.

Uses only the trend and momentum scores from ``app.analysis.confidence``.
The first grade bound to a setup is the grade that sticks. An AI explanation
may describe that grade. It cannot raise it, and it is not an input.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.analysis.confidence import momentum_score, trend_alignment_score
from app.analysis.types import Indicators, MarketStructure
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.profile import GRADE_ORDER, Grade

GRADER_VERSION = "deterministic-grade/v1"

# Exact pairs produced by the confidence helpers (0, 0.5, 1). An unknown pair
# is ungraded rather than interpolated.
_GRADE_TABLE: dict[tuple[Decimal, Decimal], Grade] = {
    (Decimal("1"), Decimal("1")): Grade.A_PLUS,
    (Decimal("1"), Decimal("0.5")): Grade.A,
    (Decimal("0.5"), Decimal("1")): Grade.A,
    (Decimal("1"), Decimal("0")): Grade.B_PLUS,
    (Decimal("0.5"), Decimal("0.5")): Grade.B_PLUS,
    (Decimal("0"), Decimal("1")): Grade.B,
    (Decimal("0.5"), Decimal("0")): Grade.B,
    (Decimal("0"), Decimal("0.5")): Grade.B,
    (Decimal("0"), Decimal("0")): Grade.C,
}

_RANK = {grade: index for index, grade in enumerate(GRADE_ORDER)}


@dataclass(frozen=True)
class GradeDecision:
    """A grade, or a refusal when the setup cannot be graded."""

    grade: Grade | None
    grader_version: str
    trend_score: Decimal | None
    momentum_score: Decimal | None
    refusal: str | None

    @property
    def tradable(self) -> bool:
        return self.grade is not None and self.grade is not Grade.C and self.refusal is None


def grade_setup(
    *,
    direction: str | None,
    structure: MarketStructure,
    indicators: Indicators,
) -> GradeDecision:
    """Grade one setup from deterministic features. Missing direction is ungraded."""

    if direction not in {"long", "short"}:
        return GradeDecision(None, GRADER_VERSION, None, None, "ungraded")
    trend = _score(trend_alignment_score(direction, structure))
    momentum = _score(momentum_score(direction, indicators))
    grade = _GRADE_TABLE.get((trend, momentum))
    if grade is None:
        return GradeDecision(None, GRADER_VERSION, trend, momentum, "ungraded")
    return GradeDecision(grade, GRADER_VERSION, trend, momentum, None)


def bind_first_grade(existing: GradeDecision | None, proposed: GradeDecision) -> GradeDecision:
    """The first successful or refused grade for a setup is the one that binds."""

    if existing is not None:
        return existing
    return proposed


def apply_ai_grade(bound: Grade, suggested: Grade, explanation: str) -> tuple[Grade, str]:
    """Keep the bound grade. A higher AI suggestion is ignored.

    ``explanation`` is returned unchanged so a caller can store it beside the
    grade. It is not parsed and cannot change the grade.
    """

    if _RANK[suggested] < _RANK[bound]:
        # A lower suggestion would still be a change to the bound grade.
        # The first grade binds, so the suggestion is recorded and not applied.
        return bound, explanation
    return bound, explanation


class GradeBook:
    """In-memory first-bind store. The database binding is the durable copy."""

    def __init__(self) -> None:
        self._bound: dict[str, GradeDecision] = {}

    def bind(self, setup_id: str, decision: GradeDecision) -> GradeDecision:
        if not setup_id:
            raise RiskPolicyRejected("setup_id_missing")
        existing = self._bound.get(setup_id)
        if existing is not None:
            return existing
        self._bound[setup_id] = decision
        return decision

    def get(self, setup_id: str) -> GradeDecision | None:
        return self._bound.get(setup_id)


def _score(value: float) -> Decimal:
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise RiskPolicyRejected("grader_score_invalid") from exc
    if not number.is_finite():
        raise RiskPolicyRejected("grader_score_invalid")
    return number
