#!/usr/bin/env bash
# Slice 66b — place exactly ONE far-from-market BloFin demo LIMIT order via paper mirroring,
# verify idempotency, cancel venue order, close internal paper position.
#
# COMPLETE: slice66b-demo-limit-003 consumed 2026-06-28 — do not reuse. See
# docs/slice_66b_demo_venue_validation.md before any future run.
#
# Prerequisites:
#   - Render CLI authenticated (render login OR RENDER_API_KEY), OR DATABASE_URL for staging DB
#   - Staging at paper_exchange_demo with demo probes healthy
#
# Usage:
#   BACKEND_URL=https://alphatrade-api-staging.onrender.com ./scripts/run-slice66b-controlled-demo-order.sh
#
# Optional:
#   RENDER_SERVICE_NAME=alphatrade-api-staging
#   IDEMPOTENCY_KEY=slice66b-demo-limit-001
#   PRICE_FACTOR=0.95
#   SKIP_REGISTER=true SMOKE_ACCESS_TOKEN='...'
#
# Does not print secrets. Aborts on any safety failure.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=scripts/smoke-auth-helpers.sh
source "${ROOT_DIR}/scripts/smoke-auth-helpers.sh"

BACKEND_URL="${BACKEND_URL:-https://alphatrade-api-staging.onrender.com}"
BACKEND_URL="${BACKEND_URL%/}"
BASE_URL="${BACKEND_URL}"
RENDER_SERVICE_NAME="${RENDER_SERVICE_NAME:-alphatrade-api-staging}"
IDEMPOTENCY_KEY="${IDEMPOTENCY_KEY:-slice66b-demo-limit-001}"
PRICE_FACTOR="${PRICE_FACTOR:-0.95}"
TICK_SIZE="0.1"
MIN_SIZE="0.1"
SYMBOL="BTCUSDT"
INST_ID="BTC-USDT"
SEED_SCRIPT="${ROOT_DIR}/scripts/seed-approved-demo-proposal.py"

auth_header() {
  printf 'Authorization: Bearer %s' "$TOKEN"
}

abort() {
  echo "ABORT: $*" >&2
  exit 1
}

assert_no_secrets() {
  python3 - <<'PY' "$1"
import json, re, sys
raw = sys.argv[1]
forbidden = [
    r"TELEGRAM_BOT_TOKEN",
    r"ALERT_WEBHOOK_SECRET",
    r"sk-[a-zA-Z0-9]{20,}",
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
    assert re.search(pattern, raw, re.IGNORECASE) is None, f"Secret-like value: {pattern}"
PY
}

floor_to_tick() {
  python3 - <<'PY' "$1" "$2"
from decimal import Decimal, ROUND_DOWN
import sys
value = Decimal(sys.argv[1])
tick = Decimal(sys.argv[2])
print((value / tick).to_integral_value(rounding=ROUND_DOWN) * tick)
PY
}

verify_seed_runtime() {
  echo "=== Seed runtime check ==="
  if [[ -n "${DATABASE_URL:-}" ]]; then
    echo "  OK: DATABASE_URL is set (staging DB — local shell or Render env)"
    SEED_MODE="local_db"
    return 0
  fi
  if [[ -n "${RENDER_API_KEY:-}" ]]; then
    export RENDER_API_KEY
  fi
  if render whoami -o text --confirm &>/dev/null; then
    echo "  OK: Render CLI authenticated (SSH / one-off jobs available)"
    SEED_MODE="render_ssh"
    return 0
  fi
  abort "Seed runtime unavailable. Options: (1) render login or RENDER_API_KEY for Render Shell; (2) export staging DATABASE_URL from .env.staging."
}

run_seed_script() {
  local org_id="$1" user_id="$2" price="$3"
  local seed_args=(
    --organization-id "$org_id"
    --user-id "$user_id"
    --price "$price"
    --size "$MIN_SIZE"
    --symbol "$SYMBOL"
  )

  if [[ "$SEED_MODE" == "local_db" ]]; then
    echo "  Seeding via local DATABASE_URL..."
    (cd "${ROOT_DIR}/backend" && uv run python "${SEED_SCRIPT}" "${seed_args[@]}")
    return
  fi

  echo "  Seeding via Render SSH (${RENDER_SERVICE_NAME})..."
  # Pipe the script to remote python3 stdin; container has /app/src on PYTHONPATH via uvicorn.
  cat "${SEED_SCRIPT}" | render ssh "$RENDER_SERVICE_NAME" --confirm -o text -- \
    python3 - "${seed_args[@]}"
}

load_staging_env() {
  if [[ -f "${ROOT_DIR}/.env.staging" ]]; then
    eval "$(python3 - <<'PY'
from pathlib import Path
for line in Path(".env.staging").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    v = v.strip().strip('"').strip("'")
    print(f"export {k}={v!r}")
PY
)"
    export ENVIRONMENT="${ENVIRONMENT:-staging}"
    export EXECUTION_MODE="${EXECUTION_MODE:-paper}"
    export ENABLE_REAL_TRADING="${ENABLE_REAL_TRADING:-false}"
  fi
}

