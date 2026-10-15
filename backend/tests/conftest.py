"""Shared pytest fixtures.

Builds an isolated app instance with explicit, safe settings so tests never
depend on a developer's local ``.env`` or environment.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.security.rate_limit import reset_rate_limiter

pytest_plugins = (
    "tests.test_workflows",
    "tests.test_market_watcher_scanner_slice_74",
    "tests.test_market_watcher_scanner_slice_75",
    "tests.support.learning_attribution",
)


def pytest_configure(config: pytest.Config) -> None:
    """Give each xdist worker a private fixture database before tests import URLs."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return
    from tests.support.postgres_template import assign_worker_database

    assign_worker_database(worker)


def pytest_sessionstart(session: pytest.Session) -> None:
    """Drop leaked clones, then build fixture templates once for this process."""
    from tests.support.postgres_template import cleanup_owned_databases, prebuild_fixture_templates

    try:
        cleanup_owned_databases()
        prebuild_fixture_templates()
    except Exception:
        return


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> Iterator[None]:
    """Drop function-scoped template clones after fixture teardown."""
    yield
    from tests.support.postgres_template import release_ephemeral_clones

    release_ephemeral_clones()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Drop this process's templates and any clone that outlived its fixture."""
    from tests.support.postgres_template import cleanup_owned_databases

    try:
        cleanup_owned_databases()
    except Exception:
        return


@pytest.fixture(autouse=True)
def _isolate_rate_limiter() -> Iterator[None]:
    """Reset shared in-memory rate limit state so tests do not cross-contaminate."""
    reset_rate_limiter()
    yield
    reset_rate_limiter()


@pytest.fixture
def settings() -> Settings:
    """Deterministic, safe settings for tests (paper mode, no real trading)."""
    return Settings(
        environment="local",
        debug=True,
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        rate_limit_use_redis=False,
        market_data_cache_use_redis=False,
        access_token_denylist_use_redis=False,
        provider_mode="mock",
        market_data_provider="mock",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings=settings)
    with TestClient(app) as test_client:
        yield test_client
