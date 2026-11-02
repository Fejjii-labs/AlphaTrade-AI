"""HTTP schemas for the paper-only Watcher runtime status endpoint."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

WATCHER_PAPER_ACTIVATION_REQUIREMENTS: tuple[str, ...] = (
    "Default disarmed: WATCHER_PAPER_STAGING_ACTIVATION is false",
    "Local paper monitoring stays ENVIRONMENT=local with WATCHER_ORCHESTRATION_ENABLED",
    "Production rejects Watcher orchestration and the staging activation arm",
    "API process does not autostart staging; dedicated worker only, after preflight",
    "Live canonical evidence is required; replay while live is required cannot start",
    "Provider must be available; stale or degraded evidence fails closed",
    "Approved compiled strategy lineage only; empty or invalid lineage cannot start",
    "Alembic revision must match the single head",
    "PostgreSQL leases, unique worker identity, fencing, restart recovery, idempotency",
    "Candidate authority is CONFIRMED_SETUP only; risk BLOCK is final",
    "Kill switch stays in force; paper execution only; Telegram stays disabled",
    "ENABLE_REAL_TRADING stays false; runtime health gate stops the worker without deploying",
)


class WatcherPaperScopeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scan_scope: str
    symbol: str
    health_state: str
    lease_owner: str | None = None
    last_reason_code: str
    candidate_ids: list[str] = Field(default_factory=list)
    # Set on opt-in watchlist projections: the authored market whose parameters
    # are reused, e.g. "BTCUSDT 15m". Observation only.
    projection_source: str | None = None


class WatcherPaperRuntimeStatus(BaseModel):
    """Operator/tenant visibility for paper Watcher monitoring. No secrets."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    running: bool
    worker_id: str | None = None
    symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT"])
    poll_interval_seconds: float
    max_scopes_per_cycle: int
    paper_only: bool = True
    real_trading_enabled: bool = False
    telegram_enabled: bool = False
    kill_switch_active: bool = False
    last_cycle_at: datetime | None = None
    last_reason_code: str = "idle"
    cycles_completed: int = 0
    scans_succeeded: int = 0
    scans_failed: int = 0
    scans_skipped: int = 0
    scans_blocked: int = 0
    candidates_created: int = 0
    scopes: list[WatcherPaperScopeStatus] = Field(default_factory=list)
    remaining_activation_requirements: list[str] = Field(
        default_factory=lambda: list(WATCHER_PAPER_ACTIVATION_REQUIREMENTS)
    )
