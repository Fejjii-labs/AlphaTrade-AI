"""Read Binance USD-M exchangeInfo without treating a blocked host as a delisting.

`fapi.binance.com` is the evidence host. When that host answers with a regional
rejection, the same USD-M book may be read from `www.binance.com/fapi/v1/exchangeInfo`.
Redirects are not followed. A payload that is not `futuresType=U_MARGINED` is
not applied. Bybit listings are not invented from a Binance book.

With a ``rest_gate`` the read goes through the process Binance REST gate: a
closed gate (418/429 deadline, shared ban or startup standby) sends nothing,
used weight is recorded, and a 418/429 closes the gate and stops before the
next host is tried.
"""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from app.market_contracts.adapters.binance_rest_gate import (
    BinanceRestGate,
    process_binance_rest_gate,
)
from app.market_contracts.adapters.http import _retry_after_seconds
from app.market_contracts.enums import VenueId
from app.market_contracts.provider_contracts import (
    ContractBook,
    ContractProviderUnreachableError,
    apply_binance_exchange_info,
    unreachable_verdicts,
)

_BINANCE_CATALOG_ORIGINS = (
    "https://fapi.binance.com",
    "https://www.binance.com",
)
_EXCHANGE_INFO_PATH = "/fapi/v1/exchangeInfo"
_REGIONAL_STATUS = frozenset({401, 403, 451})


def fetch_binance_usdm_exchange_info(
    *,
    timeout_seconds: float = 8.0,
    client: httpx.Client | None = None,
    rest_gate: BinanceRestGate | None = None,
) -> dict[str, object]:
    """Return one USD-M exchangeInfo object, or raise when every host is blocked."""

    reasons: list[str] = []
    headers = {
        "User-Agent": "AlphaTradeAI-perpetual-evidence/read-only",
        "Accept": "application/json",
    }
    owned = client is None
    http = client or httpx.Client(timeout=timeout_seconds, follow_redirects=False, headers=headers)
    try:
        for origin in _BINANCE_CATALOG_ORIGINS:
            url = origin + _EXCHANGE_INFO_PATH
            if rest_gate is not None and rest_gate.remaining() is not None:
                reasons.append("binance rest gate closed")
                break
            try:
                response = http.get(url)
            except httpx.HTTPError:
                reasons.append(f"{origin} unreachable")
                continue
            if rest_gate is not None:
                rest_gate.note_used_weight(response.headers)
                if response.status_code in (418, 429):
                    response.read()
                    rest_gate.note_failure(
                        "ban" if response.status_code == 418 else "rate_limit",
                        _retry_after_seconds(response),
                    )
                    reasons.append(f"{origin} HTTP {response.status_code}")
                    break
            if response.status_code in _REGIONAL_STATUS or response.is_redirect:
                reasons.append(f"{origin} HTTP {response.status_code}")
                continue
            if response.status_code != 200:
                reasons.append(f"{origin} HTTP {response.status_code}")
                continue
            try:
                payload = response.json()
            except ValueError:
                reasons.append(f"{origin} malformed")
                continue
            if not isinstance(payload, dict):
                reasons.append(f"{origin} malformed")
                continue
            futures_type = payload.get("futuresType")
            if str(futures_type).upper() != "U_MARGINED":
                reasons.append(f"{origin} not USD-M")
                continue
            if not isinstance(payload.get("symbols"), list):
                reasons.append(f"{origin} missing symbols")
                continue
            return payload
    finally:
        if owned:
            http.close()
    detail = ", ".join(reasons) if reasons else "no catalog host answered"
    raise ContractProviderUnreachableError(detail)


class BinanceContractDiscovery:
    """Refresh the watchlist book once per process, then reuse that payload."""

    def __init__(
        self, *, timeout_seconds: float = 8.0, rest_gate: BinanceRestGate | None = None
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._gate = rest_gate
        self._payload: dict[str, object] | None = None
        self._unreachable: str | None = None
        self.verdicts: dict[tuple[str, VenueId], object] = {}

    def __call__(self, book: ContractBook, symbols: Sequence[str]) -> ContractBook:
        if self._payload is None and self._unreachable is None:
            gate = self._gate or process_binance_rest_gate()
            try:
                self._payload = fetch_binance_usdm_exchange_info(
                    timeout_seconds=self._timeout_seconds, rest_gate=gate
                )
            except ContractProviderUnreachableError as exc:
                # A closed gate is temporary; retry on the next call.
                if gate.remaining() is None:
                    self._unreachable = exc.reason
        if self._payload is None:
            pending = unreachable_verdicts(
                symbols,
                venue=VenueId.BINANCE,
                reason="provider_unreachable",
            )
            self.verdicts = {(item.symbol, item.venue): item for item in pending}
            return book
        updated, verdicts = apply_binance_exchange_info(book, self._payload, symbols)
        self.verdicts = {(item.symbol, item.venue): item for item in verdicts}
        return updated