load_staging_env

echo "Slice 66b controlled demo order — BACKEND_URL=${BACKEND_URL}"

verify_seed_runtime

echo "=== 1/13 Preflight health ==="
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

ready_json="$(curl -fsS "${BACKEND_URL}/health/ready")"
python3 "${ROOT_DIR}/scripts/lib/ready_health.py" "$ready_json"
assert_no_secrets "$ready_json"

echo "=== 2/13 Owner auth ==="
if [[ "${SKIP_REGISTER:-false}" == "true" ]]; then
  [[ -n "${SMOKE_ACCESS_TOKEN:-}" ]] || abort "SMOKE_ACCESS_TOKEN required when SKIP_REGISTER=true"
  TOKEN="$SMOKE_ACCESS_TOKEN"
else
  EMAIL="${SMOKE_EMAIL:-slice66b-$(date +%s)@example.com}"
  PASSWORD="${SMOKE_PASSWORD:-secure-password-1}"
  ORG_NAME="${SMOKE_ORG:-Slice 66b Validate $(date +%s)}"
  register_json="$(curl -fsS -X POST "${BACKEND_URL}/auth/register" \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\",\"organization_name\":\"${ORG_NAME}\"}")"
  assert_no_secrets "$register_json"
  smoke_login_after_register "$register_json" || abort "login failed after register"
  TOKEN="$SMOKE_ACCESS_TOKEN"
fi

me_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/auth/me")"
ORG_ID="$(python3 - <<'PY' "$me_json"
import json, sys
print(json.loads(sys.argv[1])["organization"]["id"])
PY
)"
USER_ID="$(python3 - <<'PY' "$me_json"
import json, sys
print(json.loads(sys.argv[1])["user"]["id"])
PY
)"
echo "  OK: org=${ORG_ID} user=${USER_ID}"
assert_no_secrets "$me_json"

echo "=== 3/13 Exchange status ==="
exchange_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/exchange/status")"
python3 - <<'PY' "$exchange_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("exchange_mode") == "paper_exchange_demo", p
assert p.get("demo_active") is True, p
assert p.get("real_trading_enabled") is False, p
provider = p.get("provider") or {}
assert provider.get("name") == "blofin-demo-account", provider
assert provider.get("health") == "healthy", provider
print("  OK: paper_exchange_demo active")
PY
assert_no_secrets "$exchange_json"

