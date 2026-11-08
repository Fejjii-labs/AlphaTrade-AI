#!/usr/bin/env bash
# Slice 39 paper validation runtime smoke (paper only).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BASE_URL="${BASE_URL:-http://localhost:8000}"
BASE_URL="${BASE_URL%/}"

EMAIL="${SMOKE_EMAIL:-paper-validation-smoke-$(date +%s)@example.com}"
PASSWORD="${SMOKE_PASSWORD:?Set SMOKE_PASSWORD (12+ chars; never commit it)}"

auth_header() {
  printf 'Authorization: Bearer %s' "$TOKEN"
}

CARD_JSON='{
  "strategy_name": "Paper Smoke Strategy",
  "market_type": "crypto_perp",
  "asset_universe": ["BTCUSDT"],
  "timeframes": ["15m"],
  "entry_conditions": ["Pullback to EMA cluster"],
  "confirmation_conditions": ["RSI reset above 40"],
  "invalidation": ["Close below swing low"],
  "stop_loss": ["Below invalidation swing"],
  "take_profit_plan": ["TP1 at prior high"],
  "runner_plan": ["Trail after TP1"],
  "position_sizing": ["Max 1% account risk"],
  "add_rules": [],
  "no_trade_rules": [],
  "backtest_rules": [],
  "success_criteria": ["Win rate > 45% in paper"],
  "validation_status": "draft"
}'

RULES_JSON='{
  "primary_timeframe": "15m",
  "entry_rules": [{"trigger_type": "ema_pullback", "conditions": []}],
  "exit_rules": [
    {"rule_type": "fixed_stop", "value": "2"},
    {"rule_type": "tp_multiple", "r_multiple": "1"}
  ],
  "no_trade_rules": []
}'

echo "Paper validation smoke — BASE_URL=${BASE_URL}"

echo "1/14 — health (paper mode)"
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
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\",\"organization_name\":\"Paper Smoke Org $(date +%s)\"}")"
TOKEN="$(python3 - <<'PY' "$register_json"
import json, sys
print(json.loads(sys.argv[1])["tokens"]["access_token"])
PY
)"

echo "3/10 — create strategy + rules + backtest"
strategy_json="$(curl -fsS -X POST "${BASE_URL}/strategies" \
  -H "$(auth_header)" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Paper Smoke\",\"setup_type\":\"htf_trend_pullback\",\"card\":${CARD_JSON}}")"
STRATEGY_ID="$(python3 - <<'PY' "$strategy_json"
import json, sys
print(json.loads(sys.argv[1])["id"])
PY
)"
curl -fsS -X PATCH "${BASE_URL}/strategies/${STRATEGY_ID}/structured-rules" \
  -H "$(auth_header)" -H 'Content-Type: application/json' \
  -d "${RULES_JSON}" >/dev/null
curl -fsS -X POST "${BASE_URL}/strategies/${STRATEGY_ID}/backtests" \
  -H "$(auth_header)" -H 'Content-Type: application/json' \
  -d '{"assumptions":{"symbol":"BTCUSDT","timeframe":"15m","exchange":"mock","initial_capital":"10000","fees_bps":10,"slippage_bps":5,"risk_per_trade_pct":1}}' >/dev/null
echo "  OK: strategy_id=${STRATEGY_ID}"

echo "4/10 — paper eligibility"
elig_json="$(curl -fsS -H "$(auth_header)" "${BASE_URL}/strategies/${STRATEGY_ID}/paper-eligibility")"
python3 - <<'PY' "$elig_json"
import json, sys
p = json.loads(sys.argv[1])
assert "blockers" in p
print(f"  OK: status={p.get('status')}")
PY

echo "5/10 — start paper validation"
run_json="$(curl -fsS -X POST "${BASE_URL}/strategies/${STRATEGY_ID}/paper-validation/start" \
  -H "$(auth_header)" -H 'Content-Type: application/json' \
  -d '{"runtime_mode":"scan_only"}')"
RUN_ID="$(python3 - <<'PY' "$run_json"
import json, sys
print(json.loads(sys.argv[1])["id"])
PY
)"
echo "  OK: run_id=${RUN_ID}"

echo "6/10 — scan"
scan_json="$(curl -fsS -X POST "${BASE_URL}/paper-validation/${RUN_ID}/scan" -H "$(auth_header)")"
python3 - <<'PY' "$scan_json"
import json, sys
p = json.loads(sys.argv[1])
assert "run_id" in p
print(f"  OK: trade_created={p.get('trade_created')}")
PY

echo "7/10 — tick"
tick_json="$(curl -fsS -X POST "${BASE_URL}/paper-validation/${RUN_ID}/tick" -H "$(auth_header)")"
python3 - <<'PY' "$tick_json"
import json, sys
p = json.loads(sys.argv[1])
assert "trades_open" in p
print(f"  OK: closed={p.get('trades_closed')} open={p.get('trades_open')}")
PY

echo "8/10 — signals + trades + metrics"
curl -fsS -H "$(auth_header)" "${BASE_URL}/paper-validation/${RUN_ID}/signals" >/dev/null
curl -fsS -H "$(auth_header)" "${BASE_URL}/paper-validation/${RUN_ID}/trades" >/dev/null
metrics_json="$(curl -fsS -H "$(auth_header)" "${BASE_URL}/paper-validation/${RUN_ID}/metrics")"
python3 - <<'PY' "$metrics_json"
import json, sys
p = json.loads(sys.argv[1])
assert "max_drawdown_pct" in p
print("  OK")
PY

echo "9/10 — get run"
curl -fsS -H "$(auth_header)" "${BASE_URL}/paper-validation/${RUN_ID}" >/dev/null
echo "  OK"

echo "10/14 — real trading remains disabled"
python3 - <<'PY' "$health_json"
import json, sys
assert json.loads(sys.argv[1]).get("real_trading_enabled") is False
print("  OK")
PY

echo "11/14 — scheduler status"
sched_json="$(curl -fsS -H "$(auth_header)" "${BASE_URL}/paper-validation/scheduler/status")"
python3 - <<'PY' "$sched_json"
import json, sys
p = json.loads(sys.argv[1])
assert "env_enabled" in p
assert p.get("real_trading_enabled") is False
print(f"  OK: env_enabled={p.get('env_enabled')}")
PY

echo "12/14 — manual scheduler tick"
tick_sched_json="$(curl -fsS -X POST -H "$(auth_header)" "${BASE_URL}/paper-validation/scheduler/tick")"
python3 - <<'PY' "$tick_sched_json"
import json, sys
p = json.loads(sys.argv[1])
assert "decisions" in p
print(f"  OK: effective_enabled={p.get('effective_enabled')}")
PY

echo "13/14 — runtime history + alerts summary"
curl -fsS -H "$(auth_header)" "${BASE_URL}/paper-validation/scheduler/history" >/dev/null
curl -fsS -H "$(auth_header)" "${BASE_URL}/alerts/summary" >/dev/null
echo "  OK"

echo "14/14 — metrics still present"
python3 - <<'PY' "$metrics_json"
import json, sys
p = json.loads(sys.argv[1])
assert "paper_trades_count" in p
print("  OK")
PY

echo "Paper validation smoke passed (paper only)."
