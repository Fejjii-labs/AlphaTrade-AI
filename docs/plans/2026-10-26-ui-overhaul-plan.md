# AlphaTrade AI — fixes, IA restructure, voice: plan (no code)

## Context

Sofien's verdict on the app: cluttered, link soup, duplicate pages, broken features. This plan covers the six outputs the brief asks for: critique, sitemap, wireframes, design tokens and templates, the GPT Live inventory, and an ordered PR plan with acceptance criteria. It is grounded in a read-only survey of `main` at 632da327. Nothing has been built, cut or redirected.

Decisions Sofien made during planning:
- **Voice:** a voice-only admission gate. Staging keeps `PROVIDER_MODE=fallback`, and Sofien sets the `AGENT_LIVE_*` env vars himself.
- **Track order:** Track B starts once A1–A5 are merged and checked on staging. A6/A7 run in parallel, and only B5c (the Trades table) waits for A7.
- **Cap conflict:** tightening the account cap is never blocked. Effective = min(bot, account). Only saving a bot cap above the account cap is rejected.

**Status:** Sofien approved the plan with Griller's conditions (GO WITH CONDITIONS on PR #333). The conditions below are binding and are folded into the affected items in §6.

## Griller conditions (PR #333)

**K1 — A6/A7 BloFin trade import**
- Closed rows are deduplicated by venue `historyId`. Open rows are deduplicated by `positionId` + open time (not the current per-cycle `{stamp}` key).
- The open row is linked to its close, so one position never produces two trades.
- Partial closes are handled: one history record per partial close, linked to the same open position. The doc defines how size and P&L split.
- `positionId` reuse is checked (same id, different open time means a new position).
- Ghost rows (open rows whose position no longer exists) get a gated cleanup: dry-run listing first, and no delete or update without Sofien's approval.
- **The A6 dry-run runs before Nov 1, 2026.**

**K2 — B7a GPT Live voice gate**
- The voice-scoped gate replaces the `provider_mode == "live"` check in all three places: `agent_voice/live_config.py` (`load_live_limits`), `agent_voice/config.py:144` (`load_limits`, the realtime path), and `agent_voice/retrieval.py:39` (`embedding_limitation`).
- A test proves that a voice session refuses mock or fallback answers: with `PROVIDER_MODE=fallback` and voice enabled, no mock reasoning provider and no mock embeddings ever produce a voice reply. It fails closed.
- Retrieval decision, written into the B7 doc for Griller. Proposal: voice vector retrieval uses the same gate plus its own verified embedding receipt. Without that receipt, vector retrieval is off (the existing "Vector embedding unavailable" limitation) and never falls back to mock embeddings.
- **B7 becomes its own track (Track V)** and needs a separate Griller security review before merge.

**Sequencing**
- A3 lands before B3a.
- A3 validates bot ≤ account **only when the bot cap itself changes** in the request. The bot always uses min(bot, account).
- B3a keeps the `TopBar.tsx:197` copy unchanged.

**Work rules**
- One `claude/` branch and one PR per item, targeted tests only, `Full-Backend: false`, never push to `main`.
- A Notion Agent Reports entry after each PR.

---

## 1. Critique of the current UI (from the code)

1. **The sidebar hides the product.** There are 5 flat items, and about 37 more routes live in an "Advanced" catalogue (`components/layout/advanced-routes.ts`) on `/settings/advanced` and in the command menu. The autonomous bot has no entry point. `/risk` belongs to both Strategies and Settings, and Strategies wins by rule order.
2. **The Dashboard is a workbench, not a cockpit.** `TraderDashboardView.tsx` (555 lines) has an account switcher (BloFin demo / simulator), the whole manual order flow hidden in a `<details>`, attention and daily-review cards, and simulator-only blocks. Nothing shows bot status or effective caps.
3. **Settings is three long `<details>` groups plus a link row.** That row ("Exchange connections, Team, Billing & Usage, Diagnostics, Help") is the link soup. Provider health is plain `name: health · Simulated` text, while a `ProviderStatusCard` component exists that nothing renders.
4. **Strategies is one inline page with five concerns:** family cards, a separate "Recent detections →" list, experiments, drafts, "Other configurations" (raw `JSON.stringify(spec)`), and a "Risk policy" disclosure. The family cards show 0 Nested detections because of a backend bug (A2).
5. **Journal and Knowledge are welded together.** `JournalKnowledgeWorkspace.tsx` is 621 lines with hand-built tabs (it does not use `ui/tabs`). `/knowledge` re-exports it. `/journal/statistics` redirects to `/`, so stats don't exist even though the catalogue still lists them. BloFin trades placed directly on the venue only appear for 7 days.
6. **IDs, hashes and developer text leak everywhere.** Examples: "Supporting evidence and identities" on trade detail, "Stored evidence" with UUIDs and model-usage JSON in agent messages, SHA-256 and parser version on knowledge cards, "Strategy version {uuid}" on setups, "Assignment: {id}" on the Watcher, about 15 places that show `id.slice(0,8)…`, Telegram filters asking for comma-separated UUIDs, and "(mock provider in local dev…)" in `EmailVerificationNotice`.
7. **Numbers are raw.** `RiskSettingsSummary` prints Decimal strings (risk `Numeric(20,8)` columns can render as `50.00000000`), with no unit and a hand-built `%`. A good formatter library already exists in `lib/format.ts` but isn't used here.
8. **Agent page.** Two voice systems sit side by side in the composer ("Talk to Agent" GPT Live, plus a browser-speech "Record"). Every voice error becomes one generic line. Proposals show the raw `Status {status}` value, strategy drafts dump JSON, and the header copy is long.
9. **The state primitives are fine.** Tokens are CSS variables with contrast tests, and `ui/` has page-header, card, tabs, badge, segmented-control and sheet. The problem is inconsistent use, not a missing system: pages hand-roll tabs, rows and readouts.

