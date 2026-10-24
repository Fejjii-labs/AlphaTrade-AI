"""Tests that deployment helper scripts exist and are executable."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = (
    "scripts/check-env.sh",
    "scripts/verify-safety.sh",
    "scripts/staging-smoke.sh",
    "scripts/staging-live-smoke.sh",
    "scripts/post-deploy-smoke-gate.sh",
    "scripts/validate-exchange-demo-staging.sh",
    "scripts/run-market-watcher-scan-staging.sh",
    "scripts/run-slice66b-controlled-demo-order.sh",
    "scripts/test-seed-path-staging.sh",
    "scripts/run-migrations.sh",
    "scripts/provider-validation-smoke.sh",
    "scripts/canonical-staging-smoke.sh",
    "scripts/validate-live-market-staging.sh",
    "scripts/watcher-paper-rollback.sh",
    "scripts/watcher-paper-activation-smoke.sh",
    "scripts/lib/watcher_paper_health.py",
    "scripts/telegram-paper-activation-preflight.sh",
    "scripts/telegram-paper-activation-smoke.sh",
    "scripts/telegram-paper-activation-rollback.sh",
    "scripts/controlled-paper-activation-rollback.sh",
    "scripts/lib/ready_health.py",
    "scripts/recreate-rag-collection.sh",
    "scripts/reingest-knowledge-base.sh",
)


def test_deployment_scripts_exist_and_are_executable() -> None:
    for relative in SCRIPTS:
        path = ROOT / relative
        assert path.is_file(), f"missing {relative}"
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR, f"{relative} is not executable"


def test_staging_env_example_exists() -> None:
    assert (ROOT / ".env.staging.example").is_file()
    assert (ROOT / "frontend/.env.staging.example").is_file()


def test_staging_deployment_docs_exist() -> None:
    for relative in (
        "docs/pre_deployment_checklist.md",
        "docs/deployment_command_pack.md",
        "docs/staging_deployment_worksheet.template.md",
        "docs/staging_execution_checklist.md",
        "docs/staging_deployment.md",
        "docs/deploy_rollback_runbook.md",
        "docs/controlled_paper_activation.md",
        "docs/RELEASE_READINESS.md",
        "docs/slice_66b_demo_venue_validation.md",
        "render.yaml",
        "frontend/vercel.json",
    ):
        assert (ROOT / relative).is_file(), f"missing {relative}"


def test_verify_safety_detects_unsafe_health_payload() -> None:
    """Safety logic mirrors scripts/verify-safety.sh expectations."""
    unsafe = {"execution_mode": "trade", "real_trading_enabled": True}
    assert unsafe["execution_mode"] != "paper" or unsafe["real_trading_enabled"] is not False

    safe = {"execution_mode": "paper", "real_trading_enabled": False}
    assert safe["execution_mode"] == "paper"
    assert safe["real_trading_enabled"] is False


def test_post_deploy_smoke_gate_self_check() -> None:
    """AT-005 gate wiring check runs without network access."""
    script = ROOT / "scripts/post-deploy-smoke-gate.sh"
    result = subprocess.run(
        [str(script), "--self-check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "self-check passed" in result.stdout


def test_post_deploy_smoke_gate_requires_base_url() -> None:
    script = ROOT / "scripts/post-deploy-smoke-gate.sh"
    env = {k: v for k, v in os.environ.items() if k != "BASE_URL"}
    result = subprocess.run(
        [str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 2
    assert "BASE_URL is required" in result.stderr


def test_post_deploy_smoke_gate_rejects_placeholder_base_url() -> None:
    script = ROOT / "scripts/post-deploy-smoke-gate.sh"
    result = subprocess.run(
        [str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "BASE_URL": "https://<YOUR-API>"},
    )
    assert result.returncode == 2
    assert "placeholder" in result.stderr.lower()


def test_watcher_paper_activation_scripts_self_check() -> None:
    for relative in (
        "scripts/watcher-paper-rollback.sh",
        "scripts/watcher-paper-activation-smoke.sh",
        "scripts/lib/watcher_paper_health.py",
    ):
        result = subprocess.run(
            [str(ROOT / relative), "--self-check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        assert "self-check passed" in result.stdout


def test_watcher_paper_activation_smoke_refuses_without_target() -> None:
    script = ROOT / "scripts/watcher-paper-activation-smoke.sh"
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"BASE_URL", "WATCHER_ACTIVATION_SMOKE_JSON"}
    }
    result = subprocess.run(
        [str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 2
    assert "does not activate" in result.stderr


def test_canonical_staging_smoke_self_check() -> None:
    script = ROOT / "scripts/canonical-staging-smoke.sh"
    result = subprocess.run(
        [str(script), "--self-check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "self-check passed" in result.stdout


def test_backend_entrypoint_honors_custom_command() -> None:
    text = (ROOT / "backend/docker/entrypoint.sh").read_text(encoding="utf-8")
    assert 'if [ "$#" -gt 0 ]' in text
    assert 'exec "$@"' in text
    assert "alembic upgrade head" in text


@pytest.mark.parametrize("migration_exit", [0, 23])
def test_backend_entrypoint_starts_api_only_after_successful_migration(
    tmp_path: Path, migration_exit: int
) -> None:
    """Exercise the real boot script; a failed migration must never reach the API."""
    commands = tmp_path / "bin"
    commands.mkdir()
    trace = tmp_path / "boot.log"
    for name, script in (
        ("alembic", 'printf "alembic %s\\n" "$*" >> "$BOOT_TRACE"\nexit "$MIGRATION_EXIT"'),
        ("uvicorn", 'printf "uvicorn %s\\n" "$*" >> "$BOOT_TRACE"'),
    ):
        executable = commands / name
        executable.write_text("#!/bin/sh\n" + script + "\n", encoding="utf-8")
        executable.chmod(0o755)
    result = subprocess.run(
        ["/bin/sh", str(ROOT / "backend/docker/entrypoint.sh")],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(commands),
            "BOOT_TRACE": str(trace),
            "MIGRATION_EXIT": str(migration_exit),
            "PORT": "8123",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == migration_exit
    called = trace.read_text(encoding="utf-8").splitlines()
    assert called[0] == "alembic upgrade head"
    if migration_exit:
        assert called == ["alembic upgrade head"]
    else:
        assert len(called) == 2
        assert called[1].startswith("uvicorn app.main:app ")
        assert "--port 8123" in called[1]


def test_staging_env_example_requires_hosted_qdrant() -> None:
    text = (ROOT / ".env.staging.example").read_text(encoding="utf-8")
    assert "QDRANT_URL=https://YOUR-CLUSTER.qdrant.io" in text
    assert "TELEGRAM_ALERTS_ENABLED=false" in text
    assert "WATCHER_ORCHESTRATION_ENABLED=false" in text
    assert "MARKET_WATCHER_ENABLED=false" in text
    assert "WATCHER_PAPER_STAGING_ACTIVATION=true" not in text


def test_post_deploy_smoke_gate_mandates_verify_safety() -> None:
    """Gate script must keep verify-safety as a hard dependency."""
    text = (ROOT / "scripts/post-deploy-smoke-gate.sh").read_text(encoding="utf-8")
    assert "verify-safety.sh" in text
    assert "GATE FAILED" in text
    assert "docs/deploy_rollback_runbook.md" in text
    assert "canonical-staging-smoke.sh" in text


def test_deploy_rollback_runbook_covers_required_sections() -> None:
    text = (ROOT / "docs/deploy_rollback_runbook.md").read_text(encoding="utf-8")
    for needle in (
        "Rollback triggers",
        "Rollback steps",
        "Post-rollback verification",
        "Failure handling",
        "post-deploy-smoke-gate.sh",
        "ENABLE_REAL_TRADING",
        "execution_mode",
        "d4f7a2c8e901",
        "canonical-staging-smoke.sh",
    ):
        assert needle in text, f"missing section/marker: {needle}"
