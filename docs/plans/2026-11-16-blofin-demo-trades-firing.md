# BloFin demo trades firing plan

Status: REVIEW_REQUIRED — awaiting Griller GO and Sofien's approval of this plan.
Branch: codex/trades-not-firing. Starting head: ec11e65d.
Scope: two test-first fixes; local commits only. No push, PR creation, deployment,
environment/DB changes, credential access, or real trading. The task's no-push
instruction overrides the usual draft-plan PR publication step.

## Ownership claim

Agent: Codex. Track: autonomous paper/demo trades firing. No issue number was
provided; no GitHub issue comment has been posted. Planned files/directories:
backend/src/app/market_contracts/adapters/ (only if reproduction implicates it),
backend/src/app/persistence/public_market_observations.py (only if implicated),
backend/src/app/strategy_brain/assembly.py (only if implicated),
backend/src/app/services/automated_paper_loop.py,
backend/src/app/services/governed_blofin_demo.py,
backend/src/app/core/config.py, matching backend/tests, safe configuration
examples/docs, and this plan. Do not modify single-owner hot files without checking
existing ownership/conflicts. No migrations or lockfile changes planned.

## 1. Reproduce canonical duplicate rejection before fixing it

1. Parse the supplied worker export without disclosing secrets. The supplied page
   has 30 rows, hasMore=true, eight duplicate rejections, eight failed scans on
   HYPEUSDT 15m and six health events. It has no receipt-conflict/revision diagnostic.
   The approximately 2,430 failures are user-supplied history, not this page's count.
2. Trace DuplicateDataError through watcher_port, SFP receipt reuse, provider
   finalization, revision handling and historical assembly. Existing tests already
   cover decimal encoding equivalence, real payload conflicts, receipt clocks,
   rolling/restarted scans and Binance close-boundary confirmation. Do not claim
   those already-covered causes are new defects without a reproduction.
3. Add a failing regression using deterministic mocked public market responses and
   an isolated local persistence fixture. Exercise the real watcher evidence port
   and assert both the duplicate category and failed scan mapping before the fix.
   Include repeated scans/restarts and relevant overlapping/queued reads if the
   reproduction implicates them. Record the failing command and exact output.
4. Apply the smallest fix at the first incorrect identity/finality/assembly step.
   Do not overwrite immutable receipts, invent revisions, widen freshness, or
   dedupe unequal bars. If deduplication is justified, prove every typed semantic
   field matches and keep a conflicting-duplicate refusal regression.
5. Run focused regression tests and record passing results. If no root cause is
   reproducible from available evidence, report the uncertainty and exact missing
   diagnostic rather than relaxing the canonical evidence contract.

## 2. Add opt-in SFP execution planning to governed BloFin demo

1. First add failing tests for GOVERNED_DEMO_SFP_ENABLED absent/false and true.
   The false state retains sfp_execution_plan_not_authorized before planning;
   the true state must reach the real guarded planning path, not merely a stub.
2. Add a Settings boolean defaulting false. Preserve staging-only demo arming,
   demo host allowlist, EXECUTION_MODE=paper and ENABLE_REAL_TRADING=false.
3. Reuse _sfp_structural_plan and shared paper plan construction where possible.
   Replace the internal-paper-only opt-in naming only as needed to represent the
   two permitted paper/demo callers; keep every other caller's refusal default.
   Apply the same SFP ATR stop floor and structural stop/target requirements.
4. Preserve the governed demo snapshot, quote freshness and side checks, ledger
   verification, configured leverage readback, lot/tick rounding, eligibility,
   account and bot caps, daily/exposure limits, kill switches, authorization,
   idempotency, demo dispatch and reconciliation. Recheck SFP price bounds and
   minimum reward/risk against the final rounded demo-book entry/stop/target.
5. Verify paper default risk and explicit configured risk are respected for SFP;
   do not increase risk limits or silently inherit an incompatible sizing default.
   Refactor minimally if current demo sizing requires explicit SFP risk input.
