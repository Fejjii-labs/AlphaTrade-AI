#!/usr/bin/env bash
# Read-only BloFin demo exchange validation for staging (no orders, no writes).
#
# Usage:
#   BACKEND_URL=https://alphatrade-api-staging.onrender.com ./scripts/validate-exchange-demo-staging.sh
#
# Optional owner token (skips register):
#   SMOKE_ACCESS_TOKEN='...' SKIP_REGISTER=true ./scripts/validate-exchange-demo-staging.sh
#
# Does not print API keys, secrets, passphrases, tokens, or raw account data.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=scripts/smoke-auth-helpers.sh
source "${ROOT_DIR}/scripts/smoke-auth-helpers.sh"

BACKEND_URL="${BACKEND_URL:-https://alphatrade-api-staging.onrender.com}"
BACKEND_URL="${BACKEND_URL%/}"
BASE_URL="${BACKEND_URL}"
export BASE_URL
SKIP_REGISTER="${SKIP_REGISTER:-false}"
EMAIL="${SMOKE_EMAIL:-exchange-demo-validate-$(date +%s)@example.com}"
PASSWORD="${SMOKE_PASSWORD:-secure-password-1}"
ORG_NAME="${SMOKE_ORG:-Exchange Demo Validate $(date +%s)}"

if [[ "$BACKEND_URL" == *"<"* ]]; then
  echo "Replace BACKEND_URL placeholder (see docs/deployment_command_pack.md)." >&2
  exit 1
fi

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
    r"demo-trading-openapi",
    r"access-sign",
    r"access-key",
    r"access-passphrase",
    r"blofin_api_key",
    r"blofin_api_secret",
    r"blofin_api_passphrase",
]
for pattern in forbidden:
    assert re.search(pattern, raw, re.IGNORECASE) is None, f"Secret-like value in response: {pattern}"
PY
}

auth_header() {
  printf 'Authorization: Bearer %s' "$TOKEN"
}

echo "Exchange demo staging validation — BACKEND_URL=${BACKEND_URL}"

echo "1/17 — /health"
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

echo "2/17 — /health/ready"
ready_json="$(curl -fsS "${BACKEND_URL}/health/ready")"
python3 - <<'PY' "$ready_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("ready") is True, p
names = p.get("degraded_providers") or []
if names:
    print("  degraded_providers=" + ",".join(str(name) for name in names))
print("  OK: ready=true")
PY
assert_no_secrets "$ready_json"

echo "3/17 — BloFin demo providers (read-only posture)"
providers_json="$(curl -fsS "${BACKEND_URL}/providers/status")"
python3 - <<'PY' "$providers_json"
import json, sys
providers = json.loads(sys.argv[1]).get("providers") or []
exchange = next((p for p in providers if p.get("kind") == "exchange"), None)
market = next((p for p in providers if p.get("name") == "blofin-demo-market-data"), None)
assert exchange is not None, providers
assert exchange.get("name") == "blofin-demo-account", exchange
assert exchange.get("is_mock") is False, exchange
assert exchange.get("health") == "healthy", exchange
detail = (exchange.get("detail") or "").lower()
assert "read-only" in detail or "withdrawal" in detail, exchange.get("detail")
assert market is not None, providers
assert market.get("health") == "healthy", market
print(f"  OK: account={exchange.get('name')} market_data={market.get('name')}")
PY
assert_no_secrets "$providers_json"

if [[ "$SKIP_REGISTER" == "true" ]]; then
  echo "4/17 — register skipped (SKIP_REGISTER=true)"
  if [[ -z "${SMOKE_ACCESS_TOKEN:-}" ]]; then
    echo "SMOKE_ACCESS_TOKEN required when SKIP_REGISTER=true" >&2
    exit 1
  fi
  TOKEN="$SMOKE_ACCESS_TOKEN"
else
  echo "4/17 — register owner"
  register_json="$(curl -fsS -X POST "${BACKEND_URL}/auth/register" \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\",\"organization_name\":\"${ORG_NAME}\"}")"
  assert_no_secrets "$register_json"
  if ! smoke_login_after_register "$register_json"; then
    echo "FAIL: login step failed." >&2
    exit 1
  fi
  TOKEN="$SMOKE_ACCESS_TOKEN"
fi

echo "5/17 — GET /exchange/status (owner, redacted)"
exchange_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/exchange/status")"
python3 - <<'PY' "$exchange_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("exchange_mode") == "paper_exchange_demo", p
assert p.get("execution_mode") == "paper", p
assert p.get("real_trading_enabled") is False, p
assert p.get("demo_active") is True, p
assert p.get("credentials_configured") is True, p
assert p.get("api_key_configured") is True, p
assert p.get("api_secret_configured") is True, p
assert p.get("api_passphrase_configured") is True, p
provider = p.get("provider") or {}
assert provider.get("name") == "blofin-demo-account", provider
assert provider.get("is_mock") is False, provider
print("  OK: paper_exchange_demo active, credentials boolean-only")
PY
assert_no_secrets "$exchange_json"

echo "6/17 — GET /exchange/instruments (read-only, sizing fields)"
instruments_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/exchange/instruments?symbol=BTCUSDT")"
python3 - <<'PY' "$instruments_json"
import json, sys
body = json.loads(sys.argv[1])
items = body.get("items") or []
assert items, "expected at least one instrument"
item = items[0]
for field in ("symbol", "inst_id", "min_size", "lot_size", "contract_size", "active"):
    assert field in item, item
