"""Export-only checks; never start the application lifespan or touch its database."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def test_export_is_offline_deterministic_and_ignores_inherited_configuration(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    (tmp_path / ".env").write_text(
        "EXECUTION_MODE=trade\nENABLE_REAL_TRADING=true\nPROVIDER_MODE=live\n"
    )
    artifacts = [tmp_path / "first.json", tmp_path / "second.json"]
    for output in artifacts:
        env = {
            **os.environ,
            "ENABLE_REAL_TRADING": "true",
            "EXECUTION_MODE": "trade",
            "ENVIRONMENT": "production",
            "PROVIDER_MODE": "live",
            "DATABASE_URL": "deliberately-invalid-inherited-url",
            "RATE_LIMIT_USE_REDIS": "true",
            "MARKET_DATA_CACHE_USE_REDIS": "true",
        }
        result = subprocess.run(
            [sys.executable, str(root / "scripts/export_openapi.py"), "--output", str(output)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "redis_connect" not in result.stdout
        assert "deliberately-invalid" not in output.read_text()
    assert artifacts[0].read_bytes() == artifacts[1].read_bytes()
    schema = json.loads(artifacts[0].read_text())
    assert {"user_message_id", "assistant_message_id"}.issubset(
        schema["components"]["schemas"]["AgentTurnResult"]["required"]
    )
    assert (
        schema["components"]["schemas"]["AgentTurnResult"]["properties"]["authority_mutated"][
            "type"
        ]
        == "boolean"
    )
    assert "setup_type" in schema["components"]["schemas"]["UserStrategyUpdate"]["properties"]
    print("OpenAPI SHA256", hashlib.sha256(artifacts[0].read_bytes()).hexdigest())
