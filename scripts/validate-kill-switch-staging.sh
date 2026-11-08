#!/usr/bin/env bash
# AT-014 staging kill-switch activation validation (controlled).
#
# Requirements:
#   - Explicit BACKEND_URL or BASE_URL pointing at staging (never production)
#   - .env.staging with DATABASE_URL for DB bootstrap (never printed)
#
# Usage:
#   BACKEND_URL=https://alphatrade-api-staging.onrender.com \
#     ./scripts/validate-kill-switch-staging.sh
#
# Always attempts to restore the kill switch to inactive on exit (success or failure).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=scripts/smoke-auth-helpers.sh
source "${ROOT_DIR}/scripts/smoke-auth-helpers.sh"

BACKEND_URL="${BACKEND_URL:-${BASE_URL:-}}"
if [[ -z "${BACKEND_URL}" ]]; then
  echo "FAIL: set BACKEND_URL or BASE_URL explicitly to the staging API." >&2
  echo "Example: BACKEND_URL=https://alphatrade-api-staging.onrender.com $0" >&2
  exit 1
fi
BACKEND_URL="${BACKEND_URL%/}"
BASE_URL="${BACKEND_URL}"
export BASE_URL

# Refuse production / ambiguous hosts.
_url_lower="$(printf '%s' "${BACKEND_URL}" | tr '[:upper:]' '[:lower:]')"
if [[ "${_url_lower}" != *staging* ]]; then
  echo "FAIL: BACKEND_URL must contain 'staging' (got ${BACKEND_URL})." >&2
  exit 1
fi
case "${_url_lower}" in
  *production*|*prod.*|*://prod*|*live*)
    echo "FAIL: refusing production-like BACKEND_URL (${BACKEND_URL})." >&2
    exit 1
    ;;
