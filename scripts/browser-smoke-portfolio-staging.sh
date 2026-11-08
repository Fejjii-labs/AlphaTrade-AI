#!/usr/bin/env bash
# Read-only browser smoke for staging paper portfolio (Slice 91B).
# No orders, proposals, approvals, execution, exchange, Telegram, or automation.
#
# Usage (matches portfolio-smoke credential convention):
#   ./scripts/browser-smoke-portfolio-staging.sh
#   SMOKE_PASSWORD='...' ./scripts/browser-smoke-portfolio-staging.sh
#
# Legacy alias (still accepted):
#   STAGING_BOOTSTRAP_PASSWORD='...' ./scripts/browser-smoke-portfolio-staging.sh
#
# Optional overrides:
#   PLAYWRIGHT_BASE_URL=https://alpha-trade-ai-eight.vercel.app
#   PLAYWRIGHT_API_URL=https://alphatrade-api-staging.onrender.com
#   SMOKE_EMAIL=portfolio-smoke-$(date +%s)@example.com
#   SMOKE_ORG='Portfolio Smoke Org'
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=scripts/smoke-auth-helpers.sh
source "${ROOT_DIR}/scripts/smoke-auth-helpers.sh"

cd "$ROOT_DIR/frontend"

export PLAYWRIGHT_SKIP_WEBSERVER=1
export PLAYWRIGHT_STAGING_PORTFOLIO_SMOKE=1
export PLAYWRIGHT_BASE_URL="${PLAYWRIGHT_BASE_URL:-https://alpha-trade-ai-eight.vercel.app}"
export PLAYWRIGHT_API_URL="${PLAYWRIGHT_API_URL:-https://alphatrade-api-staging.onrender.com}"

export SMOKE_EMAIL="${SMOKE_EMAIL:-portfolio-smoke-$(date +%s)@example.com}"
export SMOKE_PASSWORD="${SMOKE_PASSWORD:-${STAGING_BOOTSTRAP_PASSWORD:?Set SMOKE_PASSWORD or STAGING_BOOTSTRAP_PASSWORD (never commit it)}}"
export SMOKE_ORG="${SMOKE_ORG:-Portfolio Smoke $(date +%s)}"

reject_smoke_password_placeholder "$SMOKE_PASSWORD"

echo "Browser smoke — ${PLAYWRIGHT_BASE_URL}/portfolio (read only)"
npx playwright test e2e/portfolio-staging.spec.ts --project=chromium
echo "Portfolio browser smoke passed."
