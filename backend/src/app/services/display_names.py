"""Trader-facing display names: "Nested · BTCUSDT · Long · 15m".

Pure formatting over fields a record already carries. Never includes ids or hashes;
missing parts are left out rather than replaced with placeholders.
"""

from __future__ import annotations

SEPARATOR = " · "
_FAMILY_LABELS = {"nested": "Nested", "nested_continuation": "Nested", "sfp": "SFP"}


def _text(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw).strip() if raw is not None else ""


def family_label(family: object) -> str:
    text = _text(family)
    return _FAMILY_LABELS.get(text.lower(), text.replace("_", " ").capitalize())


def display_name(
    label: object = None,
    symbol: object = None,
    direction: object = None,
    timeframe: object = None,
) -> str:
    """Join label, symbol, direction and timeframe; empty parts are skipped."""
    parts = [
        _text(label),
        _text(symbol).upper(),
        _text(direction).capitalize(),
        _text(timeframe),
    ]
    return SEPARATOR.join(part for part in parts if part)
