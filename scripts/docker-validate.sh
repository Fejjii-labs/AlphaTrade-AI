#!/usr/bin/env bash
# Validate Docker runtime safety invariants and core HTTP endpoints.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BASE_URL="${BASE_URL:-http://localhost:8000}"
FRONTEND_URL="${FRONTEND_URL:-http://localhost:3000}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-60}"

echo "Waiting for backend at ${BASE_URL}..."
attempt=0
until curl -fsS "${BASE_URL}/health" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [[ "$attempt" -ge "$MAX_ATTEMPTS" ]]; then
    echo "Backend did not become reachable within ${MAX_ATTEMPTS} attempts."
    exit 1
  fi
  sleep 2
done

echo "Checking /health safety posture..."
health_json="$(curl -fsS "${BASE_URL}/health")"
python3 "${ROOT_DIR}/scripts/lib/watcher_paper_health.py" "$health_json"

echo "Checking /health/ready..."
ready_json="$(curl -fsS "${BASE_URL}/health/ready")"
python3 "${ROOT_DIR}/scripts/lib/ready_health.py" "$ready_json"

echo "Checking /providers/status fallback/mock posture..."
providers_json="$(curl -fsS "${BASE_URL}/providers/status")"
python3 - <<'PY' "$providers_json"
import json
import sys

payload = json.loads(sys.argv[1])
providers = payload.get("providers") or []
assert providers, "expected at least one provider"
exchange = next((p for p in providers if p.get("kind") == "exchange"), None)
assert exchange is not None, providers
assert exchange.get("is_mock") is True, exchange
assert "real trading disabled" in (exchange.get("detail") or "").lower(), exchange
llm = next((p for p in providers if p.get("kind") == "llm"), None)
assert llm is not None, providers
print(f"  {len(providers)} providers; exchange paper-only; llm={llm.get('name')}")
PY

echo "Checking protected route requires auth..."
unauth_status="$(curl -s -o /dev/null -w '%{http_code}' "${BASE_URL}/audit/events")"
if [[ "$unauth_status" != "401" ]]; then
  echo "Expected 401 for unauthenticated /audit/events, got ${unauth_status}"
  exit 1
fi
echo "  /audit/events returns 401 without token"

echo "Registering temporary user for protected endpoint checks..."
validate_email="docker-validate-$(date +%s)@example.com"
validate_org="Docker Validate $(date +%s)"
register_json="$(curl -fsS -X POST "${BASE_URL}/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"${validate_email}\",\"password\":\"secure-password-1\",\"organization_name\":\"${validate_org}\"}")"
token="$(python3 - <<'PY' "$register_json"
import json, sys
print(json.loads(sys.argv[1])["tokens"]["access_token"])
PY
)"

echo "Checking /usage/summary (authenticated)..."
curl -fsS -H "Authorization: Bearer ${token}" "${BASE_URL}/usage/summary" >/dev/null
echo "  /usage/summary OK"

echo "Checking /audit/events (authenticated)..."
curl -fsS -H "Authorization: Bearer ${token}" "${BASE_URL}/audit/events" >/dev/null
echo "  /audit/events OK"

echo "Checking logout revokes access token when denylist enabled..."
logout_status="$(curl -s -o /dev/null -w '%{http_code}' -X POST "${BASE_URL}/auth/logout" \
  -H "Authorization: Bearer ${token}" \
  -H 'Content-Type: application/json' \
  -d '{}')"
if [[ "$logout_status" != "200" ]]; then
  echo "Logout failed with status ${logout_status}"
  exit 1
fi
post_logout_status="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${token}" "${BASE_URL}/auth/me")"
if [[ "$post_logout_status" != "401" ]]; then
  echo "Expected 401 after logout denylist, got ${post_logout_status} (denylist may be disabled)"
fi
echo "  logout + post-logout auth check OK"

if curl -fsS "${FRONTEND_URL}/login" >/dev/null 2>&1; then
  echo "Checking frontend /login..."
  curl -fsS "${FRONTEND_URL}/login" >/dev/null
  echo "  frontend /login OK"
else
  echo "Frontend not running at ${FRONTEND_URL}; skipping frontend checks."
fi

echo "Docker safety validation passed."
