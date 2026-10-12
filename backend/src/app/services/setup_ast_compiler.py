"""Deterministic setup AST compiler (Phase 3)."""

from __future__ import annotations

from typing import Any

from app.core.errors import ValidationAppError
from app.schemas.common import SetupCompileStatus
from app.schemas.setup_ast import (
    ALLOWED_ARITHMETIC_OPS,
    ALLOWED_BOOLEAN_OPS,
    ALLOWED_COMPARE_OPS,
    ALLOWED_CROSSES_OPS,
    ALLOWED_FIELD_SPECS,
    ALLOWED_UNARY_OPS,
    ALLOWED_WINDOW_OPS,
    WILDER_ATR_FEATURE_TYPE,
    WILDER_ATR_FEATURE_VERSION,
    ArithmeticOp,
    AstExpr,
    AstNodeKind,
    AstUnit,
    CompareOp,
    CompiledAstDocument,
    CompileFailure,
    CompileResult,
    FeatureRef,
    FeatureRole,
    PatternAst,
    PatternStep,
    UnaryOp,
    WindowAggOp,
    abs_expr,
    arith,
    boolean,
    cmp,
    fld,
    is_missing,
    lit_bool,
    lit_decimal,
    result_unit,
    unary,
    window,
)
from app.schemas.strategy_library import StrategyCard
from app.schemas.strategy_pattern_spec import (
    FIRST_SLICE_ATR_PERIOD,
    FIRST_SLICE_CONTEXT_TF,
    FIRST_SLICE_DIRECTION,
    FIRST_SLICE_KIND,
    FIRST_SLICE_NAME,
    FIRST_SLICE_REQUIRED_SEQUENCE,
    FIRST_SLICE_SYMBOL,
    FIRST_SLICE_TRIGGER_TF,
    FirstSliceAuthoredPatternSpec,
    PatternInvalidationSemantics,
    PatternResetSemantics,
    canonical_first_slice_authored_spec,
)
from app.schemas.structured_rules import StructuredRules
from app.services.canonical_serialization import canonical_sha256

# FIRST_SLICE_TRIGGER_TF / FIRST_SLICE_CONTEXT_TF are re-exported from the spec.


class SetupAstCompileError(ValidationAppError):
    code = "setup_ast_rejected"


def _fail(code: str, message: str, path: str | None = None) -> CompileFailure:
    return CompileFailure(code=code, message=message, path=path)


def _node_unit(expr: AstExpr) -> AstUnit | None:
    if expr.unit is not None:
        return expr.unit
    if expr.kind is AstNodeKind.FIELD and expr.path is not None:
        spec = ALLOWED_FIELD_SPECS.get(expr.path)
        return spec[1] if spec else None
    if expr.kind is AstNodeKind.UNARY and expr.args:
        return _node_unit(expr.args[0])
    if expr.kind is AstNodeKind.WINDOW_AGGREGATE and expr.args:
        return _node_unit(expr.args[0])
    if expr.kind is AstNodeKind.ARITHMETIC and len(expr.args) >= 2:
        left = _node_unit(expr.args[0])
        if left is None or expr.op not in ALLOWED_ARITHMETIC_OPS:
            return None
        acc = left
        op = ArithmeticOp(expr.op)
        for arg in expr.args[1:]:
            right = _node_unit(arg)
            if right is None:
                return None
            derived = result_unit(acc, op, right)
            if derived is None:
                return None
            acc = derived
        return acc
    if expr.kind in {AstNodeKind.COMPARE, AstNodeKind.BOOLEAN, AstNodeKind.IS_MISSING}:
        return AstUnit.BOOLEAN
    return None


