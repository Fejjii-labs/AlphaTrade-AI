"""Literal render.yaml disarmed workers boot. Armed workers still fail closed.

No secret is injected into the blueprint. The API path does not use the
disarmed worker role, so staging validation stays mandatory there.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.disarmed_worker_boot import (
    WorkerBootRole,
    bind_worker_boot_role,
    defer_operational_dependencies,
    reset_disarmed_worker_boot,
    settings_are_disarmed_paper_worker,
)

ROOT = Path(__file__).resolve().parents[2]
_SECRET_KEYS = (
    "DATABASE_URL",
    "REDIS_URL",
    "JWT_SECRET",
    "OPENAI_API_KEY",
    "QDRANT_URL",
    "QDRANT_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
)
_WORKERS = (("alphatrade-paper-worker-staging", WorkerBootRole.PAPER_WORKER),)
_CONTRACT = {
    "environment": "staging",
    "execution_mode": "paper",
    "enable_real_trading": False,
    "exchange_mode": "paper_internal",
    "provider_mode": "fallback",
    "auth_refresh_cookie_enabled": True,
    "auth_cookie_secure": True,
    "auth_cookie_samesite": "none",
    "cors_origins": "https://app.example.com",
    "rate_limit_use_redis": True,
    "rate_limit_allow_in_memory_fallback": False,
    "trusted_proxy_hops": 1,
    "access_token_denylist_enabled": True,
    "access_token_denylist_use_redis": True,
    "access_token_denylist_fail_closed": True,
    "perpetual_evidence_source": "binance_usdm",
    "market_data_futures_base_url": "https://fapi.binance.com",
    "debug": False,
    "database_url": "postgresql+psycopg://alphatrade:alphatrade@localhost:5432/alphatrade",
    "redis_url": "redis://localhost:6379/0",
    "qdrant_url": "http://localhost:6333",
    "openai_api_key": "",
    "jwt_secret": "dev-only-change-me-before-production",
    "telegram_bot_token": "",
    "telegram_webhook_secret": "",
    "watcher_orchestration_enabled": False,
    "watcher_paper_staging_activation": False,
    "market_watcher_enabled": False,
    "market_watcher_bridge_enabled": False,
    "market_watcher_bridge_auto_tick": False,
    "telegram_alerts_enabled": False,
    "telegram_interaction_enabled": False,
    "automatic_telegram_delivery_enabled": False,
    "telegram_paper_activation_armed": False,
    "telegram_inbound_mode": "off",
    "telegram_network_permitted": False,
}
_PRESENT_SECRETS = {
    "database_url": "postgresql+psycopg://user:pass@db.example.com:5432/alphatrade",
    "redis_url": "redis://redis.example.com:6379/0",
    "qdrant_url": "https://qdrant.example.com",
    "openai_api_key": "sk-test-not-a-real-key",
    "jwt_secret": "j" * 48,
}
_ARMED_WATCHER = {
    "watcher_orchestration_enabled": True,
    "watcher_paper_staging_activation": True,
}
_ARMED_TELEGRAM = {
    **_ARMED_WATCHER,
    "telegram_paper_activation_armed": True,
    "telegram_interaction_enabled": True,
    "telegram_inbound_mode": "polling",
    "telegram_network_permitted": True,
    "telegram_bot_id": "123456",
    "telegram_chat_id": "789",
}

_DISARMED_SCRIPT = """
import json, os, sys
payload = json.loads(os.environ["BLUEPRINT_JSON"])
role = sys.argv[1]
os.environ.clear()
os.environ.update(payload)
import app.db.session as session_mod

def _forbidden(*_args, **_kwargs):
    raise RuntimeError("operational dependency opened during disarmed boot")

session_mod.get_engine = _forbidden
session_mod.get_session_factory = _forbidden
if role == "paper_worker":
    from app.workers.paper_worker import run_paper_worker_process
    posture = run_paper_worker_process(once=True)
elif role == "watcher_paper":
    from app.workers.watcher_paper import run_watcher_paper_process
    posture = run_watcher_paper_process(once=True)
else:
    from app.telegram_activation.__main__ import run_telegram_paper_process
    posture = run_telegram_paper_process(once=True)
