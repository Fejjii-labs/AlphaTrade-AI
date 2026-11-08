#!/usr/bin/env bash
# Slice 42 market watcher + bridge smoke (read-only watcher, paper scan bridge).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BASE_URL="${BASE_URL:-http://localhost:8000}"
BASE_URL="${BASE_URL%/}"

EMAIL="${SMOKE_EMAIL:-market-watcher-smoke-$(date +%s)@example.com}"
PASSWORD="${SMOKE_PASSWORD:?Set SMOKE_PASSWORD (12+ chars; never commit it)}"

auth_header() {
  printf 'Authorization: Bearer %s' "$TOKEN"
}

echo "Market watcher smoke — BASE_URL=${BASE_URL}"

echo "1/8 — health (paper mode)"
health_json="$(curl -fsS "${BASE_URL}/health")"
python3 - <<'PY' "$health_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("execution_mode") == "paper", p
assert p.get("real_trading_enabled") is False, p
print("  OK")
PY

echo "2/8 — register"
register_json="$(curl -fsS -X POST "${BASE_URL}/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\",\"organization_name\":\"MW Smoke Org $(date +%s)\"}")"
TOKEN="$(python3 - <<'PY' "$register_json"
import json, sys
print(json.loads(sys.argv[1])["tokens"]["access_token"])
PY
)"

echo "3/8 — market watcher status"
mw_json="$(curl -fsS -H "$(auth_header)" "${BASE_URL}/market-watcher/status")"
python3 - <<'PY' "$mw_json"
import json, sys
p = json.loads(sys.argv[1])
assert "env_enabled" in p
assert p.get("paper_only") is True
print(f"  OK: env_enabled={p.get('env_enabled')}")
PY

echo "4/8 — manual watcher scan (dry-run preview)"
scan_json="$(curl -fsS -X POST -H "$(auth_header)" -H 'Content-Type: application/json' \
  "${BASE_URL}/market-watcher/scan" \
  -d '{"confirm":"RUN_READ_ONLY_SCAN","symbols":["BTCUSDT"],"timeframes":["15m"],"dry_run":true}')"
python3 - <<'PY' "$scan_json"
import json, sys
p = json.loads(sys.argv[1])
assert "decisions" in p
assert p.get("paper_only") is True
assert p.get("dry_run") is True
assert p.get("alerts_created", 0) == 0
print(f"  OK: status={p.get('status')} candidates={len(p.get('candidates') or [])}")
PY

echo "5/8 — bridge status"
bridge_json="$(curl -fsS -H "$(auth_header)" "${BASE_URL}/market-watcher/bridge/status")"
python3 - <<'PY' "$bridge_json"
import json, sys
p = json.loads(sys.argv[1])
assert "env_enabled" in p
assert p.get("real_trading_enabled") is False
print(f"  OK: bridge env_enabled={p.get('env_enabled')}")
PY

echo "6/8 — bridge tick (or disabled response)"
tick_json="$(curl -fsS -X POST -H "$(auth_header)" "${BASE_URL}/market-watcher/bridge/tick")"
python3 - <<'PY' "$tick_json"
import json, sys
p = json.loads(sys.argv[1])
assert "decisions" in p
assert p.get("paper_only") is True
print(f"  OK: effective_enabled={p.get('effective_enabled')}")
PY

echo "7/8 — bridge history, observations, alerts summary"
bridge_hist_json="$(curl -fsS -H "$(auth_header)" "${BASE_URL}/market-watcher/bridge/history")"
python3 - <<'PY' "$bridge_hist_json"
import json, sys
p = json.loads(sys.argv[1])
assert "items" in p
assert "total" in p
print(f"  OK: bridge history total={p.get('total')}")
PY
obs_json="$(curl -fsS -H "$(auth_header)" "${BASE_URL}/market-watcher/observations")"
python3 - <<'PY' "$obs_json"
import json, sys
p = json.loads(sys.argv[1])
assert "items" in p
assert "total" in p
print(f"  OK: observations total={p.get('total')}")
PY
curl -fsS -H "$(auth_header)" "${BASE_URL}/alerts/summary" >/dev/null
echo "  OK: alerts summary"

echo "8/8 — real trading disabled"
python3 - <<'PY' "$health_json"
import json, sys
assert json.loads(sys.argv[1]).get("real_trading_enabled") is False
print("  OK")
PY

echo "Market watcher smoke passed (read-only, paper only)."
