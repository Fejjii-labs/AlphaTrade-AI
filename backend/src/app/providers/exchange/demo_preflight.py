"""Bounded demo preflight diagnostics; never include exception text or venue payloads."""

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import InvalidOperation
from typing import Any

from app.providers.exchange.demo_quote import DemoQuoteTimingError
from app.providers.exchange.errors import (
    ExchangeAuthError,
    ExchangeError,
    ExchangeRateLimitError,
    ExchangeRequestError,
    ExchangeUnavailableError,
)

_VALIDATION_REASONS = {
    "Verified read/trade-only demo permissions required.": "permissions_not_read_trade_only",
    "Governed demo requires the existing NET account mode.": "position_mode_not_net",
    "Demo instrument unavailable.": "instrument_unavailable",
    "Only base-valued linear USDT contracts are supported.": "unsupported_contract",
    "Demo perpetual instrument required.": "instrument_not_perpetual",
    "Positive finite venue value required.": "invalid_instrument_or_quote_value",
    "Demo account must already use leverage 1; no leverage mutation.": "leverage_not_one",
    "Demo account leverage does not match required leverage; no leverage mutation.": (
        "leverage_mismatch"
    ),
    "Demo account state incomplete or unreadable.": "account_state_incomplete",
    "Demo position already open or unreadable; new entry refused.": "account_not_flat",
    "Demo pending orders or protection; new entry refused.": "pending_orders_present",
    "Demo USDT equity unavailable.": "usdt_balance_unavailable",
    "Demo quote unavailable.": "quote_unavailable",
    "Demo quote stale or future dated.": "quote_stale_or_future",
    "Demo quote timestamp invalid.": "malformed_or_unsupported_response",
    "Demo quote receipt clock must be timezone aware.": "invalid_receipt_clock",
    "Demo quote value malformed.": "invalid_instrument_or_quote_value",
    "Demo quote side must be BUY or SELL.": "quote_side_invalid",
    "Demo order book unavailable or ambiguous.": "quote_unavailable",
    "Demo order book instrument mismatch.": "quote_instrument_mismatch",
    "Demo order book depth unavailable.": "quote_depth_unavailable",
    "Demo order book level malformed.": "quote_depth_malformed",
    "Demo order book level violates venue increments.": "quote_depth_invalid_increments",
    "Demo order book levels unordered or duplicated.": "quote_depth_unordered",
    "Demo order book spread crossed or locked.": "quote_spread_invalid",
    "Demo order book spread exceeds the slippage limit.": "quote_spread_excessive",
    "Demo quote quantity violates contract increments or limits.": "quote_quantity_invalid",
    "Demo order book has insufficient depth within the slippage limit.": "quote_depth_insufficient",
}


def preflight_category(diagnostics: dict[str, Any]) -> str:
    if diagnostics.get("stage") in {"positions", "pending_orders", "pending_protection", "balance"}:
        return "manual_demo_limits"
    return "exchange_constraints"