## 2. Sitemap

Redirect mechanism: entries in `lib/navigation/phase-b-redirects.ts`, consumed by `next.config.ts` (`permanent: false`). **A redirected route's `page.tsx` stays in place** until Sofien approves deleting it. "Diagnostics" means listed in the Settings → Diagnostics index, out of the sidebar, and the route still works.

### Sidebar (7 items)

Mobile bottom bar: Dashboard · Trades · Strategies · Agent · More. More opens a sheet with Autonomous, Knowledge and Settings.

| # | Item | Route | Today → change |
|---|---|---|---|
| 1 | Dashboard | `/` | Keep. Remove the account switcher and the manual order flow; add the bot pill. `/dashboard` keeps redirecting to `/`. |
| 2 | Autonomous | `/autonomous` **(new)** | Merges `/risk` and `/risk/autonomous`. Both redirect (`/autonomous#account`, `/autonomous#bot`). The kill switch moves here from `/watchlist`. |
| 3 | Strategies | `/strategies` | Keep. History moves into the cards. `/strategies/detections` → `/strategies`. Risk policy goes to Autonomous. |
| 4 | Trades | `/journal` | Label "Trades". Tabs Trades / Stats. `/journal/statistics` → `/journal?tab=stats` (today it goes to `/`). `/journal?tab=knowledge` → `/knowledge`. |
| 5 | Agent | `/agent` | Keep and overhaul (B7). `/coaching` keeps redirecting to `/agent`. |
| 6 | Knowledge | `/knowledge` | Becomes its own page (no Journal tab). |
| 7 | Settings | `/settings` | 6-section sub-nav (below). |

### Settings sections

| Section | Route | Source |
|---|---|---|
| Account | `/settings` (+ `/settings/team`, `/settings/billing` as sub-pages) | Profile, email verification, paper account, team, billing. `/invitations` keeps redirecting to `/settings/team`. |
| Exchanges | `/settings/exchange` | Unchanged. `/exchange` keeps redirecting here. |
| Notifications | `/settings/notifications` **(new)** | `NotificationSettingsPanel` + `TelegramPolicyForm`. `/settings#notifications` is forwarded client-side by a hash handler. |
| Watcher | `/settings/watcher` **(new)** | `WatcherWatchlistSection` + `WatcherAssignmentsSection` + runtime readout. `/settings#markets` hash is forwarded. `/watchlist` → here (the kill switch moves to Autonomous first). |
| Diagnostics | `/settings/diagnostics` **(new)** | Index generated from the route registry, plus provider status tiles. `/settings/advanced` → here. |
| Help | `/settings/help` | Unchanged. |

### Deep links (kept, and IDs stripped from the UI in Track C)

`/strategies/setups/[id]`, `/decision/candidates/[id]`, `/execution/manual-demo/[commandId]`, `/decision/approvals/[id]`, `/decision/executions/[id]`, `/decision/plans/[id]`, `/decision/outcomes/[id]`, `/backtests/[id]`, `/strategy-lab/[id]`.

### Diagnostics index groups (all routes keep working; out of navigation)

