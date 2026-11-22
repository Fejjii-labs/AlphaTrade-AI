"""Demo data mode: enter every valid setup on BloFin DEMO to collect strategy data.

Owner decision 2026-11-20. With GOVERNED_DEMO_DATA_MODE_ENABLED the governed demo
loop trades any scanned coin of a selected, approved strategy (not only the one
saved coin) and holds one open demo position per coin instead of one per account.
It is honoured only with risk model v2, whose daily stop is the brake, and never
with real trading. The kill switch, one position per coin and every other check
are unchanged.
"""

from __future__ import annotations

from app.core.config import Settings


def demo_data_mode_enabled(settings: Settings | None) -> bool:
    return (
        settings is not None
        and getattr(settings, "governed_demo_data_mode_enabled", False) is True
        and getattr(settings, "risk_model_v2_enabled", False) is True
        and getattr(settings, "enable_real_trading", True) is False
        and getattr(settings, "real_trading_enabled", True) is False
    )
