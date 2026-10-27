"""Reusable secret and PII redaction for logs, traces, audit metadata, and errors."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_REDACTED = "***REDACTED***"

# Every pattern below must stay linear-time: it runs synchronously on every log
# string and on tracebacks before they are capped. Key matches may only start
# where the previous character is not a key character (a lookbehind, not ``\b``,
# which also fires at ``-``), key prefixes and quoted/bracketed values are
# bounded, and no unbounded quantifier is nested inside another.
_KEY_CHARS = r"A-Za-z0-9_\-"
_KEY_START = rf"(?<![{_KEY_CHARS}])"
# Keywords that may carry a prefix (``blofin_api_secret``, ``X-API-KEY``).
_PREFIXABLE_KEYWORDS = (
    r"secret|token|signature|passphrase|password|passwd|api[_-]?key|access[_-]?key"
    r"|access[_-]?sign|private[_-]?key|credentials?|cookie|authorization"
)
# Keywords too generic for a prefix (``cache_key``, ``oauth_mode``); whole key only.
_EXACT_KEYWORDS = r"key|auth|pwd"
_KEY_BODY = (
    rf"(?:[{_KEY_CHARS}]{{0,64}}?(?:{_PREFIXABLE_KEYWORDS})|(?:{_EXACT_KEYWORDS}))"
    rf"(?![{_KEY_CHARS}])"
)
_MAX_VALUE = 2048
_AUTH_SCHEME = r"(?:Basic|Bearer|Digest|Token|Negotiate|ApiKey)"

# Credential-like key/value pairs in any rendering: ``k=v``, ``k: v``, ``k == v``,
# URL query params (``?token=v&``), headers (``ACCESS-SIGN: v``), dict reprs
# (``'secret': 'v'``), tuple reprs (``('secret', 'v')``) and JSON
# (``"passphrase": "v"``). The key and its quoting are kept; the value is not.
_CREDENTIAL_KV = re.compile(
    rf"(?P<q>[\"']?){_KEY_START}(?P<key>{_KEY_BODY})(?P=q)"
    r"(?P<sep>\s*(?:==|[:=])\s*|(?(q),\s*|(?!)))"
    r"(?P<value>"
    rf"{_AUTH_SCHEME}\s+[^\s'\",;&}}\])]+"  # scheme plus credential
    rf"|b'(?:[^'\\]|\\.){{0,{_MAX_VALUE}}}'"  # bytes reprs
    rf"|b\"(?:[^\"\\]|\\.){{0,{_MAX_VALUE}}}\""
    rf"|\"(?:[^\"\\]|\\.){{0,{_MAX_VALUE}}}\""  # quoted, escapes allowed
    rf"|'(?:[^'\\]|\\.){{0,{_MAX_VALUE}}}'"
    rf"|[{{\[][^}}\]]{{0,{_MAX_VALUE}}}[}}\]]?"  # object/array, to the first close
    r"|[^\s'\",;&}\])]+"
    r"|[\])}][^\s'\",;&]+"  # a stray closing bracket must not end the value
    r")",
    re.IGNORECASE,
)

# pydantic puts the field name on one line and ``input_value=...`` on the next.
_PYDANTIC_INPUT_VALUE = re.compile(
    rf"(?P<head>^[ \t]{{0,16}}[A-Za-z0-9_.\-]{{0,128}}?(?:{_PREFIXABLE_KEYWORDS}|{_EXACT_KEYWORDS})"
    r"[A-Za-z0-9_.\-]{0,64}[ \t]*\n[^\n]{0,512}?\binput_value=)"
    rf"(?P<value>'(?:[^'\\]|\\.){{0,{_MAX_VALUE}}}'|\"(?:[^\"\\]|\\.){{0,{_MAX_VALUE}}}\""
    r"|[^\s,\]]+)",
    re.IGNORECASE | re.MULTILINE,
)
_PEM_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----[A-Za-z0-9+/=\s]{0,16384}"
    r"(?:-----END [A-Z ]{0,40}PRIVATE KEY-----)?"
)
_AWS_ACCESS_KEY_ID = re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16,20}(?![A-Z0-9])")
# Counters such as ``fencing_token=5`` or ``has_secret=True`` are not credentials.
_BARE_SCALAR = re.compile(r"(?:\d{1,12}|true|false|none|null)", re.IGNORECASE)
_SCALAR_EXEMPT_KEY = re.compile(r".*token|has_.*", re.IGNORECASE)


def _redact_credential_kv(match: re.Match[str]) -> str:
    value = match.group("value")
    key = match.group("key")
    if _BARE_SCALAR.fullmatch(value) and _SCALAR_EXEMPT_KEY.fullmatch(key):
        return match.group(0)
    quote = value[0] if value[:1] in {'"', "'"} else ""
    if value[:2] in {"b'", 'b"'}:
        quote = value[1]
    q = match.group("q")
    return f"{q}{key}{q}{match.group('sep')}{quote}{_REDACTED}{quote}"


_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-[a-zA-Z0-9]{20,}", re.IGNORECASE), _REDACTED),
    (re.compile(r"sk_(test|live)_[a-zA-Z0-9]{16,}", re.IGNORECASE), _REDACTED),
    (re.compile(r"whsec_[a-zA-Z0-9]{16,}", re.IGNORECASE), _REDACTED),
    (re.compile(r"pk_(test|live)_[a-zA-Z0-9]{16,}", re.IGNORECASE), _REDACTED),
    (re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE), f"Bearer {_REDACTED}"),
    (
        re.compile(
            r"Authorization:\s*(?:(?:Basic|Bearer|Digest|Token|Negotiate|ApiKey)\s+)?[^\s,;]+",
            re.IGNORECASE,
        ),
        f"Authorization: {_REDACTED}",
    ),
    (
        re.compile(
            r"(api[_-]?key|access[_-]?token|refresh[_-]?token|exchange[_-]?secret"
            r"|exchange[_-]?key|client[_-]?secret|passphrase)\s*[:=]\s*['\"]?[^\s'\",;]+['\"]?",
            re.IGNORECASE,
        ),
        r"\1=" + _REDACTED,
    ),
    (
        re.compile(
            r"(mongodb|postgres|postgresql|mysql|redis)://[^\s'\"]+",
            re.IGNORECASE,
        ),
        r"\1://" + _REDACTED,
    ),
    (
        re.compile(r"password\s*[:=]\s*['\"]?[^\s'\",;]+", re.IGNORECASE),
        "password=" + _REDACTED,
    ),
    (
        re.compile(
            r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,}\b"
        ),
        _REDACTED,
    ),
    (
        re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
        _REDACTED,
    ),
    (
        re.compile(r"bot\d{6,12}:[A-Za-z0-9_-]{20,}", re.IGNORECASE),
        "bot" + _REDACTED,
    ),
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b"), _REDACTED),
]

_SENSITIVE_KEYS = frozenset(
    {
        "openai_api_key",
        "api_key",
        "authorization",
        "password",
        "token",
        "secret",
        "access_token",
        "refresh_token",
        "verification_token",
        "reset_token",
        "invite_token",
        "exchange_secret",
        "exchange_api_key",
        "exchange_api_secret",
        "exchange_passphrase",
        "passphrase",
        "api_passphrase",
        "blofin_api_key",
        "blofin_api_secret",
        "blofin_api_passphrase",
        "bearer",
        "stripe_secret_key",
        "stripe_webhook_secret",
        "stripe_signature",
        "webhook_signature",
        "telegram_bot_token",
        "bot_token",
    }
)


_SENSITIVE_KEY_NAME = re.compile(_KEY_BODY, re.IGNORECASE)


def _is_sensitive_key(key: object, raw: object) -> bool:
    """Same keyword match as message text, so ``api_secret``/``X-API-KEY`` are masked."""
    if not isinstance(key, str):
        return False
    if key.lower() in _SENSITIVE_KEYS:
        return True
    if not _SENSITIVE_KEY_NAME.fullmatch(key):
        return False
    # ``fencing_token=5`` / ``has_secret=True`` are counters and flags, not credentials.
    is_scalar = raw is None or isinstance(raw, (bool, int))
    return not (is_scalar and _SCALAR_EXEMPT_KEY.fullmatch(key))


def redact_text(value: str) -> str:
    """Redact secrets and sensitive patterns from a string."""
    if not value:
        return value
    redacted = value
    for pattern, replacement in _PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    redacted = _PEM_PRIVATE_KEY.sub(_REDACTED, redacted)
    redacted = _AWS_ACCESS_KEY_ID.sub(_REDACTED, redacted)
    redacted = _PYDANTIC_INPUT_VALUE.sub(rf"\g<head>{_REDACTED}", redacted)
    return _CREDENTIAL_KV.sub(_redact_credential_kv, redacted)


def redact_value(value: Any) -> Any:
    """Recursively redact strings inside mappings and sequences."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return redact_mapping(dict(value))
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value


def redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """Redact mapping values and mask known sensitive keys."""
    result: dict[str, Any] = {}
    for key, raw in data.items():
        if raw and _is_sensitive_key(key, raw):
            result[key] = _REDACTED
        else:
            result[key] = redact_value(raw)
    return result


def redact_for_log(event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog-compatible processor for centralized log redaction."""
    return redact_mapping(event_dict)
