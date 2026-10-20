"""Persistence and validation for autonomous trading settings (issue #284)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.autonomous_trading_settings import AutonomousTradingSettingsRow
from app.db.models import UserStrategy
from app.repositories.watcher_watchlist import WatcherWatchlistRepository
from app.schemas.audit import AuditRecordCreate
from app.schemas.autonomous_trading_settings import (
    AutonomousStrategyOption,
    AutonomousTradingSettingsUpdate,
    AutonomousTradingSettingsView,
)
from app.schemas.common import ActorType, AuditEventType, AuditResult, AuditSeverity
from app.services.audit_service import AuditService
from app.services.risk_policy.ceilings import (
    HARD_MAX_DAILY_LOSS_PERCENT,
    PAPER_DEMO_MAX_RISK_PER_TRADE_PERCENT,
)


@dataclass(frozen=True)
class AutonomousTradingDefaults:
    daily_loss_limit_percent: Decimal = Decimal("3")
    max_trades_per_day: int = 5


SYSTEM_AUTONOMOUS_DEFAULTS = AutonomousTradingDefaults()


class AutonomousSettingsValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_daily_loss_limit_percent(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise AutonomousSettingsValidationError(
            "daily_loss_limit_invalid",
            "Daily loss limit must be a number.",
        )
    if value <= 0:
        raise AutonomousSettingsValidationError(
            "daily_loss_limit_non_positive",
            "Daily loss limit must be greater than 0%.",
        )
    if value > HARD_MAX_DAILY_LOSS_PERCENT:
        raise AutonomousSettingsValidationError(
            "daily_loss_limit_exceeds_ceiling",
            f"Daily loss limit cannot exceed {HARD_MAX_DAILY_LOSS_PERCENT}% (code ceiling).",
        )
    return value


def validate_max_trades_per_day(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AutonomousSettingsValidationError(
            "max_trades_per_day_invalid",
            "Max trades per day must be a whole number.",
        )
    if value < 1:
        raise AutonomousSettingsValidationError(
            "max_trades_per_day_non_positive",
            "Max trades per day must be at least 1.",
        )
    if value > 1000:
        raise AutonomousSettingsValidationError(
            "max_trades_per_day_too_large",
            "Max trades per day cannot exceed 1000.",
        )
    return value


def validate_risk_per_trade_percent(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise AutonomousSettingsValidationError(
            "risk_per_trade_invalid",
            "Risk per trade must be a number when provided.",
        )
    if value <= 0:
        raise AutonomousSettingsValidationError(
            "risk_per_trade_non_positive",
            "Risk per trade must be greater than 0% when provided.",
        )
    if value > PAPER_DEMO_MAX_RISK_PER_TRADE_PERCENT:
        raise AutonomousSettingsValidationError(
            "risk_per_trade_exceeds_ceiling",
            f"Risk per trade cannot exceed {PAPER_DEMO_MAX_RISK_PER_TRADE_PERCENT}% "
            "(paper/demo code ceiling).",
        )
    return value


@dataclass(frozen=True)
class ResolvedAutonomousSettings:
    daily_loss_limit_percent: Decimal
    max_trades_per_day: int
    risk_per_trade_percent: Decimal | None
    strategy_id: uuid.UUID | None
    symbol: str | None
    using_defaults: bool


class AutonomousTradingSettingsService:
    def __init__(self, session: Session, audit_service: AuditService) -> None:
        self._session = session
        self._audit = audit_service

    def get(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> AutonomousTradingSettingsView:
        row = self._load_row(organization_id=organization_id, user_id=user_id)
        options = self._options(organization_id=organization_id, user_id=user_id)
        if row is None:
            defaults = SYSTEM_AUTONOMOUS_DEFAULTS
            return AutonomousTradingSettingsView(
                organization_id=organization_id,
                user_id=user_id,
                daily_loss_limit_percent=defaults.daily_loss_limit_percent,
                max_trades_per_day=defaults.max_trades_per_day,
                risk_per_trade_percent=None,
                strategy_id=None,
                symbol=None,
                using_defaults=True,
                **options,
            )
        return AutonomousTradingSettingsView(
            organization_id=organization_id,
            user_id=user_id,
            daily_loss_limit_percent=row.daily_loss_limit_percent,
            max_trades_per_day=row.max_trades_per_day,
            risk_per_trade_percent=row.risk_per_trade_percent,
            strategy_id=row.strategy_id,
            symbol=row.symbol,
            using_defaults=False,
            **options,
        )

    def resolve(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> ResolvedAutonomousSettings:
        view = self.get(organization_id=organization_id, user_id=user_id)
        return ResolvedAutonomousSettings(
            daily_loss_limit_percent=view.daily_loss_limit_percent,
            max_trades_per_day=view.max_trades_per_day,
            risk_per_trade_percent=view.risk_per_trade_percent,
            strategy_id=view.strategy_id,
            symbol=view.symbol.upper().strip() if view.symbol else None,
            using_defaults=view.using_defaults,
        )

    def update(
        self,
        payload: AutonomousTradingSettingsUpdate,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> AutonomousTradingSettingsView:
        current = self.resolve(organization_id=organization_id, user_id=user_id)
        daily = current.daily_loss_limit_percent
        max_trades = current.max_trades_per_day
        risk = current.risk_per_trade_percent
        strategy_id = current.strategy_id
        symbol = current.symbol

        changes: dict[str, str] = {}
        data = payload.model_dump(exclude_unset=True)
        if "daily_loss_limit_percent" in data and data["daily_loss_limit_percent"] is not None:
            daily = validate_daily_loss_limit_percent(data["daily_loss_limit_percent"])
            changes["daily_loss_limit_percent"] = format(daily, "f")
        if "max_trades_per_day" in data and data["max_trades_per_day"] is not None:
            max_trades = validate_max_trades_per_day(data["max_trades_per_day"])
            changes["max_trades_per_day"] = str(max_trades)
        if payload.clear_risk_per_trade_percent:
            risk = None
            changes["risk_per_trade_percent"] = "cleared"
        elif "risk_per_trade_percent" in data:
            risk = validate_risk_per_trade_percent(data["risk_per_trade_percent"])
            changes["risk_per_trade_percent"] = "null" if risk is None else format(risk, "f")
        if "strategy_id" in data:
            strategy_id = data["strategy_id"]
            if strategy_id is not None:
                self._assert_strategy(organization_id=organization_id, strategy_id=strategy_id)
            changes["strategy_id"] = str(strategy_id) if strategy_id else "null"
        if "symbol" in data:
            symbol = None if data["symbol"] is None else data["symbol"].strip().upper()
            if symbol:
                self._assert_symbol(organization_id=organization_id, symbol=symbol)
            changes["symbol"] = symbol or "null"

        row = self._load_row(organization_id=organization_id, user_id=user_id)
        if row is None:
            row = AutonomousTradingSettingsRow(
                organization_id=organization_id,
                user_id=user_id,
                daily_loss_limit_percent=daily,
                max_trades_per_day=max_trades,
                risk_per_trade_percent=risk,
                strategy_id=strategy_id,
                symbol=symbol,
            )
            self._session.add(row)
        else:
            row.daily_loss_limit_percent = daily
            row.max_trades_per_day = max_trades
            row.risk_per_trade_percent = risk
            row.strategy_id = strategy_id
            row.symbol = symbol

        self._session.flush()
        if changes:
            self._audit.record(
                AuditRecordCreate(
                    request_id=f"autonomous-settings-{organization_id}",
                    trace_id=str(uuid.uuid4()),
                    event_type=AuditEventType.AUTONOMOUS_TRADING_SETTINGS_UPDATED,
                    organization_id=organization_id,
                    user_id=user_id,
                    actor_type=ActorType.USER,
                    resource_type="autonomous_trading_settings",
                    resource_id=str(row.id),
                    result=AuditResult.SUCCESS,
                    severity=AuditSeverity.INFO,
                    metadata={"changes": changes},
                )
            )
        return self.get(organization_id=organization_id, user_id=user_id)

    def _load_row(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> AutonomousTradingSettingsRow | None:
        return self._session.scalar(
            select(AutonomousTradingSettingsRow).where(
                AutonomousTradingSettingsRow.organization_id == organization_id,
                AutonomousTradingSettingsRow.user_id == user_id,
            )
        )

    def _options(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> dict[str, object]:
        strategies = self._session.scalars(
            select(UserStrategy)
            .where(
                UserStrategy.organization_id == organization_id,
                UserStrategy.user_id == user_id,
                UserStrategy.enabled.is_(True),
            )
            .order_by(UserStrategy.name.asc(), UserStrategy.created_at.asc())
        ).all()
        watchlist = WatcherWatchlistRepository(self._session).load(organization_id)
        symbols = [s.strip().upper() for s in watchlist.enabled_symbols() if s.strip()]
        return {
            "strategy_options": [
                AutonomousStrategyOption(strategy_id=row.id, name=row.name) for row in strategies
            ],
            "symbol_options": symbols,
            "max_daily_loss_percent_ceiling": HARD_MAX_DAILY_LOSS_PERCENT,
            "max_risk_per_trade_percent_ceiling": PAPER_DEMO_MAX_RISK_PER_TRADE_PERCENT,
        }

    def _assert_strategy(self, *, organization_id: uuid.UUID, strategy_id: uuid.UUID) -> None:
        row = self._session.scalar(
            select(UserStrategy).where(
                UserStrategy.id == strategy_id,
                UserStrategy.organization_id == organization_id,
                UserStrategy.enabled.is_(True),
            )
        )
        if row is None:
            raise AutonomousSettingsValidationError(
                "strategy_not_available",
                "Selected strategy is not enabled for this organization.",
            )

    def _assert_symbol(self, *, organization_id: uuid.UUID, symbol: str) -> None:
        watchlist = WatcherWatchlistRepository(self._session).load(organization_id)
        enabled = {s.strip().upper() for s in watchlist.enabled_symbols()}
        if symbol not in enabled:
            raise AutonomousSettingsValidationError(
                "symbol_not_watched",
                "Selected coin must be on the market monitoring watchlist.",
            )
