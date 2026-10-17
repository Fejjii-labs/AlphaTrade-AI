"""A fixed evidence cutoff and an independently sampled acquisition clock."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.market_contracts.errors import WrongSourceError


@dataclass
class ScanAcquisition:
    """Receipt times never advance the clock; only the caller's clock can.

    Frozen/as-of reads have no completion clock. Current scans retain their
    original event/candle cutoff while evaluation advances after acquisition.
    """

    cutoff_at: datetime
    clock: Callable[[], datetime] | None = None
    evaluated_at: datetime = field(init=False)

    def __post_init__(self) -> None:
        self.cutoff_at = self._aware(self.cutoff_at)
        self.evaluated_at = self.cutoff_at

    def complete(self) -> datetime:
        now = self._aware(self.clock()) if self.clock is not None else self.cutoff_at
        if now < self.evaluated_at:
            raise WrongSourceError("Scan acquisition clock moved backward.")
        self.evaluated_at = now
        return now

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise WrongSourceError("Scan acquisition requires an aware clock.")
        return value.astimezone(UTC)
