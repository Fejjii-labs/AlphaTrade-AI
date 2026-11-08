#!/usr/bin/env bash
# Canonical paper synthetic staging smoke.
#
# Always covers (no exchange credentials):
#   authentication, /health paper+watcher+telegram posture, unauthenticated 401,
#   canonical Candidate list, executable-field 422, risk BLOCK, kill-switch GET,
#   cross-tenant isolation of canonical reads.
#
# Happy-path Candidate/eligibility/TradePlan/approval/execution/journal/learning
# requires synthetic IDs (seeded out of band; there is no HTTP mint):
#   CANONICAL_CANDIDATE_ID, CANONICAL_REVISION_ID, CANONICAL_ACCOUNT_ID,
#   CANONICAL_AUTHORIZATION_ID
#
# This script never enables live trading and does not activate the kill switch
# unless CANONICAL_ALLOW_KILL_SWITCH=true (isolated tenants only).
#
# Usage:
#   BASE_URL=https://api.example.com ./scripts/canonical-staging-smoke.sh
#   ./scripts/canonical-staging-smoke.sh --self-check
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=scripts/smoke-auth-helpers.sh
source "${ROOT_DIR}/scripts/smoke-auth-helpers.sh"

SELF_CHECK=false
if [[ "${1:-}" == "--self-check" ]]; then
  SELF_CHECK=true
fi

COOKIE_MODE="${COOKIE_MODE:-false}"
SKIP_REGISTER="${SKIP_REGISTER:-false}"
EMAIL="${SMOKE_EMAIL:-canonical-smoke-$(date +%s)@example.com}"
ORG_NAME="${SMOKE_ORG:-Canonical Smoke Org $(date +%s)}"
EMAIL_B="${SMOKE_EMAIL_B:-canonical-smoke-b-$(date +%s)@example.com}"
ORG_B="${SMOKE_ORG_B:-Canonical Smoke Org B $(date +%s)}"

fail() {
  echo "CANONICAL SMOKE FAILED: $*" >&2
  exit 1
}

if [[ "$SELF_CHECK" == "true" ]]; then
  echo "Canonical staging smoke — self-check (no network)"
  [[ -f "${ROOT_DIR}/scripts/smoke-auth-helpers.sh" ]] || fail "missing smoke-auth-helpers.sh"
  grep -q 'canonical/candidates' "$0" || fail "lost canonical candidate coverage"
  grep -q 'canonical/evidence' "$0" || fail "lost canonical evidence coverage"
  grep -q 'risk/check' "$0" || fail "lost risk BLOCK coverage"
  grep -q 'ENABLE_REAL_TRADING' "$0" || fail "lost real-trading refusal note"
  echo "  OK: script present and wired"
  echo "Canonical staging smoke self-check passed."
  exit 0
fi

# Required for network runs only, so the offline self-check needs no secret.
PASSWORD="${SMOKE_PASSWORD:?Set SMOKE_PASSWORD (12+ chars; never commit it)}"

BASE_URL="${BASE_URL:-http://localhost:8000}"
BASE_URL="${BASE_URL%/}"
if [[ -z "$BASE_URL" || "$BASE_URL" == *"<"* ]]; then
  fail "BASE_URL is required and must not contain placeholders"
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

auth_header() {
  echo "Authorization: Bearer ${1}"
}

echo "Canonical synthetic smoke — BASE_URL=${BASE_URL}"
echo "This script does not enable real trading (ENABLE_REAL_TRADING stays false)."

echo "1/10 — /health paper posture (Watcher disarmed, or armed paper pair only)"
health_json="$(curl_api "${BASE_URL}/health")"
python3 "${ROOT_DIR}/scripts/lib/watcher_paper_health.py" "$health_json"

echo "2/10 — unauthenticated canonical reads are 401"
unauth="$(curl -sS -o /dev/null -w '%{http_code}' "${BASE_URL}/canonical/candidates")"
[[ "$unauth" == "401" ]] || fail "expected 401 for /canonical/candidates, got ${unauth}"
unauth_ev="$(curl -sS -o /dev/null -w '%{http_code}' "${BASE_URL}/canonical/evidence")"
[[ "$unauth_ev" == "401" ]] || fail "expected 401 for /canonical/evidence, got ${unauth_ev}"
echo "  OK"

if [[ "$SKIP_REGISTER" == "true" ]]; then
  [[ -n "${SMOKE_ACCESS_TOKEN:-}" ]] || fail "SMOKE_ACCESS_TOKEN required when SKIP_REGISTER=true"
  token_a="$SMOKE_ACCESS_TOKEN"
  echo "3/10 — register skipped"
else
  echo "3/10 — register tenant A"
  register_json="$(curl_api_cookie -X POST "${BASE_URL}/auth/register" \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\",\"organization_name\":\"${ORG_NAME}\"}")"
  smoke_login_after_register "$register_json"
  token_a="$SMOKE_ACCESS_TOKEN"
