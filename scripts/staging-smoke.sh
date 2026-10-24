#!/usr/bin/env bash
# Staging deployment smoke test — health, auth (bearer or cookie), chat, CORS, safety.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=scripts/smoke-auth-helpers.sh
source "${ROOT_DIR}/scripts/smoke-auth-helpers.sh"

BASE_URL="${BASE_URL:-http://localhost:8000}"
FRONTEND_URL="${FRONTEND_URL:-}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-60}"
COOKIE_MODE="${COOKIE_MODE:-false}"
ALLOW_DEGRADED_READY="${ALLOW_DEGRADED_READY:-false}"
SKIP_REGISTER="${SKIP_REGISTER:-false}"
EMAIL="${SMOKE_EMAIL:-staging-smoke-$(date +%s)@example.com}"
PASSWORD="${SMOKE_PASSWORD:-secure-password-1}"
ORG_NAME="${SMOKE_ORG:-Staging Smoke Org $(date +%s)}"

COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

curl_api() {
  curl -fsS "$@"
}

curl_api_cookie() {
  if [[ "$COOKIE_MODE" == "true" ]]; then
    curl_api -b "$COOKIE_JAR" -c "$COOKIE_JAR" "$@"
  else
    curl_api "$@"
  fi
}

BASE_URL="${BASE_URL%/}"

if [[ "$BASE_URL" == *"<"* ]]; then
  echo "Replace <BACKEND_URL> placeholder (docs/deployment_command_pack.md)." >&2
  exit 1
fi

echo "Staging smoke — BASE_URL=${BASE_URL} COOKIE_MODE=${COOKIE_MODE}"

if [[ "$COOKIE_MODE" == "true" && "$BASE_URL" != https://* ]]; then
  echo "Note: staging cross-domain auth expects HTTPS API + AUTH_COOKIE_SAMESITE=none on Render." >&2
fi

if [[ "$COOKIE_MODE" == "true" && -z "$FRONTEND_URL" ]]; then
  echo "Tip: set FRONTEND_URL=https://your-app.vercel.app to test CORS preflight." >&2
fi

echo "Waiting for backend..."
attempt=0
until curl_api "${BASE_URL}/health" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [[ "$attempt" -ge "$MAX_ATTEMPTS" ]]; then
    echo "Backend did not become reachable within ${MAX_ATTEMPTS} attempts." >&2
    exit 1
  fi
  sleep 2
done

echo "1/12 — /health"
health_json="$(curl_api "${BASE_URL}/health")"
python3 "${ROOT_DIR}/scripts/lib/watcher_paper_health.py" "$health_json"
python3 - "$health_json" <<'PY'
import json, sys
payload = json.loads(sys.argv[1])
git_sha = payload.get("git_sha")
print(f"  git_sha={git_sha or 'null'}")
PY

echo "2/12 — /health/ready"
ready_json="$(curl_api "${BASE_URL}/health/ready")"
ready_check=(python3 "${ROOT_DIR}/scripts/lib/ready_health.py" "$ready_json")
case "${ALLOW_DEGRADED_READY}" in
  1|true|TRUE|yes|YES) ready_check+=(--allow-degraded-ready) ;;
esac
"${ready_check[@]}"

echo "3/12 — /providers/status"
providers_json="$(curl_api "${BASE_URL}/providers/status")"
python3 - <<'PY' "$providers_json"
import json, sys
payload = json.loads(sys.argv[1])
providers = payload.get("providers") or []
assert providers, payload
exchange = next((p for p in providers if p.get("kind") == "exchange"), None)
assert exchange is not None, providers
print(f"  OK: {len(providers)} providers; exchange={exchange.get('name')}")
PY

if [[ -n "$FRONTEND_URL" ]]; then
  echo "4/12 — CORS preflight (FRONTEND_URL=${FRONTEND_URL})"
  cors_status="$(curl -sS -o /dev/null -w '%{http_code}' -X OPTIONS "${BASE_URL}/health" \
    -H "Origin: ${FRONTEND_URL}" \
    -H "Access-Control-Request-Method: GET" \
    -H "Access-Control-Request-Headers: Authorization,Content-Type" || true)"
  if [[ "$cors_status" != "200" && "$cors_status" != "204" ]]; then
    echo "  WARN: OPTIONS /health returned HTTP ${cors_status}" >&2
    echo "        Fix: set CORS_ORIGINS=${FRONTEND_URL} on backend (exact origin, HTTPS, no trailing slash), redeploy." >&2
  else
    echo "  OK: CORS preflight HTTP ${cors_status}"
  fi
else
  echo "4/12 — CORS preflight skipped (set FRONTEND_URL to test)"
fi

if [[ "$SKIP_REGISTER" == "true" ]]; then
  echo "5/12 — register skipped (SKIP_REGISTER=true)"
  if [[ -z "${SMOKE_ACCESS_TOKEN:-}" ]]; then
    echo "SMOKE_ACCESS_TOKEN required when SKIP_REGISTER=true" >&2
    exit 1
  fi
  login_token="$SMOKE_ACCESS_TOKEN"
else
  echo "5/12 — register"
  register_json="$(curl_api_cookie -X POST "${BASE_URL}/auth/register" \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\",\"organization_name\":\"${ORG_NAME}\"}")"
  if [[ "$COOKIE_MODE" == "true" ]]; then
    if ! grep -q alphatrade_refresh "$COOKIE_JAR" 2>/dev/null; then
      echo "  WARN: refresh cookie not set after register." >&2
      echo "        Fix: AUTH_REFRESH_COOKIE_ENABLED=true, AUTH_COOKIE_SECURE=true, AUTH_COOKIE_SAMESITE=none on API." >&2
    else
      echo "  OK: refresh cookie present"
    fi
  fi
