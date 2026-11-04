"""Pure deterministic Telegram policy evaluation. No I/O or trading effects."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from app.schemas.telegram_policy import (
    AlertPhase,
    NotificationEventType,
    NotificationSeverity,
    TelegramNotificationEvent,
    TelegramNotificationPolicyV2,
)

SEVERITY_RANK = {severity: rank for rank, severity in enumerate(NotificationSeverity)}
EVENT_TOGGLES = {
    NotificationEventType.RISK: "risk_alerts",
    NotificationEventType.SFP_BLOCKED_BY_RISK: "risk_alerts",
    NotificationEventType.PAPER_TRADE_OPENED: "paper_trade_opened",
    NotificationEventType.PAPER_TRADE_CLOSED: "paper_trade_closed",
    NotificationEventType.STOP: "stop_event",
    NotificationEventType.PARTIAL_PROFIT: "partial_profit_event",
    NotificationEventType.DAILY_REVIEW: "daily_review_event",
}


@dataclass(frozen=True)
class PolicyHistory:
    event: TelegramNotificationEvent
    accepted_at: datetime


TERMINAL_SETUP_EVENTS = frozenset(
    {NotificationEventType.SFP_INVALIDATED, NotificationEventType.SFP_EXPIRED}
)


def cooldown_scope(event: TelegramNotificationEvent) -> tuple:
    return (event.strategy_id, event.symbol, event.event_type, event.setup_stage, event.phase)


def evaluate_telegram_policy(
    policy: TelegramNotificationPolicyV2,
    event: TelegramNotificationEvent,
    *,
    now: datetime,
    history: Iterable[PolicyHistory] = (),
) -> str | None:
    """First failing rule wins; None permits. Mandatory risk bypasses preferences."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Policy clock must be timezone-aware.")
    if event.mandatory_risk:
        return None
    for field, fact in (
        ("strategy_subscriptions", event.strategy_id),
        ("symbol_subscriptions", event.symbol),
        ("setup_stages", event.setup_stage),
        ("event_types", event.event_type),
        ("severities", event.severity),
    ):
        subscribed = getattr(policy, field)
        if subscribed is not None and fact not in subscribed:
            return f"POLICY_{field.upper()}"
    if SEVERITY_RANK[event.severity] < SEVERITY_RANK[policy.minimum_severity]:
        return "POLICY_MINIMUM_SEVERITY"
    if event.event_type in TERMINAL_SETUP_EVENTS and policy.event_types is None:
        # Cancelled/expired setups are stored and audited but not pushed unless
        # the recipient explicitly subscribes to these event types.
        return "POLICY_TERMINAL_EVENT_MUTED"
    toggle = EVENT_TOGGLES.get(event.event_type)
    if toggle is not None and not getattr(policy, toggle):
        return "POLICY_EVENT_DISABLED"
    if (
        event.event_type == NotificationEventType.SETUP
        and event.phase is None
        and (not policy.forming_alerts or not policy.confirmed_alerts)
    ):
        return "POLICY_PHASE_MISSING"
    if event.phase == AlertPhase.FORMING and not policy.forming_alerts:
        return "POLICY_FORMING_DISABLED"
    if event.phase == AlertPhase.CONFIRMED and not policy.confirmed_alerts:
        return "POLICY_CONFIRMED_DISABLED"
    if policy.minimum_quality is not None:
        if event.quality is None:
            return "POLICY_QUALITY_MISSING"
        if event.quality < policy.minimum_quality:
            return "POLICY_MINIMUM_QUALITY"
    if policy.quiet_hours is not None and policy.quiet_hours.contains(now):
        return "POLICY_QUIET_HOURS"
    history = tuple(history)
    for prior in history:
        age = (now - prior.accepted_at).total_seconds()
        if age < 0:
            continue
        if (
            age < policy.duplicate_suppression_seconds
            and prior.event.duplicate_key == event.duplicate_key
        ):
            return "POLICY_DUPLICATE"
    for prior in history:
        age = (now - prior.accepted_at).total_seconds()
        if 0 <= age < policy.cooldown_seconds and cooldown_scope(prior.event) == cooldown_scope(
            event
        ):
            return "POLICY_COOLDOWN"
    return None
