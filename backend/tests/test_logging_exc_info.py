"""``exc_info=True`` must render the active exception, not the literal flag.

Staging 2026-10-27 12:45:22Z logged ``watcher_paper_scan_error`` with
``"exception": "True"`` and no type, message or traceback.
"""

from __future__ import annotations

import json

import pytest
import structlog

from app.core.logging import _redact_sensitive, configure_logging


class _BoomError(RuntimeError):
    pass


def _raise() -> None:
    raise _BoomError("candidate path failed api_key=abc123secretvalue")


def test_exc_info_true_resolves_active_exception() -> None:
    try:
        _raise()
    except _BoomError:
        event = _redact_sensitive(None, "error", {"event": "x", "exc_info": True})
    assert event["exception"] != "True"
    assert "Traceback (most recent call last)" in event["exception"]
    assert "_raise" in event["exception"]
    assert event["exception_type"] == "_BoomError"
    assert "candidate path failed" in event["exception_message"]
    assert "exc_info" not in event


def test_exc_info_true_outside_exception_is_explicit() -> None:
    event = _redact_sensitive(None, "error", {"event": "x", "exc_info": True})
    assert event["exception"] != "True"
    assert "exception_type" not in event


def test_exception_secrets_are_redacted() -> None:
    try:
        _raise()
    except _BoomError:
        event = _redact_sensitive(None, "error", {"event": "x", "exc_info": True})
    assert "abc123secretvalue" not in event["exception"]
    assert "abc123secretvalue" not in event["exception_message"]


def test_rendered_json_scan_error_has_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(log_level="INFO", json_logs=True)
    try:
        log = structlog.get_logger("test")
        try:
            _raise()
        except _BoomError:
            log.error("watcher_paper_scan_error", symbol="BTCUSDT", exc_info=True)
        line = [x for x in capsys.readouterr().out.splitlines() if "watcher_paper_scan_error" in x][
            -1
        ]
        payload = json.loads(line)
        assert payload["exception_type"] == "_BoomError"
        assert "Traceback" in payload["exception"]
    finally:
        structlog.reset_defaults()


def test_forming_candle_retry_reads_diagnostic_reason_code() -> None:
    """Diagnostics carry ``reason_code``; reading ``.reason`` raised AttributeError."""
    from types import SimpleNamespace

    from app.market_contracts.evidence_diagnostics import DiagnosticReason
    from app.watcher.contracts import WorkerCycleStatus
    from app.workers.watcher_paper import WatcherPaperRuntime

    def result(reason: DiagnosticReason) -> object:
        diagnostic = SimpleNamespace(reason_code=reason)
        outcome = SimpleNamespace(
            reason_code="canonical_contract_invalid_contract",
            evidence_diagnostics=(diagnostic,),
        )
        return SimpleNamespace(status=WorkerCycleStatus.FAILED, outcome=outcome)

    should_retry = WatcherPaperRuntime._should_retry_after_forming_candle
    assert should_retry(result(DiagnosticReason.FORMING_CANDLE)) is True  # type: ignore[arg-type]
    assert should_retry(result(DiagnosticReason.OK)) is False  # type: ignore[arg-type]
