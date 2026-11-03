"""Telegram fail-soft on the staging internal-paper worker, and its scope.

Fail-soft is allowed only in staging, for the PAPER_WORKER role, with an armed
Watcher, EXCHANGE_MODE=paper_internal and governed BloFin demo off. BloFin demo
and governed demo configs place venue orders and keep failing closed.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, Response

from app.core import disarmed_worker_boot
from app.core.config import Settings, get_settings
from app.core.disarmed_worker_boot import (
    TELEGRAM_CONFIG_CONFLICT,
    WorkerBootRole,
    load_worker_process_settings,
    reset_disarmed_worker_boot,
    worker_boot_degradations,
)
from app.persistence.runtime_status import PAPER_WORKER_COMPONENT
from app.services.worker_config_report import (
    WORKER_CONFIG_COMPONENT,
    build_worker_config_report,
)
from tests.test_controlled_paper_activation import _STAGING, _package
from tests.test_disarmed_render_worker_boot import _VALIDATE_SCRIPT, _run_child, blueprint_env

_DEMO = {
    "EXCHANGE_MODE": "paper_exchange_demo",
    "BLOFIN_DEMO_ENABLED": "true",
    "BLOFIN_API_KEY": "fake-k",
    "BLOFIN_API_SECRET": "fake-s",
    "BLOFIN_API_PASSPHRASE": "fake-p",
    "BLOFIN_DEMO_REST_BASE_URL": "https://demo-trading-openapi.blofin.com",
    "BLOFIN_DEMO_WS_URL": "wss://demo-trading-openapi.blofin.com/ws/public",
}
_GOVERNED = {
    "GOVERNED_BLOFIN_DEMO_ENABLED": "true",
    "GOVERNED_BLOFIN_DEMO_ORGANIZATION_ID": "11111111-1111-1111-1111-111111111111",
    "GOVERNED_BLOFIN_DEMO_USER_ID": "22222222-2222-2222-2222-222222222222",
    "GOVERNED_BLOFIN_DEMO_ACCOUNT_ID": "33333333-3333-3333-3333-333333333333",
}
# Armed projection without a bot token: the 2026-11-01 class of conflict.
_CONFLICT = {
    "WATCHER_ORCHESTRATION_ENABLED": "true",
    "WATCHER_PAPER_STAGING_ACTIVATION": "true",
    "TELEGRAM_PAPER_ACTIVATION_ARMED": "true",
    "TELEGRAM_INTERACTION_ENABLED": "true",
    "TELEGRAM_INBOUND_MODE": "polling",
    "TELEGRAM_NETWORK_PERMITTED": "true",
    "TELEGRAM_BOT_ID": "123456",
    "TELEGRAM_CHAT_ID": "789",
}
# Operator-owned (sync: false once render.yaml split D lands): the staging values.
_OPERATOR_SOURCES = {
    "MARKET_DATA_ENABLED": "true",
    "PERPETUAL_EVIDENCE_SOURCE": "binance_usdm",
    "PERPETUAL_EVIDENCE_SECONDARY_SOURCE": "bybit_usdt_perpetual",
    "PERPETUAL_EVIDENCE_TIMEOUT_SECONDS": "10",
}
_OPERATIONAL = {
    "DATABASE_URL": "postgresql+psycopg://user:pass@db.example.com:5432/alphatrade",
    "REDIS_URL": "redis://redis.example.com:6379/0",
    "QDRANT_URL": "https://qdrant.example.com",
    "OPENAI_API_KEY": "sk-test-not-a-real-key",
    "JWT_SECRET": "j" * 48,
}


# --- 1. in-process: the internal-paper case boots with Telegram off ------------


@pytest.fixture
def _staging_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    reset_disarmed_worker_boot()
    get_settings.cache_clear()
    for key, value in _STAGING.items():
        text = str(value).lower() if isinstance(value, bool) else str(value)
        monkeypatch.setenv(key.upper(), text)
    for key, value in _CONFLICT.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    yield monkeypatch
    reset_disarmed_worker_boot()
    get_settings.cache_clear()


def test_internal_paper_worker_boots_with_telegram_off(_staging_env: pytest.MonkeyPatch) -> None:
    settings = load_worker_process_settings(WorkerBootRole.PAPER_WORKER)
    assert settings.telegram_paper_activation_armed is False
    assert settings.telegram_network_permitted is False
    assert settings.watcher_orchestration_enabled is True
    assert settings.exchange_mode.value == "paper_internal"
    assert settings.real_trading_enabled is False
    assert worker_boot_degradations() == (TELEGRAM_CONFIG_CONFLICT,)
    report = build_worker_config_report(settings, boot_degradations=worker_boot_degradations())
    assert report.status == "degraded"
    assert "telegram_boot_conflict" in report.failing


def test_trade_path_conflict_still_raises(_staging_env: pytest.MonkeyPatch) -> None:
    _staging_env.setenv("EXECUTION_MODE", "trade")
    with pytest.raises(ValueError):
        load_worker_process_settings(WorkerBootRole.PAPER_WORKER)
    assert worker_boot_degradations() == ()
    assert os.environ["TELEGRAM_NETWORK_PERMITTED"] == "true"


def test_dedicated_telegram_worker_still_fails_closed(_staging_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        load_worker_process_settings(WorkerBootRole.TELEGRAM_PAPER)
    assert worker_boot_degradations() == ()


def test_disarmed_watcher_does_not_fail_soft(_staging_env: pytest.MonkeyPatch) -> None:
    """Fail-soft only keeps an armed Watcher running; a Watcher-off boot fails closed."""

    _staging_env.setenv("WATCHER_ORCHESTRATION_ENABLED", "false")
    _staging_env.setenv("WATCHER_PAPER_STAGING_ACTIVATION", "false")
    for key, value in _OPERATIONAL.items():
        _staging_env.setenv(key, value)
    with pytest.raises(ValueError, match="telegram"):
        load_worker_process_settings(WorkerBootRole.PAPER_WORKER)
    assert worker_boot_degradations() == ()
    assert os.environ["TELEGRAM_PAPER_ACTIVATION_ARMED"] == "true"


def test_non_staging_env_never_fails_soft(_staging_env: pytest.MonkeyPatch) -> None:
    """Local accepts the Telegram flags as set, so a fail-soft there would be a silent change."""

    _staging_env.setenv("ENVIRONMENT", "local")
    _staging_env.setenv("PERPETUAL_EVIDENCE_SOURCE", "replay")
    _staging_env.delenv("WATCHER_PAPER_STAGING_ACTIVATION")
    original = ValueError("original boot error")
    with pytest.raises(ValueError, match="original boot error"):
        disarmed_worker_boot._settings_with_telegram_disabled(original)
    assert worker_boot_degradations() == ()
    assert os.environ["TELEGRAM_NETWORK_PERMITTED"] == "true"


@pytest.mark.parametrize(
    "extra",
    [
        {"EXCHANGE_MODE": "paper_exchange_demo"},
        {"GOVERNED_BLOFIN_DEMO_ENABLED": "true"},
        {"GOVERNED_BLOFIN_DEMO_ENABLED": "not-a-bool"},
        {"ENVIRONMENT": "production"},
        {"WATCHER_ORCHESTRATION_ENABLED": "false"},
        {"WATCHER_PAPER_STAGING_ACTIVATION": "false"},
    ],
)
def test_environ_scope_excludes(_staging_env: pytest.MonkeyPatch, extra: dict[str, str]) -> None:
    assert disarmed_worker_boot._environ_in_telegram_fail_soft_scope() is True
    for key, value in extra.items():
        _staging_env.setenv(key, value)
    assert disarmed_worker_boot._environ_in_telegram_fail_soft_scope() is False


@pytest.mark.parametrize(
    "extra",
    [
        {"environment": "production"},
        {"environment": "local"},
        {"exchange_mode": "paper_exchange_demo"},
        {"governed_blofin_demo_enabled": True},
        {"watcher_orchestration_enabled": False},
        {"watcher_paper_staging_activation": False},
        {"execution_mode": "trade"},
        {"enable_real_trading": True},
    ],
)
def test_settings_scope_excludes(extra: dict[str, object]) -> None:
    """The retry's own Settings are checked again; validators are bypassed to isolate it."""

    good = _package(telegram_paper_activation_armed=False)
    assert disarmed_worker_boot._settings_in_telegram_fail_soft_scope(good) is True
    fields = {**good.model_dump(), **extra}
    bad = Settings.model_construct(**Settings(_env_file=None).model_dump() | fields)  # type: ignore[call-arg]
    for name in ("environment", "exchange_mode", "execution_mode"):
        if name in extra:
            enum_type = type(getattr(good, name))
            object.__setattr__(bad, name, enum_type(extra[name]))
    assert disarmed_worker_boot._settings_in_telegram_fail_soft_scope(bad) is False


