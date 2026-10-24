#!/usr/bin/env bash
# Guarded staging validation for Slice 70 manual Telegram alert delivery.
#
# Sends at most ONE Telegram message for ONE explicit ALERT_ID.
# Never loops over alerts. Never retries with a different alert after send.
#
# Usage (preflight — recommended first):
#   DRY_RUN=true ALERT_ID=<uuid> BACKEND_URL=https://alphatrade-api-staging.onrender.com \
#     ./scripts/validate-telegram-delivery-staging.sh
#
# Usage (single send + dedupe on same ALERT_ID):
#   ALERT_ID=<uuid> STAGING_DEMO_PASSWORD='...' \
#     BACKEND_URL=https://alphatrade-api-staging.onrender.com \
#     ./scripts/validate-telegram-delivery-staging.sh
#
# Does not print tokens, chat ids, passwords, or JWTs.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=scripts/smoke-auth-helpers.sh
source "${ROOT_DIR}/scripts/smoke-auth-helpers.sh"

BACKEND_URL="${BACKEND_URL:-https://alphatrade-api-staging.onrender.com}"
BACKEND_URL="${BACKEND_URL%/}"
BASE_URL="${BACKEND_URL}"
export BASE_URL

DRY_RUN="${DRY_RUN:-false}"
ALERT_ID="${ALERT_ID:-}"
DEMO_EMAIL="${STAGING_DEMO_EMAIL:-demo@alphatrade.ai}"
DEMO_PASSWORD="${STAGING_DEMO_PASSWORD:-}"
CONFIRM_PHRASE="DELIVER_TELEGRAM_ALERT"

if [[ "$BACKEND_URL" == *"<"* ]]; then
  echo "Replace BACKEND_URL placeholder." >&2
  exit 1
fi

if [[ -z "$ALERT_ID" ]]; then
  cat >&2 <<'EOF'
FAIL: ALERT_ID is required.

Slice 70 guardrail: set exactly one alert UUID. Never auto-pick or loop over alerts.
Example:
  DRY_RUN=true ALERT_ID=a1000061-0000-4000-8000-000000000061 ./scripts/validate-telegram-delivery-staging.sh
EOF
  exit 1
fi

if [[ ! "$ALERT_ID" =~ ^[0-9a-fA-F-]{36}$ ]]; then
  echo "FAIL: ALERT_ID must be a UUID (got: ${ALERT_ID})." >&2
  exit 1
fi

assert_no_secrets() {
  python3 - <<'PY' "$1"
import json, re, sys
raw = sys.argv[1]
forbidden = [
    r"TELEGRAM_BOT_TOKEN",
    r"TELEGRAM_CHAT_ID",
    r"bot[0-9]{8,}:[A-Za-z0-9_-]{20,}",
    r"postgresql\+psycopg://",
    r"rediss?://",
    r"sk-[a-zA-Z0-9]{20,}",
]
for pattern in forbidden:
    assert re.search(pattern, raw, re.IGNORECASE) is None, f"Secret-like value: {pattern}"
PY
}

auth_header() {
  printf 'Authorization: Bearer %s' "$TOKEN"
}

echo "Telegram delivery staging validation — BACKEND_URL=${BACKEND_URL}"
echo "  ALERT_ID=${ALERT_ID}"
echo "  DRY_RUN=${DRY_RUN}"
echo ""
echo "Guardrails: one alert id only · send at most once · stop after first sent · no alert loops"

echo "1/8 — /health (paper-only)"
health_json="$(curl -fsS "${BACKEND_URL}/health")"
python3 - <<'PY' "$health_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("environment") == "staging", p
assert p.get("execution_mode") == "paper", p
assert p.get("real_trading_enabled") is False, p
print("  OK: staging paper-only")
PY
assert_no_secrets "$health_json"

echo "2/8 — /health/ready"
ready_json="$(curl -fsS "${BACKEND_URL}/health/ready")"
python3 "${ROOT_DIR}/scripts/lib/ready_health.py" "$ready_json"
assert_no_secrets "$ready_json"

echo "3/8 — owner login (demo tenant)"
if [[ -z "$DEMO_PASSWORD" ]]; then
  echo "FAIL: STAGING_DEMO_PASSWORD required for authenticated checks." >&2
  exit 1
fi
login_json="$(curl -fsS -X POST "${BACKEND_URL}/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"${DEMO_EMAIL}\",\"password\":\"${DEMO_PASSWORD}\"}")"
assert_no_secrets "$login_json"
TOKEN="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['tokens']['access_token'])" "$login_json")"

