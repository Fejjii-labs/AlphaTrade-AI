# Grade-based risk policy — arming checklist

Griller's verdict on PR #278 at `bc9041f09fef55517056a0da8238797ec9119cb8`
is **MERGE WITH BOTH FLAGS OFF**.

`autonomous_internal_paper_enabled` and `autonomous_blofin_demo_enabled`
stay false. Real-money trading stays blocked. This checklist does not arm
either flag, and it does not authorize a merge.

Decision: [AT-ADR-109](../.ai/DECISIONS.md) in `.ai/DECISIONS.md`, which
extends AT-ADR-107 and AT-ADR-108.

The rows below are **HARD PREREQUISITES**. A row is binding at its trigger.
Checking the box in a review does not satisfy the row. On this head every
prerequisite is still open.

## (a) Before ANY proof issuer or apply use

Trigger: before a server issues an apply proof, and before apply is used,
including a paper or demo profile apply.

| Prerequisite | Required state |
| --- | --- |
| Server-issued, single-use proof | The server issues the proof. A stored nonce makes it single-use. TTL is 5 minutes or less. |
| Real verification | The server checks a TOTP or WebAuthn step-up, or a Telegram confirmation from the pinned chat id. |
| Separate signing key | The proof signing key is not `jwt_secret`. |
| Bound proposal | `proposal_id` is required and matches the proposal being applied. |

Current state on `bc9041f0`: the proof only fails closed. A missing or
invalid proof is rejected. There is no issuer, no stored nonce, and no
separate signing key. `decode_apply_proof` verifies with `jwt_secret`. A
token that omits `proposal_id` is filled with the request's proposal id.
`actor=user` is a label on the access token. It is not a TOTP, WebAuthn, or
Telegram check.

## (b) Before exposing drawdown reset

Trigger: before a route or any other caller can reset a drawdown latch.

| Prerequisite | Required state |
| --- | --- |
| Same proof as apply | Reset requires the proof from section (a). |
| No legacy token | A token with no `actor` claim is refused. |

## (c) Before ANY grade goes above 1%

Trigger: before a tradable grade risk is allowed above the 1% placeholder,
on apply or on sizing.

AT-ADR-111 (Sofien, 2026-10-18 17:20 CEST) amends this section. The audited
field `paper_demo_data_collection` is the only exception, and only on a
stored `internal_paper` or `demo` profile. Other kinds stay on this
prerequisite. It does not close (c) for live trading or for arming either
autonomous flag. The human proof, loosening cooldown, breakers, daily and
open-risk limits, and the real-money block stay in force.

| Prerequisite | Required state |
| --- | --- |
| Server-computed report | The server computes the promotion report per grade from stored samples. |
| No caller flags | The caller does not supply eligibility flags. |
| Not one org-wide report | One report does not cover the whole organization. |

## (d) Before arming

Trigger: before either autonomous flag is set true, in any environment.

| Prerequisite | Required state |
| --- | --- |
| Append-only audit tables | Database triggers make breaker intervals, cancellations, promotion reports, and breaker resets append-only. |
| Cancellation scope | A cancellation commits only its own work. |
| Breaker during the wait | A breaker that opens and closes between apply and `effective_at` also cancels that change. |

On `bc9041f0`, profile versions have a Postgres immutability trigger.
Intervals, cancellations, promotion reports, and resets are refused by ORM
listeners. Those four tables do not yet have the database triggers above.
`_cancel_version` commits the whole session. A persisted window cancels a
loosening when the window covers `effective_at`. A window that has already
closed before `effective_at` does not.

## (e) Follow-ups

These follow-ups are required. They do not replace (a)–(d).

| Trigger | Work |
| --- | --- |
| Before a downgrade of `a17riskpolicy002` is relied on | Refuse that downgrade while cancellation, promotion-report, or breaker-reset rows exist. `a16` and `a18` already refuse when their guarded rows exist. |
| Before a client uses `/risk/policy/*` | Add route tests for those routes. |
| The next edit of `backend/src/app/core/config.py` | Correct the stale comment at lines 205–209. This checklist does not edit that file. |

## Wiring pull request

These stay in a later wiring pull request. This checklist does not authorize
that work. `ExchangeMarginWriter` stays unwired.

- an atomic budget reservation
- server-side open-risk computation
- the isolated-versus-cross margin mismatch on the demo path