from app.core.config import get_settings
settings = get_settings()
print("BOOT_RESULT " + json.dumps({
    "ok": True,
    "posture": posture,
    "environment": settings.environment.value,
    "execution_mode": settings.execution_mode.value,
    "real_trading_enabled": settings.real_trading_enabled,
    "enable_real_trading": settings.enable_real_trading,
    "exchange_mode": settings.exchange_mode.value,
    "watcher_orchestration_enabled": settings.watcher_orchestration_enabled,
    "watcher_paper_staging_activation": settings.watcher_paper_staging_activation,
    "telegram_paper_activation_armed": settings.telegram_paper_activation_armed,
    "telegram_network_permitted": settings.telegram_network_permitted,
    "telegram_inbound_mode": settings.telegram_inbound_mode.value,
    "telegram_bot_token_configured": bool(settings.telegram_bot_token.strip()),
    "auth_refresh_cookie_enabled": settings.auth_refresh_cookie_enabled,
    "auth_cookie_secure": settings.auth_cookie_secure,
    "auth_cookie_samesite": settings.auth_cookie_samesite,
    "cors_origins": settings.cors_origins,
    "database_url_configured": bool(os.environ.get("DATABASE_URL", "").strip()),
    "redis_url_configured": bool(os.environ.get("REDIS_URL", "").strip()),
    "jwt_secret_configured": bool(os.environ.get("JWT_SECRET", "").strip()),
    "openai_api_key_configured": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
    "qdrant_url_configured": bool(os.environ.get("QDRANT_URL", "").strip()),
}))
"""

_VALIDATE_SCRIPT = """
import json, os, sys
from pydantic import ValidationError
payload = json.loads(os.environ["BLUEPRINT_JSON"])
mode = sys.argv[1]
role = sys.argv[2] if len(sys.argv) > 2 else ""
os.environ.clear()
os.environ.update(payload)
if mode == "raw":
    from app.core.config import Settings
    try:
        Settings()
    except ValidationError as exc:
        print("BOOT_RESULT " + json.dumps({"ok": False, "error": str(exc)}))
    else:
        print("BOOT_RESULT " + json.dumps({"ok": True}))
else:
    from app.core.disarmed_worker_boot import WorkerBootRole, load_worker_process_settings
    try:
        settings = load_worker_process_settings(WorkerBootRole(role))
    except ValidationError as exc:
        print("BOOT_RESULT " + json.dumps({"ok": False, "error": str(exc)}))
    else:
        from app.core.disarmed_worker_boot import settings_are_disarmed_paper_worker
        print("BOOT_RESULT " + json.dumps({
            "ok": True,
            "disarmed": settings_are_disarmed_paper_worker(settings),
            "enable_real_trading": settings.enable_real_trading,
            "real_trading_enabled": settings.real_trading_enabled,
            "execution_mode": settings.execution_mode.value,
        }))
