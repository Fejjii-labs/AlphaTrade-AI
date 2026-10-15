# Manual order diagnostic checkpoint

2026-10-09, reviewed baseline PR233/main `b165b92`. Root cause: **unknown**.
No authenticated failing owner request, sanitized error envelope/request ID,
command ID or authorized runtime logs/account session is available. Healthy API
and an inactive kill switch do not establish submission readiness.

The supported form is `ManualDemoTest` in Settings execution controls. It requests
authenticated instrument limits, sends decimal strings in exchange contract units
to `/execution/manual-demo/preview`, then confirms the exact revision/content hash
at `/confirm`. Owner RBAC, configured organization/user/account pins and sealed
demo credentials are checked server-side. This path selects BloFin demo explicitly;
it does not silently substitute internal paper execution.

Preparation validates fresh executable depth, instrument units/increments, flat
account state, available demo capacity, risk limits and safety epochs. Confirmation
rechecks scope, plan integrity and readiness before the existing durable claim,
idempotency key, reservation and fenced venue submission. Native response,
acceptance, actual fills, protective orders and subsequent reconciliation are
separate observations. The UI retains the same revision after network loss or
post-submit failure; only explicit `submission=not_started` permits a fresh plan.
History/detail/reconciliation use the existing command and venue client order ID.

Baseline verification on disposable PostgreSQL 17.11:

```sh
PHASE1_POSTGRES_URL=<disposable-loopback-db> .venv/bin/pytest \
  tests/test_manual_blofin_demo.py tests/test_manual_demo_reconciliation.py \
  tests/test_manual_demo_native_pagination.py
```

110 manual-order cases passed in the combined baseline selection (two additional
knowledge regression cases failed as expected). This includes authenticated API
scope/payload validation, decimal/contract semantics, gates, concurrent confirmation,
accepted or uncertain timeout/restart recovery and read-only reconciliation.
The venue is `httpx.MockTransport`; no external order was sent or accepted.
No manual-order production code change is justified by this result.

To resolve the incident, supervised acceptance must supply the failing stage,
timestamp, HTTP status and sanitized error details/request ID; if submission began,
provide the existing command ID and read-only native reconciliation evidence.
Do not reproduce by sending another external order from this task. Account funds,
credential readiness, pins, deployment version and native venue response remain
unverified.

Dedicated retained-path checks also passed: `ManualDemoTest.test.tsx` and
`ManualDemoHistory.test.tsx` (25 cases), and
`e2e/manual-demo-recovery.spec.ts` with system Chromium (2 cases, no skips/retries).
The browser exercises fake API/history reload/logout/reconciliation behavior and
preserves command identity after a lost response. These checks establish local UI
behavior, not authenticated external submission readiness or acceptance. No
manual backend/widget production change was made.
