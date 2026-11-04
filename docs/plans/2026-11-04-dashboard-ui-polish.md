# Dashboard UI polish — finish the TradeZella-inspired performance section

Branch: `claude/ui-polish-dashboard-finish` (from `main` @ `b0b89e8e`). Local commits only.
Copy of `/workspace/at-ui-polish-plan.md`.

## Where the previous work stopped (evidence)

- PR #425 (`9bcd6619`, squash of `claude/dashboard-tradezella`, merged 2026-11-04 09:18 CET)
  shipped `frontend/src/components/dashboard/performance/*` and
  `GET /journal/statistics/calendar`.
- The original report `/workspace/dtz_report.md` lists 8 gaps. The squash message shows that
  review rounds (B2, C1–C5) already closed some of them before merge:
  - Gap 4 (truncation keeps oldest rows): **closed** by `902e8fef` (newest 5000, `oldest_covered_date`).
  - Gap 6 (filters UI): **partly closed**. `6e6c17d7` added a Human/System filter, then
    `6a62d148` (B2) **replaced** it with an explicit Demo/Paper/Real scope plus an "Other"
    select (backtest/imported/system), because `execution_actor` misclassified autonomous
    `paper_execution` rows as human. The route no longer accepts `execution_actor`.
    Still missing: the `source` narrowing (backend accepts it, UI doesn't expose it) and a
    custom date range (range is preset-only: 30D/90D/YTD/All).
  - Gaps 1–3 (PR size, plan/claim, Notion) were process items; 5 (score methodology) and 7
    (timezone default) are product decisions; 8 (tooltip tests) is a jsdom limitation.
- Visual state in the merged code (read from source):
  - KPI cards are monochrome surface-1 tiles; only the value text is coloured. No loading
    skeleton: while loading they show `—` and "No data", which reads the same as an empty journal.
  - Only one win-rate gauge (trade win %). The response already has `risk.winning_days` /
    `losing_days`, so a day win % gauge needs no backend change.
  - Radar uses the accent colour only; the overall score number is not toned by band.
  - Calendar has no "today" marker, no colour legend, and no skeleton (just "…" in the header).
  - Radar and curve show "Not enough closed trades" / "No closed trades" while still loading.
  - The top-level error state has no Retry button; the calendar error has no Retry either.
  - Light theme is defined in `tokens.css` but unreachable in the app (no toggle);
    PR #425 checked it once by forcing `data-theme`.
- No `docs/plans/*dashboard*` plan exists (gap 2), and the other UI overhaul branches
  (`claude/ui-overhaul-*`, B1–B7, C1–C2) are about navigation, settings, agent and journal,
  not this section.

## Scope (frontend only, no backend change planned)

Files: `frontend/src/components/dashboard/performance/*` only (plus tests there).
No backend, risk, trading, BloFin or autonomous-settings files. No new API fields.

## Plan — ordered commits, each ≤ ~400 changed lines

1. `docs:` this plan.
2. `feat(dashboard): toned KPI cards, day win % gauge, KPI skeletons`
   - Each KPI card gets a coloured top accent and tinted background driven by the value's
     sign/band (profit / loss / neutral), with an icon tile. Colour is never the only signal
     (signs and labels stay).
   - New "Day win %" card with a gauge from `risk.winning_days/losing_days`.
   - Win-rate gauges get `role="meter"` with an accessible value text.
   - Loading → skeleton cards (`aria-busy`), not dashes.
3. `feat(dashboard): source narrowing and custom date range filters`
   - "Source" select built from `filters.included_sources` (shown only if the scope reads
     more than one source); resets when the scope changes.
   - "Custom" range option with two `<input type="date">` fields; invalid ranges
     (from > to) are flagged inline and not sent.
   - "Reset filters" button when anything differs from the defaults.
   - `execution_actor` is **not** re-added (see the B2 decision above).
4. `feat(dashboard): calendar/radar/curve polish and loading/error states`
   - Calendar: today outline, legend, skeleton grid while loading, Retry on error.
   - Radar: overall score toned by band (≥70 good, 40–69 fair, <40 weak), with a band word.
   - Curve: header value toned, skeleton while loading, explicit range label.
   - Section error: Retry button (re-runs both requests).
5. Visual check at 1440 dark / 1440 light / 390 dark using a temporary, uncommitted
   sample-data route and headless Chrome. Fix what it shows.

## Tests (targeted only)
- `npx vitest run src/components/dashboard/performance src/components/dashboard/TraderDashboardView.test.tsx`
- `npx tsc --noEmit`
- `npx eslint src/components/dashboard/performance`
- No backend files touched, so no pytest/ruff/mypy is needed. Commits carry `Full-Backend: false`.

## Process deviations
- AGENTS.md asks for a draft plan PR plus Griller GO before code. This task forbids pushing and
  opening PRs and asks for the work to be completed, so the plan is committed locally and the
  code follows on the same branch. Sofien/Griller can review the plan commit first.
