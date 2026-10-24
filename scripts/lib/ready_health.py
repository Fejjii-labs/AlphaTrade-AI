#!/usr/bin/env python3
"""Exit non-zero when /health/ready is not actually ready.

Operators key on ``status`` and ``degraded_providers``. A payload whose
status is anything other than ``ready``, or whose degraded list is not
empty, is an alert. Printing the names is not enough.

``--allow-degraded-ready`` waives only the historical case: ``ready`` is
false, ``status`` is not ``ready``, and ``degraded_providers`` is empty.
It does not waive a banned or degraded market-data provider. That list is
non-empty while ``ready`` stays true so Render does not restart.

This script does not call the network and does not read secrets.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping


def readiness_decision(
    payload: Mapping[str, object],
    *,
    allow_degraded_ready: bool,
) -> tuple[int, str]:
    """Return ``(exit_code, message)`` for one readiness payload."""

    status = payload.get("status")
    names = payload.get("degraded_providers") or []
    if not isinstance(names, list):
        return 1, "ALERT: degraded_providers is not a list"
    rendered = ",".join(str(name) for name in names)
    if names:
        return 1, f"ALERT: status={status!r} degraded_providers={rendered}"
    if status != "ready":
        if allow_degraded_ready and payload.get("ready") is False:
            return 0, (f"WARN: degraded readiness allowed status={status!r} degraded_providers=")
        return 1, f"ALERT: status={status!r}"
    if payload.get("ready") is not True:
        return 1, "ALERT: ready is not true"
    unavailable = payload.get("providers_unavailable")
    if unavailable not in (None, 0):
        return 1, f"ALERT: providers_unavailable={unavailable!r}"
    return 0, "OK: status=ready degraded_providers="


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    allow = False
    if "--allow-degraded-ready" in args:
        allow = True
        args = [item for item in args if item != "--allow-degraded-ready"]
    raw = args[0] if args else sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print("ALERT: readiness payload is not JSON", file=sys.stderr)
        return 1
    if not isinstance(payload, dict):
        print("ALERT: readiness payload must be an object", file=sys.stderr)
        return 1
    code, message = readiness_decision(payload, allow_degraded_ready=allow)
    stream = sys.stdout if code == 0 else sys.stderr
    print(message, file=stream)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
