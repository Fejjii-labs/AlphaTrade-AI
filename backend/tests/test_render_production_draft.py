"""Guards for the production Render Blueprint draft (not applied)."""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

DRAFT = Path(__file__).resolve().parents[2] / "deploy" / "render.production.draft.yaml"
SECRET_OR_RUNTIME_KEYS = {
    "DATABASE_URL", "REDIS_URL", "QDRANT_URL", "QDRANT_API_KEY", "JWT_SECRET",
    "OPENAI_API_KEY", "ENVIRONMENT", "DEPLOYMENT_PROFILE", "EXCHANGE_MODE", "PROVIDER_MODE",
    "WATCHER_ORCHESTRATION_ENABLED", "TELEGRAM_ALERTS_ENABLED", "CORS_ORIGINS",
}


def _services() -> list[dict]:
    return yaml.safe_load(DRAFT.read_text())["services"]


def _env(service: dict) -> dict[str, dict]:
    return {e["key"]: e for e in service["envVars"]}


@pytest.mark.parametrize("service", _services(), ids=lambda s: s["name"])
def test_real_trading_pinned_off(service: dict) -> None:
    env = _env(service)
    assert str(env["ENABLE_REAL_TRADING"]["value"]).lower() == "false"
    assert env["EXECUTION_MODE"]["value"] == "paper"


@pytest.mark.parametrize("service", _services(), ids=lambda s: s["name"])
def test_runtime_and_secret_keys_are_never_synced(service: dict) -> None:
    env = _env(service)
    for key in SECRET_OR_RUNTIME_KEYS & env.keys():
        assert env[key].get("sync") is False, key
        assert "value" not in env[key], key


@pytest.mark.parametrize("service", _services(), ids=lambda s: s["name"])
def test_production_is_separate_and_manual(service: dict) -> None:
    assert service["name"].endswith("-production")
    assert service["autoDeploy"] is False


def test_separate_production_database() -> None:
    names = [d["name"] for d in yaml.safe_load(DRAFT.read_text())["databases"]]
    assert names == ["alphatrade-postgres-production"]


@pytest.mark.parametrize("service", _services(), ids=lambda s: s["name"])
def test_deployment_profile_is_dashboard_owned(service: dict) -> None:
    assert _env(service)["DEPLOYMENT_PROFILE"].get("sync") is False
