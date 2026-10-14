# PR257 PostgreSQL verification receipts

This continuation starts at `0132ef2ae15992f39b2f05bc921e06a8dc537c8e`.
Verified correction/test commit: `a48f47cf870d7e4ccbb921a84ed4c1988c0d735d`.
The only application correction is `backend/src/app/agent_voice/live_accounting.py`.
Raw JUnit/stdout are retained byte-for-byte in deterministic `.xml.gz`/`.txt.gz`
archives. Plain `.txt` previews trim only trailing whitespace; results are unchanged.
Exact source, raw-receipt and artifact SHA256 hashes, UTC times, counts and test nodes are in
[manifest.json](manifest.json). PostgreSQL 16.15 uses the official immutable image
`postgres@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea`.
Its major version matches repository CI. No workflow was run or changed.

| Receipt | Result | Time |
| --- | --- | --- |
| Seven previously skipped quota cases | 7 passed; 0 skips | 1.95s |
| Missing LiveLedger cases before correction | 8 passed; 3 failed; 0 skips | 14.55s |
| Quota + corrected LiveLedger PostgreSQL selection | 18 passed; 0 skips | 16.18s |
| Affected mocked ledger/close/drain selection | 13 passed; 10 intentionally deselected; 0 skips | 4.02s |

The three failures reproduce two defects: an equal-duration final replay accepted
another session ID or a different terminal reason; two-transaction close exposed
a temporary released hold for unconfirmed silence. Replays now validate identity
and reason before returning idempotently. Live close classifies/settles under one
row lock and one commit. The test observes every committed close state using a
separate physical PostgreSQL connection.

Four competing-reservation cases synchronize two real transactions before locks:
approved budget, monthly cost and monthly tokens each permit exactly one bounded
reservation; enough budget/quota permits both and preserves separate usage IDs/costs.
Concurrent duration and final receipt replays produce one usage row, never sum
cumulative seconds, and leave the exact replay unchanged. Finalization works from
fresh ledger/DB sessions before/after cleanup, even after expiry/role revocation;
unknown final usage and unresolved secondary work preserve quota holds.

The existing PostgreSQL fixtures are reused with NullPool and isolated schemas.
All schemas were dropped (remaining count 0). The loopback container was stopped
and removed and its tmpfs discarded. No staged/production database was contacted.

Commands (from `backend/`; supply only the disposable test URL):

```sh
OPENAI_API_KEY='' EXPERIMENT_TEST_POSTGRES_URL="$DISPOSABLE_PR257_POSTGRES_URL" timeout 120 .venv/bin/pytest tests/test_voice_reservations_quota.py tests/test_agent_live_postgres.py -o addopts='' -q -rA --show-capture=no --junitxml=postgres-after.xml
OPENAI_API_KEY='' timeout 90 .venv/bin/pytest tests/test_agent_live_integration.py -k 'cumulative_usage_once or invalid_usage_never or regression_foreign_final or existing_quota_and_current or socket_drains_final_usage' -o addopts='' -q -rA --show-capture=no --junitxml=affected-mocks.xml
.venv/bin/mypy src/app/agent_voice/live_accounting.py --follow-imports=silent
```

Only eleven missing PostgreSQL cases were added. Unaffected successful 68 backend
and 102 frontend mocked results remain valid at their recorded source; no full suite,
full CI, live provider activation, deployment or trading action occurred. Actual
microphone/model/billing acceptance remains 0/6 and is separate from this database proof.
