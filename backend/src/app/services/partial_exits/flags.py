"""Per-venue partial-exit switches. Off by default; never on a real-money venue.

Only the exact venues below can be enabled. Any other venue string, a real
trading switch, or (for the demo venue) an exchange mode other than BloFin
demo returns False. The settings fields themselves reject anything but an
explicit boolean or ``true``/``false``, so an ambiguous value fails closed at
load instead of enabling.
"""

from __future__ import annotations

from app.core.config import ExchangeMode, Settings

PAPER_INTERNAL_VENUE = "PAPER_INTERNAL"
BLOFIN_DEMO_VENUE = "BLOFIN_DEMO"


def partial_exits_enabled(settings: Settings, venue: str) -> bool:
    if settings.enable_real_trading or settings.real_trading_enabled:
        return False
    if venue == PAPER_INTERNAL_VENUE:
        return settings.paper_partial_exits_enabled is True
    if venue == BLOFIN_DEMO_VENUE:
        return (
            settings.governed_demo_partial_exits_enabled is True
            and settings.exchange_mode is ExchangeMode.PAPER_EXCHANGE_DEMO
        )
    return False
