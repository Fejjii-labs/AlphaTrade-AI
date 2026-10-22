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
admission is refused. The schema still caps `maximum_seconds` at 300, which is
inside this budget. The repository default `AGENT_MAX_OUTPUT_TOKENS` is 25000.
That default does **not** fit this example; the $1 ceiling then refuses
admission. The 417-second figure is only for `AGENT_MAX_OUTPUT_TOKENS=4000`.

The example file contains no secrets. Dates are placeholders. A real receipt
must be unexpired, tenant-scoped, and cover the whole session plus the
14-second margin.

## What was salvaged

The session, ledger, socket, and protocol modules come from
[PR 257](https://github.com/Fejjii/AlphaTrade-AI/pull/257)
(`codex/gpt-live-integration`, also kept under `archive/salvage`). That pull
request was not modified. This branch starts from current `main`.

Provider contract notes: [gpt_live_provider_contract.md](gpt_live_provider_contract.md).
