# GPT Live voice prototype

GPT Live is a disabled voice transport on the existing Agent voice stack. It does
not enable realtime voice, and it does not change the text agent's model.

`AGENT_LIVE_ENABLED` defaults to false. `AGENT_LIVE_LIMITS_FILE` defaults to
empty. `AGENT_REALTIME_ENABLED` stays false. No Render environment or flag was
changed. There is no microphone acceptance and no provider call in tests.

## Voice-scoped reasoning

Delegated backend reasoning on the voice path uses:

| Setting | Default |
| --- | --- |
| `AGENT_VOICE_REASONING_MODEL` | `gpt-6.1-sol` |
| `AGENT_VOICE_REASONING_EFFORT` | `low` |

The text agent still uses `AGENT_REASONING_MODEL` (default `gpt-6-astra`) and
`AGENT_REASONING_EFFORT` (default `high`). Voice budget receipts must name the
voice reasoning model. A receipt that names the text model is refused with
`verified_reasoning_route_mismatch`.

When `AGENT_REALTIME_ENABLED` is false, `/agent/voice/config` does not read or
validate a realtime limits file. An older receipt that still names
`gpt-6-astra` for `reasoning.model` therefore does not break disabled
deployments. If realtime is enabled, the verified realtime limits file must name
`AGENT_VOICE_REASONING_MODEL` (default `gpt-6.1-sol`), not the text agent model.

## Budget

A session is admitted only when all of the following fit the $1 session ceiling
(`session_ceiling_usd`, also capped by `remaining_budget_usd`):

- gpt-live-1 at **$0.05 per minute**, billed per second, including a 10-second
  close margin and a 4-second cleanup margin. The rate is not rounded up to a
  whole minute.
  Sources: [GPT-Live 1](https://developers.openai.com/api/docs/models/gpt-live-1)
  and [API pricing](https://developers.openai.com/api/docs/pricing).
- Reasoning reservations: `maximum_turns * 4 * (input tokens * input rate +
  AGENT_MAX_OUTPUT_TOKENS * output rate)`. Cached input is not assumed.
- Embedding reservations, when the receipt includes an embedding price:
  `maximum_turns * embedding input tokens * embedding input rate`.

gpt-6.1-sol standard short-context rates, fetched from the official model page
on 2026-10-22, are **$2 per million input tokens** and **$10 per million output
tokens**. Cached input on that page is $0.10 per million and is not used for
this reservation. Prompts over 272K input tokens are outside this example.
Source: [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol).

`text-embedding-3-small` on the same pricing page is **$0.02 per million
tokens**.

### Seconds that fit

For the example receipt in `docs/gpt_live_limits.example.json`
(2 turns, 20,000 reasoning input tokens, 2,000 embedding tokens) with
`AGENT_MAX_OUTPUT_TOKENS=4000`:

- reasoning hold = `8 * (20000 * $2 + 4000 * $10) / 1,000,000` = $0.64
- embedding hold = `2 * 2000 * $0.02 / 1,000,000` = $0.00008
- remaining for live audio = $0.35992
- billed seconds that fit = `floor(0.35992 / ($0.05 / 60))` = 431
- configured `maximum_seconds` that fit = `431 - 14` = **417**

418 configured seconds would bill 432 seconds and the total would exceed $1, so
admission is refused. The **417** figure is the largest configured
`maximum_seconds` whose full hold fits the $1 ceiling at
`AGENT_MAX_OUTPUT_TOKENS=4000`; it is not the runtime session length operators
should expect. The schema caps `maximum_seconds` at **300**, so the effective
maximum for this example receipt is **300 seconds** unless that cap is
deliberately raised in code and the receipt is updated to match. The repository
default `AGENT_MAX_OUTPUT_TOKENS` is 25000. That default does **not** fit this
example; the $1 ceiling then refuses admission.

## Expired holds without a final receipt

Voice reservations store `expires_at` in the ledger. While a hold is open,
quota treats `reserved_cost` / `reserved_tokens` as outstanding. If a socket
ends without a provider final receipt (for example a crash before
`live.finalized`), the row can remain `uncertain` and keep blocking quota.

`QuotaService` runs `release_expired_voice_holds` for the requesting organization
before it summarizes usage, so the same snapshot counts the charge. The sweep is
row-locked, flushes only (it does not commit unrelated work on the quota
session), and applies only to GPT Live rows after `expires_at` plus the
14-second close/cleanup margin. A row that never recorded a finalized Live
duration receipt is closed and charged its full `reserved_cost`. That charge is
a usage event (`agent_voice_expired_hold`), and `accounted_cost` is set to the
same amount. It is never `$0`: a crash must not drop the hold out of the $1
organization ceiling. A later final duration receipt deletes that conservative
charge and records the measured seconds instead, so the ceiling is not stacked.
Realtime rows, in-flight active sockets inside that margin, rows with a
non-null `accounted_cost`, and finalized Live receipts with unresolved secondary
delegation are left unchanged.

The example file contains no secrets. Dates are placeholders. A real receipt
must be unexpired, tenant-scoped, and cover the whole session plus the
14-second margin.

## What was salvaged

The session, ledger, socket, and protocol modules come from
[PR 257](https://github.com/Fejjii/AlphaTrade-AI/pull/257)
(`codex/gpt-live-integration`, also kept under `archive/salvage`). That pull
request was not modified. This branch starts from current `main`.

Provider contract notes: [gpt_live_provider_contract.md](gpt_live_provider_contract.md).
