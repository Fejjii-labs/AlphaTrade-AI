# PR257 GPT Live + PR258 BloFin integration

Integration review is complete at the source/mock evidence level: **5/5 integration
items accepted**. Live voice acceptance remains **0/6**; supervised exchange
acceptance remains **0/6**. Nothing is deployed or activated by this integration.

## Exact inputs and branch ownership

| Input | Pinned identity |
| --- | --- |
| Frozen PR249 base | `0fbeaa492331dcb14011ed33b357da4ca4ee19b2` |
| PR257 publication | `b2a41ad88bc0fd306465e91d8709b9bf259fe09a` |
| PR257 verified ledger correction | `a48f47cf870d7e4ccbb921a84ed4c1988c0d735d` |
| PR257 original mocked implementation | `0036f88cb3e1939b1478159e1fefa8260fe11056` |
| PR258 publication | `2b94439ffc33e75c631f04d297cb18956e1c4893` |
| PR258 tested implementation | `73765026d1b39e3564005a7f16deac838edc4f00` |
| Feature common ancestor | `de5c010c7c85dfe5cde1ec8fabf60b6262500fe0` |

The separate branch is `codex/pr257-pr258-feature-integration`, created directly
from the frozen PR249 commit. Two merges retain the exact feature heads in its
ancestry. The integration PR targets `codex/interactive-voice-monitoring` for
review; this task does not update that branch. Its remote head was verified as the
pinned base before publication. The original workspace branch is also untouched.
The existing release chat/CI worker remains the sole owner of its continuation;
no workflow dispatch, cancellation or release investigation was performed here.

All five changes between the feature ancestor and the frozen base survive:
the workflow collection invocation, launch regression, guarded PostgreSQL fixture
reset helper/regression, and paper execution fixture adaptation. Every `.github`
file and release/focused runner under `scripts/` is byte-identical to the base.

## Integration review and conflict resolution

1. **Exact inputs and ownership — accepted.** Repository operating contract,
   safety/release rules, both root feature handoffs, both feature guides and Live
   provider contract were read. Frozen base fixes and source identities verified.
2. **Shared Agent and execution behavior — accepted.** Live's `shared_turn` uses
   the existing owner-scoped grounding, TurnCoordinator, canonical history/capture,
   quotas and original-key recovery. Speech/caption fragments remain reference
   data and cannot confirm proposals or authorize trades. BloFin execution starts
   only through existing Watcher/canonical plan/approval/account/dispatch gates;
   importing its new modules does not activate a worker or contact an exchange.
   No new bridge from Live delegation to native execution was introduced.
3. **Account/source and monetary boundaries — accepted.** Native enrollment,
   UID verification, reservations, protective ancestry, fills and sample admission
   stay tied to owner/account/version/variant/sample group. Manual native activity
   stays mirrored; internal simulation remains separate. Live grounding reads
   bounded stored fills and Journal facts; it does not newly promise complete
   history or experiment-performance retrieval. Price PnL and its win rate exclude
   fees/funding; unknown signed fee/`fillPnl` semantics stay unavailable. Voice
   cost holds remain in the voice/organization ledger, separate from account risk
   reservations. Existing scope guards and disabled flags are preserved.
4. **Combined API contracts — accepted.** Source files merged cleanly. Conflicts
   were in `HANDOFF.md`, `CHANGELOG_SESSION.md`, generated `hashes.json` and
   `validators.js`. Handoffs now describe the combined checkpoint. After merging
   both backend sources and PR258's generator guards, `npm run api:generate` ran
   **once** for the combined schema; none of either feature's generated snapshots
   was chosen as the winner. Drift verification exports/compares without rewriting.
   The combined schema SHA256 is
   `6a84f380af5bf8f2a3c0e90ba238840d223c263c21f979e5aaa9b28ea90725a3`.
   Both Live provider selectors and native closure/price fields are present.
5. **Evidence and reviewable publication — accepted.** All 42 feature source/test
   files are byte-identical to the pinned heads (20 PR257; 22 PR258). Feature
   artifact hashes, compressed Live raw receipts and the original BloFin source
   manifest were independently verified. New focused results below exercise only
   the combined contract and shared path. Publication uses existing skip markers.

No application behavior needed a conflict repair. The new transport regression
guards against losing Live's provider selector while generating native contracts.
The generated code replaces conflicting output from both snapshots.

## Reused evidence, with its original limits

