"""Provider sequence admission for snapshot invalidation, without a WS activation.

The REST consumer replaces whole snapshots. It never applies a delta to a
truncated book or presents a reconstructed book as complete historical data.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.market_contracts.enums import VenueId
from app.market_contracts.errors import GapDetectedError
from app.market_contracts.hashing import semantic_content_hash
from app.market_contracts.order_book import provider_integer


class BookSequenceGuard:
    def __init__(self, venue: VenueId) -> None:
        self.venue = venue
        self.snapshot_id: int | None = None
        self.previous_final_id: int | None = None
        self.last_fingerprint: str | None = None

    def snapshot(self, update_id: int) -> None:
        self.snapshot_id = provider_integer(update_id)
        self.previous_final_id = None
        self.last_fingerprint = None

    def disconnect(self) -> None:
        self.snapshot_id = None
        self.previous_final_id = None
        self.last_fingerprint = None

    def delta(self, row: Mapping[str, Any]) -> bool:
        """True means invalidate the REST snapshot; False means duplicate/old.

        Binance first bridge follows futures U <= snapshot <= u, then pu == prior
        u. Bybit exposes ordering IDs but no documented predecessor link or
        contiguous increment guarantee; its delta continuity stays unproven.
        """
        try:
            if self.snapshot_id is None:
                raise ValueError("snapshot_required")
            if self.venue is VenueId.BYBIT:
                raise ValueError("bybit_delta_continuity_unproven")
            first, final, previous = (provider_integer(row[key]) for key in ("U", "u", "pu"))
            if first > final:
                raise ValueError("invalid_sequence_range")
            fingerprint = semantic_content_hash(row)
            if final < (self.previous_final_id or self.snapshot_id):
                return False
            if final == self.previous_final_id:
                if fingerprint == self.last_fingerprint:
                    return False
                raise ValueError("conflicting_duplicate_update")
            if self.previous_final_id is None:
                if not first <= self.snapshot_id <= final:
                    raise ValueError("snapshot_bridge_gap")
            elif previous != self.previous_final_id:
                raise ValueError("predecessor_gap")
            self.previous_final_id = final
            self.last_fingerprint = fingerprint
            return True
        except (KeyError, TypeError, ValueError) as exc:
            self.disconnect()
            raise GapDetectedError(f"order_book_resync_required:{exc}") from exc
