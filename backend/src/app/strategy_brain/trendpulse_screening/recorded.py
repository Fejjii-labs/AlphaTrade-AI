"""Trusted offline replay of an original decision envelope. Not installed by HTTP."""

from datetime import datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from app.market_contracts.models import CanonicalModel
from app.schemas.trendpulse_screening import TrendPulseScreeningEvidence
from app.strategy_brain.trendpulse_1r.contracts import TrendPulseSpec, exact_hash
from app.strategy_brain.trendpulse_screening.acquisition import ScreeningAcquisitionError


class RecordedScreeningWindow(CanonicalModel):
    contract_version: Literal["trendpulse-recorded-window/v1"] = "trendpulse-recorded-window/v1"
    provenance: Literal["recorded_public_receipts", "synthetic_fixture"]
    spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    trigger_end: AwareDatetime
    decision_at: AwareDatetime
    evidence: TrendPulseScreeningEvidence

    @model_validator(mode="after")
    def consistent_provenance(self) -> Self:
        synthetic = self.provenance == "synthetic_fixture"
        if any(
            o.identity.provenance.is_mock != synthetic
            for o in self.evidence.trend_observations + self.evidence.entry_observations
        ):
            raise ValueError("Recorded receipt provenance contradicts original envelopes")
        return self


class RecordedScreeningAcquirer:
    """Preserve values, identities and every arrival time; detector enforces as-of.

    The offline driver replays the recorded decision clock explicitly. It cannot
    assert that unaudited historical OHLCV has genuine receipt-time provenance.
    """

    mode: Literal["replay"] = "replay"

    def __init__(self, window: RecordedScreeningWindow):
        self.window = window
        self.provenance = window.provenance

    @classmethod
    def from_file(cls, path: Path) -> "RecordedScreeningAcquirer":
        with path.open("rb") as stream:
            payload = stream.read(4 * 1024 * 1024 + 1)
        if len(payload) > 4 * 1024 * 1024:
            raise ScreeningAcquisitionError("recorded_window_size_bound")
        return cls(RecordedScreeningWindow.model_validate_json(payload))

    def acquire(self, spec: TrendPulseSpec, trigger_end: datetime) -> TrendPulseScreeningEvidence:
        if exact_hash(spec) != self.window.spec_hash or trigger_end != self.window.trigger_end:
            raise ScreeningAcquisitionError("recorded_window_binding_mismatch")
        return self.window.evidence
