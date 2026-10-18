"""Risk-policy profiles. Every number is validated against the code ceilings.

Placeholders are conservative and identical across kinds: 1% risk on tradable
grades, no trade on C, 1x leverage, and both autonomous flags forced off.
Sofien replaces the placeholders later by applying a new version. This module
never clamps an out-of-range number; it rejects the profile.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.services.risk_policy.ceilings import (
    HARD_MAX_AGGREGATE_OPEN_RISK_PERCENT,
    HARD_MAX_COST_SHARE_OF_R,
    HARD_MAX_DAILY_LOSS_PERCENT,
    HARD_MAX_LEVERAGE,
    notional_ceiling_for,
    risk_ceiling_for,
)
from app.services.risk_policy.errors import RiskPolicyRejected

_QUANTUM = Decimal("0.00000001")


class PolicyKind(StrEnum):
    """Named profile. Paper and demo are separate even while the numbers match."""

    CONSERVATIVE = "conservative"
    STANDARD = "standard"
    AGGRESSIVE = "aggressive"
    INTERNAL_PAPER = "internal_paper"
    DEMO = "demo"


class Grade(StrEnum):
    """Setup grade. C and a missing grade are not tradable."""

    A_PLUS = "A+"
    A = "A"
    B_PLUS = "B+"
    B = "B"
    C = "C"


TRADABLE_GRADES: tuple[Grade, ...] = (Grade.A_PLUS, Grade.A, Grade.B_PLUS, Grade.B)
GRADE_ORDER: tuple[Grade, ...] = (
    Grade.A_PLUS,
    Grade.A,
    Grade.B_PLUS,
    Grade.B,
    Grade.C,
)


class ChangeClass(StrEnum):
    INITIAL = "initial"
    TIGHTENING = "tightening"
    LOOSENING = "loosening"


@dataclass(frozen=True)
class RiskPolicyProfile:
    """One complete policy. Percents are percent points (1 means 1% of equity).

    ``min_stop_absolute_floor`` is a fraction of entry price, not a currency
    amount, so the same profile works for any instrument. The minimum stop
    distance is ``max(k * ATR, entry * floor)``.
    """

    kind: PolicyKind
    risk_percent_by_grade: dict[Grade, Decimal]
    max_leverage: Decimal
    daily_loss_limit_percent: Decimal
    max_aggregate_open_risk_percent: Decimal
    max_open_positions: int
    max_notional_per_trade_percent: Decimal
    max_aggregate_notional_percent: Decimal
    min_stop_atr_multiple: Decimal
    min_stop_absolute_floor: Decimal
    max_cost_share_of_r: Decimal
    drawdown_breaker_percent: Decimal
    consecutive_loss_limit: int
    autonomous_internal_paper_enabled: bool = False
    autonomous_blofin_demo_enabled: bool = False
    profile_name: str = ""
    default_leverage: Decimal = Decimal("1")
    # Explicit, audited paper/demo data-collection switch. Default off.
    # It does not arm either autonomous flag and it does not apply to live.
    paper_demo_data_collection: bool = False

    def risk_percent(self, grade: Grade) -> Decimal:
        try:
            return self.risk_percent_by_grade[grade]
        except KeyError as exc:
            raise RiskPolicyRejected("grade_missing") from exc


def placeholder_profile(kind: PolicyKind) -> RiskPolicyProfile:
    """Conservative placeholder. Every kind starts here. Flags stay off."""

    profile = RiskPolicyProfile(
        kind=kind,
        risk_percent_by_grade={
            Grade.A_PLUS: Decimal("1"),
            Grade.A: Decimal("1"),
            Grade.B_PLUS: Decimal("1"),
            Grade.B: Decimal("1"),
            Grade.C: Decimal("0"),
        },
        max_leverage=Decimal("1"),
        daily_loss_limit_percent=Decimal("1"),
        max_aggregate_open_risk_percent=Decimal("1"),
        max_open_positions=1,
        max_notional_per_trade_percent=Decimal("10"),
        max_aggregate_notional_percent=Decimal("25"),
        min_stop_atr_multiple=Decimal("1"),
        min_stop_absolute_floor=Decimal("0.001"),
        max_cost_share_of_r=Decimal("0.20"),
        drawdown_breaker_percent=Decimal("10"),
        consecutive_loss_limit=4,
        autonomous_internal_paper_enabled=False,
        autonomous_blofin_demo_enabled=False,
        profile_name="",
        default_leverage=Decimal("1"),
        paper_demo_data_collection=False,
    )
    validate_profile(profile)
    return profile


def profile_with(kind: PolicyKind, **overrides: object) -> RiskPolicyProfile:
    """Copy the placeholder and validate the result. Used by tests and apply."""

    base = placeholder_profile(kind)
    data = _as_mapping(base)
    data.update(overrides)
    grades = data.get("risk_percent_by_grade")
    if not isinstance(grades, dict):
        raise RiskPolicyRejected("grade_map_invalid")
    profile = RiskPolicyProfile(
        kind=kind,
        risk_percent_by_grade={Grade(str(key)): _decimal(value) for key, value in grades.items()},
        max_leverage=_decimal(data["max_leverage"]),
        daily_loss_limit_percent=_decimal(data["daily_loss_limit_percent"]),
        max_aggregate_open_risk_percent=_decimal(data["max_aggregate_open_risk_percent"]),
        max_open_positions=_positive_int(data["max_open_positions"], "max_open_positions"),
        max_notional_per_trade_percent=_decimal(data["max_notional_per_trade_percent"]),
        max_aggregate_notional_percent=_decimal(data["max_aggregate_notional_percent"]),
        min_stop_atr_multiple=_decimal(data["min_stop_atr_multiple"]),
        min_stop_absolute_floor=_decimal(data["min_stop_absolute_floor"]),
        max_cost_share_of_r=_decimal(data["max_cost_share_of_r"]),
        drawdown_breaker_percent=_decimal(data["drawdown_breaker_percent"]),
        consecutive_loss_limit=_positive_int(
            data["consecutive_loss_limit"], "consecutive_loss_limit"
        ),
        autonomous_internal_paper_enabled=_bool_flag(
            data["autonomous_internal_paper_enabled"], "autonomous_internal_paper_enabled"
        ),
        autonomous_blofin_demo_enabled=_bool_flag(
            data["autonomous_blofin_demo_enabled"], "autonomous_blofin_demo_enabled"
        ),
        profile_name=_profile_name(data.get("profile_name", "")),
        default_leverage=_decimal(data.get("default_leverage", Decimal("1"))),
        paper_demo_data_collection=_bool_flag(
            data.get("paper_demo_data_collection", False), "paper_demo_data_collection"
        ),
    )
    validate_profile(profile)
    return profile


def validate_profile(profile: RiskPolicyProfile) -> None:
    """Reject anything outside the code ceilings. Do not clamp."""

    if not isinstance(profile.kind, PolicyKind):
        raise RiskPolicyRejected("kind_invalid")
    grades = profile.risk_percent_by_grade
    if set(grades) != set(GRADE_ORDER):
        raise RiskPolicyRejected("grade_map_invalid")
    ceiling = risk_ceiling_for(profile.kind)
    for grade in TRADABLE_GRADES:
        _require_percent(
            grades[grade],
            lo=Decimal("0"),
            hi=ceiling,
            code="risk_per_trade_exceeds_ceiling",
            positive=True,
        )
    if grades[Grade.C] != Decimal("0"):
        raise RiskPolicyRejected("c_grade_must_not_trade")
    _require_percent(
        profile.max_leverage,
        lo=Decimal("1"),
        hi=HARD_MAX_LEVERAGE,
        code="leverage_exceeds_ceiling",
        positive=True,
    )
    _require_percent(
        profile.daily_loss_limit_percent,
        lo=Decimal("0"),
        hi=HARD_MAX_DAILY_LOSS_PERCENT,
        code="daily_loss_exceeds_ceiling",
        positive=True,
    )
    _require_percent(
        profile.max_aggregate_open_risk_percent,
        lo=Decimal("0"),
        hi=HARD_MAX_AGGREGATE_OPEN_RISK_PERCENT,
        code="aggregate_open_risk_exceeds_ceiling",
        positive=True,
    )
    if (
        not isinstance(profile.max_open_positions, int)
        or isinstance(profile.max_open_positions, bool)
        or profile.max_open_positions < 1
        or profile.max_open_positions > 100
    ):
        raise RiskPolicyRejected("max_open_positions_invalid")
    notional_ceiling = notional_ceiling_for(profile.kind)
    _require_percent(
        profile.max_notional_per_trade_percent,
        lo=Decimal("0"),
        hi=notional_ceiling,
        code="notional_per_trade_invalid",
        positive=True,
    )
    _require_percent(
        profile.max_aggregate_notional_percent,
        lo=Decimal("0"),
        hi=notional_ceiling,
        code="aggregate_notional_invalid",
        positive=True,
    )
    if profile.max_notional_per_trade_percent >= profile.max_aggregate_notional_percent:
        # The PR 258 ledger refuses a symbol cap that equals total notional.
        raise RiskPolicyRejected("symbol_cap_not_independent_of_notional")
    _require_percent(
        profile.min_stop_atr_multiple,
        lo=Decimal("0"),
        hi=Decimal("100"),
        code="min_stop_multiple_invalid",
        positive=True,
    )
    _require_percent(
        profile.min_stop_absolute_floor,
        lo=Decimal("0"),
        hi=Decimal("1"),
        code="min_stop_floor_invalid",
        positive=True,
    )
    _require_percent(
        profile.max_cost_share_of_r,
        lo=Decimal("0"),
        hi=HARD_MAX_COST_SHARE_OF_R,
        code="cost_share_exceeds_ceiling",
        positive=True,
    )
    _require_percent(
        profile.drawdown_breaker_percent,
        lo=Decimal("0"),
        hi=Decimal("100"),
        code="drawdown_breaker_invalid",
        positive=True,
    )
    if (
        not isinstance(profile.consecutive_loss_limit, int)
        or isinstance(profile.consecutive_loss_limit, bool)
        or profile.consecutive_loss_limit < 1
        or profile.consecutive_loss_limit > 20
    ):
        raise RiskPolicyRejected("consecutive_loss_limit_invalid")
    if profile.autonomous_internal_paper_enabled or profile.autonomous_blofin_demo_enabled:
        raise RiskPolicyRejected("autonomous_flags_must_stay_off")
    _profile_name(profile.profile_name)
    if (
        not isinstance(profile.default_leverage, Decimal)
        or not profile.default_leverage.is_finite()
        or profile.default_leverage < 1
        or profile.default_leverage > profile.max_leverage
        or profile.default_leverage > HARD_MAX_LEVERAGE
    ):
        raise RiskPolicyRejected("default_leverage_invalid")
    if not isinstance(profile.paper_demo_data_collection, bool):
        raise RiskPolicyRejected("paper_demo_data_collection_invalid")
    if profile.paper_demo_data_collection and profile.kind not in {
        PolicyKind.INTERNAL_PAPER,
        PolicyKind.DEMO,
    }:
        raise RiskPolicyRejected("data_collection_not_paper_or_demo")


def canonical_profile(profile: RiskPolicyProfile) -> dict[str, str]:
    """Stable string form used for diffs and hashes."""

    validate_profile(profile)
    grades = profile.risk_percent_by_grade
    return {
        "kind": profile.kind.value,
        "risk_percent_a_plus": _text(grades[Grade.A_PLUS]),
        "risk_percent_a": _text(grades[Grade.A]),
        "risk_percent_b_plus": _text(grades[Grade.B_PLUS]),
        "risk_percent_b": _text(grades[Grade.B]),
        "risk_percent_c": _text(grades[Grade.C]),
        "max_leverage": _text(profile.max_leverage),
        "daily_loss_limit_percent": _text(profile.daily_loss_limit_percent),
        "max_aggregate_open_risk_percent": _text(profile.max_aggregate_open_risk_percent),
        "max_open_positions": str(profile.max_open_positions),
        "max_notional_per_trade_percent": _text(profile.max_notional_per_trade_percent),
        "max_aggregate_notional_percent": _text(profile.max_aggregate_notional_percent),
        "min_stop_atr_multiple": _text(profile.min_stop_atr_multiple),
        "min_stop_absolute_floor": _text(profile.min_stop_absolute_floor),
        "max_cost_share_of_r": _text(profile.max_cost_share_of_r),
        "drawdown_breaker_percent": _text(profile.drawdown_breaker_percent),
        "consecutive_loss_limit": str(profile.consecutive_loss_limit),
        "autonomous_internal_paper_enabled": "false",
        "autonomous_blofin_demo_enabled": "false",
        "profile_name": profile.profile_name,
        "default_leverage": _text(profile.default_leverage),
        "paper_demo_data_collection": "true" if profile.paper_demo_data_collection else "false",
    }


def profile_from_canonical(payload: dict[str, str]) -> RiskPolicyProfile:
    """Rebuild a profile from its stored canonical form and validate it again."""

    try:
        kind = PolicyKind(payload["kind"])
        grades = {
            Grade.A_PLUS: _decimal(payload["risk_percent_a_plus"]),
            Grade.A: _decimal(payload["risk_percent_a"]),
            Grade.B_PLUS: _decimal(payload["risk_percent_b_plus"]),
            Grade.B: _decimal(payload["risk_percent_b"]),
            Grade.C: _decimal(payload["risk_percent_c"]),
        }
        profile = RiskPolicyProfile(
            kind=kind,
            risk_percent_by_grade=grades,
            max_leverage=_decimal(payload["max_leverage"]),
            daily_loss_limit_percent=_decimal(payload["daily_loss_limit_percent"]),
            max_aggregate_open_risk_percent=_decimal(payload["max_aggregate_open_risk_percent"]),
            max_open_positions=int(payload["max_open_positions"]),
            max_notional_per_trade_percent=_decimal(payload["max_notional_per_trade_percent"]),
            max_aggregate_notional_percent=_decimal(payload["max_aggregate_notional_percent"]),
            min_stop_atr_multiple=_decimal(payload["min_stop_atr_multiple"]),
            min_stop_absolute_floor=_decimal(payload["min_stop_absolute_floor"]),
            max_cost_share_of_r=_decimal(payload["max_cost_share_of_r"]),
            drawdown_breaker_percent=_decimal(payload["drawdown_breaker_percent"]),
            consecutive_loss_limit=int(payload["consecutive_loss_limit"]),
            autonomous_internal_paper_enabled=payload["autonomous_internal_paper_enabled"]
            == "true",
            autonomous_blofin_demo_enabled=payload["autonomous_blofin_demo_enabled"] == "true",
            profile_name=_profile_name(payload.get("profile_name", "")),
            default_leverage=_decimal(payload.get("default_leverage", "1")),
            paper_demo_data_collection=payload.get("paper_demo_data_collection", "false")
            == "true",
        )
    except (KeyError, ValueError) as exc:
        raise RiskPolicyRejected("profile_payload_invalid") from exc
    validate_profile(profile)
    return profile


def profile_hash(profile: RiskPolicyProfile) -> str:
    blob = json.dumps(canonical_profile(profile), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def profile_diff(
    previous: RiskPolicyProfile | None, proposed: RiskPolicyProfile
) -> dict[str, dict[str, str]]:
    """Field-level diff. An initial version diffs against empty prior values."""

    current = canonical_profile(proposed)
    prior = canonical_profile(previous) if previous is not None else {}
    return {
        key: {"from": prior.get(key, ""), "to": value}
        for key, value in current.items()
        if prior.get(key, "") != value
    }


# Raising one of these allows more risk. Raising a stop floor or ATR multiple
# does the opposite: the stop must be farther away, which is a tightening.
_LOOSENS_WHEN_INCREASED = (
    "risk_percent_a_plus",
    "risk_percent_a",
    "risk_percent_b_plus",
    "risk_percent_b",
    "risk_percent_c",
    "max_leverage",
    "default_leverage",
    "daily_loss_limit_percent",
    "max_aggregate_open_risk_percent",
    "max_open_positions",
    "max_notional_per_trade_percent",
    "max_aggregate_notional_percent",
    "max_cost_share_of_r",
    "drawdown_breaker_percent",
    "consecutive_loss_limit",
)
_LOOSENS_WHEN_DECREASED = (
    "min_stop_atr_multiple",
    "min_stop_absolute_floor",
)


def classify_change(
    previous: RiskPolicyProfile | None,
    proposed: RiskPolicyProfile,
    *,
    unchanged: ChangeClass | None = None,
) -> ChangeClass:
    """A mixed change is a loosening. Fail closed.

    ``unchanged`` lets the first stored profile match the live placeholder
    without raising ``no_change``. Any real difference is classified normally,
    so a looser first profile is a loosening.
    """

    if previous is None:
        validate_profile(proposed)
        return ChangeClass.INITIAL
    diff = profile_diff(previous, proposed)
    if not diff:
        if unchanged is not None:
            return unchanged
        raise RiskPolicyRejected("no_change")
    loosened = False
    for key in _LOOSENS_WHEN_INCREASED:
        if key in diff and _decimal(diff[key]["to"]) > _decimal(diff[key]["from"]):
            loosened = True
    for key in _LOOSENS_WHEN_DECREASED:
        if key in diff and _decimal(diff[key]["to"]) < _decimal(diff[key]["from"]):
            loosened = True
    collection = diff.get("paper_demo_data_collection")
    if collection is not None and collection["to"] == "true" and collection["from"] != "true":
        loosened = True
    if loosened:
        return ChangeClass.LOOSENING
    return ChangeClass.TIGHTENING


def _as_mapping(profile: RiskPolicyProfile) -> dict[str, object]:
    return {
        "risk_percent_by_grade": dict(profile.risk_percent_by_grade),
        "max_leverage": profile.max_leverage,
        "daily_loss_limit_percent": profile.daily_loss_limit_percent,
        "max_aggregate_open_risk_percent": profile.max_aggregate_open_risk_percent,
        "max_open_positions": profile.max_open_positions,
        "max_notional_per_trade_percent": profile.max_notional_per_trade_percent,
        "max_aggregate_notional_percent": profile.max_aggregate_notional_percent,
        "min_stop_atr_multiple": profile.min_stop_atr_multiple,
        "min_stop_absolute_floor": profile.min_stop_absolute_floor,
        "max_cost_share_of_r": profile.max_cost_share_of_r,
        "drawdown_breaker_percent": profile.drawdown_breaker_percent,
        "consecutive_loss_limit": profile.consecutive_loss_limit,
        "autonomous_internal_paper_enabled": profile.autonomous_internal_paper_enabled,
        "autonomous_blofin_demo_enabled": profile.autonomous_blofin_demo_enabled,
        "profile_name": profile.profile_name,
        "default_leverage": profile.default_leverage,
        "paper_demo_data_collection": profile.paper_demo_data_collection,
    }


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise RiskPolicyRejected("not_a_number")
    if isinstance(value, Decimal):
        number = value
    else:
        try:
            number = Decimal(str(value))
        except Exception as exc:
            raise RiskPolicyRejected("not_a_number") from exc
    if not number.is_finite():
        raise RiskPolicyRejected("not_a_number")
    return number


def _text(value: Decimal) -> str:
    return format(value.quantize(_QUANTUM), "f")


def _require_percent(
    value: Decimal,
    *,
    lo: Decimal,
    hi: Decimal,
    code: str,
    positive: bool,
) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise RiskPolicyRejected(code)
    if value > hi or value < lo:
        raise RiskPolicyRejected(code)
    if positive and value <= 0:
        raise RiskPolicyRejected(code)


def _positive_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RiskPolicyRejected(code)
    return value


def sofien_aggressive_profile(kind: PolicyKind) -> RiskPolicyProfile:
    """Selectable paper or demo profile. It is not the default and it is not applied.

    Sofien's instruction of 2026-10-18: A+ 5%, A 3%, B+ 2%, B 1%, C does not
    trade, default leverage 10x, maximum 20x. Loss is the grade risk at the
    stop. Leverage sets margin. ``paper_demo_data_collection`` is explicit so
    the calibration gate stays closed unless this audited setting is applied.
    Notional caps are margin at leverage, as a percent of equity: 100% margin
    times 10x per trade, and 100% margin times 20x in aggregate.
    """

    if kind not in {PolicyKind.INTERNAL_PAPER, PolicyKind.DEMO}:
        raise RiskPolicyRejected("aggressive_profile_is_paper_or_demo_only")
    name = (
        "sofien_aggressive_paper"
        if kind is PolicyKind.INTERNAL_PAPER
        else "sofien_aggressive_demo"
    )
    return profile_with(
        kind,
        profile_name=name,
        risk_percent_by_grade={
            Grade.A_PLUS: Decimal("5"),
            Grade.A: Decimal("3"),
            Grade.B_PLUS: Decimal("2"),
            Grade.B: Decimal("1"),
            Grade.C: Decimal("0"),
        },
        max_leverage=Decimal("20"),
        default_leverage=Decimal("10"),
        daily_loss_limit_percent=Decimal("6"),
        max_aggregate_open_risk_percent=Decimal("6"),
        max_open_positions=3,
        max_notional_per_trade_percent=Decimal("1000"),
        max_aggregate_notional_percent=Decimal("2000"),
        paper_demo_data_collection=True,
    )


def _profile_name(value: object) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise RiskPolicyRejected("profile_name_invalid")
    if value and re.fullmatch(r"[a-z0-9_]+", value) is None:
        raise RiskPolicyRejected("profile_name_invalid")
    return value


def _bool_flag(value: object, code: str) -> bool:
    if not isinstance(value, bool):
        raise RiskPolicyRejected(code)
    return value