- **Market & alerts:** `/watcher` (scanner), `/market-watcher`, `/market`, `/alerts`, `/alerts/review`, `/tradingview-signals`, `/manual-levels`, `/paper-signal-orchestration`
- **Decisions:** `/decision`, `/decision/candidates`, `/decision/market`, `/decision/strategy`, `/approvals`, `/proposals`, `/pre-trade`
- **Validation & research:** `/paper-validation` + 8 sub-pages, `/research-validation`, `/validation-priority`, `/strategy-quality`, `/strategy-lab`
- **Journal tools:** `/journal/import`, `/journal/comparison`, `/analytics`, `/learning-analytics`, `/lessons`
- **Portfolio:** `/portfolio`, `/positions`, `/workspace`
- **System:** `/settings/audit` (`/audit` keeps redirecting here)

**Public routes:** unchanged (`/login`, `/register`, `/forgot-password`, `/reset-password`, `/verify-email`).

**Candidates for redirect or delete later, after usage is confirmed and Sofien approves (not in this plan):**
- `/proposals` → `/agent`
- `/positions` → `/`
- `/analytics` → `/journal?tab=stats`
- `/workspace`, `/pre-trade`, `/validation-priority`, `/strategy-quality`, `/learning-analytics`, `/lessons`
- Dead code: `AgentVoiceControls.tsx`, `StrategyBrainPanel.tsx`, `StrategySettingsSummary.tsx`, `SfpPanel`/`NestedContinuationPanel`, `MobileMoreNav.tsx`, `nav-items.ts`

**Where I challenge the brief:**
- The kill switch currently sits on `/watchlist`. Redirecting that page without moving the switch would bury a safety control.
- "Each autonomous paper bot": the model has **one** bot config per user (one `strategy_id` + one `symbol`). The live view shows "the bot", grouped by strategy as it acts. Multiple bots would be a new feature.
- B1's redirects (`/exchange`, `/audit`) already exist. B1 becomes the route registry and tests that everything after it depends on.

## 3. Text wireframes

Shared shell on every page: the sidebar, then a page header with title, at most one line of description, and actions on the right. Details stay closed by default.

**Dashboard `/`**
```
Dashboard                                  [Bot: ON · 2/3 trades today]  [Open Agent]
┌ BloFin demo account ───────────────┐ ┌ Today ─────────────────────────────┐
│ Equity 10,240 USDT   Avail 9,800   │ │ P&L +42.10 USDT (+0.4%)  Trades 2  │
│ Updated 12s ago                    │ │ Loss left 158 USDT of 200          │
└────────────────────────────────────┘ └────────────────────────────────────┘
Open positions
 BTCUSDT  Long  0.05  10x  Entry 67,120  Mark 67,480  +18.00 USDT   [Details]
 (empty: "No open positions")
Latest alerts                                                   [All alerts →]
 10:42  SFP · ZECUSDT · Short · 15m  confirmed
 ▸ Daily review (closed)
```
The bot pill opens `/autonomous`. It reads "Bot: OFF" or "Bot: ON · used/effective cap", and goes amber when the worker is stale.

**Autonomous `/autonomous`**
```
Autonomous                                            [Kill switch]
┌ Paper bot ───────────────────────────────────────────────────────┐
│ [■ ON ]  Owner only (your role: Trader)  ← shown only when locked │
│ Worker: Running · heartbeat 8s   Mode: internal paper             │
└───────────────────────────────────────────────────────────────────┘
Account limits (all trades)                      #account   [Save]
 Account: max trades/day (all trades)   3
 Daily loss limit                        200 USDT
 Max risk per trade                      1%     ▸ More (target, balance, timezone)
Bot limits (≤ account)                           #bot       [Save]
 Bot: max trades/day (≤ account)        5   → Effective 3 (capped by account)
 Daily loss limit 2%   Risk per trade 0.5%   Strategy Nested · v3   Symbol BTCUSDT
What the bot is doing
 Nested · BTCUSDT   Today 1/3   Last: 10:42 skipped – stop too wide   ▸ Last 5 actions
```

**Strategies `/strategies`**
```
Strategies                                                  [Discuss a strategy]
┌ Nested 1-2 ─────────────── 4 in 7 days ┐ ┌ SFP ────────────── 25 in 7 days ┐
│ Status: Active · Paper                 │ │ Status: Active · Paper          │
│ Mon ▂▅▁▃ … (7-day bars)                │ │ ...                              │
│ 10:42 BTCUSDT Long 15m  N2 confirmed → │ │ 09:10 ZECUSDT Short 15m        → │
│ ▸ Show all 7 days                      │ │ ▸ Show all 7 days               │
│ [Ask Agent]  ▸ Versions                │ │ [Ask Agent]  ▸ Versions         │
└────────────────────────────────────────┘ └─────────────────────────────────┘
▸ Experiments   ▸ Captured drafts            (closed; no raw JSON)
```

