"""Disarmed paper-worker boot.

Dedicated Watcher and Telegram processes, and the supervised paper worker that
hosts both, recognize a disarmed staging posture before Settings requires
PostgreSQL, Redis, JWT, provider credentials, or a Telegram bot token. The
recognition is process-local. It is not an environment variable and the API
never sets it.

Armed workers leave the role unset, so global deployment validation stays
mandatory. A disarmed role does not defer those checks when the constructed
settings are actually armed.
"""

from __future__ import annotations

import os
import signal
import threading
from contextvars import ContextVar
from enum import StrEnum
from types import FrameType

import structlog

from app.core.config import (
    Environment,
    ExchangeMode,
    ExecutionMode,
    Settings,
    TelegramInboundMode,
)

logger = structlog.get_logger("core.disarmed_worker_boot")

_TRUE_TOKENS = frozenset({"1", "true", "yes", "on", "y", "t"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off", "n", "f"})
_DISARMED_FLAGS = (
    "WATCHER_ORCHESTRATION_ENABLED",
    "WATCHER_PAPER_STAGING_ACTIVATION",
    "MARKET_WATCHER_ENABLED",
    "MARKET_WATCHER_BRIDGE_ENABLED",
    "MARKET_WATCHER_BRIDGE_AUTO_TICK",
    "TELEGRAM_ALERTS_ENABLED",
    "TELEGRAM_INTERACTION_ENABLED",
    "AUTOMATIC_TELEGRAM_DELIVERY_ENABLED",
    "TELEGRAM_PAPER_ACTIVATION_ARMED",
    "TELEGRAM_NETWORK_PERMITTED",
)


# Telegram is not on the trade path. When only these flags conflict, the
# supervised paper worker boots with Telegram off instead of crash-looping.
# The bot token and chat id stay, so safety alerts can still be sent.
_TELEGRAM_FAIL_SOFT_ENV: dict[str, str] = {
    "TELEGRAM_ALERTS_ENABLED": "false",
    "AUTOMATIC_TELEGRAM_DELIVERY_ENABLED": "false",
    "TELEGRAM_INTERACTION_ENABLED": "false",
    "TELEGRAM_PAPER_ACTIVATION_ARMED": "false",
    "TELEGRAM_INBOUND_MODE": "off",
    "TELEGRAM_NETWORK_PERMITTED": "false",
    "TELEGRAM_WEBHOOK_SECRET": "",
}
TELEGRAM_CONFIG_CONFLICT = "telegram_disabled_config_conflict"
_BOOT_DEGRADATIONS: list[str] = []


def worker_boot_degradations() -> tuple[str, ...]:
    """Features this process turned off at boot instead of failing."""

    return tuple(_BOOT_DEGRADATIONS)


class WorkerBootRole(StrEnum):
    """Paper processes that may idle while disarmed."""

    WATCHER_PAPER = "watcher_paper"
    TELEGRAM_PAPER = "telegram_paper"
    PAPER_WORKER = "paper_worker"


_DISARMED_ROLES = frozenset(
    {
        WorkerBootRole.WATCHER_PAPER,
        WorkerBootRole.TELEGRAM_PAPER,
        WorkerBootRole.PAPER_WORKER,
    }
)


_ROLE: ContextVar[WorkerBootRole | None] = ContextVar("disarmed_worker_boot_role", default=None)


def reset_disarmed_worker_boot() -> None:
    """Clear the process-local role. Tests use this between cases."""

    _ROLE.set(None)
    _BOOT_DEGRADATIONS.clear()


def bind_worker_boot_role(role: WorkerBootRole | None) -> None:
    """Bind the process-local role. Worker startup and tests are the callers."""

    _ROLE.set(role)


def defer_operational_dependencies(settings: Settings) -> bool:
    """True only while a dedicated worker is constructing disarmed settings.

    Callers other than worker startup, including the API, leave the role unset.
    Armed settings never defer, even if a role was bound first.
    """

    role = _ROLE.get()
    if role not in _DISARMED_ROLES:
        return False
    return settings_are_disarmed_paper_worker(settings)


def settings_are_disarmed_paper_worker(settings: Settings) -> bool:
    """Staging paper posture with Watcher and Telegram arms off and no bot secret."""

    return (
        settings.environment is Environment.STAGING
        and settings.execution_mode is ExecutionMode.PAPER
        and not settings.enable_real_trading
        and not settings.real_trading_enabled
        and settings.exchange_mode is ExchangeMode.PAPER_INTERNAL
        and not settings.watcher_orchestration_enabled
        and not settings.watcher_paper_staging_activation
        and not settings.market_watcher_enabled
        and not settings.market_watcher_bridge_enabled
        and not settings.market_watcher_bridge_auto_tick
        and not settings.telegram_alerts_enabled
        and not settings.telegram_interaction_enabled
        and not settings.automatic_telegram_delivery_enabled
        and not settings.telegram_paper_activation_armed
        and settings.telegram_inbound_mode is TelegramInboundMode.OFF
        and not settings.telegram_network_permitted
        and not settings.telegram_bot_token.strip()
        and not settings.telegram_webhook_secret.strip()
    )


def environ_is_disarmed_worker_boot() -> bool:
    """Read the process environment before Settings applies secret requirements.

    Invalid or armed values return false so startup uses global validation.
    """

    if _env_text("ENVIRONMENT", default="local") != Environment.STAGING.value:
        return False
    if _env_text("EXECUTION_MODE", default=ExecutionMode.PAPER.value) != ExecutionMode.PAPER.value:
        return False
    if _env_text("EXCHANGE_MODE", default=ExchangeMode.PAPER_INTERNAL.value) != (
        ExchangeMode.PAPER_INTERNAL.value
    ):
        return False
    if _env_flag("ENABLE_REAL_TRADING", default=False) is not False:
        return False
    if _env_text("TELEGRAM_INBOUND_MODE", default=TelegramInboundMode.OFF.value) != (
        TelegramInboundMode.OFF.value
    ):
        return False
    if _env_text("TELEGRAM_BOT_TOKEN", default=""):
        return False
    if _env_text("TELEGRAM_WEBHOOK_SECRET", default=""):
        return False
    return all(_env_flag(name, default=False) is False for name in _DISARMED_FLAGS)


def load_worker_process_settings(role: WorkerBootRole) -> Settings:
    """Construct Settings for a dedicated worker.

    Disarmed startup binds the role first, then builds Settings. Armed startup
    does not bind it, so missing operational dependencies fail closed.
    """

    from app.core.config import get_settings

    if role not in _DISARMED_ROLES:
        raise ValueError("unknown worker boot role")
    get_settings.cache_clear()
    disarmed = environ_is_disarmed_worker_boot()
    _ROLE.set(role if disarmed else None)
    try:
        settings = get_settings()
    except Exception as exc:
        _ROLE.set(None)
        get_settings.cache_clear()
        if disarmed or role is not WorkerBootRole.PAPER_WORKER:
            raise
        return _settings_with_telegram_disabled(exc)
    if disarmed and not settings_are_disarmed_paper_worker(settings):
        _ROLE.set(None)
        get_settings.cache_clear()
        return get_settings()
    return settings


def _settings_with_telegram_disabled(original: Exception) -> Settings:
    """Retry once with Telegram off. Every validator still runs on the retry.

    Staging only. When nothing Telegram-related is set, or the retry still
    fails (a trade-path or dependency error), the original error is raised.
    """

    from app.core.config import get_settings

    staging = _env_text("ENVIRONMENT", default="local") == Environment.STAGING.value
    changed = {
        name: value
        for name, value in _TELEGRAM_FAIL_SOFT_ENV.items()
        if _env_text(name, default=value) != value
    }
    if not staging or not changed:
        raise original
    saved = {name: os.environ.get(name) for name in changed}
    os.environ.update(changed)
    try:
        settings = get_settings()
    except Exception:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        get_settings.cache_clear()
        raise original from None
    _BOOT_DEGRADATIONS.append(TELEGRAM_CONFIG_CONFLICT)
    logger.error(
        "paper_worker_telegram_disabled_config_conflict",
        disabled=sorted(changed),
        paper_only=True,
    )
    return settings


def idle_disarmed_until_signal(settings: Settings, *, component: str) -> None:
    """Block until SIGINT or SIGTERM. Does not open PostgreSQL, Redis, or providers."""

    stop = threading.Event()

    def _handle(_signum: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    logger.warning(
        "worker_disarmed_idle",
        component=component,
        posture="disarmed",
        paper_only=True,
        enable_real_trading=settings.enable_real_trading,
        real_trading_enabled=settings.real_trading_enabled,
        execution_mode=settings.execution_mode.value,
    )
    while not stop.is_set():
        stop.wait(settings.watcher_paper_poll_interval_seconds)


def _env_text(name: str, *, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower()


def _env_flag(name: str, *, default: bool) -> bool | None:
    """Parse a boolean env value. None means the value is present but invalid."""

    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    token = raw.strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    return None
