"""Persisted deterministic notification receipts; no provider or transport sends."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.db.models import Organization, User
from app.db.strategy_brain import BrainSetupEventRow, BrainSetupRow
from app.db.telegram_security import TelegramOutboxRow
from app.db.watcher_orchestration import (
    WatcherPolicyVersionRow,
    WatcherScanAttemptRow,
    WatcherScanLineageRow,
    WatcherSourceFetchAttemptRow,
    WatcherSubscriptionEvalAttemptRow,
)
from app.schemas.nested_continuation import NestedContinuationSpec
from app.schemas.telegram_policy import (
    NotificationEventType,
    NotificationSeverity,
    TelegramNotificationEvent,
)
from app.strategy_brain.agent import read_brain
from app.strategy_brain.notification_diagnostics import ROW_LIMIT, read_notification_diagnostics
from app.strategy_brain.records import record_detections
from app.strategy_brain.service import create_template, overview
from tests.test_sfp_detector import spec as sfp_spec
from tests.test_strategy_brain_nested import bars, detection
from tests.test_strategy_brain_nested import tenant_store as sqlite_tenant_store  # noqa: F401


@pytest.fixture(params=("sqlite", "postgres"))
def tenant_store(request, sqlite_tenant_store):  # noqa: F811 — pytest fixture injection
    if request.param == "sqlite":
        yield sqlite_tenant_store
        return
    from tests.support.postgres_persistence import phase7_plan_session_factory, postgres_available

    if not postgres_available():
        pytest.skip("Diagnostic PostgreSQL JSON attribution needs disposable PostgreSQL")
    factory = phase7_plan_session_factory()
    org, user = uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Organization(id=org, name="Notification receipt fixture"),
                User(id=user, email=f"{user}@example.com", hashed_password="unused"),
            ]
        )
        session.commit()
        yield session, org, user
    factory.kw["bind"].dispose()


def world(store, *, sfp=False):
    session, org, user = store
    spec = sfp_spec() if sfp else NestedContinuationSpec(symbol="BTCUSDT")
    template = create_template(session, organization_id=org, user_id=user, spec=spec)
    series = bars()
    now = series[-1].interval_end + timedelta(seconds=5)
    if not sfp:
        record_detections(
            session,
            organization_id=org,
            strategy_id=template["strategy_id"],
            version_id=template["version_id"],
            spec=spec,
            bars=series,
            events=detection(series),
            evaluated_at=now,
            evidence_hash="a" * 64,
        )
    session.commit()
    return session, org, user, template, now


def outbox(
    org,
    user,
    version,
    now,
    *,
    event_type=NotificationEventType.SETUP,
    state="SUPPRESSED",
    reason="POLICY_QUALITY_MISSING",
):
    event = TelegramNotificationEvent(
        event_type=event_type,
        severity=NotificationSeverity.INFO,
        strategy_id=version,
        symbol="BTCUSDT",
        occurred_at=now,
        duplicate_key=uuid4().hex,
    )
    return TelegramOutboxRow(
        outbox_id=uuid4(),
        organization_id=org,
        user_id=user,
        bot_id="private-bot",
        chat_id="private-chat",
        idempotency_key=uuid4().hex,
        kind="PRIVATE_MESSAGE",
        text="private notification payload",
        notification_event=event.model_dump(mode="json"),
        state=state,
        attempt=0,
        last_error=reason,
        created_at=now,
        updated_at=now,
    )


def report(session, org, user, now):
    strategies = overview(session, organization_id=org, user_id=user, now=now)["strategies"]
    return read_notification_diagnostics(
        session, organization_id=org, user_id=user, strategies=strategies, now=now
    )


def test_detection_is_distinct_from_missing_acquisition_and_delivery_receipts(tenant_store):
    session, org, user, _, now = world(tenant_store)
    result = report(session, org, user, now)
    scope = result["scopes"][0]
    assert scope["detection_states"]["CONFIRMED"] > 0
    assert scope["scan_status_counts"] == {}
    assert scope["delivery_states"] == {}
    assert scope["candidate_links"] == 0
    assert "detection_receipts_missing" not in scope["missing_evidence"]
    assert "acquisition_receipts_missing" in scope["missing_evidence"]
    assert "missing rows do not prove no detection" in result["limitations"][0]


def test_actual_receipt_stages_preserve_canonical_refusal_and_policy_rejection(tenant_store):
    session, org, user, template, now = world(tenant_store)
    policy_id, lineage_id, attempt_id = uuid4(), uuid4(), uuid4()
    session.add(
        WatcherPolicyVersionRow(
            policy_id=policy_id,
            version=1,
            organization_id=org,
            user_id=user,
            watchlist_item_id=uuid4(),
            schema_version="test-receipt/v1",
            timeframe="15m",
            strategy_version_id=template["version_id"],
            created_by=user,
            created_at=now,
            content_hash="b" * 64,
            enabled=True,
        )
    )
    session.add(
        WatcherScanLineageRow(
            lineage_id=lineage_id,
            organization_id=org,
            principal_id=user,
            scan_scope="test",
            request_hash="c" * 64,
            policy_id=policy_id,
            policy_version=1,
            policy_content_hash="b" * 64,
            trigger_created_by="manual",
            created_at=now,
        )
    )
    session.flush()
    session.add(
        WatcherScanAttemptRow(
            attempt_id=attempt_id,
            lineage_id=lineage_id,
            organization_id=org,
            scan_scope="test",
            attempt_number=1,
            trigger="manual",
            mode="persist_evidence",
            status="failed",
            started_at=now,
            heartbeat_at=now,
            finished_at=now,
            recovery_disposition="none",
            outcome_reason_code="canonical_coverage_proof_coverage_incomplete",
            sanitized_error="private provider payload must not leave the read",
        )
    )
    session.flush()
    session.add(
        WatcherSourceFetchAttemptRow(
            fetch_attempt_id=uuid4(),
            scan_attempt_id=attempt_id,
            lineage_id=lineage_id,
            organization_id=org,
            scan_scope="test",
            subject_id=uuid4(),
            status="failed",
            reason_code="canonical_contract_invalid_contract",
            created_at=now,
            finished_at=now,
        )
    )
    session.add(
        WatcherSubscriptionEvalAttemptRow(
            subscription_attempt_id=uuid4(),
            scan_attempt_id=attempt_id,
            lineage_id=lineage_id,
            organization_id=org,
            scan_scope="test",
            subject_id=uuid4(),
            status="failed",
            reason_code="canonical_contract_invalid_contract",
            created_at=now,
            finished_at=now,
        )
    )
    row = outbox(org, user, template["version_id"], now)
    session.add(row)
    session.commit()
    before = session.scalar(select(func.count()).select_from(TelegramOutboxRow))
    scope = report(session, org, user, now)["scopes"][0]
    assert scope["scan_reasons"] == {"canonical_coverage_proof_coverage_incomplete": 1}
    assert scope["acquisition_reasons"] == {"canonical_contract_invalid_contract": 1}
    assert scope["evaluation_reasons"] == {"canonical_contract_invalid_contract": 1}
    assert scope["delivery_states"] == {"SUPPRESSED": 1}
    assert scope["deliveries"][0]["reason"] == "POLICY_QUALITY_MISSING"
    assert scope["deliveries"][0]["provider_receipt_present"] is False
    assert session.scalar(select(func.count()).select_from(TelegramOutboxRow)) == before
    assert "private" not in json.dumps(scope)


def test_sfp_delivery_facts_are_informational_and_missing_detections_remain_unknown(tenant_store):
    session, org, user, template, now = world(tenant_store, sfp=True)
    row = outbox(
        org,
        user,
        template["version_id"],
        now,
        event_type=NotificationEventType.SFP_CONFIRMED,
        state="SENT",
        reason=None,
    )
    row.sent_at = now
    row.transport_message_id = "private-transport-message"
    session.add(row)
    session.commit()
    result = report(session, org, user, now)
    scope = result["scopes"][0]
    assert scope["family"] == "sfp"
    assert scope["delivery_states"] == {"SENT": 1}
    assert scope["deliveries"][0]["event_type"] == "SFP_CONFIRMED"
    assert scope["deliveries"][0]["provider_receipt_present"] is True
    assert "detection_receipts_missing" in scope["missing_evidence"]
    assert "private-transport-message" not in json.dumps(result)
    assert "not proof a human read" in result["limitations"][2]


def test_receipt_window_caps_and_tenant_user_isolation(tenant_store):
    session, org, user, template, now = world(tenant_store)
    foreign_user = uuid4()
    session.add(
        User(id=foreign_user, email=f"{foreign_user}@example.com", hashed_password="unused")
    )
    session.flush()
    session.add_all([outbox(org, user, template["version_id"], now) for _ in range(ROW_LIMIT + 1)])
    session.add(outbox(org, foreign_user, template["version_id"], now, reason="private"))
    session.add(outbox(org, user, template["version_id"], now + timedelta(seconds=1)))
    session.add(outbox(org, user, template["version_id"], now - timedelta(days=8)))
    session.commit()
    scope = report(session, org, user, now)["scopes"][0]
    assert scope["delivery_states"] == {"SUPPRESSED": ROW_LIMIT}
    assert scope["truncated"]["delivery"]
    strategies = overview(session, organization_id=org, user_id=user, now=now)["strategies"]
    assert (
        read_notification_diagnostics(
            session, organization_id=org, user_id=foreign_user, strategies=strategies, now=now
        )["scopes"]
        == []
    )
    assert (
        read_notification_diagnostics(
            session, organization_id=uuid4(), user_id=user, strategies=strategies, now=now
        )["scopes"]
        == []
    )


def test_agent_notification_question_keeps_precise_read_only_receipts(tenant_store):
    session, org, user, template, _ = world(tenant_store)
    # Agent uses its current seven-day window; retain only current delivery metadata.
    from datetime import UTC, datetime

    session.add(outbox(org, user, template["version_id"], datetime.now(UTC)))
    session.commit()
    text, _, limitations = read_brain(
        session,
        organization_id=org,
        user_id=user,
        message="Why are Nested Telegram notifications missing?",
    )
    assert "Notification diagnostics nested" in text
    assert "POLICY_QUALITY_MISSING" in text
    assert "delivery={'SUPPRESSED': 1}" in text
    assert "component coverage proofs" in text
    assert any("Persisted samples only" in item for item in limitations)
    assert not session.new and not session.dirty
    assert session.scalar(select(func.count()).select_from(BrainSetupEventRow)) > 0
    assert session.scalar(select(func.count()).select_from(BrainSetupRow)) > 0
