"""Keyword-assisted draft of structured rules from plain English (Slice 36)."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from app.schemas.common import (
    EntryTriggerType,
    ExitRuleType,
    NoTradeRuleType,
    Timeframe,
    TradeDirection,
)
from app.schemas.setup_ast import (
    WILDER_ATR_FEATURE_TYPE,
    WILDER_ATR_FEATURE_VERSION,
    FeatureRole,
    FinalityRequirement,
    OverlapPolicy,
)
from app.schemas.strategy_pattern_spec import (
    FIRST_SLICE_CONTEXT_TF,
    FIRST_SLICE_HTF_STEP,
    FIRST_SLICE_KIND,
    FIRST_SLICE_LTF_STEP,
    FIRST_SLICE_TRIGGER_TF,
    PATTERN_SPEC_VERSION,
    AuthoredFeatureSpec,
    AuthoredInvalidationSpec,
    AuthoredSequenceStep,
    FirstSliceAuthoredPatternSpec,
    PatternInvalidationSemantics,
    PatternResetSemantics,
)
from app.schemas.structured_rules import (
    EntryRuleBlock,
    ExitRuleBlock,
    NoTradeRuleBlock,
    StructuredRules,
    StructuredRulesValidation,
    StructureFromTextRequest,
    StructureFromTextResponse,
)
from app.services.strategy_testability_service import validate_structured_rules

_EXPLICIT_NUMBER = re.compile(r"(-?\d+(?:\.\d+)?)")


def _extract_named_number(text: str, *labels: str) -> Decimal | None:
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*[:=]?\s*{_EXPLICIT_NUMBER.pattern}", text, re.I)
        if match:
            try:
                return Decimal(match.group(1))
            except InvalidOperation:
                continue
    return None


def _extract_named_int(text: str, *labels: str) -> int | None:
    value = _extract_named_number(text, *labels)
    if value is None:
        return None
    if value != value.to_integral_value():
        return None
    return int(value)


def _extract_named_bool(text: str, *labels: str) -> bool | None:
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*[:=]?\s*(true|false|yes|no)\b", text, re.I)
        if match:
            return match.group(1).lower() in {"true", "yes"}
    return None


def _extract_named_token(text: str, *labels: str) -> str | None:
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*[:=]\s*([a-z0-9_./-]+)", text, re.I)
        if match:
            return match.group(1)
    return None


def _extract_named_rest(text: str, *labels: str) -> str | None:
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*[:=]\s*(.+)$", text, re.I | re.M)
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return None


class StructureFromTextService:
    """Draft structured rules — deterministic validation decides testability."""

    def draft(self, request: StructureFromTextRequest) -> StructureFromTextResponse:
        return self.draft_preview(request)

    def draft_preview(self, request: StructureFromTextRequest) -> StructureFromTextResponse:
        text = request.text.lower()
        limitations = [
            "Draft rules are keyword-assisted — review and edit before backtesting.",
            "Deterministic validation decides testability, not the draft generator.",
            "This preview does not write a strategy version or compiled setup.",
        ]
        entry_type = EntryTriggerType.EMA_PULLBACK
        if "breakout" in text or "break above" in text:
            entry_type = EntryTriggerType.BREAKOUT
        elif "liquidity sweep" in text or "sweep" in text:
            entry_type = EntryTriggerType.LIQUIDITY_SWEEP
        elif "reclaim" in text:
            entry_type = EntryTriggerType.RECLAIM
        elif "rsi" in text:
            entry_type = EntryTriggerType.RSI_THRESHOLD
        elif "volume" in text:
            entry_type = EntryTriggerType.VOLUME_CONFIRMATION

        direction = TradeDirection.SHORT if "short" in text else TradeDirection.LONG
        tf = Timeframe.H4
        for candidate in Timeframe:
            if candidate.value in text:
                tf = candidate
                break

        exit_rules: list[ExitRuleBlock] = []
        if "atr" in text and "stop" in text:
            exit_rules.append(ExitRuleBlock(rule_type=ExitRuleType.ATR_STOP))
        elif "swing" in text and "stop" in text:
            exit_rules.append(ExitRuleBlock(rule_type=ExitRuleType.SWING_STOP))
        else:
            pct = Decimal("2")
            if "%" in text:
                match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
                if match:
                    pct = Decimal(match.group(1))
            exit_rules.append(ExitRuleBlock(rule_type=ExitRuleType.FIXED_STOP, value=pct))

        if "tp1" in text or "take profit" in text or "1r" in text:
            exit_rules.append(
                ExitRuleBlock(rule_type=ExitRuleType.TP_MULTIPLE, r_multiple=Decimal("1"))
            )
        if "tp2" in text or "2r" in text:
            exit_rules.append(
                ExitRuleBlock(rule_type=ExitRuleType.TP_MULTIPLE, r_multiple=Decimal("2"))
            )
        if not any(r.rule_type == ExitRuleType.TP_MULTIPLE for r in exit_rules):
            exit_rules.append(
                ExitRuleBlock(rule_type=ExitRuleType.TP_MULTIPLE, r_multiple=Decimal("1"))
            )
        if "runner" in text or "trail" in text:
            exit_rules.append(ExitRuleBlock(rule_type=ExitRuleType.RUNNER_STRUCTURE_BREAK))

        no_trade: list[NoTradeRuleBlock] = []
        if "funding" in text:
            no_trade.append(NoTradeRuleBlock(rule_type=NoTradeRuleType.HIGH_FUNDING))
        if "weekend" in text:
            no_trade.append(NoTradeRuleBlock(rule_type=NoTradeRuleType.WEEKEND_CHOP))
        if "daily loss" in text:
            no_trade.append(NoTradeRuleBlock(rule_type=NoTradeRuleType.DAILY_LOSS_LOCK))

        draft = StructuredRules(
            primary_timeframe=tf,
            entry_rules=[EntryRuleBlock(trigger_type=entry_type, direction=direction)],
            exit_rules=exit_rules,
            no_trade_rules=no_trade,
        )
        valid, errors, warnings = validate_structured_rules(draft)
        pattern_spec, pattern_errors = self._pattern_spec_preview(request.text)
        if pattern_errors:
            valid = False
            errors = [*errors, *pattern_errors]
        challenge_notes = self._challenge_notes(text, draft, pattern_errors)
        return StructureFromTextResponse(
            draft=draft,
            validation=StructuredRulesValidation(valid=valid, errors=errors, warnings=warnings),
            limitations=limitations,
            pattern_spec_draft=pattern_spec,
            pattern_spec_errors=pattern_errors,
            challenge_notes=challenge_notes,
            is_preview=True,
            persists_strategy=False,
        )

    def _pattern_spec_preview(self, original: str) -> tuple[dict[str, object] | None, list[str]]:
        """Emit FirstSliceAuthoredPatternSpec only when every threshold is explicit.

        Canonical first-slice constants are never copied in. Missing fields fail closed.
        """
        # A captured document may contain a complete authored research contract.
        # Import only a typed, explicit spec; text never grants approval/execution.
        from app.schemas.nested_continuation import NestedContinuationSpec
        from app.strategy_brain.sfp.contracts import SfpSpec
        from app.strategy_brain.trendpulse_1r.contracts import TrendPulseSpec

        models: dict[str, type[NestedContinuationSpec] | type[SfpSpec] | type[TrendPulseSpec]] = {
            "operational_nested_continuation/v1": NestedContinuationSpec,
            "swing_failure_pattern/v1": SfpSpec,
            "trendpulse_1r/v1": TrendPulseSpec,
        }
        candidates = [original.strip(), *re.findall(r"```(?:json)?\s*([\s\S]*?)```", original)]
        specs = []
        for candidate in candidates:
            try:
                value = json.loads(candidate)
            except (ValueError, TypeError):
                continue
            if not isinstance(value, dict):
                continue
            value = value.get("pattern_spec", value)
            if not isinstance(value, dict):
                continue
            kind = value.get("kind")
            if kind is not None and not isinstance(kind, str):
                return None, ["Authored research spec kind must be a string."]
            if kind not in models:
                continue
            model = models[kind]
            try:
                research_spec = model.model_validate(value)
                # Do not silently author omitted baseline parameters or bindings.
                missing_fields = set(model.model_fields) - value.keys()
                missing_parameters = (
                    set(type(research_spec.parameters).model_fields)
                    - value.get("parameters", {}).keys()
                )
                if missing_fields or missing_parameters:
                    return None, ["Authored research spec must state every field and parameter."]
                specs.append(research_spec.model_dump(mode="json"))
            except (ValidationError, ValueError, TypeError, AttributeError):
                return None, ["Authored research spec is invalid; correct it before confirmation."]
        if specs:
            if any(spec != specs[0] for spec in specs[1:]):
                return None, ["Conflicting authored research specs require clarification."]
            return specs[0], []

        text = original.lower()
        wants_first_slice = (
            FIRST_SLICE_KIND in text
            or ("liquidity sweep" in text and "cvd" in text)
            or ("bearish" in text and "sweep" in text and "imbalance" in text)
        )
        if not wants_first_slice:
            return None, []
        missing: list[str] = []
        kind = (_extract_named_token(original, "kind") or "").lower() or (
            FIRST_SLICE_KIND if FIRST_SLICE_KIND in text else None
        )
        if kind != FIRST_SLICE_KIND:
            missing.append("kind (supported first pattern must be stated)")
        name = _extract_named_rest(original, "name")
        if name is None:
            missing.append("name")
        symbol = _extract_named_token(original, "symbol")
        if symbol is None:
            missing.append("symbol")
        if FIRST_SLICE_TRIGGER_TF not in text:
            missing.append("trigger_timeframe (15m must be stated)")
        if FIRST_SLICE_CONTEXT_TF not in text:
            missing.append("context_timeframe (4h must be stated)")
        direction_token = (_extract_named_token(original, "direction") or "").lower()
        direction: TradeDirection | None = None
        if direction_token == "short":
            direction = TradeDirection.SHORT
        elif direction_token == "long":
            direction = TradeDirection.LONG
        if direction is None:
            missing.append("direction")
        resistance = _extract_named_number(
            text,
            "resistance_distance_atr_threshold",
            "resistance_distance_atr",
            "resistance atr",
            "resistance_distance",
        )
        if resistance is None:
            missing.append("resistance_distance_atr_threshold")
        sweep = _extract_named_number(text, "sweep_threshold_atr", "sweep atr", "sweep_threshold")
        if sweep is None:
            missing.append("sweep_threshold_atr")
        volume_ratio = _extract_named_number(
            text, "volume_ratio_threshold", "volume_ratio", "volume ratio"
        )
        if volume_ratio is None:
            missing.append("volume_ratio_threshold")
        imbalance = _extract_named_number(
            text,
            "aggressive_sell_imbalance_threshold",
            "aggressive_sell_imbalance",
            "sell imbalance",
            "imbalance_threshold",
        )
        if imbalance is None:
            missing.append("aggressive_sell_imbalance_threshold")
        invalidation_atr = _extract_named_number(
            text, "invalidation.atr_multiple", "invalidation atr", "atr_multiple"
        )
        if invalidation_atr is None:
            missing.append("invalidation.atr_multiple")
        tick_multiple = _extract_named_number(
            text,
            "invalidation.tick_multiple",
            "tick_multiple",
            "tick multiple",
        )
        if tick_multiple is None:
            missing.append("invalidation.tick_multiple")
        volume_lookback = _extract_named_int(text, "volume_lookback_bars", "volume_lookback")
        if volume_lookback is None:
            missing.append("volume_lookback_bars")
        expiry_bars = _extract_named_int(text, "expiry_final_bars", "expiry_bars")
        if expiry_bars is None:
            missing.append("expiry_final_bars")
        trigger_period = _extract_named_int(text, "trigger_atr.period", "trigger_atr period")
        context_period = _extract_named_int(text, "context_atr.period", "context_atr period")
        trigger_type = (
            _extract_named_token(text, "trigger_atr.feature_type", "trigger_atr type") or ""
        ).upper()
        context_type = (
            _extract_named_token(text, "context_atr.feature_type", "context_atr type") or ""
        ).upper()
        trigger_version = _extract_named_token(
            text, "trigger_atr.feature_version", "trigger_atr version"
        )
        context_version = _extract_named_token(
            text, "context_atr.feature_version", "context_atr version"
        )
        if trigger_period is None or trigger_type not in {WILDER_ATR_FEATURE_TYPE, "WILDER_ATR"}:
            missing.append("trigger_atr")
        if context_period is None or context_type not in {WILDER_ATR_FEATURE_TYPE, "WILDER_ATR"}:
            missing.append("context_atr")
        if trigger_version != WILDER_ATR_FEATURE_VERSION and "trigger_atr" not in missing:
            missing.append("trigger_atr.feature_version")
        if context_version != WILDER_ATR_FEATURE_VERSION and "context_atr" not in missing:
            missing.append("context_atr.feature_version")
        requires_manual = _extract_named_bool(
            text, "requires_manual_4h_resistance", "requires_manual_resistance"
        )
        if requires_manual is None:
            missing.append("requires_manual_4h_resistance")
        requires_swing = _extract_named_bool(text, "requires_confirmed_swing")
        if requires_swing is None:
            missing.append("requires_confirmed_swing")
        close_below_swing = _extract_named_bool(text, "close_below_swing")
        if close_below_swing is None:
            missing.append("close_below_swing")
        bearish_close = _extract_named_bool(
            text, "bearish_candle_close_below_open", "bearish_close_below_open"
        )
        if bearish_close is None:
            missing.append("bearish_candle_close_below_open")
        requires_cvd = _extract_named_bool(text, "requires_cvd_divergence")
        if requires_cvd is None:
            missing.append("requires_cvd_divergence")
        required_finality = _extract_named_bool(text, "required_finality")
        if required_finality is None:
            missing.append("required_finality")
        required_freshness = _extract_named_bool(text, "required_freshness")
        if required_freshness is None:
            missing.append("required_freshness")
        required_no_gap = _extract_named_bool(text, "required_no_gap")
        if required_no_gap is None:
            missing.append("required_no_gap")
        overlap_token = _extract_named_token(text, "overlap_policy")
        if overlap_token != OverlapPolicy.DISALLOW.value:
            missing.append("overlap_policy")
        reset_token = _extract_named_token(text, "reset_semantics")
        if reset_token != PatternResetSemantics.NONE.value:
            missing.append("sequence.reset_semantics")
        invalidation_token = _extract_named_token(text, "invalidation_semantics")
        if invalidation_token != PatternInvalidationSemantics.NONE.value:
            missing.append("sequence.invalidation_semantics")
        finality_token = _extract_named_token(text, "finality_requirement")
        if finality_token != FinalityRequirement.FINAL_ONLY.value:
            missing.append("sequence.finality_requirement")
        htf_min = _extract_named_int(text, "htf_min_offset")
        htf_max = _extract_named_int(text, "htf_max_offset")
        ltf_min = _extract_named_int(text, "ltf_min_offset")
        ltf_max = _extract_named_int(text, "ltf_max_offset")
        if FIRST_SLICE_HTF_STEP not in text or FIRST_SLICE_LTF_STEP not in text:
            missing.append("sequence")
        if (
            htf_min is None or htf_max is None or ltf_min is None or ltf_max is None
        ) and "sequence" not in missing:
            missing.append("sequence offsets")
        if missing:
            return None, missing
        assert name is not None
        assert symbol is not None
        assert direction is not None
        assert resistance is not None
        assert sweep is not None
        assert volume_ratio is not None
        assert imbalance is not None
        assert invalidation_atr is not None
        assert tick_multiple is not None
        assert volume_lookback is not None
        assert expiry_bars is not None
        assert trigger_period is not None
        assert context_period is not None
        assert requires_manual is not None
        assert requires_swing is not None
        assert close_below_swing is not None
        assert bearish_close is not None
        assert requires_cvd is not None
        assert required_finality is not None
        assert required_freshness is not None
        assert required_no_gap is not None
        assert htf_min is not None
        assert htf_max is not None
        assert ltf_min is not None
        assert ltf_max is not None
        try:
            spec = FirstSliceAuthoredPatternSpec(
                spec_version=PATTERN_SPEC_VERSION,
                kind=FIRST_SLICE_KIND,
                name=name,
                symbol=symbol.upper(),
                trigger_timeframe=FIRST_SLICE_TRIGGER_TF,
                context_timeframe=FIRST_SLICE_CONTEXT_TF,
                direction=direction,
                requires_manual_4h_resistance=requires_manual,
                requires_confirmed_swing=requires_swing,
                trigger_atr=AuthoredFeatureSpec(
                    feature_type=WILDER_ATR_FEATURE_TYPE,
                    feature_version=WILDER_ATR_FEATURE_VERSION,
                    role=FeatureRole.TRIGGER,
                    period=trigger_period,
                    timeframe=FIRST_SLICE_TRIGGER_TF,
                ),
                context_atr=AuthoredFeatureSpec(
                    feature_type=WILDER_ATR_FEATURE_TYPE,
                    feature_version=WILDER_ATR_FEATURE_VERSION,
                    role=FeatureRole.CONTEXT,
                    period=context_period,
                    timeframe=FIRST_SLICE_CONTEXT_TF,
                ),
                resistance_distance_atr_threshold=resistance,
                sweep_threshold_atr=sweep,
                close_below_swing=close_below_swing,
                bearish_candle_close_below_open=bearish_close,
                volume_lookback_bars=volume_lookback,
                volume_ratio_threshold=volume_ratio,
                requires_cvd_divergence=requires_cvd,
                aggressive_sell_imbalance_threshold=imbalance,
                required_finality=required_finality,
                required_freshness=required_freshness,
                required_no_gap=required_no_gap,
                invalidation=AuthoredInvalidationSpec(
                    atr_multiple=invalidation_atr,
                    tick_multiple=tick_multiple,
                ),
                expiry_final_bars=expiry_bars,
                sequence=[
                    AuthoredSequenceStep(
                        step_id=FIRST_SLICE_HTF_STEP,
                        min_offset=htf_min,
                        max_offset=htf_max,
                        finality_requirement=FinalityRequirement.FINAL_ONLY,
                        overlap_policy=OverlapPolicy.DISALLOW,
                        reset_semantics=PatternResetSemantics.NONE,
                        invalidation_semantics=PatternInvalidationSemantics.NONE,
                    ),
                    AuthoredSequenceStep(
                        step_id=FIRST_SLICE_LTF_STEP,
                        min_offset=ltf_min,
                        max_offset=ltf_max,
                        finality_requirement=FinalityRequirement.FINAL_ONLY,
                        overlap_policy=OverlapPolicy.DISALLOW,
                        reset_semantics=PatternResetSemantics.NONE,
                        invalidation_semantics=PatternInvalidationSemantics.NONE,
                    ),
                ],
                overlap_policy=OverlapPolicy.DISALLOW,
            )
        except (ValidationError, ValueError) as exc:
            return None, [str(exc)]
        if spec.direction is not TradeDirection.SHORT:
            return None, ["direction must be short for the supported first pattern"]
        return spec.model_dump(mode="json"), []

    def _challenge_notes(
        self,
        text: str,
        draft: StructuredRules,
        pattern_errors: list[str],
    ) -> list[str]:
        notes: list[str] = []
        if "always" in text or "guaranteed" in text:
            notes.append("Avoid absolute language. Rules must stay testable and conservative.")
        if not draft.no_trade_rules:
            notes.append("Consider an explicit no-trade filter (session, funding, or daily loss).")
        if pattern_errors:
            notes.append(
                "First-slice pattern_spec stays omitted until every threshold is stated. "
                "The compiler will not invent values."
            )
        if "activate" in text or "go live" in text or "enable watcher" in text:
            notes.append(
                "Conversational drafts cannot activate Watcher, Telegram, or live trading."
            )
        return notes
