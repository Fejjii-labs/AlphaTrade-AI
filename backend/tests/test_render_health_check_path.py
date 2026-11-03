"""Pin render.yaml healthCheckPath to /health.

/health/paper-worker returns 503 while the worker is degraded. If Render's
health check pointed there, every degraded boot would restart-loop.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_render_health_check_paths_stay_on_health() -> None:
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text())
    paths = {
        svc["name"]: svc.get("healthCheckPath")
        for svc in blueprint["services"]
        if "healthCheckPath" in svc
    }
    assert paths, "expected at least one service with healthCheckPath"
    for name, path in paths.items():
        assert path == "/health", f"{name} healthCheckPath must stay /health, got {path!r}"
        assert "paper-worker" not in path
