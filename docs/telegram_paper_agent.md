# Telegram paper interaction layer (AT-074)

Paper-mode Telegram interaction on top of the existing Telegram security
protocol, PostgreSQL Telegram store, and Candidate alert gateway.

Telegram remains **disabled by default**. There is no webhook on FastAPI.
Production rejects `TELEGRAM_INTERACTION_ENABLED=true`. Staging accepts it
only as the paper polling package in `docs/telegram_paper_mvp_activation.md`.
This slice does not deploy, merge, activate Watcher, or enable live trading.

See also: [telegram_security_protocol.md](./telegram_security_protocol.md) ·
[phase6_candidate_telegram_alerts.md](./phase6_candidate_telegram_alerts.md) ·
[strategy_conversation_foundation.md](./strategy_conversation_foundation.md)

## Loop

Watcher detects a meaningful event → durable `PaperNotificationIntent` →
Telegram outbox alert → bound user discusses with AlphaTrade → agent explains
canonical evidence, strategy, Candidate, and risk → mutating paper actions
remain identity-bound confirmation gated.

Meaningful Watcher events:

- `CONFIRMED_SETUP` with a persisted canonical Candidate
- fail-closed blocked scans (`stale_evidence`, `provider_outage`, `wrong_source`,
  `candidate_creation_failed`)

Empty successful scans are not alerts.

## Supported discussion

- Watcher alerts
- Candidate alerts (via existing `CandidateAlertGateway`)
- strategy discussion (not strategy approval)
- market context bound to canonical evidence
- paper trade status
- journal outcome
- learning summary

Facts are copied from existing authorities. LLM wording is not used in this
slice and cannot rewrite SetupAssessment, risk, or Candidate identity.

## What Telegram must never do

A Telegram message is never trading authority. The layer refuses:

- implicit strategy approval / compile / activate
- overriding SetupAssessment
- overriding risk (`BLOCK` stays final)
- minting a Candidate
- placing a live or paper order
- enabling live trading
- `CLOSE`
- `EXECUTE_PAPER_PLAN`

`APPROVE` remains protocol `AuthorizationIntent` only (`executes=false`).

A Watcher `CONFIRMED_SETUP` sends one message: the setup alert. Telegram
presents no Reject/Skip/Approve for it and issues no confirmation row or nonce,
so `I confirm reject`, or a pasted identity block, fails closed and nothing
changes. Replies to the alert still discuss that Candidate's thread.

Only a Candidate alert projected without a Watcher notice still presents
identity-bound actions: Reject/Skip need an exact presented confirmation
identity (footer, or `I confirm reject` / `I confirm skip` bound to one issued
nonce). Bare `I confirm` is not mutation authority.

End-user text is short plain English with bullets: no ids, hashes, enum names
or field names. Those stay in the stored records.

## Durability

- Dedup: deterministic identity hash → one notification intent and one outbox
  idempotency key (`paper-notify:` / `paper-thread:` / existing `candidate-alert:`).
- Delivery state: existing Telegram outbox (`PENDING` → `CLAIMED` → `SENT` /
  `RETRYABLE` / `DEAD_LETTER` → `ACKNOWLEDGED`).
- Retry: existing `deliver_pending` claim + transport idempotency.
- Restart: re-projecting the same semantic event converges on the outbox key.
  Paper identity/threads/confirmations also persist in
  `telegram_paper_*` (Alembic `d9e0f1a2b3c4`, which revises `e3f4a5b6c7d8`).
- Tenant isolation: organization + binding + account on every mutation.
- Rate limits: existing protocol callback/user/chat windows apply to inbound
  private messages.
- Audit: protocol audit events (`message_applied`, `action_applied`,
  `action_rejected`, replay conflict). Agent counters for execution / mint /
  live-enable attempts stay at zero.

## Flags

| Setting | Default |
|---------|---------|
| `TELEGRAM_INTERACTION_ENABLED` | `false` |
| `TELEGRAM_ALERTS_ENABLED` | `false` |
| `WATCHER_ORCHESTRATION_ENABLED` | `false` |
| `ENABLE_REAL_TRADING` | `false` |

Watcher `PERSIST_AND_NOTIFY` remains blocked (`notify_disabled`). The paper
worker may take an optional scan notification hook; the default is no hook
and no Telegram send from Watcher side effects.

## Not in this slice

- FastAPI Telegram webhook / secret-token header verification
- Enabling Telegram in staging or production
- Watcher activation
- Live trading
- LLM-authored setup truth