fi

echo "4/10 — canonical Candidate list (synthetic; empty is allowed)"
list_json="$(curl_api -H "$(auth_header "$token_a")" "${BASE_URL}/canonical/candidates")"
python3 - <<'PY' "$list_json"
import json, sys
payload = json.loads(sys.argv[1])
assert "items" in payload and "total" in payload, payload
print(f"  OK: total={payload.get('total')}")
PY

echo "4b/10 — canonical evidence is read-only and fail-closed"
ev_json="$(curl_api -H "$(auth_header "$token_a")" "${BASE_URL}/canonical/evidence")"
python3 - <<'PY' "$ev_json"
import json, sys
payload = json.loads(sys.argv[1])
assert payload.get("authority") == "canonical", payload
assert payload.get("live_executable") is False, payload
assert payload.get("watcher_activated") is False, payload
price = payload.get("current_price") or {}
assert price.get("fallback_used") is False, payload
presentation = price.get("presentation")
source = payload.get("source") or {}
assert source.get("fallback_used") is False, payload
if presentation == "live_mark":
    assert price.get("usable_as_current_market_price") is True, payload
    assert price.get("is_live") is True, payload
    assert price.get("is_mock") is False, payload
    assert source.get("source_family") == "binance_usdm_futures_public", payload
elif presentation == "replay_fixture":
    assert price.get("usable_as_current_market_price") is False, payload
    assert price.get("is_live") is False, payload
else:
    assert price.get("usable_as_current_market_price") is False, payload
print("  OK: presentation=%s" % presentation)
PY

echo "5/10 — paper-plan rejects executable fields (422)"
exec_code="$(curl -sS -o /tmp/canonical-smoke-exec.json -w '%{http_code}' \
  -X POST "${BASE_URL}/execution/paper-plan" \
  -H "$(auth_header "$token_a")" -H 'Content-Type: application/json' \
  -d '{"account_id":"00000000-0000-0000-0000-000000000001","authorization_id":"00000000-0000-0000-0000-000000000002","revision_id":"00000000-0000-0000-0000-000000000003","idempotency_key":"canonical-smoke-forbidden","side":"buy"}')"
[[ "$exec_code" == "422" ]] || fail "expected 422 for executable fields, got ${exec_code}"
echo "  OK"

echo "6/10 — risk engine BLOCK (no stop loss, synthetic)"
risk_json="$(curl_api -X POST "${BASE_URL}/risk/check" \
  -H "$(auth_header "$token_a")" -H 'Content-Type: application/json' \
  -d '{"symbol":"BTCUSDT","direction":"long","entry_price":"60000","position_size":"0.005","leverage":"3","account_equity":"10000"}')"
python3 - <<'PY' "$risk_json"
import json, sys
payload = json.loads(sys.argv[1])
assert payload.get("action") == "block", payload
print("  OK: action=block")
PY

echo "7/10 — kill switch GET (no mutation unless explicitly allowed)"
ks_json="$(curl_api -H "$(auth_header "$token_a")" "${BASE_URL}/risk/kill-switch")"
python3 - <<'PY' "$ks_json"
import json, sys
payload = json.loads(sys.argv[1])
assert "execution_blocked" in payload, payload
print(f"  OK: execution_blocked={payload.get('execution_blocked')}")
PY
if [[ "${CANONICAL_ALLOW_KILL_SWITCH:-false}" == "true" ]]; then
  echo "  activating kill switch (CANONICAL_ALLOW_KILL_SWITCH=true)"
  curl_api -X POST "${BASE_URL}/risk/kill-switch/activate" \
    -H "$(auth_header "$token_a")" -H 'Content-Type: application/json' \
    -d '{"confirm":true,"reason":"canonical synthetic smoke"}' >/dev/null
  curl_api -X POST "${BASE_URL}/risk/kill-switch/deactivate" \
    -H "$(auth_header "$token_a")" -H 'Content-Type: application/json' \
    -d '{"confirm":true,"reason":"restore after canonical smoke"}' >/dev/null
  echo "  OK: activate/deactivate round-trip"
fi

echo "8/10 — register tenant B and reject tenant A identifiers"
register_b="$(curl_api -X POST "${BASE_URL}/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"${EMAIL_B}\",\"password\":\"${PASSWORD}\",\"organization_name\":\"${ORG_B}\"}")"
token_b="$(python3 - <<'PY' "$register_b"
import json, sys
print(json.loads(sys.argv[1])["tokens"]["access_token"])
PY
)"
list_b="$(curl_api -H "$(auth_header "$token_b")" "${BASE_URL}/canonical/candidates")"
python3 - <<'PY' "$list_json" "$list_b"
import json, sys
a = json.loads(sys.argv[1])
b = json.loads(sys.argv[2])
ids_a = {item["candidate"]["candidate_id"] for item in a.get("items") or []}
ids_b = {item["candidate"]["candidate_id"] for item in b.get("items") or []}
assert ids_a.isdisjoint(ids_b), (ids_a, ids_b)
print("  OK: tenant lists do not overlap")
PY

