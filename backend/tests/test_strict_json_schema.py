"""strict_json_schema emits only keywords OpenAI strict mode supports (Griller C3)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel, Field, ValidationError

from app.agent_capture.contracts import CapturePlan
from app.schemas.narrative import TradingNarrativeDetail
from app.schemas.strict_json_schema import (
    REJECTED_COMPOSITION,
    SUPPORTED_FORMATS,
    UNSUPPORTED_KEYWORDS,
    UnsupportedStrictSchemaError,
    _strict_node,
    strict_json_schema,
)
from app.services.narrative_service import narrative_response_format
from tests.test_narrative_strict_schema import _has_default, _walk_objects


def _keywords(node: Any) -> list[tuple[str, Any]]:
    """Every (keyword, value) pair at any depth; property/definition names are skipped."""
    if isinstance(node, list):
        return [pair for item in node for pair in _keywords(item)]
    if not isinstance(node, dict):
        return []
    found: list[tuple[str, Any]] = []
    for key, value in node.items():
        if key in {"properties", "$defs"} and isinstance(value, dict):
            found.extend(pair for child in value.values() for pair in _keywords(child))
        elif key in {"enum", "const"}:
            found.append((key, value))
        else:
            found.append((key, value))
            found.extend(_keywords(value))
    return found


def _assert_only_supported(schema: dict[str, Any]) -> None:
    pairs = _keywords(schema)
    names = {key for key, _ in pairs}
    assert not names & UNSUPPORTED_KEYWORDS
    assert not names & REJECTED_COMPOSITION
    assert "default" not in names
    assert all(value in SUPPORTED_FORMATS for key, value in pairs if key == "format")
    for obj in _walk_objects(schema):
        assert obj["additionalProperties"] is False
        assert sorted(obj["required"]) == sorted(obj.get("properties", {}))


def test_helper_strips_unsupported_and_keeps_supported_keywords() -> None:
    node = {
        "type": "object",
        "title": "Demo",
        "description": "kept",
        "properties": {
            # Property names that look like keywords are not keywords.
            "minLength": {"type": "string", "minLength": 1, "maxLength": 9, "pattern": "^a"},
            "when": {"type": "string", "format": "date-time"},
            "uri": {"type": "string", "format": "uri"},
            "id": {"type": "string", "format": "uuid"},
            "n": {"type": "number", "minimum": 0, "maximum": 1, "multipleOf": 0.5},
            "m": {"type": "integer", "exclusiveMinimum": 0, "exclusiveMaximum": 9},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 3,
                "uniqueItems": True,
                "contains": {"const": "x"},
            },
            "kind": {"enum": ["a", {"minLength": 1}]},
            "fixed": {"const": {"maxLength": 2}},
            "opt": {"anyOf": [{"$ref": "#/$defs/Sub"}, {"type": "null"}], "default": None},
        },
        "$defs": {
            "Sub": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "minProperties": 1,
                "propertyNames": {"pattern": "^x"},
            }
        },
    }
    out = _strict_node(node)
    props = out["properties"]
    assert props["minLength"] == {"type": "string", "pattern": "^a"}
    assert props["when"]["format"] == "date-time" and props["id"]["format"] == "uuid"
    assert props["uri"] == {"type": "string"}
    assert props["n"] == {"type": "number", "minimum": 0, "maximum": 1, "multipleOf": 0.5}
    assert props["m"] == {"type": "integer", "exclusiveMinimum": 0, "exclusiveMaximum": 9}
    assert props["tags"] == {
        "type": "array",
        "items": {"type": "string"},
        "minItems": 1,
        "maxItems": 3,
    }
    # enum/const values are data and are copied verbatim.
    assert props["kind"] == {"enum": ["a", {"minLength": 1}]}
    assert props["fixed"] == {"const": {"maxLength": 2}}
    assert props["opt"] == {"anyOf": [{"$ref": "#/$defs/Sub"}, {"type": "null"}]}
    assert out["$defs"]["Sub"] == {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "additionalProperties": False,
        "required": ["x"],
    }
    assert (out["title"], out["description"]) == ("Demo", "kept")
    assert out["required"] == list(node["properties"])


@pytest.mark.parametrize("keyword", sorted(REJECTED_COMPOSITION))
def test_rejected_composition_fails_at_build_time(keyword: str) -> None:
    value: Any = [{"type": "string"}, {"maxLength": 3}] if keyword == "allOf" else {"type": "null"}
    with pytest.raises(UnsupportedStrictSchemaError, match=keyword):
        _strict_node({"type": "object", "properties": {"a": {keyword: value}}})


def test_single_element_all_of_collapses_into_its_ref() -> None:
    node = {"allOf": [{"$ref": "#/$defs/Sub"}], "description": "the sub", "default": None}
    assert _strict_node(node) == {"$ref": "#/$defs/Sub", "description": "the sub"}
    with pytest.raises(UnsupportedStrictSchemaError):
        _strict_node({"allOf": [{"type": "string"}], "type": "integer"})


@pytest.mark.parametrize("model", [CapturePlan, TradingNarrativeDetail])
def test_emitted_capture_and_narrative_schemas_use_only_supported_keywords(
    model: type[BaseModel],
) -> None:
    schema = strict_json_schema(model)
    _assert_only_supported(schema)
    assert not _has_default(schema)


def test_narrative_response_format_is_strict_and_clean() -> None:
    sent = narrative_response_format()["json_schema"]
    assert sent["strict"] is True
    _assert_only_supported(sent["schema"])


def test_capture_keeps_supported_constraints_in_the_emitted_schema() -> None:
    schema = strict_json_schema(CapturePlan)
    names = {key for key, _ in _keywords(schema)}
    assert {"minimum", "maximum", "minItems", "maxItems", "format", "$ref", "anyOf"} <= names
    raw = CapturePlan.model_json_schema()
    assert {"minLength", "maxLength"} <= {key for key, _ in _keywords(raw)}


def _plan(title: str) -> str:
    return json.dumps(
        {
            "entries": [
                {
                    "category": "lessons",
                    "title": title,
                    "summary": "Wait for the retest.",
                    "evidence_quotes": ["wait for the retest"],
                    "tags": [],
                    "confidence": 0.5,
                    "target_entry_id": None,
                    "draft": None,
                }
            ],
            "clarification": None,
        }
    )


def test_pydantic_still_enforces_dropped_max_length_at_parse_time() -> None:
    # The strict schema no longer tells the model maxLength=160, but CaptureModel.plan
    # parses with CapturePlan.model_validate_json, which still rejects the reply.
    assert "maxLength" not in json.dumps(strict_json_schema(CapturePlan))
    assert CapturePlan.model_validate_json(_plan("x" * 160)).entries[0].title == "x" * 160
    with pytest.raises(ValidationError, match="160"):
        CapturePlan.model_validate_json(_plan("x" * 161))
    with pytest.raises(ValidationError):
        CapturePlan.model_validate_json(_plan(""))


def test_unsupported_format_from_a_pydantic_model_is_dropped() -> None:
    class Link(BaseModel):
        url: str = Field(json_schema_extra={"format": "uri"})
        at: str = Field(json_schema_extra={"format": "date"})

    props = strict_json_schema(Link)["properties"]
    assert "format" not in props["url"] and props["at"]["format"] == "date"