def test_settings_outside_scope_restore_env_and_raise_original(
    _staging_env: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        disarmed_worker_boot, "_settings_in_telegram_fail_soft_scope", lambda _s: False
    )
    original = ValueError("original boot error")
    with pytest.raises(ValueError, match="original boot error"):
        disarmed_worker_boot._settings_with_telegram_disabled(original)
    assert worker_boot_degradations() == ()
    assert os.environ["TELEGRAM_INTERACTION_ENABLED"] == "true"
    assert os.environ["TELEGRAM_INBOUND_MODE"] == "polling"


# --- 2. subprocess boots with the Blueprint env (B8-style scope) ---------------


def _boot(role: WorkerBootRole, **extra: str) -> dict[str, object]:
    env = blueprint_env("alphatrade-paper-worker-staging")
    env.update(_OPERATOR_SOURCES)
    env.update(_CONFLICT)
    env.update(_OPERATIONAL)
    assert "TELEGRAM_BOT_TOKEN" not in env
    env.update(extra)
    return _run_child(_VALIDATE_SCRIPT, env, "worker", role.value)


def test_subprocess_internal_paper_boots_with_telegram_off() -> None:
    result = _boot(WorkerBootRole.PAPER_WORKER)
    assert result["ok"] is True
    assert result["exchange_mode"] == "paper_internal"
    assert result["telegram_paper_activation_armed"] is False
    assert result["telegram_network_permitted"] is False
    assert result["real_trading_enabled"] is False
    assert result["execution_mode"] == "paper"