if [[ -n "${CANONICAL_CANDIDATE_ID:-}" ]]; then
  echo "9/10 — seeded canonical reads (CANONICAL_CANDIDATE_ID set)"
  cand="$(curl_api -H "$(auth_header "$token_a")" \
    "${BASE_URL}/canonical/candidates/${CANONICAL_CANDIDATE_ID}")"
  python3 - <<'PY' "$cand"
import json, sys
payload = json.loads(sys.argv[1])
assert payload.get("authority") == "canonical" or "candidate" in payload, payload
print("  OK: candidate read")
PY
  curl_api -H "$(auth_header "$token_a")" \
    "${BASE_URL}/canonical/candidates/${CANONICAL_CANDIDATE_ID}/eligibility" >/dev/null
  echo "  OK: eligibility read"
  hidden="$(curl -sS -o /dev/null -w '%{http_code}' \
    -H "$(auth_header "$token_b")" \
    "${BASE_URL}/canonical/candidates/${CANONICAL_CANDIDATE_ID}")"
  [[ "$hidden" == "404" ]] || fail "tenant B should not see tenant A candidate (got ${hidden})"
  echo "  OK: cross-tenant candidate 404"
  if [[ -n "${CANONICAL_AUTHORIZATION_ID:-}" && -n "${CANONICAL_REVISION_ID:-}" && -n "${CANONICAL_ACCOUNT_ID:-}" ]]; then
    exec_json="$(curl_api -X POST "${BASE_URL}/execution/paper-plan" \
      -H "$(auth_header "$token_a")" -H 'Content-Type: application/json' \
      -d "{\"account_id\":\"${CANONICAL_ACCOUNT_ID}\",\"authorization_id\":\"${CANONICAL_AUTHORIZATION_ID}\",\"revision_id\":\"${CANONICAL_REVISION_ID}\",\"idempotency_key\":\"canonical-smoke-$(date +%s)\"}")"
    python3 - <<'PY' "$exec_json"
import json, sys
payload = json.loads(sys.argv[1])
assert payload.get("outcome") in {"ALLOW", "BLOCKED"}, payload
print(f"  OK: paper-plan outcome={payload.get('outcome')} replayed={payload.get('replayed')}")
PY
  else
    echo "  paper-plan skipped (set CANONICAL_ACCOUNT_ID, CANONICAL_AUTHORIZATION_ID, CANONICAL_REVISION_ID)"
  fi
  curl_api -H "$(auth_header "$token_a")" \
    "${BASE_URL}/canonical/learning/strategy-stats" >/dev/null
  echo "  OK: strategy stats"
  eval_json="$(curl_api -H "$(auth_header "$token_a")" \
    "${BASE_URL}/canonical/paper-evaluation/summary")"
  python3 - <<'PY' "$eval_json"
import json, sys
payload = json.loads(sys.argv[1])
assert payload.get("watcher_activated") is False, payload
assert payload.get("live_executable") is False, payload
summary = payload.get("summary") or {}
assert summary.get("authority") == "paper_evaluation_measurement", payload
assert summary.get("live_executable") is False, payload
facts = summary.get("facts") or {}
assert facts.get("watcher_orchestration_enabled") is False, payload
assert facts.get("telegram_interaction_enabled") is False, payload
assert (facts.get("missed_opportunities") or {}).get("counterfactual_pnl") is None, payload
for item in summary.get("refinements") or []:
    assert item.get("activate") is False, item
    assert item.get("auto_activate") is False, item
print("  OK: paper evaluation summary (measurement only)")
PY
else
  echo "9/10 — seeded canonical happy path skipped (set CANONICAL_CANDIDATE_ID for reads)"
fi

echo "10/10 — strategy statistics + paper evaluation + journal list"
curl_api -H "$(auth_header "$token_a")" "${BASE_URL}/canonical/learning/strategy-stats" >/dev/null
eval_json="$(curl_api -H "$(auth_header "$token_a")" "${BASE_URL}/canonical/paper-evaluation/summary")"
python3 - <<'PY' "$eval_json"
import json, sys
payload = json.loads(sys.argv[1])
assert payload.get("watcher_activated") is False
assert payload.get("live_executable") is False
assert (payload.get("summary") or {}).get("authority") == "paper_evaluation_measurement"
print("  OK: paper evaluation empty-tenant summary")
PY
curl_api -H "$(auth_header "$token_a")" "${BASE_URL}/journal/trades" >/dev/null
echo "  OK"

echo "Canonical synthetic smoke passed (paper-only, no exchange credentials)."
exit 0
