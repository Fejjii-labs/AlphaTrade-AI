"""Transparent confidence scoring.

The score is a weighted average of independent factors, each normalized to
``[0, 1]``. Every factor's weight, score, and contribution are returned so the
final number is fully explainable. A blocking no-trade filter forces the score
to zero.
"""

from __future__ import annotations

from app.analysis.types import (
    ConfidenceFactor,
    ConfidenceScore,
    Indicators,
    MarketStructure,
    SetupDetection,
)

_WEIGHTS = {
    "setup_strength": 0.35,
    "trend_alignment": 0.25,
    "momentum": 0.20,
    "volume": 0.20,
}


def dominant_direction(detections: list[SetupDetection]) -> str | None:
    """Return the majority direction among fired detections, if any."""
    longs = sum(1 for d in detections if d.detected and d.direction == "long")
    shorts = sum(1 for d in detections if d.detected and d.direction == "short")
    if longs == 0 and shorts == 0:
        return None
    if longs == shorts:
        return None
    return "long" if longs > shorts else "short"


def compute_confidence(
    detections: list[SetupDetection],
    indicators: Indicators,
    structure: MarketStructure,
    *,
    no_trade_blocked: bool,
) -> ConfidenceScore:
    """Compute a 0..100 confidence with a per-factor breakdown."""
    direction = dominant_direction(detections)
    fired = [d for d in detections if d.detected and d.direction == direction]

    setup_strength = min(len(fired) / 3.0, 1.0) if direction else 0.0
    trend_alignment = _trend_alignment(direction, structure)
    momentum = _momentum(direction, indicators)
    volume = _volume(indicators)

    raw = {
        "setup_strength": setup_strength,
        "trend_alignment": trend_alignment,
        "momentum": momentum,
        "volume": volume,
    }

    factors = tuple(
        ConfidenceFactor(
            name=name,
            weight=_WEIGHTS[name],
            score=score,
            contribution=_WEIGHTS[name] * score,
        )
        for name, score in raw.items()
    )

    if no_trade_blocked or direction is None:
        return ConfidenceScore(score=0.0, factors=factors)

    total_weight = sum(_WEIGHTS.values())
    score = sum(f.contribution for f in factors) / total_weight * 100.0
    return ConfidenceScore(score=round(score, 2), factors=factors)


def trend_alignment_score(direction: str | None, structure: MarketStructure) -> float:
    """Public trend score in ``[0, 1]`` used by the grade policy."""

    return _trend_alignment(direction, structure)


def momentum_score(direction: str | None, indicators: Indicators) -> float:
    """Public momentum score in ``[0, 1]`` used by the grade policy."""

    return _momentum(direction, indicators)


def _trend_alignment(direction: str | None, structure: MarketStructure) -> float:
    if direction is None:
        return 0.0
    if direction == "long" and structure.trend == "uptrend":
        return 1.0
    if direction == "short" and structure.trend == "downtrend":
        return 1.0
    if structure.trend == "range":
        return 0.5
    return 0.0


def _momentum(direction: str | None, indicators: Indicators) -> float:
    if direction is None or indicators.rsi is None:
        return 0.0
    rsi = indicators.rsi
    hist = indicators.macd_hist
    score = 0.0
    if direction == "long":
        score += 0.5 if rsi >= 50 else 0.0
        score += 0.5 if hist is not None and hist > 0 else 0.0
    else:
        score += 0.5 if rsi <= 50 else 0.0
        score += 0.5 if hist is not None and hist < 0 else 0.0
    return score


def _volume(indicators: Indicators) -> float:
    if indicators.volume_ratio is None:
        return 0.0
    return min(indicators.volume_ratio / 1.5, 1.0)
