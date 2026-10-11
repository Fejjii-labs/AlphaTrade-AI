# Coin strategy monitoring assignments

This contribution starts from accepted PR248 `a7a843f7c27e8bdcea5f89c802c808472477a969`. It adds observation configuration, not execution authority. Existing watcher, Telegram, kill switch, account, approval and exchange settings are preserved. No live exchange calls, orders or Telegram messages qualified this work.

## One configuration authority

`GET /watcher/assignments` and `PUT /watcher/assignments` use `WatcherAssignmentService`. Confirmed Agent actions must call that same service with the authenticated organization and actor. The caller owns commit/rollback; the synchronous HTTP mutation commits both configuration and replay receipt before replying.

The replacement DTO is `{revision, request_key, assignments}`. Each assignment contains its UUID, coin symbol, immutable saved `strategy_version_id`, independent `monitoring_enabled`, explicit timeframes and the **complete** saved `pattern_spec` parameter snapshot. Use `available_versions` from the read response rather than constructing a different snapshot. Changing timeframes or parameters requires authoring another saved version. The authored coin must match; a BTC strategy cannot silently monitor ETH.

Reads and replacements expose only the actor's private saved strategies and assignments. Other actors' assignments remain intact. Workers resolve each row against its original actor's fresh membership, tenant, immutable version/hash, lifecycle and compiled policy. The limits are 25 assignments per organization and five saved versions per coin.

Explicit assignments override inferred coverage only for the same organization, actor, strategy and coin. The first save preserves unrelated legacy targets, including other coins and actors. Pinned versions replace that binding's inferred current version without adding a duplicate. Disabled or removed bindings stay suppressed after reload and worker restart: existing immutable replacement receipts retain the previously managed coverage. An empty first save manages no bindings and therefore preserves existing coverage; an empty replacement removes only previously managed bindings. The response's `configured` flag records a saved configuration, not an organization-wide switch. Existing cycle capacity limits remain unchanged.

The root watchlist revision fences changes. Assignment replacement preserves all watchlist enabled toggles. A durable organization/request-key receipt binds the authenticated actor and exact payload hash and retains the original response. Identical concurrent or fresh-session replays converge on one revision; a changed payload or actor cannot reuse the key.

## Worker and notification boundaries

Explicit targets consume pinned version references rather than a strategy's later current draft. Both explicit and retained inferred targets check the watchlist revision and fresh authority before evidence acquisition, during the existing Candidate publication fence and again before notification. A first save, pause or removal invalidates a previously loaded inferred target. The short publication probe uses an independent database connection and PostgreSQL `FOR NO KEY UPDATE`, with a two-second lock bound. It serializes with configuration writers while remaining compatible with the scan transaction's own research FK `KEY SHARE` locks. Existing lease and Candidate fences remain in place.

Assignment targets skip the paper execution continuation. Existing unassigned governed paths retain their original gates. Read-only research and Candidate qualification do not activate trading. Missing bindings, draft/disabled versions, absent compilation, unsupported authored patterns, disabled coins and cycle capacity have explicit scan inability reasons.

Nested forming and expired evidence remains recorded; the existing confirmed-alert policy suppresses alerts without inventing an informational policy. Fresh qualified Nested confirmation and existing SFP lifecycle events use their established outbox policy. Synthetic SFP expiry preserves original evidence timestamps and deduplicates across fresh-session reads. All delivery checks use `FakeTelegramTransport`; live Telegram sends and live acceptance are both zero.

## Compact UI and recovery

Settings → Market monitoring contains coin strategy cards, independent toggles, Add/Remove/Save and expandable details with Back navigation. Technical IDs and parameter evidence stay in details. The authoring link returns to Agent.

The pending request uses the durable authenticated recovery session scope under `alphatrade:watcher-assignments:`. Session generation only cancels stale responses. Body validation happens before new pending storage and before explicit recovery transport. Local validation/storage rejection sends no request. Timeout or malformed successful replies retain the exact original body and key; reload never automatically retries. Auth lifecycle cleanup must include this prefix even while the panel is unmounted; the integration owner supplies that shared cleanup.

## Focused evidence

Disposable PostgreSQL used database `alphatrade_assignment_worker` on loopback port 55439, with a unique schema per case and only that schema dropped. All 26 distinct new backend cases have passing coverage across the selections below. These receipts represent changed behavior; they are not full backend acceptance. No unexpected skips occurred.

