"""PostgreSQL concurrent confirmation for presented strategy drafts."""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.models import Membership, Organization, User
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import MembershipRole
from app.security.passwords import hash_password
from tests.support.postgres_persistence import (
    POSTGRES_URL,
    phase7_plan_session_factory,
    requires_postgres,
)
from tests.test_strategy_conversation_foundation import (
    ORG_A,
    PASSWORD,
    USER_A,
    _confirm_payload,
    _create_strategy,
)

ORG = ORG_A
USER = USER_A


@pytest.fixture
def postgres_conv_env() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    factory = phase7_plan_session_factory()
    settings = Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url=POSTGRES_URL,
        jwt_secret="conversation-postgres-test-secret",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        provider_mode="mock",
        market_data_provider="mock",
    )
    with factory() as session:
        session.add(Organization(id=ORG, name="Conv PG Org"))
        session.add(
            User(
                id=USER,
                email="conv-pg@test.example",
                hashed_password=hash_password(PASSWORD, settings),
                email_verified=True,
            )
        )
        session.flush()
        session.add(Membership(user_id=USER, organization_id=ORG, role=MembershipRole.OWNER))
        session.commit()

    app = create_app(settings=settings)

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as client:
        yield client, factory
    app.dependency_overrides.clear()


@requires_postgres
def test_postgres_concurrent_confirmation_converges_to_one_version(
    postgres_conv_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = postgres_conv_env
    login = client.post("/auth/login", json={"email": "conv-pg@test.example", "password": PASSWORD})
    assert login.status_code == 200, login.text
    client.headers.update({"Authorization": f"Bearer {login.json()['tokens']['access_token']}"})
    strategy_id = _create_strategy(client, name="PG concurrent strategy")
    versions_before = client.get(f"/strategies/{strategy_id}/versions")
    start_count = len(versions_before.json()["items"])
    conversation = client.post("/conversations", json={"strategy_id": strategy_id})
    conv_id = conversation.json()["id"]
    draft = client.post(
        f"/conversations/{conv_id}/proposals",
        json={
            "text": "HTF pullback long, 2% fixed stop, take profit 1R, skip high funding",
            "strategy_id": strategy_id,
        },
    )
    proposal_body = draft.json()
    proposal_id = proposal_body["id"]
    results: list[object] = []

    def _confirm() -> None:
        response = client.post(
            f"/conversations/{conv_id}/proposals/{proposal_id}/confirm",
            json=_confirm_payload(proposal_body),
        )
        results.append(response)

    workers = [threading.Thread(target=_confirm) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert len(results) == 2
    successes = [item for item in results if item.status_code == 200]  # type: ignore[attr-defined]
    conflicts = [item for item in results if item.status_code == 409]  # type: ignore[attr-defined]
    assert successes, [item.status_code for item in results]  # type: ignore[attr-defined]
    assert len(successes) + len(conflicts) == 2
    version_ids = {item.json()["resulting_version_id"] for item in successes}  # type: ignore[attr-defined]
    assert len(version_ids) == 1
    versions_after = client.get(f"/strategies/{strategy_id}/versions")
    assert len(versions_after.json()["items"]) == start_count + 1


@requires_postgres
def test_postgres_agent_authoring_preserves_setup_type_and_replays_one_version(postgres_conv_env):
    from tests.test_strategy_conversation_foundation import (
        _assert_agent_authoring_confirmation_journey,
        _auth,
    )

    client, factory = postgres_conv_env
    _auth(client, "conv-pg@test.example")
    _assert_agent_authoring_confirmation_journey(client, factory)
