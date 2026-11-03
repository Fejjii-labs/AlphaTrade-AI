#!/usr/bin/env bash
# Validate controlled read-only Binance USD-M staging evidence.
#
# Does not deploy, does not write platform environment variables, and does not
# send exchange credentials.
#
#   ./scripts/validate-live-market-staging.sh --self-check
#   ENV_FILE=.env.staging ./scripts/validate-live-market-staging.sh --profile --require-active
#   BASE_URL=https://api.example.com ./scripts/validate-live-market-staging.sh --remote --require-active
#   BASE_URL=https://api.example.com ./scripts/validate-live-market-staging.sh --remote --expect inactive
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

fail() {
  echo "LIVE MARKET VALIDATION FAILED: $*" >&2
  exit 1
}

usage() {
  echo "Usage: $0 --self-check | --profile [--require-active] | --remote [--require-active|--expect inactive]" >&2
  exit 2
}

MODE=""
REQUIRE_ACTIVE=false
EXPECT="active"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --self-check) MODE="self-check" ;;
    --profile) MODE="profile" ;;
    --remote) MODE="remote" ;;
    --require-active) REQUIRE_ACTIVE=true; EXPECT="active" ;;
    --expect)
      shift
      EXPECT="${1:-}"
      ;;
    *) usage ;;
  esac
  shift
done

if [[ -z "$MODE" ]]; then
  usage
fi
if [[ "$EXPECT" != "active" && "$EXPECT" != "inactive" ]]; then
  fail "expect must be active or inactive"
fi

if [[ "$MODE" == "self-check" ]]; then
  echo "Live market staging validation — self-check (no network)"
  [[ -f "${ROOT_DIR}/docs/live_market_staging_activation.md" ]] || fail "missing activation runbook"
  [[ -f "${ROOT_DIR}/backend/src/app/market_activation/profile.py" ]] || fail "missing activation profile"
  grep -q '^PERPETUAL_EVIDENCE_SOURCE=binance_usdm$' "${ROOT_DIR}/.env.staging.example" \
    || fail "staging example is not the intended live source"
  grep -q '^MARKET_DATA_FUTURES_BASE_URL=https://fapi.binance.com$' "${ROOT_DIR}/.env.staging.example" \
    || fail "staging example futures host is not fapi.binance.com"
  grep -q '^PERPETUAL_EVIDENCE_SECONDARY_SOURCE=bybit_usdt_perpetual$' \
    "${ROOT_DIR}/.env.staging.example" \
    || fail "staging example secondary is not bybit_usdt_perpetual"
  grep -q '^BYBIT_PERPETUAL_BASE_URL=https://api.bybit.com$' "${ROOT_DIR}/.env.staging.example" \
    || fail "staging example Bybit host is not api.bybit.com"
  grep -q '^PERPETUAL_EVIDENCE_SOURCE=replay$' "${ROOT_DIR}/.env.example" \
    || fail "local example must stay on replay"
  grep -q '^PERPETUAL_EVIDENCE_SOURCE=replay$' "${ROOT_DIR}/.env.production.example" \
    || fail "production example must stay on replay"
  # Sources are operator-owned (sync: false); host pins stay synced literals.
  grep -A1 -q 'key: PERPETUAL_EVIDENCE_SOURCE$' "${ROOT_DIR}/render.yaml" \
    || fail "render.yaml missing live source key"
  grep -A1 'key: PERPETUAL_EVIDENCE_SOURCE$' "${ROOT_DIR}/render.yaml" | grep -q 'sync: false' \
    || fail "render.yaml live source must be sync: false"
  grep -q 'value: https://fapi.binance.com' "${ROOT_DIR}/render.yaml" || fail "render.yaml missing futures host"
  grep -A1 'key: PERPETUAL_EVIDENCE_SECONDARY_SOURCE$' "${ROOT_DIR}/render.yaml" \
    | grep -q 'sync: false' \
    || fail "render.yaml missing Bybit secondary key"
  grep -q 'value: https://api.bybit.com' "${ROOT_DIR}/render.yaml" \
    || fail "render.yaml missing Bybit host"
  if grep -E -q 'okx_usdt_swap|OKX_SWAP_BASE_URL|www\.okx\.com' \
    "${ROOT_DIR}/.env.staging.example" "${ROOT_DIR}/render.yaml"; then
    fail "OKX is not the MVP secondary perpetual source"
  fi
  if grep -E -q '^(BINANCE_API_KEY|BINANCE_API_SECRET|BINANCE_FUTURES_API_KEY|FAPI_API_KEY)=' \
    "${ROOT_DIR}/.env.staging.example" "${ROOT_DIR}/.env.example" "${ROOT_DIR}/.env.production.example"; then
    fail "an env example assigns a Binance credential"
  fi
  (
    cd "${ROOT_DIR}/backend"
    PYTHONPATH=src uv run python - <<'PY'
from app.core.config import Settings
from app.market_activation.profile import (
    activation_state,
    live_market_activation_violations,
)

def _local(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": "local",
        "execution_mode": "paper",
        "enable_real_trading": False,
        "provider_mode": "mock",
        "rate_limit_use_redis": False,
        "market_data_cache_use_redis": False,
        "access_token_denylist_use_redis": False,
        "perpetual_evidence_source": "binance_usdm",
        "market_data_futures_base_url": "https://fapi.binance.com",
    }
    base.update(overrides)
    return Settings(**base)

from pydantic import ValidationError

live = _local()
assert activation_state(live) == "active", activation_state(live)
assert live_market_activation_violations(live) == []
rollback = _local(perpetual_evidence_source="replay")
assert activation_state(rollback) == "inactive"
assert live_market_activation_violations(rollback) == []
try:
    _local(market_data_futures_base_url="https://api.binance.com")
except ValidationError as exc:
    assert "fapi.binance.com" in str(exc)
else:
    raise SystemExit("spot host was accepted")
print("  OK: profile accepts live staging shape and replay rollback")
PY
  )
  echo "Live market staging validation self-check passed."
  exit 0
