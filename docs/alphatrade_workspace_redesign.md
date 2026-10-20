# AlphaTrade workspace and conversational capture

Status: implementation available for review; release and authenticated acceptance pending.
Dependency: PR [232](https://github.com/Fejjii/AlphaTrade-AI/pull/232) was merged
on 2026-10-09. Its accepted head `9d44decf31617135a152fc9fc64241ad448207b7` and
main merge `d175613f01646e03e87cec65fd568d1160a0b5c5` are integrated into this
branch. PR233 now targets main. This task has not merged PR233 or deployed anything.

The controlling request is the supplied user prompt. The attached design notes
inform presentation; their recovery, activation and deployment instructions do
not authorize this task to operate a live account or monitoring worker.
Estimated engineering scope after repository inspection: approximately 5–7 days
for shell/pages, persistence/orchestration and representative verification;
authenticated acceptance and dependency review add separate calendar time.

## Presentation guide

1. **Dashboard** opens the configured BloFin demo account. USD account equity,
   available USDT and native open positions cover the entire account, including
   external exposure. Verified net PnL is explicitly partial; win rate remains
   unavailable because the inherited contract lacks complete outcome coverage.
   Expand performance coverage to see gross PnL, signed fees, funding and sample
   limitations. Recent AlphaTrade activity links to exact Journal records.
   Manual demo preparation/history, a small Daily Review and one attention area
   follow. Internal simulator statistics require an explicit account selection.
2. **Agent** uses a wide conversation and one composer. Enter a message, attach
   a supported document or record speech. Review/edit a transcript, add it to
   the message and Send. Recording shows elapsed time and cancellation controls.
   History collapses; mobile history is a focus-managed drawer. Stored evidence,
   sources and model usage expand on demand. Ordinary chat and capture continue
   while execution is paused. A durable receipt offers Open, Reclassify and Undo.
3. **Journal & Knowledge** has two tabs. Journal shows execution-backed BloFin
   activity and independent personal reflections. Knowledge shows Rules,
   Strategies, News & Analysis and Lessons, alongside preserved source documents.
   Search, categories and paging, including original document text pages, live in the URL. Open a note or exact trade;
   Back restores the list's filters and scroll. Note correction checks the
   current revision. Original content and structured drafts remain expandable.
4. **Strategies** starts with Nested Continuation and SFP family cards.
   Directional configurations and revisions open in details. Recent detections
   default to seven days, with preserved history accessible separately. Captured
   strategy drafts have their own reviewable records and revision numbers.
   They do not change canonical approval, validation or automation status.
   Risk Policy opens the existing centrally enforced account settings; strategy
   limits remain bounded by that policy.
5. **Settings** has exactly three expandable groups: Market Monitoring,
   Notifications and Account & System. Existing watchlist and Telegram preferences
   retain their supported controls. Profile, exchange identity, provider status,
   Diagnostics and Help belong in Account & System.

The shared header has one verified mode label, a separate pause label, the
existing confirmation control and a secondary Status dropdown. “No global pause”
does not assert that execution is ready. Unknown state stays unknown. The pause
dialog is portalled to the document body, bounded to the viewport and retains
focus trapping, Escape, reason and confirmation behavior. Successful pause
mutations refresh dependent health and Watcher snapshots. No runtime flags changed.

Screenshots and reproduction: [workspace screenshot index](screenshots/workspace-redesign/README.md).
All shown balances, trades, receipts and model responses are synthetic fixtures.

## Data boundaries

The PR232 native account service, lifecycle reconciliation, accounting, fills,
holds, manual command behavior and `useDemoAccountSnapshot` polling hook are
unchanged. The default Journal/recent-activity presentation requires BloFin,
`manual_demo_test` or `paper_execution`, a projector-owned execution lifecycle
link, entry time and a non-planned/non-cancelled status. A manually claimed venue
and source alone cannot qualify. Connectivity tests are labelled and excluded
from strategy statistics. External account exposure remains visible in native
positions; unsupported external attribution is never invented.

Historical records remain available through the preserved archive, source paging
and exact links. Detection history is not deleted. Daily Review retains its
existing stored-application-record scope; it is not presented as a complete native
BloFin outcome report. Equity changes never establish realized PnL. Missing fees,
funding, conversions and outcome coverage stay unavailable. There is no fabricated
week/month performance series or win-rate calculation from incomplete data.

## Application Agent model

The Codex coding model and AlphaTrade's runtime Agent are separate. No additional
coding agents were started. Runtime prose and capture both default to the same
frontier model, using the existing OpenAI Responses provider and typed router:

```dotenv
AGENT_REASONING_MODEL=gpt-6-astra
AGENT_REASONING_EFFORT=high
AGENT_MAX_OUTPUT_TOKENS=25000
```

The identifier, Responses support and structured output support were checked in
official provider documentation on 2026-10-09:
[latest reasoning model guide](https://developers.openai.com/api/docs/guides/latest-model)
and [GPT-6 Astra model reference](https://developers.openai.com/api/docs/models/gpt-6-astra).
Model availability for this account was **not** verified: no authorized provider
credential or model session is available here. No access was purchased or
subscription changed. Configure an authorized server-side `OPENAI_API_KEY`,
provider mode and base URL through the existing deployment process; secrets never
enter frontend configuration. Default local `PROVIDER_MODE=mock` deliberately
produces unavailable Agent reasoning rather than plausible mocked answers.

Both runtime calls use high reasoning and a configurable output budget. Capture
requires strict structured output; schemas reject additional executable fields.
The override affects this Agent's prose/capture, not the existing tier defaults
for other callers. Both tiers point to the same selected model and fail closed.
Provider errors, unavailable results and mock/fallback output cannot become a
saved summary or substitute analysis. Deterministic stored trade evidence can
still be displayed with its explicit reasoning-unavailable limitation.

An ordinary contribution can make two sequential model calls: conversational
response and classification/capture. Strong reasoning adds latency and cost;
no cheap classifier is substituted. Official standard rates at inspection were
$10 per million input tokens, $50 per million output tokens and $1 per million
cached input tokens. For illustration, one uncached call using 4,000 input and
1,000 billable output tokens costs about $0.09; reasoning tokens and the second
call can increase that total. These are documentation-based illustrations, not
measured bills. Per-call token counts, latency, fallback and cost provenance are
retained in receipt evidence. Unknown pricing in the existing cost catalog stays
`unavailable`/null, not zero. A live account benchmark and billing reconciliation
remain pending.

The browser allows 180 seconds, retains the draft on failure and does not
automatically retry a POST. A disconnected client cannot cancel an already
committing server transaction; inspect retained conversation history before a
manual retry. Content deduplication protects saved entries across retries and
conversations. Duplicate transcript messages are retained as actual events.

## Persistence and API contracts

An additive migration `a8agentcapture001` follows PR232's `a7manualrecovery001`.
It creates only `agent_saved_entries` and `agent_capture_sources`. It has not been
applied to a deployed database. Disposable PostgreSQL17 migration and capture
concurrency checks passed; deployment migration and live acceptance remain pending.

`POST /agent/turns` accepts an optional owner-scoped `source_document_id` after
existing Knowledge preview/import. Uploads cannot carry an explicit tool action.
The original source stays in the conversation/document store. Original revision
content and source message IDs are preserved. Capture suggestions use recent
user turns and scoped existing entries, with relevant older candidates retrieved
from private history. They can split mixed topics or combine related contributions
into a revision. Each summary requires source-matching verbatim quotes, current
contribution support and confidence of at least 0.8; ambiguous capture asks one
targeted question. These structural controls do not prove semantic faithfulness
of an untested live model.

Persistence runs inside a savepoint and deduplicates a whitespace-normalized
content hash per organization/user. A PostgreSQL `FOR NO KEY UPDATE` user-row
lock serializes private capture while allowing unrelated foreign-key references.
Concurrent duplicate capture produces one entry; competing corrections reject a
stale revision. These behaviors were verified with separate PostgreSQL sessions. The outer API transaction commits before returning Saved. If
capture fails, the source conversation remains committed and the response says
capture failed. Owner-private endpoints provide:

- `GET /agent/saved` — category/view/search and offset paging;
- `GET /agent/saved/{id}` — exact original, summary and draft;
- `PATCH /agent/saved/{id}` — revision-checked correction/reclassification/Undo;
- `POST /agent/saved/retry` — retry the retained original message/document.

Undo restores the preceding revision, or hides a newly created entry while
retaining it and its deduplication record. Replayed receipts read canonical entry
state, so a correction or Undo survives reload. Successful retry updates the
stored failed receipt. Exact trade links require an explicit UUID and a
deterministic organization/user-scoped Journal lookup; the model cannot invent
an execution identity. Uploaded content and saved notes remain user-supplied,
unverified reference data with no tool authority.

Retrieval filters the entire private saved history for token overlap before
bounding candidates to 200 and ranking the top eight. It precedes ordinary
conversational synthesis and returns linked source notes. It is lexical retrieval,
not a newly claimed semantic index; multilingual recall, implicit pronoun references
and larger retrieval quality need live evaluation. Documents over 100,000 readable
characters are refused with a split-source message; their originals remain saved.

Structured strategy drafts preserve family, market, direction, timeframe,
entry/exit/invalidation rules and missing fields. A saved draft/revision cannot
approve a strategy, activate automation, change risk limits or write an order.
Existing typed actions retain their permission, confirmation, idempotency and
execution gates. Capture never writes JournalTrade, Order or UserStrategyVersion.

## Verification results

These are development checks on this branch, not full backend release acceptance.

| Check | Result | Scope / limitation |
| --- | --- | --- |
| Frontend unit suite | 1,420 passed, 231 files | Actual UI/API contract assertions; mocked network sources |
| Frontend lint/typecheck | Passed | No ESLint warnings/errors |
| Ordinary production build | Passed in CI run 814 on `0dcd847`; local font fetch unavailable | Final published-ref CI is recorded in PR233; deployed font delivery unverified |
| Production build with existing test-only offline font fixture | Passed | Compilation, types and route generation; never use that fixture for deployment |
| Focused backend regression selection with PostgreSQL17 | 228 passed, no skips | Capture, typed actions, continuity, provider/routing, migrations and PR232 review regressions; fixture reasoning |
| Deployment safety selection | 108 passed | Unchanged safety/config/script/Watcher controls; migration expectations advanced |
| PostgreSQL migration/dependency selection | 26 passed, included in the 228 | Previously skipped database cases and three inherited PR232 regressions; ancestry/data preservation retained |
| New capture contract evaluations | 16 passed within the selection | 14 representative model-output/HTTP cases plus two PostgreSQL concurrency cases; no live model quality claim |
| Ruff + format | Passed, 1,157 files | Entire backend static formatting/lint selection |
| Full mypy compared with PR232 baseline | 495 existing errors in 100 files; zero introduced diagnostics | Compared error multiset after line/literal-order normalization; not a clean mypy gate |
| Agent / RAG / narrative guardrail runners | 16/16, 5/5, 7/7 | Existing deterministic/mock evaluation suites |
| Full Chromium API/UI smoke | 47 passed, 13 opt-in staging skips, no retries | Desktop, portrait and landscape; local shared SQLite fixture uses one worker; live staging remains pending |
| Exact legacy document/filter paths | Four latest cases passed, no retries | Missing exact source is explicit; changing filters clears both URL aliases |

The new capture evaluations map to the requested scenarios:

| Scenario | Representative evidence |
| --- | --- |
| Multi-turn strategy and retained context | Related contributions produce one structured draft with a second revision and source IDs |
| Mixed/ambiguous content | Reflection and opinion split; ambiguous intent yields one question with no save |
| Faithful summary / destination | Exact quote validation, categories, originals; invented quotation rolls back all saves |
| Cross-conversation retrieval | Scoped source recall, including an older note after 205 unrelated additions |
| Unsupported questions / uncertainty | Empty unrelated retrieval and retained unverified/uncertain claims; live semantic response remains unverified |
| Duplicates / failures / corrections / Undo | Durable dedupe, optimistic revision refusal, restore/hide and HTTP failure→retry→replay |
| Upload injection / unauthorized arguments | Untrusted reference only; extra executable fields and invented targets rejected |
| Tenant / execution isolation | Same-org other user and other organization denied; no execution-table writes |
| Provider / fallback / usage | Mock provider unavailable; strict model/effort/schema wiring and token/latency provenance |
| Additive migration | SQLite roundtrip preserves sentinel evidence; actual PostgreSQL Alembic chain preserves prior plan/native contracts |
| Concurrent save / corrections | Separate PostgreSQL sessions dedupe to one entry, allow unrelated user references and refuse stale revisions |

Reproduction from the repository root:

```sh
cd backend
# Use a disposable PostgreSQL database for this focused selection.
export AT028_POSTGRES_URL=postgresql+psycopg://alphatrade:alphatrade@127.0.0.1:5432/alphatrade_test
PYTHONPATH=src:. .venv/bin/pytest -p tests.test_interactive_agent_foundation \
  tests/test_agent_capture.py tests/test_agent_capture_postgres.py \
  tests/test_interactive_agent_foundation.py \
  tests/test_agent_action_orchestration.py tests/test_agent_action_application.py \
  tests/test_agent_conversation_continuity.py tests/test_knowledge_file_migration.py \
  tests/test_release_wave002_migrations.py tests/test_manual_demo_migration.py \
  tests/test_phase2_model_router.py tests/test_openai_llm_responses.py \
  tests/test_pr232_review_regressions.py tests/test_phase8_learning_persistence.py \
  tests/test_journal_trades_alembic_empty_tenant.py tests/test_phase2_4_alembic_postgres.py \
  -q -o addopts=''
PYTHONPATH=src:. .venv/bin/pytest tests/test_deployment_safety.py \
  tests/test_deployment_scripts.py tests/test_config.py tests/test_watcher_paper_activation.py -q -o addopts=''
.venv/bin/ruff check .
.venv/bin/ruff format --check .
cd ../frontend
npm ci
npm run lint
npm run typecheck
npm run test
npm run build
# Restricted-network build verification only:
NEXT_FONT_GOOGLE_MOCKED_RESPONSES="$PWD/test-fixtures/offline-fonts.cjs" npm run build
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium npm run test:e2e -- --retries=0
cd ..
backend/.venv/bin/python evaluation/evaluate_agent.py
backend/.venv/bin/python evaluation/evaluate_rag.py
backend/.venv/bin/python evaluation/evaluate_guardrails.py
```

Ordinary CI exposed stale navigation/head expectations and two async unit-test
races; the checks now await rendered native/document state and use the current
five destinations. Trading, authentication, ancestry and data-preservation
assertions remain. Browser smoke also found legacy document and Settings anchors;
these now resolve the exact source/group and preserve filter behavior.

A two-worker local smoke run passed 46 cases but encountered a SQLite write lock
between Agent source import and the Journal API flow. The shared local fixture
now defaults to one worker; the complete serial run passed 47/skipped 13 without
retries. External-server runs keep normal worker selection. No application DB,
authentication limit or runtime flag was changed. Actual PostgreSQL concurrency
is covered separately rather than inferred from serial SQLite checks.

The final228-case PostgreSQL selection passed with no skips. PostgreSQL17 was
extracted under `/tmp`, bound to loopback, and used only with synthetic disposable
data; no system installation or external database was changed. CI uses PostgreSQL16.
The new concurrency fixture initially omitted a required strict-schema field;
that test setup was corrected before the clean228-case run. Ordinary PR CI on
the final published head is recorded in PR233 and remains distinct from the
complete supervised release gate.

The pytest plugin explicitly registers the shared legacy `agent_db` fixture used
by the action selections. In this cloud environment, set `UV_CACHE_DIR` to a
writable directory; loopback/API/browser checks also need normal sandbox network
permission. Neither adjustment changes application behavior.

## Pending acceptance and handoff

1. Review PR233 against accepted PR232/main. The accepted head and main merge are
   integrated; subsequent base changes still require affected checks.
2. Review the additive migration for the deployed PostgreSQL version and rollout.
   Disposable migration/database and concurrent capture/revision checks passed.
3. With authorized model access, run the nine scenarios against actual reasoning
   output, score faithfulness/destination/uncertainty/recall, capture two-call
   latency/token/billing distributions, and verify account-specific model access.
4. Perform authenticated native demo checks and physical iPhone/Safari voice,
   permissions, dialogs and return paths. No credentials or native session were
   available here. Mock screenshots do not prove these checks.
5. After supervising review and consolidated staging verification, run the **one
   complete exact-ref release gate** prescribed by `.ai/RELEASE.md`, including
   `gh workflow run ci.yml --ref <reviewed-release-ref> -f full_backend=true`.
   It is deliberately not dispatched during implementation. Ordinary PR CI is
   focused and does not replace the complete gate.
6. Verify deployed font delivery and the Mac/iCloud handoff mirror.
   GitHub publication is available; local Mac mirror/hash confirmation is not.

No deployment, monitoring activation, safety reset, risk policy change or native
exchange mutation was performed. Runtime flags and centralized enforcement are
unchanged. See root `HANDOFF.md` for the exact review instruction.

## Design direction: MindPillar-inspired (phases 3-9)

Remaining redesign phases (3-9, on top of PRs 276/277) take their look and feel
from MindPillar (https://mindpillar.com, app CSS `/assets/index-B6CAGr97.css`,
captured 2026-10-19). **Inspired by, not a copy:** no MindPillar logo, copy,
illustrations or chart widgets. Keep AlphaTrade's semantic colour rules
(violet = PAPER, cyan = DEMO, green/red = PnL) and the existing token *names* in
`frontend/src/styles/tokens.css`; retune values toward the targets below.

### Observed MindPillar tokens (dark theme is the default)
| Role | Dark | Light | Source |
|---|---|---|---|
| Background | `#07090f` | `#eceef4` | `--bg`, `.dark{--background}` |
| Surface / muted | `#0c1020` | `#fff` | `--surface`, `--muted` |
| Card | `#0f1420` | `#fff` | `--card` |
| Secondary / accent fill | `#141a28` | `#e8eaf2` | `--secondary`, `--accent` |
| Border | `#1c2438` | `#c8cdde` | `--border` |
| Subtle border | `#111827` | `#dde0eb` | `--bsub` |
| Text 1 / 2 / 3 | `#eef2f8` / `#7d8ba0` / `#3a4558` | `#0a0e1a` / `#374151` / `#6b7280` | `--t1..t3` |
| Primary (brand violet-blue) | `#5a6aff` | — | `--primary`, Webflow `primary-500` |
| Interactive blue | `#2563eb` (soft `#2563eb1f`) | `#1d4ed8` | `--blue`, `--bsoft` |
| Highlight cyan | `#47d7ff` | — | `--blue-accent` |
| Orange (eyebrow) | `#f97316` | — | `--secondary-500` |
| Up / down | `#10b981` / `#ef4444` (also `#f36`) | `#047857` / `#b91c1c` | `--green`, `--red`, `--mp-down` |
| Amber | `#f59e0b` | `#b45309` | `--mp-amber` |
| Dark violet (deep brand) | `#020042` | — | `--mp-dark-violet` |

- **Font:** Figtree (variable), used for both UI and numbers (`--font-sans`, `--font-mono` both Figtree). Marketing site also uses Inter / Inter Tight headlines.
- **Radius:** base `--radius: .625rem` (10px); most-used values 6px, 8px, 4px, 10px, 12px; pills 50%.
- **Density:** compact/data-dense. Most frequent font sizes 11/13/12/10/14px; gaps 8/6/10/12/4/16px; 4px spacing unit (`--spacing: .25rem`).
- **Layout:** content max-widths 1200–1280px app, 720–880px reading columns. Subtle glass on overlays (`backdrop-filter: blur(3-10px)`).
- **Aesthetic:** near-black navy, flat cards separated by thin borders rather than shadows, one violet-blue brand accent, small type, calm "terminal" feel.
- **Not observed:** nav structure from CSS alone (rendered nav not inspected), shadow scale, motion specs.

### Rules for phases 3-9
1. Dark is default; light theme uses the light column.
2. Retune `tokens.css` values toward the table (background `#07090f`, surfaces `#0c1020`/`#0f1420`, border `#1c2438`, text `#eef2f8`/`#7d8ba0`); keep variable names; keep AA contrast (`tokens.contrast.test.ts` must pass — `--t3 #3a4558` is decorative only, never body text).
3. Brand accent `#5a6aff` for primary actions/focus; blue `#2563eb` stays for links/interactive; orange only as eyebrow.
4. Switch the UI font to Figtree (self-hosted `@fontsource-variable/figtree`), tabular numerals for prices/PnL.
5. Dense by default: 13px body in tables/panels, 11–12px labels, 8px gaps, 10px card radius, 6px controls.
6. Flat cards with 1px borders; no heavy shadows; blur only on dialogs/sticky headers.
7. Safety badges (PAPER/DEMO/kill-switch/pause) keep their existing meaning and stay the most prominent elements.

### UI redesign phase map (3–9)

| Phase | Scope |
| --- | --- |
| **3 — Shell** | Top bar, sidebar, mobile tabs, and main content frame. Safety strip (mode, pause, kill switch) stays the most prominent chrome. Glass blur on sticky header and menus only. |
| **4 — Dashboard** | Presentation guide §1: demo account card, metrics, attention queue, Daily Review. `PageHeader`, dense sections, phase 2 primitives where they clarify scan density. |
| **5 — Agent** | Presentation guide §2: conversation layout, composer, history. MindPillar density; no change to capture or voice authority. |
| **6 — Journal & Knowledge** | Presentation guide §3: tabs, lists, knowledge filters. `PageHeader` and flat bordered panels. |
| **7 — Strategies** | Presentation guide §4: family cards, detections, drafts. `PageHeader` and library cards. |
| **8 — Settings** | Presentation guide §5: three expandable groups. `PageHeader` and existing anchors unchanged. |
| **9 — Tokens & typography** | Retune `tokens.css` toward MindPillar targets, self-hosted Figtree (UI + tabular numerals), 10px card radius, 1280px content max, WCAG AA contrast tests. |
