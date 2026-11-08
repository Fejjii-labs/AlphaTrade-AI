# Risk management

At main `b58beda` (October 7, 2026), deterministic eligibility, account exposure, daily limits, immutable plan authorization, freshness and kill-switch checks gate canonical paper/demo entry. New authority requires [allocation-weighted gross planned 1R](../backend/src/app/services/planned_reward_risk.py); historical plans are not rewritten. Manual demo exposure counts for risk even though manual tests are excluded from strategy validation/learning. See [current architecture](architecture.md#setup-detection-to-authorization-execution-and-journal) and [manual demo bounds](manual_blofin_demo_acceptance.md). Real-money execution is refused.

The detailed discipline APIs and legacy PnL/Agent-tool contract below retain the Slice 45 material. Their listed PnL sources describe that dashboard/compatibility contract; they are not a complete inventory of canonical execution risk accounting or native exchange outcomes.

## User risk settings

Persisted per organization + user in `user_risk_settings`:

| Field | Purpose |
| --- | --- |
| `daily_loss_limit` | Realized+unrealized paper loss that sets the display/WARN loss lock (does not refuse since AT-ADR-152) |
| `daily_target` | Green-day protection threshold |
| `max_trades_per_day` | Overtrading guard |
| `max_risk_per_trade_percent` | Position sizing guidance (≤ 10%) |
| `default_account_balance` | Paper equity reference |
| `timezone` | Local day boundaries (invalid values fall back to UTC) |
| `green_day_protection_enabled` | Toggle green-day signal |
| `one_loss_stop_enabled` | Lock after first losing closed paper trade today |
| `overtrading_guard_enabled` | Toggle frequency guard |
| `notes` | Optional trader notes |

### API

- `GET /risk/settings` — returns persisted settings or safe system defaults (`using_defaults: true`)
- `PATCH /risk/settings` — partial update; audit logged (`risk_settings_updated`)
- `POST /risk/settings/reset-defaults` — restore system defaults

All routes are auth-protected, tenant-scoped, and rate limited. No broker or live trading paths.

## Which rule can stop trading (after AT-ADR-152)

Fable audit L5/M4/L1/L4, checked against main `c5a46c99` (2026-11-07). Since
AT-ADR-152 the **account daily equity stop is the only loss rule that halts
entries**. Every other daily-loss figure is display or warning only.

| Rule | Scope | Clock | Effect | Reset |
| --- | --- | --- | --- | --- |
| Account daily equity stop (`risk_policy/equity_stop.py`) | Organization + user: `default_account_balance` + all-time realized (closed **and liquidated** legacy positions, canonical paper and manual demo journal trades) + legacy open marks | 00:00 UTC baseline | Equity below baseline × (1 − `ACCOUNT_EQUITY_STOP_THRESHOLD_PERCENT`, 10%) arms the organization kill switch | Owner deactivates the kill switch; never cleared automatically |
| Kill switch (`risk/kill_switch.py`) | Organization (plus `GLOBAL_KILL_SWITCH_ACTIVE`) | None | Blocks every new entry; fail-closed on read error. Entry-only: it does not close open positions | Owner deactivate with reason |
| Risk-settings daily loss lock (`daily_loss_limit`, `DailyRiskState.locked`) | Organization + user | User's risk-settings timezone day | Dashboard / attention / pre-trade **WARN only** | Next local day |
| Profile breaker figures (daily loss %, consecutive losses, peak drawdown; `risk_policy/breakers.py`) | Organization + account | Informational | Computed and stored, **never refuse** (`refuse_new_trades` is always false; `breaker_active` is always false) | — |
| Autonomous `daily_loss_limit_percent` | Organization | — | Saved and shown, **display only** | — |
| Legacy `RiskLimits.max_daily_loss_pct` (3%) | Request | — | WARN in `check_daily_loss_lock` | — |
| Weekly loss 8% (`check_weekly_loss`) | Replay only | — | BLOCK inside strategy replay; no live entry path sets it | — |

Because nothing refuses on the risk-day lock any more, the different clocks
(user timezone for the display lock, UTC for the stop) no longer decide
whether a trade is allowed (audit M4). The display lock and the profile
figures keep their own denominators (cash limit vs reference equity); the stop
uses its own 00:00 UTC baseline (audit L1).

**Caps are separate and labelled, not merged** (AGENTS.md): the **account cap**
(risk settings `max_trades_per_day`) and the **bot cap** (Autonomous Trading
`max_trades_per_day`, never above the account cap; the page shows
`account_max_trades_per_day` and `effective_max_trades_per_day`). The bot cap
refuses new autonomous entries (`autonomous_max_trades_per_day`). Internal paper
also allows one open position at a time (`max_open_positions` = 1).

**Paper and BloFin demo share one equity reading (audit M2, decision needed).**
The stop reads one figure per organization + user across internal paper and
demo journal trades, measured against the paper `default_account_balance`.
A losing paper day therefore moves the same stop that guards demo entries, and
demo losses count against the paper balance. Demo sizing already uses venue
equity. Splitting the reading per venue would let combined losses exceed 10%
of the stated balance, which would loosen an approved safety rule, so it is
not changed without an explicit decision from Sofien and a Griller review.

## Daily discipline snapshot resolution

The dashboard daily discipline block resolves limits in order:

1. **`configured_daily_state`** — today's `daily_risk_states` row when present
2. **`user_risk_settings`** — persisted user settings (may initialize today's daily state when loss limit is set)
3. **`system_default`** — engine defaults with explicit limitations

The snapshot includes `risk_settings_source` so the UI never silently invents limits.

## Daily PnL sources (paper only)

Today's paper PnL aggregates:

- Closed **paper-validation** `PaperTrade.net_pnl` (local day)
- Closed **proposal-flow** `Position.realized_pnl`
- Unrealized PnL from open proposal-flow positions only (when available)

Returned in `pnl_sources` with limitations when data is incomplete. No broker data.

## Agent tools

`risk_settings_tool` (deterministic, DB-backed):

- `get` — current settings
- `update` — **requires explicit confirmation** (`confirm=true` or "I confirm")
- `discipline`, `discipline_score`, `open_trades`, `paper_pnl`, `loss_lock_reason`

The LLM explains tool output; it must not invent settings or PnL.

## Limitations

- Paper-validation open trades do not include live unrealized PnL in this slice
- Unrealized PnL uses proposal-flow position marks only
- Settings affect paper discipline guidance, not exchange orders
