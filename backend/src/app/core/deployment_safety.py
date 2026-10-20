"""Deployment safety checks for staging and production environments.

Validates invariants at settings load time so the service fails fast rather than
starting with unsafe configuration. Local and Docker Compose development are
unaffected unless ``ENVIRONMENT`` is set to ``staging`` or ``production``.
"""

from __future__ import annotations

from app.core.config import Environment, ExchangeMode, ExecutionMode, Settings
from app.market_activation.profile import REPLAY_MODES, perpetual_evidence_health

_LOCALHOST_MARKERS = ("localhost", "127.0.0.1")
_WEAK_JWT_SECRETS = frozenset(
    {
        "dev-only-change-me-before-production",
        "change-me-in-production-use-long-random-value",
        "changeme",
        "secret",
    }
)
_VALID_SAMESITE = frozenset({"lax", "strict", "none"})


def _url_uses_localhost(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in _LOCALHOST_MARKERS)


def _cookie_secure_resolved(settings: Settings) -> bool:
    if settings.auth_cookie_secure is not None:
        return settings.auth_cookie_secure
    return settings.environment is not Environment.LOCAL


def validate_deployment_settings(settings: Settings) -> None:
    """Raise ``ValueError`` when staging/production invariants are violated."""
    if settings.environment not in (Environment.STAGING, Environment.PRODUCTION):
        return

    errors: list[str] = []

    if settings.enable_real_trading:
        errors.append("enable_real_trading must be false in staging/production")
    if settings.real_trading_enabled:
        errors.append("real_trading_enabled must be false in staging/production")
    if settings.execution_mode is ExecutionMode.TRADE:
        errors.append("execution_mode=trade is not allowed in staging/production")
    if settings.execution_mode is not ExecutionMode.PAPER:
        errors.append("execution_mode must be paper in staging/production")

    # Legacy scanner, bridge, and Telegram stay off. Production Watcher stays off.
    # Staging may construct an armed paper-monitoring pair; preflight still
    # refuses to scan until live evidence, lineage, and migrations are healthy.
    if settings.market_watcher_enabled:
        errors.append("market_watcher_enabled must be false in staging/production")
    if settings.market_watcher_bridge_enabled:
        errors.append("market_watcher_bridge_enabled must be false in staging/production")
    if settings.market_watcher_bridge_auto_tick:
        errors.append("market_watcher_bridge_auto_tick must be false in staging/production")
    errors.extend(_watcher_activation_errors(settings))
    errors.extend(_telegram_activation_errors(settings))

    # The demo exchange is allowed in staging only (for validation), never in
    # production. ``trade_live`` is rejected globally by exchange_safety.
    if (
        settings.exchange_mode is ExchangeMode.PAPER_EXCHANGE_DEMO
        and settings.environment is Environment.PRODUCTION
    ):
        errors.append("exchange_mode=paper_exchange_demo is not allowed in production")
    if settings.environment is Environment.PRODUCTION:
        if settings.autonomous_internal_paper_enabled:
            errors.append("autonomous_internal_paper_enabled must be false in production")
        if settings.autonomous_blofin_demo_enabled:
            errors.append("autonomous_blofin_demo_enabled must be false in production")
        if settings.paper_exit_monitor_enabled:
            errors.append("paper_exit_monitor_enabled must be false in production")

    # Dedicated disarmed workers bind a process-local role before this runs.
    # The API never binds it. Armed settings ignore it and keep every check below.
    from app.core.disarmed_worker_boot import defer_operational_dependencies

    defer_ops = defer_operational_dependencies(settings)

    if not defer_ops and settings.jwt_secret.strip().lower() in _WEAK_JWT_SECRETS:
        errors.append("jwt_secret is a known weak placeholder; use a long random value")

    if not settings.auth_refresh_cookie_enabled:
        errors.append("auth_refresh_cookie_enabled must be true in staging/production")

    if not _cookie_secure_resolved(settings):
        errors.append("auth_cookie_secure must be true in staging/production (HTTPS only)")

    samesite = settings.auth_cookie_samesite.strip().lower()
    if samesite not in _VALID_SAMESITE:
        errors.append(f"auth_cookie_samesite must be one of {sorted(_VALID_SAMESITE)}")
    elif samesite == "none" and not _cookie_secure_resolved(settings):
        errors.append("auth_cookie_samesite=none requires auth_cookie_secure=true")

    if not defer_ops:
        errors.extend(_operational_dependency_errors(settings))

    provider_mode = settings.provider_mode.strip().lower()
    if provider_mode == "mock":
        errors.append(
            "provider_mode=mock is not allowed in staging/production (AT-013 fail-closed)"
        )

    if not settings.cors_origins:
        errors.append("cors_origins must include the deployed frontend URL(s)")
    else:
        for origin in settings.cors_origins:
            lowered = origin.lower().rstrip("/")
            if not lowered.startswith("https://"):
                errors.append(f"cors_origins must use HTTPS in staging/production (got: {origin})")
            elif _url_uses_localhost(lowered):
                errors.append(
                    f"cors_origins must not use localhost in staging/production (got: {origin})"
                )

    if not settings.rate_limit_use_redis:
        errors.append("rate_limit_use_redis must be true in staging/production")
    if settings.rate_limit_allow_in_memory_fallback:
        errors.append(
            "rate_limit_allow_in_memory_fallback must be false in staging/production "
            "(AT-018: rate limiting requires shared Redis state)"
        )
    if settings.auth_register_rate_limit != 10:
        errors.append(
            "auth_register_rate_limit must stay 10 in staging/production "
            "(the higher budget is only for the disposable local e2e server)"
        )

    if not settings.access_token_denylist_enabled:
        errors.append("access_token_denylist_enabled must be true in staging/production")
    if not settings.access_token_denylist_use_redis:
        errors.append("access_token_denylist_use_redis must be true in staging/production")
    if not settings.access_token_denylist_fail_closed:
        errors.append("access_token_denylist_fail_closed must be true in staging/production")

    if settings.trusted_proxy_hops < 1:
        errors.append(
            "trusted_proxy_hops must be >= 1 in staging/production (AT-018: requests "
            "arrive via a trusted reverse proxy; 0 would rate-limit all clients as one)"
        )

    if settings.environment is Environment.PRODUCTION and settings.debug:
        errors.append("debug must be false in production")

    if errors:
        joined = "; ".join(errors)
        raise ValueError(f"deployment safety check failed: {joined}")


