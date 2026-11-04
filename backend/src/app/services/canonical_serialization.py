"""Versioned canonical JSON primitives for trading-semantic hashes."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel


class CanonicalSerializationError(ValueError):
    """Raised when a value has no unambiguous canonical representation."""


def canonical_json_bytes(value: BaseModel | dict[str, Any]) -> bytes:
    """Return deterministic UTF-8 JSON bytes without insignificant whitespace."""
    source = value.model_dump(mode="python") if isinstance(value, BaseModel) else value
    canonical = _canonicalize(source)
    return json.dumps(
        canonical,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: BaseModel | dict[str, Any]) -> str:
    """Hash the exact bytes returned by :func:`canonical_json_bytes`.

    The bytes are fed to the digest container by container instead of being
    built as one string: a 20k-trade batch otherwise holds its whole JSON text
    (and a bytes copy) at once, which is most of a ~115 MiB transient peak.
    """
    source = value.model_dump(mode="python") if isinstance(value, BaseModel) else value
    if not _holds_large_sequence(source):
        # Small values (one trade, one bar): one-shot encoding is faster.
        return hashlib.sha256(canonical_json_bytes(source)).hexdigest()
    digest = hashlib.sha256()
    _feed_canonical_json(source, digest.update, depth=_STREAM_DEPTH)
    return digest.hexdigest()


# Containers this deep are streamed; anything below is one json.dumps call.
_STREAM_DEPTH = 2
# A top-level sequence longer than this is hashed in pieces.
_STREAM_MIN_ITEMS = 256


def _holds_large_sequence(value: Any) -> bool:
    items = value.values() if isinstance(value, dict) else (value,)
    return any(isinstance(item, list | tuple) and len(item) > _STREAM_MIN_ITEMS for item in items)


def _dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _feed_canonical_json(value: Any, write: Callable[[bytes], object], *, depth: int) -> None:
    """Write the bytes of ``canonical_json_bytes(value)``, one subtree at a time.

    Subtrees are canonicalized as they are written, so no canonical copy of the
    whole value exists at once.
    """
    if depth <= 0 or not isinstance(value, dict | list | tuple):
        write(_dumps(_canonicalize(value)).encode("utf-8"))
        return
    if isinstance(value, list | tuple):
        write(b"[")
        for index, item in enumerate(value):
            if index:
                write(b",")
            _feed_canonical_json(item, write, depth=depth - 1)
        write(b"]")
        return
    if not all(isinstance(key, str) for key in value):
        raise CanonicalSerializationError("Canonical JSON object keys must be strings.")
    # Same key normalization (and last-wins collisions) as _canonicalize.
    normalized = {unicodedata.normalize("NFC", key): item for key, item in value.items()}
    write(b"{")
    for index, key in enumerate(sorted(normalized)):
        if index:
            write(b",")
        write(_dumps(key).encode("utf-8") + b":")
        _feed_canonical_json(normalized[key], write, depth=depth - 1)
    write(b"}")


def _canonicalize(value: Any) -> Any:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        raise CanonicalSerializationError("Binary floating-point values are not canonical.")
    if isinstance(value, Decimal):
        return _canonical_decimal(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise CanonicalSerializationError("Canonical datetimes must be timezone-aware.")
        utc_value = value.astimezone(UTC)
        return utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, Enum):
        return _canonicalize(value.value)
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list | tuple):
        return [_canonicalize(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise CanonicalSerializationError("Canonical JSON object keys must be strings.")
        return {
            unicodedata.normalize("NFC", key): _canonicalize(item) for key, item in value.items()
        }
    raise CanonicalSerializationError(
        f"Unsupported canonical value type: {type(value).__qualname__}."
    )


def _canonical_decimal(value: Decimal) -> dict[str, str | int]:
    if not value.is_finite():
        raise CanonicalSerializationError("Canonical decimals must be finite.")
    if value.is_zero():
        return {"scale": 0, "value": "0"}

    normalized = value.normalize()
    sign, digits, exponent = normalized.as_tuple()
    exponent = cast(int, exponent)
    coefficient = int("".join(str(digit) for digit in digits))
    if exponent >= 0:
        coefficient *= 10**exponent
        scale = 0
    else:
        scale = -exponent
    if sign:
        coefficient = -coefficient
    return {"scale": scale, "value": str(coefficient)}
