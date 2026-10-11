"""Monitoring-only bindings to immutable saved strategy versions."""

import json
from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.common import StrictModel

MAX_MONITORING_ASSIGNMENTS = 25


class WatcherAssignmentWrite(StrictModel):
    id: UUID
    symbol: str = Field(min_length=6, max_length=20, pattern=r"^[A-Z0-9]+USDT$")
    strategy_version_id: UUID
    monitoring_enabled: bool
    timeframes: list[str] = Field(min_length=1, max_length=5)
    parameters: dict[str, Any]

    @model_validator(mode="after")
    def bounded_snapshot(self) -> Self:
        if len(set(self.timeframes)) != len(self.timeframes):
            raise ValueError("Timeframes must be unique.")
        encoded = json.dumps(self.parameters, sort_keys=True, allow_nan=False)
        if len(encoded) > 20000:
            raise ValueError("Strategy parameter snapshot is too large.")
        return self


class WatcherAssignmentReplace(StrictModel):
    revision: int = Field(ge=0)
    request_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    assignments: list[WatcherAssignmentWrite] = Field(max_length=MAX_MONITORING_ASSIGNMENTS)

    @model_validator(mode="after")
    def distinct_bindings(self) -> Self:
        if len({a.id for a in self.assignments}) != len(self.assignments):
            raise ValueError("Assignment identities must be unique.")
        if len({(a.symbol, a.strategy_version_id) for a in self.assignments}) != len(
            self.assignments
        ):
            raise ValueError("A saved version can be assigned to a coin only once.")
        return self


class WatcherAssignmentObservation(StrictModel):
    assignment_id: UUID
    configuration_revision: int = Field(ge=0)
    observed_at: datetime
    status: str
    reason_code: str
    replayed: bool = False
    candidate_count: int = Field(default=0, ge=0)


class WatcherAssignmentRead(WatcherAssignmentWrite):
    strategy_id: UUID
    strategy_name: str
    version: int = Field(ge=1)
    version_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    eligible: bool
    reason_code: str
    latest: WatcherAssignmentObservation | None = None


class WatcherAssignmentVersion(StrictModel):
    strategy_id: UUID
    strategy_version_id: UUID
    strategy_name: str
    version: int = Field(ge=1)
    version_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    symbol: str | None
    timeframes: list[str]
    parameters: dict[str, Any]


class WatcherAssignmentsRead(StrictModel):
    revision: int = Field(ge=0)
    configured: bool
    assignments: list[WatcherAssignmentRead]
    available_versions: list[WatcherAssignmentVersion]
    symbols: list[str]
    max_assignments: Literal[25] = 25
    monitoring_only: Literal[True] = True
