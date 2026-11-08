"""L14: uvicorn access/error logs bypass the root redaction handlers.

uvicorn installs its own non-propagating handler on ``uvicorn.access``; the
filters must sit on the logger itself so every handler sees redacted records.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator

import pytest
from uvicorn.logging import AccessFormatter

from app.core.logging import (
    UvicornAccessRedactionFilter,
    _redact_query_values,
    configure_logging,
)

_SECRET = "s3cr3tvalue123"


@pytest.fixture
def access_stream() -> Iterator[io.StringIO]:
    """Mimic uvicorn's LOGGING_CONFIG: own handler, AccessFormatter, propagate=False."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False)
    )
    access = logging.getLogger("uvicorn.access")
    saved = (access.handlers[:], access.filters[:], access.propagate, access.level)
    access.handlers = [handler]
    access.filters = []
    access.propagate = False
    access.setLevel(logging.INFO)
    try:
        yield stream
    finally:
        access.handlers, access.filters, access.propagate, _level = saved
        access.setLevel(saved[3])


def test_access_log_query_values_are_redacted(access_stream: io.StringIO) -> None:
    configure_logging(log_level="INFO", json_logs=True)
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d',
        "203.0.113.5:4100",
        "GET",
        f"/api/v1/auth/verify-email?token={_SECRET}&next=%2Fdash",
        "1.1",
        200,
    )
    line = access_stream.getvalue()
    assert _SECRET not in line
    assert "/api/v1/auth/verify-email?token=***REDACTED***&next=***REDACTED***" in line
    assert "203.0.113.5:4100" in line
    assert " 200" in line


def test_access_log_path_without_query_is_unchanged(access_stream: io.StringIO) -> None:
    configure_logging(log_level="INFO", json_logs=True)
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "203.0.113.5:4100", "GET", "/health", "1.1", 200
    )
    assert '"GET /health HTTP/1.1" 200' in access_stream.getvalue()


def test_filters_installed_once_on_uvicorn_loggers() -> None:
    configure_logging(log_level="INFO", json_logs=True)
    configure_logging(log_level="INFO", json_logs=True)
    for name in ("uvicorn.access", "uvicorn.error"):
        filters = logging.getLogger(name).filters
        assert sum(isinstance(item, UvicornAccessRedactionFilter) for item in filters) == 1


def test_uvicorn_error_record_secrets_are_redacted() -> None:
    record = logging.LogRecord(
        "uvicorn.error", logging.ERROR, __file__, 1, "bad header %s", (f"Bearer {_SECRET}",), None
    )
    assert UvicornAccessRedactionFilter().filter(record) is True
    assert _SECRET not in record.getMessage()


def test_query_redaction_keeps_keys_only() -> None:
    assert _redact_query_values("/x?a=1&b=") == "/x?a=***REDACTED***&b=***REDACTED***"
    assert _redact_query_values("/x?opaque") == "/x?opaque=***REDACTED***"
    assert _redact_query_values("/x?") == "/x?***REDACTED***"
    assert _redact_query_values("/x") == "/x"