esac
if [[ "${_url_lower}" != https://* ]]; then
  echo "FAIL: BACKEND_URL must use https:// (${BACKEND_URL})." >&2
  exit 1
fi

PASSWORD="${SMOKE_PASSWORD:?Set SMOKE_PASSWORD (12+ chars; never commit it)}"
TICK_SIZE="0.1"
MIN_SIZE="0.1"
SYMBOL="ETHUSDT"
SEED_SCRIPT="${ROOT_DIR}/scripts/seed-approved-demo-proposal.py"
RUN_ID="at014-ks-$(date +%s)"
OWNER_TOKEN=""
ORG_ID=""
OWNER_USER_ID=""
LIMIT_PRICE=""
PROPOSAL_ID=""
APPROVAL_ID=""
TRADER_TOKEN=""
_CLEANUP_DONE=0

load_env_file() {
  python3 - <<'PY'
from pathlib import Path
path = Path(".env.staging")
if not path.is_file():
    raise SystemExit("FAIL: .env.staging is required (gitignored; never printed).")
for line in path.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    v = v.strip().strip('"').strip("'")
    # Export only keys needed for DB bootstrap / seed; never echo values.
    if k in {
        "DATABASE_URL",
        "JWT_SECRET",
        "ENVIRONMENT",
        "EXECUTION_MODE",
        "ENABLE_REAL_TRADING",
        "EXCHANGE_MODE",
    }:
        print(f"export {k}={v!r}")
PY
}

eval "$(load_env_file)"
export ENVIRONMENT="${ENVIRONMENT:-staging}"
export EXECUTION_MODE="${EXECUTION_MODE:-paper}"
export ENABLE_REAL_TRADING="${ENABLE_REAL_TRADING:-false}"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "FAIL: DATABASE_URL missing from .env.staging." >&2
  exit 1
fi

auth_header() {
  printf 'Authorization: Bearer %s' "$1"
}

assert_no_secrets() {
  python3 - <<'PY' "$1"
import re, sys
raw = sys.argv[1]
forbidden = [
    r"postgresql", r"redis://", r"rediss://", r"sk-[a-zA-Z0-9]{20,}",
    r"access-key", r"access-passphrase", r"JWT_SECRET", r"DATABASE_URL",
    r"Bearer\s+[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+",
]
for pattern in forbidden:
    assert re.search(pattern, raw, re.I) is None, f"Secret-like value: {pattern}"
PY
}

restore_kill_switch_inactive() {
  if [[ "${_CLEANUP_DONE}" -eq 1 ]]; then
    return 0
  fi
  _CLEANUP_DONE=1
  if [[ -z "${OWNER_TOKEN:-}" ]]; then
    echo "  WARN: skip kill-switch restore (no owner token yet)"
    return 0
  fi
  echo "Cleanup — restore kill switch inactive"
  local code body
  body="$(mktemp)"
  code="$(curl -sS -o "$body" -w '%{http_code}' -X POST \
    -H "$(auth_header "$OWNER_TOKEN")" -H 'Content-Type: application/json' \
    "${BACKEND_URL}/risk/kill-switch/deactivate" \
    -d '{"confirm":true,"reason":"AT-014 validation cleanup restore inactive"}' || true)"
  if [[ "$code" == "200" ]]; then
    python3 - <<'PY' "$(cat "$body")"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("active") is False, p
print("  OK: kill switch restored inactive")
PY
  else
    echo "  WARN: deactivate HTTP ${code} (manual check may be needed)"
  fi
  rm -f "$body"
}

trap restore_kill_switch_inactive EXIT

register_owner() {
  local email="at014-owner-${RUN_ID}@example.com"
  local org_name="AT014 Kill Switch ${RUN_ID}"
  local setup_out login
  setup_out="$(cd "${ROOT_DIR}/backend" && uv run python - <<PY
import os
import uuid
import psycopg
from datetime import UTC, datetime
from pathlib import Path

for line in Path("../.env.staging").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, _, value = line.partition("=")
    os.environ.setdefault(key, value.strip().strip('"').strip("'"))

from app.core.config import get_settings
from app.security.passwords import hash_password

org_id = uuid.uuid4()
user_id = uuid.uuid4()
email = "${email}"
org_name = "${org_name}"
password = "${PASSWORD}"
settings = get_settings()
pg_url = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
now = datetime.now(UTC)
hashed = hash_password(password, settings)

with psycopg.connect(pg_url) as conn:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO organizations (id, name, created_at, updated_at) VALUES (%s, %s, %s, %s)",
            (org_id, org_name, now, now),
        )
        cur.execute(
            """
            INSERT INTO users (id, email, hashed_password, role, risk_profile, timezone,
                               is_active, email_verified, created_at, updated_at)
            VALUES (%s, %s, %s, 'TRADER', 'MODERATE', 'UTC', true, true, %s, %s)
            """,
            (user_id, email.lower(), hashed, now, now),
        )
        cur.execute(
            """
            INSERT INTO memberships (id, user_id, organization_id, role, created_at, updated_at)
            VALUES (%s, %s, %s, 'OWNER', %s, %s)
            """,
            (uuid.uuid4(), user_id, org_id, now, now),
        )
    conn.commit()
print(org_id)
print(user_id)
PY
)"
  ORG_ID="$(echo "$setup_out" | sed -n '1p')"
  OWNER_USER_ID="$(echo "$setup_out" | sed -n '2p')"
  login="$(curl -fsS -X POST "${BACKEND_URL}/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"${email}\",\"password\":\"${PASSWORD}\"}")"
  assert_no_secrets "$login"
  OWNER_TOKEN="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["tokens"]["access_token"])' "$login")"
  echo "  OK: owner org=${ORG_ID} (DB bootstrap + login)"
}

prepare_risk_headroom() {
  curl -fsS -X PATCH -H "$(auth_header "$OWNER_TOKEN")" -H 'Content-Type: application/json' \
    "${BACKEND_URL}/risk/settings" \
    -d '{"default_account_balance":"1000000"}' >/dev/null
  echo "  OK: risk settings default_account_balance raised for validation notional"
}

