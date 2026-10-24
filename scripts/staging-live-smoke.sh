#!/usr/bin/env bash
# Slice 48 — live staging smoke (health, safety, CORS, frontend routes, auth, notifications).
# Usage:
#   BACKEND_URL=https://your-api.onrender.com ./scripts/staging-live-smoke.sh
#   BACKEND_URL=... FRONTEND_URL=https://your-app.vercel.app ./scripts/staging-live-smoke.sh
#   SMOKE_EMAIL=user@example.com SMOKE_PASSWORD='...' ./scripts/staging-live-smoke.sh
#
# Does not print secrets. Does not hardcode credentials.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=scripts/smoke-auth-helpers.sh
source "${ROOT_DIR}/scripts/smoke-auth-helpers.sh"

BACKEND_URL="${BACKEND_URL:-${BASE_URL:-http://localhost:8000}}"
FRONTEND_URL="${FRONTEND_URL:-}"
ALLOW_DEGRADED_READY="${ALLOW_DEGRADED_READY:-true}"
COOKIE_MODE="${COOKIE_MODE:-false}"
SKIP_REGISTER="${SKIP_REGISTER:-false}"
EMAIL="${SMOKE_EMAIL:-staging-live-$(date +%s)@example.com}"
PASSWORD="${SMOKE_PASSWORD:-secure-password-1}"
ORG_NAME="${SMOKE_ORG:-Staging Live Smoke $(date +%s)}"

BACKEND_URL="${BACKEND_URL%/}"
FRONTEND_URL="${FRONTEND_URL%/}"
BASE_URL="${BACKEND_URL}"
export BASE_URL

if [[ "$BACKEND_URL" == *"<"* ]]; then
  echo "Replace BACKEND_URL placeholder (see docs/deployment_command_pack.md)." >&2
  exit 1
fi

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

assert_no_secrets() {
  python3 - <<'PY' "$1"
import json, re, sys
raw = sys.argv[1]
forbidden = [
    r"TELEGRAM_BOT_TOKEN",
    r"ALERT_WEBHOOK_SECRET",
    r"sk-[a-zA-Z0-9]{20,}",
    r"whsec_[a-zA-Z0-9]{16,}",
    r"bot[0-9]{8,}:[A-Za-z0-9_-]{20,}",
    r"postgresql\+psycopg://[^\"]+",
    r"rediss?://[^\"]+",
]
for pattern in forbidden:
    assert re.search(pattern, raw, re.IGNORECASE) is None, f"Secret-like value in response: {pattern}"
PY
}

auth_header() {
  printf 'Authorization: Bearer %s' "$TOKEN"
}

echo "Staging live smoke — BACKEND_URL=${BACKEND_URL} FRONTEND_URL=${FRONTEND_URL:-<none>}"

echo "1/16 — /health"
health_json="$(curl_api "${BACKEND_URL}/health")"
python3 - <<'PY' "$health_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("execution_mode") == "paper", p
assert p.get("real_trading_enabled") is False, p
print(f"  OK: execution_mode=paper real_trading_enabled=false environment={p.get('environment')!r}")
PY
assert_no_secrets "$health_json"

echo "2/16 — /health/ready"
ready_json="$(curl_api "${BACKEND_URL}/health/ready")"
ready_check=(python3 "${ROOT_DIR}/scripts/lib/ready_health.py" "$ready_json")
case "${ALLOW_DEGRADED_READY}" in
  1|true|TRUE|yes|YES) ready_check+=(--allow-degraded-ready) ;;
esac
"${ready_check[@]}"
assert_no_secrets "$ready_json"

echo "3/16 — verify-safety invariants"
BASE_URL="${BACKEND_URL}" ./scripts/verify-safety.sh

if [[ -n "$FRONTEND_URL" ]]; then
  echo "4/16 — frontend loads (${FRONTEND_URL})"
  frontend_html="$(curl -sS "${FRONTEND_URL}/" || true)"
  frontend_code="$(curl -sS -o /dev/null -w '%{http_code}' "${FRONTEND_URL}/" || true)"
  if [[ "$frontend_code" == "200" ]]; then
    echo "  OK: frontend HTTP ${frontend_code}"
  else
    echo "  WARN: frontend HTTP ${frontend_code} (check Vercel root directory=frontend and deployment protection)"
  fi
  if printf '%s' "$frontend_html" | rg -q '_next/static|<title>AlphaTrade AI</title>'; then
    echo "  OK: Next.js AlphaTrade frontend detected"
  elif printf '%s' "$frontend_html" | rg -q '<title>Your Project</title>'; then
    echo "  WARN: placeholder app detected — use production alias https://alpha-trade-ai-eight.vercel.app"
  else
    echo "  WARN: could not confirm Next.js app markup"
  fi
else
  echo "4/16 — frontend check skipped (set FRONTEND_URL to enable)"
fi

if [[ -n "$FRONTEND_URL" ]]; then
  echo "5/16 — frontend /login route"
  login_code="$(curl -sS -o /dev/null -w '%{http_code}' "${FRONTEND_URL}/login" || true)"
  if [[ "$login_code" == "200" ]]; then
    echo "  OK: /login HTTP ${login_code}"
  else
    echo "  WARN: /login HTTP ${login_code} — wrong Vercel project or deployment protection"
  fi
else
  echo "5/16 — frontend /login skipped"
fi