6. Cover long/short valid plans and refusals for missing/invalid stop or target,
   stale quote, unfavorable reward/risk, wrong venue, real-trading setting,
   kill switch, blocked eligibility and risk caps. Confirm flag-off behavior for
   existing plans also fails closed and other strategy families remain unchanged.

## 3. Validate, document and commit locally

Run focused tests while iterating, keeping fail-then-pass output externally.
Then run the user's exact selection from backend/:

```sh
env -u CI uv run --extra dev python -m pytest tests -k "paper or canonical or autonomy or decision or sfp or governed or evidence" -q
```

Run relevant Ruff lint/format checks and git diff --check. Record actual result
counts, failures, warnings and persistence skips. No remote/shared DB access.
A full backend gate is required before later merge review; this task requests
local commits and no PR, so do not imply that merge review is complete.

Update safe documentation for the flag and write
/workspace/at-notes/trades-fix/report.md with the proven root cause, changes,
regression failures and passes, exact test results, final head SHA and required
worker environment values. List configuration requirements only; change none.
Update ignored local handoff/session files. File an Agent Reports entry with
verbatim visible transcript; never check the full-transcript box for a partial
snapshot. Review the diff and commit locally with Full-Backend: false. No push.

## Staging configuration inventory to verify, not change

ENVIRONMENT=staging; EXECUTION_MODE=paper; ENABLE_REAL_TRADING=false;
EXCHANGE_MODE=paper_exchange_demo; AUTONOMOUS_INTERNAL_PAPER_ENABLED=false;
AUTONOMOUS_BLOFIN_DEMO_ENABLED=true; BLOFIN_DEMO_ENABLED=true;
GOVERNED_BLOFIN_DEMO_ENABLED=true; proposed GOVERNED_DEMO_SFP_ENABLED=true
only after separate staging operator authorization. Scoped organization/user/account
pins and enabled matching execution account, demo credentials and allowlisted
demo REST/optional WS URLs must be valid. Watcher orchestration and controlled
staging activation must be enabled; public evidence source, polling/timeframes,
kill switches and exclusive automation/Telegram arming must satisfy current
controlled_watcher_arm checks. Exact supporting flags are in the prior report;
re-read current validators before publishing the final inventory. Do not enable
leverage writes as a workaround. Approved strategies, symbols, risk settings,
verified ledger and current venue state remain necessary non-env gates.

## Approval and deviations

No implementation starts until Griller GO and Sofien approves this plan, or the
user explicitly overrides that repository plan gate. No such approval of this
saved plan exists at creation. Record later deviations here; retain the approved
plan unchanged as the original record.

### Implementation record — 2026-11-16

The user explicitly cleared the plan gate and supplied Griller's GO WITH
CONDITIONS review at `/workspace/griller/alphatrade-trades-fix-plan-review-2026-11-16.md`.
The original plan above is retained unchanged. The implementation follows all
additional test-first, default-off, rounded-price, safety and local-only conditions.

Duplicate investigation deviation: identical re-scans did not reproduce the
staging failure. Following Griller's explicit fallback, retain strict receipt
validation and add allowlisted market/exception diagnostics. A corrupt-source-hash
fixture reproduces the exact undiagnosed wrapper for HYPEUSDT 15m; it is fault
injection, not evidence that staging has corrupt hashes. Root cause remains
unknown until the enriched diagnostic is observed. No duplicate exception is
swallowed and no immutable receipt is overwritten.

Execution regressions use isolated serial SQLite storage with timestamp result
normalization and a single connection to exercise the real watcher and canonical
authorities without a PostgreSQL database change. This does not prove concurrent
PostgreSQL fencing; that remains part of the later full merge gate.

Validation-only deviation: the requested selection exposed two existing SFP
registration tests expecting a policy error for a foreign tenant's version. The
resolver already deliberately returns NotFoundError. Correct those test
expectations to preserve non-disclosure; do not change the resolver behavior.