"""


def blueprint_services() -> dict[str, dict[str, object]]:
    document = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    services = document["services"]
    assert isinstance(services, list)
    return {str(item["name"]): item for item in services}


def blueprint_env(service_name: str) -> dict[str, str]:
    service = blueprint_services()[service_name]
    items = service["envVars"]
    assert isinstance(items, list)
    env: dict[str, str] = {}
    for item in items:
        assert isinstance(item, dict)
        key = str(item["key"])
        if "value" not in item:
            continue
        value = item["value"]
        if isinstance(value, bool):
            env[key] = "true" if value else "false"
        else:
            env[key] = str(value)
    return env


def _run_child(script: str, payload: dict[str, str], *args: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-c", script, *args],
        check=False,
        cwd="/tmp",
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(ROOT / "backend" / "src"),
            "BLUEPRINT_JSON": json.dumps(payload),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    line = next(
        item for item in reversed(completed.stdout.splitlines()) if item.startswith("BOOT_RESULT ")
    )
    loaded = json.loads(line.removeprefix("BOOT_RESULT "))
    assert isinstance(loaded, dict)
    return loaded


def boot_literal_worker(service_name: str, role: WorkerBootRole) -> dict[str, object]:
    """Construct Settings and idle once from the literal blueprint environment.

    The child refuses the database. A disarmed reboot tries to delete leftover
    arming rows and then continues, so this boot still returns disarmed.
    """

    env = blueprint_env(service_name)
    for secret in _SECRET_KEYS:
        assert secret not in env
    return _run_child(_DISARMED_SCRIPT, env, role.value)


def test_blueprint_is_api_plus_one_disarmed_paper_worker() -> None:
    """Two Render compute services. The paper worker hosts Watcher and Telegram."""

    services = blueprint_services()
    assert set(services) == {
        "alphatrade-api-staging",
        "alphatrade-paper-worker-staging",
    }
    assert "alphatrade-worker-staging" not in services
    assert "alphatrade-watcher-paper-staging" not in services
    assert "alphatrade-telegram-paper-staging" not in services
    commands = {item.get("dockerCommand") for item in services.values()}
    assert "python -m app.workers.entrypoint" not in commands
    assert "python -m app.workers.watcher_paper" not in commands
    assert "python -m app.telegram_activation run" not in commands
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert text.count("\n  - type: web\n") == 1
    assert text.count("\n  - type: worker\n") == 1
    api = services["alphatrade-api-staging"]
    assert api["type"] == "web"
    assert api["plan"] == "starter"
    assert api["runtime"] == "docker"
    assert "dockerCommand" not in api
    assert api["healthCheckPath"] == "/health"
    assert api["preDeployCommand"] == "alembic upgrade head"
    worker = services["alphatrade-paper-worker-staging"]
    assert worker["type"] == "worker"
    assert worker["plan"] == "starter"
    assert worker["region"] == "frankfurt"
    assert worker["dockerCommand"] == "python -m app.workers.paper_worker"
    assert "preDeployCommand" not in worker


def test_literal_render_workers_boot_disarmed_without_secrets() -> None:
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN" not in text
    for name, role in _WORKERS:
        service = blueprint_services()[name]
        assert service["dockerCommand"] == "python -m app.workers.paper_worker"
        booted = boot_literal_worker(name, role)
        assert booted["ok"] is True
        assert booted["posture"] == "disarmed"
        assert booted["environment"] == "staging"
        assert booted["execution_mode"] == "paper"
        assert booted["exchange_mode"] == "paper_internal"
        assert booted["real_trading_enabled"] is False
        assert booted["enable_real_trading"] is False
        assert booted["watcher_orchestration_enabled"] is False
        assert booted["watcher_paper_staging_activation"] is False
        assert booted["telegram_paper_activation_armed"] is False
        assert booted["telegram_network_permitted"] is False
        assert booted["telegram_inbound_mode"] == "off"
        assert booted["telegram_bot_token_configured"] is False
        assert booted["database_url_configured"] is False
        assert booted["redis_url_configured"] is False
        assert booted["jwt_secret_configured"] is False
        assert booted["openai_api_key_configured"] is False
        assert booted["qdrant_url_configured"] is False
        assert booted["auth_refresh_cookie_enabled"] is True
        assert booted["auth_cookie_secure"] is True
        assert booted["auth_cookie_samesite"] == "none"
        origins = booted["cors_origins"]
        assert isinstance(origins, list)
        assert origins
        assert all(str(origin).startswith("https://") for origin in origins)


def test_literal_blueprint_without_worker_role_still_fails_closed() -> None:
    for name, _role in _WORKERS:
        result = _run_child(_VALIDATE_SCRIPT, blueprint_env(name), "raw", "")
        assert result["ok"] is False
        error = str(result["error"])
        assert "database_url" in error
        assert "redis_url" in error
        assert "jwt_secret" in error
        assert "openai_api_key" in error
        assert "qdrant_url" in error


def test_api_staging_blueprint_still_requires_operational_secrets() -> None:
    result = _run_child(_VALIDATE_SCRIPT, blueprint_env("alphatrade-api-staging"), "raw", "")
    assert result["ok"] is False
    error = str(result["error"])
    assert "database_url" in error
    assert "jwt_secret" in error
    assert "openai_api_key" in error
    assert "qdrant_url" in error


def test_armed_literal_workers_fail_closed_without_operational_dependencies() -> None:
    env = blueprint_env("alphatrade-paper-worker-staging")
    env["WATCHER_ORCHESTRATION_ENABLED"] = "true"
    env["WATCHER_PAPER_STAGING_ACTIVATION"] = "true"
    for role in (
        WorkerBootRole.PAPER_WORKER,
        WorkerBootRole.WATCHER_PAPER,
        WorkerBootRole.TELEGRAM_PAPER,
    ):
        result = _run_child(_VALIDATE_SCRIPT, env, "worker", role.value)
        assert result["ok"] is False
        error = str(result["error"])
        assert "database_url" in error
        assert "redis_url" in error
        assert "jwt_secret" in error
        assert "openai_api_key" in error
        assert "qdrant_url" in error


def test_armed_telegram_worker_fails_closed_without_bot_token() -> None:
    env = blueprint_env("alphatrade-paper-worker-staging")
    env.update(
        {
            "WATCHER_ORCHESTRATION_ENABLED": "true",
            "WATCHER_PAPER_STAGING_ACTIVATION": "true",
            "TELEGRAM_PAPER_ACTIVATION_ARMED": "true",
            "TELEGRAM_INTERACTION_ENABLED": "true",
            "TELEGRAM_INBOUND_MODE": "polling",
            "TELEGRAM_NETWORK_PERMITTED": "true",
            "TELEGRAM_BOT_ID": "123456",
            "TELEGRAM_CHAT_ID": "789",
            "DATABASE_URL": "postgresql+psycopg://user:pass@db.example.com:5432/alphatrade",
            "REDIS_URL": "redis://redis.example.com:6379/0",
            "QDRANT_URL": "https://qdrant.example.com",
            "OPENAI_API_KEY": "sk-test-not-a-real-key",
            "JWT_SECRET": "j" * 48,
        }
    )
    assert "TELEGRAM_BOT_TOKEN" not in env
    for role in (WorkerBootRole.PAPER_WORKER, WorkerBootRole.TELEGRAM_PAPER):
        result = _run_child(_VALIDATE_SCRIPT, env, "worker", role.value)
        assert result["ok"] is False
        error = str(result["error"])
        assert "telegram_paper_activation_armed" in error


@pytest.fixture
def _reset_boot_role() -> object:
    reset_disarmed_worker_boot()
    get_settings.cache_clear()
    yield
    reset_disarmed_worker_boot()
    get_settings.cache_clear()


def test_unbound_settings_still_reject_missing_operational_dependencies(
    _reset_boot_role: object,
) -> None:
    del _reset_boot_role
    with pytest.raises(ValidationError, match="database_url") as raised:
        Settings(**_CONTRACT)
    error = str(raised.value)
    assert "redis_url" in error
    assert "jwt_secret" in error
    assert "openai_api_key" in error
    assert "qdrant_url" in error


@pytest.mark.parametrize(
    "role",
    [WorkerBootRole.PAPER_WORKER, WorkerBootRole.WATCHER_PAPER],
)
def test_bound_disarmed_role_constructs_settings_without_those_dependencies(
    _reset_boot_role: object,
    role: WorkerBootRole,
) -> None:
    del _reset_boot_role
    bind_worker_boot_role(role)
    settings = Settings(**_CONTRACT)
    assert settings_are_disarmed_paper_worker(settings) is True
    assert defer_operational_dependencies(settings) is True
    assert settings.enable_real_trading is False
    assert settings.real_trading_enabled is False
    assert settings.execution_mode.value == "paper"


def test_bound_role_does_not_defer_when_the_worker_is_armed(
    _reset_boot_role: object,
) -> None:
    del _reset_boot_role
    bind_worker_boot_role(WorkerBootRole.WATCHER_PAPER)
    with pytest.raises(ValidationError, match="database_url") as raised:
        Settings(**{**_CONTRACT, **_ARMED_WATCHER})
    error = str(raised.value)
    assert "redis_url" in error
    assert "jwt_secret" in error
    assert "openai_api_key" in error
    assert "qdrant_url" in error


@pytest.mark.parametrize(
    ("missing", "match"),
    [
        ({"database_url": _CONTRACT["database_url"]}, "database_url"),
        ({"redis_url": _CONTRACT["redis_url"]}, "redis_url"),
        ({"jwt_secret": _CONTRACT["jwt_secret"]}, "jwt_secret"),
        ({"openai_api_key": ""}, "openai_api_key"),
        ({"qdrant_url": _CONTRACT["qdrant_url"]}, "qdrant_url"),
    ],
)
def test_armed_watcher_fails_closed_on_each_operational_dependency(
    _reset_boot_role: object,
    missing: dict[str, str],
    match: str,
) -> None:
    del _reset_boot_role
    payload = {**_CONTRACT, **_PRESENT_SECRETS, **_ARMED_WATCHER, **missing}
    with pytest.raises(ValidationError, match=match):
        Settings(**payload)


def test_armed_telegram_settings_fail_closed_without_bot_token(
    _reset_boot_role: object,
) -> None:
    del _reset_boot_role
    payload = {**_CONTRACT, **_PRESENT_SECRETS, **_ARMED_TELEGRAM, "telegram_bot_token": ""}
    with pytest.raises(ValidationError, match="telegram_paper_activation_armed"):
        Settings(**payload)


def test_disarmed_role_still_rejects_mock_providers_and_insecure_cookies(
    _reset_boot_role: object,
) -> None:
    del _reset_boot_role
    bind_worker_boot_role(WorkerBootRole.TELEGRAM_PAPER)
    with pytest.raises(ValidationError, match="provider_mode=mock"):
        Settings(**{**_CONTRACT, "provider_mode": "mock"})
    with pytest.raises(ValidationError, match="auth_refresh_cookie_enabled"):
        Settings(**{**_CONTRACT, "auth_refresh_cookie_enabled": False})
    with pytest.raises(ValidationError, match="jwt_secret must be at least"):
        Settings(**{**_CONTRACT, "jwt_secret": "short"})


def test_armed_worker_with_dependencies_is_not_disarmed(
    _reset_boot_role: object,
) -> None:
    del _reset_boot_role
    settings = Settings(**{**_CONTRACT, **_PRESENT_SECRETS, **_ARMED_WATCHER})
    assert settings_are_disarmed_paper_worker(settings) is False
    assert defer_operational_dependencies(settings) is False
    assert settings.enable_real_trading is False
    assert settings.real_trading_enabled is False
