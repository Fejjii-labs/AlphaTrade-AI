#!/usr/bin/env bash
# Slice 38 strategy + lesson smoke (paper only).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BASE_URL="${BASE_URL:-http://localhost:8000}"
BASE_URL="${BASE_URL%/}"

EMAIL="${SMOKE_EMAIL:-strategy-smoke-$(date +%s)@example.com}"
PASSWORD="${SMOKE_PASSWORD:?Set SMOKE_PASSWORD (12+ chars; never commit it)}"

auth_header() {
  printf 'Authorization: Bearer %s' "$TOKEN"
}

CARD_JSON='{
  "strategy_name": "Smoke HTF Pullback",
  "market_type": "crypto_perp",
  "asset_universe": ["BTCUSDT"],
  "timeframes": ["4h", "1h"],
  "entry_conditions": ["Pullback to EMA cluster"],
  "confirmation_conditions": ["RSI reset above 40"],
  "invalidation": ["Close below swing low"],
  "stop_loss": ["Below invalidation swing"],
  "take_profit_plan": ["TP1 at prior high"],
  "runner_plan": ["Trail after TP1"],
  "position_sizing": ["Max 1% account risk"],
  "add_rules": ["No adds until TP1"],
  "no_trade_rules": ["Skip if funding extreme"],
  "backtest_rules": ["Placeholder — not run"],
  "success_criteria": ["Win rate > 45% in paper"],
  "validation_status": "draft"
}'

RULES_JSON='{
  "primary_timeframe": "4h",
  "entry_rules": [{"trigger_type": "ema_pullback", "conditions": []}],
  "exit_rules": [
    {"rule_type": "fixed_stop", "value": "2"},
    {"rule_type": "tp_multiple", "r_multiple": "1"}
  ],
  "no_trade_rules": []
}'

echo "Strategy smoke — BASE_URL=${BASE_URL}"

echo "1/12 — /health (paper mode, real trading disabled)"
health_json="$(curl -fsS "${BASE_URL}/health")"
python3 - <<'PY' "$health_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("execution_mode") == "paper", p
assert p.get("real_trading_enabled") is False, p
print("  OK")
PY

echo "2/12 — register or login"
register_json="$(curl -fsS -X POST "${BASE_URL}/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\",\"organization_name\":\"Strategy Smoke Org $(date +%s)\"}")"
TOKEN="$(python3 - <<'PY' "$register_json"
import json, sys
print(json.loads(sys.argv[1])["tokens"]["access_token"])
PY
)"

echo "3/12 — create strategy"
strategy_json="$(curl -fsS -X POST "${BASE_URL}/strategies" \
  -H "$(auth_header)" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Smoke HTF Pullback\",\"setup_type\":\"htf_trend_pullback\",\"card\":${CARD_JSON}}")"
STRATEGY_ID="$(python3 - <<'PY' "$strategy_json"
import json, sys
print(json.loads(sys.argv[1])["id"])
PY
)"
echo "  OK: strategy_id=${STRATEGY_ID}"

echo "4/12 — patch structured rules"
curl -fsS -X PATCH "${BASE_URL}/strategies/${STRATEGY_ID}/structured-rules" \
  -H "$(auth_header)" -H 'Content-Type: application/json' \
  -d "${RULES_JSON}" >/dev/null
echo "  OK"

echo "5/12 — create lesson candidate"
lesson_json="$(curl -fsS -X POST "${BASE_URL}/lessons/candidates" \
  -H "$(auth_header)" -H 'Content-Type: application/json' \
  -d "{
    \"source_type\": \"journal\",
    \"lesson_text\": \"Smoke: exited runner too early.\",
    \"mistake_type\": \"early_exit\",
    \"severity\": \"medium\",
    \"related_strategy_id\": \"${STRATEGY_ID}\",
    \"proposed_rule_update\": {
      \"summary\": \"Hold runner until structure break\",
      \"structured_rules_patch\": ${RULES_JSON}
    }
  }")"
LESSON_ID="$(python3 - <<'PY' "$lesson_json"
import json, sys
print(json.loads(sys.argv[1])["id"])
PY
)"
echo "  OK: lesson_id=${LESSON_ID}"

