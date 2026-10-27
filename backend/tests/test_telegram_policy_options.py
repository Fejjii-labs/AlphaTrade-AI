"""C2: named Telegram filter choices map to the stored identifiers."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import Membership, Organization, User, UserStrategy, UserStrategyVersion
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import MembershipRole, StrategyId
from app.schemas.telegram_policy import TelegramNotificationPolicyV2
from app.security.passwords import hash_password
from app.services.notifications import telegram_policy_options as options_module
from app.services.notifications.telegram_policy_options import telegram_policy_options

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000c20")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000c21")
READER_ID = uuid.UUID("00000000-0000-0000-0000-000000000c22")
OTHER_ORG = uuid.UUID("00000000-0000-0000-0000-000000000c29")
SETTINGS = Settings(
    environment="local",
    log_json=False,
    execution_mode="paper",
    enable_real_trading=False,
    database_url="sqlite+pysqlite:///:memory:",
    jwt_secret="c2-telegram-options-test-secret-key",
    rate_limit_use_redis=False,
    access_token_denylist_use_redis=False,
    provider_mode="mock",
    telegram_alerts_enabled=False,
)


@pytest.fixture
def c2_db() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(Organization(id=ORG_ID, name="C2 Org"))
        for user_id, email in (
            (USER_ID, "owner-c2@test.example"),
            (READER_ID, "mate@test.example"),
        ):
            session.add(
                User(
                    id=user_id,
                    email=email,
                    hashed_password=hash_password("TestPassword123!", SETTINGS),
                    email_verified=True,
                )
            )
        session.flush()
        session.add(Membership(user_id=USER_ID, organization_id=ORG_ID, role=MembershipRole.OWNER))
        session.add(
            Membership(user_id=READER_ID, organization_id=ORG_ID, role=MembershipRole.TRADER)
        )
        session.commit()
    yield factory
    engine.dispose()


@pytest.fixture
def c2_client(c2_db: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app(settings=SETTINGS)

    def _session() -> Iterator[Session]:
        with c2_db() as session:
            yield session

    app.dependency_overrides[get_session] = _session
    with TestClient(app) as client:
        login = client.post(
            "/auth/login", json={"email": "owner-c2@test.example", "password": "TestPassword123!"}
        )
        client.headers["Authorization"] = f"Bearer {login.json()['tokens']['access_token']}"
        yield client


def _strategy(session: Session, *, name: str, user_id=USER_ID, org_id=ORG_ID, versions=1):
    strategy = UserStrategy(
        organization_id=org_id,
        user_id=user_id,
        name=name,
        setup_type=StrategyId.HTF_TREND_PULLBACK,
    )
    session.add(strategy)
    session.flush()
    rows = [
        UserStrategyVersion(strategy_id=strategy.id, version=n, card={}, content_hash=f"{n:064d}")
        for n in range(1, versions + 1)
    ]
    session.add_all(rows)
    session.flush()
    return strategy, rows


def test_options_name_each_choice_and_list_its_stored_identifiers(
    c2_client: TestClient, c2_db: sessionmaker[Session]
) -> None:
    with c2_db() as session:
        nested, nested_versions = _strategy(session, name="Nested", versions=2)
        sfp, sfp_versions = _strategy(session, name="Liquidity sweep")
        session.commit()

    body = c2_client.get("/notifications/filter-options").json()

    assert [o["label"] for o in body["strategies"]] == ["Liquidity sweep", "Nested"]
    by_label = {o["label"]: o["values"] for o in body["strategies"]}
    # Strategy id first, then newest version first.
    assert by_label["Nested"] == [
        str(nested.id),
        str(nested_versions[1].id),
        str(nested_versions[0].id),
    ]
    assert by_label["Liquidity sweep"] == [str(sfp.id), str(sfp_versions[0].id)]
    assert [o["label"] for o in body["setup_stages"]] == ["N1", "N2", "N3", "N4+"]
    assert body["setup_stages"][-1]["values"] == ["N4_PLUS"]
    assert body["symbols"] and all(o["values"] == [o["label"]] for o in body["symbols"])


def test_chosen_values_are_accepted_by_the_saved_policy(
    c2_client: TestClient, c2_db: sessionmaker[Session]
) -> None:
    with c2_db() as session:
        _strategy(session, name="Nested", versions=3)
        session.commit()
    body = c2_client.get("/notifications/filter-options").json()
    policy = TelegramNotificationPolicyV2(
        strategy_subscriptions=tuple(body["strategies"][0]["values"]),
        symbol_subscriptions=tuple(v for o in body["symbols"] for v in o["values"]),
        setup_stages=tuple(v for o in body["setup_stages"] for v in o["values"]),
    )
    saved = c2_client.patch(
        "/notifications/preferences",
        json={"telegram_policy": policy.model_dump(mode="json")},
    )
    assert saved.status_code == 200, saved.text


def test_only_the_callers_own_strategies_are_offered(
    c2_client: TestClient, c2_db: sessionmaker[Session]
) -> None:
    with c2_db() as session:
        session.add(Organization(id=OTHER_ORG, name="Other org"))
        session.flush()
        _strategy(session, name="Mine")
        _strategy(session, name="Teammate", user_id=READER_ID)
        _strategy(session, name="Other org", org_id=OTHER_ORG)
        session.commit()
    body = c2_client.get("/notifications/filter-options").json()
    assert [o["label"] for o in body["strategies"]] == ["Mine"]


def test_version_list_is_bounded_per_strategy(c2_db: sessionmaker[Session], monkeypatch) -> None:
    monkeypatch.setattr(options_module, "VERSIONS_PER_STRATEGY", 2)
    with c2_db() as session:
        strategy, versions = _strategy(session, name="Busy", versions=4)
        session.commit()
        options = telegram_policy_options(session, organization_id=ORG_ID, user_id=USER_ID)
    assert options.strategies[0].values == (
        str(strategy.id),
        str(versions[3].id),
        str(versions[2].id),
    )


def test_options_require_authentication(c2_client: TestClient) -> None:
    c2_client.headers.pop("Authorization")
    assert c2_client.get("/notifications/filter-options").status_code == 401
