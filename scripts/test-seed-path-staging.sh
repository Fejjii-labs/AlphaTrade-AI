#!/usr/bin/env bash
# Smoke-test seed-approved-demo-proposal.py against staging DATABASE_URL (no order placed).
set -eo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

load_env_file() {
  python3 - <<'PY'
from pathlib import Path
for line in Path(".env.staging").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    v = v.strip().strip('"').strip("'")
    print(f"export {k}={v!r}")
PY
}

eval "$(load_env_file)"
export ENVIRONMENT="${ENVIRONMENT:-staging}"
export EXECUTION_MODE="${EXECUTION_MODE:-paper}"
export ENABLE_REAL_TRADING="${ENABLE_REAL_TRADING:-false}"

# shellcheck source=scripts/smoke-auth-helpers.sh
source "${ROOT_DIR}/scripts/smoke-auth-helpers.sh"

BACKEND_URL="${BACKEND_URL:-https://alphatrade-api-staging.onrender.com}"
BACKEND_URL="${BACKEND_URL%/}"
BASE_URL="${BACKEND_URL}"
SEED_SCRIPT="${ROOT_DIR}/scripts/seed-approved-demo-proposal.py"
TICK_SIZE="0.1"
PASSWORD="${SMOKE_PASSWORD:?Set SMOKE_PASSWORD (12+ chars; never commit it)}"

echo "=== Seed path smoke (no order) ==="
[[ -n "${DATABASE_URL:-}" ]] || { echo "missing DATABASE_URL" >&2; exit 1; }
echo "DATABASE_URL: present"

curl -fsS "${BACKEND_URL}/health" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert p["real_trading_enabled"] is False; print("health OK")'

EMAIL="slice66b-seed-test-$(date +%s)@example.com"
ORG_NAME="Slice 66b Seed Test $(date +%s)"
register_json="$(curl -fsS -X POST "${BACKEND_URL}/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"${EMAIL}\",\"password\":\"${PASSWORD}\",\"organization_name\":\"${ORG_NAME}\"}")"
smoke_login_after_register "$register_json"
TOKEN="${SMOKE_ACCESS_TOKEN}"
AUTH="Authorization: Bearer ${TOKEN}"

me_json="$(curl -fsS -H "${AUTH}" "${BACKEND_URL}/auth/me")"
ORG_ID="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["organization"]["id"])' "$me_json")"
USER_ID="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["user"]["id"])' "$me_json")"
echo "org=${ORG_ID} user=${USER_ID}"

ref="$(curl -fsS "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["price"])')"
price="$(python3 - <<PY
from decimal import Decimal, ROUND_DOWN
ref=Decimal("${ref}"); tick=Decimal("${TICK_SIZE}")
print((ref*Decimal("0.75")/tick).to_integral_value(rounding=ROUND_DOWN)*tick)
PY
)"
echo "limit_price=${price}"

seed_out="$(cd "${ROOT_DIR}/backend" && uv run python "${SEED_SCRIPT}" \
  --organization-id "${ORG_ID}" --user-id "${USER_ID}" --price "${price}")"
echo "seed_output=${seed_out}"

PROPOSAL_ID="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1].strip().splitlines()[-1])["proposal_id"])' "$seed_out")"
APPROVAL_ID="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1].strip().splitlines()[-1])["approval_id"])' "$seed_out")"

curl -fsS -H "${AUTH}" "${BACKEND_URL}/proposals/${PROPOSAL_ID}" | python3 -c '
import json,sys
p=json.load(sys.stdin)
assert p.get("risk_result",{}).get("action")=="allow"
print("proposal OK")
'
curl -fsS -H "${AUTH}" "${BACKEND_URL}/approvals/${APPROVAL_ID}" | python3 -c '
import json,sys
assert json.load(sys.stdin)["status"]=="approved"
print("approval OK")
'
echo "SEED PATH OK — no order placed"
