#!/usr/bin/env bash
# Slice 46 notifications smoke (paper only, no real Telegram/webhook required).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BASE_URL="${BASE_URL:-http://localhost:8000}"
BASE_URL="${BASE_URL%/}"

EMAIL="${SMOKE_EMAIL:-notifications-smoke-$(date +%s)@example.com}"
PASSWORD="${SMOKE_PASSWORD:?Set SMOKE_PASSWORD (12+ chars; never commit it)}"

auth_header() {
  printf 'Authorization: Bearer %s' "$TOKEN"
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
]
for pattern in forbidden:
    assert re.search(pattern, raw, re.IGNORECASE) is None, f"Secret-like value in response: {pattern}"
print("  OK (no secrets in payload)")
PY
}

echo "Notifications smoke — BASE_URL=${BASE_URL}"

echo "1/10 — health (paper mode, real trading disabled)"
health_json="$(curl -fsS "${BASE_URL}/health")"
python3 - <<'PY' "$health_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("execution_mode") == "paper", p
assert p.get("real_trading_enabled") is False, p
print("  OK")
PY

echo "2/10 — register"
register_json="$(curl -fsS -X POST "${BASE_URL}/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\",\"organization_name\":\"Notifications Smoke\"}")"
TOKEN="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['tokens']['access_token'])" "$register_json")"

echo "3/10 — GET notification preferences"
prefs_json="$(curl -fsS "${BASE_URL}/notifications/preferences" -H "$(auth_header)")"
python3 - <<'PY' "$prefs_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("in_app_enabled") is True, p
assert p.get("webhook_enabled") is False, p
assert p.get("telegram_enabled") is False, p
print("  OK")
PY
assert_no_secrets "$prefs_json"

echo "4/10 — PATCH notification preferences (safe values)"
patch_json="$(curl -fsS -X PATCH "${BASE_URL}/notifications/preferences" \
  -H "$(auth_header)" \
  -H "Content-Type: application/json" \
  -d '{"min_severity":"warning","webhook_enabled":false,"telegram_enabled":false}')"
python3 - <<'PY' "$patch_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("min_severity") == "warning", p
print("  OK")
PY
assert_no_secrets "$patch_json"

echo "5/10 — POST reset notification preferences to defaults"
reset_json="$(curl -fsS -X POST "${BASE_URL}/notifications/preferences/reset-defaults" \
  -H "$(auth_header)")"
python3 - <<'PY' "$reset_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("webhook_enabled") is False, p
assert p.get("telegram_enabled") is False, p
assert p.get("in_app_enabled") is True, p
print("  OK")
PY
assert_no_secrets "$reset_json"

echo "6/10 — alerts delivery status (external disabled without secrets)"
status_json="$(curl -fsS "${BASE_URL}/alerts/delivery-status" -H "$(auth_header)")"
python3 - <<'PY' "$status_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("paper_only") is True, p
assert p.get("effective_external_enabled") is False, p
assert p.get("delivery_enabled") is False, p
assert p.get("webhook_enabled") is False, p
assert p.get("telegram_enabled") is False, p
channels = {s.get("channel"): s for s in p.get("channel_statuses") or []}
for name in ("webhook", "telegram"):
    assert name in channels, p
    assert channels[name].get("available") is False, channels[name]
    assert channels[name].get("status_label") in {"disabled", "user_disabled", "not_configured"}, channels[name]
print(f"  effective_external_enabled={p.get('effective_external_enabled')}")
print("  OK")
PY
assert_no_secrets "$status_json"

echo "7/10 — test notification endpoint (safe, no external secrets required)"
test_json="$(curl -fsS -X POST "${BASE_URL}/notifications/test" -H "$(auth_header)")"
python3 - <<'PY' "$test_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("paper_only") is True, p
assert "TEST" in p.get("test_label", ""), p
assert "external" in p.get("channels_skipped", []) or p.get("success") is False, p
print("  OK")
PY
assert_no_secrets "$test_json"

echo "8/10 — deliver pending (owner only; safe when delivery disabled)"
pending_json="$(curl -fsS -X POST "${BASE_URL}/alerts/deliver-pending" -H "$(auth_header)")"
python3 - <<'PY' "$pending_json"
import json, sys
p = json.loads(sys.argv[1])
assert "processed" in p, p
assert p.get("delivered", 0) == 0, p
print(f"  processed={p.get('processed')} delivered={p.get('delivered')} skipped={p.get('skipped')}")
print("  OK")
PY
assert_no_secrets "$pending_json"

echo "9/10 — delivery summary readable"
summary_json="$(curl -fsS "${BASE_URL}/alerts/delivery-summary" -H "$(auth_header)")"
python3 - <<'PY' "$summary_json"
import json, sys
p = json.loads(sys.argv[1])
for key in ("total", "pending", "delivered", "failed", "disabled", "skipped"):
    assert key in p, p
print("  OK")
PY
assert_no_secrets "$summary_json"

echo "10/10 — real_trading_enabled remains false on health"
health_json="$(curl -fsS "${BASE_URL}/health")"
python3 - <<'PY' "$health_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("real_trading_enabled") is False, p
print("  OK")
PY

echo "Notifications smoke passed."
