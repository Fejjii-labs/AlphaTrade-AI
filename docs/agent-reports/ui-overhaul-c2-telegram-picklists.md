# UI overhaul C2: Telegram pick lists (Track C)

Agent: Claude Code · Date: 2026-10-27 · Status: Done (PR open, not merged)
Plan: `docs/plans/2026-10-26-ui-overhaul-plan.md`, item **C2** (plan §1.6:
"Telegram filters asking for comma-separated UUIDs").
Branch from `main`. Scope: one read-only backend endpoint + the Telegram policy form.

## What changed

- **Backend options endpoint** `GET /notifications/filter-options` (reader,
  own organization and user, read-only). It returns named choices and the
  stored values behind each one:
  - Symbols: the organization's enabled watcher symbols (label = value).
  - Strategies: the caller's own strategies, by name. Each one stands for its
    strategy id plus its 10 newest version ids. Candidate alerts match on the
    version id and legacy paper alerts on the strategy id, so one chip covers
    both. At most 50 strategies.
  - Nested stages: N1, N2, N3, N4+ (stored as `N4_PLUS`), from the existing
    `NestedMaturityStage` enum.
- **Storage is unchanged.** The policy still stores identifier lists;
  `TelegramNotificationPolicyV2` and its validation are not touched.
- **Form** (`TelegramPolicyForm.tsx`): each filter keeps All / None /
  Selected. "Selected" now shows tappable chips (`aria-pressed`), not a
  comma-separated text box.
  - Saved values with no matching choice: symbols and stages show them as
    chips (they are readable). Strategy ids are never shown; they are counted
    as "Other saved (n) ✕", which removes them.
  - Choices unreadable → "Choices unavailable. Saved picks are kept." Saving
    then sends the saved lists unchanged. No fake choices.
  - Empty lists → "No strategies yet." / "No watched symbols yet."
  - The UUID instruction copy is removed. The client-side UUID check is
    removed because values now come from the server's own ids.
- `npm run api:generate` → `openapi.json` / `hashes.json`; `api:check` passes.
- `e2e/notification-settings-v2.spec.ts`: the new read is answered 503 in the
  fixture, like the other unrelated reads there. A real 401 with the
  synthetic token would sign the fixture out.

## Decisions taken without asking

- **The endpoint is `/notifications/filter-options`, not under `/telegram`.**
  `test_worker_notifier` and `test_exchange_blackbox` allow no new API path
  containing "telegram" (no inbound Telegram surface). The first gate run
  caught this. I renamed the route instead of widening that security
  allowlist.

- **One chip per strategy, covering strategy id + recent versions.** Matching
  stays exactly as today. Picking "Nested" subscribes to both Candidate and
  legacy alerts for it. Versions older than the newest 10 are left out to
  stay under the 200-item cap. Alerts come from the selected version, which
  is always among the newest.
- **Only the caller's own strategies** are offered, the same scope as the
  strategy list. A teammate's strategy saved earlier still counts under
  "Other saved".
- **Selected with nothing picked saves as an empty list (= none)**, as the
  empty text box did before.
- **Quiet-hours timezone stays free text.** It is not an identifier and is
  validated on save.

## Acceptance criteria (plan C2)

- [x] Backend options endpoint maps names to ids; storage stays UUIDs.
- [x] pytest for the mapping: labels, value order (strategy id, then newest
      version), stage labels, round trip into the saved policy, own-user
      scoping (teammate and other org excluded), version bound, auth.
- [x] vitest: no free-text identifier inputs remain (only the timezone
      textbox), strategy chip → all ids, unknown ids counted not shown,
      unavailable choices keep saved picks.

## Test evidence

- Backend: `pytest tests/test_telegram_policy_options.py` → 5 passed; with the
  notification and Telegram delivery suites → 56 passed. ruff, ruff format,
  mypy clean.
- Full backend gate (Postgres 16, local, 4 file shards): 6,546 passed. The
  first run had 2 real failures (the "telegram" route allowlist, above). After
  the rename, those two, every test that walks `app.routes` or the OpenAPI
  document, and the C2 and notification suites → **253 passed**. The other 22
  were from the local harness: the shard DB rename is refused by
  `test_execution_account_registration.py` and three migration tests, and
  `test_pr237_migration_aware_rollback` needs history missing from the
  shallow clone. Re-run alone → **25 passed**.
- Frontend: `vitest run src/components/settings src/components/Notification src/lib/api`
  → 19 files, 107 passed. Mutation: showing raw strategy ids
  (`hideUnknown` off) fails the "never shows them" test. eslint and
  `tsc --noEmit` clean. `npm run api:check` passes.

## Screenshots

Production builds of `main` (before) and this branch (after) against this
branch's mock backend. One account, two strategies, a saved policy with one
strategy, BTCUSDT, N2 and N3.

- Desktop: `ui-overhaul-c2-telegram-picklists/before-telegram-policy-1280.png` →
  `ui-overhaul-c2-telegram-picklists/after-telegram-policy-1280.png`
- Phone: `ui-overhaul-c2-telegram-picklists/before-telegram-policy-390.png` →
  `ui-overhaul-c2-telegram-picklists/after-telegram-policy-390.png`
