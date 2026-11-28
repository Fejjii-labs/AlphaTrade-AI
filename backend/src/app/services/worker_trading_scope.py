"""Which strategy families and timeframes the paper worker trades on BloFin demo.

``GOVERNED_DEMO_DLINE_TIMEFRAMES``, ``GOVERNED_DEMO_NESTED_TIMEFRAMES`` and
``GOVERNED_DEMO_SFP_ENABLED`` are worker-only settings: the API process cannot
read them from its own env. The worker publishes them as short tokens with its
heartbeat (``persistence/worker_runtime_state.py``) so the Bot page can tell a
traded strategy from one that is only watched. The rules mirror the refusals in
``services/governed_blofin_demo.py``; this module only reports, it gates nothing.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.core.config import Settings
from app.schemas.common import Timeframe
from app.services.demo_data_mode import demo_data_mode_enabled
from app.services.partial_exits.flags import BLOFIN_DEMO_VENUE, partial_exits_enabled

ANY_TIMEFRAME = "any"
OFF = "off"
_KNOWN_TIMEFRAMES = frozenset(timeframe.value for timeframe in Timeframe)


def trading_scope_tokens(settings: Settings) -> tuple[str, ...]:
    """``family:timeframe``, ``family:off`` or ``family:any`` per family.

    Empty when the governed demo loop is off: the worker then reports no scope
    and the page shows the status as unknown rather than guessing.
    """

    if not settings.governed_blofin_demo_enabled:
        return ()
    dline_ready = demo_data_mode_enabled(settings) and partial_exits_enabled(
        settings, BLOFIN_DEMO_VENUE
    )
    return (
        *_family_tokens("d_line", dline_ready, settings.governed_demo_dline_timeframes),
        *_family_tokens("nested", True, settings.governed_demo_nested_timeframes),
        f"sfp:{ANY_TIMEFRAME}" if settings.governed_demo_sfp_enabled else f"sfp:{OFF}",
    )


def _family_tokens(family: str, trading: bool, timeframes: Iterable[object]) -> list[str]:
    if not trading:
        return [f"{family}:{OFF}"]
    chosen = dict.fromkeys(str(value).strip().lower() for value in timeframes)
    tokens = [f"{family}:{value}" for value in chosen if value in _KNOWN_TIMEFRAMES]
    return tokens or [f"{family}:{OFF}"]
