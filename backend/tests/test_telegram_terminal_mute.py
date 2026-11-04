"""Behavioural tests: invalidated/expired SFP pushes are muted by default but stored."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.schemas.telegram_policy import (
    NotificationEventType,
    NotificationSeverity,
    TelegramNotificationEvent,
    TelegramNotificationPolicyV2,
)
from app.services.notifications.telegram_policy import evaluate_telegram_policy
from app.telegram_security.contracts import OutboxState
from tests.test_telegram_notification_policy_v2 import World

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
TERMINAL = (NotificationEventType.SFP_INVALIDATED, NotificationEventType.SFP_EXPIRED)


def sfp_event(event_type: NotificationEventType, severity=NotificationSeverity.INFO):
    return TelegramNotificationEvent.model_validate(
        {
            "event_type": event_type,
            "severity": severity,
            "strategy_id": uuid4(),
            "symbol": "ZECUSDT",
            "duplicate_key": f"sfp-{event_type.value}-{uuid4()}",
            "occurred_at": NOW,
        }
    )


@pytest.mark.parametrize("event_type", TERMINAL)
def test_terminal_sfp_is_muted_by_default(event_type):
    reason = evaluate_telegram_policy(
        TelegramNotificationPolicyV2(), sfp_event(event_type), now=NOW
    )
    assert reason == "POLICY_TERMINAL_EVENT_MUTED"


@pytest.mark.parametrize("event_type", TERMINAL)
def test_explicit_event_type_subscription_unmutes(event_type):
    policy = TelegramNotificationPolicyV2(event_types=(event_type,))
    assert evaluate_telegram_policy(policy, sfp_event(event_type), now=NOW) is None


@pytest.mark.parametrize(
    "event_type",
    [NotificationEventType.SFP_CONFIRMED, NotificationEventType.SFP_BLOCKED_BY_RISK],
)
def test_non_terminal_sfp_still_delivers(event_type):
    event = sfp_event(event_type, severity=NotificationSeverity.WATCH)
    assert evaluate_telegram_policy(TelegramNotificationPolicyV2(), event, now=NOW) is None


def test_below_minimum_severity_is_suppressed():
    policy = TelegramNotificationPolicyV2(minimum_severity=NotificationSeverity.WATCH)
    event = sfp_event(NotificationEventType.SFP_CONFIRMED, severity=NotificationSeverity.INFO)
    assert evaluate_telegram_policy(policy, event, now=NOW) == "POLICY_MINIMUM_SEVERITY"


@pytest.mark.parametrize("event_type", TERMINAL)
def test_muted_terminal_event_is_stored_as_suppressed_and_never_sent(event_type):
    w = World()
    row = w.enqueue("terminal", notification=sfp_event(event_type))
    assert row.state == OutboxState.SUPPRESSED
    assert row.last_error == "POLICY_TERMINAL_EVENT_MUTED"
    assert w.store.get_outbox(row.outbox_id).state == OutboxState.SUPPRESSED
    w.protocol.deliver_pending()
    assert w.transport.sent == []


@pytest.mark.parametrize("event_type", TERMINAL)
def test_subscribed_terminal_event_is_queued(event_type):
    w = World()
    w.policy = TelegramNotificationPolicyV2(event_types=(event_type,))
    row = w.enqueue("terminal", notification=sfp_event(event_type))
    assert row.state == OutboxState.PENDING
