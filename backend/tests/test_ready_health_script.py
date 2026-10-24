"""Exit codes for the staging /health/ready alert helper. No network."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "lib" / "ready_health.py"
READY_SCRIPTS = (
    "scripts/staging-smoke.sh",
    "scripts/staging-live-smoke.sh",
    "scripts/docker-validate.sh",
    "scripts/validate-exchange-demo-staging.sh",
    "scripts/validate-telegram-delivery-staging.sh",
    "scripts/run-slice66b-controlled-demo-order.sh",
)


def _run(payload: dict[str, object], *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(SCRIPT), json.dumps(payload), *extra],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_ready_status_exits_zero() -> None:
    result = _run(
        {"status": "ready", "ready": True, "degraded_providers": [], "providers_unavailable": 0}
    )
    assert result.returncode == 0, result.stderr
    assert "status=ready" in result.stdout


def test_degraded_providers_exit_nonzero_even_when_ready_is_true() -> None:
    result = _run(
        {
            "status": "degraded",
            "ready": True,
            "degraded_providers": ["binance-usdm-perpetual"],
        },
        "--allow-degraded-ready",
    )
    assert result.returncode != 0
    assert "ALERT" in result.stderr
    assert "binance-usdm-perpetual" in result.stderr


def test_status_other_than_ready_exits_nonzero() -> None:
    result = _run({"status": "degraded", "ready": False, "degraded_providers": []})
    assert result.returncode != 0
    assert "status='degraded'" in result.stderr


def test_allow_degraded_ready_does_not_waive_a_named_provider() -> None:
    strict = _run(
        {"status": "degraded", "ready": True, "degraded_providers": ["binance-usdm-perpetual"]}
    )
    waived = _run(
        {"status": "degraded", "ready": False, "degraded_providers": []},
        "--allow-degraded-ready",
    )
    assert strict.returncode != 0
    assert waived.returncode == 0
    assert "WARN" in waived.stdout
    assert "degraded_providers=" in waived.stdout


def test_invalid_json_exits_nonzero() -> None:
    result = subprocess.run(
        ["python3", str(SCRIPT), "not-json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "ALERT" in result.stderr


def test_staging_scripts_key_on_the_ready_helper() -> None:
    for relative in READY_SCRIPTS:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "scripts/lib/ready_health.py" in text, relative
        assert "degraded_providers=" not in text, relative
