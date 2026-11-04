"""Worker memory guard: allocator arenas and the soft RSS watchdog."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_image_caps_glibc_arenas() -> None:
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    runtime = dockerfile.split("AS runtime", 1)[1]
    assert "MALLOC_ARENA_MAX=2" in runtime


def test_staging_paper_worker_blueprint_caps_glibc_arenas() -> None:
    document = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    worker = next(
        item for item in document["services"] if item["name"] == "alphatrade-paper-worker-staging"
    )
    env = {item["key"]: item.get("value") for item in worker["envVars"]}
    assert env["MALLOC_ARENA_MAX"] == "2"