def validate_expr(expr: AstExpr, *, path: str, failures: list[CompileFailure]) -> None:
    if expr.kind is AstNodeKind.LITERAL:
        if expr.value is None or expr.value_type is None or expr.unit is None:
            failures.append(
                _fail("invalid_literal", "Literal requires value, type, and unit.", path)
            )
        return
    if expr.kind is AstNodeKind.FIELD:
        if expr.path is None or expr.path not in ALLOWED_FIELD_SPECS:
            failures.append(
                _fail("unsupported_field", f"Field {expr.path!r} is not in the V1 allowlist.", path)
            )
            return
        expected_type, expected_unit = ALLOWED_FIELD_SPECS[expr.path]
        if expr.value_type not in {None, expected_type} or expr.unit not in {None, expected_unit}:
            failures.append(
                _fail("field_type_mismatch", "Field type/unit does not match allowlist.", path)
            )
        return
    if expr.kind is AstNodeKind.UNARY:
        if expr.op not in ALLOWED_UNARY_OPS:
            failures.append(
                _fail("unsupported_operator", f"Unary operator {expr.op!r} rejected.", path)
            )
        if len(expr.args) != 1:
            failures.append(_fail("invalid_arity", "Unary requires one argument.", path))
        else:
            validate_expr(expr.args[0], path=f"{path}.args[0]", failures=failures)
        return
    if expr.kind is AstNodeKind.ARITHMETIC:
        if expr.op not in ALLOWED_ARITHMETIC_OPS:
            failures.append(
                _fail("unsupported_operator", f"Arithmetic operator {expr.op!r} rejected.", path)
            )
            return
        if len(expr.args) < 2:
            failures.append(
                _fail("invalid_arity", "Arithmetic requires at least two arguments.", path)
            )
            return
        units: list[AstUnit] = []
        for index, arg in enumerate(expr.args):
            validate_expr(arg, path=f"{path}.args[{index}]", failures=failures)
            unit = _node_unit(arg)
            if unit is None:
                failures.append(_fail("missing_unit", "Arithmetic operand missing unit.", path))
            else:
                units.append(unit)
        if len(units) >= 2:
            acc = units[0]
            op = ArithmeticOp(expr.op)
            for unit in units[1:]:
                derived = result_unit(acc, op, unit)
                if derived is None:
                    failures.append(
                        _fail("incompatible_units", "Arithmetic units are incompatible.", path)
                    )
                    break
                acc = derived
        return
    if expr.kind is AstNodeKind.COMPARE:
        if expr.op not in ALLOWED_COMPARE_OPS:
            failures.append(
                _fail("unsupported_operator", f"Compare operator {expr.op!r} rejected.", path)
            )
            return
        if len(expr.args) != 2:
            failures.append(_fail("invalid_arity", "Compare requires two arguments.", path))
            return
        validate_expr(expr.args[0], path=f"{path}.left", failures=failures)
        validate_expr(expr.args[1], path=f"{path}.right", failures=failures)
        left_unit = _node_unit(expr.args[0])
        right_unit = _node_unit(expr.args[1])
        if left_unit is not None and right_unit is not None and left_unit != right_unit:
            failures.append(
                _fail("incompatible_units", "Compare operands require compatible units.", path)
            )
        return
    if expr.kind is AstNodeKind.BOOLEAN:
        if expr.op not in ALLOWED_BOOLEAN_OPS:
            failures.append(
                _fail("unsupported_operator", f"Boolean operator {expr.op!r} rejected.", path)
            )
        if len(expr.args) < 2:
            failures.append(
                _fail("invalid_arity", "Boolean requires at least two arguments.", path)
            )
            return
        for index, arg in enumerate(expr.args):
            validate_expr(arg, path=f"{path}.args[{index}]", failures=failures)
        return
    if expr.kind is AstNodeKind.WINDOW_AGGREGATE:
        if expr.op not in ALLOWED_WINDOW_OPS:
            failures.append(
                _fail("unsupported_operator", f"Window operator {expr.op!r} rejected.", path)
            )
        if expr.window_bars is None:
            failures.append(_fail("invalid_window", "WindowAggregate requires window_bars.", path))
        if len(expr.args) != 1:
            failures.append(_fail("invalid_arity", "WindowAggregate requires one series.", path))
        else:
            validate_expr(expr.args[0], path=f"{path}.series", failures=failures)
        return
    if expr.kind is AstNodeKind.CROSSES:
        if expr.op not in ALLOWED_CROSSES_OPS:
            failures.append(
                _fail("unsupported_operator", f"Crosses operator {expr.op!r} rejected.", path)
            )
        if len(expr.args) != 2:
            failures.append(_fail("invalid_arity", "Crosses requires left and right.", path))
            return
        validate_expr(expr.args[0], path=f"{path}.left", failures=failures)
        validate_expr(expr.args[1], path=f"{path}.right", failures=failures)
        return
    if expr.kind is AstNodeKind.IS_MISSING:
        if len(expr.args) != 1:
            failures.append(_fail("invalid_arity", "IsMissing requires one argument.", path))
        else:
            validate_expr(expr.args[0], path=f"{path}.args[0]", failures=failures)
        return
    failures.append(_fail("unsupported_operator", f"AST kind {expr.kind} is not supported.", path))


