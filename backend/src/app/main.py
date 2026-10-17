"""FastAPI application factory and lifecycle wiring.

Composition root: configures logging, builds the provider registry, installs
middleware and exception handlers, and mounts routers. Kept free of business
logic so it stays a thin, testable composition layer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.agent_capture import routes as saved_entry_routes
from app.api.routes import (
    agent_voice,
    alerts,
    analytics,
    approvals,
    audit,
    auth,
    backtests,
    billing,
    blofin_activity,
    canonical,
    chat,
    coaching,
    conversations,
    dashboard,
    demo,
    exchange,
    execution,
    experiments,
    governed_learning,
    health,
    human_vs_system,
    interactive_agent,
    journal,
    knowledge,
    learning_analytics,
    lessons,
    manual_levels,
    market,
    market_watcher,
    metrics,
    notifications,
    organizations,
    paper_signal_orchestration,
    paper_validation,
    performance,
    positions,
    pretrade,
    proposals,
    providers,
    research_validation,
    risk,
    strategy_analytics,
    strategy_brain,
    strategy_library,
    strategy_modules,
    strategy_quality,
    telegram_enrollment,
    tools,
    tradingview,
    trendpulse_screening,
    usage,
    validation_priority,
    watcher_paper,
    worker,
)
from app.core.config import Environment, Settings, get_settings
from app.core.deployment_safety import deployment_posture
from app.core.errors import register_exception_handlers
from app.core.exchange_readiness import run_exchange_demo_startup_check
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.providers.factory import resolve_providers
from app.providers.qdrant import get_process_vector_store
from app.providers.registry import build_default_registry, get_provider_registry
from app.strategies.registry import build_default_registry as build_strategy_registry
from app.strategies.registry import get_strategy_registry
from app.tools.registry import build_default_registry as build_tool_registry
from app.tools.registry import get_tool_registry

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    logger.info(
        "startup",
        app=settings.app_name,
        version=__version__,
        **deployment_posture(settings),
    )
    run_exchange_demo_startup_check(settings, app.state.provider_registry)
    # TODO(slice-18): open real Qdrant/Redis clients when credentials are present.
    app.state.vector_store = get_process_vector_store()
    resolved = resolve_providers(settings)
    app.state.resolved_providers = resolved
    from app.security.rate_limit import get_rate_limiter

    limiter = get_rate_limiter(settings)
    rate_backend = "redis" if getattr(limiter, "using_redis", False) else "memory"
    logger.info("rate_limit_backend", backend=rate_backend)

    from app.db.session import get_session_factory
    from app.runtime.canonical import build_production_canonical_runtime

    runtime = build_production_canonical_runtime(get_session_factory(), settings=settings)
    app.state.canonical_runtime = runtime
    logger.info(
        "canonical_runtime_ready",
        watcher_enabled=runtime.watcher_enabled,
        telegram_enabled=runtime.telegram_enabled,
        real_trading_enabled=runtime.flags.real_trading_enabled,
    )

    worker_driver = _maybe_start_in_process_worker(settings)
    app.state.worker_driver = worker_driver
    paper_runtime = _maybe_start_watcher_paper_runtime(
        settings, monitor=getattr(app.state, "market_monitor", None)
    )
    app.state.watcher_paper_runtime = paper_runtime

    yield

    if paper_runtime is not None:
        paper_runtime.stop()
    if worker_driver is not None:
        worker_driver.stop()
    # TODO(slice-18): close Qdrant client gracefully when using live vector store.
    logger.info("shutdown")


class _BackgroundStoppable(Protocol):
    def stop(self) -> None: ...


def _maybe_start_in_process_worker(settings: Settings) -> _BackgroundStoppable | None:
    """Start the background worker on a daemon thread when so configured.

    Only runs when ``WORKER_ENABLED=true`` and ``WORKER_MODE=in_process``; the
    recommended deployment uses a dedicated worker process instead.
    """
    if not (settings.worker_enabled and settings.worker_mode == "in_process"):
        return None
    from app.workers.entrypoint import build_driver

    driver = build_driver(settings)
    driver.start_background_thread()
    logger.info("worker_in_process_enabled", worker_name=settings.worker_name)
    return driver


def _maybe_start_watcher_paper_runtime(
    settings: Settings, *, monitor: object | None = None
) -> _BackgroundStoppable | None:
    """Start the paper Watcher loop locally when orchestration is explicitly on.

    Staging activation is the dedicated worker only. This API process does not
    autostart it. Production cannot enable the arm.
    """

    from app.db.session import get_session_factory
    from app.market_monitor.monitor import PerpetualMarketMonitor
    from app.workers.watcher_paper import build_watcher_paper_runtime, paper_runtime_enabled

    if settings.environment is not Environment.LOCAL:
        return None
    if not paper_runtime_enabled(settings):
        return None
    resolved_monitor = monitor if isinstance(monitor, PerpetualMarketMonitor) else None
    runtime = build_watcher_paper_runtime(settings, get_session_factory(), monitor=resolved_monitor)
    runtime.start_background_thread()
    logger.info("watcher_paper_runtime_in_process", worker_id=runtime.snapshot().worker_id)
    return runtime


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    from app.core.paper_safety import assert_execution_capable_composition_root
    from app.core.persistence_firewall import install_persistence_firewall

    settings = settings or get_settings()
    assert_execution_capable_composition_root(settings)
    install_persistence_firewall()
    configure_logging(log_level=settings.log_level, json_logs=settings.log_json)

    docs_enabled = settings.environment == Environment.LOCAL
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.state.telegram_paper_activation = None
    app.state.telegram_webhook_mounted = False
    app.state.provider_registry = build_default_registry(settings)
    from app.market_monitor.factory import build_perpetual_market_monitor

    app.state.market_monitor = build_perpetual_market_monitor(settings)
    app.state.strategy_registry = build_strategy_registry()
    app.state.tool_registry = build_tool_registry(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(
        RequestContextMiddleware,
        request_id_header=settings.request_id_header,
        trace_id_header=settings.trace_id_header,
        metrics_enabled=settings.metrics_enabled,
    )

    register_exception_handlers(app)

    # Resolve shared resources from app.state so the same registry instance is
    # used across requests and can be overridden in tests.
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    app.dependency_overrides[get_provider_registry] = lambda: app.state.provider_registry
    app.dependency_overrides[get_strategy_registry] = lambda: app.state.strategy_registry
    app.dependency_overrides[get_tool_registry] = lambda: app.state.tool_registry

    for r in (
        health.router,
        agent_voice.router,
        interactive_agent.router,
        saved_entry_routes.router,
        metrics.router,
        providers.router,
        auth.router,
        organizations.router,
        chat.router,
        conversations.router,
        market.router,
        strategy_modules.router,
        strategy_library.router,
        governed_learning.router,
        strategy_analytics.router,
        strategy_brain.router,
        paper_validation.router,
        research_validation.router,
        tradingview.router,
        paper_signal_orchestration.router,
        alerts.router,
        notifications.router,
        market_watcher.router,
        backtests.router,
        manual_levels.router,
        pretrade.router,
        human_vs_system.router,
        risk.router,
        proposals.router,
        approvals.router,
        execution.router,
        canonical.router,
        exchange.router,
        blofin_activity.router,
        experiments.router,
        trendpulse_screening.router,
        positions.router,
        journal.router,
        lessons.router,
        analytics.router,
        learning_analytics.router,
        validation_priority.router,
        strategy_quality.router,
        coaching.router,
        performance.router,
        knowledge.router,
        audit.router,
        usage.router,
        billing.router,
        dashboard.router,
        demo.router,
        tools.router,
        worker.router,
        watcher_paper.router,
        telegram_enrollment.router,
    ):
        app.include_router(r)
    return app


app = create_app()
