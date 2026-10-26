"""Manifest, startup validator, and drift comparator (mocked Render HTTP)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.core.required_env import (
    MissingRequiredEnvError,
    Role,
    missing_keys,
    required_keys,
    should_enforce,
    validate_required_env,
)

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_env_drift.py"
_spec = importlib.util.spec_from_file_location("check_env_drift", _SCRIPT)
drift = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drift)

PINS = {"BLOFIN_ACTIVITY_ORGANIZATION_ID": "org-1", "BLOFIN_ACTIVITY_EXPECTED_UID": "uid-1"}
BASE = {"ENVIRONMENT": "staging", "PROVIDER_MODE": "live"}
KEYS = {
    "BLOFIN_READONLY_API_KEY": "k-SECRETVALUE",
    "BLOFIN_READONLY_API_SECRET": "s-SECRETVALUE",
    "BLOFIN_READONLY_API_PASSPHRASE": "p-SECRETVALUE",
}


def test_api_always_requires_activity_pins() -> None:
    assert set(PINS) <= set(required_keys(Role.API, {}))
    assert missing_keys(Role.API, BASE) == list(PINS)


def test_worker_requires_keys_and_pins_only_when_activity_enabled() -> None:
    assert missing_keys(Role.PAPER_WORKER, BASE) == []
    missing = missing_keys(Role.PAPER_WORKER, {**BASE, "BLOFIN_ACTIVITY_ENABLED": "true"})
    assert set(missing) == set(KEYS) | set(PINS)
    full = {**BASE, **PINS, **KEYS, "BLOFIN_ACTIVITY_ENABLED": "true"}
    assert missing_keys(Role.PAPER_WORKER, full) == []


def test_blank_values_count_as_missing() -> None:
    env = {**BASE, **PINS, "BLOFIN_ACTIVITY_EXPECTED_UID": "  "}
    assert missing_keys(Role.API, env) == ["BLOFIN_ACTIVITY_EXPECTED_UID"]


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, False),
        ({"ENVIRONMENT": "local"}, False),
        ({"ENVIRONMENT": "staging"}, True),
        ({"ENVIRONMENT": "production"}, True),
        ({"ENVIRONMENT": "staging", "ENV_VALIDATION_ENFORCE": "false"}, False),
        ({"ENVIRONMENT": "local", "ENV_VALIDATION_ENFORCE": "true"}, True),
    ],
)
def test_should_enforce(env: dict[str, str], expected: bool) -> None:
    assert should_enforce(env) is expected


def test_validator_fails_fast_naming_keys_never_values() -> None:
    env = {**BASE, **KEYS, "BLOFIN_ACTIVITY_ENABLED": "true"}
    with pytest.raises(MissingRequiredEnvError) as info:
        validate_required_env(Role.PAPER_WORKER, env)
    msg = str(info.value)
    assert "BLOFIN_ACTIVITY_ORGANIZATION_ID" in msg
    assert "SECRETVALUE" not in msg


def test_validator_bypass_and_local_do_not_raise() -> None:
    assert set(PINS) <= set(validate_required_env(Role.API, {"ENVIRONMENT": "local"}))
    bypass = {**BASE, "ENV_VALIDATION_ENFORCE": "false"}
    assert validate_required_env(Role.API, bypass) == list(PINS)
    assert validate_required_env(Role.API, {**BASE, **PINS}) == []


def test_compare_detects_incident_drift() -> None:
    api = dict(BASE)
    worker = {**BASE, **PINS}
    findings = drift.compare(api, worker)
    assert "missing on api: BLOFIN_ACTIVITY_ORGANIZATION_ID" in findings
    assert any("absent on api" in f and "EXPECTED_UID" in f for f in findings)
    assert not any("org-1" in f or "uid-1" in f for f in findings)


def test_compare_hash_mismatch_and_clean() -> None:
    api = {**BASE, **PINS}
    assert drift.compare(api, dict(api)) == []
    worker = {**api, "PROVIDER_MODE": "mock"}
    assert drift.compare(api, worker) == ["shared value differs (hash mismatch): PROVIDER_MODE"]


def _fake_render(envs: dict[str, dict[str, str]]):
    def fetch(url: str, token: str) -> object:
        assert token == "tok"
        sid = url.split("/services/")[1].split("/")[0]
        return [{"envVar": {"key": k, "value": v}, "cursor": k} for k, v in envs[sid].items()]

    return fetch


def test_main_exit_codes_and_no_values_printed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("RENDER_API_KEY", "tok")
    good = {**BASE, **PINS}
    args = ["--api-service", "a", "--worker-service", "w"]
    assert drift.main(args, fetch=_fake_render({"a": good, "w": good})) == 0
    bad_worker = {
        **BASE,
        **PINS,
        **KEYS,
        "BLOFIN_ACTIVITY_ENABLED": "true",
        "BLOFIN_ACTIVITY_EXPECTED_UID": "other",
    }
    assert drift.main(args, fetch=_fake_render({"a": good, "w": bad_worker})) == 1
    out = capsys.readouterr().out
    assert "BLOFIN_ACTIVITY_EXPECTED_UID" in out
    assert "SECRETVALUE" not in out and "other" not in out and "uid-1" not in out


def test_main_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RENDER_API_KEY", raising=False)
    assert drift.main([], fetch=_fake_render({})) == 2
