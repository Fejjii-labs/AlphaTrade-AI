"""Versioned notification contracts, independent of strategy and trading authority."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from app.schemas.common import StrictModel


class NotificationSeverity(StrEnum):
    INFO = "INFO"
    WATCH = "WATCH"
    ACTION = "ACTION"
    CRITICAL = "CRITICAL"


class NotificationEventType(StrEnum):
    SETUP = "SETUP"
    RISK = "RISK"
    PAPER_TRADE_OPENED = "PAPER_TRADE_OPENED"
    PAPER_TRADE_CLOSED = "PAPER_TRADE_CLOSED"
    STOP = "STOP"
    PARTIAL_PROFIT = "PARTIAL_PROFIT"
    DAILY_REVIEW = "DAILY_REVIEW"
    OTHER = "OTHER"
    SFP_SWEEP_DETECTED = "SFP_SWEEP_DETECTED"
    SFP_RECLAIM_FORMING = "SFP_RECLAIM_FORMING"
    SFP_CONFIRMED = "SFP_CONFIRMED"
    SFP_INVALIDATED = "SFP_INVALIDATED"
    SFP_EXPIRED = "SFP_EXPIRED"
    SFP_BLOCKED_BY_RISK = "SFP_BLOCKED_BY_RISK"
    SFP_LEVEL_APPROACH = "SFP_LEVEL_APPROACH"


class AlertPhase(StrEnum):
    FORMING = "FORMING"
    CONFIRMED = "CONFIRMED"


class PolicyQuietHours(StrictModel):
    start: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(default="UTC", max_length=64)

    @model_validator(mode="after")
    def validate_window(self) -> "PolicyQuietHours":
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown quiet-hours timezone.") from exc
        if self.start == self.end:
            raise ValueError("Quiet-hours start and end must differ.")
        return self

    def contains(self, now: datetime) -> bool:
        local = now.astimezone(ZoneInfo(self.timezone)).strftime("%H:%M")
        if self.start < self.end:
            return self.start <= local < self.end
        return local >= self.start or local < self.end


class TelegramNotificationPolicyV2(StrictModel):
    """None subscriptions allow all; empty subscriptions allow none. Full replacement."""

    schema_version: Literal[2] = 2
    strategy_subscriptions: tuple[UUID, ...] | None = None
    symbol_subscriptions: tuple[str, ...] | None = None
    setup_stages: tuple[str, ...] | None = None
    event_types: tuple[NotificationEventType, ...] | None = None
    severities: tuple[NotificationSeverity, ...] | None = None
    minimum_severity: NotificationSeverity = NotificationSeverity.INFO
    minimum_quality: Decimal | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    forming_alerts: bool = True
    confirmed_alerts: bool = True
    risk_alerts: bool = True
    paper_trade_opened: bool = True
    paper_trade_closed: bool = True
    stop_event: bool = True
    partial_profit_event: bool = True
    daily_review_event: bool = True
    cooldown_seconds: int = Field(default=0, ge=0, le=604800, strict=True)
    duplicate_suppression_seconds: int = Field(default=86400, ge=0, le=604800, strict=True)
    quiet_hours: PolicyQuietHours | None = None

    @model_validator(mode="after")
    def validate_identifiers(self) -> "TelegramNotificationPolicyV2":
        for values in (self.symbol_subscriptions, self.setup_stages):
            if values is not None and (
                len(values) > 200 or any(not value or len(value) > 80 for value in values)
            ):
                raise ValueError("Subscriptions must contain bounded, nonempty identifiers.")
        if self.strategy_subscriptions is not None and len(self.strategy_subscriptions) > 200:
            raise ValueError("Too many strategy subscriptions.")
        return self


class TelegramNotificationEvent(StrictModel):
    """Producer facts only, shared by all strategy notification projections."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    event_type: NotificationEventType
    severity: NotificationSeverity
    strategy_id: UUID | None = None
    symbol: str | None = Field(default=None, min_length=1, max_length=80)
    setup_stage: str | None = Field(default=None, min_length=1, max_length=80)
    phase: AlertPhase | None = None
    quality: Decimal | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    duplicate_key: str = Field(min_length=1, max_length=255)
    occurred_at: AwareDatetime
    mandatory_risk: bool = False

    @model_validator(mode="after")
    def validate_risk(self) -> "TelegramNotificationEvent":
        if self.mandatory_risk and self.event_type != NotificationEventType.RISK:
            raise ValueError("Only risk events can be mandatory.")
        return self


class TelegramFilterOption(StrictModel):
    """One named choice for a Telegram filter and the stored values it stands for."""

    label: str = Field(min_length=1, max_length=120)
    values: tuple[str, ...] = Field(min_length=1)


class TelegramPolicyOptions(StrictModel):
    """Named choices for the policy's subscription filters. Storage stays identifiers."""

    symbols: tuple[TelegramFilterOption, ...]
    strategies: tuple[TelegramFilterOption, ...]
    setup_stages: tuple[TelegramFilterOption, ...]
