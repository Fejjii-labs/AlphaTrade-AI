"""Activation preflight. A passing report does not arm or send anything."""

from __future__ import annotations

from app.core.config import Environment, ExchangeMode, ExecutionMode, Settings, TelegramInboundMode
from app.telegram_activation.binding import (
    assess_recipient,
    recipient_is_bound,
    recipient_is_isolated,
    recipient_is_private,
)
from app.telegram_activation.contracts import (
    ActivationVerdict,
    InboundSourceKind,
    PreflightReport,
)
from app.telegram_activation.policy import WEBHOOK_SECRET_MIN_LENGTH
from app.telegram_paper_agent.contracts import PaperAlertRecipient
from app.telegram_security.persistence import TelegramSecurityStore


def defaults_are_safe(settings: Settings) -> bool:
    """True when process defaults cannot deliver or arm Telegram."""
    return (
        settings.execution_mode is ExecutionMode.PAPER
        and not settings.enable_real_trading
        and not settings.real_trading_enabled
        and settings.exchange_mode is not ExchangeMode.TRADE_LIVE
        and not settings.telegram_alerts_enabled
        and not settings.telegram_interaction_enabled
        and not settings.automatic_telegram_delivery_enabled
        and not settings.telegram_paper_activation_armed
        and settings.telegram_inbound_mode is TelegramInboundMode.OFF
        and not settings.telegram_network_permitted
        and not settings.telegram_webhook_secret.strip()
        and not settings.watcher_orchestration_enabled
        and not settings.market_watcher_enabled
    )


def configuration_blockers(settings: Settings) -> tuple[str, ...]:
    """Settings gates.

    Local arming is unchanged. Staging may arm only the controlled paper
    package. Production stays forbidden. A partial staging flag set is not
    armable.
    """
    from app.controlled_activation.profile import controlled_telegram_projection

    blockers: list[str] = []
    projection = controlled_telegram_projection(settings)
    if settings.environment is Environment.PRODUCTION and not settings.production_demo_active:
        blockers.append("environment_forbidden")
    elif settings.environment is not Environment.LOCAL and not projection:
        if settings.paper_runtime_environment:
            blockers.append("staging_package_incomplete")
        else:
            blockers.append("environment_not_local")
    if settings.execution_mode is not ExecutionMode.PAPER:
        blockers.append("execution_mode_not_paper")
    if settings.enable_real_trading or settings.real_trading_enabled:
        blockers.append("real_trading_enabled")
    if settings.exchange_mode is ExchangeMode.TRADE_LIVE:
        blockers.append("exchange_mode_live")
    if not settings.telegram_paper_activation_armed:
        blockers.append("activation_not_armed")
    if not settings.telegram_interaction_enabled:
        blockers.append("interaction_disabled")
    if settings.telegram_alerts_enabled:
        blockers.append("legacy_telegram_alerts_enabled")
    if settings.automatic_telegram_delivery_enabled:
        blockers.append("automatic_delivery_enabled")
    if settings.telegram_inbound_mode is TelegramInboundMode.OFF:
        blockers.append("inbound_mode_off")
    if (
        settings.telegram_inbound_mode is TelegramInboundMode.WEBHOOK
        and len(settings.telegram_webhook_secret) < WEBHOOK_SECRET_MIN_LENGTH
    ):
        blockers.append("webhook_secret_too_short")
    if (
        settings.telegram_network_permitted
        and settings.environment is not Environment.LOCAL
        and not projection
    ):
        blockers.append("network_not_local")
    return tuple(blockers)


def configuration_warnings(settings: Settings) -> tuple[str, ...]:
    warnings: list[str] = []
    if settings.watcher_orchestration_enabled:
        warnings.append("watcher_orchestration_enabled")
    if settings.market_watcher_enabled:
        warnings.append("market_watcher_enabled")
    if settings.telegram_network_permitted:
        warnings.append("network_permitted")
    return tuple(warnings)


def verdict_for(*, runtime_armable: bool, blockers: tuple[str, ...]) -> ActivationVerdict:
    if runtime_armable:
        return ActivationVerdict.ARMABLE
    if "activation_not_armed" in blockers or "environment_not_local" in blockers:
        return ActivationVerdict.NOT_ARMED
    return ActivationVerdict.BLOCKED


def run_preflight(
    *,
    settings: Settings,
    store: TelegramSecurityStore | None = None,
    recipient: PaperAlertRecipient | None = None,
    protocol_enabled: bool = False,
    outbound_ready: bool = False,
    source_kind: InboundSourceKind = InboundSourceKind.REFUSING,
    webhook_mounted: bool = False,
    inbound_cursor_present: bool = False,
) -> PreflightReport:
    """Evaluate arming gates. Does not mutate settings, bindings, or outbox rows."""
    blockers = list(configuration_blockers(settings))
    warnings = list(configuration_warnings(settings))
    if recipient is None or store is None:
        blockers.append("recipient_binding_missing")
    else:
        blockers.extend(assess_recipient(store, recipient))
    if not protocol_enabled:
        blockers.append("protocol_disabled")
    if not outbound_ready:
        blockers.append("outbound_transport_disabled")
    if settings.telegram_inbound_mode is TelegramInboundMode.POLLING:
        if source_kind is InboundSourceKind.REFUSING:
            blockers.append("polling_source_disabled")
        if source_kind is InboundSourceKind.HTTP and not settings.telegram_network_permitted:
            blockers.append("polling_network_not_permitted")
    if source_kind is InboundSourceKind.HTTP and not settings.telegram_network_permitted:
        blockers.append("http_source_without_network_permit")
    if source_kind is InboundSourceKind.RECORDED:
        warnings.append("inbound_source_is_test_double")
    if source_kind is InboundSourceKind.REFUSING and not settings.telegram_network_permitted:
        warnings.append("network_disabled")
    unique = tuple(dict.fromkeys(blockers))
    bound = recipient_is_bound(unique)
    isolated = recipient_is_isolated(unique, bound)
    armable = not unique
    return PreflightReport(
        runtime_armable=armable,
        defaults_safe=defaults_are_safe(settings),
        verdict=verdict_for(runtime_armable=armable, blockers=unique),
        blockers=unique,
        warnings=tuple(dict.fromkeys(warnings)),
        environment=settings.environment.value,
        execution_mode=settings.execution_mode.value,
        real_trading_enabled=settings.real_trading_enabled,
        enable_real_trading=settings.enable_real_trading,
        exchange_mode=settings.exchange_mode.value,
        watcher_orchestration_enabled=settings.watcher_orchestration_enabled,
        market_watcher_enabled=settings.market_watcher_enabled,
        telegram_alerts_enabled=settings.telegram_alerts_enabled,
        telegram_interaction_enabled=settings.telegram_interaction_enabled,
        automatic_telegram_delivery_enabled=settings.automatic_telegram_delivery_enabled,
        telegram_paper_activation_armed=settings.telegram_paper_activation_armed,
        telegram_inbound_mode=settings.telegram_inbound_mode.value,
        telegram_network_permitted=settings.telegram_network_permitted,
        webhook_mounted=webhook_mounted,
        recipient_bound=bound,
        private_chat=recipient_is_private(unique, bound),
        tenant_isolated=isolated,
        inbound_cursor_present=inbound_cursor_present,
    )
