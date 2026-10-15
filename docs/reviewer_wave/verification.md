# Reviewer wave 1 development verification

Baseline `b165b92276346f0e0fe3ccdbd2bec3443dc75d40`; isolated frontend branch.
This is local development verification, not the consolidated release gate or live acceptance.
No merge/deploy, release workflow dispatch, credentials or real trading activation.

## Commands and results

Run from `frontend/`, unless marked backend. Locked npm/uv environments are installed.

| Command | Result |
| --- | --- |
| `npm run test` | 231 files, 1472 cases passed, no failures (98.72s) |
| `npm exec vitest run -- src/components/agent/AgentWorkspace.test.tsx src/components/agent/acknowledged-turn.test.ts` | Final full-reply retention change and added unmount check: 2 files, 33 cases passed (4.60s); do not add overlapping counts to the full suite |
| `npm run lint` | No ESLint warnings/errors; existing Next lint deprecation notice |
| `npm run typecheck` | Passed |
| `npm run api:generate` then `npm run api:check` | Deterministic artifacts/drift passed; schema SHA in contract manifest |
| `NEXT_FONT_GOOGLE_MOCKED_RESPONSES="$PWD/test-fixtures/offline-fonts.cjs" npm run build` | Production build passed, 61 prerendered pages |
| `npm exec playwright test -- --config=playwright.reviewer.config.ts --reporter=list,json` | 7 routed production Chromium cases passed, zero skips/retries |
| `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 npm exec playwright test -- --config=playwright.reviewer-api.config.ts --reporter=list` | 1 real HTTP/PostgreSQL persistence case passed (7.4s including startup) |
| Backend: `UV_CACHE_DIR=/tmp/reviewer-uv-cache uv run pytest tests/test_openapi_export.py -q` | 1 export-only case passed (14.60s), one existing Starlette dependency deprecation warning |
| Backend: `UV_CACHE_DIR=/tmp/reviewer-uv-cache uv run ruff check scripts/export_openapi.py tests/test_openapi_export.py` | Passed |
| Backend: `UV_CACHE_DIR=/tmp/reviewer-uv-cache uv run ruff format --check scripts/export_openapi.py tests/test_openapi_export.py` | Passed |
| `git diff --check` | Passed |

The full frontend run preceded the final full-reply guard and additional unmount test;
the final targeted run covers that change. Lint/type/build and routed checks followed it.
No full backend suite was run. Normal production build failed only because Google Fonts
could not be fetched under this environment's network policy. The offline-font build uses
the repository's existing test-only fixture; external font delivery remains unverified.

## Reproducible routed fixtures

Build with the offline-font command, then run the reviewer browser config. It starts a
fresh production Next server at `127.0.0.1:3000` and uses system Chromium at
`/usr/bin/chromium` (override with `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`). Synthetic
session/API fixtures establish frontend behavior, never database/provider acceptance.

Cases: canonical billing/usage URLs with query and real `#usage` anchor; both retired
authoring routes, authorized strategy context/back link and zero navigation mutations;
denied context disables sending; delayed Agent acknowledgment remains visible through
stale history; narrow Validation summary prefetch, independent pending/failed widgets,
and reuse when returning to the same filters. Unit checks cover every supported offset
reset, first-page recovery versus unavailable data, stable-ID dedup, stale/failed history,
out-of-order navigation, logout/unmount, session refresh races, tenant/query cancellation,
transient versus domain-unavailable retry policy and malformed/decimal boundary values.

Query retains its normal stale/error refetch on mount: returning explicitly to Validation
re-reads the previously failed quality source, while fresh successful sources use cache.
The measurement separates first-visit counts (one per source) from that tab-return read;
HTTP 422 is not automatically retried within either read.

Final production measurements are stored in `browser_latency.json`; screenshots:
`screenshots/agent-acknowledged.png` and `screenshots/validation-independent.png`.
These fixture timings are reproducible scenarios, not production latency claims.
Development Strict Mode cancels/restarts initial uncached reads; production measurements
avoid attributing those development-only attempts to product request counts.

## Disposable PostgreSQL API persistence reproduction

Use the managed local Docker daemon. Create only this task-owned loopback fixture:

```sh
env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH \
  docker --host=unix:///var/run/docker.sock run --name reviewer-wave-frontend-postgres \
  -e POSTGRES_USER=reviewer -e POSTGRES_PASSWORD=local-fixture-only -e POSTGRES_DB=reviewer \
  -p 127.0.0.1:55432:5432 -d postgres:17-alpine
env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH \
  docker --host=unix:///var/run/docker.sock exec reviewer-wave-frontend-postgres pg_isready -U reviewer -d reviewer
cd frontend
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 npm exec playwright test -- \
  --config=playwright.reviewer-api.config.ts --reporter=list
```

The fixture credentials are explicitly local test values. PostgreSQL image used:
`sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24`.
`run-reviewer-api.mjs` rejects any other database URL, initializes ORM tables with
`create_all`, then starts the backend with explicit mock/replay/paper settings, trading,
workers, Telegram and Redis disabled. It does not validate migration upgrades.

The test registers disposable tenants through HTTP, creates a strategy, uses the actual
generated PATCH client to change `htf_trend_pullback` to `nested_continuation`, validates
the response, independently reloads it by GET and verifies other-tenant GET returns 404.
Cleanup only the named fixture container and its anonymous volume:

```sh
env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH \
  docker --host=unix:///var/run/docker.sock rm -f -v reviewer-wave-frontend-postgres
```

An initial SQLite attempt was rejected by the generated validator because `created_at`
lacked its required timezone. This is recorded as a backend-owner compatibility request;
the runtime date-time contract was not loosened to make that test pass.

## Delivery limits

Agent 3's concrete ingestion/listing schema and Agent 2's durable recovery schema were
not published as code at the last check. Their dependent UI phases are explicitly blocked;
the baseline must be regenerated after integration. Integrator owns CI drift wiring and
the consolidated `.ai/RELEASE.md` dispatch. Mac/iCloud mirroring is unavailable on Linux;
local normalized handoff hashes and cloud-branch bytes are checked, Mac bytes are UNKNOWN.