echo "=== 4/13 Instruments ==="
instruments_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/exchange/instruments?symbol=${SYMBOL}")"
python3 - <<'PY' "$instruments_json" "$MIN_SIZE" "$TICK_SIZE"
import json, sys
body = json.loads(sys.argv[1])
min_size, tick = sys.argv[2], sys.argv[3]
items = body.get("items") or []
assert items, "no instruments"
item = items[0]
assert item.get("active") is True, item
assert str(item.get("min_size")) == min_size, item
assert str(item.get("tick_size")) == tick, item
assert str(item.get("contract_size")) == "0.001", item
print(f"  OK: {item.get('symbol')} min_size={item.get('min_size')} tick={item.get('tick_size')}")
PY
assert_no_secrets "$instruments_json"

echo "=== 5/13 Balances ==="
balances_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/exchange/balances")"
python3 - <<'PY' "$balances_json"
import json, sys
body = json.loads(sys.argv[1])
items = body.get("items") or []
assert items, "no balances"
avail = float(items[0].get("available", 0))
assert avail > 0, items
print(f"  OK: available={items[0].get('available')} {items[0].get('asset')}")
PY
assert_no_secrets "$balances_json"

echo "=== 6/13 Venue positions (must be zero) ==="
positions_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/exchange/positions")"
python3 - <<'PY' "$positions_json"
import json, sys
body = json.loads(sys.argv[1])
count = len(body.get("items") or [])
assert count == 0, f"expected 0 venue positions, got {count}"
print("  OK: venue positions=0")
PY
assert_no_secrets "$positions_json"

echo "=== 7/13 Worker + Telegram disabled ==="
worker_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/worker/health")"
python3 - <<'PY' "$worker_json"
import json, sys
assert json.loads(sys.argv[1]).get("configured") is False
print("  OK: worker disabled")
PY
prefs_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/notifications/preferences")"
delivery_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/alerts/delivery-status")"
python3 - <<'PY' "$prefs_json" "$delivery_json"
import json, sys
prefs, delivery = json.loads(sys.argv[1]), json.loads(sys.argv[2])
assert prefs.get("telegram_enabled") is False, prefs
assert delivery.get("effective_external_enabled") is False, delivery
print("  OK: Telegram/external disabled")
PY

echo "=== 8/13 Reference price + limit price ==="
ref_json="$(curl -fsS "https://api.binance.com/api/v3/ticker/price?symbol=${SYMBOL}")"
REFERENCE_PRICE="$(python3 - <<'PY' "$ref_json"
import json, sys
print(json.loads(sys.argv[1])["price"])
PY
)"
LIMIT_PRICE="$(floor_to_tick "$(python3 - <<PY
from decimal import Decimal
print(Decimal("$REFERENCE_PRICE") * Decimal("$PRICE_FACTOR"))
PY
)" "$TICK_SIZE")"
python3 - <<'PY' "$REFERENCE_PRICE" "$LIMIT_PRICE" "$TICK_SIZE" "$PRICE_FACTOR"
from decimal import Decimal
import sys
ref, limit, tick, factor = map(Decimal, sys.argv[1:])
pct = (Decimal("1") - factor) * Decimal("100")
assert limit < ref, (limit, ref)
assert limit % tick == 0, limit
print(f"  OK: reference={ref} limit={limit} (buy, ~{pct:.0f}% below, factor={factor})")
PY

echo "=== 9/13 Seed approved proposal (service layer) ==="
seed_out="$(run_seed_script "$ORG_ID" "$USER_ID" "$LIMIT_PRICE")"
PROPOSAL_ID="$(python3 - <<'PY' "$seed_out"
import json, sys
# take last JSON line
lines = [ln for ln in sys.argv[1].strip().splitlines() if ln.strip()]
print(json.loads(lines[-1])["proposal_id"])
PY
)"
APPROVAL_ID="$(python3 - <<'PY' "$seed_out"
import json, sys
lines = [ln for ln in sys.argv[1].strip().splitlines() if ln.strip()]
print(json.loads(lines[-1])["approval_id"])
PY
)"
echo "  OK: proposal_id=${PROPOSAL_ID} approval_id=${APPROVAL_ID}"

