# UI overhaul C1a: Display names (Track C)

Agent: Claude Code · Date: 2026-10-27 · Status: Done (PR open, not merged)
Plan: `docs/plans/2026-10-26-ui-overhaul-plan.md`, item **C1a**.
Branch from `main`. Scope: backend read models + regenerated API contract.

## What changed

- **One formatter** (`backend/src/app/services/display_names.py`):
  `display_name(label, symbol, direction, timeframe)` →
  "Nested · BTCUSDT · Long · 15m". Missing parts are left out, never replaced
  with placeholders. No ids or hashes go in. `family_label` turns
  `nested_continuation` into "Nested" and `sfp` into "SFP".
- **Setups** (`strategy_brain/service.py::setup_view`): every stored setup in
  `GET /strategy-brain/overview` and `GET /strategy-brain/setups/{id}` now
  carries `display_name`, e.g. "Nested · BTCUSDT · Long · 15m".
- **Candidates** (`CanonicalCandidateRead`): `GET /canonical/candidates` and
  `/canonical/candidates/{id}` carry `display_name`, using the strategy's
  own name, e.g. "Phase 7 compiled setup strategy · BTCUSDT · Short · 15m".
  The names for a page are read in one query, scoped to the organization.
  The symbol is the instrument's base + quote asset.
- **Journal trades** (`JournalTradeRead`): a computed `display_name` from the
  strategy label, symbol, direction and timeframe, e.g.
  "Nested · BTCUSDT · Long · 15m", or "BTCUSDT · Short · 1h" with no strategy.
  It reaches every endpoint that returns `JournalTradeRead`.
- `npm run api:generate` → `openapi.json` / `hashes.json` updated;
  `npm run api:check` passes.

## Decisions taken without asking

- **Additive only.** Nothing is renamed or removed; ids stay in the payload
  for links. The UI can switch labels to `display_name` page by page.
- **Journal trades use a computed field**, so every existing endpoint and
  every code path that builds `JournalTradeRead` gets the name without
  touching each route. It is `readOnly` in the contract.
- **Candidates read the strategy name, not a family.** A canonical
  Candidate has no family field; the strategy name is what the trader chose.
  If the name cannot be found (for example, another organization's version),
  the name is just "BTCUSDT · Short · 15m". It is never another tenant's
  name.
- **Frontend adoption is not in this PR.** The plan scopes C1a to the
  backend. The pages that still build labels by hand can use
  `display_name` in a later Track C batch.
- **Setup endpoints return untyped dicts** today, so the setup name does not
  show in `openapi.json`; the field is covered by tests instead.

## Acceptance criteria (plan C1a)

- [x] `display_name` built as "Nested · BTCUSDT · Long · 15m" for setups,
      candidates and journal trades.
- [x] pytest: formatter, family labels, setup view (Nested, SFP, missing
      direction), journal trade serialization, published contract, and the
      canonical HTTP flow asserting the candidate name.
- [x] No UUID or hash pattern in any display name (asserted).
- [x] `npm run api:check` passes.

## Test evidence

From `backend/`:

- `uv run pytest tests/test_display_names.py` → 9 passed.
- The 60 existing test files that touch these reads (journal trades, Strategy
  Brain, canonical candidates, OpenAPI) → all pass. 1,185 passed in the first
  run. 195 Postgres tests in 11 files errored there because a concurrent gate
  dropped the shared template DB; re-run alone → 195 passed.
- `test_phase8_canonical_workflow` now asserts the candidate name
  "Phase 7 compiled setup strategy · BTCUSDT · Short · 15m".
- ruff, ruff format, mypy (touched files) clean. `npm run api:check` passes;
  frontend `tsc --noEmit` clean; `vitest run src/lib/api` → 54 passed.
- Full backend gate (Postgres 16, local, 4 file shards): **6,552 passed**. 22
  failed or errored, all from the local harness. The shard DB rename is refused
  by `test_execution_account_registration.py` and three migration tests, and
  `test_pr237_migration_aware_rollback` needs history missing from the shallow
  clone. Re-run alone → **25 passed**.

## Screenshots

Backend only; no page changes. Frontend adoption of `display_name` is a later
Track C batch.
