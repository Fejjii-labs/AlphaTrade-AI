"""Health and readiness endpoints.

These are fully functional: ``/health`` is a cheap liveness probe that also
surfaces the trading-safety posture; ``/health/ready`` reports readiness based
on aggregated provider status.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request

from app import __version__
from app.core.config import Settings
from app.core.dependencies import ProviderRegistryDep, SettingsDep
from app.core.deploy_info import resolve_git_sha
from app.market_activation.profile import perpetual_evidence_health
from app.persistence.runtime_health import project_worker_component
from app.providers.base import ProviderHealth
from app.schemas.health import (
    HealthResponse,
    PaperWorkerHeartbeatResponse,
    ReadinessResponse,
    WorkerRuntimeObservation,
)
from app.telegram_activation.contracts import PreflightReport
from app.telegram_activation.controller import TelegramPaperActivation
from app.telegram_activation.preflight import run_preflight

router = APIRouter(tags=["health"])


def _read_worker_runtime(
    settings: Settings,
    *,
    now: datetime | None = None,
) -> WorkerRuntimeObservation:
    """Best-effort read of worker rows. A database error leaves liveness intact.

    Component ``available`` follows heartbeat freshness. A stored row alone is
    not ``RUNNING``.
    """

    observed_at = now or datetime.now(UTC)
    limit = settings.watcher_heartbeat_stale_after_seconds
    try:
        from app.db.session import get_session_factory
        from app.persistence.runtime_status import (
            PAPER_WORKER_COMPONENT,
            TELEGRAM_COMPONENT,
            WATCHER_COMPONENT,
            load_runtime_rows,
        )
        from app.services.worker_heartbeat import (
            paper_worker_stale_after_seconds,
            project_paper_worker_heartbeat,
        )

        with get_session_factory()() as session:
            rows = load_runtime_rows(session)
    except Exception:
        return WorkerRuntimeObservation(
            available=False,
            watcher=project_worker_component(None, now=observed_at, stale_after_seconds=limit),
            telegram=project_worker_component(None, now=observed_at, stale_after_seconds=limit),
            paper_worker=project_worker_component(None, now=observed_at, stale_after_seconds=limit),
        )
    worker_limit = paper_worker_stale_after_seconds(settings)
    return WorkerRuntimeObservation(
        available=True,
        watcher=project_worker_component(
            rows.get(WATCHER_COMPONENT),
            now=observed_at,
            stale_after_seconds=limit,
        ),
        telegram=project_worker_component(
            rows.get(TELEGRAM_COMPONENT),
            now=observed_at,
            stale_after_seconds=limit,
        ),
        paper_worker=project_paper_worker_heartbeat(
            rows.get(PAPER_WORKER_COMPONENT),
            now=observed_at,
            stale_after_seconds=worker_limit,
        ),
    )


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health(settings: SettingsDep) -> HealthResponse:
    evidence = perpetual_evidence_health(settings)
    return HealthResponse(
        app=settings.app_name,
        version=__version__,
        environment=settings.environment.value,
        execution_mode=settings.execution_mode.value,
        real_trading_enabled=settings.real_trading_enabled,
        exchange_mode=settings.exchange_mode.value,
        must_verify_email=settings.must_verify_email,
        demo_seed_enabled=settings.demo_seed_enabled,
        market_watcher_enabled=settings.market_watcher_enabled,
        market_watcher_bridge_enabled=settings.market_watcher_bridge_enabled,
        watcher_orchestration_enabled=settings.watcher_orchestration_enabled,
        watcher_paper_staging_activation=settings.watcher_paper_staging_activation,
        telegram_alerts_enabled=settings.telegram_alerts_enabled,
        telegram_interaction_enabled=settings.telegram_interaction_enabled,
        automatic_telegram_delivery_enabled=settings.automatic_telegram_delivery_enabled,
        telegram_paper_activation_armed=settings.telegram_paper_activation_armed,
        telegram_inbound_mode=settings.telegram_inbound_mode.value,
        telegram_network_permitted=settings.telegram_network_permitted,
        blofin_readonly_sync_enabled=settings.blofin_readonly_sync_enabled,
        blofin_live_evidence_demo_enabled=settings.blofin_live_evidence_demo_enabled,
        blofin_readonly_sync_credentials_configured=settings.blofin_readonly_configured,
        blofin_readonly_sync_origin_configured=bool(settings.blofin_demo_rest_base_url.strip()),
        perpetual_evidence_source=evidence["perpetual_evidence_source"],
        perpetual_evidence_secondary_source=evidence["perpetual_evidence_secondary_source"],
        perpetual_evidence_activation=evidence["perpetual_evidence_activation"],
        perpetual_evidence_intended_staging_source=evidence[
            "perpetual_evidence_intended_staging_source"
        ],
        perpetual_evidence_rollback_source=evidence["perpetual_evidence_rollback_source"],
        live_market_read_only=evidence["live_market_read_only"],
        exchange_credentials_used_for_market_evidence=evidence[
            "exchange_credentials_used_for_market_evidence"
        ],
        spot_fallback_permitted=evidence["spot_fallback_permitted"],
        fabricated_fallback_permitted=evidence["fabricated_fallback_permitted"],
        live_quote_freshness_seconds=evidence["live_quote_freshness_seconds"],
        first_perpetual_symbol=evidence["first_perpetual_symbol"],
        git_sha=resolve_git_sha(),
        worker_runtime=_read_worker_runtime(settings),
        timestamp=datetime.now(UTC),
    )


@router.get(
    "/health/telegram-paper-activation",
    response_model=PreflightReport,
    summary="Paper Telegram activation posture",
)
async def telegram_paper_activation(settings: SettingsDep, request: Request) -> PreflightReport:
    """Read-only posture. This route does not arm Telegram or send a message."""
    controller = getattr(request.app.state, "telegram_paper_activation", None)
    mounted = bool(getattr(request.app.state, "telegram_webhook_mounted", False))
    if not isinstance(controller, TelegramPaperActivation):
        return run_preflight(settings=settings, webhook_mounted=mounted)
    status = controller.preflight()
    if mounted and not status.webhook_mounted:
        return status.model_copy(update={"webhook_mounted": True})
    return status


@router.get(
    "/health/paper-worker",
    response_model=PaperWorkerHeartbeatResponse,
    summary="Paper worker heartbeat",
)
async def paper_worker_heartbeat(settings: SettingsDep) -> PaperWorkerHeartbeatResponse:
    """Report the worker heartbeat and alert when it is stale or missing.

    This route does not arm the worker, the exit monitor, or real trading.
    A Telegram send uses the existing bot token and chat id. The response
    does not include those values.
    """

    from app.services.worker_heartbeat import (
        HeartbeatAlertLedger,
        MemoryHeartbeatAlertLedger,
        RuntimeStatusHeartbeatLedger,
        heartbeat_status,
        load_paper_worker_row,
        paper_worker_stale_after_seconds,
        project_paper_worker_heartbeat,
        raise_heartbeat_alert,
    )

    observed_at = datetime.now(UTC)
    limit = paper_worker_stale_after_seconds(settings)
    row: object | None = None
    ledger: HeartbeatAlertLedger
    try:
        from app.db.session import get_session_factory

        factory = get_session_factory()
        with factory() as session:
            row = load_paper_worker_row(session)
        ledger = RuntimeStatusHeartbeatLedger(factory)
    except Exception:
        row = None
        ledger = MemoryHeartbeatAlertLedger()
    observation = project_paper_worker_heartbeat(row, now=observed_at, stale_after_seconds=limit)
    alert_raised = raise_heartbeat_alert(settings, observation, now=observed_at, ledger=ledger)
    return PaperWorkerHeartbeatResponse(
        status=heartbeat_status(observation),
        observation=observation,
        alert_raised=alert_raised,
        timestamp=observed_at,
    )


@router.get("/health/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def readiness(registry: ProviderRegistryDep, settings: SettingsDep) -> ReadinessResponse:
    from app.core.provider_policy import provider_fail_closed

    statuses = registry.statuses()
    unavailable = sum(1 for s in statuses if s.health is ProviderHealth.UNAVAILABLE)
    if provider_fail_closed(settings):
        # Staging/production: authoritative LLM/embeddings/vector must not be
        # silently degraded onto mocks or in-memory substitutes.
        critical = {"llm", "embeddings", "vector"}
        unavailable += sum(
            1
            for s in statuses
            if s.kind.value in critical
            and (s.is_mock or (s.health is ProviderHealth.DEGRADED and s.using_fallback))
        )
    ready = unavailable == 0
    return ReadinessResponse(
        status="ready" if ready else "degraded",
        ready=ready,
        providers_total=len(statuses),
        providers_unavailable=unavailable,
        timestamp=datetime.now(UTC),
    )
