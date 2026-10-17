"""Read-only BloFin demo account provider.

Exposes instruments, balances, positions, and API-key permissions. The
permission probe is the basis for the platform's hard refusal of any key that
can move funds (withdraw/transfer) - see :func:`resolve_exchange_provider`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

from app.providers.base import ProviderHealth, ProviderKind, ProviderStatus
from app.providers.exchange.base import (
    AccountLeverageInfo,
    AccountPermissions,
    AccountPositionMode,
    ExchangeBalance,
    ExchangeInstrument,
    ExchangePositionData,
)
from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.errors import ExchangeRequestError
from app.providers.exchange.mapping import from_blofin_inst_id

logger = structlog.get_logger(__name__)

# Endpoint that reports the configured key's scope. BloFin reports scope via an
# integer ``readOnly`` field (0 = read+write/trade, 1 = read-only) and does not
# return a per-permission string here; older/proxy shapes may, so we accept both.
PERMISSIONS_ENDPOINT = "/api/v1/user/query-apikey"

_TRADE_TOKENS = frozenset({"trade", "trading", "write", "readwrite", "read_write"})
_READ_TOKENS = frozenset({"read", "readonly", "read_only"})
_TRANSFER_TOKENS = frozenset({"transfer", "internal_transfer", "internaltransfer"})
_WITHDRAW_TOKENS = frozenset({"withdraw", "withdrawal"})

# Dict keys whose value is a list/string/bool of permission tokens.
_PERMISSION_FIELDS = (
    "permissions",
    "permission",
    "perm",
    "perms",
    "scopes",
    "scope",
    "authorities",
    "authority",
)

# String/int values of a ``readOnly``-style field that mean "read-only".
_READ_ONLY_TRUE = frozenset({"1", "true", "yes", "readonly", "read_only", "read-only", "y"})
_READ_ONLY_FALSE = frozenset(
    {"0", "false", "no", "readwrite", "read_write", "read-write", "rw", "n"}
)


def _to_decimal(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _account_number(value: Any) -> Decimal:
    """Account evidence must never turn a missing or malformed quantity into zero."""
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExchangeRequestError("Invalid BloFin account quantity.") from exc
    if not result.is_finite():
        raise ExchangeRequestError("Invalid BloFin account quantity.")
    return result


def _optional_account_number(value: Any) -> Decimal | None:
    return None if value in (None, "") else _account_number(value)


def _truthy(value: Any) -> bool:
    """Interpret a permission flag value (bool/int/string) as a boolean."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _normalize_tokens(value: Any) -> set[str]:
    """Lower-cased permission tokens from a string, list, or boolean-dict value."""
    tokens: set[str] = set()
    if isinstance(value, str):
        for part in value.replace(",", " ").replace(";", " ").replace("|", " ").split():
            cleaned = part.strip().lower()
            if cleaned:
                tokens.add(cleaned)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            tokens |= _normalize_tokens(item)
    elif isinstance(value, dict):
        # e.g. {"read": true, "trade": true}
        tokens |= {str(k).strip().lower() for k, v in value.items() if _truthy(v)}
    return tokens