**Trades `/journal`**
```
Trades                                   [Trades | Stats]        [New demo order]
Filters: All · Open · Closed   Source: All · In-app · BloFin direct
 Date   Symbol   Side  Size  Lev  Entry    Exit     P&L          Fees   Status
 Oct 8  BTCUSDT  Long  0.05  10x  67,120   67,480   +18.00 USDT  0.42   Closed  →
Trade detail (row click → side sheet): numbers, timeline, notes. ▸ Venue details (closed)
Stats tab: Win rate · Net P&L · Profit factor · Avg R · by strategy and by symbol
```
"New demo order" opens a sheet with the existing `ManualDemoTest` flow: preview → confirm.

**Agent `/agent`**
```
Agent                                         [New chat] [History]
 thread: readable answers, Markdown, no "Stored evidence" dumps
 ┌ Proposal: Add SFP alert on ZECUSDT ─────────────┐
 │ Waiting for your decision     [Confirm] [Reject]│
 └─────────────────────────────────────────────────┘
 ┌──────────────────────────────────────────────────────────┐
 │ Ask anything…                         📎   ( 🎤 )   [Send] │
 └──────────────────────────────────────────────────────────┘
 Voice bar while live:  ● Listening · 2:41 left · 4 turns left   [Interrupt] [End]
 Captions (one line).  States: Connecting / Listening / Thinking / Speaking /
 Limit reached ("Daily voice limit reached – resets 00:00") / Unavailable (mic hidden or tooltip)
```

**Knowledge `/knowledge`**
```
Knowledge                                   [Import]   Search…
 ┌ Title ───────────────┐  card = title, 2-line summary,
 │ Summary…             │  "Created Oct 3 · Updated Oct 20"; click → reader
 └──────────────────────┘  (no hash, URI or parser)
```

**Settings `/settings/*`**
```
Settings
 Account | Exchanges | Notifications | Watcher | Diagnostics | Help   ← sub-nav (left rail on desktop, scroll-chips on mobile)
 Diagnostics:  Providers  [● Market data OK] [● Bybit Amber] [● LLM OK] [Simulated]
               Market & alerts  →  Scanner, Market watcher, Alerts …
               Decisions → … Validation & research → … (one line each)
 Notifications: Telegram on/off · Symbols [BTC×][ZEC×][TAO×][+]
                Strategies [Nested 1-2 ×][+] · Nested stages [N2][N3]
```

## 4. Design tokens + component templates

**Tokens:** keep the token names in `frontend/src/styles/tokens.css` and the Tailwind mapping. Retune the values to the MindPillar table in `docs/alphatrade_workspace_redesign.md`, and keep `tokens.contrast.test.ts` passing:
- Surfaces: bg `#07090f`, surface `#0c1020`, card `#0f1420`, raised `#141a28`
- Borders `#1c2438` / `#111827`
- Text: `#eef2f8` / `#7d8ba0`
- Brand `#5a6aff` for primary actions and focus, link blue `#2563eb`
- Up `#10b981`, down `#ef4444`, amber `#f59e0b`
- PAPER violet and DEMO cyan meanings unchanged
- Type: 11 labels, 13 body/table, 16 section, 24 title, tabular numerals
- Radius: 6 for controls, 10 for cards
- Spacing: 8 inside, 16 between cards, 24 between sections

**Number rules** (`lib/format.ts`):
- Amounts use the `formatAmount(v, "USDT")` form: `50 USDT`, `10,240.5 USDT`, at most 2 decimals and trailing zeros trimmed
- Percent: `0.5%`, using `alreadyPercent` for fields on a 0–100 scale
- Prices use instrument precision
- Signed P&L is coloured
- Missing values render as `—`

**Templates** (existing primitives are reused; new ones are marked as new):