@pytest.mark.parametrize(
    ("label", "extra"),
    [
        ("production", {"ENVIRONMENT": "production"}),
        ("B6_paper_exchange_demo", _DEMO),
        ("B8_governed_demo", {**_DEMO, **_GOVERNED}),
        ("governed_flag_on_internal", _GOVERNED),
        ("watcher_off", {"WATCHER_ORCHESTRATION_ENABLED": "false"}),
    ],
)
def test_subprocess_out_of_scope_boots_fail_closed(label: str, extra: dict[str, str]) -> None:
    for role in (WorkerBootRole.PAPER_WORKER, WorkerBootRole.TELEGRAM_PAPER):
        result = _boot(role, **extra)
        assert result["ok"] is False, (label, role)
        assert "telegram" in str(result["error"]).lower(), (label, role)


def test_subprocess_governed_demo_with_token_still_boots_armed() -> None:
    """The scope refuses only the token-missing case; a configured demo worker is unchanged."""

    result = _boot(
        WorkerBootRole.PAPER_WORKER,
        **_DEMO,
        **_GOVERNED,
        TELEGRAM_BOT_TOKEN="123456:fakefakefake",
    )
    assert result["ok"] is True
    assert result["exchange_mode"] == "paper_exchange_demo"
    assert result["telegram_paper_activation_armed"] is True


# --- 3. a degraded boot is visible without Telegram ----------------------------


def _config_row(status: str) -> SimpleNamespace:
    return SimpleNamespace(
        activation_state=status,
        request_count=1 << 9 if status == "degraded" else 0,
        last_scan_reason="paper_internal",
        market_source="binance_usdm",
        telegram_runtime_state="off",
        inbound_mode="internal_paper",
        last_error_code=TELEGRAM_CONFIG_CONFLICT if status == "degraded" else "none",
        heartbeat_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    ("status", "code"),
    [("degraded", 503), ("disarmed_unexpected", 503), ("ok", 200), ("not_applicable", 200)],
)
def test_paper_worker_health_returns_503_when_config_unhealthy(
    monkeypatch: pytest.MonkeyPatch, status: str, code: int
) -> None:
    from app.api.routes.health import paper_worker_heartbeat

    rows = {PAPER_WORKER_COMPONENT: None, WORKER_CONFIG_COMPONENT: _config_row(status)}
    monkeypatch.setattr("app.api.routes.health._load_runtime_rows", lambda: rows)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: None)
    monkeypatch.setattr(
        "app.services.worker_heartbeat.raise_heartbeat_alert", lambda *_a, **_k: False
    )
    response = Response()
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    body = asyncio.run(paper_worker_heartbeat(settings, BackgroundTasks(), response))
    assert body.worker_config.status == status
    assert response.status_code == code


def test_paper_worker_health_route_sets_503_over_http(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    rows = {PAPER_WORKER_COMPONENT: None, WORKER_CONFIG_COMPONENT: _config_row("degraded")}
    monkeypatch.setattr("app.api.routes.health._load_runtime_rows", lambda: rows)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: None)
    monkeypatch.setattr(
        "app.services.worker_heartbeat.raise_heartbeat_alert", lambda *_a, **_k: False
    )
    monkeypatch.setattr(
        "app.services.risk.kill_switch.kill_switch_retry_should_schedule", lambda *_a, **_k: False
    )
    with TestClient(create_app()) as client:
        response = client.get("/health/paper-worker")
    assert response.status_code == 503
    payload = response.json()
    assert payload["worker_config"]["status"] == "degraded"
    assert "123456:" not in response.text