register_trader_member() {
  local email="at014-trader-${RUN_ID}@example.com"
  cd "${ROOT_DIR}/backend"
  uv run python - <<PY
import os
import uuid
import psycopg
from datetime import UTC, datetime
from pathlib import Path

for line in Path("../.env.staging").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, _, value = line.partition("=")
    os.environ.setdefault(key, value.strip().strip('"').strip("'"))

from app.core.config import get_settings
from app.security.passwords import hash_password

org_id = uuid.UUID("${ORG_ID}")
user_id = uuid.uuid4()
email = "${email}"
password = "${PASSWORD}"
settings = get_settings()
pg_url = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
now = datetime.now(UTC)
hashed = hash_password(password, settings)

with psycopg.connect(pg_url) as conn:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (id, email, hashed_password, role, risk_profile, timezone,
                               is_active, email_verified, created_at, updated_at)
            VALUES (%s, %s, %s, 'TRADER', 'MODERATE', 'UTC', true, true, %s, %s)
            """,
            (user_id, email.lower(), hashed, now, now),
        )
        cur.execute(
            """
            INSERT INTO memberships (id, user_id, organization_id, role, created_at, updated_at)
            VALUES (%s, %s, %s, 'TRADER', %s, %s)
            """,
            (uuid.uuid4(), user_id, org_id, now, now),
        )
    conn.commit()
print("  OK: trader user inserted for org (DB setup)")
PY
  local login
  login="$(curl -fsS -X POST "${BACKEND_URL}/auth/login" \
    -H 'Content-Type: application/json' \
    -d "{\"email\":\"${email}\",\"password\":\"${PASSWORD}\"}")"
  assert_no_secrets "$login"
  TRADER_TOKEN="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["tokens"]["access_token"])' "$login")"
  echo "  OK: trader login succeeded"
}

ensure_inactive() {
  local json
  json="$(curl -fsS -H "$(auth_header "$OWNER_TOKEN")" "${BACKEND_URL}/risk/kill-switch")"
  if python3 -c 'import json,sys; sys.exit(0 if json.loads(sys.argv[1]).get("active") else 1)' "$json"; then
    curl -fsS -X POST -H "$(auth_header "$OWNER_TOKEN")" -H 'Content-Type: application/json' \
      "${BACKEND_URL}/risk/kill-switch/deactivate" \
      -d '{"confirm":true,"reason":"AT-014 validation restore inactive"}' >/dev/null
    echo "  OK: deactivated prior active state"
  else
    echo "  OK: already inactive"
  fi
}

seed_proposal() {
  local ref price seed_out
  ref="$(curl -fsS "https://api.binance.com/api/v3/ticker/price?symbol=${SYMBOL}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["price"])')"
  price="$(python3 - <<PY
from decimal import Decimal, ROUND_DOWN
ref=Decimal("${ref}"); tick=Decimal("${TICK_SIZE}")
print((ref*Decimal("0.75")/tick).to_integral_value(rounding=ROUND_DOWN)*tick)
PY
)"
  seed_out="$(cd "${ROOT_DIR}/backend" && uv run python "${SEED_SCRIPT}" \
    --organization-id "${ORG_ID}" --user-id "${OWNER_USER_ID}" --price "${price}" --size "${MIN_SIZE}" --symbol "${SYMBOL}")"
  PROPOSAL_ID="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1].strip().splitlines()[-1])["proposal_id"])' "$seed_out")"
  APPROVAL_ID="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1].strip().splitlines()[-1])["approval_id"])' "$seed_out")"
  LIMIT_PRICE="$price"
  echo "  OK: proposal=${PROPOSAL_ID} approval=${APPROVAL_ID} price=${LIMIT_PRICE}"
}

place_paper() {
  local key="$1"
  local body http
  body="$(python3 - <<PY
import json
print(json.dumps({
    "proposal_id": "${PROPOSAL_ID}",
    "approval_id": "${APPROVAL_ID}",
    "symbol": "${SYMBOL}",
    "side": "buy",
    "type": "limit",
    "size": "${MIN_SIZE}",
    "price": "${LIMIT_PRICE}",
    "reduce_only": False,
    "idempotency_key": "${key}",
}))
PY
)"
  http="$(curl -sS -o /tmp/at014-paper.json -w '%{http_code}' -X POST \
    -H "$(auth_header "$OWNER_TOKEN")" -H 'Content-Type: application/json' \
    "${BACKEND_URL}/execution/paper" -d "$body")"
  echo "$http"
}

echo "AT-014 kill-switch staging validation — BACKEND_URL=${BACKEND_URL} RUN_ID=${RUN_ID}"

echo "1/12 — safety invariants (/health + /exchange/status)"
health_json="$(curl -fsS "${BACKEND_URL}/health")"
python3 - <<'PY' "$health_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("environment") == "staging", p
assert p.get("execution_mode") == "paper", p
assert p.get("real_trading_enabled") is False, p
print(f"  OK: staging paper real_trading=false git_sha={p.get('git_sha')}")
PY

register_owner
echo "  OK: login"
exchange_json="$(curl -fsS -H "$(auth_header "$OWNER_TOKEN")" "${BACKEND_URL}/exchange/status")"
python3 - <<'PY' "$exchange_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("exchange_mode") == "paper_exchange_demo", p
assert p.get("real_trading_enabled") is False, p
print("  OK: EXCHANGE_MODE=paper_exchange_demo")
PY

worker_json="$(curl -fsS -H "$(auth_header "$OWNER_TOKEN")" "${BACKEND_URL}/worker/health")"
python3 - <<'PY' "$worker_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("configured") is False, p
print("  OK: worker configured=false")
PY

echo "2/12 — ensure kill switch inactive"
ensure_inactive

echo "2b/12 — risk headroom for valid paper path"
prepare_risk_headroom

echo "3/12 — seed approved proposal"
seed_proposal

echo "4/12 — paper execution allowed when inactive"
http="$(place_paper "${RUN_ID}-inactive")"
python3 - <<PY "$http"
import json, sys
http = sys.argv[1]
assert http == "200", f"expected 200, got {http}"
with open("/tmp/at014-paper.json") as f:
    body = json.load(f)
assert body.get("id"), body
print(f"  OK: paper order placed id={body.get('id')}")
PY

echo "5/12 — owner activates kill switch (confirm + reason)"
activate_json="$(curl -fsS -X POST -H "$(auth_header "$OWNER_TOKEN")" -H 'Content-Type: application/json' \
  "${BACKEND_URL}/risk/kill-switch/activate" \
  -d '{"confirm":true,"reason":"AT-014 staging validation activate"}')"
python3 - <<'PY' "$activate_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("active") is True, p
assert p.get("execution_blocked") is True, p
assert p.get("reason"), p
print(f"  OK: active=true version={p.get('version')}")
PY

echo "6/12 — paper/demo execution blocked when active"
http="$(place_paper "${RUN_ID}-blocked")"
blocked_body="$(cat /tmp/at014-paper.json)"
python3 - <<PY "$http" "$blocked_body"
import json, sys
http, raw = sys.argv[1], sys.argv[2]
assert http == "403", f"expected 403, got {http}"
body = json.loads(raw)
err = body.get("error") or {}
details = err.get("details") or {}
assert details.get("reason") == "kill_switch_active", details
print("  OK: trading_policy_violation kill_switch_active")
PY

echo "7/12 — non-owner (trader) cannot mutate"
register_trader_member
trader_http="$(curl -sS -o /tmp/at014-trader-deny.json -w '%{http_code}' -X POST \
  -H "$(auth_header "$TRADER_TOKEN")" -H 'Content-Type: application/json' \
  "${BACKEND_URL}/risk/kill-switch/activate" \
  -d '{"confirm":true,"reason":"trader should fail"}')"
python3 - <<PY "$trader_http"
import sys
http = sys.argv[1]
assert http == "403", f"expected 403, got {http}: {open('/tmp/at014-trader-deny.json').read()[:200]}"
print("  OK: trader activate denied")
PY

echo "8/12 — owner deactivates and restores operation"
deactivate_json="$(curl -fsS -X POST -H "$(auth_header "$OWNER_TOKEN")" -H 'Content-Type: application/json' \
  "${BACKEND_URL}/risk/kill-switch/deactivate" \
  -d '{"confirm":true,"reason":"AT-014 validation restore after block test"}')"
python3 - <<'PY' "$deactivate_json"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("active") is False, p
print("  OK: inactive restored")
PY

http="$(place_paper "${RUN_ID}-restored")"
python3 - <<PY "$http"
import json, sys
assert sys.argv[1] == "200", f"expected 200 after deactivate, got {sys.argv[1]}"
with open("/tmp/at014-paper.json") as f:
    body = json.load(f)
assert body.get("id"), body
print(f"  OK: paper order after deactivate id={body.get('id')}")
PY

echo "9/12 — activate for persistence / restart test"
curl -fsS -X POST -H "$(auth_header "$OWNER_TOKEN")" -H 'Content-Type: application/json' \
  "${BACKEND_URL}/risk/kill-switch/activate" \
  -d '{"confirm":true,"reason":"AT-014 persistence probe"}' >/dev/null
pre_restart="$(curl -fsS -H "$(auth_header "$OWNER_TOKEN")" "${BACKEND_URL}/risk/kill-switch")"
python3 - <<'PY' "$pre_restart"
import json, sys
p = json.loads(sys.argv[1])
assert p.get("active") is True, p
print(f"  OK: pre-restart active=true version={p.get('version')}")
PY

echo "10/12 — API restart (Render) and re-read persisted state"
SERVICE_ID=""
if render services --output json >/tmp/render-services.json 2>/tmp/render-services.err; then
  SERVICE_ID="$(python3 - <<'PY'
import json
from pathlib import Path
raw = Path("/tmp/render-services.json").read_text().strip()
if not raw:
    raise SystemExit(0)
data = json.loads(raw)
items = data if isinstance(data, list) else data.get("services", [])
for item in items:
    svc = item.get("service", item)
    name = svc.get("name") or ""
    if name == "alphatrade-api-staging":
        print(svc.get("id") or "")
        break
PY
)"
fi
if [[ -n "${SERVICE_ID:-}" ]]; then
  render restart "$SERVICE_ID" --confirm >/dev/null
  echo "  OK: restart requested for alphatrade-api-staging"
  attempt=0
  until curl -fsS "${BACKEND_URL}/health/ready" >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [[ "$attempt" -ge 60 ]]; then
      echo "FAIL: API not ready after restart" >&2
      exit 1
    fi
    sleep 5
  done
  echo "  OK: API ready after restart"
else
  echo "  WARN: Render CLI unavailable; verifying DB persistence directly"
  cd "${ROOT_DIR}/backend"
  uv run python - <<PY
import uuid
import psycopg

org_id = uuid.UUID("${ORG_ID}")
pg_url = "${DATABASE_URL}".replace("postgresql+psycopg://", "postgresql://")
with psycopg.connect(pg_url) as conn:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT active, version FROM kill_switch_states WHERE organization_id = %s",
            (org_id,),
        )
        row = cur.fetchone()
assert row is not None, "kill_switch_states row missing"
assert row[0] is True, row
print(f"  OK: DB row active={row[0]} version={row[1]}")
PY
fi

post_restart="$(curl -fsS -H "$(auth_header "$OWNER_TOKEN")" "${BACKEND_URL}/risk/kill-switch")"
python3 - <<'PY' "$post_restart" "$pre_restart"
import json, sys
post, pre = json.loads(sys.argv[1]), json.loads(sys.argv[2])
assert post.get("active") is True, post
assert post.get("organization_id") == pre.get("organization_id"), (post, pre)
assert post.get("version") == pre.get("version"), (post.get("version"), pre.get("version"))
print(f"  OK: post-restart active=true version={post.get('version')} persisted")
PY

echo "11/12 — restore inactive (required cleanup)"
# Explicit restore; EXIT trap remains idempotent.
_CLEANUP_DONE=0
restore_kill_switch_inactive

echo "12/12 — post-validation safety recheck"
BASE_URL="${BACKEND_URL}" "${ROOT_DIR}/scripts/verify-safety.sh" | tail -3

echo "AT-014 kill-switch staging validation passed."
