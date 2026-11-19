"""Trading analytics API schemas (Slice 31)."""

from __future__ import annotations

from datetime import date

from pydantic import Field

from app.schemas.common import StrictModel


class AnalyticsDateRange(StrictModel):
    start: date | None = None
    end: date | None = None


class DisciplineScoreResult(StrictModel):
    score: int = Field(ge=0, le=100)
    grade: str
    positive_behaviors: list[str] = Field(default_factory=list)
    negative_behaviors: list[str] = Field(default_factory=list)
    improvement_suggestions: list[str] = Field(default_factory=list)
