"""OpenAI strict structured-output schema derived from a Pydantic model.

Strict mode rejects schemas unless every object sets ``additionalProperties: false``
and lists every property in ``required``; it also rejects ``default``. Pydantic emits
defaults and omits defaulted fields from ``required``, so the raw
``model_json_schema()`` is not strict-compatible for models with defaulted fields.
The parsed reply is still validated by the Pydantic model itself.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return ``model``'s JSON schema rewritten for OpenAI ``strict: true``."""
    return _strict_node(model.model_json_schema())


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
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "default":
            continue
        if key in {"properties", "$defs"} and isinstance(value, dict):
            # Keys of these mappings are field / definition names, not keywords.
            out[key] = {name: _strict_node(child) for name, child in value.items()}
        else:
            out[key] = _strict_node(value)
    if out.get("type") == "object" or "properties" in out:
        out["additionalProperties"] = False
        out["required"] = list(out.get("properties", {}))
    return out
