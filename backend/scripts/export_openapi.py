"""Export deterministic HTTP schemas without runtime credentials or provider I/O."""

from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path


def export_schema() -> dict:
    # This runs in a separate process. Never use the caller's database/provider credentials.
    os.environ.clear()
    os.environ.update(
        {
            "ENVIRONMENT": "local",
            "PROVIDER_MODE": "mock",
            "EXECUTION_MODE": "paper",
            "ENABLE_REAL_TRADING": "false",
            "EXCHANGE_MODE": "paper_internal",
            "PERPETUAL_EVIDENCE_SOURCE": "replay",
            "WORKER_ENABLED": "false",
            "RATE_LIMIT_USE_REDIS": "false",
            "MARKET_DATA_CACHE_USE_REDIS": "false",
            "DATABASE_URL": "sqlite+pysqlite:///:memory:",
            "JWT_SECRET": "offline-schema-only",
        }
    )

    def deny_network(*args: object, **kwargs: object) -> None:
        raise RuntimeError("OpenAPI export must not perform network/provider I/O")

    socket.socket.connect = deny_network  # type: ignore[method-assign]
    socket.socket.connect_ex = deny_network  # type: ignore[method-assign]
    socket.getaddrinfo = deny_network  # type: ignore[assignment]
    from app.core.config import Settings

    # Also prevent a local .env file from supplying deployed credentials/settings.
    Settings.model_config = {**Settings.model_config, "env_file": None}
    from app.main import app

    # Do not enter lifespan, construct a TestClient or resolve request dependencies.
    return app.openapi()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    schema = json.dumps(export_schema(), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(schema)


if __name__ == "__main__":
    main()
