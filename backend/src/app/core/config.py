"""Application settings loaded from environment variables.

Settings are the single source of truth for runtime configuration. They are
validated at startup so the service fails fast on misconfiguration rather than
silently running with unsafe defaults.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment."""

    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class ExecutionMode(StrEnum):
    """Trading execution mode.

    ``paper`` is the only valid trading mode. ``trade`` is a tombstone: selecting
    it refuses Settings construction in every environment. ``read_only`` may be
    constructed for a future read-only process but cannot be combined with an
    execution-capable composition root (API, worker, AgentRuntime, ExecutionService).
    """

    PAPER = "paper"
    READ_ONLY = "read_only"
    TRADE = "trade"


class TelegramInboundMode(StrEnum):
    """How paper Telegram receives updates. Off is the only default."""

    OFF = "off"
    POLLING = "polling"
    WEBHOOK = "webhook"


class ExchangeMode(StrEnum):
    """Exchange connectivity for paper flows.

    This axis is INDEPENDENT from ``execution_mode``/``enable_real_trading``.
    It controls only whether paper trading talks to an external demo exchange.

    * ``paper_internal`` (default): pure in-database simulation, no exchange calls.
    * ``paper_exchange_demo``: routes paper orders to an allowlisted BloFin *demo*
      account. Still not real money; requires ``execution_mode=paper`` and
      ``enable_real_trading=false`` (enforced in :mod:`app.core.exchange_safety`).
    * ``trade_live``: an explicit tombstone. Selecting it refuses startup. Real
      exchange execution is intentionally never implemented.
    """

    PAPER_INTERNAL = "paper_internal"
    PAPER_EXCHANGE_DEMO = "paper_exchange_demo"
    TRADE_LIVE = "trade_live"


