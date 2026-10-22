# AlphaTrade GPT Live integration

REVIEW_REQUIRED: implemented and mocked; activation disabled; live microphone acceptance
pending. Branch `codex/gpt-live-integration` starts exactly from reviewed PR249 candidate
`de5c010c7c85dfe5cde1ec8fabf60b6262500fe0`, tree `2ebed239ecaeacbe32c4a4d50a1e8459c4ef4122`.
Controlling evidence: [PR249](https://github.com/Fejjii/AlphaTrade-AI/pull/249) and its
[current release handoff](https://github.com/Fejjii/AlphaTrade-AI/blob/cursor/experiments-market-handoff/HANDOFF.md).
Release CI, native execution and deployment remain separate tasks.

## Behavior and existing authorities

The existing authenticated voice configuration selects `/agent/voice/live` only when
`AGENT_LIVE_ENABLED` is enabled and `AGENT_LIVE_LIMITS_FILE` binds a valid tenant-scoped,
dated duration/Agent/optional embedding price and existing budget receipt. Both new
settings default false/empty. No runtime/operator settings were changed. The existing
Realtime route and browser dictation fallback remain available under their existing gates.

The server-primary GPT Live WebSocket proxy keeps credentials private, validates Origin,
JWT/role/conversation ownership and organization quotas before provider connection, and
rechecks authorization/expiry during capture. Native SDK retries are disabled. Continuous
PCM24 capture/playback, voice controls, conversation creation and canonical pending/result
callbacks are reused. Input/output captions carry provider timestamps in the private voice
reservation ledger. Captions are evidence/display context, not canonical Agent responses
or proof of complete utterances. AlphaTrade stores no raw audio; provider `store` is false.

Client delegations call existing `shared_turn` → `TurnCoordinator` → `run_interactive`:
scoped saved rules, selected strategies, Journal, Knowledge/native account grounding,
ModelRouter, capture, canonical history, quota and idempotency authorities are reused.
The original complete Agent request/body/key must be retained by the browser before
execution. Confirm/Reject controls remain explicit; speech cannot confirm or trade.
Duplicate delegations do not rerun work. Delegations serialize; corrections fence queued
work and suppress obsolete result injection into Live. Already admitted canonical work
stays durable for explicit original-key recovery. Missing transcript text waits at most
two seconds for matching fragments, otherwise requests repetition/typed input. This wait
never proves an utterance complete. Results preserve limitations in append commands of
at most 480 UTF-8 bytes, within the official 500-token bound.

Input caption arrival flushes queued playback. Interrupt immediately clears/mutes local
output and sends a trusted stop-speaking instruction. Explicit Resume audio controls
future playback; an instruction acknowledgment never unmutes. Live has no response.cancel,
item truncate, transcript-completed or output-audio-done event. Acoustic barge-in latency,
actual spoken grounding and echo remain unverified; discarded speech may remain in model
context. Provider API/SDK verification: [gpt_live_provider_contract.md](gpt_live_provider_contract.md).

End, hiding, context/navigation, logout and unmount stop local capture/playback and fence
late callbacks. One provider reader survives browser loss, revoked authorization and
rejected commands to drain final usage. The close command and drain share a 10-second
deadline; transport close handshake is 1 second. Browser drain is capped at 12 seconds.
Missing final usage is disclosed and remains unconfirmed. Canceling a waiting coroutine
cannot forcibly cancel an admitted thread; the original canonical key/body governs recovery.

## Accounting and availability

Live usage is cumulative session seconds, including silence:12 then15 means15, not27.
One idempotent `agent_voice_live` UsageEvent updates provider-observed seconds times the
verified USD/second receipt, with static-estimated cost and no fabricated voice tokens.
Agent reply/capture and embedding costs stay in existing records. Admission reserves
maximum duration +10 seconds close +4 seconds authorization/guard/transport margin, plus existing bounded
secondary attempts. Receipts must cover that whole period. No second quota/billing system
or price catalog is introduced. Known final voice-only duration can settle its hold;
missing/invalid/regressed final usage and unresolved secondary costs keep conservative
reservations. The approved budget-reference debit is never erased at close. Closing now
classifies and settles under one row lock/commit, preventing a temporary released
hold for unconfirmed silence. Exact final receipt replay also validates the
original provider session and terminal reason before accepting equal duration.

`openai[realtime]==3.28.0` preserves the SDK version and resolves WebSockets 15.0.1 within
its declared `<16` range. HTTP schema/types/validators were regenerated for the provider
selector. No migration is needed: existing voice reservation JSON and usage rows are reused.
No paid provider call, supported-model probe, exchange action, deployment, full suite or
workflow change occurred. Price/access availability and model quality are unverified.

## Focused mocked evidence

Published [PR257](https://github.com/Fejjii/AlphaTrade-AI/pull/257), implementation
`0036f88cb3e1939b1478159e1fefa8260fe11056`: **68 backend and 102 frontend mocks
passed, zero skips**. Targeted Python types (8 files), frontend types, changed
lint/format, API drift and diff checks passed. Only these files are selected:

```sh
# backend
OPENAI_API_KEY='' timeout 90 .venv/bin/pytest tests/test_agent_live_contract.py tests/test_agent_live_integration.py tests/test_agent_realtime_protocol.py -o addopts='' -q --show-capture=no
# frontend
npx vitest run src/lib/voice/realtime-controller.test.ts src/lib/voice/realtime-audio.test.ts src/components/agent/RealtimeVoiceControls.test.tsx src/components/agent/AgentWorkspace.test.tsx src/lib/api/generated-contracts.test.ts
npx tsc --noEmit --pretty false --incremental false
npm run api:check
```

Changed Python lint/format, targeted Python types, changed TypeScript ESLint, API drift
and diff checks accompany the mocks. In-memory SDK sockets and disposable SQLite verify
protocol, shared canonical history/usage/replay and boundaries; they do not prove actual
microphones, model quality or PostgreSQL concurrency. The async fixtures need the managed
executor network namespace for worker-thread event-loop wakeups, but use only fake/local
transports. The PostgreSQL continuation closed that gap on a disposable PostgreSQL 16.15
instance: **all seven formerly skipped quota cases plus eleven new LiveLedger
cases passed (18/18, zero skips)**. Two finalization corrections were verified by
**13 affected mocked checks (zero skips, 10 intentionally deselected)**; unchanged
mocked checks above are retained at their original recorded revision. Exact
JUnit/stdout receipts, source hashes, before/after failures and cleanup evidence:
[PostgreSQL receipts](evidence/pr257-postgres/README.md).

## Exact live microphone acceptance still required

Separately authorized acceptance must provide project access to `gpt-live-1`, the configured
Agent route and verified scoped receipts covering duration and secondary calls within the
existing $1 session ceiling. This implementation PR grants no activation/deployment.
Record exact application revision, browser/device, conversation/session IDs, capped spend,
timestamps, latency, final usage and quota receipts for each item below:

1. On the intended desktop browser, desktop Safari and physical iPhone/Safari with HTTPS,
   grant microphone access. Speak at least three consecutive requests without restarting:
   a provisional saved risk rule, strategy/Knowledge or Journal follow-up, and read-only
   native account evidence. Verify same-conversation canonical storage, citations/provenance,
   uncertainty, stale/unavailable facts and corrections. No speech confirmation/execution
   or operator mutation. Restart a session and restore saved context without replaying actions.
2. Correct a request while Agent work is pending and while speech is audible. Measure
   acoustic barge-in, queued speech discard and speaker/headphone echo; obsolete results
   must not be spoken/injected. Verify delayed fragments never establish false completeness.
3. Press Interrupt during output: immediate silence, microphone continues, no output until
   explicit Resume audio, no old queued audio after resume. Check behavior when discarded
   speech remains in model context; a context ack is not playback completion.
4. End, change conversation/strategy context, hide/background the tab/iPhone, log out and
   leave the page. OS microphone indicator/playback must stop; old callbacks must not touch
   a new session. Test permission denial/revocation, absent microphone and unsupported input.
   Typing/dictation fallback and typed draft preservation must work.
5. Break browser/provider connection and revoke/expire authorization during a pending turn.
   Recover original request/body/key explicitly to one canonical result, without an automatic
   retry. Closing caption/error events must not block final usage; missing finalization must
   stay unconfirmed with the hold retained.
6. Reach duration/audio/turn limits and exhausted organization quota. Verify forwarding and
   admissions stop, resources close, final `session.closed.usage.seconds` matches cumulative
   storage, and Live estimate plus separate Agent/capture/embedding records/holds reconcile.
   Quota exhaustion must refuse before provider connection. Unknown secondary costs remain held.

Implementation acceptance: **6/6** for this bounded scope; live microphone acceptance
**0/6**. Fixed implementation checklist (6): official contract; continuous voice/interruption;
shared grounding/storage; cleanup/recovery; quota/duration accounting; focused mocked
checks/reviewable PR. Live microphone acceptance is separate and currently 0/6.
Mac/iCloud handoff sync is unavailable in this cloud environment.