proposal_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/proposals/${PROPOSAL_ID}")"
python3 - <<'PY' "$proposal_json"
import json, sys
p = json.loads(sys.argv[1])
risk = p.get("risk_result") or {}
assert risk, "risk_result missing"
assert risk.get("action") != "block", risk
print(f"  OK: risk_result action={risk.get('action')}")
PY
assert_no_secrets "$proposal_json"

approval_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/approvals/${APPROVAL_ID}")"
python3 - <<'PY' "$approval_json"
import json, sys
assert json.loads(sys.argv[1]).get("status") == "approved"
print("  OK: approval approved")
PY
assert_no_secrets "$approval_json"

echo "=== 10/13 POST /execution/paper (once) ==="
paper_body="$(python3 - <<PY
import json
print(json.dumps({
    "proposal_id": "$PROPOSAL_ID",
    "approval_id": "$APPROVAL_ID",
    "symbol": "$SYMBOL",
    "side": "buy",
    "type": "limit",
    "size": "$MIN_SIZE",
    "price": "$LIMIT_PRICE",
    "reduce_only": False,
    "idempotency_key": "$IDEMPOTENCY_KEY",
}))
PY
)"
paper_json="$(curl -fsS -X POST -H "$(auth_header)" -H 'Content-Type: application/json' \
  "${BACKEND_URL}/execution/paper" -d "$paper_body")"
ORDER_ID="$(python3 - <<'PY' "$paper_json"
import json, sys
print(json.loads(sys.argv[1])["id"])
PY
)"
EXCHANGE_ORDER_ID="$(python3 - <<'PY' "$paper_json"
import json, sys
print(json.loads(sys.argv[1]).get("exchange_order_id") or "")
PY
)"
python3 - <<'PY' "$paper_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("type") == "limit", p
assert str(p.get("size")) == "0.1", p
print(f"  OK: internal order id={p.get('id')} exchange_order_id={p.get('exchange_order_id')}")
PY
assert_no_secrets "$paper_json"

if [[ "$EXCHANGE_ORDER_ID" == paper-* || -z "$EXCHANGE_ORDER_ID" ]]; then
  abort "Venue mirror failed (exchange_order_id=${EXCHANGE_ORDER_ID}). STOP — no venue order to verify/cancel."
fi

echo "=== 11/13 Venue order status + idempotency ==="
venue_status_json="$(curl -fsS -H "$(auth_header)" \
  "${BACKEND_URL}/exchange/orders/${INST_ID}/${EXCHANGE_ORDER_ID}")"
python3 - <<'PY' "$venue_status_json"
import json, sys
p = json.loads(sys.argv[1])
filled = str(p.get("filled_size", "0"))
assert filled in ("0", "0.0", "0.00"), p
print(f"  OK: venue status={p.get('status')} filled_size={filled}")
PY
assert_no_secrets "$venue_status_json"

paper_dup_json="$(curl -fsS -X POST -H "$(auth_header)" -H 'Content-Type: application/json' \
  "${BACKEND_URL}/execution/paper" -d "$paper_body")"
python3 - <<'PY' "$paper_json" "$paper_dup_json"
import json, sys
a, b = json.loads(sys.argv[1]), json.loads(sys.argv[2])
assert a["id"] == b["id"], (a["id"], b["id"])
print("  OK: idempotency returned same internal order id")
PY

audit_json="$(curl -fsS -H "$(auth_header)" \
  "${BACKEND_URL}/audit/events?event_type=exchange_demo_order_created&limit=50")"
