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

# Tests never send the startup BloFin key-scope probe to a real host. Tests that
# exercise the probe opt in explicitly with a mock transport or stub provider.
os.environ.setdefault("EXCHANGE_DEMO_STARTUP_SCOPE_PROBE_ENABLED", "false")

pytest_plugins = (
    "tests.test_workflows",
    "tests.test_market_watcher_scanner_slice_74",
    "tests.test_market_watcher_scanner_slice_75",
    "tests.support.learning_attribution",
)


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