def _required_feature_paths(expr: AstExpr) -> set[str]:
    found: set[str] = set()
    if expr.kind is AstNodeKind.FIELD and expr.path and expr.path.startswith("feature."):
        found.add(expr.path)
    for arg in expr.args:
        found.update(_required_feature_paths(arg))
    return found


def _walk_pattern_exprs(pattern: PatternAst) -> list[tuple[str, AstExpr]]:
    nodes: list[tuple[str, AstExpr]] = [
        ("preconditions", pattern.preconditions),
        ("trigger", pattern.trigger),
    ]
    for index, item in enumerate(pattern.invalidation):
        nodes.append((f"invalidation[{index}]", item))
    for index, step in enumerate(pattern.sequence):
        nodes.append((f"sequence[{index}].predicate", step.predicate))
        if step.reset_on is not None:
            nodes.append((f"sequence[{index}].reset_on", step.reset_on))
        if step.invalidate_on is not None:
            nodes.append((f"sequence[{index}].invalidate_on", step.invalidate_on))
    return nodes


def compile_pattern(pattern: PatternAst) -> CompileResult:
    failures: list[CompileFailure] = []
    for path, expr in _walk_pattern_exprs(pattern):
        validate_expr(expr, path=path, failures=failures)

    declared_roles = {feature.role for feature in pattern.features}
    for feature in pattern.features:
        if feature.feature_type != WILDER_ATR_FEATURE_TYPE:
            failures.append(
                _fail(
                    "missing_feature",
                    f"Feature type {feature.feature_type!r} is not supported in V1.",
                    "features",
                )
            )
        if feature.feature_version != WILDER_ATR_FEATURE_VERSION:
            failures.append(
                _fail(
                    "missing_feature",
                    f"Feature version {feature.feature_version!r} is not supported.",
                    "features",
                )
            )

    feature_paths: set[str] = set()
    for _, expr in _walk_pattern_exprs(pattern):
        feature_paths.update(_required_feature_paths(expr))
    if "feature.wilder_atr_v1.value" in feature_paths and FeatureRole.TRIGGER not in declared_roles:
        failures.append(
            _fail("missing_feature", "WilderAtrFeatureV1 trigger feature is required.", "features")
        )
    if (
        "feature.wilder_atr_v1.context.value" in feature_paths
        and FeatureRole.CONTEXT not in declared_roles
    ):
        failures.append(
            _fail("missing_feature", "WilderAtrFeatureV1 context feature is required.", "features")
        )

    if failures:
        return CompileResult(status=SetupCompileStatus.NON_EXECUTABLE.value, failures=failures)

    document = CompiledAstDocument(
        compiler_version=pattern.compiler_version,
        grammar_version=pattern.grammar_version,
        pattern=pattern,
        content_hash=canonical_sha256(pattern.model_dump(mode="python")),
    )
    return CompileResult(status=SetupCompileStatus.EXECUTABLE.value, document=document)


def _decimal_literal(value: object, unit: AstUnit) -> AstExpr:
    return lit_decimal(format(value, "f"), unit)


