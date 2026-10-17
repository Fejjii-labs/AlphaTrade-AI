"""Slice 24 — usage tracking, cost sources, and organization quotas."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.models import AuditLog, Membership, Organization, User
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import CostSource, MembershipRole
from app.schemas.usage import OrganizationQuotaUpdate, UsageEventCreate
from app.services.quota_service import QuotaService
from app.services.usage_cost import build_provider_metadata, resolve_usage_cost
from app.services.usage_service import UsageService

_PASSWORD = "secure-password-1"


@pytest.fixture
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        org = Organization(name="Usage Org")
        owner = User(email="owner@usage.test", hashed_password="hash")
        viewer = User(email="viewer@usage.test", hashed_password="hash")
        session.add_all([org, owner, viewer])
        session.flush()
        session.add(Membership(user_id=owner.id, organization_id=org.id, role=MembershipRole.OWNER))
        session.add(
            Membership(user_id=viewer.id, organization_id=org.id, role=MembershipRole.VIEWER)
        )
        session.commit()
        session.info["org_id"] = org.id
        session.info["owner_id"] = owner.id
        session.info["viewer_id"] = viewer.id
        yield session
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def api_client(db_session: Session) -> Iterator[TestClient]:
    settings = Settings(execution_mode="paper", enable_real_trading=False, log_json=False)
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = lambda: db_session
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _register(client: TestClient, email: str, org: str | None = None) -> str:
    org_name = org or f"Org-{email.split('@')[0]}"
    response = client.post(
        "/auth/register",
        json={"email": email, "password": _PASSWORD, "organization_name": org_name},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["tokens"]["access_token"]


def test_provider_reported_usage_metadata_captured(db_session: Session) -> None:
    service = UsageService(db_session)
    event = service.record(
        UsageEventCreate(
            request_id="req-provider",
            feature="agent_chat",
            provider="openai-llm",
            model="gpt-4o-mini",
            input_tokens=100,
            output_tokens=50,
            provider_metadata=build_provider_metadata(
                input_tokens=100,
                output_tokens=50,
                provider_reported_cost=Decimal("0.00042"),
            ),
        )
    )
    assert event.cost_source is CostSource.PROVIDER_REPORTED
    assert event.is_billing_grade is True
    assert event.provider_reported_cost == Decimal("0.00042")


def test_fallback_usage_metadata_captured(db_session: Session) -> None:
    service = UsageService(db_session)
    event = service.record(
        UsageEventCreate(
            request_id="req-fallback",
            feature="agent_chat",
            provider="mock-llm",
            model="gpt-4o-mini",
            input_tokens=80,
            output_tokens=20,
            fallback_used=True,
            provider_metadata=build_provider_metadata(
                input_tokens=80,
                output_tokens=20,
                cost_source=CostSource.STATIC_ESTIMATED,
                fallback_used=True,
            ),
        )
    )
    assert event.fallback_used is True
    assert event.cost_source is CostSource.STATIC_ESTIMATED
    assert event.is_billing_grade is False


def test_static_estimated_cost_marked_non_billing_grade() -> None:
    resolved = resolve_usage_cost(
        model="gpt-4o-mini",
        input_tokens=1000,
        output_tokens=500,
        provider_metadata={"cost_source": "static_estimated"},
    )
    assert resolved.cost_source is CostSource.STATIC_ESTIMATED
    assert resolved.is_billing_grade is False


def test_usage_summary_by_feature(db_session: Session) -> None:
    org_id = db_session.info["org_id"]
    service = UsageService(db_session)
    for feature in ("agent_chat", "rag_ingest"):
        service.record(
            UsageEventCreate(
                request_id=f"req-{feature}",
                organization_id=org_id,
                feature=feature,
                input_tokens=100,
                output_tokens=10,
                provider_metadata={"cost_source": "static_estimated"},
            )
        )
    breakdown = service.summarize_by_feature(organization_id=org_id)
    features = {row.feature for row in breakdown}
    assert "agent_chat" in features
    assert "rag_ingest" in features


def test_usage_summary_by_provider(db_session: Session) -> None:
    org_id = db_session.info["org_id"]
    service = UsageService(db_session)
    service.record(
        UsageEventCreate(
            request_id="req-openai",
            organization_id=org_id,
            feature="agent_chat",
            provider="openai-llm",
            input_tokens=50,
            provider_metadata={"cost_source": "tokenizer_estimated", "input_tokens": 50},
        )
    )
    breakdown = service.summarize_by_provider(organization_id=org_id)
    assert any(row.provider == "openai-llm" for row in breakdown)


def test_quota_soft_warning(db_session: Session) -> None:
    org_id = db_session.info["org_id"]
    quota_service = QuotaService(db_session)
    quota_service.update_quota(
        org_id,
        OrganizationQuotaUpdate(
            monthly_token_limit=100,
            soft_warning_threshold=Decimal("0.50"),
            hard_block_threshold=Decimal("1.00"),
        ),
    )
    UsageService(db_session).record(
        UsageEventCreate(
            request_id="req-soft",
            organization_id=org_id,
            feature="agent_chat",
            input_tokens=60,
            output_tokens=0,
            provider_metadata={"cost_source": "static_estimated"},
        )
    )
    result = quota_service.check_feature(org_id, "agent_chat")
    assert result.allowed is True
    assert result.soft_warning is True


def test_quota_hard_block_emits_audit(db_session: Session) -> None:
    org_id = db_session.info["org_id"]
    quota_service = QuotaService(db_session)
    quota_service.update_quota(
        org_id,
        OrganizationQuotaUpdate(
            limit_agent_chat=1,
            soft_warning_threshold=Decimal("0.50"),
            hard_block_threshold=Decimal("1.00"),
        ),
    )
    UsageService(db_session).record(
        UsageEventCreate(
            request_id="req-block-1",
            organization_id=org_id,
            feature="agent_chat",
            input_tokens=1,
            provider_metadata={"cost_source": "unavailable"},
        )
    )
    result = quota_service.check_feature(org_id, "agent_chat", request_id="req-block-2")
    assert result.hard_blocked is True
    rows = list(db_session.scalars(select(AuditLog)).all())
    assert any(r.action.value == "quota_block" for r in rows)


def test_owner_can_update_quota(api_client: TestClient) -> None:
    token = _register(api_client, "owner-quota@example.com")
    response = api_client.patch(
        "/usage/quota",
        headers={"Authorization": f"Bearer {token}"},
        json={"monthly_token_limit": 500000},
    )
    assert response.status_code == 200, response.text
    assert response.json()["quota"]["monthly_token_limit"] == 500000


def test_viewer_cannot_update_quota(api_client: TestClient, db_session: Session) -> None:
    from app.core.config import Settings
    from app.security.passwords import hash_password

    settings = Settings()
    token = _register(api_client, "owner-quota2@example.com")
    me = api_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    org_id = uuid.UUID(me["organization"]["id"])

    viewer = User(
        email="viewer-same-org@example.com",
        hashed_password=hash_password(_PASSWORD, settings),
    )
    db_session.add(viewer)
    db_session.flush()
    db_session.add(
        Membership(user_id=viewer.id, organization_id=org_id, role=MembershipRole.VIEWER)
    )
    db_session.commit()

    login = api_client.post(
        "/auth/login",
        json={"email": "viewer-same-org@example.com", "password": _PASSWORD},
    )
    viewer_token = login.json()["tokens"]["access_token"]

    response = api_client.patch(
        "/usage/quota",
        headers={"Authorization": f"Bearer {viewer_token}"},
        json={"monthly_token_limit": 1},
    )
    assert response.status_code == 403


def test_chat_blocked_when_quota_exceeded(api_client: TestClient, db_session: Session) -> None:
    token = _register(api_client, "blocked-chat@example.com")
    me = api_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    org_id = uuid.UUID(me["organization"]["id"])

    quota_service = QuotaService(db_session)
    quota_service.update_quota(
        org_id,
        OrganizationQuotaUpdate(
            limit_agent_chat=0,
            hard_block_threshold=Decimal("0.00"),
        ),
    )
    # Independent turn phases read committed policy, as the quota API persists it.
    db_session.commit()

    response = api_client.post(
        "/chat/message",
        headers={"Authorization": f"Bearer {token}"},
        json={"message": "analyze btc"},
    )
    assert response.status_code == 429


def test_knowledge_ingest_blocked_when_quota_exceeded(
    api_client: TestClient, db_session: Session
) -> None:
    token = _register(api_client, "blocked-ingest@example.com")
    me = api_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    org_id = uuid.UUID(me["organization"]["id"])

    QuotaService(db_session).update_quota(
        org_id,
        OrganizationQuotaUpdate(limit_rag_ingest=0, hard_block_threshold=Decimal("0.00")),
    )

    response = api_client.post(
        "/knowledge/ingest",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "Test doc",
            "text": "Risk management requires stop losses.",
            "source_type": "trading_playbook",
        },
    )
    assert response.status_code == 429


def test_tenant_isolation_for_usage(api_client: TestClient) -> None:
    token_a = _register(api_client, "tenant-a@example.com", org="Org A")
    token_b = _register(api_client, "tenant-b@example.com", org="Org B")

    api_client.post(
        "/chat/message",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"message": "hello from org a"},
    )

    events_a = api_client.get("/usage/events", headers={"Authorization": f"Bearer {token_a}"})
    events_b = api_client.get("/usage/events", headers={"Authorization": f"Bearer {token_b}"})
    assert events_a.status_code == 200
    assert events_b.status_code == 200
    assert events_a.json()["total"] >= 1
    org_a = events_a.json()["items"][0]["organization_id"]
    for item in events_b.json()["items"]:
        assert item.get("organization_id") != org_a or item is None