class Settings(BaseSettings):
    """Validated application configuration.

    Values are read from environment variables (and an optional ``.env`` file),
    case-insensitively. Unknown variables are ignored so the same ``.env`` can be
    shared across services.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "AlphaTrade AI"
    environment: Environment = Environment.LOCAL
    debug: bool = True
    log_level: str = "INFO"
    log_json: bool = True

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: Annotated[
        list[str],
        NoDecode,
    ] = Field(default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"])
    request_id_header: str = "X-Request-ID"
    # Number of reverse proxies in front of the app whose appended
    # X-Forwarded-For entries are trusted (AT-018). 0 ignores the header
    # entirely, so a client can never choose its own IP identity. Render
    # deployments sit behind exactly one proxy and must set this to 1
    # (enforced in deployment_safety for staging/production).
    trusted_proxy_hops: int = Field(default=0, ge=0, le=10)

    # --- Trading safety ---
    execution_mode: ExecutionMode = ExecutionMode.PAPER
    enable_real_trading: bool = False
    # Ops emergency gate (AT-014). Independent of org DB kill switch; fail-closed
    # when true. No UI — set via GLOBAL_KILL_SWITCH_ACTIVE env for incident response.
    global_kill_switch_active: bool = False

    # --- Exchange connectivity (Slice 56A — demo only, disabled by default) ---
    # Independent from execution_mode; see ``ExchangeMode``. Defaults keep the
    # platform on pure internal simulation with no external exchange calls.
    exchange_mode: ExchangeMode = ExchangeMode.PAPER_INTERNAL
    blofin_demo_enabled: bool = False
    # Explicit staging API capability. The Watcher remains internal paper until
    # its canonical venue dispatch and fill reconciliation are implemented.
    blofin_live_evidence_demo_enabled: bool = False
    # Stored secrets. ``repr=False`` so Settings logs and exceptions do not
    # print them. Loading for an authenticated client goes through
    # ``app.core.execution_credentials``, not these fields.
    blofin_api_key: str = Field(default="", repr=False)
    blofin_api_secret: str = Field(default="", repr=False)
    blofin_api_passphrase: str = Field(default="", repr=False)
    # Dedicated read-only demo sync credentials never open the execution gate.
    blofin_readonly_sync_enabled: bool = False
    blofin_readonly_api_key: str = Field(default="", repr=False)
    blofin_readonly_api_secret: str = Field(default="", repr=False)
    blofin_readonly_api_passphrase: str = Field(default="", repr=False)
    blofin_demo_rest_base_url: str = ""
    blofin_demo_ws_url: str = ""
    blofin_request_timeout_seconds: float = Field(default=10.0, ge=1.0, le=30.0)
    blofin_max_retries: int = Field(default=2, ge=0, le=5)
    blofin_rate_limit_requests_per_second: int = Field(default=5, ge=1, le=50)

    # --- Background worker (Slice 59 — disabled by default) ---
    worker_enabled: bool = False
    worker_mode: str = "process"  # process | in_process
    worker_name: str = "alphatrade-worker"
    worker_scan_interval_seconds: int = Field(default=60, ge=5, le=86400)
    worker_lock_ttl_seconds: int = Field(default=120, ge=5, le=3600)
    worker_heartbeat_interval_seconds: int = Field(default=30, ge=5, le=3600)

    # --- Worker system alerts (Slice 60 — outbound only, disabled by default) ---
    worker_alerts_enabled: bool = False
    worker_alert_min_severity: str = "info"  # info | warning | critical
    worker_quiet_hours_start: str = ""  # "HH:MM" UTC; empty disables quiet hours
    worker_quiet_hours_end: str = ""

    # --- Paper validation scheduler (Slice 40 — disabled by default) ---
    enable_paper_scheduler: bool = False
    paper_scheduler_interval_seconds: int = Field(default=300, ge=60, le=86400)
    paper_scheduler_max_runs_per_cycle: int = Field(default=5, ge=1, le=50)
    paper_scheduler_max_scans_per_minute: int = Field(default=10, ge=1, le=120)
    paper_scheduler_stale_data_max_age_minutes: int = Field(default=60, ge=5, le=1440)

    # --- Alert delivery (Slice 41 — disabled by default) ---
    alert_delivery_enabled: bool = False
    alert_webhook_enabled: bool = False
    alert_webhook_url: str = ""
    alert_webhook_timeout_seconds: float = Field(default=5.0, ge=1.0, le=30.0)
    alert_webhook_max_retries: int = Field(default=2, ge=0, le=5)
    telegram_alerts_enabled: bool = False
    automatic_telegram_delivery_enabled: bool = False
    # Isolated inbound interaction protocol (AT-043). Not wired to HTTP or execution.
    telegram_interaction_enabled: bool = False
    # Controlled paper activation. Default disarmed. Production rejects every
    # arming flag. Staging accepts the arm only as the full paper package
    # (Watcher arm + binance_usdm + verified bot/chat + inbound). Network
    # delivery stays off unless that package explicitly permits it.
    telegram_paper_activation_armed: bool = False
    telegram_inbound_mode: TelegramInboundMode = TelegramInboundMode.OFF
    telegram_webhook_secret: str = ""
    telegram_network_permitted: bool = False
    telegram_outbound_per_chat: int = Field(default=20, ge=1, le=100)
    telegram_outbound_window_seconds: int = Field(default=60, ge=1, le=3600)
    telegram_bot_token: str = ""
    telegram_bot_id: str = Field(default="", max_length=64)
    telegram_chat_id: str = ""
    telegram_timeout_seconds: float = Field(default=5.0, ge=1.0, le=30.0)
    telegram_max_retries: int = Field(default=2, ge=0, le=5)
    email_alerts_enabled: bool = False

    # --- Market watcher (Slice 41 — read-only, disabled by default) ---
    market_watcher_enabled: bool = False
    market_watcher_stale_data_max_age_minutes: int = Field(default=60, ge=5, le=1440)
    market_watcher_default_symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["BTCUSDT"]
    )

    # --- Watcher orchestration foundation (Phase 7 worker; disabled by default) ---
    # Isolated from the legacy scanner. Production must stay false. Staging
    # must stay false unless ``watcher_paper_staging_activation`` is armed and
    # the paper preflight passes. Local paper Watcher
    # (``python -m app.workers.watcher_paper``) may set this true only when
    # ENVIRONMENT=local and EXECUTION_MODE=paper. Fusion wiring may persist a
    # canonical candidate only when this flag is on and evaluate_setup returns
    # CONFIRMED_SETUP. Does not enable Telegram or live trading. A
    # CONFIRMED_SETUP Candidate continues through existing ActionEligibility,
    # the canonical TradePlan, and the internal paper fill only when
    # EXECUTION_MODE=paper, ENABLE_REAL_TRADING=false, and
    # EXCHANGE_MODE=paper_internal. WATCH and NO_SETUP create nothing.
    watcher_orchestration_enabled: bool = False
    # Staging paper-monitoring arm. Default false. Does not start the worker,
    # does not change Telegram, and is rejected in production. Leave unset.
    watcher_paper_staging_activation: bool = False
    # Governed demo dispatch is a separate staging worker capability. One key
    # is pinned to one tenant/user/account; defaults never construct a client.
    manual_blofin_demo_enabled: bool = False
    governed_blofin_demo_enabled: bool = False
    governed_blofin_demo_organization_id: str = ""
    governed_blofin_demo_user_id: str = ""
    governed_blofin_demo_account_id: str = ""
    # Paper live evidence raises this floor so one timeout plus backoff fits
    # between lease heartbeats. See paper_lease_ttl_seconds.
    watcher_lease_ttl_seconds: int = Field(default=30, ge=1, le=3600)
    watcher_heartbeat_stale_after_seconds: int = Field(default=90, ge=5, le=3600)
    watcher_paper_symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["BTCUSDT"]
    )
    # Legacy local/test file helper. Production watchlists and runtime status
    # use organization-owned database rows, shared by API and dedicated worker.
    watcher_watchlist_path: str = ""
    watcher_paper_poll_interval_seconds: float = Field(default=15.0, ge=1.0, le=3600.0)
    # Optional tenant pin for controlled staging acceptance. Empty preserves
    # the normal round-robin tenant scheduler.
    watcher_paper_organization_id: str = ""
    paper_worker_memory_diagnostics_enabled: bool = False
    watcher_paper_max_scopes_per_cycle: int = Field(default=20, ge=1, le=200)
    watcher_paper_worker_id: str = Field(default="watcher-paper-1", min_length=1, max_length=80)

    # --- Market watcher bridge (Slice 42 — disabled by default, paper scan only) ---
    market_watcher_bridge_enabled: bool = False
    market_watcher_bridge_auto_tick: bool = False
    market_watcher_bridge_interval_seconds: int = Field(default=300, ge=60, le=86400)
    market_watcher_bridge_max_observations_per_cycle: int = Field(default=10, ge=1, le=100)
    market_watcher_bridge_max_scans_per_cycle: int = Field(default=5, ge=1, le=50)
    alert_webhook_secret: str = ""

    # --- TradingView signal intake (AT-037 — disabled by default; paper-only) ---
    tradingview_webhook_enabled: bool = False
    tradingview_webhook_secret: str = Field(default="", repr=False)
    tradingview_webhook_max_skew_seconds: int = Field(default=300, ge=30, le=3600)
    tradingview_webhook_rate_limit: int = Field(default=60, ge=1, le=600)
    tradingview_webhook_rate_window_seconds: int = Field(default=3600, ge=60, le=86400)
    tradingview_auto_create_candidate: bool = False

    # --- BloFin demo read-only sync (AT-037 — demo only; never places orders) ---
    blofin_sync_stale_after_seconds: int = Field(default=300, ge=30, le=86400)
    blofin_sync_max_positions: int = Field(default=50, ge=1, le=200)
    blofin_sync_max_balances: int = Field(default=50, ge=1, le=200)

    # --- Paper-signal orchestration (AT-038 — paper-only; disabled by default) ---
    paper_signal_orchestration_enabled: bool = False
    # observe_only | candidate_only | approval_required (no live/autonomous mode)
    paper_signal_orchestration_mode: str = "observe_only"
    paper_signal_max_age_seconds: int = Field(default=900, ge=60, le=86400)
    paper_signal_min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    paper_signal_require_setup_when_named: bool = True
    paper_signal_require_strategy_when_provided: bool = True
    paper_signal_allowed_timeframes: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "1m",
            "3m",
            "5m",
            "15m",
            "30m",
            "1h",
            "2h",
            "4h",
            "6h",
            "12h",
            "1d",
            "3d",
            "1w",
        ]
    )
    paper_signal_cooldown_after_loss_seconds: int = Field(default=3600, ge=0, le=86400)
    paper_signal_conflict_window_seconds: int = Field(default=3600, ge=60, le=86400)
    paper_signal_default_position_size: float = Field(default=0.001, gt=0, le=1_000_000)
    paper_signal_default_leverage: float = Field(default=1.0, gt=0, le=125)
    paper_signal_create_run_plan: bool = True

    # --- Data stores (placeholders; clients wired in later slices) ---
    database_url: str = "postgresql+psycopg://alphatrade:alphatrade@localhost:5432/alphatrade"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    # --- Providers (blank credentials -> mock/fallback providers) ---
    provider_mode: str = "mock"  # mock | fallback | live
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    # Phase 2 model router — callers never supply free-form model names.
    agent_reasoning_model: str = Field(default="gpt-6-astra", min_length=1, max_length=80)
    agent_reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    agent_max_output_tokens: int = Field(default=25000, ge=1024, le=65536)
    llm_tier_a_model: str = "gpt-4o"
    llm_tier_b_model: str = "gpt-4o-mini"
    model_router_policy_version: str = "model-router/v1"
    model_router_fail_closed: bool = False
    embeddings_model: str = "text-embedding-3-small"
    # None = auto (384 mock; OpenAI model native size when key configured).
    embeddings_dimensions: int | None = Field(default=None, ge=1)

    # --- Market data (read-only; Binance public API, no key required) ---
    market_data_enabled: bool = True
    market_data_provider: str = "binance"  # binance | mock
    market_data_spot_base_url: str = "https://api.binance.com"
    market_data_futures_base_url: str = "https://fapi.binance.com"
    market_data_cache_use_redis: bool = True
    market_data_timeout_seconds: float = 10.0
    # Phase 5 perpetual evidence: replay fixtures by default; live USD-M is opt-in.
    # Never falls back to spot. Values: replay | binance_usdm | bybit_usdt_perpetual
    perpetual_evidence_source: str = "replay"
    # Optional whole-source failover. none | bybit_usdt_perpetual. Not a price substitute.
    perpetual_evidence_secondary_source: str = "none"
    bybit_perpetual_base_url: str = "https://api.bybit.com"
    perpetual_evidence_timeout_seconds: float = Field(default=10.0, ge=1.0, le=30.0)
    # Public USD-M request-weight budget. 2400/min is the exchange IP cap.
    binance_request_weight_per_minute: int = Field(default=1800, ge=20, le=2400)
    binance_request_max_retries: int = Field(default=3, ge=0, le=8)
    binance_request_max_backoff_seconds: float = Field(default=30.0, ge=0.0, le=120.0)
    binance_evidence_cache_entries: int = Field(default=8, ge=1, le=64)
    # Closed aggTrade windows are reused only until this TTL. A later payload
    # with a different fingerprint replaces the entry (exchange correction).
    binance_evidence_cache_ttl_seconds: float = Field(default=120.0, gt=0.0, le=3600.0)
    # Raw aggTrade rows above this count are not cached. Reduced snapshots are.
    binance_evidence_cache_max_rows: int = Field(default=4096, ge=1, le=100_000)
    # AT-069 continuous read-only monitor. Tick-on-read only; no Watcher start.
    perpetual_monitor_poll_seconds: float = Field(default=2.0, ge=0.25, le=60.0)
    perpetual_monitor_backoff_initial_seconds: float = Field(default=0.25, ge=0.05, le=10.0)
    perpetual_monitor_backoff_max_seconds: float = Field(default=30.0, ge=1.0, le=120.0)

    # --- Observability ---
    langsmith_api_key: str = ""
    observability_strict_mode: bool = False
    trace_id_header: str = "X-Trace-ID"
    # AT-016 RED metrics — disabled by default; scrape token required outside local.
    metrics_enabled: bool = False
    metrics_scrape_token: str = ""

    # --- Authentication ---
    jwt_secret: str = Field(
        default="dev-only-change-me-before-production",
        description="HS256 signing secret for access tokens.",
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    auth_refresh_cookie_enabled: bool = False
    auth_refresh_cookie_name: str = "alphatrade_refresh"
    auth_refresh_cookie_path: str = "/auth"
    auth_cookie_secure: bool | None = None
    auth_cookie_samesite: str = "lax"
    auth_omit_refresh_from_body: bool = True
    access_token_denylist_enabled: bool = True
    access_token_denylist_use_redis: bool = True
    access_token_denylist_fail_closed: bool = True
    password_min_length: int = 12
    password_max_length: int = 128
    bcrypt_max_password_bytes: int = 72

    # --- Journal → RAG (optional learning loop) ---
    journal_rag_sync_enabled: bool = True

    # --- Journal statistics (AT-031) — bounded aggregate scans ---
    journal_stats_max_rows: int = Field(default=5000, ge=100, le=50_000)

    # --- Journal excursion replay (AT-032) — bounded HistoricalCandle reads ---
    journal_replay_max_candles: int = Field(default=5000, ge=10, le=50_000)
    journal_replay_batch_max: int = Field(default=100, ge=1, le=1000)

    # --- Auto-journal hooks (AT-033) — opt-in, default off ---
    # When enabled, closing a paper position / paper-validation trade also
    # creates a canonical journal trade (entry_method=auto). Journaling
    # failures never block the close itself.
    journal_auto_from_position_close: bool = False
    journal_auto_from_paper_validation: bool = False

    # --- Journal attachments (AT-033) — DB-backed, size/MIME/quota capped ---
    journal_attachment_max_bytes: int = Field(default=5_242_880, ge=1024, le=26_214_400)
    journal_attachment_allowed_types: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["image/png", "image/jpeg", "image/webp", "application/pdf"]
    )
    journal_attachment_max_per_trade: int = Field(default=20, ge=1, le=100)

    # --- Backtest engine (AT-034) — bounded workload; truncate not allowed ---
    backtest_max_bars: int = Field(default=20_000, ge=100, le=100_000)
    # Sync path for small datasets (preserves Slice 35 UX); larger runs stay QUEUED.
    backtest_sync_max_bars: int = Field(default=6000, ge=10, le=100_000)
    backtest_max_active_runs_per_org: int = Field(default=2, ge=1, le=50)
    backtest_journal_bulk_max: int = Field(default=1000, ge=1, le=10_000)
    # Setup evidence tiers (AT-034) — advisory only; never feeds execution/risk.
    backtest_tier1_oos_min_trades: int = Field(default=30, ge=1, le=10_000)
    backtest_tier1_oos_min_profit_factor: float = Field(default=1.3, ge=0.0, le=100.0)
    backtest_tier1_min_confirm_trades: int = Field(default=20, ge=1, le=10_000)
    backtest_tier2_min_trades: int = Field(default=30, ge=1, le=10_000)
    backtest_tier2_oos_min_trades: int = Field(default=15, ge=1, le=10_000)
    backtest_tier2_oos_min_profit_factor: float = Field(default=1.1, ge=0.0, le=100.0)

    # --- LLM narrative polish (Slice 21 — explanation only, not decision authority) ---
    narrative_llm_enabled: bool = True

    # --- Rate limiting ---
    rate_limit_use_redis: bool = True
    rate_limit_allow_in_memory_fallback: bool = True
    redis_connect_timeout_seconds: float = 1.0
    jwt_secret_min_length: int = 32

    # --- Email (Slice 25 — verification, reset, invitations) ---
    email_provider: str = "mock"  # mock | smtp | resend | sendgrid
    email_from_address: str = "noreply@alphatrade.local"
    email_base_url: str = "http://localhost:3000"
    email_send_enabled: bool = True
    email_verification_expire_hours: int = 48
    password_reset_expire_hours: int = 2
    invitation_expire_hours: int = 168
    require_email_verified: bool | None = None
    demo_seed_enabled: bool = False
    email_auto_verify_local: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    resend_api_key: str = ""
    sendgrid_api_key: str = ""

    # --- Billing (Slice 26 — disabled by default; mock unless Stripe configured) ---
    billing_enabled: bool = False
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_publishable_key: str = ""
    billing_checkout_success_url: str = "http://localhost:3000/billing?checkout=success"
    billing_checkout_cancel_url: str = "http://localhost:3000/billing?checkout=cancel"
    billing_portal_return_url: str = "http://localhost:3000/billing"

    @field_validator("email_provider", mode="before")
    @classmethod
    def _normalize_email_provider(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("auth_cookie_samesite", mode="before")
    @classmethod
    def _normalize_samesite(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_database_url(cls, value: object) -> object:
        """Accept Render/Heroku-style ``postgres://`` URLs for managed Postgres."""
        if not isinstance(value, str):
            return value
        url = value.strip()
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://") :]
        if url.startswith("postgresql://") and "+psycopg" not in url:
            return "postgresql+psycopg://" + url[len("postgresql://") :]
        return url

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Allow a comma-separated string for ``CORS_ORIGINS`` in env files."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("journal_attachment_allowed_types", mode="before")
    @classmethod
    def _split_attachment_types(cls, value: object) -> object:
        """Allow a comma-separated string for ``JOURNAL_ATTACHMENT_ALLOWED_TYPES``."""
        if isinstance(value, str):
            return [item.strip().lower() for item in value.split(",") if item.strip()]
        return value

    @field_validator("provider_mode", mode="before")
    @classmethod
    def _normalize_provider_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("worker_mode", mode="before")
    @classmethod
    def _normalize_worker_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("exchange_mode", mode="before")
    @classmethod
    def _normalize_exchange_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("paper_signal_orchestration_mode", mode="before")
    @classmethod
    def _normalize_paper_signal_orchestration_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("paper_signal_allowed_timeframes", mode="before")
    @classmethod
    def _split_paper_signal_timeframes(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("watcher_paper_symbols", mode="before")
    @classmethod
    def _split_watcher_paper_symbols(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip().upper() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip().upper() for item in value if str(item).strip()]
        return value

    @field_validator("watcher_paper_organization_id", mode="before")
    @classmethod
    def _validate_watcher_paper_organization_id(cls, value: object) -> str:
        if value is None:
            return ""
        token = str(value).strip()
        if not token:
            return ""
        try:
            UUID(token)
        except ValueError as exc:
            raise ValueError("watcher_paper_organization_id must be a UUID") from exc
        return token.lower()

    @field_validator("redis_url", mode="before")
    @classmethod
    def _normalize_redis_url(cls, value: object) -> object:
        """Accept bare URLs; recover redis:// from common redis-cli --tls -u mistakes."""
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if stripped.startswith("redis-cli"):
            for scheme in ("rediss://", "redis://"):
                idx = stripped.find(scheme)
                if idx >= 0:
                    remainder = stripped[idx:]
                    return remainder.split()[0].strip("'\"")
        return stripped

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.upper()
        return value

    @field_validator("telegram_inbound_mode", mode="before")
    @classmethod
    def _normalize_telegram_inbound_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("perpetual_evidence_source")
    @classmethod
    def _validate_perpetual_evidence_source(cls, value: str) -> str:
        from app.market_activation.profile import canonicalize_perpetual_evidence_source

        return canonicalize_perpetual_evidence_source(value)

    @field_validator("perpetual_evidence_secondary_source")
    @classmethod
    def _validate_perpetual_evidence_secondary(cls, value: str) -> str:
        from app.market_activation.profile import canonicalize_secondary_evidence_source

        return canonicalize_secondary_evidence_source(value)

    @model_validator(mode="after")
    def _enforce_trading_safety(self) -> Settings:
        """Refuse to start with any live/real trading configuration.

        Phase 1 permanent paper invariant: ENABLE_REAL_TRADING=true and
        execution_mode=trade fail closed in local, test, staging and production.
        ``trade`` cannot silently alias paper.
        """
        from app.core.paper_safety import validate_permanent_paper_settings

        validate_permanent_paper_settings(self)
        return self

    @model_validator(mode="after")
    def _validate_jwt_secret(self) -> Settings:
        """Require a strong JWT secret outside local development."""
        secret_length = len(self.jwt_secret.encode("utf-8"))
        if self.environment is not Environment.LOCAL and secret_length < self.jwt_secret_min_length:
            raise ValueError(
                f"jwt_secret must be at least {self.jwt_secret_min_length} bytes in "
                f"{self.environment.value} environments."
            )
        return self

    @model_validator(mode="after")
    def _validate_metrics_scrape_gate(self) -> Settings:
        """Refuse unrestricted public metrics outside local."""
        if (
            self.metrics_enabled
            and self.environment is not Environment.LOCAL
            and not self.metrics_scrape_token.strip()
        ):
            raise ValueError(
                "metrics_enabled=true outside local requires metrics_scrape_token "
                "(METRICS_SCRAPE_TOKEN). Unrestricted public scrape is not allowed."
            )
        return self

    @model_validator(mode="after")
    def _enforce_exchange_safety(self) -> Settings:
        """Refuse to start with unsafe/ambiguous exchange connectivity.

        Runs in every environment so demo mode can never be wired to a
        production venue and ``trade_live`` can never be selected.
        """
        from app.core.exchange_safety import validate_exchange_mode_settings

        validate_exchange_mode_settings(self)
        return self

    @model_validator(mode="after")
    def _validate_deployment_safety(self) -> Settings:
        """Enforce staging/production deployment invariants."""
        from app.core.deployment_safety import validate_deployment_settings

        validate_deployment_settings(self)
        return self

    @model_validator(mode="after")
    def _validate_live_market_activation(self) -> Settings:
        """Refuse an unsafe Binance USD-M evidence selection in every environment."""
        from app.market_activation.profile import validate_live_market_activation

        validate_live_market_activation(self)
        return self

    @property
    def must_verify_email(self) -> bool:
        """Whether login requires a verified email address."""
        if self.require_email_verified is not None:
            return self.require_email_verified
        return self.environment is not Environment.LOCAL

    @property
    def real_trading_enabled(self) -> bool:
        """Always false: live order execution cannot be constructed.

        Kept as a boolean derived from both flags so any latent combination
        remains fail-closed even if a future caller bypasses Settings validators.
        """
        return False

    @property
    def blofin_readonly_configured(self) -> bool:
        """Presence of dedicated sync credentials; never execution authority."""
        return bool(
            self.blofin_readonly_api_key.strip()
            and self.blofin_readonly_api_secret.strip()
            and self.blofin_readonly_api_passphrase.strip()
        )

    @property
    def blofin_demo_configured(self) -> bool:
        """True when all BloFin demo credentials are present (env-only)."""
        return bool(
            self.blofin_api_key.strip()
            and self.blofin_api_secret.strip()
            and self.blofin_api_passphrase.strip()
        )

    @property
    def exchange_demo_active(self) -> bool:
        """True when paper orders should route to the BloFin demo exchange."""
        return self.exchange_mode is ExchangeMode.PAPER_EXCHANGE_DEMO


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance.

    Cached so configuration is parsed once per process. Tests can clear the
    cache via ``get_settings.cache_clear()``.
    """
    return Settings()
