"""Structured logging setup using structlog.

Logs are emitted as JSON in non-local environments for ingestion by log
pipelines, and as a human-friendly console format locally. A ``request_id`` and
other contextual fields are bound per-request via :mod:`app.core.middleware`.
"""

from __future__ import annotations

import logging
import sys
import traceback
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode

import structlog
from structlog.typing import EventDict, Processor

from app.guardrails.redaction import _REDACTED, redact_for_log, redact_text


class SecretRedactionFilter(logging.Filter):
    """Redact secrets in stdlib log records, including httpx URL lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(
                redact_text(item) if isinstance(item, str) else item for item in args
            )
        elif isinstance(args, dict):
            record.args = {
                key: redact_text(value) if isinstance(value, str) else value
                for key, value in args.items()
            }
        return True


def _redact_query_values(path: str) -> str:
    """Keep the path and query keys; replace every query value (L14)."""
    base, sep, query = path.partition("?")
    if not sep:
        return path
    pairs = parse_qsl(query, keep_blank_values=True)
    if not pairs:
        return f"{base}?{_REDACTED}"
    return f"{base}?" + urlencode([(key, _REDACTED) for key, _value in pairs], safe="*")


class UvicornAccessRedactionFilter(SecretRedactionFilter):
    """Redact uvicorn access/error records, which bypass the root handlers (L14).

    uvicorn's ``uvicorn.access`` logger has its own handler and ``propagate=False``.
    Access records carry ``(client_addr, method, path_with_query, http_version, status)``;
    the tuple shape is kept so uvicorn's ``AccessFormatter`` still renders it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if (
            record.name == "uvicorn.access"
            and isinstance(args, tuple)
            and len(args) == 5
            and isinstance(args[2], str)
        ):
            record.args = (*args[:2], _redact_query_values(args[2]), *args[3:])
        return super().filter(record)


_UVICORN_LOGGERS = ("uvicorn.access", "uvicorn.error")


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts the rendered line and any traceback."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))

    def formatException(self, ei: logging._SysExcInfoType) -> str:  # noqa: N802
        return redact_text(super().formatException(ei))


_EXCEPTION_MESSAGE_CAP = 500
_TRACEBACK_CAP = 32 * 1024
_TRACEBACK_TRUNCATED = "...[traceback truncated]...\n"


def _cap_traceback(text: str) -> str:
    """Cap an already-redacted traceback, keeping its tail (the raised exception)."""
    if len(text) <= _TRACEBACK_CAP:
        return text
    return _TRACEBACK_TRUNCATED + text[-(_TRACEBACK_CAP - len(_TRACEBACK_TRUNCATED)) :]


def _redact_sensitive(_logger: object, _name: str, event_dict: EventDict) -> EventDict:
    """structlog processor. Exception text is redacted before it is rendered.

    ``exc_info=True`` (and ``logger.exception``) means "the exception being
    handled now". It is resolved with :func:`sys.exc_info` here; rendering the
    bare flag produced ``"exception": "True"`` with no type or traceback.
    """
    exc_info = event_dict.get("exc_info")
    event: dict[str, Any] = redact_for_log(dict(event_dict))
    if not exc_info:
        return event
    error: BaseException | None = None
    if exc_info is True:
        error = sys.exc_info()[1]
    elif isinstance(exc_info, BaseException):
        error = exc_info
    elif isinstance(exc_info, tuple) and len(exc_info) == 3:
        error = exc_info[1] if isinstance(exc_info[1], BaseException) else None
    if error is not None:
        text = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        event["exception_type"] = type(error).__qualname__
        event["exception_message"] = redact_text(str(error))[:_EXCEPTION_MESSAGE_CAP]
    elif exc_info is True:
        text = "exc_info=True logged outside an active exception"
    else:
        text = str(exc_info)
    event["exception"] = _cap_traceback(redact_text(text))
    event.pop("exc_info", None)
    return event


def _install_stdlib_redaction(level: int) -> None:
    """Keep application logs at ``level``. Quiet httpx/httpcore URL logs."""

    redaction_filter = SecretRedactionFilter()
    formatter = RedactingFormatter("%(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        handler.addFilter(redaction_filter)
        root.addHandler(handler)
    else:
        for existing in root.handlers:
            if not any(isinstance(item, SecretRedactionFilter) for item in existing.filters):
                existing.addFilter(redaction_filter)
            if not isinstance(existing.formatter, RedactingFormatter):
                existing.setFormatter(formatter)
    for name in ("httpx", "httpcore"):
        library = logging.getLogger(name)
        library.setLevel(logging.WARNING)
        if not any(isinstance(item, SecretRedactionFilter) for item in library.filters):
            library.addFilter(redaction_filter)
    for name in _UVICORN_LOGGERS:
        server = logging.getLogger(name)
        if not any(isinstance(item, UvicornAccessRedactionFilter) for item in server.filters):
            server.addFilter(UvicornAccessRedactionFilter())


def configure_logging(*, log_level: str = "INFO", json_logs: bool = True) -> None:
    """Configure stdlib logging and structlog.

    Args:
        log_level: Minimum level name (e.g. ``"INFO"``).
        json_logs: Emit JSON when True, console-formatted output otherwise.
    """
    level = logging.getLevelNamesMapping().get(log_level.upper(), logging.INFO)
    _install_stdlib_redaction(level)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        # Render stack_info first so its text goes through redaction too.
        structlog.processors.StackInfoRenderer(),
        _redact_sensitive,
    ]

    renderer = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger, optionally namespaced."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
