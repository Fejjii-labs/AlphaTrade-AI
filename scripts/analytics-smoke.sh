#!/usr/bin/env bash
# Analytics smoke test — journal + setup tracking + analytics APIs (Slice 31/32).
# Safe test data only; paper mode invariants enforced.
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
EMAIL="${SMOKE_EMAIL:-analytics-smoke-$(date +%s)@example.com}"
PASSWORD="${SMOKE_PASSWORD:?Set SMOKE_PASSWORD (12+ chars; never commit it)}"
ORG_NAME="${SMOKE_ORG:-Analytics Smoke Org $(date +%s)}"
UNIQUE_LESSON="Analytics smoke lesson $(date +%s)"

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

echo "Analytics smoke — BASE_URL=${BASE_URL} COOKIE_MODE=${COOKIE_MODE}"

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

echo "1/9 — /health (paper mode)"
health_json="$(curl_api "${BASE_URL}/health")"
python3 - <<'PY' "$health_json"
import json, sys
payload = json.loads(sys.argv[1])
assert payload.get("execution_mode") == "paper", payload
assert payload.get("real_trading_enabled") is False, payload
print("  OK: paper mode, real_trading_enabled=false")
PY

if [[ "$SKIP_REGISTER" == "true" ]]; then
  echo "2/9 — register skipped (SKIP_REGISTER=true)"
  if [[ -z "${SMOKE_ACCESS_TOKEN:-}" ]]; then
    echo "SMOKE_ACCESS_TOKEN required when SKIP_REGISTER=true" >&2
    exit 1
  fi
  login_token="$SMOKE_ACCESS_TOKEN"
else
  echo "2/9 — register + login"
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

echo "3/9 — /auth/me + watchlist"
me_json="$(curl_api_cookie "${auth_header[@]}" "${BASE_URL}/auth/me")"
org_id="$(python3 - <<'PY' "$me_json"
import json, sys
body = json.loads(sys.argv[1])
print(body["organization"]["id"])
PY
)"
user_id="$(python3 - <<'PY' "$me_json"
import json, sys
body = json.loads(sys.argv[1])
print(body["user"]["id"])
PY
)"

curl_api_cookie -X POST "${auth_header[@]}" -H 'Content-Type: application/json' \
  "${BASE_URL}/market/watchlist" \
  -d "{\"organization_id\":\"${org_id}\",\"user_id\":\"${user_id}\",\"symbol\":\"BTCUSDT\",\"exchange\":\"mock\",\"timeframes\":[\"1h\"],\"strategy_ids\":[\"htf_trend_pullback\"],\"enabled\":true}" \
  >/dev/null
echo "  OK: watchlist item created"

echo "4/9 — workspace chat (proposal path)"
chat_json="$(curl_api_cookie -X POST "${auth_header[@]}" -H 'Content-Type: application/json' \
  "${BASE_URL}/chat/message" \
  -d '{"message":"Analyze BTC pullback setup for analytics smoke","symbol":"BTCUSDT","timeframe":"1h"}')"
python3 - <<'PY' "$chat_json"
import json, sys
body = json.loads(sys.argv[1])
assert any(k in body for k in ("reply", "response", "message", "content")), body
print("  OK: chat response received")
PY

echo "5/9 — journal entry (setup, tags, lesson)"
curl_api_cookie -X POST "${auth_header[@]}" -H 'Content-Type: application/json' \
  "${BASE_URL}/journal/entries" \
  -d "{\"symbol\":\"BTCUSDT\",\"timeframe\":\"1h\",\"direction\":\"long\",\"strategy_id\":\"htf_trend_pullback\",\"entry_rationale\":\"Analytics smoke pullback test\",\"lessons\":\"${UNIQUE_LESSON}\",\"mistakes\":[\"fomo\",\"early_entry\"],\"emotions\":[\"anxious\",\"confident\"],\"tags\":[\"analytics-smoke\"],\"result\":\"win\",\"pnl\":\"12.50\"}" \
  >/dev/null
echo "  OK: journal entry created"

echo "6/9 — GET /analytics/setups"
setups_json="$(curl_api_cookie "${auth_header[@]}" "${BASE_URL}/analytics/setups")"
python3 - <<'PY' "$setups_json" "$org_id" "$user_id"
import json, sys
body = json.loads(sys.argv[1])
assert body.get("organization_id") == sys.argv[2], body
assert body.get("user_id") == sys.argv[3], body
assert "setups" in body and isinstance(body["setups"], list), body
print(f"  OK: {len(body['setups'])} setup stat(s)")
PY

echo "7/9 — GET /analytics/trade-review"
review_json="$(curl_api_cookie "${auth_header[@]}" "${BASE_URL}/analytics/trade-review")"
python3 - <<'PY' "$review_json"
import json, sys
body = json.loads(sys.argv[1])
assert "total_journaled_trades" in body, body
assert body["total_journaled_trades"] >= 1, body
print(f"  OK: total_journaled_trades={body['total_journaled_trades']}")
PY

echo "8/9 — GET /analytics/discipline + /analytics/risk-behavior"
discipline_json="$(curl_api_cookie "${auth_header[@]}" "${BASE_URL}/analytics/discipline")"
python3 - <<'PY' "$discipline_json"
import json, sys
body = json.loads(sys.argv[1])
score = body.get("score")
grade = body.get("grade")
assert isinstance(score, int) and 0 <= score <= 100, body
assert grade in {"A", "B", "C", "D", "F"}, body
print(f"  OK: discipline score={score} grade={grade}")
PY

risk_json="$(curl_api_cookie "${auth_header[@]}" "${BASE_URL}/analytics/risk-behavior")"
python3 - <<'PY' "$risk_json"
import json, sys
body = json.loads(sys.argv[1])
assert "journal_completion_rate" in body, body
rate = body["journal_completion_rate"]
assert isinstance(rate, (int, float)) and 0.0 <= float(rate) <= 1.0, body
print(f"  OK: journal_completion_rate={rate}")
PY

echo "9/9 — deployment safety invariants"
if [[ "$SKIP_SAFETY" == "true" ]]; then
  echo "  skipped (SKIP_SAFETY=true; caller runs verify-safety.sh)"
else
  BASE_URL="${BASE_URL}" ./scripts/verify-safety.sh
fi

echo "Analytics smoke checks passed."
