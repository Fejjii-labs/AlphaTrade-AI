"""Configured BloFin demo leverage and margin mode (owner setting).

Pure validation plus one read of the owner's autonomous settings row. Nothing
here calls an exchange. The governed demo loop is the only caller that turns
this into a venue write, and only against the allowlisted demo host.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.risk_policy.ceilings import HARD_MAX_LEVERAGE

DEFAULT_DEMO_LEVERAGE = 10
DEFAULT_DEMO_MARGIN_MODE = "isolated"
# Hard ceiling, also the DB CHECK. Never above the risk-policy code ceiling.
DEMO_LEVERAGE_CEILING = min(20, int(HARD_MAX_LEVERAGE))
DEMO_MARGIN_MODES = frozenset({"isolated", "cross"})
MAX_DEMO_LEVERAGE_OVERRIDES = 50
_SYMBOL = re.compile(r"^[A-Z0-9]{2,24}USDT$")


class DemoLeverageValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DemoLeverageConfig:
    leverage: int
    margin_mode: str
    source: str  # "default", "account", or "symbol_override"

    @property
    def leverage_decimal(self) -> Decimal:
        return Decimal(self.leverage)


def validate_demo_leverage(value: object) -> int:
    if isinstance(value, bool):
        raise DemoLeverageValidationError("demo_leverage_invalid", "Leverage must be a number.")
    if isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value():
            raise DemoLeverageValidationError(
                "demo_leverage_not_whole", "Leverage must be a whole number."
            )
        value = int(value)
    if not isinstance(value, int):
        raise DemoLeverageValidationError("demo_leverage_invalid", "Leverage must be a number.")
    if value < 1:
        raise DemoLeverageValidationError(
            "demo_leverage_below_one", "Leverage must be at least 1x."
        )
    if value > DEMO_LEVERAGE_CEILING:
        raise DemoLeverageValidationError(
            "demo_leverage_exceeds_ceiling",
            f"Leverage cannot exceed {DEMO_LEVERAGE_CEILING}x (code ceiling).",
        )
    return value


def validate_demo_margin_mode(value: object) -> str:
    if not isinstance(value, str) or value.strip().lower() not in DEMO_MARGIN_MODES:
        raise DemoLeverageValidationError(
            "demo_margin_mode_invalid", "Margin mode must be isolated or cross."
        )
    return value.strip().lower()


def validate_demo_leverage_overrides(
    value: Mapping[str, Mapping[str, object]] | None,
) -> dict[str, dict[str, object]] | None:
    """Normalize the per-symbol map. Every entry needs a leverage or a mode."""

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise DemoLeverageValidationError(
            "demo_leverage_overrides_invalid", "Per-coin overrides must be an object."
        )
    if len(value) > MAX_DEMO_LEVERAGE_OVERRIDES:
        raise DemoLeverageValidationError(
            "demo_leverage_overrides_too_many",
            f"At most {MAX_DEMO_LEVERAGE_OVERRIDES} per-coin overrides.",
        )
    normalized: dict[str, dict[str, object]] = {}
    for raw_symbol, entry in value.items():
        symbol = raw_symbol.strip().upper() if isinstance(raw_symbol, str) else ""
        if not _SYMBOL.match(symbol) or symbol in normalized:
            raise DemoLeverageValidationError(
                "demo_leverage_override_symbol_invalid",
                "Per-coin overrides need a unique USDT symbol such as BTCUSDT.",
            )
        if not isinstance(entry, Mapping) or not set(entry) <= {"leverage", "margin_mode"}:
            raise DemoLeverageValidationError(
                "demo_leverage_overrides_invalid",
                "Each per-coin override may set only leverage and margin_mode.",
            )
        item: dict[str, object] = {}
        if entry.get("leverage") is not None:
            item["leverage"] = validate_demo_leverage(entry["leverage"])
        if entry.get("margin_mode") is not None:
            item["margin_mode"] = validate_demo_margin_mode(entry["margin_mode"])
        if not item:
            raise DemoLeverageValidationError(
                "demo_leverage_overrides_invalid",
                "Each per-coin override must set leverage or margin_mode.",
            )
        normalized[symbol] = item
    return normalized or None


def config_for_symbol(
    *,
    leverage: int | None,
    margin_mode: str | None,
    overrides: Mapping[str, Mapping[str, object]] | None,
    symbol: str,
) -> DemoLeverageConfig:
    """Resolve one symbol. Stored values are re-validated; bad data raises."""

    source = "default" if leverage is None and margin_mode is None else "account"
    resolved_leverage = (
        DEFAULT_DEMO_LEVERAGE if leverage is None else validate_demo_leverage(leverage)
    )
    resolved_mode = (
        DEFAULT_DEMO_MARGIN_MODE if margin_mode is None else validate_demo_margin_mode(margin_mode)
    )
    override = (validate_demo_leverage_overrides(overrides) or {}).get(symbol.strip().upper())
    if override:
        source = "symbol_override"
        resolved_leverage = validate_demo_leverage(override.get("leverage", resolved_leverage))
        resolved_mode = validate_demo_margin_mode(override.get("margin_mode", resolved_mode))
    return DemoLeverageConfig(leverage=resolved_leverage, margin_mode=resolved_mode, source=source)


def load_demo_leverage_config(
    session: Session, *, organization_id: uuid.UUID, user_id: uuid.UUID, symbol: str
) -> DemoLeverageConfig:
    from app.db.autonomous_trading_settings import AutonomousTradingSettingsRow

    row = session.scalar(
        select(AutonomousTradingSettingsRow).where(
            AutonomousTradingSettingsRow.organization_id == organization_id,
            AutonomousTradingSettingsRow.user_id == user_id,
        )
    )
    return config_for_symbol(
        leverage=None if row is None else row.demo_leverage,
        margin_mode=None if row is None else row.demo_margin_mode,
        overrides=None if row is None else row.demo_leverage_overrides,
        symbol=symbol,
    )


# Liquidation must sit at least this many stop distances from the entry.
LIQUIDATION_STOP_DISTANCE_MULTIPLE = Decimal("2")


def demo_liquidation_refusal(
    *,
    entry: Decimal,
    stop: Decimal,
    direction: str,
    leverage: Decimal,
    notional: Decimal,
    maintenance_tiers: tuple[tuple[Decimal, Decimal], ...],
) -> str | None:
    """Refuse unless the estimated liquidation is beyond the stop with a buffer.

    The estimate is the isolated maintenance formula at the configured
    leverage and the venue's maintenance tier for this notional. Cross margin
    uses the same estimate: free account equity only moves a cross
    liquidation further away, so this is conservative. Fail closed: missing
    tiers or an invalid estimate refuse.
    """

    from app.services.risk_policy.errors import RiskPolicyRejected
    from app.services.risk_policy.liquidation import (
        MaintenanceTier,
        isolated_liquidation_price,
        liquidation_crosses_stop,
        select_maintenance_tier,
    )

    stop_distance = abs(entry - stop)
    if stop_distance <= 0 or direction not in {"long", "short"}:
        return "demo_liquidation_estimate_invalid"
    try:
        tier = select_maintenance_tier(
            notional,
            tuple(
                MaintenanceTier(notional_floor=floor, maintenance_rate=rate)
                for floor, rate in maintenance_tiers
            ),
        )
        liquidation = isolated_liquidation_price(
            entry=entry,
            leverage=leverage,
            maintenance_rate=tier.maintenance_rate,
            direction=direction,
        )
    except RiskPolicyRejected as exc:
        if exc.code == "maintenance_tiers_unavailable":
            return "demo_maintenance_tiers_unavailable"
        return "demo_liquidation_estimate_invalid"
    if liquidation <= 0 or liquidation_crosses_stop(
        direction=direction, liquidation_price=liquidation, stop=stop
    ):
        return "demo_liquidation_inside_stop"
    if abs(entry - liquidation) < stop_distance * LIQUIDATION_STOP_DISTANCE_MULTIPLE:
        return "demo_liquidation_inside_stop_buffer"
    return None
