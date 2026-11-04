"""Per-user autonomous trading preferences (issue #284)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

_MONEY = Numeric(20, 8)


class AutonomousTradingSettingsRow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "autonomous_trading_settings"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_autonomous_trading_settings_org_user",
        ),
        CheckConstraint(
            "daily_loss_limit_percent > 0 AND daily_loss_limit_percent <= 6",
            name="ck_autonomous_trading_settings_daily_loss",
        ),
        CheckConstraint(
            "max_trades_per_day >= 1 AND max_trades_per_day <= 1000",
            name="ck_autonomous_trading_settings_max_trades",
        ),
        CheckConstraint(
            "(risk_per_trade_percent IS NULL) OR "
            "(risk_per_trade_percent > 0 AND risk_per_trade_percent <= 5)",
            name="ck_autonomous_trading_settings_risk_override",
        ),
        # BloFin demo only. NULL uses the code default (10x isolated). 20x is
        # the hard ceiling, also enforced by the service and the demo gate.
        CheckConstraint(
            "(demo_leverage IS NULL) OR (demo_leverage >= 1 AND demo_leverage <= 20)",
            name="ck_autonomous_trading_settings_demo_leverage",
        ),
        CheckConstraint(
            "(demo_margin_mode IS NULL) OR (demo_margin_mode IN ('isolated', 'cross'))",
            name="ck_autonomous_trading_settings_demo_margin_mode",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    daily_loss_limit_percent: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    max_trades_per_day: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_per_trade_percent: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("user_strategies.id"), nullable=True
    )
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    demo_leverage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    demo_margin_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # {"BTCUSDT": {"leverage": 5, "margin_mode": "isolated"}}; validated by the service.
    demo_leverage_overrides: Mapped[dict[str, dict[str, object]] | None] = mapped_column(
        JSON, nullable=True
    )
