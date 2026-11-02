"""Alert wording for opt-in watchlist projection (WATCHER_APPLY_STRATEGIES_TO_WATCHLIST).

A projected scope reuses parameters approved for another symbol/timeframe.
Its alerts must say so, so nobody reads them as coming from an approved market.
"""

from __future__ import annotations


def projection_tag(source: str) -> str:
    """Header suffix, e.g. ``(watchlist scan, BTCUSDT 15m params)``."""

    return f"(watchlist scan, {source} params)"


def projection_note(source: str, *, symbol: str, timeframe: str) -> str:
    return (
        f"Observation only: parameters approved for {source} are reused unchanged "
        f"on {symbol} {timeframe}, which was not approved. No paper or demo order."
    )