def preflight_message(diagnostics: dict[str, Any]) -> str:
    """Actionable fixed copy; never reflect arbitrary provider text into the UI."""
    reason = diagnostics.get("reason_code")
    if reason in {"account_state_incomplete", "usdt_balance_unavailable"}:
        return (
            "Demo account state is unknown or incomplete. Verify positions, pending orders and "
            "USDT equity in the selected BloFin demo account, then refresh; no entry is permitted."
        )
    if reason in {"account_not_flat", "pending_orders_present"}:
        return (
            "Manual demo capacity: the selected demo account has an open position, pending order "
            "or protection. Reconcile it in BloFin demo before preparing another test."
        )
    if diagnostics.get("stage") in {"positions", "pending_orders", "pending_protection", "balance"}:
        return (
            "Demo account state could not be verified. Check the reported account endpoint, "
            "positions, pending orders and available USDT in BloFin demo, then refresh."
        )
    if reason == "quote_stale_or_future":
        if diagnostics.get("freshness_status") == "future":
            return (
                "BloFin demo order book is future dated. "
                "Check the server clock and venue timestamp; preview refused."
            )
        return (
            "BloFin demo order book is at least 10 seconds old. "
            "Retry preview when fresh demo depth is available."
        )
    if reason in {"quote_depth_insufficient", "quote_depth_unavailable", "quote_unavailable"}:
        return (
            "BloFin demo order book has no executable depth for this size within 10 bps. "
            "Check demo liquidity or reduce the contract quantity and preview again."
        )
    if reason == "quote_quantity_invalid":
        return (
            "Demo quantity must use the instrument's contract lot, minimum and maximum. "
            "Correct the contract quantity and preview again."
        )
    if reason in {"venue_unavailable", "venue_rate_limited"}:
        return (
            "BloFin demo data read failed or was rate limited. Wait briefly and retry preview; "
            "check demo provider access if it persists."
        )
    if reason == "venue_auth_or_permission_rejected":
        return (
            "BloFin demo rejected a preflight read. Verify demo API access and the required "
            "read/trade-only permissions; preview refused."
        )
    if reason == "venue_request_rejected":
        return (
            "BloFin demo rejected a preflight request. Check the reported endpoint "
            "and venue error code before retrying preview."
        )
    if diagnostics.get("stage") == "quote":
        return (
            "BloFin demo quote evidence is invalid. Check the reported reason, book timestamp, "
            "spread and contract depth; preview refused."
        )
    return (
        "BloFin demo preflight failed. Check the reported stage and reason before retrying preview."
    )


def failure_diagnostics(
    exc: Exception, *, stage: str, endpoint: str | None = None
) -> dict[str, Any]:
    """Copy only fixed labels and bounded numeric protocol fields, never messages."""
    if isinstance(exc, DemoPreflightError):
        return dict(exc.diagnostics)
    error_type = "UnexpectedError"
    reason = "unexpected_failure"
    for cls, code in (
        (ExchangeAuthError, "venue_auth_or_permission_rejected"),
        (ExchangeRateLimitError, "venue_rate_limited"),
        (ExchangeUnavailableError, "venue_unavailable"),
        (ExchangeRequestError, "venue_request_rejected"),
    ):
        if isinstance(exc, cls):
            error_type, reason = cls.__name__, code
            break
    else:
        if isinstance(exc, (ValueError, TypeError, KeyError, InvalidOperation, OverflowError)):
            error_type = "InvalidVenueData"
            reason = _VALIDATION_REASONS.get(str(exc), "malformed_or_unsupported_response")
    diagnostics: dict[str, Any] = {
        "stage": stage,
        "reason_code": reason,
        "error_type": error_type,
    }
    if endpoint is not None:
        diagnostics["endpoint_name"] = endpoint
    if isinstance(exc, DemoQuoteTimingError):
        diagnostics.update(exc.timing)
    if isinstance(exc, ExchangeError) and exc.details is not None:
        status = exc.details.http_status
        if type(status) is int and 100 <= status <= 599:
            diagnostics["http_status"] = status
        venue_code = exc.details.venue_error_code
        if (
            isinstance(venue_code, str)
            and 0 < len(venue_code) <= 16
            and venue_code.isascii()
            and venue_code.isdecimal()
        ):
            diagnostics["venue_error_code"] = venue_code
    return diagnostics


class DemoPreflightError(ValueError):
    """A refusal with safe stage evidence, compatible with existing ValueError callers."""

    def __init__(self, exc: Exception, *, stage: str, endpoint: str) -> None:
        self.diagnostics = failure_diagnostics(exc, stage=stage, endpoint=endpoint)
        # Preserve known fixed validation copy; arbitrary exception text stays private.
        message = str(exc) if str(exc) in _VALIDATION_REASONS else "Demo preflight failed."
        super().__init__(message)


@contextmanager
def preflight_stage(stage: str, endpoint: str) -> Iterator[None]:
    try:
        yield
    except DemoPreflightError:
        raise
    except Exception as exc:
        raise DemoPreflightError(exc, stage=stage, endpoint=endpoint) from exc