def _spec_completeness_failures(spec: FirstSliceAuthoredPatternSpec) -> list[CompileFailure]:
    failures: list[CompileFailure] = []
    if spec.kind != FIRST_SLICE_KIND:
        failures.append(
            _fail("unsupported_pattern_kind", "Pattern kind is not a V1 compiler target.", "kind")
        )
    if spec.name != FIRST_SLICE_NAME:
        failures.append(
            _fail(
                "first_slice_name_mismatch",
                "Authored name is not the canonical first-slice Pattern Card name.",
                "name",
            )
        )
    if spec.symbol != FIRST_SLICE_SYMBOL:
        failures.append(
            _fail(
                "wrong_symbol",
                "First slice identity requires symbol BTCUSDT.",
                "symbol",
            )
        )
    if spec.trigger_timeframe != FIRST_SLICE_TRIGGER_TF:
        failures.append(
            _fail(
                "wrong_trigger_timeframe",
                "First slice identity requires trigger timeframe 15m.",
                "trigger_timeframe",
            )
        )
    if spec.context_timeframe != FIRST_SLICE_CONTEXT_TF:
        failures.append(
            _fail(
                "wrong_context_timeframe",
                "First slice identity requires context timeframe 4h.",
                "context_timeframe",
            )
        )
    if spec.direction is not FIRST_SLICE_DIRECTION:
        failures.append(
            _fail(
                "wrong_direction",
                "First slice identity requires direction SHORT.",
                "direction",
            )
        )
    if not spec.requires_manual_4h_resistance:
        failures.append(
            _fail(
                "missing_resistance_rule",
                "First slice requires an explicit 4h resistance rule.",
                "requires_manual_4h_resistance",
            )
        )
    if not spec.requires_confirmed_swing:
        failures.append(
            _fail(
                "missing_confirmed_swing",
                "First slice requires a confirmed swing.",
                "requires_confirmed_swing",
            )
        )
    if not spec.requires_cvd_divergence:
        failures.append(
            _fail(
                "missing_cvd_requirement",
                "First slice requires explicit CVD divergence.",
                "requires_cvd_divergence",
            )
        )
    if not spec.close_below_swing or not spec.bearish_candle_close_below_open:
        failures.append(
            _fail(
                "missing_close_semantics",
                "First slice requires close-below-S and bearish candle close.",
                "close",
            )
        )
    if not spec.required_finality or not spec.required_freshness or not spec.required_no_gap:
        failures.append(
            _fail(
                "missing_finality_freshness_gap",
                "First slice requires finality, freshness, and no-gap.",
                "predicates",
            )
        )
    if spec.trigger_atr.feature_type != WILDER_ATR_FEATURE_TYPE:
        failures.append(
            _fail(
                "missing_feature",
                "Trigger ATR feature type is not Wilder ATR V1.",
                "trigger_atr",
            )
        )
    if spec.context_atr.feature_type != WILDER_ATR_FEATURE_TYPE:
        failures.append(
            _fail(
                "missing_feature",
                "Context ATR feature type is not Wilder ATR V1.",
                "context_atr",
            )
        )
    if spec.trigger_atr.role is not FeatureRole.TRIGGER:
        failures.append(
            _fail("missing_feature", "Trigger ATR role must be trigger.", "trigger_atr.role")
        )
    if spec.context_atr.role is not FeatureRole.CONTEXT:
        failures.append(
            _fail("missing_feature", "Context ATR role must be context.", "context_atr.role")
        )
    if spec.trigger_atr.feature_version != WILDER_ATR_FEATURE_VERSION:
        failures.append(
            _fail(
                "missing_feature",
                "Trigger ATR feature version is not Wilder ATR V1.",
                "trigger_atr.feature_version",
            )
        )
    if spec.context_atr.feature_version != WILDER_ATR_FEATURE_VERSION:
        failures.append(
            _fail(
                "missing_feature",
                "Context ATR feature version is not Wilder ATR V1.",
                "context_atr.feature_version",
            )
        )
    if (
        spec.trigger_atr.period != FIRST_SLICE_ATR_PERIOD
        or spec.context_atr.period != FIRST_SLICE_ATR_PERIOD
    ):
        failures.append(
            _fail(
                "wrong_atr_period",
                "First slice identity requires Wilder ATR period 14.",
                "atr.period",
            )
        )
    if spec.trigger_atr.timeframe != FIRST_SLICE_TRIGGER_TF:
        failures.append(
            _fail(
                "wrong_atr_timeframe",
                "Trigger Wilder ATR must be computed on the 15m timeframe.",
                "trigger_atr.timeframe",
            )
        )
    if spec.context_atr.timeframe != FIRST_SLICE_CONTEXT_TF:
        failures.append(
            _fail(
                "wrong_atr_timeframe",
                "Context Wilder ATR must be computed on the 4h timeframe.",
                "context_atr.timeframe",
            )
        )
    if not spec.sequence:
        failures.append(
            _fail(
                "missing_sequence_semantics",
                "Ordered sequence semantics are required.",
                "sequence",
            )
        )
    failures.extend(_sequence_identity_failures(spec))
    return failures