python3 - <<'PY' "$audit_json" "$IDEMPOTENCY_KEY" "$EXCHANGE_ORDER_ID"
import json, sys
items = json.loads(sys.argv[1]).get("items") or []
key, eid = sys.argv[2], sys.argv[3]
matches = [
    e for e in items
    if e.get("request_id") == key
    or (e.get("redacted_metadata") or {}).get("exchange_order_id") == eid
]
assert len(matches) >= 1, f"no audit match for key={key}"
meta = matches[0].get("redacted_metadata") or {}
assert meta.get("position_mode") == "long_short_mode", meta
assert meta.get("position_side") == "long", meta
print(f"  OK: audit position_mode={meta.get('position_mode')} position_side={meta.get('position_side')}")
PY
assert_no_secrets "$audit_json"

echo "=== 12/13 Cancel venue order ==="
cancel_json="$(curl -fsS -X POST -H "$(auth_header)" \
  "${BACKEND_URL}/exchange/orders/${INST_ID}/${EXCHANGE_ORDER_ID}/cancel")"
python3 - <<'PY' "$cancel_json"
import json, sys
assert json.loads(sys.argv[1]).get("cancelled") is True
print("  OK: venue order cancelled")
PY
assert_no_secrets "$cancel_json"

venue_after_json="$(curl -fsS -H "$(auth_header)" \
  "${BACKEND_URL}/exchange/orders/${INST_ID}/${EXCHANGE_ORDER_ID}?after_cancel=true")"
python3 - <<'PY' "$venue_after_json"
import json, sys
body = json.loads(sys.argv[1])
status = (body.get("status") or "").lower()
source = body.get("status_source") or "venue"
assert status in ("cancelled", "canceled"), status
print(f"  OK: venue status after cancel={status} (source={source})")
PY

positions_after_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/exchange/positions")"
python3 - <<'PY' "$positions_after_json"
import json, sys
count = len(json.loads(sys.argv[1]).get("items") or [])
assert count == 0, count
print("  OK: venue positions still 0")
PY

echo "=== 13/13 Close internal paper position + validation ==="
internal_positions_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/positions?status=open")"
INTERNAL_POSITION_ID="$(python3 - <<'PY' "$internal_positions_json" "$PROPOSAL_ID"
import json, sys
items = json.loads(sys.argv[1]).get("items") or []
proposal_id = sys.argv[2]
match = next((i for i in items if i.get("linked_proposal_id") == proposal_id), None)
if match is None and items:
    match = items[0]
assert match, "no internal paper position found"
print(match["id"])
PY
)"
close_json="$(curl -fsS -X POST -H "$(auth_header)" -H 'Content-Type: application/json' \
  "${BACKEND_URL}/positions/${INTERNAL_POSITION_ID}/close-paper" \
  -d "{\"exit_price\":\"${LIMIT_PRICE}\",\"reason\":\"Slice 66b cleanup after demo limit test\"}")"
python3 - <<'PY' "$close_json"
import json, sys
assert json.loads(sys.argv[1]).get("status") == "closed"
print("  OK: internal paper position closed")
PY
assert_no_secrets "$close_json"

echo "Running validate-exchange-demo-staging.sh..."
BACKEND_URL="${BACKEND_URL}" ./scripts/validate-exchange-demo-staging.sh

if [[ -f "${ROOT_DIR}/scripts/staging-live-smoke.sh" ]]; then
  echo "Running staging-live-smoke.sh (may skip some checks)..."
  FRONTEND_URL="${FRONTEND_URL:-https://alpha-trade-ai-eight.vercel.app}" \
    BACKEND_URL="${BACKEND_URL}" \
    ./scripts/staging-live-smoke.sh || echo "  WARN: staging-live-smoke had non-fatal issues"
fi

echo ""
echo "Slice 66b PASSED — exactly one demo limit order placed and cancelled."
echo "  proposal_id=${PROPOSAL_ID}"
echo "  approval_id=${APPROVAL_ID}"
echo "  internal_order_id=${ORDER_ID}"
echo "  venue_exchange_order_id=${EXCHANGE_ORDER_ID}"
echo "  idempotency_key=${IDEMPOTENCY_KEY}"
echo "  limit_price=${LIMIT_PRICE} size=${MIN_SIZE} side=buy"