| Selection | Actual result | Receipt |
| --- | --- | --- |
| Initial assignment cases | 12 passed, 3 failed | `/tmp/alphatrade-assignments-initial.xml` |
| Three affected repairs | 3 passed | `/tmp/alphatrade-assignments-repair.xml` |
| Expanded eligibility and family policy run, interrupted | 5 passed, 2 failed; final case not completed | `/tmp/alphatrade-assignments-expanded.xml` |
| Expanded repairs | 8 passed | `/tmp/alphatrade-assignments-expanded-repair.xml` |
| New publication race fixture | 1 failed: missing datetime import | `/tmp/alphatrade-assignments-lock-race.xml` |
| Race repair | 1 passed | `/tmp/alphatrade-assignments-lock-race-repair.xml` |
| Expiry and actual-login role refusal | 3 passed | `/tmp/alphatrade-assignments-expiry-role.xml` |
| Affected assignment and existing tenant/revision/replay compatibility | 8 passed | `/tmp/alphatrade-assignments-compatibility.xml` |
| Final component and request/response transport | 11 component + 9 transport passed | `/tmp/alphatrade-assignments-ui-validation.xml` |

Initial repairs corrected an ordered fixture assertion, a SideEffectProbe field assertion and the production error's required reason code. Expanded failures exposed an invalid fixture attempt to delete an immutable compiled definition and a genuine publication deadlock: independent `FOR UPDATE` waited on the scan's own FK lock. The fixture now creates a legitimate approved uncompiled version; the compatible publication lock is proved against a real concurrent pause, without test-only serialization or weaker assertions.

The PostgreSQL command prefix was:

```sh
EXPERIMENT_TEST_POSTGRES_URL=postgresql+psycopg://alphatrade@127.0.0.1:55439/alphatrade_assignment_worker \
PYTHONPATH=/workspace/AlphaTrade-monitoring-assignments/backend/src:. \
.venv/bin/pytest -o addopts='' -q -rs <affected selectors> --junitxml=<receipt>
```

Frontend verification used `npx vitest run src/components/WatcherAssignmentsSection.test.tsx src/lib/monitoring/assignments-api.test.ts`, `npx tsc --noEmit` and scoped ESLint. Backend verification used changed-file Ruff lint/format and strict mypy on the three new model/schema/service modules.

## Remaining integration and rollback qualification

PR249's scoped transition correction has 28 distinct focused passing cases:
19 new (18 PostgreSQL and one portable SQLite case), one adjusted historical
removal case and eight existing compatibility cases. Zero unexpected skips.
First-save unrelated coverage, pinned-version deduplication, independent disable
and removal, empty replacement/restart, actor/tenant/configuration changes and
publication/dispatch fences are covered. Changed-file Ruff/format and service
mypy pass. Direct contribution `c9e3be59` is integrated as `5213c94f`; exact source
hashes, selectors and XML receipts are in
`/tmp/alphatrade-assignment-transition-final-manifest.json`. One unchanged Binance
composition case initially failed and passed its isolated retry; its cause is
not established. No assertion or freshness guard was weakened. This correction
does not change monitoring settings, schema, execution authority or deployment.

The owner has now integrated this contribution with the generated client, authenticated real-login desktop/mobile API/PostgreSQL save/reload/toggle/recovery and off-panel logout/account-change checks, and eight canonical confirmed Agent action persistence cases. M1–M5 are locally accepted (5/5); these checks do not establish hosted worker consumption or live notification delivery. Shared contracts have sole additive head a13voicemonitor001. The retained-schema rollback preserves assignments, receipts and unknown voice holds; the old worker remains drained. No hosted deployment or operator setting changed. Exact candidate and consolidated receipts are in the existing integration checkpoint.

The owner sequences the additive a13 tables after immutable a12 ancestry. Retain assignment rows and replay receipts during a drained rollback. An a7 worker lacks this assignment consumer and must **not** resume against configured assignment data unless a compatible consumer is separately ported and qualified. A compatible image and aligned API/worker/frontend revisions are prerequisites; retaining the schema alone does not make an old armed worker compatible.

The observed five-enabled/four-`no_strategy` report follows the old source path: five default enabled coins, but only selected approved and compiled strategies with matching authored symbols become targets. Four enabled coins with no matching strategy remain `no_strategy`. Selected hosted receipts are stale and not verified as the API's actual database binding; they establish no current runtime acceptance. This implementation does not fabricate assignments for those coins or mutate their operator configuration.
