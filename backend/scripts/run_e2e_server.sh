#!/usr/bin/env bash
# Start backend for Playwright E2E with SQLite (no Docker required).
set -euo pipefail

cd "$(dirname "$0")/.."

export DATABASE_URL="${DATABASE_URL:-sqlite+pysqlite:///./.e2e-alphatrade.db}"
export JWT_SECRET="${JWT_SECRET:-e2e-test-secret-at-least-32-characters-long}"
export PYTHONPATH=src
export RATE_LIMIT_USE_REDIS="${RATE_LIMIT_USE_REDIS:-false}"
export MARKET_DATA_CACHE_USE_REDIS="${MARKET_DATA_CACHE_USE_REDIS:-false}"
export PROVIDER_MODE="${PROVIDER_MODE:-mock}"
export MARKET_DATA_PROVIDER="${MARKET_DATA_PROVIDER:-mock}"
export JOURNAL_RAG_SYNC_ENABLED="${JOURNAL_RAG_SYNC_ENABLED:-true}"
export EMAIL_AUTO_VERIFY_LOCAL="${EMAIL_AUTO_VERIFY_LOCAL:-true}"
export ACCESS_TOKEN_DENYLIST_ENABLED="${ACCESS_TOKEN_DENYLIST_ENABLED:-false}"
export LOG_JSON="${LOG_JSON:-false}"
export CORS_ORIGINS="${CORS_ORIGINS:-http://localhost:3000,http://127.0.0.1:3000}"

rm -f .e2e-alphatrade.db
uv run python scripts/init_e2e_db.py
exec uv run python scripts/run_e2e_api.py
