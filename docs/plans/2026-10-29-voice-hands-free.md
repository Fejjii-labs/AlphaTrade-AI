# Hands-free voice (ChatGPT-style) plan

Branch: `claude/voice-hands-free`. Paper/demo only. The task owner asked for the work
directly, so implementation proceeds with this plan as the record (deviation from the
"plan PR first" rule is noted here for Griller).

## Backend (realtime path, `backend/src/app/agent_voice/`)
1. `protocol.session_update`: server VAD with `create_response: true` and
   `interrupt_response: true`; `tool_choice: "auto"` so simple conversational turns are
   answered directly; `alpha_trade_turn` stays the only tool. Instructions: never place,
   modify or cancel orders/trades by voice; point the user to the explicit on-screen button.
   `session.updated` verification now expects the new effective values.
2. Accounting: provider-created responses are admitted on `response.created` through a new
   `VoiceLedger.provider_response()` that counts them toward `maximum_responses`
   (interrupted/cancelled responses included). An over-cap response is still recorded
   (billable, leaves the row `uncertain`) and the session fails closed. Before a delegated
   turn can no longer fit (fewer than 2 response admissions left), the session ends.
3. Barge-in: `input_audio_buffer.speech_started` no longer sends `response.cancel`
   (the provider cancels); the backend forwards `playback.clear` + `speech.started`.
   `response.done` with status `cancelled` (and a literal `response.cancelled`) forward
   `response.interrupted`.
4. `maximum_turns`: new `AGENT_VOICE_MAXIMUM_TURNS` setting (default 20, hard bound 24).
   The receipt default rises from 6 to the setting; an explicit receipt value is an upper
   bound. When the receipt relies on the default, the effective value is the largest
   value between the legacy 6 and the setting whose reservation fits the session ceiling;
   anything that failed before still fails (`verified_cap_cannot_cover_session`).
   `maximum_responses` defaults to `2 * maximum_turns`.
5. Live path: instructions gain the same trade rule; same turn setting applies.
6. Audit + test: no voice-callable tool executes trades.

## Frontend
- `realtime-controller`: flush playback on `speech.started`/`response.interrupted`;
  expose live audio level.
- `realtime-audio`: `AnalyserNode` taps on mic and output, `level()` RMS.
- `VoiceOrb`: full-screen overlay (listening / thinking / speaking), level-driven scale and
  glow, End button, Esc closes, `prefers-reduced-motion` respected, no IDs.
- `RealtimeVoiceControls`: one "Talk to Agent" button opens the orb.

## Tests
Targeted pytest on voice modules; vitest for controller/audio/orb/controls; typecheck and
lint for changed files; `npm run api:generate` and check `pilot_sha256`.
