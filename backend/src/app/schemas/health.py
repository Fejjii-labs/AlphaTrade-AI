"""Schemas for health and provider-status endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.providers.base import ProviderStatus

WorkerHealthState = Literal["RUNNING", "STALE", "UNAVAILABLE"]


class WorkerComponentObservation(BaseModel):
    """One Watcher or Telegram process row.

    ``available`` is true only when ``health_state`` is ``RUNNING``. A stored
    row with a missing, future, or stale heartbeat is not running.
    """

    model_config = ConfigDict(extra="forbid")

    available: bool = False
    worker_id: str = ""
    heartbeat_at: datetime | None = None
    activation_state: str = "unknown"
    lease_owner: str = ""
    lease_epoch: int = 0
    lease_expires_at: datetime | None = None
    fence_held: bool = False
    last_scan_at: datetime | None = None
    last_scan_reason: str = ""
    market_source: str = ""
    freshness_seconds: float | None = None
    telegram_runtime_state: str = "absent"
    inbound_mode: str = "off"
    outbox_pending: int = 0
    outbox_retryable: int = 0
    outbox_dead_letter: int = 0
    last_delivery_at: datetime | None = None
    last_error_code: str = ""
    kill_switch_active: bool = False
    request_count: int = 0
    request_weight: int = 0
    rate_limited_count: int = 0
    cache_hits: int = 0
    process_rss_bytes: int | None = None
    process_rss_peak_bytes: int | None = None
    health_state: WorkerHealthState = "UNAVAILABLE"
    heartbeat_age_seconds: float | None = None
    heartbeat_stale_after_seconds: int = 90


class ArmingRefusalObservation(BaseModel):
    """Boot refusal of the exit monitor or an autonomous loop.

    ``refused`` is true only when the worker recorded an arming-gate reason
    such as ``consecutive_loss_cooldown_invalid`` or
    ``cycle_interval_exceeds_timeframe``. ``source`` names which loop refused.
    """

    model_config = ConfigDict(extra="forbid")

    refused: bool = False
    reason: str = ""
    source: str = ""


class WorkerRuntimeObservation(BaseModel):
    """Observed worker rows.

    ``available`` is false when the status read fails. It does not mean either
    worker is fresh. Each component's ``health_state`` is the liveness claim.
    These fields are not the API process configuration. ``exit_monitor`` is
    the exit-monitor thread, not the watcher heartbeat.
    """

    model_config = ConfigDict(extra="forbid")

    available: bool = False
    watcher: WorkerComponentObservation = Field(default_factory=WorkerComponentObservation)
    telegram: WorkerComponentObservation = Field(default_factory=WorkerComponentObservation)
    paper_worker: WorkerComponentObservation = Field(default_factory=WorkerComponentObservation)
    exit_monitor: WorkerComponentObservation = Field(default_factory=WorkerComponentObservation)


class PaperWorkerKillSwitch(BaseModel):
    """Kill-switch posture for the external paper-worker poll.

    ``active`` is true when the global setting is on, when the scoped
    organization switch is on, or when that state cannot be read.
    ``source`` is a fixed token such as ``manual`` or ``global_setting``.
    ``since`` is the organization row's activation time when one exists.
    The free-text reason is not included. No secret and no P&L amount.
    """

    model_config = ConfigDict(extra="forbid")

    active: bool = False
    source: str = ""
    since: datetime | None = None


class WorkerEffectiveObservation(BaseModel):
    """Effective flags published by the worker, with heartbeat freshness.

    ``source`` is ``worker`` only when a row was read. ``stale`` is true unless
    ``health_state`` is ``RUNNING``. These booleans are not the API process env.
    ``real_trading_enabled`` is fixed false: this record cannot arm real money.
    """

    model_config = ConfigDict(extra="forbid")

    source: Literal["worker", "unavailable"] = "unavailable"
    health_state: WorkerHealthState = "UNAVAILABLE"
    stale: bool = True
    heartbeat_at: datetime | None = None
    heartbeat_age_seconds: float | None = None
    heartbeat_stale_after_seconds: int = 90
    last_cycle_at: datetime | None = None
    autonomy_mode: str = "off"
    autonomous_internal_paper_env_ceiling: bool = False
    autonomous_internal_paper_db_enabled: bool = False
    autonomous_internal_paper_effective: bool = False
    watcher_orchestration_enabled: bool = False
    watcher_paper_staging_activation: bool = False
    telegram_paper_activation_armed: bool = False
    telegram_interaction_enabled: bool = False
    telegram_alerts_enabled: bool = False
    automatic_telegram_delivery_enabled: bool = False
    watchlist: list[str] = Field(default_factory=list)
    real_trading_enabled: Literal[False] = False


class PaperWorkerHeartbeatResponse(BaseModel):
    """External liveness of the paper worker.

    ``status`` is ``fresh``, ``stale``, or ``missing``. ``alert_raised`` is
    true only when this request successfully delivered a new Telegram alert.
    A repeated poll inside the cooldown reports the same status and does not
    send again. Use structured logs and these fields for health-poller
    monitoring of conditions such as ``degraded_repeat``.
    ``kill_switch`` is the switch posture. It is not a P&L figure.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["fresh", "stale", "missing"]
    observation: WorkerComponentObservation
    alert_raised: bool = False
    arming_refusal: ArmingRefusalObservation = Field(
        default_factory=lambda: ArmingRefusalObservation()
    )
    arming_alert_raised: bool = False
    exit_monitor_status: Literal["fresh", "stale", "missing", "disabled"] = "disabled"
    exit_monitor: WorkerComponentObservation = Field(default_factory=WorkerComponentObservation)
    exit_monitor_alert_raised: bool = False
    kill_switch: PaperWorkerKillSwitch = Field(default_factory=PaperWorkerKillSwitch)
    worker_effective: WorkerEffectiveObservation = Field(default_factory=WorkerEffectiveObservation)
    timestamp: datetime