if [[ -n "$FRONTEND_URL" ]]; then
  echo "6/16 — CORS preflight"
  cors_status="$(curl -sS -o /dev/null -w '%{http_code}' -X OPTIONS "${BACKEND_URL}/health" \
    -H "Origin: ${FRONTEND_URL}" \
    -H "Access-Control-Request-Method: GET" \
    -H "Access-Control-Request-Headers: Authorization,Content-Type" || true)"
  if [[ "$cors_status" == "200" || "$cors_status" == "204" ]]; then
    echo "  OK: CORS preflight HTTP ${cors_status}"
  else
    echo "  WARN: CORS preflight HTTP ${cors_status} — set CORS_ORIGINS=${FRONTEND_URL} on backend and redeploy"
  fi
else
  echo "6/16 — CORS preflight skipped"
fi

if [[ "$SKIP_REGISTER" == "true" ]]; then
  echo "7/16 — register skipped (SKIP_REGISTER=true)"
  if [[ -z "${SMOKE_ACCESS_TOKEN:-}" ]]; then
    echo "SMOKE_ACCESS_TOKEN required when SKIP_REGISTER=true" >&2
    exit 1
  fi
  TOKEN="$SMOKE_ACCESS_TOKEN"
else
  echo "7/16 — register"
  register_json="$(curl_api_cookie -X POST "${BACKEND_URL}/auth/register" \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\",\"organization_name\":\"${ORG_NAME}\"}")"
  assert_no_secrets "$register_json"
  if ! smoke_login_after_register "$register_json"; then
    echo "FAIL: login step failed." >&2
    exit 1
  fi
  TOKEN="$SMOKE_ACCESS_TOKEN"
fi

echo "8/16 — GET /dashboard/summary"
dash_json="$(curl_api -H "$(auth_header)" "${BACKEND_URL}/dashboard/summary")"
python3 - <<'PY' "$dash_json"
import json, sys
p = json.loads(sys.argv[1])
safety = p.get("safety") or {}
assert safety.get("execution_mode") == "paper", p
assert safety.get("real_trading_enabled") is False, p
print("  OK: dashboard summary paper-only")
PY
assert_no_secrets "$dash_json"

echo "9/16 — GET /risk/settings"
risk_json="$(curl_api -H "$(auth_header)" "${BACKEND_URL}/risk/settings")"
python3 - <<'PY' "$risk_json"
import json, sys
p = json.loads(sys.argv[1])
assert "max_trades_per_day" in p, p
print("  OK")
PY
assert_no_secrets "$risk_json"

echo "10/16 — notification preferences + delivery status"
prefs_json="$(curl_api -H "$(auth_header)" "${BACKEND_URL}/notifications/preferences")"
python3 - <<'PY' "$prefs_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("webhook_enabled") is False, p
assert p.get("telegram_enabled") is False, p
print("  OK: external channels disabled by default")
PY
assert_no_secrets "$prefs_json"

status_json="$(curl_api -H "$(auth_header)" "${BACKEND_URL}/alerts/delivery-status")"
python3 - <<'PY' "$status_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("paper_only") is True, p
assert p.get("effective_external_enabled") is False, p
print("  OK: effective_external_enabled=false")
PY
assert_no_secrets "$status_json"

echo "11/16 — POST /notifications/test (safe skip external)"
test_json="$(curl_api -X POST -H "$(auth_header)" "${BACKEND_URL}/notifications/test")"
python3 - <<'PY' "$test_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("paper_only") is True, p
print("  OK")
PY
assert_no_secrets "$test_json"

echo "12/16 — GET /alerts/delivery-summary"
summary_json="$(curl_api -H "$(auth_header)" "${BACKEND_URL}/alerts/delivery-summary")"
python3 - <<'PY' "$summary_json"
import json, sys
p = json.loads(sys.argv[1])
for key in ("total", "pending", "delivered", "failed", "disabled", "skipped"):
    assert key in p, p
print("  OK")
PY
assert_no_secrets "$summary_json"

echo "13/16 — market watcher + bridge status"
mw_json="$(curl_api -H "$(auth_header)" "${BACKEND_URL}/market-watcher/status")"
python3 - <<'PY' "$mw_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("paper_only") is True, p
print(f"  OK: env_enabled={p.get('env_enabled')}")
PY
assert_no_secrets "$mw_json"

bridge_json="$(curl_api -H "$(auth_header)" "${BACKEND_URL}/market-watcher/bridge/status")"
python3 - <<'PY' "$bridge_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("real_trading_enabled") is False, p
print(f"  OK: bridge env_enabled={p.get('env_enabled')}")
PY
assert_no_secrets "$bridge_json"

echo "14/16 — protected chat (read-only safe message)"
curl_api -X POST -H "$(auth_header)" -H 'Content-Type: application/json' \
  "${BACKEND_URL}/chat/message" \
  -d '{"message":"Staging live smoke: what is paper mode?"}' >/dev/null
echo "  OK"

echo "15/16 — environment still paper-only"
health_json="$(curl_api "${BACKEND_URL}/health")"
python3 - <<'PY' "$health_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("execution_mode") == "paper", p
assert p.get("real_trading_enabled") is False, p
env = p.get("environment")
if env == "staging":
    print("  OK: environment=staging")
else:
    print(f"  WARN: environment={env!r} (set ENVIRONMENT=staging on Render)")
PY

echo "16/16 — real_trading_enabled still false"
health_json="$(curl_api "${BACKEND_URL}/health")"
python3 - <<'PY' "$health_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("real_trading_enabled") is False, p
print("  OK")
PY

echo "Staging live smoke passed."
