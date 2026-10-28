# UI overhaul B7a: Voice-scoped admission gate (Track V)

Agent: Claude Code · Date: 2026-10-27 · Status: Done (PR open, not merged)
Plan: `docs/plans/2026-10-26-ui-overhaul-plan.md`, item **B7a**, under Griller
condition **K2**. It needs a **separate Griller security review** before any merge.
Branch from `main`. Scope: `backend/src/app/agent_voice/` plus tests. No route,
schema or env value changes. Every voice limit is unchanged.

## What changed

- **One gate, three places.** New `voice_route_refusal(settings)` in
  `agent_voice/config.py` replaces the `provider_mode == "live"` check in all
  three places K2 names:
  - `live_config.load_live_limits` (GPT Live)
  - `config.load_limits` (the realtime path, was `config.py:144`)
  - `retrieval.embedding_limitation` (was `retrieval.py:39`)

  It refuses a missing key (`provider_credential_unbound`) and an explicit
  `PROVIDER_MODE=mock` (`approved_live_route_unavailable`). It admits
  `fallback` and `live`. All the other checks stay exactly as before: the voice
  flag, the limits file, the receipt's validity and expiry, receipt coverage,
  the reasoning and embedding model match, and the cost cap.
- **Fail closed: mock or fallback output never reaches a voice reply.**
  - `voice_llm()` rebuilds the shared `OpenAILLMProvider` as `fail_closed=True`
    with no fallback. Outside staging the shared provider always carries a
    `MockLLMProvider` fallback, even when `fallback=None` is passed.
  - `voice_llm()` and `VoiceReasoningProvider` refuse a `MockLLMProvider`
    (`voice_reasoning_mock_refused`).
  - `VoiceReasoningProvider.complete` now also rejects any result whose
    `provider` is a mock, on top of the existing `fallback_used` / model /
    token checks (`voice_reasoning_response_outside_receipt`).
  - `VoiceEmbeddingsProvider` refuses `MockEmbeddingsProvider`
    (`voice_embedding_mock_refused`). It already rebuilt the OpenAI embeddings
    route fail-closed.
- `agent_live_enabled` is added to `test_pr249_feature_flag_defaults.py`.

## Retrieval decision (K2, for Griller)

I adopted the plan's proposal as written. **Voice vector retrieval uses the same
voice gate plus its own verified embedding receipt.** Without a valid,
unexpired receipt whose model matches `EMBEDDINGS_MODEL`, vector retrieval is
off. The turn carries the existing "Vector embedding unavailable: …" note. It
never falls back to mock embeddings: `VoiceEmbeddingsProvider` refuses a mock
instance and rebuilds the OpenAI route with `fail_closed=True` and
`require_usage_receipt=True`. Under `PROVIDER_MODE=mock`, retrieval is off
("approved live embedding route is unavailable").

## Decisions taken without asking

- **Mock mode is refused, not only "not live".** K2 replaces the `live` check.
  It does not open voice to `PROVIDER_MODE=mock`, whose whole purpose is mock
  providers.
- **Defense in depth, without changing the router.** The router already sends
  `allow_internal_fallback=False`, so a reasoning failure is reported as "reply
  unavailable". The rebuild and the refusals make the voice route safe even if
  a future caller changes that.
- **Coordination with draft #314** (xAI Grok realtime path). It also edits
  `agent_voice/config.py`. My change there is one helper plus a 2-line swap in
  `load_limits`, so a conflict would be small and mechanical. I did not comment
  on #314: that is another agent's PR, and the task does not authorize writing
  to it. This note is the coordination record.
- **Env and enablement are untouched.** Staging stays `PROVIDER_MODE=fallback`.
  `AGENT_LIVE_ENABLED` and the receipt are Sofien's step (B7d), and nothing here
  turns voice on.

## Acceptance criteria (plan B7a + K2)

- [x] Fallback + flag + key + receipt is admitted
      (`test_live_voice_is_admitted_under_fallback_with_flag_key_and_receipt`).
- [x] A missing flag, key (empty or blank) or receipt is refused, mock mode is
      refused, and a model mismatch is refused (parametrized test). The realtime
      path uses the same gate.
- [x] **Proof of fail-closed:**
      `test_fallback_mode_voice_turn_fails_closed_and_never_uses_mock` sets
      `PROVIDER_MODE=fallback`, voice on, the real provider factory, and an
      upstream that fails at the transport. The reply is the unavailable notice,
      and `MockLLMProvider.complete` and `MockEmbeddingsProvider.embed_with_metadata`
      are **never called**. Unit tests also cover the mock provider refusals,
      mock or fallback results, and the fail-closed rebuild.
- [x] All existing `test_agent_live_*`, realtime, voice retrieval and voice
      quota tests pass.

## Test evidence

From `backend/`:

- `uv run pytest tests/test_agent_voice_gate.py` → 14 passed.
- Mutations:
  - With the mock-mode refusal disabled → 3 failed.
  - With the fail-closed rebuild disabled → 1 failed.
  - Restored → 14 passed.
- `uv run pytest tests/test_agent_live*.py tests/test_agent_realtime*.py tests/test_agent_voice*.py tests/test_voice_reservations_quota.py tests/test_pr249_feature_flag_defaults.py`
  (Postgres 16 for the `*_postgres` modules) → **191 passed**.
- `uv run ruff check .` → clean. `uv run ruff format --check .` → clean.
- Full backend gate (Postgres 16, local, 4 file shards, after rebasing on
  current `main`): 6,558 passed. 22 failed or errored, all from the local
  harness. The shard script renames the test DB to `alphatrade_test_gwN`,
  which `test_execution_account_registration.py` and three migration tests
  refuse by design. `test_pr237_migration_aware_rollback` needs historical
  commits missing from the shallow clone. Re-run alone with those fixed →
  **25 passed**.
- `mypy src/app/agent_voice` reports 16 errors in 3 files. `main` has the same
  16, so none are new here.
- No API schema change, so `api:check` is not affected.

## Next steps

Separate Griller security review (K2). Sofien merges. B7d (env and receipt on
staging) is Sofien's action. Then B7b (voice UI).
