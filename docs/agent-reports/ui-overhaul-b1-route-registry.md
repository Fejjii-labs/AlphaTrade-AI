# UI overhaul B1: route registry

Agent: Claude Code · Date: 2026-10-27 · Status: Done (PR open, not merged)
Plan: `docs/plans/2026-10-26-ui-overhaul-plan.md` (PR #333, approved), item **B1**.
Scope: frontend only. No backend, API schema, workflow or lockfile changes.

## What changed

- New `frontend/src/lib/navigation/route-registry.ts`. It classifies all 72
  `page.tsx` routes as `primary`, `section`, `deep-link`, `diagnostics`,
  `redirect` or `public`. Diagnostics routes carry the plan's §2 groups
  (Market & alerts, Decisions, Validation & research, Journal tools,
  Portfolio, System). The later slices (B2b Diagnostics, B6 sidebar) read from it.
- `ROUTE_REDIRECTS` joins the `next.config` redirects (`PHASE_B_REDIRECTS`)
  with the three page-level `redirect()` pages (`/dashboard`, `/coaching`,
  `/journal/statistics`).
- `resolveRoute(path)` follows redirects the way a browser does. It returns the
  page that renders, or `null` for a 404 or a loop.
- `/risk` double prefix: `/risk` was in both the Strategies prefixes and
  `SETTINGS_ROUTE_PREFIXES`. Strategies won by rule order. It is now only a
  Strategies prefix. Nothing visible changes. B3a moves it to Autonomous.

## Decisions taken without asking

- **Classify by today's pages, not the target sitemap.** `/autonomous`,
  `/settings/notifications`, `/settings/watcher` and `/settings/diagnostics`
  don't exist yet. Each later slice updates the registry when it adds or
  redirects a route. The test fails if a slice forgets.
- **`/knowledge` is `primary`.** It is its own sidebar item in the plan (§2,
  item 6), and it already renders on its own route.
- **`/strategy-lab`, `/workspace`, `/watcher` are diagnostics**, as in plan §2.
  `/watchlist` and `/settings/advanced` stay `section` until B2a/B2b redirect them.
- **`advanced-routes.ts` is unchanged** apart from the `/risk` prefix. Plan B2b
  replaces it with the registry. Doing it here would change the Advanced page.
- **No screenshots.** Nothing visible changes in this slice (registry, tests
  and one prefix list entry with no effect on which item is active).

## Acceptance criteria (plan B1)

- [x] One test per redirect: each of the 11 redirects lands on a real page.
      Each config redirect is in `next.config`'s `redirects()`. Each
      page-level redirect calls `redirect()` with the registered target.
- [x] A test that every `page.tsx` is in the registry, and that the registry
      has nothing else.
- [x] A test that every redirect target, nav link (primary + secondary) and
      Advanced link resolves to a real page (no 404).
- [x] `/risk` belongs to one destination.

## Test evidence

From `frontend/`, after `npm ci` (lockfile unchanged):

- `npx vitest run src/lib/navigation src/components/layout` → 8 files, 148
  tests passed before the dedupe of nav links; after it,
  `npx vitest run src/lib/navigation` → 72 tests passed.
- Mutation check: commenting out the `/lessons` entry made 2 tests fail
  (page list and `/lessons` link), as expected. Restored.
- `npx eslint src/lib/navigation src/components/layout/advanced-routes.ts` → clean.
- `npx tsc --noEmit` → clean.
- Backend: not touched, not run (`Full-Backend: false`).

## Next steps

Griller review on the PR head. Then B2a (Settings sub-nav).