class HealthResponse(BaseModel):
    """Basic liveness payload plus trading-safety posture."""

    model_config = ConfigDict(extra="forbid")

    status: str = "ok"
    app: str
    version: str
    environment: str
    execution_mode: str
    real_trading_enabled: bool
    exchange_mode: str
    must_verify_email: bool
    demo_seed_enabled: bool = False
    market_watcher_enabled: bool = False
    market_watcher_bridge_enabled: bool = False
    watcher_orchestration_enabled: bool = False
    watcher_paper_staging_activation: bool = False
    telegram_alerts_enabled: bool = False
    telegram_interaction_enabled: bool = False
    automatic_telegram_delivery_enabled: bool = False
    telegram_paper_activation_armed: bool = False
    telegram_inbound_mode: str = "off"
    telegram_network_permitted: bool = False
    blofin_readonly_sync_enabled: bool = False
    blofin_live_evidence_demo_enabled: bool = False
    blofin_readonly_sync_credentials_configured: bool = False
    blofin_readonly_sync_origin_configured: bool = False
    perpetual_evidence_source: Literal["replay", "binance_usdm", "bybit_usdt_perpetual"]
    perpetual_evidence_secondary_source: Literal["none", "bybit_usdt_perpetual"] = "none"
    perpetual_evidence_activation: Literal["inactive", "active", "refused"]
    perpetual_evidence_intended_staging_source: Literal["binance_usdm"] = "binance_usdm"
    perpetual_evidence_rollback_source: Literal["replay"] = "replay"
    live_market_read_only: Literal[True] = True
    exchange_credentials_used_for_market_evidence: Literal[False] = False
    spot_fallback_permitted: Literal[False] = False
    fabricated_fallback_permitted: Literal[False] = False
    live_quote_freshness_seconds: Literal[10] = 10
    first_perpetual_symbol: Literal["BTCUSDT"] = "BTCUSDT"
    git_sha: str | None = None
    worker_runtime: WorkerRuntimeObservation = Field(default_factory=WorkerRuntimeObservation)
    worker_effective: WorkerEffectiveObservation = Field(default_factory=WorkerEffectiveObservation)
    worker_flags_source: Literal["worker", "api_process"] = "api_process"
    worker_flags_stale: bool = False
    arming_refusal: ArmingRefusalObservation = Field(
        default_factory=lambda: ArmingRefusalObservation()
    )
    timestamp: datetime


class ReadinessResponse(BaseModel):
    """Readiness payload: ``ready`` only when no provider is unavailable.

    Staging smoke, live smoke, docker validate, and the demo and Telegram
    checks read ``ready`` and sometimes ``providers_unavailable``. They do not
    read ``status``. Render's health check is ``/health``, so a degraded
    market-data provider must not set ``ready`` false. ``degraded_providers``
    names those providers. The checks print the list and still pass.
    """

    model_config = ConfigDict(extra="forbid")

    status: str
    ready: bool
    providers_total: int
    providers_unavailable: int
    degraded_providers: list[str] = Field(default_factory=list)
    timestamp: datetime


class ProviderStatusResponse(BaseModel):
    """Aggregated provider statuses for operators and the frontend."""

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    providers: list[ProviderStatus] = Field(default_factory=list)
