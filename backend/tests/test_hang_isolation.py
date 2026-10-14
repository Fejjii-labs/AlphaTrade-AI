"""Hang-isolation diagnostics must name a slow test without opening a broker."""

from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pytest_socket import SocketConnectBlockedError, _remove_restrictions, socket_allow_hosts

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "backend_release_hang", ROOT / "scripts/run_backend_release.py"
)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)

from tests.support.hang_isolation import (  # noqa: E402
    assert_hang_isolation_safety,
    forbidden_credential_names,
)

_PAPER_ENV = {
    "EXECUTION_MODE": "paper",
    "ENABLE_REAL_TRADING": "false",
    "PROVIDER_MODE": "mock",
    "MARKET_DATA_PROVIDER": "mock",
}


def test_diagnostic_command_collects_every_failure_with_a_per_test_timeout(tmp_path):
    command = release.pytest_command(tmp_path, diagnostic=True)
    assert "-x" not in command
    assert "-v" in command
    assert "-rA" in command
    assert "--durations=50" in command
    assert "--timeout=300" in command
    assert "--timeout-method=thread" in command
    assert "faulthandler_timeout=60" in command
    assert "--disable-socket" in command
    assert "--allow-unix-socket" in command
    assert "--allow-hosts=localhost,127.0.0.1,::1" in command
    assert "tests.support.hang_isolation" in command
    assert any(arg.startswith("--junitxml=") and arg.endswith("junit.xml") for arg in command)
    assert release.phase_budget(180, diagnostic=True) == 420
    assert release.phase_budget(420, diagnostic=True) == 420
    assert release.phase_budget(180, diagnostic=False) == 180


def test_default_release_command_still_stops_at_the_first_failure(tmp_path):
    command = release.pytest_command(tmp_path, diagnostic=False)
    assert "-x" in command
    assert "--timeout=300" not in command
    assert "--disable-socket" not in command


def test_diagnostic_env_is_teed_to_the_child_log(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "postgres_snapshot", lambda: {"available": False})
    code = release.run(
        [sys.executable, "-c", "import os; print(os.environ['MARKET_DATA_PROVIDER'])"],
        tmp_path,
        phase_seconds=5,
        run_seconds=5,
        extra_env={"MARKET_DATA_PROVIDER": "mock"},
    )
    assert code == 0
    assert (tmp_path / "pytest.log").read_text().strip() == "mock"


def test_postgres_url_is_not_a_broker_credential_and_keys_are_names_only():
    names = forbidden_credential_names(
        {
            "BLOFIN_ACTIVITY_TEST_POSTGRES_URL": "postgresql://localhost/test",
            "OPENAI_API_KEY": "sk-test-value",
            "BLOFIN_API_SECRET": "secret-value",
            "BROKER_API_KEY": "broker-value",
            "OPENAI_BASE_URL": "https://example.invalid",
            "EMPTY_OPENAI_API_KEY": "",
        }
    )
    assert names == ["BLOFIN_API_SECRET", "BROKER_API_KEY", "OPENAI_API_KEY"]


def _apply_paper_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _PAPER_ENV.items():
        monkeypatch.setenv(name, value)
    for name in ("OPENAI_API_KEY", "BLOFIN_API_KEY", "BLOFIN_API_SECRET", "BROKER_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_session_guard_accepts_paper_mock_without_broker_keys(monkeypatch):
    _apply_paper_env(monkeypatch)
    monkeypatch.setenv(
        "BLOFIN_ACTIVITY_TEST_POSTGRES_URL",
        "postgresql+psycopg://alphatrade:alphatrade@localhost:5432/alphatrade_test",
    )
    assert_hang_isolation_safety()


@pytest.mark.parametrize(
    "name,value",
    [
        ("OPENAI_API_KEY", "sk-test-value"),
        ("BLOFIN_API_SECRET", "secret-value"),
        ("BROKER_API_KEY", "broker-value"),
    ],
)
def test_session_guard_fails_loudly_on_broker_keys(monkeypatch, name, value):
    _apply_paper_env(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(AssertionError, match=name) as error:
        assert_hang_isolation_safety()
    assert value not in str(error.value)


def test_session_guard_rejects_a_live_market_provider(monkeypatch):
    _apply_paper_env(monkeypatch)
    monkeypatch.setenv("MARKET_DATA_PROVIDER", "binance")
    with pytest.raises(AssertionError, match="market_data_provider=binance"):
        assert_hang_isolation_safety()


def test_socket_guard_blocks_outbound_and_allows_loopback():
    socket_allow_hosts(["localhost", "127.0.0.1", "::1"], allow_unix_socket=True)
    try:
        outbound = socket.socket()
        with pytest.raises(SocketConnectBlockedError, match=r"203\.0\.113\.1"):
            outbound.connect(("203.0.113.1", 9))
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        client = socket.create_connection(("127.0.0.1", port), timeout=2)
        client.close()
        server.close()
    finally:
        _remove_restrictions()


def test_hang_isolation_plugin_registers_with_pytest_sessionstart():
    env = os.environ.copy()
    env.update(_PAPER_ENV)
    for name in ("OPENAI_API_KEY", "BLOFIN_API_KEY", "BLOFIN_API_SECRET", "BROKER_API_KEY"):
        env.pop(name, None)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests.support.hang_isolation",
            "--collect-only",
            "-q",
            "tests/test_hang_isolation.py::test_postgres_url_is_not_a_broker_credential_and_keys_are_names_only",
        ],
        cwd=ROOT / "backend",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "PluginValidationError" not in completed.stderr


def test_hang_isolation_workflow_publishes_evidence_before_the_job_timeout():
    workflow = yaml.load(
        (ROOT / ".github/workflows/backend-hang-isolation.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    assert workflow["on"]["push"]["branches"] == ["ci/hang-isolation-pr249"]
    job = workflow["jobs"]["backend"]
    assert int(job["timeout-minutes"]) == 120
    steps = {step.get("name"): step for step in job["steps"]}
    test = steps["Complete backend hang isolation"]
    assert int(test["timeout-minutes"]) == 100
    assert int(test["timeout-minutes"]) < int(job["timeout-minutes"])
    assert "--diagnostic" in test["run"]
    assert "--run-seconds 5700" in test["run"]
    assert "tee -a ../backend-ci-diagnostics/runner.log" in test["run"]
    assert "-x" not in test["run"]
    uploads = [
        "Upload pytest log",
        "Upload junit xml",
        "Upload faulthandler dumps",
        "Upload backend diagnostics",
    ]
    for name in uploads:
        assert steps[name]["if"] == "always()"
        assert steps[name]["uses"] == "actions/upload-artifact@v4"
    assert steps["Summarize timeouts, socket blocks, and slowest tests"]["if"] == "always()"
    assert job["env"]["EXECUTION_MODE"] == "paper"
    assert job["env"]["ENABLE_REAL_TRADING"] == "false"
    assert job["env"]["PROVIDER_MODE"] == "mock"
    assert job["env"]["MARKET_DATA_PROVIDER"] == "mock"
    assert "BLOFIN_API" not in job["env"]
    assert "OPENAI_API" not in job["env"]
