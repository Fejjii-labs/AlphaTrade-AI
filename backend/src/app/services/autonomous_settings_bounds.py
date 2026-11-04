"""Bounds for the Autonomous Trading settings page, from what the server enforces.

Nothing here is a new limit. Each value is read from the constant, request
schema, or setting that already validates the write, so the page cannot drift
from the server.
"""

from __future__ import annotations

from decimal import Decimal

import annotated_types
from pydantic import BaseModel

from app.core.config import Settings
from app.schemas.autonomous_settings_bounds import (
    AutonomousSettingsBoundsView,
    BoundSource,
    LeverageBounds,
    NumericBound,
    ProfileCapBounds,
    StopLossBandBounds,
)
from app.schemas.autonomous_trading_settings import AutonomousTradingSettingsUpdate
from app.schemas.risk import UserRiskSettingsUpdate
from app.services.risk_policy.ceilings import (
    HARD_MAX_AGGREGATE_OPEN_RISK_PERCENT,
    HARD_MAX_DAILY_LOSS_PERCENT,
    HARD_MAX_LEVERAGE,
    PAPER_DEMO_MAX_RISK_PER_TRADE_PERCENT,
    notional_ceiling_for,
    risk_ceiling_for,
)
from app.services.risk_policy.profile import PolicyKind

# The profiles the page shows (frontend `PROFILE_KINDS`).
PAGE_PROFILE_KINDS = (PolicyKind.INTERNAL_PAPER, PolicyKind.DEMO)


def schema_bound(
    model: type[BaseModel],
    field: str,
    *,
    editable: bool,
    source: BoundSource = "request_schema",
) -> NumericBound:
    """Read gt/ge/le from a request field's pydantic constraints."""

    low: Decimal | None = None
    exclusive = False
    high: Decimal | None = None
    for item in model.model_fields[field].metadata:
        if isinstance(item, annotated_types.Gt):
            low, exclusive = Decimal(str(item.gt)), True
        elif isinstance(item, annotated_types.Ge):
            low, exclusive = Decimal(str(item.ge)), False
        elif isinstance(item, annotated_types.Le):
            high = Decimal(str(item.le))
    return NumericBound(
        min=low, min_exclusive=exclusive, max=high, editable=editable, source=source
    )


def stop_loss_band_bounds(settings: Settings) -> StopLossBandBounds:
    # Lazy: the demo loop module is heavy and only its constants are needed here.
    from app.services.governed_blofin_demo import (
        STOP_LOSS_BAND_CEILING_PERCENT,
        STOP_LOSS_BAND_FLOOR_PERCENT,
        stop_loss_band,
    )

    low, high = stop_loss_band(settings, Decimal("100"))
    return StopLossBandBounds(
        configured_min_percent=settings.governed_demo_stop_loss_min_percent,
        configured_max_percent=settings.governed_demo_stop_loss_max_percent,
        floor_percent=STOP_LOSS_BAND_FLOOR_PERCENT,
        ceiling_percent=STOP_LOSS_BAND_CEILING_PERCENT,
        configured_valid=low is not None and high is not None,
    )


def leverage_bounds(settings: Settings) -> LeverageBounds:
    from app.services.governed_blofin_demo import DEMO_LEVERAGE_HARD_CAP, demo_max_leverage

    return LeverageBounds(
        profile_max_leverage_ceiling=HARD_MAX_LEVERAGE,
        demo_hard_cap=DEMO_LEVERAGE_HARD_CAP,
        demo_effective_max=demo_max_leverage(settings),
    )


def profile_cap_bounds(kind: PolicyKind) -> ProfileCapBounds:
    return ProfileCapBounds(
        kind=kind.value,
        risk_per_trade_percent_ceiling=risk_ceiling_for(kind),
        notional_percent_ceiling=notional_ceiling_for(kind),
        aggregate_open_risk_percent_ceiling=HARD_MAX_AGGREGATE_OPEN_RISK_PERCENT,
        daily_loss_percent_ceiling=HARD_MAX_DAILY_LOSS_PERCENT,
        max_leverage_ceiling=HARD_MAX_LEVERAGE,
    )


def autonomous_settings_bounds(settings: Settings) -> AutonomousSettingsBoundsView:
    return AutonomousSettingsBoundsView(
        # The bot validators use the code ceilings, not the schema, for these two.
        bot_risk_per_trade_percent=NumericBound(
            min=Decimal("0"),
            min_exclusive=True,
            max=PAPER_DEMO_MAX_RISK_PER_TRADE_PERCENT,
            editable=True,
            source="code_ceiling",
        ),
        bot_daily_loss_limit_percent=NumericBound(
            min=Decimal("0"),
            min_exclusive=True,
            max=HARD_MAX_DAILY_LOSS_PERCENT,
            editable=True,
            source="code_ceiling",
        ),
        bot_max_trades_per_day=schema_bound(
            AutonomousTradingSettingsUpdate, "max_trades_per_day", editable=True
        ),
        account_max_risk_per_trade_percent=schema_bound(
            UserRiskSettingsUpdate, "max_risk_per_trade_percent", editable=True
        ),
        account_max_trades_per_day=schema_bound(
            UserRiskSettingsUpdate, "max_trades_per_day", editable=True
        ),
        stop_loss_band=stop_loss_band_bounds(settings),
        leverage=leverage_bounds(settings),
        profiles=[profile_cap_bounds(kind) for kind in PAGE_PROFILE_KINDS],
    )
