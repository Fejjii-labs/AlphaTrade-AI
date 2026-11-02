#!/usr/bin/env python3
"""Pre-deploy env drift check between the AlphaTrade API and paper worker on Render.

Read-only. Uses RENDER_API_KEY. Prints key NAMES only; shared values are
compared by SHA-256 and never printed. Exit 0 = no drift, 1 = drift, 2 = error.

    RENDER_API_KEY=... python scripts/check_env_drift.py \
        [--api-service srv-...] [--worker-service srv-...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from app.core.required_env import SHARED_VALUE_KEYS, Role, missing_keys

RENDER_API = "https://api.render.com/v1"
DEFAULT_API_SERVICE = "srv-d8fvbcd7vvec739mc060"
DEFAULT_WORKER_SERVICE = "srv-daqej7o473hc73fsc1k0"

Fetch = Callable[[str, str], object]


def _http_get(url: str, token: str) -> object:
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_env(service_id: str, token: str, fetch: Fetch = _http_get) -> dict[str, str]:
    env: dict[str, str] = {}
    cursor = ""
    while True:
        url = f"{RENDER_API}/services/{service_id}/env-vars?limit=100"
        if cursor:
            url += f"&cursor={cursor}"
        page = fetch(url, token)
        if not isinstance(page, list) or not page:
            break
        for item in page:
            var = item.get("envVar", item)
            env[var["key"]] = var.get("value") or ""
        cursor = page[-1].get("cursor", "")
        if not cursor or len(page) < 100:
            break
    return env


def _digest(value: str) -> str:
    return hashlib.sha256(value.strip().encode()).hexdigest()


def compare(api_env: Mapping[str, str], worker_env: Mapping[str, str]) -> list[str]:
    """Return human-readable drift findings (names only)."""
    findings: list[str] = []
    for role, env in ((Role.API, api_env), (Role.PAPER_WORKER, worker_env)):
        for key in missing_keys(role, env):
            findings.append(f"missing on {role.value}: {key}")
    for key in SHARED_VALUE_KEYS:
        a, w = (api_env.get(key) or "").strip(), (worker_env.get(key) or "").strip()
        if not a and not w:
            continue
        if not a or not w:
            side = Role.API.value if not a else Role.PAPER_WORKER.value
            findings.append(f"shared key set on one side only (absent on {side}): {key}")
        elif _digest(a) != _digest(w):
            findings.append(f"shared value differs (hash mismatch): {key}")
    return findings


def main(argv: list[str] | None = None, fetch: Fetch = _http_get) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-service", default=os.environ.get("RENDER_API_SERVICE_ID", DEFAULT_API_SERVICE)
    )
    parser.add_argument(
        "--worker-service",
        default=os.environ.get("RENDER_WORKER_SERVICE_ID", DEFAULT_WORKER_SERVICE),
    )
    args = parser.parse_args(argv)
    token = os.environ.get("RENDER_API_KEY", "")
    if not token:
        print("error: RENDER_API_KEY is not set", file=sys.stderr)
        return 2
    try:
        api_env = fetch_env(args.api_service, token, fetch)
        worker_env = fetch_env(args.worker_service, token, fetch)
    except Exception as exc:
        print(f"error: Render API request failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    findings = compare(api_env, worker_env)
    if findings:
        print(f"ENV DRIFT ({len(findings)}):")
        for line in findings:
            print(f"  - {line}")
        return 1
    print("OK: no env drift between api and paper_worker")
    return 0


if __name__ == "__main__":
    sys.exit(main())
