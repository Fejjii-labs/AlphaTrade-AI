#!/usr/bin/env bash
# Read-only API smoke for paper portfolio endpoints (Slice 91B).
#
# Usage:
#   BACKEND_URL=https://alphatrade-api-staging.onrender.com ./scripts/portfolio-smoke.sh
#
# Optional owner token (skips register):
#   SMOKE_ACCESS_TOKEN='...' SKIP_REGISTER=true ./scripts/portfolio-smoke.sh
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
EMAIL="${SMOKE_EMAIL:-portfolio-smoke-$(date +%s)@example.com}"
PASSWORD="${SMOKE_PASSWORD:?Set SMOKE_PASSWORD (12+ chars; never commit it)}"
ORG_NAME="${SMOKE_ORG:-Portfolio Smoke $(date +%s)}"

assert_no_secrets() {
  python3 - <<'PY' "$1"
import json, re, sys
raw = sys.argv[1]
forbidden = [
    r"TELEGRAM_BOT_TOKEN", r"sk-[a-zA-Z0-9]{20,}", r"bot[0-9]{8,}:[A-Za-z0-9_-]{20,}",
    r"postgresql\+psycopg://", r"access-key", r"access-passphrase",
]
for pattern in forbidden:
    assert re.search(pattern, raw, re.I) is None, f"Secret-like value in response: {pattern}"
PY
}

auth_header() {
  printf 'Authorization: Bearer %s' "$TOKEN"
}

echo "Portfolio API smoke — BACKEND_URL=${BACKEND_URL}"

if [[ "$SKIP_REGISTER" == "true" ]]; then
  if [[ -z "${SMOKE_ACCESS_TOKEN:-}" ]]; then
    echo "SMOKE_ACCESS_TOKEN required when SKIP_REGISTER=true" >&2
    exit 1
  fi
  TOKEN="$SMOKE_ACCESS_TOKEN"
else
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

portfolio_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/performance/portfolio")"
python3 - <<'PY' "$portfolio_json"
import json, sys
p = json.loads(sys.argv[1])
assert p["safety"]["paper_only"] is True, p["safety"]
assert p["safety"]["real_trading_enabled"] is False, p["safety"]
for field in ("starting_balance", "current_equity", "cumulative_realized_pnl"):
    assert field in p["account"], p["account"]
assert len(p["equity_curve"]) >= 1, p
assert isinstance(p["daily_series"], list), p
assert "by_symbol" in p["breakdowns"], p["breakdowns"]
dumped = json.dumps(p).lower()
for bad in ("enable trading", "place order", "execute trade", "approve proposal"):
    assert bad not in dumped, f"unsafe wording: {bad}"
print("  OK: GET /performance/portfolio")
PY
assert_no_secrets "$portfolio_json"

snapshots_json="$(curl -fsS -H "$(auth_header)" "${BACKEND_URL}/performance/snapshots")"
python3 - <<'PY' "$snapshots_json"
import json, sys
p = json.loads(sys.argv[1])
assert "items" in p and "total" in p, p
print(f"  OK: GET /performance/snapshots total={p['total']}")
PY
assert_no_secrets "$snapshots_json"

echo "Portfolio API smoke passed."
