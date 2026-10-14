# GPT Live provider contract

Verified on 2026-10-13 against OpenAI's current documentation and the repository's
pinned official Python SDK, `openai==3.28.0`. Evidence here and in
`backend/tests/test_agent_live_contract.py` is offline protocol evidence, not
microphone acceptance or evidence of an enabled provider account.

## Official references

- [Primary WebSocket reference](https://developers.openai.com/api/reference/resources/live/primary-websocket)
- [WebSocket audio and startup guide](https://developers.openai.com/api/docs/guides/voice-websockets?api=live)
- [Session lifecycle, transcripts, and graceful close](https://developers.openai.com/api/docs/guides/live-conversations)
- [Client delegation and context updates](https://developers.openai.com/api/docs/guides/live-delegation)
- [Migration from Realtime and existing agents](https://developers.openai.com/api/docs/guides/live-migration)
- [Application playback controls and safeguards](https://developers.openai.com/api/docs/guides/voice-server-controls?api=live#control-playback-when-needed)
- [Live model, duration pricing, and concurrent-session limits](https://developers.openai.com/api/docs/models/gpt-live-1)
- [Python Live connection reference](https://developers.openai.com/api/reference/python/resources/live/methods/connect)

## Transport and startup

Use the trusted backend's bearer credential at
`wss://api.openai.com/v1/live/sessions`, without query parameters. Credentials must
stay on the server. The Python SDK's `client.live.connect()` creates the socket;
the application then sends `session.start`. The model belongs in the startup
payload, not `connect(model=...)` or a URL query.

AlphaTrade uses this startup configuration:

```json
{
  "type": "session.start",
  "session": {
    "model": "gpt-live-1",
    "audio": {
      "format": {"type": "audio/pcm", "rate": 24000},
      "output": {"voice": "marin"}
    },
    "delegation": {"type": "client"},
    "store": false,
    "instructions": "Application-authored conversation and delegation policy",
    "input": []
  }
}
```

Wait for `session.started` before audio or other application commands. Its
`session.id` and `expires_at` identify the running session. Send continuous raw
mono PCM16LE audio as base64 in `session.input_audio.append.audio`; input and
output share the chosen format. Play `session.output_audio.delta.delta` in
delivery order. Primary output audio has no event ID, timing fields, or
audio-done event. Sideband output timing is a separate interface.

Startup history supports user/developer `input_text` and assistant
`text`/`output_text`, one text part per message; the service limits it to 128
messages and 8,192 rendered tokens. Voice/model/audio are immutable after startup.
Live provides no Realtime-style audio commit, voice response trigger, response
token cap, or truncation setting. `session.update` only changes supported
Responses delegation settings within the current delegation mode.

## Agent delegation and transcripts

`session.delegation.created` supplies a delegation ID, `target: "client"`, and
`offset_ms`. It supplies no utterance or task text. Preserve the ID unchanged
when returning verified Agent results. Use recent transcript history and saved
application state to interpret short replies and corrections. Retain an
unresolved notice while context is unclear rather than inventing its request.

Input and output transcript events contain `event_id`, `delta`, `start_ms`, and
`end_ms`. Accumulate each speaker's text verbatim in delivery order, preserving
spaces and repetitions. Speakers can overlap. Timestamps describe approximate
fragment intervals; they are not exact word boundaries. There is no item ID,
turn-done event, or guarantee that receipt of a delegation includes all transcript
fragments for the request. A fragment spanning an offset cannot be split into
exact words using timestamps alone. A delivery gap is not proof of silence.
Application grouping and settling are heuristics; saved context and revision
checks must prevent outdated results from being announced.

Return plain-string results through `session.commentary.append`, with the
original `delegation_id`; Live can paraphrase them. Use
`session.thinking.append` for relevant facts/progress without requesting immediate
speech. Each append is limited to 500 tokens. A required nullable `delegation_id`
must be null for session-wide context. Keep secrets and private reasoning in the
Agent backend: thinking context can influence later speech. Multiple updates
may use the same known client delegation ID.

The corresponding `*.appended` events correlate through `client_event_id`.
They acknowledge estimated context injection; they establish neither exact
spoken wording nor playback completion. Context acknowledgments can remain
pending when the session timeline stalls. Handle rejected commands alongside
acks; an error's `error.client_event_id` identifies its command when present.

## Interruptions, microphone, and playback

The Live SDK has no provider `response.cancel`, output-clear, item-truncate,
delegation-done/cancel, speech-started, or output-pause event. Application playback
controls must immediately discard queued audio and suppress stale output during
an interruption. Send a trusted `session.instructions.append` with
`delegation_id: null` to redirect the voice model. Do not copy untrusted transcript
text into trusted instructions. Cancel application-owned Agent work where
supported and ignore late results whose revision is obsolete.

An instruction ack is not a playback-resume signal. Resume under the
application's recovery policy after stale audio has been cleared. A corrective
instruction cannot retract speech already heard. Dropped speech can remain in the
model's context. Live also provides no output VAD/noise gate; application silence
or playback tracking establishes candidate speech segments, not completion of an
entire answer.

`session.input_audio.mute` and `session.input_audio.unmute` acknowledge with
`session.input_audio.muted` and `session.input_audio.unmuted`, correlated by
`client_event_id`. These commands affect model input. Local recording/playback
requires separate device controls. Muting leaves the voice session and backend
work running, so it does not establish an idle or billing boundary.

## Accounting and cleanup

`session.usage.updated.usage.seconds` is cumulative Live audio duration. Updates
of 12 then 15 seconds mean 15 seconds total, not 27. Keep delegated Agent model
and tool usage separate. The official model price at verification time is
USD 0.05 per minute billed per second, without rounding to a whole minute;
AlphaTrade admission still requires its scoped, dated budget/price receipt.

Maintain a single provider reader. Before `session.close`, arrange to receive
the terminal event. Stop new session work, continue reading pending events, and
persist `session.closed`'s final duration, reason, and snapshot before releasing
the socket. Final session snapshots retain `status: "active"`; the terminal
event type identifies closure. Known reasons are `close_requested`, `expired`,
`content`, `remote_hangup`, and `connection_lost`.

If the terminal event is absent when the socket fails or the application close
timeout expires, retain observed usage and mark finalization/usage unconfirmed.
A socket close alone proves neither successful finalization nor zero charge.
Client delegation work must be finished or canceled separately. A new socket is
not a restored session; rehydrate saved text/task state and fence late results.

## SDK and focused evidence

The pinned SDK contains `openai.resources.live.live.AsyncLiveConnection`, the
native connector, and the typed Live client/server events. The official guide
requires a Python SDK with Live support and installation of `openai[realtime]`;
it does not name a numerical minimum version. SDK 3.28.0's declared transport
extra requires `websockets>=13,<16`. The integration retains SDK 3.28.0 and uses
that extra to resolve a compatible transport rather than relying on a transitive
WebSocket package outside the declared range.

Focused tests validate the application startup payload and representative
commands using the SDK's Pydantic adapters, reject Realtime-only/incomplete
commands, decode representative typed server events, and run the native SDK
against an in-memory connector to inspect URL, authorization, serialization,
and cleanup. They contact no provider, use no microphone, and incur no charges.
They cannot prove provider model availability, transcript latency, spoken
grounding, interruption timing, actual playback, or billing reconciliation.