| Template | Base | Spec |
|---|---|---|
| PageHeader | `ui/page-header` | Title, optional one-line description, right-aligned actions. No eyebrow by default. The safety/mode pill stays in the TopBar. |
| Card | `ui/card` | Flat, 1px border, 16px padding, title row (title left, one action right), content. No nested cards. |
| Row (new) | `ui/row.tsx` | One CSS grid `label · value · meta · action` with `items-baseline` and min-height 40. Badges use `self-baseline` and `leading-none`. This fixes the Watcher source-badge misalignment. |
| StatusTile (new) | adapts the unused `ProviderStatusCard` | Name + dot (green / amber / red) + one-word state. Grey "Simulated" tag. No account references. |
| EmptyState | `components/states.tsx` | One line of text + at most one action. No paragraphs. |
| SubNav (new) | `ui/sub-nav.tsx` | `<nav>` of links with `aria-current`. Vertical rail on desktop (≥lg), horizontal scroll chips on mobile. |
| Tabs | `ui/tabs` + new `LinkTabs` | URL-synced (`?tab=`) so tabs are deep-linkable. Replaces the hand-built tabs in the Journal. |
| Disclosure | `<details>` style | "▸ label", closed by default. This is where all secondary detail goes. |

## 5. GPT Live voice inventory (exists vs missing)

**Exists (backend)**
- **Routes** (`api/routes/agent_voice.py`): `GET /agent/voice/config` returns availability, provider, `maximum_seconds` and `maximum_turns`. `WS /agent/voice/live` is the GPT Live relay (`agent_voice/live_socket.py`). `WS /agent/voice/realtime` is the older Realtime path.
- **Design:** a server WebSocket relay holds the OpenAI key. There are no ephemeral tokens, `client_secret` or WebRTC. The browser sends its access token in the first frame.
- **Session** (`live_service.py`): voice delegation runs the same `shared_turn` text-agent pipeline as typed turns, so no separate tool bridge is needed. It waits for `turn.accept` before running a turn. `live_protocol.py` sets model `gpt-live-1`, voice `marin`, PCM 24k, `store: False`, and speech is never treated as confirmation.
- **Limits** (`live_config.py`): 180 s session and audio, 6 turns, at most $1 per session, priced at $0.05/min, plus reasoning and embedding holds. `load_live_limits` refuses unless the flag is on, the key is set, **`provider_mode == "live"`**, the limits receipt is valid and unexpired, and the reasoning model matches.
- **Ledger:** `accounting.py` `VoiceLedger.reserve` checks the org budget, daily request quota, monthly feature, cost and token quotas, and the turn cap. Persistence is the `voice_session_reservations` table, and `reservations_sweep.py` charges expired holds.
- **Flags:** `agent_live_enabled=False`, `agent_live_limits_file=""` (`core/config.py:201`). Example receipt: `docs/gpt_live_limits.example.json`.
- **Tests:** `test_agent_live_*` (4 files), `test_voice_reservations_quota.py`, realtime tests.

**Exists (frontend)**
- `lib/voice/realtime-controller.ts`: states idle / connecting / listening / thinking / speaking / error, captions, interrupt and resume.
- `realtime-audio.ts`: AudioWorklet loaded from a `blob:` URL.
- `RealtimeVoiceControls.tsx`, already mounted in `AgentWorkspace.tsx:1003`, plus the browser-speech `VoiceConversationControls`.
- Unit tests for the controller and audio, and voice cases in `AgentWorkspace.test.tsx`.

**Missing or blocking**
1. **Gate.** The `provider_mode == "live"` requirement conflicts with staging's `fallback`. **Fix (B7a):** a voice-scoped gate on `agent_live_enabled` plus the verified receipt.
2. **Env (Sofien sets it; hard gate, new spend of at most $1 per session):**
   - `AGENT_LIVE_ENABLED`, `AGENT_LIVE_LIMITS_FILE` plus the receipt as a Render secret file
   - `OPENAI_API_KEY` on the API service
   - `AGENT_VOICE_REASONING_MODEL` matching the receipt
   - `AGENT_MAX_OUTPUT_TOKENS` low enough for the $1 ceiling (the default 25000 is refused, per `docs/gpt_live_integration.md`)
   - The frontend origin in `CORS_ORIGINS`

   I will not change any of these values.
3. **CSP.** The production `script-src` has no `blob:` (`security-headers.ts:29`), so the worklet likely fails. **Fix:** serve the worklet as a static file from `/public` (`'self'`), with no CSP loosening.
4. **No availability check before the click.** The button always renders and fails on click. **Fix:** fetch `/agent/voice/config` on mount, then hide the mic or show it disabled with the reason.
5. **The server `reason` is dropped** (`realtime-controller.ts:148`). Add the missing `limit_reached` and `unavailable` states, a countdown and turns left, and human text per reason code.
6. **Two voice UIs** (Talk to Agent + Record). **Fix:** one mic. GPT Live when it is available, browser dictation as the fallback.
7. **Tests:** `agent_live_enabled` is missing from `test_pr249_feature_flag_defaults.py`, and no browser test covers the Live socket path.
8. **Coordination.** Draft PR #314 (xAI Grok, realtime path) touches `agent_voice/` and `config.py`. Per K2, B7a also has to touch `agent_voice/config.py`, so it is sequenced with #314.
9. **Not fixed (out of scope):** there is no explicit concurrent-session cap; the budget holds bound concurrency.

