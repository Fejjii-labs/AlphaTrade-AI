"""Deterministic setup AST V1 contracts (Phase 3).

No model-generated code, no Python eval, no arbitrary expressions.
Operands are allowlisted fields or literals only.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self, cast
from uuid import UUID

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from app.schemas.common import StrictModel, TradeDirection
from app.schemas.nested_continuation import NestedContinuationSpec
from app.strategy_brain.sfp.contracts import SfpSpec
from app.strategy_brain.trendpulse_1r.contracts import TrendPulseSpec

COMPILER_VERSION = "setup-ast/v1"
GRAMMAR_VERSION = "setup-ast-grammar/v1"
WILDER_ATR_FEATURE_TYPE = "WILDER_ATR"
WILDER_ATR_FEATURE_VERSION = "wilder-atr/v1"


class AstNodeKind(StrEnum):
    LITERAL = "literal"
    FIELD = "field"
    UNARY = "unary"
    ARITHMETIC = "arithmetic"
    COMPARE = "compare"
    BOOLEAN = "boolean"
    WINDOW_AGGREGATE = "window_aggregate"
    CROSSES = "crosses"
    IS_MISSING = "is_missing"


class AstValueType(StrEnum):
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DURATION = "duration"
    TIMESTAMP = "timestamp"
    ENUM = "enum"
    MISSING = "missing"


class AstUnit(StrEnum):
    PRICE = "price"
    VOLUME = "volume"
    PRICE_VOLUME = "price_volume"
    RATIO = "ratio"
    COUNT = "count"
    DURATION = "duration"
    BOOLEAN = "boolean"
    DIMENSIONLESS = "dimensionless"


class CompareOp(StrEnum):
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"


class ArithmeticOp(StrEnum):
    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"
    DIVIDE = "divide"
    MIN = "min"
    MAX = "max"


class UnaryOp(StrEnum):
    NEGATE = "negate"
    NOT = "not"


class WindowAggOp(StrEnum):
    MIN = "min"
    MAX = "max"
    SUM = "sum"
    MEAN = "mean"
    COUNT = "count"
    FIRST = "first"
    LAST = "last"


class CrossesDirection(StrEnum):
    ABOVE = "above"
    BELOW = "below"


class AlignmentPolicy(StrEnum):
    EXACT = "exact"
    PREVIOUS_FINAL = "previous_final"
    BOUNDED_AS_OF = "bounded_as_of"


class OverlapPolicy(StrEnum):
    DISALLOW = "disallow"
    RESTART_AT_CURRENT = "restart_at_current"
    ALLOW_DISTINCT_START = "allow_distinct_start"


class FinalityRequirement(StrEnum):
    FINAL_ONLY = "final_only"


class FeatureRole(StrEnum):
    TRIGGER = "trigger"
    CONTEXT = "context"


ALLOWED_FIELD_SPECS: dict[str, tuple[AstValueType, AstUnit]] = {
    "ohlcv.open": (AstValueType.DECIMAL, AstUnit.PRICE),
    "ohlcv.high": (AstValueType.DECIMAL, AstUnit.PRICE),
    "ohlcv.low": (AstValueType.DECIMAL, AstUnit.PRICE),
    "ohlcv.close": (AstValueType.DECIMAL, AstUnit.PRICE),
    "ohlcv.volume": (AstValueType.DECIMAL, AstUnit.VOLUME),
    "trigger.ohlcv.high": (AstValueType.DECIMAL, AstUnit.PRICE),
    "feature.wilder_atr_v1.value": (AstValueType.DECIMAL, AstUnit.PRICE),
    "feature.wilder_atr_v1.context.value": (AstValueType.DECIMAL, AstUnit.PRICE),
    "swing.S": (AstValueType.DECIMAL, AstUnit.PRICE),
    "manual_level.R": (AstValueType.DECIMAL, AstUnit.PRICE),
    "cvd.at_bar_close": (AstValueType.DECIMAL, AstUnit.PRICE_VOLUME),
    "cvd.at_swing_close": (AstValueType.DECIMAL, AstUnit.PRICE_VOLUME),
    "flow.signed_quote_delta_T": (AstValueType.DECIMAL, AstUnit.PRICE_VOLUME),
    "flow.total_quote_volume_T": (AstValueType.DECIMAL, AstUnit.PRICE_VOLUME),
    "evidence.tick_size": (AstValueType.DECIMAL, AstUnit.PRICE),
    "predicate.finality": (AstValueType.BOOLEAN, AstUnit.BOOLEAN),
    "predicate.freshness": (AstValueType.BOOLEAN, AstUnit.BOOLEAN),
    "predicate.gap": (AstValueType.BOOLEAN, AstUnit.BOOLEAN),
}

ALLOWED_COMPARE_OPS: frozenset[str] = frozenset(op.value for op in CompareOp)
ALLOWED_ARITHMETIC_OPS: frozenset[str] = frozenset(op.value for op in ArithmeticOp)
ALLOWED_UNARY_OPS: frozenset[str] = frozenset(op.value for op in UnaryOp)
ALLOWED_WINDOW_OPS: frozenset[str] = frozenset(op.value for op in WindowAggOp)
ALLOWED_BOOLEAN_OPS: frozenset[str] = frozenset({"and", "or"})
ALLOWED_CROSSES_OPS: frozenset[str] = frozenset(op.value for op in CrossesDirection)

_COMPATIBLE_UNITS: dict[tuple[AstUnit, AstUnit], AstUnit] = {
    (AstUnit.PRICE, AstUnit.PRICE): AstUnit.PRICE,
    (AstUnit.VOLUME, AstUnit.VOLUME): AstUnit.VOLUME,
    (AstUnit.PRICE_VOLUME, AstUnit.PRICE_VOLUME): AstUnit.PRICE_VOLUME,
    (AstUnit.RATIO, AstUnit.RATIO): AstUnit.RATIO,
    (AstUnit.COUNT, AstUnit.COUNT): AstUnit.COUNT,
    (AstUnit.DURATION, AstUnit.DURATION): AstUnit.DURATION,
}


class FeatureRef(StrictModel):
    feature_type: str = Field(min_length=1, max_length=40)
    feature_version: str = Field(min_length=1, max_length=40)
    role: FeatureRole = FeatureRole.TRIGGER
    period: int = Field(default=14, ge=1, le=200)
    timeframe: str = Field(min_length=1, max_length=8)


class AstExpr(StrictModel):
    """Typed AST node. The compiler rejects unknown kinds, fields, and operators."""

    kind: AstNodeKind
    value: str | None = None
    value_type: AstValueType | None = None
    unit: AstUnit | None = None
    path: str | None = Field(default=None, max_length=80)
    op: str | None = Field(default=None, max_length=40)
    args: list[AstExpr] = Field(default_factory=list)
    strict: bool | None = None
    alignment: AlignmentPolicy | None = None
    window_bars: int | None = Field(default=None, ge=1, le=500)


class PatternStep(StrictModel):
    step_id: str = Field(min_length=1, max_length=80)
    predicate: AstExpr
    min_offset: int = Field(default=0, ge=0)
    max_offset: int = Field(default=0, ge=0)
    finality_requirement: FinalityRequirement = FinalityRequirement.FINAL_ONLY
    reset_on: AstExpr | None = None
    invalidate_on: AstExpr | None = None
    overlap_policy: OverlapPolicy = OverlapPolicy.DISALLOW

    @model_validator(mode="after")
    def _offset_order(self) -> Self:
        if self.max_offset < self.min_offset:
            raise ValueError("max_offset must be >= min_offset")
        return self


class PatternAst(StrictModel):
    """Compiled-or-authored detector pattern. Metadata is not a second strategy identity."""

    compiler_version: str = COMPILER_VERSION
    grammar_version: str = GRAMMAR_VERSION
    nested_spec: NestedContinuationSpec | None = None
    sfp_spec: SfpSpec | None = None
    trendpulse_spec: TrendPulseSpec | None = None
    symbols: list[str] = Field(min_length=1)
    trigger_timeframe: str = Field(min_length=1, max_length=8)
    context_timeframe: str | None = Field(default=None, max_length=8)
    direction: TradeDirection
    features: list[FeatureRef] = Field(default_factory=list)
    preconditions: AstExpr
    sequence: list[PatternStep] = Field(min_length=1)
    trigger: AstExpr
    invalidation: list[AstExpr] = Field(min_length=1)
    expiration_final_bars: int = Field(ge=1, le=2000)

    @model_validator(mode="after")
    def one_family(self) -> Self:
        if sum(s is not None for s in (self.nested_spec, self.sfp_spec, self.trendpulse_spec)) > 1:
            raise ValueError("A pattern must bind exactly one family")
        if self.sfp_spec is None and self.expiration_final_bars > 500:
            raise ValueError("Legacy pattern expiration_final_bars must be <= 500")
        return self

    @model_serializer(mode="wrap")
    def preserve_legacy_serialization(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        payload = cast(dict[str, Any], handler(self))
        if self.nested_spec is None:
            payload.pop("nested_spec", None)
        if self.sfp_spec is None:
            payload.pop("sfp_spec", None)
        if self.trendpulse_spec is None:
            payload.pop("trendpulse_spec", None)
        return payload


class CompiledAstDocument(StrictModel):
    compiler_version: str
    grammar_version: str
    pattern: PatternAst
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class CompileFailure(StrictModel):
    code: str
    message: str
    path: str | None = None


class CompileResult(StrictModel):
    status: str
    document: CompiledAstDocument | None = None
    failures: list[CompileFailure] = Field(default_factory=list)
    strategy_version_id: UUID | None = None
    organization_id: UUID | None = None


def lit_decimal(value: str, unit: AstUnit) -> AstExpr:
    return AstExpr(
        kind=AstNodeKind.LITERAL,
        value=value,
        value_type=AstValueType.DECIMAL,
        unit=unit,
    )


def lit_bool(value: bool) -> AstExpr:
    return AstExpr(
        kind=AstNodeKind.LITERAL,
        value="true" if value else "false",
        value_type=AstValueType.BOOLEAN,
        unit=AstUnit.BOOLEAN,
    )


def fld(path: str) -> AstExpr:
    spec = ALLOWED_FIELD_SPECS[path]
    return AstExpr(
        kind=AstNodeKind.FIELD,
        path=path,
        value_type=spec[0],
        unit=spec[1],
    )


def unary(op: UnaryOp, arg: AstExpr) -> AstExpr:
    return AstExpr(kind=AstNodeKind.UNARY, op=op.value, args=[arg])


def arith(op: ArithmeticOp, *args: AstExpr) -> AstExpr:
    return AstExpr(kind=AstNodeKind.ARITHMETIC, op=op.value, args=list(args))


def cmp(op: CompareOp, left: AstExpr, right: AstExpr) -> AstExpr:
    return AstExpr(kind=AstNodeKind.COMPARE, op=op.value, args=[left, right])


def boolean(op: str, *args: AstExpr) -> AstExpr:
    return AstExpr(kind=AstNodeKind.BOOLEAN, op=op, args=list(args))


def window(op: WindowAggOp, series: AstExpr, *, bars: int) -> AstExpr:
    return AstExpr(
        kind=AstNodeKind.WINDOW_AGGREGATE,
        op=op.value,
        args=[series],
        window_bars=bars,
    )


def is_missing(expr: AstExpr) -> AstExpr:
    return AstExpr(kind=AstNodeKind.IS_MISSING, args=[expr])


def abs_expr(expr: AstExpr) -> AstExpr:
    return arith(ArithmeticOp.MAX, expr, unary(UnaryOp.NEGATE, expr))


def result_unit(left: AstUnit, op: ArithmeticOp, right: AstUnit) -> AstUnit | None:
    if op in {ArithmeticOp.ADD, ArithmeticOp.SUBTRACT, ArithmeticOp.MIN, ArithmeticOp.MAX}:
        return _COMPATIBLE_UNITS.get((left, right))
    if op is ArithmeticOp.MULTIPLY:
        if left is AstUnit.PRICE and right is AstUnit.VOLUME:
            return AstUnit.PRICE_VOLUME
        if left is AstUnit.VOLUME and right is AstUnit.PRICE:
            return AstUnit.PRICE_VOLUME
        if AstUnit.RATIO in {left, right}:
            other = right if left is AstUnit.RATIO else left
            return other
        if left == right:
            return left
        return None
    if op is ArithmeticOp.DIVIDE:
        if left == right:
            return AstUnit.RATIO
        if left is AstUnit.PRICE_VOLUME and right is AstUnit.PRICE_VOLUME:
            return AstUnit.RATIO
        if right is AstUnit.RATIO:
            return left
        return None
    return None