def _operational_dependency_errors(settings: Settings) -> list[str]:
    """PostgreSQL, Redis, provider, and Qdrant requirements for active staging.

    Disarmed worker startup does not call this. The API and armed workers do.
    """

    errors: list[str] = []
    if not settings.database_url.strip():
        errors.append("database_url is required in staging/production")
    elif _url_uses_localhost(settings.database_url):
        errors.append("database_url must point to managed Postgres (not localhost)")

    if not settings.redis_url.strip():
        errors.append("redis_url is required in staging/production")
    elif _url_uses_localhost(settings.redis_url):
        errors.append("redis_url must point to managed Redis (not localhost)")

    if not settings.openai_api_key.strip():
        errors.append("openai_api_key is required in staging/production (AT-013 fail-closed)")

    qdrant = settings.qdrant_url.strip()
    if not qdrant:
        # Staging and production both require hosted Qdrant for authoritative RAG.
        errors.append("qdrant_url is required in staging/production (AT-013 fail-closed)")
    elif _url_uses_localhost(qdrant):
        errors.append("qdrant_url must point to hosted Qdrant (not localhost)")
    return errors


def deployment_posture(settings: Settings) -> dict[str, object]:
    """Return a redaction-safe summary for startup logs (no secrets)."""
    return {
        "environment": settings.environment.value,
        "execution_mode": settings.execution_mode.value,
        "real_trading_enabled": settings.real_trading_enabled,
        "enable_real_trading": settings.enable_real_trading,
        "exchange_mode": settings.exchange_mode.value,
        "exchange_demo_active": settings.exchange_demo_active,
        "blofin_demo_configured": settings.blofin_demo_configured,
        "provider_mode": settings.provider_mode,
        "auth_refresh_cookie_enabled": settings.auth_refresh_cookie_enabled,
        "auth_cookie_secure": _cookie_secure_resolved(settings),
        "auth_cookie_samesite": settings.auth_cookie_samesite,
        "rate_limit_use_redis": settings.rate_limit_use_redis,
        "rate_limit_allow_in_memory_fallback": settings.rate_limit_allow_in_memory_fallback,
        "access_token_denylist_enabled": settings.access_token_denylist_enabled,
        "access_token_denylist_fail_closed": settings.access_token_denylist_fail_closed,
        "trusted_proxy_hops": settings.trusted_proxy_hops,
        "cors_origin_count": len(settings.cors_origins),
        "log_json": settings.log_json,
        "debug": settings.debug,
        "openai_configured": bool(settings.openai_api_key.strip()),
        "qdrant_configured": bool(settings.qdrant_url.strip()),
        "qdrant_api_key_configured": bool(settings.qdrant_api_key.strip()),
        "embeddings_model": settings.embeddings_model,
        "embeddings_dimensions": settings.embeddings_dimensions,
        "telegram_alerts_enabled": settings.telegram_alerts_enabled,
        "telegram_interaction_enabled": settings.telegram_interaction_enabled,
        "automatic_telegram_delivery_enabled": settings.automatic_telegram_delivery_enabled,
        "telegram_paper_activation_armed": settings.telegram_paper_activation_armed,
        "telegram_inbound_mode": settings.telegram_inbound_mode.value,
        "telegram_network_permitted": settings.telegram_network_permitted,
        "telegram_webhook_secret_configured": bool(settings.telegram_webhook_secret.strip()),
        "market_watcher_enabled": settings.market_watcher_enabled,
        "market_watcher_bridge_enabled": settings.market_watcher_bridge_enabled,
        "watcher_orchestration_enabled": settings.watcher_orchestration_enabled,
        "watcher_paper_staging_activation": settings.watcher_paper_staging_activation,
        "autonomous_internal_paper_enabled": settings.autonomous_internal_paper_enabled,
        "autonomous_blofin_demo_enabled": settings.autonomous_blofin_demo_enabled,
        "paper_exit_monitor_enabled": settings.paper_exit_monitor_enabled,
        "paper_exit_monitor_dry_run": settings.paper_exit_monitor_dry_run,
        **dict(perpetual_evidence_health(settings)),
    }