def _sequence_identity_failures(spec: FirstSliceAuthoredPatternSpec) -> list[CompileFailure]:
    """Fail closed unless the required first-slice step identities match exactly."""

    ids = tuple(step.step_id for step in spec.sequence)
    required = FIRST_SLICE_REQUIRED_SEQUENCE
    if ids == required:
        return []
    required_set = set(required)
    seen: set[str] = set()
    duplicate = False
    for step_id in ids:
        if step_id in required_set and step_id in seen:
            duplicate = True
        if step_id in required_set:
            seen.add(step_id)
    missing = [step_id for step_id in required if step_id not in seen]
    if duplicate:
        return [
            _fail(
                "duplicate_sequence_step",
                "Required first-slice sequence steps must not be duplicated.",
                "sequence",
            )
        ]
    if missing:
        return [
            _fail(
                "missing_required_sequence_step",
                "Required first-slice sequence steps are missing.",
                "sequence",
            )
        ]
    return [
        _fail(
            "wrong_sequence_order",
            "Required first-slice sequence steps must appear in authored order.",
            "sequence",
        )
    ]


def compile_from_spec(spec: FirstSliceAuthoredPatternSpec) -> CompileResult:
    """Compile AST solely from authored Pattern values. No canonical defaults."""

    completeness = _spec_completeness_failures(spec)
    if completeness:
        return CompileResult(status=SetupCompileStatus.NON_EXECUTABLE.value, failures=completeness)

    atr = fld("feature.wilder_atr_v1.value")
    atr_htf = fld("feature.wilder_atr_v1.context.value")
    swing = fld("swing.S")
    resistance = fld("manual_level.R")
    high = fld("ohlcv.high")
    close = fld("ohlcv.close")
    open_px = fld("ohlcv.open")
    volume = fld("ohlcv.volume")
    tick = fld("evidence.tick_size")
    cvd_t = fld("cvd.at_bar_close")
    cvd_s = fld("cvd.at_swing_close")
    signed_flow = fld("flow.signed_quote_delta_T")
    total_flow = fld("flow.total_quote_volume_T")
    trigger_high = fld("trigger.ohlcv.high")

    atr_present = boolean(
        "and",
        unary(UnaryOp.NOT, is_missing(atr)),
        unary(UnaryOp.NOT, is_missing(atr_htf)),
    )
    data_quality = boolean(
        "and",
        cmp(CompareOp.EQ, fld("predicate.finality"), lit_bool(spec.required_finality)),
        cmp(CompareOp.EQ, fld("predicate.freshness"), lit_bool(spec.required_freshness)),
        cmp(CompareOp.EQ, fld("predicate.gap"), lit_bool(not spec.required_no_gap)),
        atr_present,
    )
    near_resistance = cmp(
        CompareOp.LTE,
        abs_expr(arith(ArithmeticOp.SUBTRACT, swing, resistance)),
        arith(
            ArithmeticOp.MULTIPLY,
            _decimal_literal(spec.resistance_distance_atr_threshold, AstUnit.RATIO),
            atr_htf,
        ),
    )
    sweep = cmp(
        CompareOp.GTE,
        high,
        arith(
            ArithmeticOp.ADD,
            swing,
            arith(
                ArithmeticOp.MULTIPLY,
                _decimal_literal(spec.sweep_threshold_atr, AstUnit.RATIO),
                atr,
            ),
        ),
    )
    close_parts: list[AstExpr] = []
    if spec.close_below_swing:
        close_parts.append(cmp(CompareOp.LT, close, swing))
    if spec.bearish_candle_close_below_open:
        close_parts.append(cmp(CompareOp.LT, close, open_px))
    close_back = boolean("and", *close_parts) if len(close_parts) > 1 else close_parts[0]
    volume_spike = cmp(
        CompareOp.GTE,
        arith(
            ArithmeticOp.DIVIDE,
            volume,
            window(WindowAggOp.MEAN, volume, bars=spec.volume_lookback_bars),
        ),
        _decimal_literal(spec.volume_ratio_threshold, AstUnit.RATIO),
    )
    cvd_bearish = boolean(
        "and",
        cmp(CompareOp.GT, high, swing),
        cmp(CompareOp.LT, cvd_t, cvd_s),
    )
    sell_imbalance = cmp(
        CompareOp.LTE,
        arith(ArithmeticOp.DIVIDE, signed_flow, total_flow),
        _decimal_literal(spec.aggressive_sell_imbalance_threshold, AstUnit.RATIO),
    )
    trigger = boolean(
        "and",
        near_resistance,
        sweep,
        close_back,
        volume_spike,
        cvd_bearish,
        sell_imbalance,
    )
    invalidation_buffer = arith(
        ArithmeticOp.MAX,
        arith(
            ArithmeticOp.MULTIPLY,
            _decimal_literal(spec.invalidation.atr_multiple, AstUnit.RATIO),
            atr,
        ),
        arith(
            ArithmeticOp.MULTIPLY,
            _decimal_literal(spec.invalidation.tick_multiple, AstUnit.RATIO),
            tick,
        ),
    )
    invalidation = cmp(
        CompareOp.GT,
        high,
        arith(ArithmeticOp.ADD, trigger_high, invalidation_buffer),
    )

    predicates = {
        "htf_resistance_context": near_resistance,
        "ltf_liquidity_sweep": boolean("and", sweep, close_back),
    }
    steps: list[PatternStep] = []
    for authored in spec.sequence:
        predicate = predicates.get(authored.step_id)
        if predicate is None:
            return CompileResult(
                status=SetupCompileStatus.NON_EXECUTABLE.value,
                failures=[
                    _fail(
                        "missing_sequence_semantics",
                        f"Sequence step {authored.step_id!r} has no compiled predicate.",
                        "sequence",
                    )
                ],
            )
        reset_on = (
            lit_bool(True)
            if authored.reset_semantics is PatternResetSemantics.RETURN_TO_STEP_ZERO
            else None
        )
        invalidate_on = (
            invalidation
            if authored.invalidation_semantics is PatternInvalidationSemantics.TERMINATE_OCCURRENCE
            else None
        )
        steps.append(
            PatternStep(
                step_id=authored.step_id,
                predicate=predicate,
                min_offset=authored.min_offset,
                max_offset=authored.max_offset,
                finality_requirement=authored.finality_requirement,
                reset_on=reset_on,
                invalidate_on=invalidate_on,
                overlap_policy=authored.overlap_policy,
            )
        )

    pattern = PatternAst(
        symbols=[spec.symbol],
        trigger_timeframe=spec.trigger_timeframe,
        context_timeframe=spec.context_timeframe,
        direction=spec.direction,
        features=[
            FeatureRef(
                feature_type=spec.trigger_atr.feature_type,
                feature_version=spec.trigger_atr.feature_version,
                role=spec.trigger_atr.role,
                period=spec.trigger_atr.period,
                timeframe=spec.trigger_atr.timeframe,
            ),
            FeatureRef(
                feature_type=spec.context_atr.feature_type,
                feature_version=spec.context_atr.feature_version,
                role=spec.context_atr.role,
                period=spec.context_atr.period,
                timeframe=spec.context_atr.timeframe,
            ),
        ],
        preconditions=data_quality,
        sequence=steps,
        trigger=trigger,
        invalidation=[invalidation],
        expiration_final_bars=spec.expiry_final_bars,
    )
    return compile_pattern(pattern)


