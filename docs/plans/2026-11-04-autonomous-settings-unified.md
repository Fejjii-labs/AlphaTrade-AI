# Plan: one unified Autonomous Trading settings page

Branch `claude/autonomous-settings-unified` (from `origin/main` b0b89e8e). Agent: Claude Code.
Status: plan + implementation in one local branch, per Sofien's direct task message
(no push, no PR). AGENTS.md "Plans" asks for a draft plan PR and Griller GO before code;
the task forbids opening PRs, so this file is the plan record and the gate is still owed
before anything is pushed.

## 1. Inventory: what exists today (origin/main b0b89e8e)

The two near-duplicate pages were already merged into one by **#413** (e0fc682c):

| Old surface | Where it was | State on main |
| --- | --- | --- |
| "Autonomous Trading Settings" (bot limits, `PATCH /autonomous/settings`, #293) | `/risk/autonomous` | `next.config` redirect → `/autonomous#limits` (`phase-b-redirects.ts:27`); page file re-exports `/autonomous` |
| "Configure Account Limits" (account limits, `PATCH /risk/settings`) | `/risk`, link from `/strategies#risk` | redirect → `/autonomous#limits` (`phase-b-redirects.ts:26`); `/strategies#risk` → `router.replace("/autonomous#limits")` |
| Read-only risk summary | `components/settings/RiskSettingsSummary.tsx` | not rendered anywhere (dead component) |

Current `/autonomous` page (`frontend/src/app/(app)/autonomous/page.tsx`):
kill-switch status card (read-only) → `BotSettings` (paper switch, `TradingLimitsCard`,
`BotActivityPanel`) → `RiskProfileSections` (`GradeRiskCard`, `LeverageSettingsSlot`)
→ `StrategyStatsPanel`. Header carries the existing `KillSwitchButton` (unchanged,
not part of this task) and the safety posture (real trading disabled).

So this task is **completing** the unified page, not merging two live pages.

## 2. Field → backend map

| Field Sofien edits | Backend today | Bounds source | Editable today? |
| --- | --- | --- | --- |
| Leverage (demo ≤ 20x, per-coin overrides, margin mode) | **PR #430 (unmerged)**: `demo_leverage*` on `autonomous_trading_settings`, migration `a24demoleverage001` | `max_demo_leverage_ceiling` in #430 view; `HARD_MAX_LEVERAGE=20` in `risk_policy/ceilings.py` | No (pending #430) |
| Leverage profiles (default/max leverage per profile) | `GET /policy/profiles/{kind}`; change = governed proposal + apply with human proof | `HARD_MAX_LEVERAGE` | Read-only (governed) |
| Position size | account `default_account_balance` (`PATCH /risk/settings`) | `PositiveDecimal` | Yes |
| Per-trade risk (bot) | `risk_per_trade_percent` (`PATCH /autonomous/settings`), validated `>0, ≤5` | `PAPER_DEMO_MAX_RISK_PER_TRADE_PERCENT=5` (exposed as `max_risk_per_trade_percent_ceiling`) | Yes |
| Per-trade risk (account) | `max_risk_per_trade_percent` (`PATCH /risk/settings`), `>0, ≤10` | pydantic `le=10` on `UserRiskSettingsUpdate` | Yes (bound not exposed → gap) |
| Stop-loss band (1–5 % of equity per trade, demo) | env `GOVERNED_DEMO_STOP_LOSS_MIN/MAX_PERCENT` (#424); floor/ceiling `STOP_LOSS_BAND_FLOOR/CEILING_PERCENT` in `services/governed_blofin_demo.py` | constants | **No** (env only; not exposed to UI → gap) |
| Daily loss limit % (bot) | `daily_loss_limit_percent` `>0, ≤6` | `HARD_MAX_DAILY_LOSS_PERCENT` (exposed) | Yes |
| Daily loss limit (account, amount) | `daily_loss_limit` | positive | Yes |
| Max trades/day (account + bot, both kept, bot ≤ account) | both PATCH endpoints | bot `1..1000`, account `≥1` | Yes |
| Per-coin notional cap | profile `max_notional_per_trade_percent` (governed) | `PAPER_DEMO_MAX_NOTIONAL_PERCENT=2000` / `HARD_MAX_NOTIONAL_PERCENT=100` | Read-only (governed) |
| Exposure limit | profile `max_aggregate_notional_percent`, `max_aggregate_open_risk_percent` (governed) | same + `HARD_MAX_AGGREGATE_OPEN_RISK_PERCENT=6` | Read-only; aggregate notional not shown → gap |
| Strategy selector | `strategy_id` + `strategy_options` | enabled user strategies | Yes |
| Watchlist coin selector | `symbol` + `symbol_options` | enabled watchlist symbols | Yes |
| Kill switch / real-money block | `GET /risk/kill-switch`, safety posture | — | Read-only status (must stay) |

Audit: `PATCH /autonomous/settings` writes `AUTONOMOUS_TRADING_SETTINGS_UPDATED`
with a `changes` map; `PATCH /risk/settings` writes `RISK_SETTINGS_UPDATED`;
profile apply is audited by `RiskPolicyService`. Reused as-is.

## 3. Gaps

1. UI input bounds are partly hard-coded (`min={1}` etc.) and the account risk ceiling
   (10 %) and trade-cap ceiling (1000) are not read from the backend.
2. Stop-loss band is invisible in the UI.
3. Aggregate notional (exposure) cap is not shown.
4. Leverage section must feature-detect #430's fields and say "pending PR #430" otherwise.

## 4. Constraints that shape the scope

- AGENTS.md: **only one open migration PR at a time**; #430 holds `a24demoleverage001`.
  Hot single-owner files: `autonomous_trading_settings` db/schema/service,
  `schemas/risk.py`, `services/risk/`, migrations — #430 edits the first three.
- Per-coin cap, exposure and leverage profiles are **governed** risk-policy fields:
  apply needs a human step-up/Telegram proof. A plain PATCH would bypass governance.
- Stop-loss band is consumed inside `governed_blofin_demo.py` (#430: +436 lines).

## 5. Decision: what is built vs scoped out

Built (no migration, no hot-file edits):
- **Backend** `GET /autonomous/settings/bounds` (reader role, read-only): one
  config-driven payload of every hard ceiling and the configured stop-loss band, read from
  the existing constants / pydantic field metadata / `Settings` (single source, no copies).
  New files `schemas/autonomous_settings_bounds.py`, `services/autonomous_settings_bounds.py`;
  route added to `api/routes/autonomous_trading_settings.py` (not touched by #430).
- **Frontend**:
  - `TradingLimitsCard` reads min/max/hints from bounds (falls back to the view's ceilings).
  - New read-only `GuardrailsCard` (`#guardrails`): stop-loss band, hard ceilings,
    "how to change" note.
  - `GradeRiskCard`: add "Max total exposure (notional)" row.
  - New `LeverageSection`: if the settings response has `demo_leverage` → render
    `LeverageSettingsSlot` (#430's editor once merged); else read-only profile leverage
    + ceiling + "Leverage config pending PR #430". `LeverageSettingsSlot.tsx` is **not edited**.
  - Settings view loaded once in `BotSettings` and passed to `RiskProfileSections`
    (new optional prop; no prop = old behaviour so #430's test keeps passing).
- Redirects already exist; keep them, add a test that both old routes still redirect.

Scoped out (needs Sofien / follow-up after #430 merges):
- Editable stop-loss band per user (needs DB field + migration + wiring in
  `governed_blofin_demo.py`) — would be a second open migration and collide with #430.
- Editable per-coin notional cap / exposure / leverage profiles from this page — governed
  proposal + human proof flow; needs a product decision on a web proof UI.
- Raising the bot per-trade risk floor to 1 % (backend allows `>0`).

## 6. Tests (targeted only)

- `uv run pytest tests/test_autonomous_settings_bounds.py`; ruff + mypy on touched files.
- `npx vitest run` on touched components + redirect test; `npx tsc --noEmit`; eslint on touched files.
- Commits carry `Full-Backend: false`.
