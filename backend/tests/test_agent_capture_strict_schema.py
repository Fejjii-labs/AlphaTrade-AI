"""Capture planning requests a strict-compatible json_schema (review: agent_capture strict)."""

from __future__ import annotations

import contextlib
import json
import uuid
from typing import Any

from pydantic import Field

from app.agent_capture.contracts import CapturePlan
from app.agent_capture.model import CaptureModel
from app.core.config import Settings
from app.providers.llm import LLMCompletionRequest, LLMCompletionResult, MockLLMProvider
from app.schemas.strict_json_schema import strict_json_schema
from tests.test_narrative_strict_schema import _has_default, _walk_objects


def _assert_strict(schema: dict[str, Any]) -> None:
    objects = _walk_objects(schema)
    assert objects
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert sorted(obj["required"]) == sorted(obj.get("properties", {}))
    assert not _has_default(schema)
    assert set(schema["$defs"]) == {"CaptureSuggestion", "StrategyDraft"}


class _Recording(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[LLMCompletionRequest] = []

    def complete(self, request: LLMCompletionRequest) -> LLMCompletionResult:
        self.requests.append(request)
        return LLMCompletionResult(
            content='{"entries": [], "clarification": null}',
            model=request.model,
            provider="synthetic-llm",
            input_tokens=5,
            output_tokens=5,
            latency_ms=1.0,
        )


def test_capture_request_sends_strict_compatible_schema() -> None:
    llm = _Recording()
    settings = Settings(execution_mode="paper", enable_real_trading=False, log_json=False)
    with contextlib.suppress(Exception):
        CaptureModel(settings, llm_provider=llm).plan(
            organization_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            content={"message": "hi"},
        )
    sent = llm.requests[0].response_format
    assert sent is not None
    assert sent["type"] == "json_schema"
    assert sent["json_schema"]["name"] == "agent_capture"
    assert sent["json_schema"]["strict"] is True
    assert sent["json_schema"]["schema"] == strict_json_schema(CapturePlan)
    _assert_strict(sent["json_schema"]["schema"])


def test_raw_capture_schema_differs_from_strict_only_by_unsupported_keywords() -> None:
    # StrictModel forbids extras and no CapturePlan field has a default, so the raw
    # schema already had strict object shape. Griller C3: minLength/maxLength are not
    # strict-supported, so the helper drops them and changes nothing else.
    raw = CapturePlan.model_json_schema()
    _assert_strict(raw)
    assert _without(raw, {"minLength", "maxLength"}) == strict_json_schema(CapturePlan)


def _without(node: Any, keys: set[str]) -> Any:
    if isinstance(node, list):
        return [_without(item, keys) for item in node]
    if not isinstance(node, dict):
        return node
    return {
        key: (
            {name: _without(child, keys) for name, child in value.items()}
            if key in {"properties", "$defs"}
            else _without(value, keys)
        )
        for key, value in node.items()
        if key not in keys or not isinstance(value, int)
    }


def test_helper_keeps_capture_strict_if_a_defaulted_field_is_added() -> None:
    class Extended(CapturePlan):
        note: str | None = Field(default=None, max_length=10)

    raw = Extended.model_json_schema()
    assert "note" not in raw["required"]
    schema = strict_json_schema(Extended)
    assert "note" in schema["required"]
    assert not _has_default(schema)


def test_plan_accepts_explicit_nulls_that_strict_mode_returns() -> None:
    plan = CapturePlan.model_validate_json(
        json.dumps(
            {
                "entries": [
                    {
                        "category": "strategies",
                        "title": "Pullback idea",
                        "summary": "Buy pullbacks to the 20 EMA.",
                        "evidence_quotes": ["buy pullbacks to the 20 EMA"],
                        "tags": [],
                        "confidence": 0.8,
                        "target_entry_id": None,
                        "draft": {
                            "family": "pullback",
                            "market": None,
                            "direction": None,
                            "timeframe": None,
                            "entry_rules": ["20 EMA touch"],
                            "exit_rules": [],
                            "invalidation": [],
                            "missing_fields": ["stop"],
                        },
                    }
                ],
                "clarification": None,
            }
        )
    )
    assert plan.clarification is None
    entry = plan.entries[0]
    assert entry.target_entry_id is None
    assert entry.draft is not None and entry.draft.market is None