def _coerce_read_only(value: Any) -> bool | None:
    """Map a ``readOnly``-style field to True/False, or None when absent/unknown.

    BloFin semantics: ``0`` = read + write (trade allowed), ``1`` = read only.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) == 1
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _READ_ONLY_TRUE:
            return True
        if token in _READ_ONLY_FALSE:
            return False
    return None


def parse_account_permissions(payload: Any) -> AccountPermissions:
    """Parse a BloFin API-key info payload into normalized permission flags.

    Accepts every realistic shape returned by (or proxied in front of) BloFin's
    ``query-apikey`` endpoint:

    * the canonical demo response ``{"readOnly": 0}`` (0 = read+trade, 1 = read-only);
    * a ``permissions`` string such as ``"read,trade"`` or a list ``["READ", "TRADE"]``;
    * a boolean map such as ``{"read": true, "trade": true}``;
    * ``scopes`` / ``authorities`` arrays.

    Parsing is case-insensitive. ``TRADE`` implies read access (per BloFin docs).
    Withdraw/transfer scopes are surfaced so callers can refuse money-movement keys;
    an empty or unexpected payload yields ``can_trade=False`` so startup fails safe.
    """
    row = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(row, dict):
        row = {}

    response_keys = tuple(sorted(str(k) for k in row))

    tokens: set[str] = set()
    for key in _PERMISSION_FIELDS:
        if key in row:
            tokens |= _normalize_tokens(row[key])

    # Direct boolean flags at the top level, e.g. {"trade": true}.
    flag_trade = _truthy(row.get("trade")) or _truthy(row.get("trading"))
    flag_read = _truthy(row.get("read"))
    flag_transfer = _truthy(row.get("transfer")) or _truthy(row.get("internal_transfer"))
    flag_withdraw = _truthy(row.get("withdraw")) or _truthy(row.get("withdrawal"))

    read_only = _coerce_read_only(row.get("readOnly", row.get("read_only")))

    has_trade = bool(tokens & _TRADE_TOKENS) or flag_trade or read_only is False
    has_transfer = bool(tokens & _TRANSFER_TOKENS) or flag_transfer
    has_withdraw = bool(tokens & _WITHDRAW_TOKENS) or flag_withdraw
    # TRADE includes read access; a known readOnly flag also implies read.
    has_read = bool(tokens & _READ_TOKENS) or flag_read or has_trade or read_only is not None

    return AccountPermissions(
        can_read=has_read,
        can_trade=has_trade,
        can_withdraw=has_withdraw,
        can_transfer=has_transfer,
        raw_scopes=tuple(sorted(tokens)),
        response_keys=response_keys,
    )


class BloFinAccountProvider:
    """Read-only access to the BloFin demo account."""

    name = "blofin-demo-account"
    kind = ProviderKind.EXCHANGE
    permissions_endpoint = PERMISSIONS_ENDPOINT

    def __init__(self, client: BloFinClient, *, is_demo: bool = True) -> None:
        self._client = client
        self._is_demo = is_demo
        self._permissions_verified_at: datetime | None = None
        self.native_account_uid: str | None = None
        self.total_equity_usd: Decimal | None = None

    def get_instruments(self) -> list[ExchangeInstrument]:
        data = self._client.request(
            "GET", "/api/v1/market/instruments", params={"instType": "SWAP"}
        )
        rows = data if isinstance(data, list) else []
        instruments: list[ExchangeInstrument] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            inst_id = str(row.get("instId", ""))
            if not inst_id:
                continue
            instruments.append(
                ExchangeInstrument(
                    symbol=from_blofin_inst_id(inst_id),
                    inst_id=inst_id,
                    base_currency=str(row.get("baseCurrency", "")),
                    quote_currency=str(row.get("quoteCurrency", "")),
                    instrument_type=str(row.get("instType", "")),
                    tick_size=_to_decimal(row.get("tickSize")) if row.get("tickSize") else None,
                    lot_size=_to_decimal(row.get("lotSize")) if row.get("lotSize") else None,
                    min_size=_to_decimal(row.get("minSize")) if row.get("minSize") else None,
                    contract_size=(
                        _to_decimal(row.get("contractValue")) if row.get("contractValue") else None
                    ),
                    active=str(row.get("state", "live")).lower() in ("live", "active", ""),
                )
            )
        return instruments

    def get_balances(self) -> list[ExchangeBalance]:
        self.total_equity_usd = None
        data = self._client.request("GET", "/api/v1/account/balance", signed=True)
        rows = self._coerce_rows(data, key="details")
        if (
            not isinstance(data, list | dict)
            or not isinstance(rows, list)
            or (isinstance(data, dict) and "details" not in data)
        ):
            raise ExchangeRequestError("Invalid BloFin balance response.")
        wrapper = data[0] if isinstance(data, list) and len(data) == 1 else data
        if isinstance(wrapper, dict) and "details" in wrapper:
            # BloFin totalEquity is USD; details[].equity is in that asset.
            self.total_equity_usd = _optional_account_number(wrapper.get("totalEquity"))
        balances: list[ExchangeBalance] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ExchangeRequestError("Invalid BloFin balance row.")
            asset = row.get("currency", row.get("ccy"))
            if not isinstance(asset, str) or not asset:
                raise ExchangeRequestError("Missing BloFin balance asset.")
            balances.append(
                ExchangeBalance(
                    asset=asset,
                    total=_account_number(row.get("balance")),
                    available=_account_number(row.get("available", row.get("availBal"))),
                    equity=_optional_account_number(row.get("equity", row.get("eq"))),
                )
            )
        return balances

    def get_position_metadata(self, inst_ids: set[str]) -> dict[str, dict[str, str]]:
        """Verify base-valued linear contracts using one public instruments read.

        Kept separate from execution's existing permissive instrument adapter.
        Missing, ambiguous or unsupported metadata never implies a multiplier.
        """
        # BloFin documents only optional instId; an unfiltered read is the
        # documented bulk form. Verify the returned type rather than guessing a query.
        data = self._client.request("GET", "/api/v1/market/instruments")
        if not isinstance(data, list):
            raise ExchangeRequestError("Invalid BloFin instruments response.")
        result: dict[str, dict[str, str]] = {}
        for inst_id in inst_ids:
            matches = [
                row for row in data if isinstance(row, dict) and row.get("instId") == inst_id
            ]
            if len(matches) != 1:
                continue
            row = matches[0]
            base, quote = row.get("baseCurrency"), row.get("quoteCurrency")
            if (
                not isinstance(base, str)
                or not base.isalnum()
                or not isinstance(quote, str)
                or not quote.isalnum()
                or inst_id not in {f"{base}-{quote}", f"{base}-{quote}-SWAP"}
                or row.get("instType") not in {"SWAP", "PERPETUAL"}
                or row.get("contractType") != "linear"
                or row.get("state") != "live"
                or row.get("contractValueCurrency", base) != base
            ):
                continue
            try:
                multiplier = _account_number(row.get("contractValue"))
            except ExchangeRequestError:
                continue
            if multiplier <= 0:
                continue
            result[inst_id] = {
                "inst_id": inst_id,
                "base_asset": base,
                "quote_asset": quote,
                "contract_type": "linear",
                "contract_value": format(multiplier, "f"),
                "source": "/api/v1/market/instruments",
            }
        return result

    def get_positions(self) -> list[ExchangePositionData]:
        data = self._client.request("GET", "/api/v1/account/positions", signed=True)
        if not isinstance(data, list):
            raise ExchangeRequestError("Invalid BloFin positions response.")
        rows = data
        positions: list[ExchangePositionData] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ExchangeRequestError("Invalid BloFin position row.")
            inst_id = row.get("instId")
            size = _account_number(row.get("positions", row.get("pos")))
            if size == 0:
                continue
            side = str(row.get("positionSide", row.get("posSide", "")))
            if not isinstance(inst_id, str) or not inst_id or side not in {"long", "short", "net"}:
                raise ExchangeRequestError("Invalid BloFin position identity.")
            positions.append(
                ExchangePositionData(
                    symbol=from_blofin_inst_id(inst_id),
                    inst_id=inst_id,
                    side=side,
                    size=size,
                    entry_price=_optional_account_number(row.get("averagePrice", row.get("avgPx"))),
                    mark_price=_optional_account_number(row.get("markPrice", row.get("markPx"))),
                    unrealized_pnl=_optional_account_number(
                        row.get("unrealizedPnl", row.get("upl"))
                    ),
                    leverage=_optional_account_number(row.get("leverage")),
                )
            )
        return positions

    def get_position_mode(self) -> AccountPositionMode:
        """Return the account position mode (one-way net vs hedge long/short)."""
        data = self._client.request("GET", "/api/v1/account/position-mode", signed=True)
        row = data if isinstance(data, dict) else {}
        return AccountPositionMode(position_mode=str(row.get("positionMode", "")))

    def get_leverage_info(self, *, inst_id: str, margin_mode: str) -> AccountLeverageInfo:
        """Return configured leverage for an instrument under a margin mode."""
        data = self._client.request(
            "GET",
            "/api/v1/account/leverage-info",
            params={"instId": inst_id, "marginMode": margin_mode},
            signed=True,
        )
        row = data if isinstance(data, dict) else {}
        position_side_raw = row.get("positionSide")
        return AccountLeverageInfo(
            inst_id=str(row.get("instId", inst_id)),
            margin_mode=str(row.get("marginMode", margin_mode)),
            leverage=_to_decimal(row.get("leverage", "0")),
            position_side=str(position_side_raw) if position_side_raw not in (None, "") else None,
        )

    def get_account_permissions(self) -> AccountPermissions:
        """Probe the configured API key's scopes.

        Raises an :class:`ExchangeError` subclass if the venue cannot be reached
        or rejects the key; callers decide whether that is fatal.
        """
        from app.core.blofin_identity import native_uid

        self.native_account_uid = None
        data = self._client.request("GET", PERMISSIONS_ENDPOINT, signed=True)
        permissions = parse_account_permissions(data)
        self.native_account_uid = native_uid(data) if permissions.can_read else None
        self._permissions_verified_at = datetime.now(UTC)
        return permissions

    @staticmethod
    def _coerce_rows(data: Any, *, key: str) -> list[Any]:
        rows = data
        if isinstance(data, list):
            # Either a list of detail rows, or a list wrapping a dict with details.
            if data and isinstance(data[0], dict) and key in data[0]:
                rows = data[0][key]
        elif isinstance(data, dict):
            rows = data.get(key)
        if not isinstance(rows, list):
            raise ExchangeRequestError("Invalid BloFin balance response.")
        return rows

    def status(self) -> ProviderStatus:
        verified = self._permissions_verified_at is not None
        detail = "BloFin demo account (read-only, no withdrawal scope)."
        if not verified:
            detail = "BloFin demo account (read-only); permissions not yet verified."
        return ProviderStatus(
            name=self.name,
            kind=self.kind,
            health=ProviderHealth.HEALTHY if verified else ProviderHealth.DEGRADED,
            using_fallback=False,
            is_mock=False,
            detail=detail,
            last_success_at=self._client.last_success_at,
            error_message=self._client.last_error,
        )
