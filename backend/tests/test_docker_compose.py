"""Docker Compose configuration and deployment-safe settings tests.

These tests stay fast and deterministic — they do not require Docker to be
installed or running.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"


@pytest.fixture
def compose_config() -> dict:
    assert COMPOSE_FILE.is_file(), "docker-compose.yml must exist at repo root"
    with COMPOSE_FILE.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_compose_defines_core_services(compose_config: dict) -> None:
    services = compose_config["services"]
    for name in ("backend", "postgres", "redis", "qdrant"):
        assert name in services, f"missing service: {name}"


def test_compose_backend_has_safe_trading_defaults(compose_config: dict) -> None:
    env = compose_config["services"]["backend"]["environment"]
    assert env["EXECUTION_MODE"] == "paper"
    assert env["ENABLE_REAL_TRADING"] in ("false", False)
    assert env["EXCHANGE_MODE"] == "paper_internal"
    assert env["MARKET_WATCHER_ENABLED"] in ("false", False)
    assert env["WATCHER_ORCHESTRATION_ENABLED"] in ("false", False)
    assert env["TELEGRAM_ALERTS_ENABLED"] in ("false", False)
    assert env["TELEGRAM_INTERACTION_ENABLED"] in ("false", False)
    assert env["OBSERVABILITY_STRICT_MODE"] in ("false", False)
    assert env["PROVIDER_MODE"] == "mock"
    assert env["OPENAI_API_KEY"] in ("", None)
    assert env.get("AUTH_REFRESH_COOKIE_ENABLED") in ("true", True)
    assert env.get("ACCESS_TOKEN_DENYLIST_ENABLED") in ("true", True)


def test_compose_backend_points_at_infrastructure_containers(compose_config: dict) -> None:
    env = compose_config["services"]["backend"]["environment"]
    assert "@postgres:5432" in env["DATABASE_URL"]
    assert "redis://redis:" in env["REDIS_URL"]
    assert "http://qdrant:" in env["QDRANT_URL"]


def test_compose_services_define_healthchecks(compose_config: dict) -> None:
    for name in ("backend", "postgres", "redis", "qdrant"):
        healthcheck = compose_config["services"][name].get("healthcheck")
        assert healthcheck is not None, f"{name} missing healthcheck"
        assert healthcheck.get("test"), f"{name} healthcheck test is empty"


def test_compose_backend_waits_for_infrastructure_health(compose_config: dict) -> None:
    depends_on = compose_config["services"]["backend"]["depends_on"]
    for service in ("postgres", "redis", "qdrant"):
        assert depends_on[service]["condition"] == "service_healthy"


def test_settings_accept_docker_compose_urls() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://alphatrade:alphatrade@postgres:5432/alphatrade",
        redis_url="redis://redis:6379/0",
        qdrant_url="http://qdrant:6333",
        execution_mode="paper",
        enable_real_trading=False,
        provider_mode="mock",
        observability_strict_mode=False,
        debug=False,
    )
    assert settings.real_trading_enabled is False
    assert settings.provider_mode == "mock"


def test_docker_like_settings_keep_real_trading_disabled() -> None:
    settings = Settings(
        execution_mode="paper",
        enable_real_trading=False,
        provider_mode="fallback",
    )
    assert settings.real_trading_enabled is False


def test_health_and_provider_status_under_docker_like_settings() -> None:
    from collections.abc import Iterator

    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db.base import Base
    from app.db.session import get_session

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

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    settings = Settings(
        debug=False,
        log_json=True,
        execution_mode="paper",
        enable_real_trading=False,
        provider_mode="mock",
        observability_strict_mode=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="docker-compose-test-secret",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
    )
    app = create_app(settings=settings)
    app.dependency_overrides[get_session] = _override_session

    register = None
    with TestClient(app) as client:
        register = client.post(
            "/auth/register",
            json={
                "email": "docker@test.example",
                "password": "secure-password-1",
                "organization_name": "Docker Org",
            },
        )
        assert register.status_code == 201
        token = register.json()["tokens"]["access_token"]
        client.headers.update({"Authorization": f"Bearer {token}"})
        health = client.get("/health")
        assert health.status_code == 200
        body = health.json()
        assert body["execution_mode"] == "paper"
        assert body["real_trading_enabled"] is False
        assert body["exchange_mode"] == "paper_internal"
        assert body["market_watcher_enabled"] is False
        assert body["telegram_alerts_enabled"] is False

        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["ready"] is True

        usage = client.get("/usage/summary")
        assert usage.status_code == 200

        audit = client.get("/audit/events")
        assert audit.status_code == 404


def test_env_example_documents_docker_safe_defaults() -> None:
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for key in (
        "EXECUTION_MODE=paper",
        "ENABLE_REAL_TRADING=false",
        "OBSERVABILITY_STRICT_MODE=false",
        "PROVIDER_MODE=mock",
    ):
        assert key in env_example
