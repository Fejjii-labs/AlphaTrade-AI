"""``exc_info=True`` must render the active exception, not the literal flag.

Staging 2026-10-27 12:45:22Z logged ``watcher_paper_scan_error`` with
``"exception": "True"`` and no type, message or traceback.
"""

from __future__ import annotations

import json
import sys

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
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from app.market_contracts.evidence_diagnostics import (
        DiagnosticReason,
        DiagnosticStatus,
        EvidenceComponent,
        EvidenceComponentDiagnostic,
    )
    from app.watcher.contracts import WorkerCycleStatus
    from app.workers.watcher_paper import WatcherPaperRuntime

    def result(reason: DiagnosticReason) -> object:
        diagnostic = EvidenceComponentDiagnostic(
            component=next(iter(EvidenceComponent)),
            provider="binance-usdm-perpetual",
            symbol="BTCUSDT",
            status=next(iter(DiagnosticStatus)),
            reason_code=reason,
            evaluated_at=datetime(2026, 10, 27, tzinfo=UTC),
        )
        assert not hasattr(diagnostic, "reason")
        outcome = SimpleNamespace(
            reason_code="canonical_contract_invalid_contract",
            evidence_diagnostics=(diagnostic,),
        )
        return SimpleNamespace(status=WorkerCycleStatus.FAILED, outcome=outcome)

    should_retry = WatcherPaperRuntime._should_retry_after_forming_candle
    assert should_retry(result(DiagnosticReason.FORMING_CANDLE)) is True  # type: ignore[arg-type]
    assert should_retry(result(DiagnosticReason.OK)) is False  # type: ignore[arg-type]


# --- Redaction of credential shapes in exception text (Griller PR #365 B1) ---
# All values are synthetic.
_SECRET_SHAPES: dict[str, tuple[str, str]] = {
    "blofin_api_secret_kv": ("blofin_api_secret=BFSECRETzz9", "BFSECRETzz9"),
    "plain_secret_kv": ("secret=PLAINSECRETqq7", "PLAINSECRETqq7"),
    "url_token": ("https://x.example/cb?token=URLTOKENabc&sig=1", "URLTOKENabc"),
    "url_signature": ("https://x.example/cb?signature=URLSIGxyz", "URLSIGxyz"),
    "dict_repr_api_key": (repr({"api_key": "DICTKEYvvv"}), "DICTKEYvvv"),
    "dict_repr_secret": (repr({"secret": "DICT SECRETwww"}), "SECRETwww"),
    "header_access_key": ("ACCESS-KEY: HDRKEYuuu", "HDRKEYuuu"),
    "header_access_sign": ("ACCESS-SIGN: HDRSIGttt", "HDRSIGttt"),
    "header_access_passphrase": ("ACCESS-PASSPHRASE: HDRPPsss", "HDRPPsss"),
    "json_passphrase": ('{"passphrase": "JSONPPrrr"}', "JSONPPrrr"),
}


def _raise_with(placement: str, text: str) -> None:
    if placement == "message":
        raise _BoomError(f"request failed {text}")
    if placement == "args":
        raise OSError(5, text)
    if placement == "chained_cause":
        try:
            raise KeyError(f"cause {text}")
        except KeyError as cause:
            raise _BoomError("outer failure") from cause
    if placement == "traceback":
        error = _BoomError("outer failure")
        error.add_note(f"note {text}")  # rendered only in the traceback
        raise error
    raise AssertionError(placement)


def _resolve(mode: str) -> dict[str, object]:
    if mode == "true":
        return _redact_sensitive(None, "error", {"event": "x", "exc_info": True})
    exc_type, error, tb = sys.exc_info()
    if mode == "instance":
        return _redact_sensitive(None, "error", {"event": "x", "exc_info": error})
    return _redact_sensitive(None, "error", {"event": "x", "exc_info": (exc_type, error, tb)})


@pytest.mark.parametrize("mode", ["true", "instance", "tuple"])
@pytest.mark.parametrize("placement", ["message", "args", "chained_cause", "traceback"])
@pytest.mark.parametrize("shape", sorted(_SECRET_SHAPES))
def test_exception_credential_shapes_are_redacted(shape: str, placement: str, mode: str) -> None:
    text, marker = _SECRET_SHAPES[shape]
    try:
        _raise_with(placement, text)
    except Exception:
        event = _resolve(mode)
    rendered = json.dumps(event)
    assert marker not in rendered
    assert "***REDACTED***" in str(event["exception"])
    assert "Traceback (most recent call last)" in str(event["exception"])


def test_secret_straddling_message_cap_is_redacted_before_cap() -> None:
    secret = "sk-" + "A" * 24
    # The key starts before character 500 and ends after it. Capping first would
    # keep ``sk-`` plus 7 characters, too short for the pattern, so it would leak.
    try:
        raise _BoomError("x" * 490 + secret)
    except _BoomError:
        event = _redact_sensitive(None, "error", {"event": "x", "exc_info": True})
    message = str(event["exception_message"])
    assert len(message) <= 500
    assert "sk-A" not in message
    assert "AAAA" not in message
    assert message.endswith("x***REDACTE")  # the cut falls inside the marker, not the key


def test_traceback_is_capped_after_redaction() -> None:
    try:
        raise _BoomError("secret=TAILSECRETkkk " + "y" * 100_000)
    except _BoomError:
        event = _redact_sensitive(None, "error", {"event": "x", "exc_info": True})
    exception = str(event["exception"])
    assert len(exception) <= 32 * 1024
    assert exception.startswith("...[traceback truncated]...")
    assert "TAILSECRETkkk" not in exception


def test_stack_info_is_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(log_level="INFO", json_logs=True)
    try:
        log = structlog.get_logger("test")
        token = "secret=" + "STACKSECRETjjj"
        log.info("stack_probe", stack_info=True, note=token)
        _emit_stack_with_secret(log)
        out = capsys.readouterr().out
        assert "stack_probe" in out
        assert "STACKSECRETjjj" not in out
        assert "STACKFRAMEhhh" not in out
        assert '"stack"' in out
    finally:
        structlog.reset_defaults()


def _emit_stack_with_secret(log: structlog.stdlib.BoundLogger) -> None:
    log.info("stack_probe_frame", stack_info=True)  # secret=STACKFRAMEhhh
