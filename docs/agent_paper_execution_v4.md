# Agent paper execution V4 handoff

Branch: `codex/agent_paper_execution_v4_001`
Exact starting base (PR167): `7df8bcc6e42ba68ab2194b980de1792abeceabcc`
Draft PR: [#171](https://github.com/Fejjii/AlphaTrade-AI/pull/171), stacked on #167
Implementation commits: `dadae5ef85a285deb0e75cb3f346013140c42328`,
`6d2311dfe3552b47c85a0b8c996f182c6dc0810b`

## Behavior

An authenticated Trader sends explicit trade details through the existing
`POST /agent/turns` endpoint using `paper_trade.prepare_execution`, then confirms
through the existing `/agent/proposals/{proposal_id}/confirm` endpoint. The legacy
`POST /chat/message` conversation graph delegates to the same adapter. The Agent validates a bounded paper
intent and delegates to existing canonical authorities. It never detects a setup,
mints a Candidate, chooses a position size, places a venue order, or writes journal
lifecycle rows directly.

An existing ACTIVE canonical Candidate and a currently paper-actionable
ActionEligibility issued for this user/account are required. Candidate identity,
market, direction and timeframe must match. Missing Candidate or eligibility is
refused; a conversational request does not manufacture either authority.

Canonical pretrade evidence and risk checks use `PreTradeAnalysisService.analyze_paper_trade`,
`CanonicalEvidenceService`,
`CandidateLifecycleService`, `ActionEligibilityService`, `DailyRiskAccounting`,
`RiskSettingsService`, `KillSwitchService` and `RiskService`. Advisory pretrade
placeholder prices and model-generated levels are never executable inputs.

The approved cash ceiling is authoritative portfolio equity times the persisted
maximum risk percentage. `PositionSizingService.calculate_paper` sizes from that
ceiling and the RiskEngine notional cap, preserves cost allowances and rounds down
to the existing internal paper lot size. The Agent cannot supply cash risk, equity,
quantity, leverage, instrument rules, fees or execution policy overrides.

Shared DailyRiskAccounting includes canonical lifecycle-bound paper journal net
outcomes, trade counts and open exposure. Imported/unbound journals and legacy
linked-position mirrors are excluded from these additional totals. These limits
are checked before preparation and again before confirmation. The after-loss
cooldown no longer refuses an Agent paper trade (AT-ADR-152, R6); the 10%
account daily equity stop is the only loss brake.

`CanonicalTradePlanService` creates the immutable revision and existing
`ApprovalService` creates its pending decision. The proposal displays entry, stop,
targets, deterministic quantity, maximum loss, costs and expiry. Preparing a
proposal creates no authorization, execution action, fill or journal trade.

## Conversation protocol

The primary V3 Agent API accepts a typed action in a conversation turn:

```json
{
  "message": "Prepare this paper execution",
  "action": {
    "name": "paper_trade.prepare_execution",
    "arguments": {
      "trade": {
        "candidate_id": "<canonical-candidate-uuid>",
        "account_id": "<paper-account-uuid>",
        "symbol": "BTCUSDT",
        "venue": "binance",
        "market": "PERPETUAL",
        "timeframe": "15m",
        "direction": "short",
        "entry": "100000",
        "stop": "101000",
        "targets": ["99000", "98000"]
      }
    }
  }
}
```

The sealed proposal contains `payload.paper_execution`, including canonical
Candidate, eligibility, approval and plan identities, full execution terms, current
risk verdict and pretrade risk/reward analysis. Narrative models cannot modify this
proposal or choose its size. Confirm the proposal with:

```json
{
  "conversation_id": "<returned-conversation-uuid>",
  "expected_content_hash": "<returned-proposal-content-hash>",
  "statement": "I confirm"
}
```

Send this to `POST /agent/proposals/<returned-proposal-uuid>/confirm`. Existing
membership checks, explicit consent grammar, content hash validation, rejection
state and transcript row locks guard application. The proposal seal includes the
exact canonical plan revision/hash. An applied proposal retains its seal and
returns canonical identities in `application_result`, with `resulting_record_id`
equal to the paper action ID. Repeated confirmations replay the saved receipt.
Preparation reports `authority_mutated=true` because the canonical plan and its
lineage are persisted, while `applied=false` and `execution_attempted=false` reflect
that no paper action has been authorized or executed yet.

Both conversation APIs also recognize explicit labelled fields, with decimal values expressed as base-10 text:

```text
Prepare paper trade candidate_id=<canonical-candidate-uuid> account_id=<paper-account-uuid> symbol=BTCUSDT venue=binance market=PERPETUAL timeframe=15m direction=short entry=100000 stop=101000 targets=99000,98000
```

Alternatively send `Prepare paper trade ` followed by a JSON object containing
those same fields. JSON prices must be strings, not binary floating-point values.
Required details have no market/price defaults. Stops must be on the loss side;
targets must progress on the profit side; all levels must satisfy paper tick
precision. A MARKET entry must match the fresh canonical quote exactly.

For the legacy `/chat/message` route, the response includes `paper_execution`, containing the canonical Candidate and
eligibility IDs, full immutable plan (including plan/revision IDs and hash),
approval ID and an exact confirmation message:

```text
Confirm paper execution revision=<presented-revision-uuid> hash=<presented-content-hash>
```

Send that exact revision/hash message to `/chat/message` in the **same conversation**. Generic approval, `yes`, inferred
consent, questions, conditional statements and unpresented revision IDs never
confirm an execution. Assistant prose and model output cannot issue confirmation.
The durable assistant payload binds the presented identity to the authenticated
conversation. In the legacy graph, a later preparation request supersedes an earlier proposal, including
when the later request has invalid or incomplete details.

## Confirmation and execution

Before a fresh confirmation, the adapter rechecks the persisted plan/hash,
Candidate state/expiry, latest eligibility identity/expiry, current canonical quote,
current monetary risk ceiling, deterministic risk verdict and execution quota.
A rejected/superseded/expired approval cannot issue authorization. Risk BLOCK is
final; the adapter does not resize or retry around it.

Only exact human confirmation enters a scoped APPROVE operation to issue the
existing plan-bound authorization, then a separate EXECUTE_PAPER_PLAN operation.
Existing `ExecutionService.execute_paper_plan` routes to canonical paper execution,
whose claim transaction revalidates lineage and locks current safety epoch and risk
accounting before allocating capacity. No detector or exchange execution is called.

The existing internal fill gateway and canonical journal projector record the fill
and journal linkage. The response returns authorization, paper action (canonical
execution command), receipt and journal IDs. This authority uses execution commands,
not legacy Order rows. Plan creation, approval, claim, fill, metering and transcript
persistence share the caller's transaction. The `/agent` API rolls back a failed
request; the legacy graph uses a savepoint to retain a refusal without partial execution.

Conversation locking serializes concurrent confirmations without blocking ordinary
foreign-key message inserts. Execution and fill keys derive from the immutable
revision and use the existing gateway's idempotency mechanisms. Duplicate completed
confirmations return the durable receipt and journal IDs; they create no new
execution, fill, reservation or usage charge, including after restart/expiry.

## Limits and review

Only the existing PAPER_INTERNAL, linear USDT perpetual policy is accepted.
Its exact-price simulation, zero fee/funding/slippage allowances, lot and tick
precision, quantity units and canonical plan serialization remain unchanged.
The shared terms builder was extracted from the existing automated paper loop;
that loop retains its prior entry and sizing behavior. No database migration is
needed: proposals bind through existing canonical plans, approvals and conversation
payloads.

Proposal validity is capped by Candidate, eligibility and current quote validity.
A changed entry quote or expired window requires a fresh canonical setup/proposal;
confirmation never refreshes or modifies immutable plan terms. Disabled/degraded
market evidence fails closed. Live execution and `ENABLE_REAL_TRADING=true` are
refused by the existing permanent paper invariant and this adapter's posture check.

Focused tests in `backend/tests/test_agent_paper_execution_v4.py` exercise the V3
sealed proposal service and HTTP confirmation endpoint, and the legacy
conversation graph, with actual PostgreSQL plan, approval, claim, fill and journal
authorities. Only external market reads are stubbed. Coverage includes successful
flow, risk BLOCK before preparation/at confirmation, locked claim capacity, stale
identity/expiry/price/Candidate/risk/proposal, duplicate/restarted/concurrent
confirmation, invalid details, missing fields, tenant/account/conversation isolation,
rejected approvals, degraded evidence, quota rejection, live refusal and cost/lot
sizing. Existing canonical execution, paper loop, Agent and plan regressions are
also included in verification.

No frontend, Telegram, deployment or live configuration changes. Review the draft
PR; merge and deployment are outside this handoff.

## Verification

- Final backend matrix: **3,022 passed, 3 failed, zero skipped**, covering all
  3,025 collected tests across 215 modules. All **62 V4 cases** pass, including
  the HTTP and legacy conversation paths and the six added daily-stop/cooldown
  regressions. No new V4 regression remains.
- Existing failures in `test_controlled_paper_activation_rehearsal.py` reproduce
  identically on an untouched checkout of the exact starting SHA: the live-evidence
  rehearsal returns `provider_unreachable`, and the outage/stale cases return
  `blocked` where their assertions expect `failed`. The full suite is therefore
  not green; these baseline failures are recorded for review and left outside V4.
- Final source: `.venv/bin/ruff check src tests` and
  `.venv/bin/ruff format --check src tests` pass (981 files formatted);
  `git diff --check` passes. Focused mypy on the changed Agent execution, daily
  accounting and cooldown modules passes with `--follow-imports=silent`.
- Full-run command: `UV_CACHE_DIR=/tmp/alphatrade-uv .venv/bin/python /tmp/alphatrade-v4-suite.py`.
  This temporary test runner collects every test, partitions complete modules
  across four processes with separate local PostgreSQL databases through
  `PHASE1_POSTGRES_URL` and `AT028_POSTGRES_URL`, and invokes each with
  `pytest -q -o addopts='' --tb=short`. The two Watcher migration tests run
  serially afterward in the required ephemeral `alphatrade_test` database and
  pass. The aggregate exit is 1 solely for the three baseline failures.
- Base comparison command: `pytest -q -o addopts='' --tb=short tests/test_controlled_paper_activation_rehearsal.py`
  from a detached checkout at the exact starting SHA, with an isolated PostgreSQL
  database: 1 passed, 3 failed, exit 1 with the same assertions.

The requested branch and draft PR already existed when publishing was attempted.
Their canonical implementation was preserved, and the daily-accounting/cooldown
fixes were added with a fast-forward continuation. No competing execution adapter
is present on this branch. Review draft PR171 and the root `HANDOFF.md`; the task
stops after pushing this handoff. No merge or deployment follows.