def first_slice_bearish_sweep_pattern() -> PatternAst:
    """Golden AST for the canonical first-slice authored spec."""

    result = compile_from_spec(canonical_first_slice_authored_spec())
    if result.document is None:
        raise SetupAstCompileError("Canonical first-slice spec failed to compile.")
    return result.document.pattern


def first_slice_mapping_possible(card: StrategyCard, rules: StructuredRules | None) -> bool:
    """Legacy mapping is never sufficient. Kept as an explicit always-false adapter."""

    del card, rules
    return False


def compile_from_authored(
    *,
    card: StrategyCard,
    rules: StructuredRules | None,
    pattern_spec: FirstSliceAuthoredPatternSpec | dict[str, Any] | None = None,
    strategy_version_id: Any | None = None,
    organization_id: Any | None = None,
    alias: str | None = None,
) -> CompileResult:
    del rules
    if alias is not None:
        allowed = {card.strategy_name, FIRST_SLICE_NAME}
        if alias not in allowed:
            return CompileResult(
                status=SetupCompileStatus.NON_EXECUTABLE.value,
                failures=[
                    _fail(
                        "alias_mismatch",
                        "Compatibility alias does not match the canonical strategy identity.",
                        "alias",
                    )
                ],
                strategy_version_id=strategy_version_id,
                organization_id=organization_id,
            )
    if pattern_spec is None:
        return CompileResult(
            status=SetupCompileStatus.NON_EXECUTABLE.value,
            failures=[
                _fail(
                    "missing_pattern_spec",
                    "Executable compilation requires an exact typed authored Pattern spec.",
                    "pattern_spec",
                )
            ],
            strategy_version_id=strategy_version_id,
            organization_id=organization_id,
        )
    from pydantic import ValidationError

    from app.schemas.nested_continuation import NESTED_KIND, NestedContinuationSpec

    if isinstance(pattern_spec, NestedContinuationSpec) or (
        isinstance(pattern_spec, dict) and pattern_spec.get("kind") == NESTED_KIND
    ):
        try:
            nested = NestedContinuationSpec.model_validate(pattern_spec)
        except ValidationError:
            return CompileResult(
                status=SetupCompileStatus.NON_EXECUTABLE.value,
                failures=[_fail("invalid_nested_spec", "Invalid Nested Continuation parameters.")],
            )
        pattern = PatternAst(
            nested_spec=nested,
            symbols=[nested.symbol],
            trigger_timeframe=nested.trigger_timeframe.value,
            direction=nested.direction,
            preconditions=boolean("and", fld("predicate.finality"), fld("predicate.freshness")),
            sequence=[
                PatternStep(
                    step_id="operational_nested_continuation_v1",
                    predicate=cmp(CompareOp.GT, fld("ohlcv.high"), fld("ohlcv.low")),
                )
            ],
            trigger=cmp(CompareOp.GT, fld("ohlcv.volume"), lit_decimal("0", AstUnit.VOLUME)),
            invalidation=[cmp(CompareOp.LTE, fld("ohlcv.close"), lit_decimal("0", AstUnit.PRICE))],
            expiration_final_bars=nested.parameters.confirmation_window,
        )
        return compile_pattern(pattern).model_copy(
            update={"strategy_version_id": strategy_version_id, "organization_id": organization_id}
        )
    from app.strategy_brain.sfp.contracts import SFP_KIND, SfpSpec

    if isinstance(pattern_spec, SfpSpec) or (
        isinstance(pattern_spec, dict) and pattern_spec.get("kind") == SFP_KIND
    ):
        try:
            sfp = SfpSpec.model_validate(pattern_spec)
        except ValidationError:
            return CompileResult(
                status=SetupCompileStatus.NON_EXECUTABLE.value,
                failures=[_fail("invalid_sfp_spec", "Invalid SFP parameters.")],
            )
        # Family metadata binds the entire spec into the canonical compiled hash.
        # Setup truth is evaluated only by the registered SFP adapter in evaluate_setup.
        pattern = PatternAst(
            sfp_spec=sfp,
            symbols=[sfp.symbol],
            trigger_timeframe=sfp.trigger_timeframe.value,
            direction=sfp.direction,
            preconditions=boolean("and", fld("predicate.finality"), fld("predicate.freshness")),
            sequence=[PatternStep(step_id="swing_failure_pattern_v1", predicate=lit_bool(True))],
            trigger=lit_bool(False),
            invalidation=[lit_bool(False)],
            expiration_final_bars=sfp.parameters.expiry_bars,
        )
        return compile_pattern(pattern).model_copy(
            update={"strategy_version_id": strategy_version_id, "organization_id": organization_id}
        )
    from app.strategy_brain.trendpulse_1r.contracts import TRENDPULSE_KIND, TrendPulseSpec

    if isinstance(pattern_spec, TrendPulseSpec) or (
        isinstance(pattern_spec, dict) and pattern_spec.get("kind") == TRENDPULSE_KIND
    ):
        try:
            trendpulse = TrendPulseSpec.model_validate(pattern_spec)
        except ValidationError:
            return CompileResult(
                status=SetupCompileStatus.NON_EXECUTABLE.value,
                failures=[_fail("invalid_trendpulse_spec", "Invalid fixed TrendPulse1R rules.")],
            )
        pattern = PatternAst(
            trendpulse_spec=trendpulse,
            symbols=[trendpulse.symbol],
            trigger_timeframe=trendpulse.trigger_timeframe.value,
            context_timeframe=trendpulse.trend_timeframe.value,
            direction=trendpulse.direction,
            preconditions=boolean("and", fld("predicate.finality"), fld("predicate.freshness")),
            sequence=[PatternStep(step_id="trendpulse_1r_v1", predicate=lit_bool(True))],
            trigger=lit_bool(False),
            invalidation=[lit_bool(False)],
            expiration_final_bars=1,
        )
        return compile_pattern(pattern).model_copy(
            update={"strategy_version_id": strategy_version_id, "organization_id": organization_id}
        )
    if isinstance(pattern_spec, FirstSliceAuthoredPatternSpec):
        spec = pattern_spec
    else:
        try:
            spec = FirstSliceAuthoredPatternSpec.model_validate(pattern_spec)
        except Exception as exc:
            loc = ""
            errors = getattr(exc, "errors", None)
            if callable(errors):
                first = errors()[0] if errors() else {}
                loc_parts = first.get("loc") if isinstance(first, dict) else None
                loc = ".".join(str(part) for part in loc_parts) if loc_parts else ""
            code = "invalid_pattern_spec"
            if "volume_ratio_threshold" in loc:
                code = "missing_volume_threshold"
            elif "sequence" in loc:
                code = "missing_sequence_semantics"
            elif "requires_cvd_divergence" in loc:
                code = "missing_cvd_requirement"
            elif "requires_manual_4h_resistance" in loc:
                code = "missing_resistance_rule"
            return CompileResult(
                status=SetupCompileStatus.NON_EXECUTABLE.value,
                failures=[
                    _fail(
                        code,
                        "Authored Pattern specification is incomplete.",
                        loc or "pattern_spec",
                    )
                ],
                strategy_version_id=strategy_version_id,
                organization_id=organization_id,
            )
    result = compile_from_spec(spec)
    return result.model_copy(
        update={
            "strategy_version_id": strategy_version_id,
            "organization_id": organization_id,
        }
    )


def assert_canonical_alias(
    *, strategy_version_id: Any, alias: str | None, strategy_name: str
) -> None:
    if alias is None:
        return
    allowed = {str(strategy_version_id), strategy_name, FIRST_SLICE_NAME}
    if alias not in allowed:
        raise SetupAstCompileError(
            "Strategy alias mismatch rejected.",
            details={"alias": alias, "strategy_version_id": str(strategy_version_id)},
            code="strategy_alias_mismatch",
        )
