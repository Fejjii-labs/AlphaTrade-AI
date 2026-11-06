# Paper-Signal Orchestration (AT-038)

Deterministic, reviewable routing from **validated TradingView signals** into the
existing **paper-validation** and **paper-proposal** pathways.

**Paper only.** This slice never places, modifies, or cancels live or paper orders.
`execution_mode` remains `paper` and `enable_real_trading` remains `false`. There is
no autonomous live mode.

See also: [tradingview_blofin_sync.md](./tradingview_blofin_sync.md) ·
[paper_validation.md](./paper_validation.md) · [security.md](./security.md)

## Modes

| Mode | Behavior |
|------|----------|
| `observe_only` (default) | Evaluate eligibility/risk; persist decision; no candidate/proposal side effects |
| `candidate_only` | On eligible + orchestrate: create/reuse paper-validation candidate (+ optional run plan) |
| `approval_required` | On eligible + orchestrate: candidate (+ optional run plan), then `awaiting_review` for explicit paper-proposal approval |

## Decision state machine

| Status | Meaning |
|--------|---------|
| `eligible` | Passed eligibility + risk checks |
| `blocked` | Risk/context gate failed (kill switch, cooldown, conflict, market context, …) |
| `rejected` | Signal malformed / contradictory / not validated |
| `expired` | Stale beyond `PAPER_SIGNAL_MAX_AGE_SECONDS` |
| `awaiting_review` | Candidate ready; human must approve paper proposal |
| `paper_candidate_created` | Candidate (and optional run plan) created in `candidate_only` |
| `paper_proposal_created` | Paper trade proposal created after explicit confirm |

Every transition is recorded with timestamp, from/to status, reason, and actor.

## Eligibility & risk evidence

Eligibility (fail closed):

- validated / non-duplicate TradingView signal
- freshness (`occurred_at` or `received_at`)
- allowed timeframe
- direction valid
- confidence ≥ configured minimum
- setup link when name/version provided (if required)
- strategy/version linkage when provided (if required)
- level consistency (long/short vs stop/TP)
- no conflicting opposite signal in window
- market context (BloFin demo snapshot when demo sync enabled)

Risk / safety:

- `execution_mode=paper`
- `enable_real_trading=false`
- kill switch clear
- daily loss lock clear
- cooldown after recent losing close

## API

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/paper-signal-orchestration/decisions` | Reader | Queue |
| GET | `/paper-signal-orchestration/decisions/{id}` | Reader | Detail + checks + links |
| POST | `/paper-signal-orchestration/signals/{id}/evaluate` | Trader | Evaluate only |
| POST | `/paper-signal-orchestration/signals/{id}/orchestrate` | Trader | Evaluate + advance by mode |
| POST | `/paper-signal-orchestration/decisions/{id}/approve-paper-proposal` | Trader | Confirm phrase required |

Confirm phrase: `APPROVE_PAPER_SIGNAL_PROPOSAL`.

Idempotency: unique `(organization_id, tradingview_signal_id)` and
`(organization_id, idempotency_key)`.

## Settings (defaults)

| Setting | Default | Notes |
|---------|---------|-------|
| `PAPER_SIGNAL_ORCHESTRATION_ENABLED` | `false` | Fail-closed when false |
| `PAPER_SIGNAL_ORCHESTRATION_MODE` | `observe_only` | No live mode |
| `PAPER_SIGNAL_MAX_AGE_SECONDS` | `900` | Stale → `expired` |
| `PAPER_SIGNAL_MIN_CONFIDENCE` | `0.0` | |
| `PAPER_SIGNAL_REQUIRE_SETUP_WHEN_NAMED` | `true` | |
| `PAPER_SIGNAL_REQUIRE_STRATEGY_WHEN_PROVIDED` | `true` | |
| `PAPER_SIGNAL_COOLDOWN_AFTER_LOSS_SECONDS` | `3600` | |
| `PAPER_SIGNAL_CONFLICT_WINDOW_SECONDS` | `3600` | |
| `PAPER_SIGNAL_CREATE_RUN_PLAN` | `true` | Uses existing Slice 81 pathway |

## Frontend

`/paper-signal-orchestration` — queue, detail, progressive disclosure of eligibility/
risk checks, links to signal / candidate / run plan / proposal / journal, approval
confirm for `awaiting_review`. Loading / empty / forbidden / disabled / error states.

## Safety invariants

1. Never calls exchange or paper order placement APIs.
2. Paper proposals are created only via `ProposalService` with `approval_required=True`.
3. Candidates/run plans reuse AT-037 / Slice 80–81 pathways.
4. Kill switch and approval confirmations are enforced. Daily loss and the
   after-loss cooldown are reported in the risk checks but do not block
   (AT-ADR-152); the 10% account daily equity stop is the only loss brake.
5. Fail closed when orchestration is disabled or mode is invalid.
