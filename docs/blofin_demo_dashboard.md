# BloFin demo Dashboard integration

PR [#230](https://github.com/Fejjii/AlphaTrade-AI/pull/230), branch
`codex/blofin-demo-dashboard`, is based on merged main
`d9ebf87e9d50685da5078bdd4d590757b0a9c1c0` (PR229).
PR [#231](https://github.com/Fejjii/AlphaTrade-AI/pull/231) was inspected separately.
Its reconciliation, recovery and Agent selection changes are not incorporated.
Review and release reconciliation first, then this Dashboard PR.

The Dashboard presents the configured native demo account above paper portfolio
metrics: account equity, cash/available/asset equity, native position count,
direction, contracts, verified base quantity, entry, mark, leverage and unrealized
PnL. Unknown values remain `—`. Journal rows show their recorded exchange/source.
Native snapshots may include outside trades and do not establish fills, SL/TP
linkage, Journal evidence or realized results.

## Field semantics

| Field | Native evidence and units |
| --- | --- |
| Native account equity (USD) | `/api/v1/account/balance` top-level `totalEquity`, explicitly USD. Never inferred from cash, available funds, asset sums or paper equity. |
| Cash / Available | `details[].balance` / `details[].available`, in that currency. `eq` is not a substitute for cash balance. |
| Asset equity | `details[].equity` (legacy `eq` alias), in the asset. Missing equity is unknown; native zero stays zero. |
| Contracts | Absolute native `positions`/`pos`; signed NET size determines long/short. Decimal strings retain precision. |
| Base quantity | Absolute contracts × finite positive `contractValue`, from public instruments metadata saved with the same Dashboard sync. Requires a unique exact ID, matching base/quote, live SWAP/PERPETUAL and `contractType=linear`. Explicit conflicting contract-value currency is rejected. |
| Unknown base quantity | Missing, duplicate, malformed, suspended, inverse or mismatched metadata yields `null`. No symbol-based multiplier, assumed unit contract or price conversion. Older snapshots without metadata stay unknown until Dashboard native refresh. |

Field definitions were checked against BloFin's
[balance](https://blofin.com/docs#get-balance) and
[instruments](https://blofin.com/docs#get-instruments) documentation, using a
[pinned source](https://github.com/Anonymous-fe/blofin-api-docs/blob/167da57aec7751207d6b53248d002435a397b234/index.md)
and an independent native account shape in
[CCXT](https://github.com/ccxt/ccxt/blob/f0aca06eb7482f083bed8406fadfcc0c05e45b1c/python/ccxt/blofin.py).
With verified `0.001 BTC/contract`, `0.1 contracts` is `0.0001 BTC`.
This is a fixture example, not a live account claim.

## Retrieval and refresh

| Action | Behavior |
| --- | --- |
| Open / Dashboard Refresh | Authenticated organization-scoped `GET /dashboard/demo-account`. Saved evidence only; no venue request. An owner with demo gates open then posts one native refresh immediately. |
| Visible owner Dashboard and /exchange | Native `POST /dashboard/demo-account/refresh` on open, then every `BLOFIN_DEMO_SNAPSHOT_SYNC_SECONDS` (default 45, allowed 30–60) while visible. One native request at a time; saved reads cannot overtake it. |
| Visible trader/viewer | Saved GET on the same interval. Native refresh retains owner-only authorization. |
| Hidden page | Stops scheduled polling. Visibility return performs at most one overdue request, respecting due time/backoff. Existing in-flight requests may complete. |
| Failure | Retry delay doubles from the configured interval and is capped at 900 seconds. HTTP/timeouts and unavailable responses retain successful evidence and its timestamp with stale/error status. Success clears the error and restores the configured interval. |
| Timeout / navigation | A 30-second deadline and AbortSignal bound browser work. Superseded reads/unmount cancel requests; late results cannot overwrite state. A server read already started may finish after browser cancellation. |
| Explicit native refresh | Owner-only; repeated clicks and automatic refresh are deduplicated in the card. Dashboard-wide Refresh remains a saved read. |
| Failed saved attempt / reload | Failure remains persisted/audited. Dashboard retrieves this organization's last valid successful snapshot, marks it stale and exposes the failed attempt timestamp/generic error. Other sync consumers still read the actual latest attempt. |
| First failure / no snapshot | Unavailable / not synced / inactive never claim an invented flat account. |
| Limits | The native endpoint allows 240 refreshes per organization and user per hour so one or two open owner views can poll every 30 seconds. Multiple tabs share that limit and independently back off. The worker snapshot cycle skips when a fresh snapshot already exists. Lists retain configured bounds; truncated positions have no full count. |

Both endpoints return `Cache-Control: private, no-store`. Server freshness
(default 300 seconds) and browser expiry still apply. Errors/provenance payloads
are excluded. Refresh does not reconcile orders, write Journal or authorize trades.

## Shared changes and dependencies

No new dependency on PR231. Existing sync, snapshot JSON, RBAC and Journal
exchange/source contracts are reused. No migration, credential or activation change.

- `ExchangeBalance` gains optional equity. The native provider exposes optional
  USD total equity from the same balance read. Required quantities stay strict;
  missing metrics never become zero.
- `sync(include_instrument_metadata=False)` adds an optional public instruments
  GET only when the Dashboard opts in and positions exist. Existing settings and
  reconciliation sync retain their request sequence. Metadata failure degrades
  conversion while preserving native account evidence. Metadata and instrument
  IDs are saved in existing bounded JSON.
- The read-only transport permits the exact unsigned public instruments GET
  without query parameters alongside existing account GETs. Returned metadata
  must pass SWAP/linear validation; order/transfer/withdrawal calls remain forbidden.
  Its signature includes merged main's `before_send` hook.
- `latest(successful_only=False)` and its repository query gain an optional
  organization-scoped success filter for Dashboard preservation. Defaults stay
  unchanged for other consumers.

The configuration represents one demo account without an account fingerprint
or selector. After rotating credentials, fetch a new snapshot before interpreting
saved historical account data.

## CI failure and verification

Job `113374298672` failed at `0.1 contracts` for both widths because the whole
card was absent. Reproduction on `localhost` showed Sign in: the fixture installed
`alphatrade_session` for `127.0.0.1`, while CI opens `localhost`. Middleware
redirected before the Dashboard/account request. The fixture now derives cookie
and frontend origin from Playwright's base URL. Navigation, bearer authentication
and the account GET are asserted; the contracts assertion remains intact.

Local verification uses authenticated SQLite/tenant fixtures,
`httpx.MockTransport` native payloads and Chromium presentation fixtures:

- 123 focused backend account/acceptance/probe/Dashboard checks passed without
  skips; three additional precision/malformed-equity checks passed.
- Final account suite passed all 44 cases using CI's locked dependencies,
  including the documented unsigned bulk instruments lookup and its restrictions.
- Broader sync/provider regression: 154 passed, 62 existing PostgreSQL-only
  governed/reconciliation cases skipped because PostgreSQL is unavailable.
- 36 frontend tests passed across card, account API, Dashboard page and helpers:
  backoff cap/recovery, visibility, timeout, unmount, races, reader/owner behavior
  and preserved evidence.
- Both Chromium widths (1280/390) passed on CI-default `localhost`, using CI's
  dev frontend/backend webServer configuration and a production server. Checks
  retain `0.1 contracts`, exercise automatic failure/backoff/manual recovery,
  equity/base quantity, separate paper equity and no horizontal overflow.
- Repository Ruff lint/format passed with CI's locked 0.15.15; scoped mypy,
  frontend ESLint and TypeScript passed.

A production build passed using Next's local font response fixture. Existing
Google Fonts downloads are restricted by this environment's network policy;
repository font/network configuration was not changed. Full backend CI was not
manually dispatched. Existing test-key/deprecation warnings are unrelated.

The normal PR CI run [802](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/37816224281)
passed on `ba29e25`: 131 focused backend tests, all 1,434 frontend tests, normal
production build, all 41 Chromium cases, deployment safety, Docker and evaluations.
The final native query correction is covered by the 44-case local account suite
and triggers another ordinary PR CI run on push.

## Remaining live gate

After review, required checks and deployment, an owner must compare equity **in
USD**, asset equity/cash/available and the existing native position with BloFin at
the snapshot timestamp. Compare contracts with contracts and verify current
instrument metadata before comparing BTC quantity. Leave Dashboard visible to
confirm a native timestamp advance, hide it to check polling pauses, and verify
failure preserves stale evidence before recovery. Confirm tenant isolation and
reader restrictions. Reconciliation/SL/TP evidence stays in its separate workflow.

No live exchange reads/orders, deployment, credentials, activation or hold changes
were performed. This environment has no BloFin credentials; native field
availability, live refresh and account comparison remain unverified.