echo "4/8 — GET /alerts/routing/summary"
routing_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/alerts/routing/summary")"
python3 - <<'PY' "$routing_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("paper_only") is True, p
assert p.get("telegram_configured") is True, p
assert p.get("telegram_chat_configured") is True, p
assert p.get("telegram_alert_delivery_available") is True, p
assert p.get("worker_enabled") is False, p
assert p.get("worker_running") is False, p
assert p.get("external_delivery_enabled") is False, p
print("  OK: telegram delivery available, worker/scanner off")
PY
assert_no_secrets "$routing_json"

echo "5/8 — GET /exchange/diagnostics/summary (read-only)"
diag_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/exchange/diagnostics/summary")"
python3 - <<'PY' "$diag_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("real_trading_enabled") is False, p
assert p.get("worker_enabled") is False, p
assert p.get("readiness") == "ready", p
print("  OK: exchange diagnostics ready, no trading")
PY
assert_no_secrets "$diag_json"

echo "6/8 — GET /alerts/${ALERT_ID} (exact alert only — no list scan for send targets)"
alert_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/alerts/${ALERT_ID}")"
python3 - <<'PY' "$alert_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("id"), p
print(f"  OK: alert loaded type={p.get('alert_type')} delivery={p.get('delivery_status')}")
PY
assert_no_secrets "$alert_json"

if [[ "$DRY_RUN" == "true" ]]; then
  echo "7/8 — DRY_RUN: skipping POST /alerts/${ALERT_ID}/deliver-telegram"
  echo "8/8 — DRY_RUN: skipping dedupe check"
  echo ""
  echo "Preflight passed. Re-run with DRY_RUN=false to send exactly one message for this ALERT_ID."
  exit 0
fi

echo ""
echo "WARNING: About to send ONE Telegram message for ALERT_ID=${ALERT_ID}"
echo "         Will stop after first response. Will not try other alerts."
sleep 2

echo "7/8 — POST /alerts/${ALERT_ID}/deliver-telegram (single send)"
deliver_http="$(curl -sS -o /tmp/tg_deliver_1.json -w '%{http_code}' \
  -X POST -H "$(auth_header)" -H 'Content-Type: application/json' \
  "${BACKEND_URL}/alerts/${ALERT_ID}/deliver-telegram" \
  -d "{\"confirm\":\"${CONFIRM_PHRASE}\"}")"
deliver_json="$(cat /tmp/tg_deliver_1.json)"
rm -f /tmp/tg_deliver_1.json
assert_no_secrets "$deliver_json"
python3 - <<'PY' "$deliver_http" "$deliver_json"
import json, sys
http, body = sys.argv[1], json.loads(sys.argv[2])
assert http == "200", (http, body)
status = body.get("status")
assert status in ("sent", "already_delivered"), body
assert body.get("channel") == "telegram", body
if status == "sent":
    assert body.get("sent_at"), body
    print("  OK: status=sent — stopping (no further sends)")
else:
    print("  OK: status=already_delivered — alert was already sent (no new message)")
PY

FIRST_STATUS="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('status'))" "$deliver_json")"
if [[ "$FIRST_STATUS" == "sent" ]]; then
  echo "  Guardrail: first response was sent — will not send to any other alert."
fi

echo "8/8 — dedupe check (same ALERT_ID only)"
dedupe_http="$(curl -sS -o /tmp/tg_deliver_2.json -w '%{http_code}' \
  -X POST -H "$(auth_header)" -H 'Content-Type: application/json' \
  "${BACKEND_URL}/alerts/${ALERT_ID}/deliver-telegram" \
  -d "{\"confirm\":\"${CONFIRM_PHRASE}\"}")"
dedupe_json="$(cat /tmp/tg_deliver_2.json)"
rm -f /tmp/tg_deliver_2.json
assert_no_secrets "$dedupe_json"
python3 - <<'PY' "$dedupe_http" "$dedupe_json" "$deliver_json"
import json, sys
http, body, first = sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3])
assert http == "200", (http, body)
assert body.get("status") == "already_delivered", body
if first.get("status") == "sent":
    assert body.get("delivery_id") == first.get("delivery_id"), (body, first)
    assert body.get("sent_at") == first.get("sent_at"), (body, first)
print("  OK: dedupe already_delivered on same alert")
PY

echo "Telegram delivery staging validation passed."
