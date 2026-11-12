"""Narrative LLM calls request a strict, OpenAI-compatible json_schema (review item 7)."""

from __future__ import annotations

from typing import Any

from app.providers.llm import (
    LLMCompletionRequest,
    LLMCompletionResult,
    MockLLMProvider,
    _map_response_format,
)
from app.schemas.agent import AgentState
from app.schemas.analysis import TradingAnalysisDetail
from app.schemas.common import RiskSeverity
from app.schemas.narrative import TradingNarrativeDetail
from app.schemas.strict_json_schema import strict_json_schema
from app.services.narrative_service import NarrativeService, narrative_response_format


def _walk_objects(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, list):
        return [obj for item in node for obj in _walk_objects(item)]
    if not isinstance(node, dict):
        return []
    found = [node] if node.get("type") == "object" or "properties" in node else []
    for key, value in node.items():
        if key in {"properties", "$defs"} and isinstance(value, dict):
            found.extend(obj for child in value.values() for obj in _walk_objects(child))
        else:
            found.extend(_walk_objects(value))
    return found


def _has_default(node: Any) -> bool:
    if isinstance(node, list):
        return any(_has_default(item) for item in node)
    if not isinstance(node, dict):
        return False
    return (
        "default" in node
        or any(_has_default(v) for k, v in node.items() if k not in {"properties", "$defs"})
        or any(
            _has_default(child)
            for k in ("properties", "$defs")
            for child in (node.get(k) or {}).values()
        )
    )


def test_narrative_schema_is_strict_compatible() -> None:
    schema = strict_json_schema(TradingNarrativeDetail)
    objects = _walk_objects(schema)
    assert objects, "schema has no object nodes"
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert sorted(obj["required"]) == sorted(obj.get("properties", {}))
    assert not _has_default(schema)
    # Every model field is present and required at the top level.
    assert sorted(schema["required"]) == sorted(TradingNarrativeDetail.model_fields)


def test_raw_pydantic_schema_was_not_strict_compatible() -> None:
    raw = TradingNarrativeDetail.model_json_schema()
    assert sorted(raw.get("required", [])) != sorted(raw["properties"])


def test_strict_schema_keeps_a_field_literally_named_default() -> None:
    from pydantic import BaseModel

    class Odd(BaseModel):
        default: str = "x"

    schema = strict_json_schema(Odd)
    assert "default" in schema["properties"]
    assert schema["required"] == ["default"]


class _RecordingLLM(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[LLMCompletionRequest] = []

    def complete(self, request: LLMCompletionRequest) -> LLMCompletionResult:
        self.requests.append(request)
        return super().complete(request)


def test_narrative_request_carries_strict_json_schema() -> None:
    llm = _RecordingLLM()
    service = NarrativeService(llm_provider=llm, llm_model="gpt-6-luna")
    analysis = TradingAnalysisDetail.model_validate(
        {
            "summary": "No trade.",
            "risk_level": RiskSeverity.MEDIUM,
            "stop_loss_or_no_trade_reason": "No trade.",
            "approval_status": "not_required",
            "market_data_quality": "mock",
        }
    )
    result = service.enhance(AgentState(request_id="r", message="analyze btc"), analysis)
    assert result.metadata.source == "llm"
    sent = llm.requests[0].response_format
    assert sent == narrative_response_format()
    assert sent["type"] == "json_schema"
    assert sent["json_schema"]["strict"] is True
    assert sent["json_schema"]["name"] == "narrative"
    mapped = _map_response_format(sent)
    assert mapped["type"] == "json_schema" and mapped["strict"] is True
    assert mapped["schema"] == strict_json_schema(TradingNarrativeDetail)
