#!/usr/bin/env bash
# Lessons read-only smoke (Slice 90B) — list pending/accepted; no writes.
# No orders, proposals, approvals, execution, exchange, Telegram, or automation.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=scripts/smoke-auth-helpers.sh
source "${ROOT_DIR}/scripts/smoke-auth-helpers.sh"

BASE_URL="${BASE_URL:-http://localhost:8000}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-60}"
COOKIE_MODE="${COOKIE_MODE:-false}"
SKIP_REGISTER="${SKIP_REGISTER:-false}"
SKIP_SAFETY="${SKIP_SAFETY:-false}"
EMAIL="${SMOKE_EMAIL:-lessons-smoke-$(date +%s)@example.com}"
PASSWORD="${SMOKE_PASSWORD:?Set SMOKE_PASSWORD (12+ chars; never commit it)}"
ORG_NAME="${SMOKE_ORG:-Lessons Smoke Org $(date +%s)}"

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

echo "Lessons smoke — BASE_URL=${BASE_URL} COOKIE_MODE=${COOKIE_MODE}"

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

echo "1/5 — /health (paper mode)"
health_json="$(curl_api "${BASE_URL}/health")"
python3 - <<'PY' "$health_json"
import json, sys
payload = json.loads(sys.argv[1])
assert payload.get("execution_mode") == "paper", payload
assert payload.get("real_trading_enabled") is False, payload
print("  OK: paper mode, real_trading_enabled=false")
PY

if [[ "$SKIP_REGISTER" == "true" ]]; then
  echo "2/5 — register skipped (SKIP_REGISTER=true)"
  if [[ -z "${SMOKE_ACCESS_TOKEN:-}" ]]; then
    echo "SMOKE_ACCESS_TOKEN required when SKIP_REGISTER=true" >&2
    exit 1
  fi
  login_token="$SMOKE_ACCESS_TOKEN"
else
  echo "2/5 — register + login"
  register_json="$(curl_api_cookie -X POST "${BASE_URL}/auth/register" \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\",\"organization_name\":\"${ORG_NAME}\"}")"
  if ! smoke_login_after_register "$register_json"; then
    echo "FAIL: register/login step failed." >&2
    exit 1
  fi
  login_token="$SMOKE_ACCESS_TOKEN"
fi

auth_header=(-H "Authorization: Bearer ${login_token}")

echo "3/5 — GET /lessons/candidates?status=pending_review"
pending_json="$(curl_api_cookie "${auth_header[@]}" \
  "${BASE_URL}/lessons/candidates?status=pending_review")"
python3 - <<'PY' "$pending_json"
import json, sys
body = json.loads(sys.argv[1])
assert "items" in body and isinstance(body["items"], list), body
assert "total" in body, body
print(f"  OK: pending candidates={body['total']}")
PY

echo "4/5 — GET /lessons/accepted"
accepted_json="$(curl_api_cookie "${auth_header[@]}" "${BASE_URL}/lessons/accepted")"
python3 - <<'PY' "$pending_json" "$accepted_json"
import json, sys
forbidden = {"order", "execution", "proposal", "approval", "exchange", "telegram", "secret", "token"}

def scan(obj, path="$"):
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            for f in forbidden:
                if f in kl:
                    raise AssertionError(f"forbidden key {path}.{k}")
            scan(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            scan(v, f"{path}[{i}]")

for label, raw in [("pending", sys.argv[1]), ("accepted", sys.argv[2])]:
    scan(json.loads(raw), label)
print("  OK: no forbidden keys in lesson list payloads")
PY

echo "5/5 — deployment safety invariants"
if [[ "$SKIP_SAFETY" == "true" ]]; then
  echo "  skipped (SKIP_SAFETY=true; caller runs verify-safety.sh)"
else
  BASE_URL="${BASE_URL}" ./scripts/verify-safety.sh
fi

echo "Lessons smoke checks passed."