fi

if [[ "$MODE" == "profile" ]]; then
  ENV_FILE="${ENV_FILE:-}"
  if [[ -n "$ENV_FILE" ]]; then
    if [[ ! -f "$ENV_FILE" && -f "${ROOT_DIR}/$ENV_FILE" ]]; then
      ENV_FILE="${ROOT_DIR}/$ENV_FILE"
    fi
    [[ -f "$ENV_FILE" ]] || fail "ENV_FILE not found: $ENV_FILE"
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
  cd "${ROOT_DIR}/backend"
  PYTHONPATH=src uv run python - <<PY
from app.core.config import Settings, get_settings
from app.market_activation.profile import activation_state, perpetual_evidence_health

get_settings.cache_clear()
settings = Settings()
state = activation_state(settings)
expect = "${EXPECT}"
require = "${REQUIRE_ACTIVE}" == "true"
health = perpetual_evidence_health(settings)
print("OK: profile loaded")
for key in (
    "perpetual_evidence_source",
    "perpetual_evidence_activation",
    "live_quote_freshness_seconds",
    "first_perpetual_symbol",
    "exchange_credentials_used_for_market_evidence",
    "spot_fallback_permitted",
    "fabricated_fallback_permitted",
):
    print(f"  {key}={health[key]}")
if require and state != "active":
    raise SystemExit(f"activation state is {state}, expected active")
if not require and state != expect:
    raise SystemExit(f"activation state is {state}, expected {expect}")
if health["live_quote_freshness_seconds"] != 10:
    raise SystemExit("freshness is not 10 seconds")
if health["exchange_credentials_used_for_market_evidence"] is not False:
    raise SystemExit("market evidence reported credentials")
if health["spot_fallback_permitted"] is not False:
    raise SystemExit("spot fallback reported permitted")
PY
  exit 0
fi

BASE_URL="${BASE_URL:-}"
BASE_URL="${BASE_URL%/}"
if [[ -z "$BASE_URL" || "$BASE_URL" == *"<"* ]]; then
  fail "BASE_URL is required for --remote and must not contain placeholders"
fi

health_json="$(curl -fsS "${BASE_URL}/health")"
python3 - <<PY "$health_json"
import json, sys
payload = json.loads(sys.argv[1])
expect = "${EXPECT}"
require = "${REQUIRE_ACTIVE}" == "true"
if payload.get("execution_mode") != "paper":
    raise SystemExit("execution_mode is not paper")
if payload.get("real_trading_enabled") is not False:
    raise SystemExit("real_trading_enabled is not false")
for flag in (
    "market_watcher_enabled",
    "market_watcher_bridge_enabled",
    "watcher_orchestration_enabled",
    "telegram_alerts_enabled",
    "telegram_interaction_enabled",
    "automatic_telegram_delivery_enabled",
):
    if flag in payload and payload.get(flag) is not False:
        raise SystemExit(f"{flag} is not false")
source = payload.get("perpetual_evidence_source")
state = payload.get("perpetual_evidence_activation")
if source not in ("replay", "binance_usdm"):
    raise SystemExit(f"unexpected perpetual_evidence_source={source!r}")
if state not in ("inactive", "active"):
    raise SystemExit(f"activation state {state!r} is not a running posture")
if payload.get("live_quote_freshness_seconds") != 10:
    raise SystemExit("live quote freshness is not 10 seconds")
if payload.get("first_perpetual_symbol") != "BTCUSDT":
    raise SystemExit("first symbol is not BTCUSDT")
if payload.get("exchange_credentials_used_for_market_evidence") is not False:
    raise SystemExit("credentials flag is not false")
if payload.get("spot_fallback_permitted") is not False:
    raise SystemExit("spot fallback flag is not false")
if payload.get("fabricated_fallback_permitted") is not False:
    raise SystemExit("fabricated fallback flag is not false")
if payload.get("live_market_read_only") is not True:
    raise SystemExit("live market path is not read-only")
wanted = "active" if require else expect
if state != wanted:
    raise SystemExit(f"activation is {state}, expected {wanted}")
if wanted == "active" and source != "binance_usdm":
    raise SystemExit("active activation is not binance_usdm")
if wanted == "inactive" and source != "replay":
    raise SystemExit("inactive activation is not replay")
print(f"  OK: source={source} activation={state} freshness=10 symbol=BTCUSDT")
PY

echo "Live market staging validation passed."