def _telegram_activation_errors(settings: Settings) -> list[str]:
    """Refuse Telegram except the staging paper projection on the Watcher package.

    Alerts and automatic delivery stay refused in every deployed environment.
    Incomplete arming flags keep the original error text so a partial flag
    cannot start. Production cannot select the package.
    """

    errors: list[str] = []
    if settings.telegram_alerts_enabled:
        errors.append("telegram_alerts_enabled must be false in staging/production")
    if settings.automatic_telegram_delivery_enabled:
        errors.append("automatic_telegram_delivery_enabled must be false in staging/production")
    from app.controlled_activation.profile import (
        controlled_telegram_projection,
        telegram_enrollment_runtime,
    )

    if controlled_telegram_projection(settings) or telegram_enrollment_runtime(settings):
        return errors
    if settings.telegram_interaction_enabled:
        errors.append("telegram_interaction_enabled must be false in staging/production")
    if settings.telegram_paper_activation_armed:
        errors.append("telegram_paper_activation_armed must be false in staging/production")
    if settings.telegram_inbound_mode.value != "off":
        errors.append("telegram_inbound_mode must be off in staging/production")
    if settings.telegram_network_permitted:
        errors.append("telegram_network_permitted must be false in staging/production")
    if settings.telegram_webhook_secret.strip():
        errors.append("telegram_webhook_secret must be empty in staging/production")
    return errors


def _watcher_activation_errors(settings: Settings) -> list[str]:
    """Refuse every Watcher arm except disarmed, or staging paper with live evidence."""

    errors: list[str] = []
    armed = settings.watcher_paper_staging_activation
    orchestration = settings.watcher_orchestration_enabled
    if settings.environment is Environment.PRODUCTION:
        if armed:
            errors.append("watcher_paper_staging_activation must be false in production")
        if orchestration:
            errors.append("watcher_orchestration_enabled must be false in staging/production")
        return errors
    if orchestration and not armed:
        errors.append("watcher_orchestration_enabled must be false in staging/production")
    elif armed and not orchestration:
        errors.append(
            "watcher_paper_staging_activation requires watcher_orchestration_enabled "
            "for paper monitoring only"
        )
    elif armed and orchestration:
        source = settings.perpetual_evidence_source.strip().lower()
        if source in REPLAY_MODES:
            errors.append(
                "watcher paper activation refuses replay evidence while live canonical "
                "evidence is required"
            )
    return errors