echo "6/12 — accept lesson and create strategy version"
curl -fsS -X PATCH "${BASE_URL}/lessons/candidates/${LESSON_ID}/accept" \
  -H "$(auth_header)" -H 'Content-Type: application/json' \
  -d "{
    \"create_strategy_version\": true,
    \"related_strategy_id\": \"${STRATEGY_ID}\",
    \"accepted_rule_update\": {
      \"summary\": \"Smoke version from lesson\",
      \"structured_rules_patch\": ${RULES_JSON}
    }
  }" >/dev/null
echo "  OK"

echo "7/12 — verify accepted lessons list"
accepted_json="$(curl -fsS -H "$(auth_header)" "${BASE_URL}/lessons/accepted")"
python3 - <<'PY' "$accepted_json" "$LESSON_ID"
import json, sys
items = json.loads(sys.argv[1]).get("items", [])
lesson_id = sys.argv[2]
assert any(i["id"] == lesson_id for i in items), items
print("  OK")
PY

echo "8/12 — create manual level + pre-trade"
level_json="$(curl -fsS -X POST "${BASE_URL}/manual-levels" \
  -H "$(auth_header)" -H 'Content-Type: application/json' \
  -d '{"symbol":"BTCUSDT","exchange":"mock","level_type":"support","price":"60000"}')"
LEVEL_ID="$(python3 - <<'PY' "$level_json"
import json, sys
print(json.loads(sys.argv[1])["id"])
PY
)"
curl -fsS -X POST "${BASE_URL}/pretrade/analyze" \
  -H "$(auth_header)" -H 'Content-Type: application/json' \
  -d "{\"symbol\":\"BTCUSDT\",\"exchange\":\"binance\",\"direction\":\"long\",\"strategy_id\":\"${STRATEGY_ID}\",\"manual_level_ids\":[\"${LEVEL_ID}\"],\"account_size\":10000,\"max_risk_per_trade\":1.0}" >/dev/null
echo "  OK"

echo "9/12 — backtest v1"
backtest_json="$(curl -fsS -X POST "${BASE_URL}/strategies/${STRATEGY_ID}/backtests" \
  -H "$(auth_header)" -H 'Content-Type: application/json' \
  -d '{"assumptions":{"symbol":"BTCUSDT","timeframe":"15m","initial_capital":"10000","fees_bps":10,"slippage_bps":5,"risk_per_trade_pct":1}}')"
python3 - <<'PY' "$backtest_json"
import json, sys
p = json.loads(sys.argv[1])
status = (p.get("status") or "").lower()
assert status in {"completed", "failed"}, p
print(f"  OK: backtest status={p.get('status')}")
PY

echo "10/12 — paper eligibility gates"
elig_json="$(curl -fsS -H "$(auth_header)" "${BASE_URL}/strategies/${STRATEGY_ID}/paper-eligibility")"
python3 - <<'PY' "$elig_json"
import json, sys
p = json.loads(sys.argv[1])
assert "status" in p and "blockers" in p, p
assert p.get("real_trading_enabled") is False, p
print(f"  OK: status={p.get('status')} eligible={p.get('paper_eligible')}")
PY

echo "11/12 — paper validation start"
curl -fsS -X POST "${BASE_URL}/strategies/${STRATEGY_ID}/paper-validation/start" \
  -H "$(auth_header)" >/dev/null
echo "  OK"

echo "12/12 — confirm real trading remains disabled"
providers_json="$(curl -fsS -H "$(auth_header)" "${BASE_URL}/providers/status")"
python3 - <<'PY' "$providers_json" "$health_json"
import json, sys
payload = json.loads(sys.argv[1])
health = json.loads(sys.argv[2])
providers = payload.get("providers", payload)
assert health.get("real_trading_enabled") is False
exchange = next((p for p in providers if p.get("kind") == "exchange"), None)
if exchange:
    detail = (exchange.get("detail") or "").lower()
    assert exchange.get("is_mock") or "paper" in detail or "disabled" in detail, exchange
print("  OK: exchange provider paper-only / mock")
PY

echo "Strategy smoke passed (paper only)."