## 6. PR plan (ordered; stop after each PR for Griller + Sofien)

**Every PR:**
- Branch `claude/<track>-<topic>`, claimed on the issue, PR template filled in.
- At most about 400 lines.
- Targeted tests with `Full-Backend: false` while iterating. A PR touching `backend/src` gets the full backend gate (`ruff check`, `ruff format --check`, `pytest` on Postgres) once, on its merge-ready head. Before the first full run I check the #316 baseline.
- Evidence: screenshots from the local preview, what was removed, tests run.
- Squash merge only with Griller GO + green CI + Sofien's OK.
- Hot files (risk, autonomous settings, BloFin, migrations) one PR at a time.
- A Notion Agent Reports entry after each PR (Agent = Other, Repo = AlphaTrade AI), with the full transcript in the page body.
- One `claude/` branch and one PR per item, never a push to `main`.

### Track A — broken features (A1–A5 merge and are checked on staging before Track B)

**A1 `claude/a-resend-verification`** (frontend fix + backend test only; branch from `main`)
- Change: `requestVerifyEmail` in `frontend/src/lib/api/index.ts:251` passes `auth: true`, matching `logout` and `me` next to it. The cause is `client.ts:101`, which defaults `/auth/*` calls to no token, so the backend (`account_service.request_verification`) sees neither a body email nor a tenant.
- New `frontend/src/lib/api/auth.test.ts`, mocking `./client` the same way as `dashboard-demo-account.test.ts`: asserts `apiFetch` is called with `"/auth/verify-email/request"`, `method: "POST"`, `body: "{}"` and `auth: true`.
- `backend/tests/test_account_management.py` gains two tests (no `backend/src` change):
  - An authenticated `json={}` request returns 200, and the mock provider's `sent` has an `email_verification` message to the profile email.
  - An unauthenticated `json={}` request returns 422 "Email is required." This pins the failure the fix avoids.
- Tests run: `npx vitest run src/lib/api/auth.test.ts src/components/account/AccountPages.test.tsx` and `uv run pytest tests/test_account_management.py -q`.
- AC: both tests pass; on staging, Settings → "Resend verification email" shows the success message (the mail itself is the mock).

**A5 `claude/a-number-format`** (frontend)
- Change: add `formatAmount(value, unit)` to `lib/format.ts`, reusing `parseDecimal`/`formatMoney`. Apply it plus `formatPercent(..., {alreadyPercent:true})` in `RiskSettingsSummary`.
- AC: vitest shows `"50.00000000"` → "50 USDT" and `0.5` → "0.5%", and the existing format tests pass.
- Note: risk settings have no currency field. The unit comes from the account (USDT for BloFin demo); I'll verify the paper account's currency.

**A4 `claude/a-owner-reason`** (frontend)
- Change: on `/risk/autonomous` (moves to `/autonomous` in B3), a non-owner sees "Owner only (your role: Trader)"; a null role shows "your role: unknown". The same reason disables Save on bot limits, because PATCH `/autonomous/settings` is `OwnerDep` and currently returns a 403 to non-owners. No client override.
- AC: vitest for owner (enabled), trader (reason text), and null role.
- After deploy, the UI shows Sofien's role, which answers the staging check with no DB access. If it isn't Owner, fixing his membership is a permission change, and I stop for his OK.

**A2 `claude/a-nested-family`** (backend + frontend; full backend gate)
- Write side: `strategy_brain/records.py` adds `family: "nested_continuation"` to the payload.
- Read side: `setup_view` defaults a missing family for Nested detections, so existing rows show correctly **with no data write**.
- Query: setups take optional `family` and `since` params, with the limit applied per family (`service.py:212`).
- Frontend: `/strategies` fetches per family.
- AC: pytest that a Nested row without a family returns `nested_continuation`; pytest that with 25 SFP + 1 Nested the per-family query returns that Nested row; vitest shows a non-zero Nested count.

