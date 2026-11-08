#!/usr/bin/env bash
# Lightweight authenticated smoke checks against a running stack.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BASE_URL="${BASE_URL:-http://localhost:8000}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:3000}"
BASE_URL="${BASE_URL%/}"
FRONTEND_URL="${FRONTEND_URL%/}"

EMAIL="${SMOKE_EMAIL:-smoke-$(date +%s)@example.com}"
PASSWORD="${SMOKE_PASSWORD:?Set SMOKE_PASSWORD (12+ chars; never commit it)}"

echo "E2E smoke — BASE_URL=${BASE_URL} FRONTEND_URL=${FRONTEND_URL}"

echo "Checking /health (paper mode)..."
health_json="$(curl -fsS "${BASE_URL}/health")"
python3 - <<'PY' "$health_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("execution_mode") == "paper", p
assert p.get("real_trading_enabled") is False, p
print("  OK: paper mode, real_trading_enabled=false")
PY

echo "Registering smoke user at ${BASE_URL}..."
register_json="$(curl -fsS -X POST "${BASE_URL}/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\",\"organization_name\":\"Smoke Org $(date +%s)\"}")" \
  || { echo "FAIL: register — is backend up? docker compose up -d" >&2; exit 1; }

token="$(python3 - <<'PY' "$register_json"
import json, sys
print(json.loads(sys.argv[1])["tokens"]["access_token"])
PY
)"
refresh="$(python3 - <<'PY' "$register_json"
import json, sys
print(json.loads(sys.argv[1])["tokens"].get("refresh_token") or "")
PY
)"

echo "Checking authenticated /auth/me..."
curl -fsS -H "Authorization: Bearer ${token}" "${BASE_URL}/auth/me" >/dev/null

echo "Checking /chat/message..."
curl -fsS -X POST -H "Authorization: Bearer ${token}" -H 'Content-Type: application/json' \
  "${BASE_URL}/chat/message" \
  -d '{"message":"Smoke test message"}' >/dev/null

echo "Checking /proposals..."
curl -fsS -H "Authorization: Bearer ${token}" "${BASE_URL}/proposals" >/dev/null

echo "Logging out..."
if [[ -n "$refresh" ]]; then
  curl -fsS -X POST -H "Authorization: Bearer ${token}" -H 'Content-Type: application/json' \
    "${BASE_URL}/auth/logout" \
    -d "{\"refresh_token\":\"${refresh}\"}" >/dev/null
else
  curl -fsS -X POST -H "Authorization: Bearer ${token}" -H 'Content-Type: application/json' \
    "${BASE_URL}/auth/logout" \
    -d '{}' >/dev/null
fi

echo "Checking protected route without token returns 401..."
status="$(curl -s -o /dev/null -w '%{http_code}' "${BASE_URL}/proposals")"
if [[ "$status" != "401" ]]; then
  echo "FAIL: expected 401 without token, got ${status}" >&2
  exit 1
fi

if curl -fsS "${FRONTEND_URL}/login" >/dev/null 2>&1; then
  echo "Frontend login page reachable at ${FRONTEND_URL}/login"
else
  echo "WARN: frontend not reachable at ${FRONTEND_URL}/login (start: cd frontend && npm run dev)" >&2
fi

echo "Smoke checks passed."
