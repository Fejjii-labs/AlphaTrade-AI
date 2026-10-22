# PR 257 — disabled GPT Live voice

- Source PR: https://github.com/Fejjii/AlphaTrade-AI/pull/257
- Head SHA: `b2a41ad88bc0fd306465e91d8709b9bf259fe09a`
- Status: partial

## What it does

A disabled GPT Live transport on top of the existing Agent voice stack. New modules:

- `backend/src/app/agent_voice/live_service.py` — full-duplex session, transcript fragments, delegation into `shared_turn`
- `live_accounting.py` — `LiveLedger` duration receipts on the existing voice budget
- `live_protocol.py` — session-start payload and context chunking
- `live_config.py` — `LiveLimits` / `gpt-live-1` price receipt
- `live_socket.py` — `/agent/voice/live` websocket; reads `settings.openai_api_key` and stays closed unless live mode is enabled

Wiring that is only the Live delta versus current `main` is included for `accounting.py`, `config.py`, `service.py`, `contracts.py` (`provider: realtime | gpt_live`), `api/routes/agent_voice.py` (websocket route `/live`), `RealtimeVoiceControls.tsx`, and `frontend/src/lib/voice/realtime-controller.ts` (captions, mute/resume, `/agent/voice/live`).

Tests: `test_agent_live_contract.py`, `test_agent_live_integration.py`, `test_agent_live_postgres.py`, plus the realtime controller and controls tests. Design notes: `docs/gpt_live_provider_contract.md` and `docs/gpt_live_integration.md`.

## Dependencies and assumptions

- Paper / disabled by design. `agent_live_enabled` defaults to false. No live orders, no microphone acceptance, no provider account.
- `backend/src/app/core/config.py` was **not** copied. On this PR it is behind `main` (about 174 lines `main` has that the branch lacks). To mount the slice later, add only:

  - `agent_live_enabled: bool = False`
  - `agent_live_limits_file: str = ""`

- Dependency extra from the PR, not copied as a lockfile: `openai[realtime]==3.28.0` (main pins `openai==3.28.0` without the extra).
- Depends on current voice types that still match this head: `VoiceLedger`, `shared_turn`, `VoiceStart` / `VoiceAudio` / `VoiceEnd`, `StrictModel`, organization quota tables.
- Postgres tests expect the existing voice schema and `NullPool` sessions. They were not re-run in this archive.
- Generated OpenAPI, `uv.lock`, and `docs/evidence/pr257-postgres/*` run logs were left out.
- Test settings use empty or obvious fixture values (`openai_api_key="fixture"`, a synthetic JWT string). They are not credentials.

## Why keep it

SFP, Nested Continuation, Telegram, watcher, and replay work from the other open PRs is already on `main`. This Live session path is not: `main` has realtime voice, and it does not have `LiveLedger`, `/agent/voice/live`, or the GPT Live caption/mute controller. The implementation is real (service, protocol, accounting, socket, tests) and intentionally disarmed.
