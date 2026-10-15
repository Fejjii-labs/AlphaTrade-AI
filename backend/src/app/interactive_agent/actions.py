"""Public contracts for bounded Agent tools. Narrative text is never authority."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from app.schemas.agent_paper import AgentPaperTradeIntent
from app.schemas.backtest import BacktestRunCreate
from app.schemas.common import (
    PositiveDecimal,
    StrategyId,
    StrictModel,
    Symbol,
    Timeframe,
    TradeDirection,
)
from app.schemas.pretrade import PreTradeAnalyzeBody
from app.schemas.watcher_watchlist import WatcherWatchlistSlotWrite


class ActionRequest(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ActionDescriptor(StrictModel):
    name: str
    input_contract: dict[str, Any]
    authority: str
    behavior: Literal["read", "propose", "confirm"]
    required_permissions: list[str]
    explicit_confirmation_required: bool
    result_record_identity: str
    limitations: list[str]


class TextInput(StrictModel):
    text: str = Field(min_length=1, max_length=4000)


class JournalCreateInput(TextInput):
    symbol: Symbol | None = None
    timeframe: Timeframe | None = None
    direction: TradeDirection | None = None


class JournalNoteInput(TextInput):
    journal_entry_id: UUID | None = None


class StrategyInput(TextInput):
    setup_type: StrategyId | None = None
    strategy_id: UUID | None = None
    evidence_document_ids: list[UUID] = Field(default_factory=list, max_length=20)


class KnowledgeInput(StrategyInput):
    title: str = Field(default="Trading knowledge proposal", min_length=1, max_length=255)
    kind: Literal["lesson", "rule", "observation"] = "lesson"


class StrategyValidationInput(StrategyInput):
    backtest: BacktestRunCreate | None = None


class WatcherChangeInput(StrictModel):
    operation: Literal["enable", "disable", "replace", "reorder", "universe"]
    revision: int | None = Field(default=None, ge=0)
    position: int | None = Field(default=None, ge=1, le=5)
    symbol: Symbol | None = None
    positions: list[int] = Field(default_factory=list, max_length=5)
    slots: list[WatcherWatchlistSlotWrite] = Field(default_factory=list, max_length=5)


class PaperTradeInput(StrictModel):
    symbol: Symbol | None = None
    timeframe: Timeframe | None = None
    direction: TradeDirection | None = None
    entry: PositiveDecimal | None = None
    stop: PositiveDecimal | None = None
    targets: list[PositiveDecimal] = Field(default_factory=list, max_length=10)
    trade_proposal_id: UUID | None = None
    pretrade: PreTradeAnalyzeBody | None = None


class EmptyInput(StrictModel):
    pass


class LearningStatusInput(StrictModel):
    strategy_id: UUID | None = None
    proposal_id: UUID | None = None
    limit: int = Field(default=5, ge=1, le=10)


class RecordedTradeInput(StrictModel):
    market_name: str | None = Field(default=None, min_length=2, max_length=16)
    symbol: Symbol | None = None
    direction: TradeDirection | None = None
    account_id: UUID | None = None
    journal_trade_id: UUID | None = None
    latest: bool = False
    paper_only: bool = False
    execution_venue: Literal["PAPER_INTERNAL", "BLOFIN_DEMO", "BLOFIN_REAL", "BLOFIN"] | None = None
    trade_origin: Literal["manual_demo_test", "paper_execution", "manual"] | None = None
    command_id: UUID | None = None
    since: datetime | None = None
    until: datetime | None = None
    requested_quantity: PositiveDecimal | None = None
    submission_status: Literal["attempt", "blocked", "submitted", "uncertain", "filled"] = "attempt"
    unsupported_filters: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_times(self) -> RecordedTradeInput:
        for value in (self.since, self.until):
            if value is not None and value.utcoffset() is None:
                raise ValueError("Time filters require an explicit timezone.")
        if self.since and self.until and self.since > self.until:
            raise ValueError("Submission-time range is reversed.")
        return self


class PaperExecutionExplanationInput(StrictModel):
    command_id: UUID


class PaperExecutionInput(StrictModel):
    trade: AgentPaperTradeIntent


class DailyReviewInput(StrictModel):
    day: date | None = None
    relative_day: Literal["today", "yesterday"] = "today"
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    focus: Literal[
        "summary",
        "setups",
        "trades",
        "blocked",
        "performance",
        "mistakes",
        "lessons",
        "evidence",
        "tomorrow",
    ] = "summary"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Use an IANA timezone such as Europe/Berlin.") from exc
        return value
