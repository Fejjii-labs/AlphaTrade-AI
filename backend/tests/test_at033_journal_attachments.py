"""AT-033 — journal trade attachments (DB-backed, size/MIME/quota capped)."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    AuditLog,
    JournalTradeAttachment,
    JournalTradeEvidence,
    Membership,
    Organization,
    User,
)
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import AuditEventType, MembershipRole
from app.security.passwords import hash_password
from app.security.rate_limit import reset_rate_limiter

ORG_A = uuid.UUID("00000000-0000-0000-0000-000000033201")
ORG_B = uuid.UUID("00000000-0000-0000-0000-000000033202")
USER_A = uuid.UUID("00000000-0000-0000-0000-000000033211")
USER_B = uuid.UUID("00000000-0000-0000-0000-000000033212")
VIEWER_A = uuid.UUID("00000000-0000-0000-0000-000000033213")
OTHER_TRADER_A = uuid.UUID("00000000-0000-0000-0000-000000033214")

_BASE = {
    "environment": "local",
    "log_json": False,
    "execution_mode": "paper",
    "enable_real_trading": False,
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "journal-attach-test-secret-abc-32ch",
    "rate_limit_use_redis": False,
    "access_token_denylist_use_redis": False,
    "provider_mode": "mock",
    "market_data_provider": "mock",
    "alert_delivery_enabled": False,
    "telegram_alerts_enabled": False,
    "worker_enabled": False,
    "market_watcher_enabled": False,
}

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    reset_rate_limiter()


@contextmanager
def _build_client(
    **settings_overrides: object,
) -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
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
    settings = Settings(**{**_BASE, **settings_overrides})

    with factory() as session:
        session.add(Organization(id=ORG_A, name="Attach Org A"))
        session.add(Organization(id=ORG_B, name="Attach Org B"))
        for user_id, email in (
            (USER_A, "attach-a@test.example"),
            (USER_B, "attach-b@test.example"),
            (VIEWER_A, "attach-viewer@test.example"),
        ):
            session.add(
                User(
                    id=user_id,
                    email=email,
                    hashed_password=hash_password("SecurePass123!", settings),
                    email_verified=True,
                )
            )
        session.flush()
        session.add(Membership(user_id=USER_A, organization_id=ORG_A, role=MembershipRole.OWNER))
        session.add(Membership(user_id=USER_B, organization_id=ORG_B, role=MembershipRole.OWNER))
        session.add(Membership(user_id=VIEWER_A, organization_id=ORG_A, role=MembershipRole.VIEWER))
        session.commit()

    app = create_app(settings=settings)

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session

    with TestClient(app) as test_client:
        yield test_client, factory

    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    with _build_client() as pair:
        yield pair


def _seed_other_trader_in_org_a(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        if session.get(User, OTHER_TRADER_A) is not None:
            return
        settings = Settings(**_BASE)
        session.add(
            User(
                id=OTHER_TRADER_A,
                email="attach-other-trader-a@test.example",
                hashed_password=hash_password("SecurePass123!", settings),
                email_verified=True,
            )
        )
        session.flush()
        session.add(
            Membership(
                user_id=OTHER_TRADER_A,
                organization_id=ORG_A,
                role=MembershipRole.TRADER,
            )
        )
        session.commit()


def _auth(client: TestClient, email: str, password: str = "SecurePass123!") -> dict[str, str]:
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["tokens"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create_trade(client: TestClient, headers: dict[str, str]) -> str:
    resp = client.post(
        "/journal/trades",
        json={
            "source": "manual",
            "status": "planned",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "direction": "long",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _upload(
    client: TestClient,
    headers: dict[str, str],
    trade_id: str,
    *,
    filename: str = "chart.png",
    content: bytes = _PNG,
    content_type: str = "image/png",
    caption: str | None = "Entry screenshot",
):
    data = {"caption": caption} if caption is not None else {}
    return client.post(
        f"/journal/trades/{trade_id}/attachments",
        files={"file": (filename, content, content_type)},
        data=data,
        headers=headers,
    )


# --------------------------------------------------------------------------- #
# Upload / download round-trip
# --------------------------------------------------------------------------- #


def test_upload_download_roundtrip_with_evidence_and_audit(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "attach-a@test.example")
    trade_id = _create_trade(test_client, headers)

    resp = _upload(test_client, headers, trade_id)
    assert resp.status_code == 201, resp.text
    meta = resp.json()
    assert meta["filename"] == "chart.png"
    assert meta["content_type"] == "image/png"
    assert meta["size_bytes"] == len(_PNG)
    assert meta["sha256"] == hashlib.sha256(_PNG).hexdigest()
    assert meta["storage_backend"] == "db"

    download = test_client.get(f"/journal/attachments/{meta['id']}/content", headers=headers)
    assert download.status_code == 200
    assert download.content == _PNG
    assert download.headers["content-type"].startswith("image/png")
    assert 'filename="chart.png"' in download.headers["content-disposition"]

    with factory() as session:
        evidence = session.scalars(
            select(JournalTradeEvidence).where(
                JournalTradeEvidence.ref == f"attachment:{meta['id']}"
            )
        ).all()
        assert len(evidence) == 1
        assert evidence[0].kind.value == "screenshot"
        audit = session.scalars(
            select(AuditLog).where(AuditLog.action == AuditEventType.JOURNAL_ATTACHMENT_ADDED)
        ).all()
        assert len(audit) == 1
        assert audit[0].resource_id == meta["id"]


def test_pdf_upload_creates_file_evidence(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "attach-a@test.example")
    trade_id = _create_trade(test_client, headers)
    resp = _upload(
        test_client,
        headers,
        trade_id,
        filename="notes.pdf",
        content=b"%PDF-1.4 test",
        content_type="application/pdf",
    )
    assert resp.status_code == 201
    with factory() as session:
        evidence = session.scalars(select(JournalTradeEvidence)).one()
        assert evidence.kind.value == "file"


def test_list_attachments(client: tuple[TestClient, sessionmaker[Session]]) -> None:
    test_client, _ = client
    headers = _auth(test_client, "attach-a@test.example")
    trade_id = _create_trade(test_client, headers)
    _upload(test_client, headers, trade_id)
    _upload(test_client, headers, trade_id, filename="exit.png")

    resp = test_client.get(f"/journal/trades/{trade_id}/attachments", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert [item["filename"] for item in body["items"]] == ["chart.png", "exit.png"]


def test_delete_removes_attachment_and_evidence(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "attach-a@test.example")
    trade_id = _create_trade(test_client, headers)
    meta = _upload(test_client, headers, trade_id).json()

    resp = test_client.delete(f"/journal/attachments/{meta['id']}", headers=headers)
    assert resp.status_code == 204
    assert (
        test_client.get(f"/journal/attachments/{meta['id']}/content", headers=headers).status_code
        == 404
    )

    with factory() as session:
        assert session.scalars(select(JournalTradeAttachment)).all() == []
        assert session.scalars(select(JournalTradeEvidence)).all() == []
        audit = session.scalars(
            select(AuditLog).where(AuditLog.action == AuditEventType.JOURNAL_ATTACHMENT_DELETED)
        ).all()
        assert len(audit) == 1


def test_filename_is_sanitized_to_basename(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, _ = client
    headers = _auth(test_client, "attach-a@test.example")
    trade_id = _create_trade(test_client, headers)
    resp = _upload(test_client, headers, trade_id, filename="../../etc/passwd.png")
    assert resp.status_code == 201
    assert resp.json()["filename"] == "passwd.png"


# --------------------------------------------------------------------------- #
# RBAC & tenant isolation
# --------------------------------------------------------------------------- #


def test_viewer_cannot_access_other_users_attachments(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, _ = client
    trader = _auth(test_client, "attach-a@test.example")
    viewer = _auth(test_client, "attach-viewer@test.example")
    owner_trade_id = _create_trade(test_client, trader)
    owner_meta = _upload(test_client, trader, owner_trade_id).json()

    assert _upload(test_client, viewer, owner_trade_id).status_code == 403
    assert (
        test_client.delete(f"/journal/attachments/{owner_meta['id']}", headers=viewer).status_code
        == 403
    )
    assert (
        test_client.get(f"/journal/trades/{owner_trade_id}/attachments", headers=viewer).status_code
        == 404
    )
    assert (
        test_client.get(
            f"/journal/attachments/{owner_meta['id']}/content", headers=viewer
        ).status_code
        == 404
    )


def test_cross_org_access_fails_closed(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, _ = client
    headers_a = _auth(test_client, "attach-a@test.example")
    headers_b = _auth(test_client, "attach-b@test.example")
    trade_id = _create_trade(test_client, headers_a)
    meta = _upload(test_client, headers_a, trade_id).json()

    assert _upload(test_client, headers_b, trade_id).status_code == 404
    assert (
        test_client.get(f"/journal/trades/{trade_id}/attachments", headers=headers_b).status_code
        == 404
    )
    assert (
        test_client.get(f"/journal/attachments/{meta['id']}/content", headers=headers_b).status_code
        == 404
    )
    assert (
        test_client.delete(f"/journal/attachments/{meta['id']}", headers=headers_b).status_code
        == 404
    )


def test_attachment_routes_require_trade_owner(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    _seed_other_trader_in_org_a(factory)
    headers_owner = _auth(test_client, "attach-a@test.example")
    headers_other_org = _auth(test_client, "attach-b@test.example")
    headers_other_user = _auth(test_client, "attach-other-trader-a@test.example")
    trade_id = _create_trade(test_client, headers_owner)
    meta = _upload(test_client, headers_owner, trade_id).json()

    list_owner = test_client.get(f"/journal/trades/{trade_id}/attachments", headers=headers_owner)
    assert list_owner.status_code == 200
    download_owner = test_client.get(
        f"/journal/attachments/{meta['id']}/content", headers=headers_owner
    )
    assert download_owner.status_code == 200
    assert _upload(test_client, headers_other_org, trade_id).status_code == 404
    assert _upload(test_client, headers_other_user, trade_id).status_code == 404
    list_other_org = test_client.get(
        f"/journal/trades/{trade_id}/attachments", headers=headers_other_org
    )
    assert list_other_org.status_code == 404
    list_other_user = test_client.get(
        f"/journal/trades/{trade_id}/attachments", headers=headers_other_user
    )
    assert list_other_user.status_code == 404
    assert (
        test_client.get(
            f"/journal/attachments/{meta['id']}/content", headers=headers_other_org
        ).status_code
        == 404
    )
    assert (
        test_client.get(
            f"/journal/attachments/{meta['id']}/content", headers=headers_other_user
        ).status_code
        == 404
    )

    with factory() as session:
        assert len(session.scalars(select(JournalTradeAttachment)).all()) == 1
        assert len(session.scalars(select(JournalTradeEvidence)).all()) == 1


def test_same_org_trader_cannot_delete_others_attachment(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    _seed_other_trader_in_org_a(factory)
    headers_owner = _auth(test_client, "attach-a@test.example")
    headers_other_trader = _auth(test_client, "attach-other-trader-a@test.example")
    trade_id = _create_trade(test_client, headers_owner)
    meta = _upload(test_client, headers_owner, trade_id).json()

    denied = test_client.delete(f"/journal/attachments/{meta['id']}", headers=headers_other_trader)
    assert denied.status_code == 404
    assert denied.json()["error"]["message"] == "Attachment not found."

    with factory() as session:
        attachment = session.get(JournalTradeAttachment, uuid.UUID(meta["id"]))
        assert attachment is not None
        evidence = session.scalars(select(JournalTradeEvidence)).all()
        assert len(evidence) == 1


def test_swapped_attachment_ids_between_owners_return_same_not_found(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    _seed_other_trader_in_org_a(factory)
    headers_owner = _auth(test_client, "attach-a@test.example")
    headers_other_trader = _auth(test_client, "attach-other-trader-a@test.example")
    owner_trade_id = _create_trade(test_client, headers_owner)
    other_trade_id = _create_trade(test_client, headers_other_trader)
    owner_meta = _upload(test_client, headers_owner, owner_trade_id).json()
    other_meta = _upload(test_client, headers_other_trader, other_trade_id).json()

    missing = test_client.get(f"/journal/attachments/{uuid.uuid4()}/content", headers=headers_owner)
    assert missing.status_code == 404
    assert missing.json()["error"]["message"] == "Attachment not found."

    cross_download = test_client.get(
        f"/journal/attachments/{other_meta['id']}/content", headers=headers_owner
    )
    assert cross_download.status_code == 404
    assert cross_download.json()["error"]["message"] == "Attachment not found."

    swapped_download = test_client.get(
        f"/journal/attachments/{owner_meta['id']}/content", headers=headers_other_trader
    )
    assert swapped_download.status_code == 404
    assert swapped_download.json()["error"]["message"] == "Attachment not found."

    cross_delete = test_client.delete(
        f"/journal/attachments/{owner_meta['id']}", headers=headers_other_trader
    )
    assert cross_delete.status_code == 404
    assert cross_delete.json()["error"]["message"] == "Attachment not found."


def test_upload_to_missing_trade_404(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, _ = client
    headers = _auth(test_client, "attach-a@test.example")
    resp = _upload(test_client, headers, str(uuid.uuid4()))
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Limits (fail closed)
# --------------------------------------------------------------------------- #


def test_rejects_disallowed_content_type(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "attach-a@test.example")
    trade_id = _create_trade(test_client, headers)
    resp = _upload(
        test_client,
        headers,
        trade_id,
        filename="payload.svg",
        content=b"<svg/>",
        content_type="image/svg+xml",
    )
    assert resp.status_code == 422
    with factory() as session:
        assert session.scalars(select(JournalTradeAttachment)).all() == []


def test_rejects_empty_content(client: tuple[TestClient, sessionmaker[Session]]) -> None:
    test_client, _ = client
    headers = _auth(test_client, "attach-a@test.example")
    trade_id = _create_trade(test_client, headers)
    resp = _upload(test_client, headers, trade_id, content=b"")
    assert resp.status_code == 422


def test_rejects_oversized_content() -> None:
    with _build_client(journal_attachment_max_bytes=1024) as (test_client, _factory):
        headers = _auth(test_client, "attach-a@test.example")
        trade_id = _create_trade(test_client, headers)
        resp = _upload(test_client, headers, trade_id, content=b"\x89PNG" + b"\x00" * 2048)
        assert resp.status_code == 422
        assert "maximum size" in resp.json()["error"]["message"]


def test_enforces_per_trade_quota() -> None:
    with _build_client(journal_attachment_max_per_trade=1) as (test_client, _factory):
        headers = _auth(test_client, "attach-a@test.example")
        trade_id = _create_trade(test_client, headers)
        assert _upload(test_client, headers, trade_id).status_code == 201
        second = _upload(test_client, headers, trade_id, filename="second.png")
        assert second.status_code == 422
        assert "limit reached" in second.json()["error"]["message"]
