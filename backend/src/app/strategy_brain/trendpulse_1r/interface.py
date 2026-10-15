"""Published pure adapter interface bundle. It is not a wire endpoint."""

from app.market_contracts.models import CanonicalModel
from app.strategy_brain.trendpulse_1r.contracts import TrendPulseRequest, TrendPulseResult
from app.strategy_brain.trendpulse_1r.experiment import ExperimentBoundTrendPulse


class TrendPulseInterface(CanonicalModel):
    request: TrendPulseRequest
    result: TrendPulseResult
    experiment_binding: ExperimentBoundTrendPulse | None = None
