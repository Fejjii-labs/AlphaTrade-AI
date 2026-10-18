"""Approach alerts fire near a level, once per cooldown, and before a sweep."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.telegram_policy import NotificationEventType
from app.strategy_brain.sfp.approach import (
    approach_alert_summaries,
    approach_hits,
    approach_preparation,
)
from app.strategy_brain.sfp.contracts import sfp_research_v2_parameters
from app.strategy_brain.sfp.levels import derive_levels
from tests.test_sfp_detector import evidence, parameters, spec


def test_v2_defaults_are_a_new_version_and_v1_does_not_approach() -> None:
    current = sfp_research_v2_parameters()
    assert current.version == "sfp-research/v2"
    assert current.level_lookback == 72
    assert current.minimum_sweep_depth == Decimal("0.002")
    assert current.maximum_sweep_depth == Decimal("0.003")
    assert current.approach_proximity == Decimal("0.001")
    assert parameters().approach_proximity is None
    assert parameters().version == "sfp-research/v1"


def test_approach_fires_at_one_tenth_percent_once_and_before_a_sweep() -> None:
    bars, observations = evidence()
    evaluated = bars[-1].interval_end
    levels = derive_levels(bars, observations, parameters(), evaluated_at=evaluated)
    level = next(item for item in levels if item.price == max(item.price for item in levels))
    near = level.price * Decimal("1.0005")
    cooldowns: dict = {}
    first = approach_hits(
        price=near,
        levels=(level,),
        proximity=Decimal("0.001"),
        minimum_sweep_depth=Decimal("0.01"),
        now=evaluated,
        cooldowns=cooldowns,
        cooldown_seconds=900,
    )
    assert first == (level,)
    again = approach_hits(
        price=near,
        levels=(level,),
        proximity=Decimal("0.001"),
        minimum_sweep_depth=Decimal("0.01"),
        now=evaluated,
        cooldowns=cooldowns,
        cooldown_seconds=900,
    )
    assert again == ()
    swept = approach_hits(
        price=level.price * Decimal("1.02"),
        levels=(level,),
        proximity=Decimal("0.001"),
        minimum_sweep_depth=Decimal("0.01"),
        now=evaluated,
        cooldowns={},
        cooldown_seconds=900,
    )
    assert swept == ()
    far = approach_hits(
        price=level.price * Decimal("1.05"),
        levels=(level,),
        proximity=Decimal("0.001"),
        minimum_sweep_depth=Decimal("0.01"),
        now=evaluated,
        cooldowns={},
        cooldown_seconds=900,
    )
    assert far == ()
    authored = spec(approach_proximity="0.001", minimum_sweep_depth="0.01")
    summaries = approach_alert_summaries(
        organization_id=uuid4(),
        strategy_version_id=uuid4(),
        spec=authored,
        bars=bars,
        observations=observations,
        price=near,
        venue="binance",
        now=evaluated,
        cooldowns={},
    )
    assert summaries
    assert summaries[0].event_type is NotificationEventType.SFP_LEVEL_APPROACH
    assert summaries[0].reclaim_state == "not_swept"
    assert "has not swept" in summaries[0].text()
    assert summaries[0].notification_event().phase is None
    prepared = approach_preparation(summaries[0])
    assert prepared["preparation_only"] is True
    assert prepared["execution_authorized"] is False
    silent = approach_alert_summaries(
        organization_id=uuid4(),
        strategy_version_id=uuid4(),
        spec=spec(),
        bars=bars,
        observations=observations,
        price=near,
        venue="binance",
        now=evaluated,
        cooldowns={},
    )
    assert silent == ()


def test_approach_cooldown_rejects_zero_and_enforces_sixty_seconds() -> None:
    from app.strategy_brain.sfp.contracts import SfpParameters

    current = sfp_research_v2_parameters()
    assert current.approach_cooldown_seconds == 900
    payload = current.model_dump(mode="python")
    with pytest.raises(ValidationError):
        SfpParameters.model_validate({**payload, "approach_cooldown_seconds": 0})
    with pytest.raises(ValidationError):
        SfpParameters.model_validate({**payload, "approach_cooldown_seconds": 59})
    accepted = SfpParameters.model_validate({**payload, "approach_cooldown_seconds": 60})
    assert accepted.approach_cooldown_seconds == 60
    assert (
        approach_hits(
            price=Decimal("100"),
            levels=(),
            proximity=Decimal("0.001"),
            minimum_sweep_depth=Decimal("0.002"),
            now=evidence()[0][-1].interval_end,
            cooldowns={},
            cooldown_seconds=0,
        )
        == ()
    )
