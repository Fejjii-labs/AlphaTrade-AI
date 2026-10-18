"""Unwired exchange margin writer.

Setting leverage or switching a position to isolated margin is a new BloFin
write. It is not implemented here and this module must not gain a client,
an allowlist call, or a route that submits that write.

A follow-up needs its own pull request, a Griller review, and an ADR update
before any of the following can be connected: demo-host allowlisting, a
pinned account, a read-back that the set leverage and margin mode match, and
the write itself. Until that review, callers use
:func:`unwired_margin_writer` and get an error if they invoke it.

See AT-ADR-107.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol


class ExchangeMarginWriter(Protocol):
    """The write this policy is not allowed to perform."""

    def set_leverage(self, *, symbol: str, leverage: Decimal, margin_mode: str) -> None:
        """Set leverage and margin mode. Not wired."""


class UnwiredExchangeMarginWriter:
    """The only writer this package constructs. It is not a BloFin client."""

    def set_leverage(self, *, symbol: str, leverage: Decimal, margin_mode: str) -> None:
        raise NotImplementedError(
            "BloFin leverage and margin-mode writes are not wired. "
            "They need a separate PR, a Griller review, and an ADR update "
            "(AT-ADR-107)."
        )


def unwired_margin_writer() -> ExchangeMarginWriter:
    """Return the unwired writer. Do not replace this with a live client here."""

    return UnwiredExchangeMarginWriter()
