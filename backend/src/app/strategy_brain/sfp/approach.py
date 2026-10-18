"""Early SFP level approach. Information only; execution stays unauthorized.

The watcher already holds a quote. This module does not fetch a price.
A missing quote or a spec with ``approach_proximity is None`` produces no alert.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid5

from app.candidate_alerts.sfp import SfpAlertSummary
from app.market_contracts.observation import PublicMarketObservation
from app.market_contracts.ohlcv import OhlcvBar
from app.schemas.telegram_policy import NotificationEventType
from app.strategy_brain.sfp.contracts import SFP_NAMESPACE, SfpSpec, StructuralLevel
from app.strategy_brain.sfp.levels import derive_levels


def approach_hits(
    *,
    price: Decimal,
    levels: tuple[StructuralLevel, ...],
    proximity: Decimal,
    minimum_sweep_depth: Decimal,
    now: datetime,
    cooldowns: dict[UUID, datetime],
    cooldown_seconds: int,
) -> tuple[StructuralLevel, ...]:
    """Levels whose live price is within proximity and has not swept them.

    Updates ``cooldowns`` for each returned level so a second call inside the
    window does not refire. A price that has already passed the sweep depth
    is not an approach.
    """

    if price <= 0 or proximity <= 0 or now.tzinfo is None:
        return ()
    found: list[StructuralLevel] = []
    window = timedelta(seconds=cooldown_seconds)
    for level in levels:
        if level.price <= 0:
            continue
        distance = abs(price - level.price) / level.price
        if distance > proximity or _already_swept(level, price, minimum_sweep_depth):
            continue
        previous = cooldowns.get(level.level_id)
        if previous is not None and now < previous + window:
            continue
        cooldowns[level.level_id] = now
        found.append(level)
    return tuple(found)


def approach_alert_summaries(
    *,
    organization_id: UUID,
    strategy_version_id: UUID,
    spec: SfpSpec,
    bars: tuple[OhlcvBar, ...],
    observations: tuple[PublicMarketObservation, ...],
    price: Decimal | None,
    venue: str,
    now: datetime,
    cooldowns: dict[UUID, datetime],
) -> tuple[SfpAlertSummary, ...]:
    """Build approach alerts from bars and a price the caller already has."""

    proximity = spec.parameters.approach_proximity
    if proximity is None or price is None:
        return ()
    levels = derive_levels(bars, observations, spec.parameters, evaluated_at=now)
    hits = approach_hits(
        price=price,
        levels=levels,
        proximity=proximity,
        minimum_sweep_depth=spec.parameters.minimum_sweep_depth,
        now=now,
        cooldowns=cooldowns,
        cooldown_seconds=spec.parameters.approach_cooldown_seconds,
    )
    bucket = (
        str(int(now.timestamp()) // spec.parameters.approach_cooldown_seconds)
        if spec.parameters.approach_cooldown_seconds
        else now.isoformat()
    )
    summaries: list[SfpAlertSummary] = []
    for level in hits:
        summaries.append(
            SfpAlertSummary(
                organization_id=organization_id,
                strategy_version_id=strategy_version_id,
                setup_id=uuid5(
                    SFP_NAMESPACE,
                    f"approach:{strategy_version_id}:{level.level_id}",
                ),
                event_type=NotificationEventType.SFP_LEVEL_APPROACH,
                occurred_at=now,
                evidence_at=now,
                symbol=spec.symbol,
                venue=venue,
                timeframe=spec.trigger_timeframe.value,
                direction=spec.direction.value,
                level_type=level.kind.value,
                structural_level=format(level.price, "f"),
                sweep_extreme=format(price, "f"),
                reclaim_state="not_swept",
                quality_coverage=(),
                missing_evidence=("sweep:not_yet",),
                risk_state="not_evaluated",
                duplicate_salt=bucket,
            )
        )
    return tuple(summaries)


def approach_preparation(summary: SfpAlertSummary) -> dict[str, object]:
    """Autonomous paths may prepare. They may not execute an SFP plan."""

    if summary.event_type is not NotificationEventType.SFP_LEVEL_APPROACH:
        raise ValueError("not an approach alert")
    return {
        "event_type": summary.event_type.value,
        "preparation_only": True,
        "execution_authorized": False,
        "reason": "sfp_execution_plan_not_authorized",
        "symbol": summary.symbol,
        "level_type": summary.level_type,
        "structural_level": summary.structural_level,
        "price": summary.sweep_extreme,
    }


def _already_swept(level: StructuralLevel, price: Decimal, minimum_sweep_depth: Decimal) -> bool:
    if level.kind.support:
        return price <= level.price * (Decimal("1") - minimum_sweep_depth)
    return price >= level.price * (Decimal("1") + minimum_sweep_depth)