| Feature evidence | Accepted source/result |
| --- | --- |
| GPT Live corrected PostgreSQL quota/ledger | `a48f47c`: 18 passed, no skips, PostgreSQL 16.15 |
| GPT Live affected mocked close/ledger/drain | `a48f47c`: 13 passed, no skips; 10 intentionally deselected |
| GPT Live original unaffected mocks | Reported at `0036f88`: 68 backend/102 frontend; ledger subset superseded by corrected receipts |
| BloFin reservations/concurrency/admission/lifecycle | `7376502`: 54 passed, no skips, PostgreSQL 17.11/mock venue IO |
| BloFin execution/readiness/ownership/manual/activity | `7376502`: 181 passed, no skips, same disposable database/mock IO |
| BloFin frontend/type/lint/contract checks | Original receipts retained; superseded combined contract/type checks below |

The PostgreSQL evidence is retained, **not rerun** and not presented as tests on
the integration commit. Both features' application code is unchanged, which
supports reuse; their common base fixture corrections are preserved. This is
not complete dependency analysis or full release acceptance. The older original
Live mock totals are documented feature evidence, not newly produced raw receipts.

## Focused integration checks

Exact output and source/output hashes are in
[`evidence/pr257-pr258-integration/`](evidence/pr257-pr258-integration/).

```sh
# backend; fake/local transports, no configured provider key or deployed DB
OPENAI_API_KEY='' timeout 90 .venv/bin/python -m pytest \
  tests/test_agent_live_integration.py::test_activation_is_disabled_and_live_uses_existing_budget_routes \
  tests/test_agent_live_integration.py::test_shared_agent_grounding_storage_exact_replay_and_secondary_usage \
  tests/test_agent_presentation_grounding.py::test_grounded_definitions_respect_tenant_and_library_owner \
  -o addopts='' -q --show-capture=no \
  --junitxml=../docs/evidence/pr257-pr258-integration/backend.xml
# 3 passed, no skips, 2.37 seconds

# frontend
npx vitest run src/lib/api/generated-contracts.test.ts src/lib/api/native-outcomes.test.ts
# 13 passed, 2 files; no skips
npx tsc --noEmit --pretty false --incremental false
# exit 0
UV_CACHE_DIR=/tmp/alphatrade-integration-uv-cache npm run api:check
# exit 0; combined schema hash above
```

Changed-test ESLint, source/diff safety checks and source equality verification
also passed. Plain retained raw receipts contain blank EOF lines: the initial
ordinary staged diff check flagged three receipt files. The accepted check uses
`git -c core.whitespace=-blank-at-eof diff --cached --check`, preserving byte-exact
feature receipts while checking other whitespace. No source conflict markers or
credential patterns remain. Dependency installation used the frozen lockfiles;
initial read-only default cache failures were resolved with `/tmp` caches.

## Remaining blockers and exact next acceptance

No unresolved source or focused-check blocker remains. Physical/provider/venue
acceptance is explicitly pending, and no exact integration full CI acceptance is
claimed. Mac/iCloud sync is unavailable in this cloud executor; GitHub carries the
current combined handoff, but Mac destination bytes/hash/mtime are unverified.

1. Review this exact integration head and its source/evidence manifest. The release
   owner decides when to adopt it into the frozen release continuation; preserve
   their branch/worker ownership. No main merge or deployment is authorized here.
2. Separately authorize the six checks in
   [`gpt_live_integration.md`](gpt_live_integration.md#exact-live-microphone-acceptance-still-required):
   grounded continuous requests/restored context; corrections/acoustic barge-in/echo;
   Interrupt/explicit Resume; physical permissions/device/lifecycle cleanup;
   broken-connection/auth original-key recovery; duration/quota/final accounting.
   Required inputs are actual scoped model access, dated duration/secondary-call
   receipts within the existing $1 ceiling, and physical desktop/Safari/iPhone
   microphones. Record exact SHA/device/session, latency and capped spend/usage.
3. Separately authorize the six supervised steps in
   [`autonomous_blofin_demo_execution.md`](autonomous_blofin_demo_execution.md#remaining-supervised-demo-acceptance):
   disarmed owner/UID/enrollment/NET-cross-1x readiness; one bounded minimum demo
   entry; actual protective parent/child/fill/flat receipts; complete mirrored
   interval and one sample after restart; independent monetary semantics before
   fee-adjusted claims; disarm and existing rollback. No venue calls happened here.
4. Any future consolidated staging/release/full gate remains with the existing
   release/CI owner under `.ai/RELEASE.md` and separate authority. Do not rerun green
   feature batches or launch full CI as part of this integration review.
