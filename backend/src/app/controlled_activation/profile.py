"""One paper package. Defaults stay disarmed. Production cannot arm it."""

from __future__ import annotations

from app.core.config import Settings, TelegramInboundMode


def _paper_exchange_allowed(settings: Settings) -> bool:
    """Internal paper, or the governed staging BloFin demo worker gate.

    Demo mode counts only when the governed worker arm is pinned and the full
    BloFin execution gate holds (paper, real trading off, allowlisted demo host,
    demo credentials). ``trade_live`` and any other value never qualify.
    """

    mode = settings.exchange_mode.value
    if mode == "paper_internal":
        return True
    if mode != "paper_exchange_demo":
        return False
    from app.core.execution_credentials import (
        blofin_execution_authorized,
        governed_demo_worker_access_requested,
    )

    return governed_demo_worker_access_requested(settings) and blofin_execution_authorized(settings)


def _paper_staging_market(settings: Settings) -> bool:
    return (
        settings.paper_runtime_environment
        and settings.execution_mode.value == "paper"
        and not settings.enable_real_trading
        and not settings.real_trading_enabled
        and _paper_exchange_allowed(settings)
        and settings.perpetual_evidence_source == "binance_usdm"
        and not settings.telegram_alerts_enabled
        and not settings.automatic_telegram_delivery_enabled
        and not settings.telegram_webhook_secret.strip()
    )


def controlled_telegram_projection(settings: Settings) -> bool:
    """Staging paper projection onto a verified binding. Polling only.

    Requires the Watcher paper arm, public USD-M evidence, network permission,
    and a bot token. Webhook is not a staging activation mode. Legacy Telegram
    alerts and automatic delivery stay off. This does not mint a Candidate.
    """

    from app.market_activation.profile import controlled_watcher_arm

    if not _paper_staging_market(settings) or not controlled_watcher_arm(settings):
        return False
    if settings.telegram_inbound_mode is not TelegramInboundMode.POLLING:
        return False
    if not settings.telegram_network_permitted or not settings.telegram_bot_token.strip():
        return False
    if not settings.telegram_bot_id.strip() or not settings.telegram_chat_id.strip():
        return False
    return settings.telegram_paper_activation_armed and settings.telegram_interaction_enabled


def telegram_enrollment_runtime(settings: Settings) -> bool:
    """Staging enrollment posture. The Watcher can keep scanning.

    Polling, network, bot id, and bot token are set together. Projection is
    not armed, so there is no step where the hook is required and the network
    is off. Mutually exclusive with :func:`controlled_telegram_projection`.
    """

    if controlled_telegram_projection(settings) or not _paper_staging_market(settings):
        return False
    if settings.telegram_paper_activation_armed:
        return False
    if settings.telegram_inbound_mode is not TelegramInboundMode.POLLING:
        return False
    return (
        settings.telegram_network_permitted
        and settings.telegram_interaction_enabled
        and bool(settings.telegram_bot_id.strip())
        and bool(settings.telegram_bot_token.strip())
    )


def package_disarmed(settings: Settings) -> bool:
    """True when the process is back on replay with Watcher and Telegram off."""

    return (
        settings.execution_mode.value == "paper"
        and not settings.enable_real_trading
        and not settings.real_trading_enabled
        and settings.perpetual_evidence_source == "replay"
        and not settings.watcher_orchestration_enabled
        and not settings.watcher_paper_staging_activation
        and not settings.market_watcher_enabled
        and not settings.telegram_alerts_enabled
        and not settings.telegram_interaction_enabled
        and not settings.automatic_telegram_delivery_enabled
        and not settings.telegram_paper_activation_armed
        and settings.telegram_inbound_mode is TelegramInboundMode.OFF
        and not settings.telegram_network_permitted
        and not settings.telegram_webhook_secret.strip()
    )