fi

echo "6/12 — login"
if [[ "$SKIP_REGISTER" == "true" ]]; then
  echo "  skipped (SKIP_REGISTER=true)"
else
  if ! smoke_login_after_register "$register_json"; then
    echo "FAIL: login step failed." >&2
    exit 1
  fi
  login_token="$SMOKE_ACCESS_TOKEN"
fi

echo "7/12 — protected chat"
curl_api -X POST -H "Authorization: Bearer ${login_token}" -H 'Content-Type: application/json' \
  "${BASE_URL}/chat/message" \
  -d '{"message":"Staging smoke test message"}' >/dev/null
echo "  OK"

if [[ "$COOKIE_MODE" == "true" ]]; then
  echo "8/12 — refresh (cookie)"
  refresh_json="$(curl_api_cookie -X POST "${BASE_URL}/auth/refresh" \
    -H 'Content-Type: application/json' \
    -d '{}')"
  login_token="$(python3 - <<'PY' "$refresh_json"
import json, sys
body = json.loads(sys.argv[1])
tokens = body.get("tokens") or body
print(tokens["access_token"])
PY
)"
  echo "  OK: rotated access token"
else
  echo "8/12 — refresh skipped (COOKIE_MODE=false)"
fi

if [[ "${INCLUDE_ANALYTICS:-false}" == "true" ]]; then
  echo "9/12 — analytics smoke (INCLUDE_ANALYTICS=true)"
  SKIP_REGISTER=true \
    SMOKE_ACCESS_TOKEN="${login_token}" \
    SKIP_SAFETY=true \
    COOKIE_MODE="${COOKIE_MODE}" \
    BASE_URL="${BASE_URL}" \
    ./scripts/analytics-smoke.sh
else
  echo "9/12 — analytics smoke skipped (set INCLUDE_ANALYTICS=true to enable)"
fi

if [[ "${INCLUDE_STRATEGY_QUALITY:-false}" == "true" ]]; then
  echo "9b/12 — strategy quality smoke (INCLUDE_STRATEGY_QUALITY=true)"
  SKIP_REGISTER=true \
    SMOKE_ACCESS_TOKEN="${login_token}" \
    SKIP_SAFETY=true \
    COOKIE_MODE="${COOKIE_MODE}" \
    BASE_URL="${BASE_URL}" \
    ./scripts/strategy-quality-smoke.sh
else
  echo "9b/12 — strategy quality smoke skipped (set INCLUDE_STRATEGY_QUALITY=true to enable)"
fi

echo "10/12 — logout"
if [[ "$COOKIE_MODE" == "true" ]]; then
  curl_api_cookie -X POST -H "Authorization: Bearer ${login_token}" -H 'Content-Type: application/json' \
    "${BASE_URL}/auth/logout" \
    -d '{}' >/dev/null
else
  refresh="$(python3 - <<'PY' "${SMOKE_SESSION_JSON:-$register_json}" 2>/dev/null || true
import json, sys
try:
    body = json.loads(sys.argv[1])
    print(body["tokens"].get("refresh_token") or "")
except Exception:
    print("")
PY
)"
  if [[ -n "$refresh" ]]; then
    curl_api -X POST -H 'Content-Type: application/json' \
      "${BASE_URL}/auth/logout" \
      -d "{\"refresh_token\":\"${refresh}\"}" >/dev/null
  else
    curl_api -X POST -H "Authorization: Bearer ${login_token}" -H 'Content-Type: application/json' \
      "${BASE_URL}/auth/logout" \
      -d '{}' >/dev/null
  fi
fi
echo "  OK"

echo "11/12 — deployment safety invariants"
BASE_URL="${BASE_URL}" ./scripts/verify-safety.sh

echo "12/12 — staging smoke complete"
echo "Staging smoke checks passed."
