#!/usr/bin/env bash
# Strategy quality smoke test — read-only detector performance APIs (Slice 89).
# Safe test data only; paper mode invariants enforced. No orders, proposals,
# approvals, execution, rule changes, detector toggles, or automation.
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
EMAIL="${SMOKE_EMAIL:-strategy-quality-smoke-$(date +%s)@example.com}"
PASSWORD="${SMOKE_PASSWORD:?Set SMOKE_PASSWORD (12+ chars; never commit it)}"
ORG_NAME="${SMOKE_ORG:-Strategy Quality Smoke Org $(date +%s)}"

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

echo "Strategy quality smoke — BASE_URL=${BASE_URL} COOKIE_MODE=${COOKIE_MODE}"

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

echo "1/6 — /health (paper mode)"
health_json="$(curl_api "${BASE_URL}/health")"
python3 - <<'PY' "$health_json"
import json, sys
payload = json.loads(sys.argv[1])
assert payload.get("execution_mode") == "paper", payload
assert payload.get("real_trading_enabled") is False, payload
print("  OK: paper mode, real_trading_enabled=false")
PY

if [[ "$SKIP_REGISTER" == "true" ]]; then
  echo "2/6 — register skipped (SKIP_REGISTER=true)"
  if [[ -z "${SMOKE_ACCESS_TOKEN:-}" ]]; then
    echo "SMOKE_ACCESS_TOKEN required when SKIP_REGISTER=true" >&2
    exit 1
  fi
  login_token="$SMOKE_ACCESS_TOKEN"
else
  echo "2/6 — register + login"
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

echo "3/6 — GET /strategy-quality/summary"
summary_json="$(curl_api_cookie "${auth_header[@]}" "${BASE_URL}/strategy-quality/summary")"
python3 - <<'PY' "$summary_json"
import json, sys
body = json.loads(sys.argv[1])
assert "organization_id" in body, body
assert isinstance(body.get("by_verdict"), list) and body["by_verdict"], body
assert isinstance(body.get("by_trust_tier"), list) and body["by_trust_tier"], body
note = str(body.get("note", "")).lower()
assert "read-only" in note and "do not" in note, body
print("  OK: summary read-only, verdict/trust distributions present")
PY

echo "4/6 — GET /strategy-quality/detectors"
detectors_json="$(curl_api_cookie "${auth_header[@]}" "${BASE_URL}/strategy-quality/detectors")"
python3 - <<'PY' "$detectors_json"
import json, sys
body = json.loads(sys.argv[1])
detectors = body.get("detectors")
assert isinstance(detectors, list) and detectors, body
names = {d["condition"] for d in detectors}
# Known setup detectors always appear, even without validated data yet.
for known in ("liquidity_sweep", "sfp", "trend_pullback", "order_block", "breakout_retest"):
    assert known in names, (known, sorted(names))
print(f"  OK: {len(detectors)} detector report(s)")
PY

echo "5/6 — GET /strategy-quality/detectors/liquidity_sweep/explain"
explain_json="$(curl_api_cookie "${auth_header[@]}" \
  "${BASE_URL}/strategy-quality/detectors/liquidity_sweep/explain")"
python3 - <<'PY' "$explain_json"
import json, sys
body = json.loads(sys.argv[1])
report = body.get("report")
assert report and report.get("condition") == "liquidity_sweep", body
assert "confidence_calibration" in report, body
assert isinstance(body.get("timeframes"), list), body
print("  OK: detector explain returns report + timeframe breakdown")
PY

echo "6/6 — deployment safety invariants"
if [[ "$SKIP_SAFETY" == "true" ]]; then
  echo "  skipped (SKIP_SAFETY=true; caller runs verify-safety.sh)"
else
  BASE_URL="${BASE_URL}" ./scripts/verify-safety.sh
fi

echo "Strategy quality smoke checks passed."