**A3 `claude/a-trade-caps`** (backend hot files: `autonomous_trading_settings_service.py`, `schemas/autonomous_trading_settings.py`; full backend gate)
- Validation (K-sequencing): only when the request changes the bot `max_trades_per_day`, reject a value above the account cap with a 422 and a human message. Other bot updates never re-validate the cap. The bot always uses min(bot, account).
- Lands before B3a.
- View: add `account_max_trades_per_day` and `effective_max_trades_per_day = min(...)`.
- Not changed: the guard, the ledger and the account-cap save. Effective = min already matches how both caps enforce today.
- UI labels: "Account: max trades/day (all trades)" and "Bot: max trades/day (≤ account)", plus "Effective N".
- AC: pytest rejects bot > account; pytest accepts lowering the account cap below the bot cap, with effective = account; vitest shows both labels and the effective value; the ledger tests pass unchanged.

**A6 `claude/a-blofin-journal-design`** (docs only; runs in parallel with Track B)
- `docs/blofin_demo_trades_journal.md`. Proposed owner is `blofin_demo_manual_mirror.py`; it is the only journal writer and it has the economics. `blofin_activity_service.py` stays the fact store (it doesn't touch the journal).
- The doc must settle:
  1. **Open→closed duplicate risk (K1).** Open rows use `blofin-demo-open:{positionId}:{stamp}` and closed rows `blofin-demo-history:{historyId}`, so one position can get two rows.
     - New keys: closed rows by `historyId`, open rows by `positionId` + open time.
     - The open row is linked to its close.
     - Partial closes are handled.
     - `positionId` reuse is checked.
     - Ghost rows get a gated cleanup: dry-run first, no deletes without Sofien's approval.
  2. **The 7-day window.** It comes from the funding route. Older history like Oct 8 needs a backfill path with funding left null when the venue doesn't return it.
  3. **Fees** come from fills or position history.
  4. **A dry-run diff format.**
  5. **Flag state.** `blofin_demo_manual_mirror_enabled` defaults off. Any staging change is an env gate.
- Time risk: if venue retention is about 30 days (the activity service's fill lookback maximum is 30), the Oct 8 data could age out around Nov 7. **Deadline: the read-only A6 dry-run runs before Nov 1, 2026** (K1).
- AC: Griller GO on the doc.

**A7 `claude/a-blofin-journal-impl`** (backend; may span 2 PRs; full backend gate)
- Implement A6, with the backfill as a script that defaults to `--dry-run`.
- The real backfill runs only after Sofien approves the dry-run diff.
- AC: a rerun produces 0 duplicates (pytest + dry-run); the Oct 8 trade has exit, P&L and fees, and funding only if the venue returns it.

### Track B — navigation and pages (starts after A1–A5 are on staging)

**B1 `claude/b-route-registry`**
- Add `lib/navigation/route-registry.ts` classifying all 72 routes (primary, section, deep-link, diagnostics, redirect, public), and fix the `/risk` double-prefix.
- AC: one test per redirect; a test that every `page.tsx` is in the registry; a test that every redirect target and every nav and advanced link resolves to a real page (no 404).

**B2a `claude/b-settings-subnav`**
- `SubNav` + the new routes `/settings/notifications` and `/settings/watcher` (reusing the existing sections), the hash forwarder, and removal of the link row.
- AC: sub-nav tests (active state, mobile), hash forward tests, and the existing settings tests pass.

**B2b `claude/b-diagnostics`**
- `/settings/diagnostics` generated from the registry (replacing `advanced-routes.ts`) + provider StatusTiles. `/settings/advanced` redirects here.
- AC: every advanced route appears in Diagnostics (a test against the registry); tile colour tests.

**B3a `claude/b-autonomous-page`**
- `/autonomous` with the owner switch, Account limits and Bot limits. The `/risk` and `/risk/autonomous` forms are extracted into components; the kill switch moves here; Risk policy is removed from `/strategies`; redirects are added.
- Requires A3 merged first. Keeps the `TopBar.tsx:197` copy unchanged.
- AC: old routes redirect; the owner-gating and both form tests are kept and pass; the kill switch is reachable; `TopBar.tsx` is not in the diff.

**B3b `claude/b-bot-activity`**
- The "What the bot is doing" panel. First I check whether the existing autonomous decision and watcher runtime reads are enough. If a backend read endpoint is needed, it gets its own PR with pytest and the full gate.
- AC: vitest for running, idle, stale and empty; it shows today's used/effective count.

**B4 `claude/b-strategy-cards`**
- In-card 7-day history (using the A2 query). The standalone list link is removed, `/strategies/detections` redirects to `/strategies`, and the raw JSON is cut.
- AC: vitest for the per-family history and counts; redirect test.

**B5a `claude/b-trades-tabs`**
- Trades / Stats LinkTabs, Knowledge split out to `/knowledge`, and the redirects `/journal/statistics` and `?tab=knowledge`.
- AC: vitest for tabs and redirects.

**B5b `claude/b-new-demo-order`**
- Move `ManualDemoTest` from the Dashboard into a "New demo order" sheet on Trades.
- AC: the manual demo flow tests pass unchanged; the Dashboard no longer has the flow.

**B5c `claude/b-trades-table`** (after A7)
- Table columns: entry, exit, size, leverage, P&L, fees, status, source.
- AC: vitest; BloFin-direct trades are listed.

**B6 `claude/b-sidebar`**
- 7 items, plus the mobile 4 + More layout.
- AC: navigation-config tests, `isNavLinkActive` cases for every primary route plus redirected and deep-link routes.

### Track V — GPT Live voice (own track, K2; separate Griller security review before any merge)

**B7a `claude/v-voice-gate`** (backend; full backend gate)
- Voice admission no longer requires `provider_mode == "live"` in any of `agent_voice/live_config.py`, `agent_voice/config.py:144` or `agent_voice/retrieval.py:39`. It requires the voice flag, the key, the valid receipt and the model match. Every limit is unchanged.
- Add `agent_live_enabled` to the flag-defaults test.
- The retrieval decision (K2) is written up in the PR.
- AC: pytest shows fallback + flag + receipt is admitted, and a missing flag, receipt or key is refused; a pytest proves mock or fallback reasoning and embeddings are refused in a voice session; all existing `test_agent_live_*` and realtime tests pass.

PR #314 (xAI) touches `agent_voice/config.py`. I'll coordinate before B7a so the two don't conflict.

**B7b `claude/v-voice-ui`**
- Fetch the config on mount, one mic, states including limit reached and unavailable, reason-code text, countdown and turns left, and the worklet served from `/public`.
- AC: vitest for every state including limit reached; the existing controller and audio tests pass.

**B7c `claude/v-agent-ui`**
- Proposals as cards with a human status and Confirm/Reject. Remove the "Stored evidence" IDs and JSON, render the strategy draft as readable rules, cut the header copy.
- AC: vitest; the `AgentWorkspace` tests pass; screenshots.

**B7d — staging enablement** (Sofien's action, not a PR)
- Sofien sets the env checklist from §5.2.
- Then I verify on staging: a voice session starts and ends from `/agent`, and the limit-reached state shows. Evidence is screenshots.

### Track C — polish (batched frontend PRs, at most 3 per batch)

- **C1a `claude/c-display-names`** (backend): a `display_name` built as "Nested · BTCUSDT · Long · 15m" for setups, candidates and journal trades. AC: pytest.
- **C1b `claude/c-remove-ids`**: remove the IDs listed in §1.6. Version dropdowns use names. AC: vitest that no UUID or hash pattern renders on the touched pages.
- **C2 `claude/c-telegram-picklists`**: a backend options endpoint maps names to ids (storage stays UUIDs), and the frontend uses multi-select chips. AC: pytest for the mapping; vitest that no free-text identifier inputs remain.
- **C3 `claude/c-row-baseline`**: adopt `Row` on the Watcher slots and settings readouts. AC: screenshots + a vitest for the class contract.
- **C4 `claude/c-copy-cut`**: `EmailVerificationNotice` dev text, header descriptions, knowledge cards showing dates only. AC: vitest; screenshots.

## Next steps (this turn, once approved)

1. On `claude/ui-overhaul-plan`, copy this plan unchanged to `docs/plans/2026-10-26-ui-overhaul-plan.md`, commit with `Full-Backend: false`, and push. That updates PR #333.
2. Branch `claude/a-resend-verification` from `origin/main`. Implement A1 exactly as specified in §6 (three files: `lib/api/index.ts`, the new `lib/api/auth.test.ts`, and `tests/test_account_management.py`). Run the targeted tests, commit with `Full-Backend: false`, push, open a PR using the template, and bind it to this session.
3. File Notion Agent Reports entries for the PR #333 update and the A1 PR.
4. Stop for Griller review of A1.

## Verification

- **Per PR:** targeted vitest (`cd frontend && npx vitest run <files>`) and pytest (`cd backend && uv run pytest <module>`). On the merge-ready head, the full backend gate once.
- **Locally:** the dev server via the preview pane, screenshots at desktop and mobile width, `npm run lint` and `tsc`.
- **After each merged batch:** Sofien checks staging. Production only on his explicit go.
- **Hard gates I stop at:**
  - Env and flag changes (B7d, the mirror flag)
  - The backfill write (A7)
  - Any membership or role change (A4)
  - Deleting any page or file
  - New spend (voice)
  - Anything touching live execution
