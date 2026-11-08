#!/usr/bin/env bash
# Seed synthetic paper-only demo data on local or staging backend.
# Usage:
#   ./scripts/seed-demo.sh
#   DEMO_SEED_PASSWORD='...' BACKEND_URL=https://alphatrade-api-staging.onrender.com ./scripts/seed-demo.sh --api
#   DEMO_SEED_USE_SERVER_PASSWORD=true ./scripts/seed-demo.sh --api   # Render has DEMO_SEED_PASSWORD
#
# API mode: demo-org owner token (DEMO_OWNER_TOKEN), demo login with
# DEMO_SEED_PASSWORD, or (first-time bootstrap only, while the demo org does not
# exist) an auto-registered throwaway owner. The server refuses other owners.
# Password: the server-side DEMO_SEED_PASSWORD is authoritative outside local; a
# body password is honoured only when the backend runs with ENVIRONMENT=local.
# Never prints passwords.
#
# CLI mode (requires DATABASE_URL / local backend env):
#   cd backend && uv run python scripts/seed_demo.py
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MODE="${1:-cli}"

if [[ "$MODE" == "--api" ]]; then
  BACKEND_URL="${BACKEND_URL:-https://alphatrade-api-staging.onrender.com}"
  BACKEND_URL="${BACKEND_URL%/}"
  DEMO_EMAIL="${DEMO_EMAIL:-demo@alphatrade.ai}"
  DEMO_SEED_USE_SERVER_PASSWORD="${DEMO_SEED_USE_SERVER_PASSWORD:-false}"

  if [[ -z "${DEMO_SEED_PASSWORD:-}" && "$DEMO_SEED_USE_SERVER_PASSWORD" != "true" ]]; then
    echo "Set DEMO_SEED_PASSWORD or DEMO_SEED_USE_SERVER_PASSWORD=true for API seed mode." >&2
    exit 1
  fi

  obtain_owner_token() {
    if [[ -n "${DEMO_OWNER_TOKEN:-}" ]]; then
      return 0
    fi
    if [[ -n "${DEMO_SEED_PASSWORD:-}" ]]; then
      LOGIN_JSON="$(curl -fsS -X POST "${BACKEND_URL}/auth/login" \
        -H 'Content-Type: application/json' \
        -d "{\"email\":\"${DEMO_EMAIL}\",\"password\":\"${DEMO_SEED_PASSWORD}\"}" 2>/dev/null || true)"
      if [[ -n "$LOGIN_JSON" ]]; then
        DEMO_OWNER_TOKEN="$(python3 - <<'PY' "$LOGIN_JSON"
import json, sys
try:
    print(json.loads(sys.argv[1])["tokens"]["access_token"])
except (KeyError, json.JSONDecodeError):
    pass
PY
)"
      fi
      if [[ -n "${DEMO_OWNER_TOKEN:-}" ]]; then
        return 0
      fi
    fi
    # Reuse bootstrap owner when register would fail on duplicate org name.
    LEGACY_BOOT_EMAIL="${DEMO_BOOTSTRAP_EMAIL:-seed-bootstrap-1782212606@example.com}"
    if [[ -n "${DEMO_BOOTSTRAP_PASSWORD:-}" ]]; then
      LEGACY_LOGIN="$(curl -fsS -X POST "${BACKEND_URL}/auth/login" \
        -H 'Content-Type: application/json' \
        -d "{\"email\":\"${LEGACY_BOOT_EMAIL}\",\"password\":\"${DEMO_BOOTSTRAP_PASSWORD}\"}" 2>/dev/null || true)"
      if [[ -n "$LEGACY_LOGIN" ]]; then
        DEMO_OWNER_TOKEN="$(python3 - <<'PY' "$LEGACY_LOGIN"
import json, sys
try:
    print(json.loads(sys.argv[1])["tokens"]["access_token"])
except (KeyError, json.JSONDecodeError):
    pass
PY
)"
      fi
      if [[ -n "${DEMO_OWNER_TOKEN:-}" ]]; then
        return 0
      fi
    else
      echo "Note: DEMO_BOOTSTRAP_PASSWORD unset — skipping bootstrap owner login fallback." >&2
      echo "      Set DEMO_BOOTSTRAP_PASSWORD (same as STAGING_BOOTSTRAP_PASSWORD) or DEMO_OWNER_TOKEN." >&2
    fi
    BOOT_EMAIL="demo-seed-bootstrap-$(date +%s)@example.com"
    # Throwaway bootstrap owner: random per run, never a committed literal (M20).
    BOOT_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
    BOOT_ORG="Demo Seed Bootstrap $(date +%s)"
    REG_JSON="$(curl -fsS -X POST "${BACKEND_URL}/auth/register" \
      -H 'Content-Type: application/json' \
      -d "{\"email\":\"${BOOT_EMAIL}\",\"password\":\"${BOOT_PASS}\",\"organization_name\":\"${BOOT_ORG}\"}")"
    DEMO_OWNER_TOKEN="$(python3 - <<'PY' "$REG_JSON"
import json, sys
print(json.loads(sys.argv[1])["tokens"]["access_token"])
PY
)"
  }

  obtain_owner_token

  if [[ -n "${DEMO_SEED_PASSWORD:-}" ]]; then
    SEED_BODY="$(python3 - <<'PY'
import json, os
print(json.dumps({"password": os.environ["DEMO_SEED_PASSWORD"]}))
PY
)"
  else
    SEED_BODY='{}'
  fi

  curl -fsS -X POST "${BACKEND_URL}/demo/seed" \
    -H "Authorization: Bearer ${DEMO_OWNER_TOKEN}" \
    -H 'Content-Type: application/json' \
    -d "$SEED_BODY" | python3 -m json.tool
  echo "Demo seed via API OK."
  exit 0
fi

cd backend
uv run python scripts/seed_demo.py
