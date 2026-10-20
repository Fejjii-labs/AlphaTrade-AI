"""Out-of-sample calibration report. Promotion stays manual.

A grade can be marked eligible for a later human promotion only when all of
the following hold for one frozen grader version, using out-of-sample trades:

* at least 100 trades in each tradable grade
* expectancy strictly ordered A+ > A > B+ > B
* the lower 90% confidence bound of A+ and of A is above zero
* every walk-forward fold keeps A+ and A expectancy above zero
* the same order and bounds hold after subtracting recorded slippage

Failing those checks with a full sample flags auto-demotion on the report.
This module does not change a profile.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

from app.services.risk_policy.ceilings import PAPER_DEMO_KINDS
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.grader import GRADER_VERSION
from app.services.risk_policy.profile import TRADABLE_GRADES, Grade, RiskPolicyProfile

MIN_TRADES = 100
PLACEHOLDER_RISK_PERCENT = Decimal("1")
# One-sided 90% normal quantile.
_Z_90 = Decimal("1.6448536269514722")


@dataclass(frozen=True)
class CalibrationSample:
    grade: Grade
    grader_version: str
    r_multiple: Decimal
    in_sample: bool
    walk_forward_fold: int
    slippage_r: Decimal


@dataclass(frozen=True)
class GradeCalibration:
    grade: Grade
    trades: int
    expectancy: Decimal | None
    lower_90_bound: Decimal | None
    haircut_expectancy: Decimal | None
    haircut_lower_90_bound: Decimal | None


@dataclass(frozen=True)
class PromotionReport:
    grader_version: str
    grades: tuple[GradeCalibration, ...]
    ordered: bool
    walk_forward_ok: bool
    eligible: bool
    auto_demotion: tuple[Grade, ...]
    promotion_applied: bool = False
    promotion_mode: str = "manual_only"


def promotion_report(
    samples: tuple[CalibrationSample, ...] | list[CalibrationSample],
    *,
    grader_version: str = GRADER_VERSION,
) -> PromotionReport:
    """Build the eligibility report. ``promotion_applied`` is always false."""

    usable = [
        sample
        for sample in samples
        if sample.grader_version == grader_version and not sample.in_sample
    ]
    by_grade = {
        grade: [sample for sample in usable if sample.grade is grade] for grade in TRADABLE_GRADES
    }
    stats = tuple(_grade_stats(grade, by_grade[grade]) for grade in TRADABLE_GRADES)
    ordered = _ordered(stats)
    walk_ok = _walk_forward(by_grade)
    counts_ok = all(item.trades >= MIN_TRADES for item in stats)
    bounds_ok = all(
        item.lower_90_bound is not None
        and item.haircut_lower_90_bound is not None
        and item.lower_90_bound > 0
        and item.haircut_lower_90_bound > 0
        for item in stats
        if item.grade in {Grade.A_PLUS, Grade.A}
    ) and all(item.trades >= MIN_TRADES for item in stats if item.grade in {Grade.A_PLUS, Grade.A})
    haircut_ordered = _ordered(stats, haircut=True)
    eligible = counts_ok and ordered and haircut_ordered and walk_ok and bounds_ok
    demotions = _demotions(stats, ordered=ordered and haircut_ordered)
    return PromotionReport(
        grader_version=grader_version,
        grades=stats,
        ordered=ordered and haircut_ordered,
        walk_forward_ok=walk_ok,
        eligible=eligible,
        auto_demotion=demotions,
        promotion_applied=False,
    )


def assert_promotion_allows(profile: RiskPolicyProfile, report: PromotionReport | None) -> None:
    """A tradable grade above the 1% placeholder needs a stored eligible report.

    The report must be for the current grader version. Promotion itself stays manual.
    """

    above = any(profile.risk_percent(grade) > PLACEHOLDER_RISK_PERCENT for grade in TRADABLE_GRADES)
    if not above:
        return
    # AT-ADR-111 amends AT-ADR-109 (c) for data collection only. The kind is
    # the server-set paper or demo kind, the same set as the 5% ceiling.
    # A caller cannot open this by naming another kind. Promotion stays manual.
    if profile.paper_demo_data_collection and profile.kind.value in PAPER_DEMO_KINDS:
        return
    if (
        report is None
        or report.grader_version != GRADER_VERSION
        or not report.eligible
        or not report.ordered
        or not report.walk_forward_ok
        or report.promotion_applied
    ):
        raise RiskPolicyRejected("grade_above_one_percent_requires_promotion")


def promote(*_args: object, **_kwargs: object) -> None:
    """Promotion is a human action outside this package."""

    raise RiskPolicyRejected("promotion_is_manual")


def _grade_stats(grade: Grade, samples: list[CalibrationSample]) -> GradeCalibration:
    raw = [sample.r_multiple for sample in samples]
    haircut = [sample.r_multiple - sample.slippage_r for sample in samples]
    return GradeCalibration(
        grade=grade,
        trades=len(samples),
        expectancy=_mean(raw),
        lower_90_bound=_lower_bound(raw),
        haircut_expectancy=_mean(haircut),
        haircut_lower_90_bound=_lower_bound(haircut),
    )


def _mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _lower_bound(values: list[Decimal]) -> Decimal | None:
    if len(values) < 2:
        return None
    mean = _mean(values)
    if mean is None:
        return None
    variance = sum((value - mean) ** 2 for value in values) / Decimal(len(values) - 1)
    if variance < 0:
        return None
    if variance == 0:
        return mean
    std = Decimal(str(math.sqrt(float(variance))))
    return mean - _Z_90 * std / Decimal(len(values)).sqrt()


def _ordered(stats: tuple[GradeCalibration, ...], *, haircut: bool = False) -> bool:
    expectancies: list[Decimal] = []
    for item in stats:
        value = item.haircut_expectancy if haircut else item.expectancy
        if value is None:
            return False
        expectancies.append(value)
    return all(left > right for left, right in pairwise(expectancies))


def _walk_forward(by_grade: dict[Grade, list[CalibrationSample]]) -> bool:
    folds = {sample.walk_forward_fold for rows in by_grade.values() for sample in rows}
    if len(folds) < 2:
        return False
    for fold in folds:
        for grade in (Grade.A_PLUS, Grade.A):
            rows = [
                sample.r_multiple for sample in by_grade[grade] if sample.walk_forward_fold == fold
            ]
            mean = _mean(rows)
            if mean is None or mean <= 0:
                return False
    return True


def _demotions(stats: tuple[GradeCalibration, ...], *, ordered: bool) -> tuple[Grade, ...]:
    """Flag grades that have a full sample and then fail the bar. Do not change a profile."""

    flagged: list[Grade] = []
    for item in stats:
        if item.trades < MIN_TRADES:
            continue
        bound = item.haircut_lower_90_bound
        if bound is None or bound <= 0 or not ordered:
            flagged.append(item.grade)
    return tuple(flagged)
