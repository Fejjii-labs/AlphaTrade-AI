# PR251 focused CI failure and correction

Failed source: `03be2d39567ed0990613e9b284f46910ac245641`, base
`df0a12d3b9c03b73ae8a1109adad66e4cb900885`.
[Run 38249795179, backend114812825333](https://github.com/Fejjii/AlphaTrade-AI/actions/runs/38249795179/job/114812825333)
was cancelled, **not passed**. Backend ran 12:33:47–12:54:03 UTC on 12 October 2026;
the focused step ran 12:34:05–12:54:00 UTC. The complete backend gate was skipped.

## Recovered order and exact failed case

The connected GitHub job-log reader recovered the archive after the CLI's signed
archive redirect was denied. Log base/head and all 75 selector entries match
`scripts/run_backend_focused.py --list-only` at the failed source. CI checked out
merge `31458b0dd93ac2f3132043287b94fccf16e261be`; its tree equals the feature's
`aebd699e5138f786ab4140dbf2f6c2a108643fff`. Locked local collection reconstructs
**1,440 ordered cases**, with SHA256 of newline-terminated node IDs:
`77465e971cb25b93d77187b1278664faf0a3748469b26bcc14dea89dc3e16d4f`.
The exact collection and selected-file list are retained in the ignored feature
checkpoint `.ai/local/trading-workflow/ci-repair/`.

Eleven complete 72-result passing rows precede 47 passes, `F`, then 24 passes in
the 60% row flushed at 12:50:26.378 UTC. Thus the failed ordinal is
`11 × 72 + 47 + 1 = 840`:

| Ordinal | Node |
| --- | --- |
| 838 | `tests/test_experiment_api.py::test_scoped_openapi_matches_published_interface` |
| 839 | `tests/test_experiment_api.py::test_api_requires_auth_roles_exact_revision_and_tenant_scope` |
| **840** | `tests/test_frontier_remediation.py::test_telegram_token_cannot_appear_in_logs_exceptions_or_health` |
| 841 | `tests/test_governed_blofin_demo.py::test_demo_gate_requires_separate_arm_and_exact_principal_pins` |

The timestamp identifies the flushed row, not the exact failure time. The 65% row
at 12:52:32 confirms 936 reported outcomes (935 passes, one failure). Its last case
is `test_knowledge_indexing.py::test_private_shared_templates_and_missing_sql_are_checked`.
No later result row, final traceback or stack survives. The exact active case at
cancellation is unknown; node 937 must not be called a proven hang.

To independently reconstruct the order, use a checkout of the failed source,
the locked development dependencies and explicit disposable PostgreSQL bindings
for all four CI fixture variables:

```bash
python scripts/run_backend_focused.py \
  --base df0a12d3b9c03b73ae8a1109adad66e4cb900885 \
  --head 03be2d39567ed0990613e9b284f46910ac245641 \
  --list-only > /tmp/pr251-selection.txt
mapfile -t selected < <(tail -n +2 /tmp/pr251-selection.txt)
cd backend
uv run pytest -o addopts='' --collect-only -q "${selected[@]}" > /tmp/pr251-collection.txt
```

## Smallest reproduced sequence and correction

The target passes alone and after its two adjacent cases. The ordered BloFin
predecessor group reproduces 158 passes followed by the target failure, 77.26s.
Reduction to **two cases** preserves the failure (one passed, one failed, 1.45s):

```bash
# Bind BLOFIN_ACTIVITY_TEST_POSTGRES_URL to an explicit disposable database.
uv run pytest -o addopts='' -vv -x --tb=long \
  -o faulthandler_timeout=60 --durations=5 \
  tests/test_blofin_activity_migration.py::test_additive_migration_and_rollback_preserve_existing_organization \
  tests/test_frontier_remediation.py::test_telegram_token_cannot_appear_in_logs_exceptions_or_health
```

Immediate failure: `test_frontier_remediation.py:382`, `assert handler.lines`,
actual `[]`. The migration fixture used `Config("alembic.ini")`; Alembic's
`fileConfig()` applies CLI logging globally and disables existing HTTPX/HTTPCore
loggers. Subsequent app configuration sets levels and redaction filters, but the
disabled HTTPX logger emits no record. This was test-process pollution, not a
demonstrated Telegram token leak or exchange failure.

The fixture now uses the fileless `Config()` pattern already used by existing
migration fixtures, with an explicit absolute script location. Actual revisions,
upgrade/downgrade/reupgrade, organization-history assertions, disposable schema
and cleanup are retained. It additionally verifies root/HTTPX/HTTPCore disabled
flags, levels and handler identities survive the round-trip. Production migration
and logging configuration, redaction assertions and CI files are unchanged.

## Affected local verification and acceptance

| Repair criterion | Result |
| --- | --- |
| Recover ordered collection and identify failed case | Complete: 1440 cases, ordinal 840, tree/selection/hash above |
| Reproduce smallest relevant sequence with immediate failure | Complete: two cases, `-vv -x --tb=long`, original failure retained |
| Correct demonstrated defect without weakening checks | Complete: isolated migration config plus logging-state regression assertion |
| Verify affected checks before publication | Complete: two-case sequence 2/2 and ordered BloFin sequence 159/159; no skips |

Repair acceptance is **4/4 local criteria**. The two-case repair passes in 2.20s;
the same ordered 159-case group passes in 77.53s. Counts overlap and are not added.
The group is, in order: activity migration, activity PostgreSQL, activity provider,
native producer PostgreSQL, presentation evidence, BloFin provider, then the exact
frontier case. Changed-file Ruff lint/format and `git diff --check` pass.
Repair file SHA256: `7b5015c42a8932f4588ed71cf7c498f621752698bdfc06b98b8b5c4556b6f8de`.

Local Python 3.12.14/PostgreSQL 17 differ from CI's Python 3.12.3/PostgreSQL 16-Alpine.
All four fixture URLs shared one owned disposable loopback database, matching CI
topology. Provider values and transport are simulated. These results do not prove
actual provider behavior or that the complete focused CI selection finishes within
20 minutes. No full backend CI was launched.

## Coordination, remaining gaps and next executable step

[Initial PR249 coordination](https://github.com/Fejjii/AlphaTrade-AI/pull/249#issuecomment-6125787952)
records the recovered failure and duration. Shared selector, fixture infrastructure,
verbosity, traceback retention and duration-budget changes belong to the release
owner. This repair changes one fixture and scoped evidence only; it neither extends
the 20-minute budget nor drops meaningful checks. Recommend the owner preserve
verbose case output, immediate failures and timing/stack evidence when qualifying
shared CI duration. The failed log alone cannot establish a slow or hung final case.

Next executable step: review the fixture repair and reproduce the two-case command
in the owner's disposable PostgreSQL 16 environment; then the release owner can
address the 75-entry/1440-case focused selector's duration using actual timings.
This feature does not dispatch the full combined release gate.

The five workflow implementation streams remain reviewable; native end-to-end
trading, live notification attribution and actual market acquisition remain **0/3
accepted**. Automated research returns `execution_requested=false` and
`samples_admitted=0`; simulated canonical execution tests grant no autonomous trades.
Missing BloFin account/rules/quote/reservation authority, protected-child/position
ancestry and fee/PnL/funding semantics remain precise blockers. Authenticated installed
Nested/SFP receipt packets and authorized exchange egress remain unavailable.
See [operational acceptance and next provider actions](trading_workflow_continuation.md).
Frozen PR249, root release handoff, deployment/operator settings and permissions
are preserved. BloFin remains authoritative for balances, fills and replenishment.
