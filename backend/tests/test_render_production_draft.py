"""Guards for the production Render Blueprint draft (not applied)."""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

DRAFT = Path(__file__).resolve().parents[2] / "deploy" / "render.production.draft.yaml"
SECRET_OR_RUNTIME_KEYS = {
    "REDIS_URL",
    "QDRANT_URL",
    "QDRANT_API_KEY",
    "JWT_SECRET",
    "OPENAI_API_KEY",
    "EXCHANGE_MODE",
    "PROVIDER_MODE",
    "WATCHER_ORCHESTRATION_ENABLED",
    "TELEGRAM_ALERTS_ENABLED",
    "CORS_ORIGINS",
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
def test_environment_and_profile_are_pinned(service: dict) -> None:
    env = _env(service)
    assert env["ENVIRONMENT"] == {"key": "ENVIRONMENT", "value": "production"}
    assert env["DEPLOYMENT_PROFILE"] == {"key": "DEPLOYMENT_PROFILE", "value": "production_demo"}


@pytest.mark.parametrize("service", _services(), ids=lambda s: s["name"])
def test_database_url_from_production_database(service: dict) -> None:
    ref = _env(service)["DATABASE_URL"]
    assert ref["fromDatabase"] == {
        "name": "alphatrade-postgres-production",
        "property": "connectionString",
    }


@pytest.mark.parametrize("service", _services(), ids=lambda s: s["name"])
def test_watcher_arm_never_comes_from_blueprint(service: dict) -> None:
    assert "WATCHER_PAPER_STAGING_ACTIVATION" not in _env(service)


def test_api_pins_email_verification() -> None:
    api = next(s for s in _services() if s["type"] == "web")
    assert str(_env(api)["REQUIRE_EMAIL_VERIFIED"]["value"]).lower() == "true"