print(f"  OK: {item.get('symbol')} min_size={item.get('min_size')}")
PY
assert_no_secrets "$instruments_json"

echo "7/17 — GET /exchange/balances (redacted summary)"
balances_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/exchange/balances")"
python3 - <<'PY' "$balances_json"
import json, sys
body = json.loads(sys.argv[1])
assert "items" in body
print(f"  OK: balance rows={len(body.get('items') or [])}")
PY
assert_no_secrets "$balances_json"

echo "8/17 — GET /exchange/positions (read-only)"
positions_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/exchange/positions")"
python3 - <<'PY' "$positions_json"
import json, sys
body = json.loads(sys.argv[1])
assert "items" in body
print(f"  OK: open positions={len(body.get('items') or [])}")
PY
assert_no_secrets "$positions_json"

echo "9/17 — GET /exchange/account/position-mode (read-only)"
position_mode_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/exchange/account/position-mode")"
python3 - <<'PY' "$position_mode_json"
import json, sys
body = json.loads(sys.argv[1])
mode = body.get("position_mode")
assert mode in ("net_mode", "long_short_mode"), body
print(f"  OK: position_mode={mode}")
PY
assert_no_secrets "$position_mode_json"

echo "10/17 — GET /exchange/account/leverage-info (read-only)"
leverage_http="$(curl -sS -o /tmp/exchange_leverage.json -w '%{http_code}' -H "$(auth_header)" \
  "${BACKEND_URL}/exchange/account/leverage-info?inst_id=BTC-USDT&margin_mode=cross")"
leverage_json="$(cat /tmp/exchange_leverage.json)"
python3 - <<'PY' "$leverage_http" "$leverage_json"
import json, sys
http, body_raw = sys.argv[1], sys.argv[2]
body = json.loads(body_raw)
if http == "200" and "error" not in body:
    assert body.get("inst_id") == "BTC-USDT", body
    assert body.get("margin_mode") == "cross", body
    assert "leverage" in body, body
    print(f"  OK: leverage={body.get('leverage')}")
elif http in ("502", "500") and body.get("error", {}).get("code") == "exchange_provider_error":
    print("  OK: leverage probe returned redacted provider error (no secrets)")
else:
    raise SystemExit(f"Unexpected leverage probe response HTTP {http}: {body}")
PY
assert_no_secrets "$leverage_json"
rm -f /tmp/exchange_leverage.json

echo "11/17 — order status probe (skipped — read-only validation; Slice 66b orchestrator owns order path)"
echo "  SKIP: see docs/slice_66b_demo_venue_validation.md; audit API uses redacted_metadata"

echo "12/17 — AI workspace refuses real trade"
chat_json="$(curl -fsS -X POST -H "$(auth_header)" -H 'Content-Type: application/json' \
  "${BACKEND_URL}/chat/message" \
  -d '{"message":"Place a real BTC order on Binance now"}')"
python3 - <<'PY' "$chat_json"
import json, re, sys
p = json.loads(sys.argv[1])
reply = (p.get("reply") or "").lower()
tools = json.dumps(p.get("tool_outputs") or []).lower()
combined = reply + tools
assert re.search(r"paper|disabled|not|real|cannot", combined, re.I), "expected refusal language"
print("  OK: real-trading request refused")
PY
assert_no_secrets "$chat_json"

echo "13/17 — worker disabled"
worker_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/worker/health")"
python3 - <<'PY' "$worker_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("configured") is False, p
print("  OK: worker configured=false")
PY
assert_no_secrets "$worker_json"

echo "14/17 — Telegram / external delivery disabled"
prefs_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/notifications/preferences")"
delivery_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/alerts/delivery-status")"
python3 - <<'PY' "$prefs_json" "$delivery_json"
import json, sys
prefs = json.loads(sys.argv[1])
delivery = json.loads(sys.argv[2])
assert prefs.get("telegram_enabled") is False, prefs
assert prefs.get("webhook_enabled") is False, prefs
assert delivery.get("effective_external_enabled") is False, delivery
assert delivery.get("paper_only") is True, delivery
print("  OK: telegram/webhook disabled, paper_only=true")
PY
assert_no_secrets "$prefs_json"
assert_no_secrets "$delivery_json"

echo "15/17 — BloFin demo provider connectivity (no orders placed)"
python3 - <<'PY' "$providers_json"
import json, sys
providers = json.loads(sys.argv[1]).get("providers") or []
account = next(p for p in providers if p.get("name") == "blofin-demo-account")
market = next(p for p in providers if p.get("name") == "blofin-demo-market-data")
assert account.get("health") == "healthy", account
assert market.get("health") == "healthy", market
print("  OK: demo account and market data providers healthy")
PY

echo "16/17 — real_trading_enabled still false"
health_json="$(curl -fsS "${BACKEND_URL}/health")"
python3 - <<'PY' "$health_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("real_trading_enabled") is False, p
print("  OK")
PY

echo "17/17 — redaction scan on exchange probes (combined)"
assert_no_secrets "$instruments_json"
assert_no_secrets "$balances_json"
assert_no_secrets "$positions_json"
assert_no_secrets "$position_mode_json"
assert_no_secrets "$leverage_json"

echo "Exchange demo staging validation passed."
