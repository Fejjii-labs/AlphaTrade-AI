"""Health and readiness endpoints.

These are fully functional: ``/health`` is a cheap liveness probe that also
surfaces the trading-safety posture; ``/health/ready`` reports readiness based
on aggregated provider status.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Request

from app import __version__
from app.core.config import Settings
from app.core.dependencies import ProviderRegistryDep, SettingsDep
from app.core.deploy_info import resolve_git_sha
from app.market_activation.profile import perpetual_evidence_health
from app.persistence.runtime_health import project_worker_component
from app.persistence.worker_runtime_state import (
    load_worker_runtime_row,
    project_worker_effective,
)
from app.providers.base import ProviderHealth, ProviderKind, ProviderStatus
from app.schemas.health import (
    ArmingRefusalObservation,
    HealthResponse,
    PaperWorkerHeartbeatResponse,
    ReadinessResponse,
    WorkerEffectiveObservation,
    WorkerRuntimeObservation,
)
from app.telegram_activation.contracts import PreflightReport
from app.telegram_activation.controller import TelegramPaperActivation
from app.telegram_activation.preflight import run_preflight

router = APIRouter(tags=["health"])


def _arming_refusal(rows: dict[str, object] | None) -> ArmingRefusalObservation:
    """Project the boot refusal. A database miss is not a refusal."""

    if rows is None:
        return ArmingRefusalObservation()
    from app.persistence.runtime_status import PAPER_ARMING_COMPONENT
    from app.services.arming_visibility import project_arming_refusal

    return project_arming_refusal(rows.get(PAPER_ARMING_COMPONENT))


def _load_runtime_rows() -> dict[str, object] | None:
    """Runtime rows, or None when the database cannot be read."""

    try:
        from app.db.session import get_session_factory
        from app.persistence.runtime_status import load_runtime_rows

        with get_session_factory()() as session:
            return dict(load_runtime_rows(session))
    except Exception:
        return None


def _worker_effective(settings: Settings, *, now: datetime) -> WorkerEffectiveObservation:
    """Worker flags, or unavailable when the row cannot be read."""

    limit = settings.watcher_heartbeat_stale_after_seconds
    return project_worker_effective(load_worker_runtime_row(), now=now, stale_after_seconds=limit)


def _apply_worker_flags(
    payload: dict[str, object],
    effective: WorkerEffectiveObservation,
) -> None:
    """Replace watcher and Telegram flags when the worker has published them.

    Execution mode and real trading stay on the API process. A stale row is
    still the worker's last report; ``worker_flags_stale`` says so.
    """

    payload["worker_effective"] = effective
    if effective.source != "worker":
        payload["worker_flags_source"] = "api_process"
        payload["worker_flags_stale"] = False
        return
    payload["worker_flags_source"] = "worker"
    payload["worker_flags_stale"] = effective.stale
    payload["watcher_orchestration_enabled"] = effective.watcher_orchestration_enabled
    payload["watcher_paper_staging_activation"] = effective.watcher_paper_staging_activation
    payload["telegram_alerts_enabled"] = effective.telegram_alerts_enabled
    payload["telegram_interaction_enabled"] = effective.telegram_interaction_enabled
    payload["automatic_telegram_delivery_enabled"] = effective.automatic_telegram_delivery_enabled
    payload["telegram_paper_activation_armed"] = effective.telegram_paper_activation_armed


def _read_worker_runtime(
    settings: Settings,
    *,
    now: datetime | None = None,
) -> WorkerRuntimeObservation:
    """Best-effort read of worker rows. A database error leaves liveness intact."""

    observed_at = now or datetime.now(UTC)
    return _project_worker_runtime(settings, _load_runtime_rows(), now=observed_at)


def _project_worker_runtime(
    settings: Settings,
    rows: dict[str, object] | None,
    *,
    now: datetime,
) -> WorkerRuntimeObservation:
    """Project already loaded rows. ``None`` means the database could not be read.

    Component ``available`` follows heartbeat freshness. A stored row alone is
    not ``RUNNING``. ``exit_monitor`` is the exit-monitor thread. It is not
    the watcher heartbeat.
    """

    observed_at = now
    limit = settings.watcher_heartbeat_stale_after_seconds
    loaded = rows
    if loaded is None:
        absent = project_worker_component(None, now=observed_at, stale_after_seconds=limit)
        return WorkerRuntimeObservation(
            available=False,
            watcher=absent,
            telegram=absent,
            paper_worker=absent,
            exit_monitor=absent,
        )
    from app.persistence.runtime_status import (
        PAPER_ARMING_COMPONENT,
        PAPER_EXIT_MONITOR_COMPONENT,
        PAPER_WORKER_COMPONENT,
        TELEGRAM_COMPONENT,
        WATCHER_COMPONENT,
    )
    from app.services.arming_visibility import exit_monitor_expected
    from app.services.worker_heartbeat import (
        exit_monitor_liveness_status,
        paper_worker_stale_after_seconds,
        project_paper_worker_heartbeat,
    )

    worker_limit = paper_worker_stale_after_seconds(settings)
    _status, exit_monitor = exit_monitor_liveness_status(
        expected=exit_monitor_expected(loaded.get(PAPER_ARMING_COMPONENT)),
        row=loaded.get(PAPER_EXIT_MONITOR_COMPONENT),
        now=observed_at,
        stale_after_seconds=worker_limit,
    )
    return WorkerRuntimeObservation(
        available=True,
        watcher=project_worker_component(
            loaded.get(WATCHER_COMPONENT),
            now=observed_at,
            stale_after_seconds=limit,
        ),
        telegram=project_worker_component(
            loaded.get(TELEGRAM_COMPONENT),
            now=observed_at,
            stale_after_seconds=limit,
        ),
        paper_worker=project_paper_worker_heartbeat(
            loaded.get(PAPER_WORKER_COMPONENT),
            now=observed_at,
            stale_after_seconds=worker_limit,
        ),
        exit_monitor=exit_monitor,
    )


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health(settings: SettingsDep) -> HealthResponse:
    evidence = perpetual_evidence_health(settings)
    observed_at = datetime.now(UTC)
    rows = _load_runtime_rows()
    arming = _arming_refusal(rows)
    payload: dict[str, object] = dict(  # noqa: C408 — mutated before HealthResponse
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
        worker_runtime=_project_worker_runtime(settings, rows, now=observed_at),
        arming_refusal=arming,
        timestamp=observed_at,
    )
    _apply_worker_flags(payload, _worker_effective(settings, now=observed_at))
    return HealthResponse(**payload)


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
async def paper_worker_heartbeat(
    settings: SettingsDep,
    background_tasks: BackgroundTasks,
) -> PaperWorkerHeartbeatResponse:
    """Report the worker heartbeat and alert when it is stale or missing.

    This route does not arm the worker, the exit monitor, or real trading.
    A Telegram send uses the existing bot token and chat id. The response
    does not include those values. An unsent kill-switch page is retried
    after the response, in a worker thread, at most once per 60 seconds
    per activation version. No thread is started when no unsent page is
    visible, or when every visible page is still inside that limit.
    """

    from sqlalchemy.orm import Session, sessionmaker

    from app.persistence.runtime_status import (
        PAPER_ARMING_COMPONENT,
        PAPER_EXIT_MONITOR_COMPONENT,
        PAPER_WORKER_COMPONENT,
    )
    from app.services.arming_visibility import (
        ARMING_ALERT_COMPONENT,
        exit_monitor_expected,
        raise_arming_refusal_alert,
    )
    from app.services.worker_heartbeat import (
        EXIT_MONITOR_ALERT_COMPONENT,
        HeartbeatAlertLedger,
        MemoryHeartbeatAlertLedger,
        RuntimeStatusHeartbeatLedger,
        exit_monitor_heartbeat_alert,
        exit_monitor_liveness_status,
        heartbeat_status,
        paper_worker_stale_after_seconds,
        project_paper_worker_heartbeat,
        raise_heartbeat_alert,
        raise_prepared_alert,
    )

    observed_at = datetime.now(UTC)
    limit = paper_worker_stale_after_seconds(settings)
    rows = _load_runtime_rows()
    ledger: HeartbeatAlertLedger
    factory: sessionmaker[Session] | None
    try:
        from app.db.session import get_session_factory

        factory = get_session_factory() if rows is not None else None
    except Exception:
        factory = None
    if factory is None:
        ledger = MemoryHeartbeatAlertLedger()
    else:
        ledger = RuntimeStatusHeartbeatLedger(factory)
    row = None if rows is None else rows.get(PAPER_WORKER_COMPONENT)
    observation = project_paper_worker_heartbeat(row, now=observed_at, stale_after_seconds=limit)
    alert_raised = raise_heartbeat_alert(settings, observation, now=observed_at, ledger=ledger)
    arming = _arming_refusal(rows)
    arming_alert = False
    if arming.refused and factory is not None:
        arming_alert = raise_arming_refusal_alert(
            settings,
            arming,
            now=observed_at,
            ledger=RuntimeStatusHeartbeatLedger(
                factory,
                component=ARMING_ALERT_COMPONENT,
            ),
        )
    expected = rows is not None and exit_monitor_expected(rows.get(PAPER_ARMING_COMPONENT))
    exit_status, exit_observation = exit_monitor_liveness_status(
        expected=expected,
        row=None if rows is None else rows.get(PAPER_EXIT_MONITOR_COMPONENT),
        now=observed_at,
        stale_after_seconds=limit,
    )
    exit_alert = False
    if expected and factory is not None:
        exit_alert = raise_prepared_alert(
            settings,
            exit_monitor_heartbeat_alert(exit_observation, exit_status),
            now=observed_at,
            ledger=RuntimeStatusHeartbeatLedger(
                factory,
                component=EXIT_MONITOR_ALERT_COMPONENT,
            ),
        )
    from app.services.risk.kill_switch import (
        kill_switch_retry_should_schedule,
        read_paper_worker_kill_switch,
        retry_unsent_kill_switch_alert,
    )

    if kill_switch_retry_should_schedule(rows, now=observed_at):
        background_tasks.add_task(retry_unsent_kill_switch_alert, settings)
    return PaperWorkerHeartbeatResponse(
        status=heartbeat_status(observation),
        observation=observation,
        alert_raised=alert_raised,
        arming_refusal=arming,
        arming_alert_raised=arming_alert,
        exit_monitor_status=exit_status,
        exit_monitor=exit_observation,
        exit_monitor_alert_raised=exit_alert,
        kill_switch=read_paper_worker_kill_switch(settings),
        worker_effective=_worker_effective(settings, now=observed_at),
        timestamp=observed_at,
    )


def readiness_projection(
    statuses: list[ProviderStatus],
    *,
    fail_closed: bool,
) -> tuple[bool, str, int, tuple[str, ...]]:
    """Return ``(ready, status, unavailable, degraded_providers)``.

    ``ready`` stays true while every provider is reachable. Nothing in the
    staging checks reads ``status``. ``degraded_providers`` names each
    non-mock market-data provider that is degraded without a fallback, so a
    banned Binance primary is visible without failing the probe or restarting
    Render.
    """

    unavailable = sum(1 for item in statuses if item.health is ProviderHealth.UNAVAILABLE)
    if fail_closed:
        # Staging/production: authoritative LLM/embeddings/vector must not be
        # silently degraded onto mocks or in-memory substitutes.
        critical = {"llm", "embeddings", "vector"}
        unavailable += sum(
            1
            for item in statuses
            if item.kind.value in critical
            and (item.is_mock or (item.health is ProviderHealth.DEGRADED and item.using_fallback))
        )
    degraded = tuple(
        item.name
        for item in statuses
        if item.kind is ProviderKind.MARKET_DATA
        and item.health is ProviderHealth.DEGRADED
        and not item.is_mock
        and not item.using_fallback
    )
    ready = unavailable == 0
    label = "ready" if ready and not degraded else "degraded"
    return ready, label, unavailable, degraded


@router.get("/health/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def readiness(registry: ProviderRegistryDep, settings: SettingsDep) -> ReadinessResponse:
    from app.core.provider_policy import provider_fail_closed

    statuses = registry.statuses()
    ready, label, unavailable, degraded = readiness_projection(
        statuses, fail_closed=provider_fail_closed(settings)
    )
    return ReadinessResponse(
        status=label,
        ready=ready,
        providers_total=len(statuses),
        providers_unavailable=unavailable,
        degraded_providers=list(degraded),
        timestamp=datetime.now(UTC),
    )
