"""OpenAI strict structured-output schema derived from a Pydantic model.

Strict mode rejects schemas unless every object sets ``additionalProperties: false``
and lists every property in ``required``; it also rejects ``default``. Pydantic emits
defaults and omits defaulted fields from ``required``, so the raw
``model_json_schema()`` is not strict-compatible for models with defaulted fields.

Strict mode also supports only some constraint keywords (OpenAI Structured Outputs,
"Supported schemas"): strings ``pattern`` and ``format`` with a fixed format list;
numbers ``multipleOf``/``minimum``/``maximum``/``exclusiveMinimum``/``exclusiveMaximum``;
arrays ``minItems``/``maxItems``. Other constraints (``minLength``, ``maxLength``,
``uniqueItems``, ...) and unsupported formats are dropped from the emitted schema.
``allOf``/``not``/``if``/``then``/``else`` raise at build time instead of being sent,
except that a single-element ``allOf`` is collapsed into its node.

Dropping a keyword only loosens what the model is told. Callers validate the parsed
reply with the Pydantic model itself (``CapturePlan.model_validate_json`` in
agent_capture, ``TradingNarrativeDetail.model_validate`` in narrative_service), so
every original constraint is still enforced server-side.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

#: Constraint keywords strict mode does not accept; removed from the emitted schema.
UNSUPPORTED_KEYWORDS = frozenset(
    {
        "minLength",
        "maxLength",
        "uniqueItems",
        "contains",
        "minContains",
        "maxContains",
        "minProperties",
        "maxProperties",
        "patternProperties",
        "propertyNames",
        "unevaluatedProperties",
        "unevaluatedItems",
        "dependentRequired",
        "dependentSchemas",
    }
)
#: String formats strict mode accepts; any other ``format`` value is removed.
SUPPORTED_FORMATS = frozenset(
    {"date-time", "time", "date", "duration", "email", "hostname", "ipv4", "ipv6", "uuid"}
)
#: Composition strict mode rejects. Building such a schema fails loudly.
REJECTED_COMPOSITION = frozenset({"allOf", "not", "if", "then", "else"})
# Values that are data, not subschemas: copied verbatim.
_LITERAL_KEYWORDS = frozenset({"enum", "const", "examples"})


class UnsupportedStrictSchemaError(ValueError):
    """The schema uses composition that OpenAI strict mode cannot accept."""


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return ``model``'s JSON schema rewritten for OpenAI ``strict: true``."""
    schema: dict[str, Any] = _strict_node(model.model_json_schema())
    return schema


def strict_response_format(name: str, model: type[BaseModel]) -> dict[str, Any]:
    """Chat Completions shaped ``response_format``; llm.py maps it for the Responses API."""
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": strict_json_schema(model)},
    }


def _strict_node(node: Any) -> Any:
    if isinstance(node, list):
        return [_strict_node(item) for item in node]
    if not isinstance(node, dict):
        return node
    node = _collapse_single_all_of(node)
    rejected = REJECTED_COMPOSITION.intersection(node)
    if rejected:
        raise UnsupportedStrictSchemaError(
            f"strict json_schema cannot contain {sorted(rejected)}; reshape the model"
        )
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "default" or key in UNSUPPORTED_KEYWORDS:
            continue
        if key == "format" and not (isinstance(value, str) and value in SUPPORTED_FORMATS):
            continue
        if key in _LITERAL_KEYWORDS:
            out[key] = value
        elif key in {"properties", "$defs"} and isinstance(value, dict):
            # Keys of these mappings are field / definition names, not keywords.
            out[key] = {name: _strict_node(child) for name, child in value.items()}
        else:
            out[key] = _strict_node(value)
    if out.get("type") == "object" or "properties" in out:
        out["additionalProperties"] = False
        out["required"] = list(out.get("properties", {}))
    return out


def _collapse_single_all_of(node: dict[str, Any]) -> dict[str, Any]:
    """``{"allOf": [X], ...siblings}`` -> X merged with the siblings (siblings win)."""
    all_of = node.get("allOf")
    if not (isinstance(all_of, list) and len(all_of) == 1 and isinstance(all_of[0], dict)):
        return node
    siblings = {key: value for key, value in node.items() if key != "allOf"}
    if set(all_of[0]) & (set(siblings) - {"title", "description"}):
        raise UnsupportedStrictSchemaError("allOf member conflicts with sibling keywords")
    return {**all_of[0], **siblings}
