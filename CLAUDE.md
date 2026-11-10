# Claude Code instructions

Read and follow `AGENTS.md` and `.cursor/rules/*.mdc`; they are canonical.

Before starting any work, read the "Multi-agent coordination (Claude Code, Cursor, Codex)"
section in `AGENTS.md`. Claude Code branches use the `claude/` prefix.

## Canonical model configuration

`backend/src/app/core/config.py` (`Settings`) is the single source of truth for model defaults:
`LLM_MODEL=gpt-6-luna`, `LLM_TIER_A_MODEL=gpt-6.1-sol`, `LLM_TIER_B_MODEL=gpt-6-luna`,
`AGENT_REASONING_MODEL=gpt-6-astra`, `AGENT_VOICE_REASONING_MODEL=gpt-6.1-sol`,
`EMBEDDINGS_MODEL=text-embedding-3-small`. `.env.example` mirrors those defaults
(`backend/tests/test_model_defaults_consistency.py` enforces it). Docs must not restate a
different default; dated evidence of what an environment ran at the time must say so.

## Prompt provenance

Every model call records the SHA-256 of its system prompt.
It is stored on `ModelCallAttempt.system_prompt_sha256` (with `prompt_id` / `prompt_version`), computed by
`ModelRouter` from the exact instructions text sent; calls without a system prompt record NULL.
System prompts live in `backend/prompts/*.txt` with a `# prompt-id:` / `# version:` / `---` header
(`app/core/prompt_registry.py`); bump `# version` whenever the body or a fragment composed into it
changes, and pass the record's `id`/`version` on `ModelTaskRequest`.
