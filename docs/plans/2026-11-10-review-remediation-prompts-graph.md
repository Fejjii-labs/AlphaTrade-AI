# Review remediation: prompt traceability and graph engineering (batches 3+4)

Branch: `claude/review-remediation-prompts-graph`, stacked on
`claude/review-remediation-rag-integrity` @ f08f17bb (draft PR #455). Owner: Claude Code.
Source: external AI-engineering review, items (4, deferred part), (5), (6), (11), (12), (15) and
the agent_capture strict-schema note. Supervisor-directed; Griller review happens on the PR.
Reuses the previous batch's `schemas/strict_json_schema.py` and `guardrails/citations.py`.

## Size note

The branch exceeds the ~400 changed-line guideline (most of it is tests, legacy prompt fixtures
and the regenerated OpenAPI snapshot). Each item is one commit, so a split is a cherry-pick:
prompts (6 then 5, because 5 uses the prompt ids), graph (15, 11, 12), citations (4 plus the
OpenAPI regen), capture strict schema.

## Decisions

1. **Versioned prompt files (6).** `backend/prompts/<id>.txt` starts with
   `# prompt-id: <id>` / `# version: N` / `---`. The body after the separator is used verbatim
   (no newline normalization). `app/core/prompt_registry.py` returns
   `PromptRecord(id, version, text, sha256)`. `prompt-id` must equal the file stem.
   `narrative_service.load_prompt()` delegates to it and still returns `str`. The three
   narrative prompts got a v1 header; their text is unchanged.
   - Moved: interactive `_SYSTEM` → `interactive_agent_system`; capture `SYSTEM` →
     `agent_capture_system`; voice `TRADE_REFUSAL`, `GROUNDED_FACTS`, `INSTRUCTIONS` (realtime)
     and live `INSTRUCTIONS` → `voice_*`.
   - Voice keeps its composition, so each fragment has one source: the two instruction
     templates contain `{{grounded_facts}}` / `{{trade_refusal}}` exactly once (`render_prompt`
     enforces this), and live still applies its two `.replace` calls to `GROUNDED_FACTS`. The
     exported names and values are unchanged, and loading happens at import from the packaged
     file.
   - Byte-identity: legacy values were captured into `tests/fixtures/prompts_legacy/*.v1.txt`
     before the move. A test asserts equality while a prompt is at version 1. Editing a body
     without bumping the version therefore fails the test. After a bump, the v1 snapshot is
     skipped for that prompt.
   - Packaging: `backend/Dockerfile` already copies `prompts` to `/app/prompts` (the narrative
     prompts used it). `render.yaml` builds from that Dockerfile. No change was needed.
2. **Prompt hash on every model call (5).** `ModelRouter._record_attempt` is the only place a
   `ModelCallAttempt` is built. The router hashes `providers.llm.responses_instructions(messages)`:
   system and developer parts joined with `\n\n`, exactly the Responses `instructions` text. For
   the usual single system message this is also the Chat Completions system content. The hash is
   recorded on every attempt, including failed ones. `prompt_id` / `prompt_version` come from new
   optional `ModelTaskRequest` fields, which conversation, capture and narrative now pass. A call
   with no system prompt records NULL. Persisted through `ModelCallTelemetryService` into three
   new nullable columns. `persistence_firewall` classifies whole entities (`ModelCallAttempt` →
   USAGE) and has no column allow-list, so nothing changed there.
   - **Migration `a28promptsha001` (HIGH-STAKES):** adds `system_prompt_sha256 VARCHAR(64)`,
     `prompt_id VARCHAR(80)` and `prompt_version INTEGER`, all nullable, to `model_call_attempts`.
     No default, backfill or constraint. The downgrade drops them. `down_revision =
     a27obsretention001`, and it is the single head. The head-pin test in
     `test_account_equity_stop.py` was extended, as previous migration PRs did.
   - The narrative *rendered* system text (template plus context JSON plus task marker) is what
     gets hashed. Its hash therefore differs per call, while `prompt_id` / `prompt_version`
     identify the template.
3. **Compile the graph once (11).** `get_agent_graph()` compiles once per process under a lock
   (double-checked). The per-request `AgentRuntime` travels in the invoke config
   (`configurable["agent_runtime"]`). The node wrapper reads it there (LangGraph 1.2.2 injects
   `config` into nodes that declare it). `turn.node_scope`, the durable chat turn's per-node
   phase runtime, still takes precedence, so the phase session, detached quota/audit and provider
   ports are unchanged. Invoking without a runtime raises. `AgentService.invoke_graph()` replaces
   `service._graph.invoke`.
4. **Node registry once (15).** `node_names` was deleted; registration iterates `node_fns`.
5. **Typed graph state (12).** `StateGraph(AgentGraphState)` uses a TypedDict with one channel
   per `AgentState` field (JSON-mode value types). Nodes still use `parse_state` / `patch_state`.
   **No reducers.** Every node returns the full validated state (verified at runtime across six
   turn paths), so an `operator.add` reducer would append each full list to itself. A deliberate
   reducer grew one seeded history entry to 16384. All channels keep last-value (replacement)
   semantics, identical to the old root-dict channel. Tests pin keys == `AgentState` fields,
   forbid reducer metadata, and check seeded lists pass through the graph unduplicated.
6. **Citation verification on `/agent/turns` (4, deferred part).** The reply is plain-text model
   prose with no structured output, so verification is marker-based. `build_knowledge_context`
   returns the structural label→chunk map (`K1..Kn`). A label counts as supplied only if it is in
   that map **and** its passage line is inside the bounded facts actually sent (16000 chars,
   shared constant with `compose()`). User-uploaded text therefore cannot add a reference.
   Unknown markers are stripped from `full_reply` and flagged in `citation_violations` plus a
   limitation, reason `citation_not_in_retrieved_set`. Valid markers become
   `AgentTurnResult.cited_chunk_ids`, which is also stored in the assistant payload. If sources
   were supplied but nothing is cited, a limitation note says the reply is not presented as
   grounded. This is not a failure. If no sources were supplied and there are no markers, nothing
   is flagged. The check applies on both compose paths: durable `run_interactive` (HTTP and voice
   `alpha_trade_turn` delegation) and the in-session service. No voice code was changed. The
   OpenAPI snapshot was regenerated.
7. **agent_capture strict schema.** The capture call now uses
   `strict_response_format("agent_capture", CapturePlan)`. The raw schema was already
   strict-compatible and is identical to the helper output, so today's request does not change.
   The helper guards against a future defaulted field. Explicit nulls parse.

## Deferred / out of scope

- **Voice realtime/live session instructions hash.** Those sessions create no `ModelCallAttempt`
  rows; recording would need a new record-creation point in voice session code (excluded).
  Voice delegation (`alpha_trade_turn`) goes through `ModelRouter` and is covered.
- **Narrative direct-provider path** (`NarrativeService` without a router) creates no
  `ModelCallAttempt` at all, so there is nothing to attach a hash to. Production wiring sets the
  router via `AgentRuntime.model_router`.
- **Graph reducers.** These need nodes to return partial updates instead of full state. That is a
  node-semantics change and was not made.
- **Structured interactive output.** `/agent/turns` would need a JSON reply contract shared with
  voice. Marker verification is the interim control.
- `ModelTaskRequest.prompt_template_version` (pre-existing, unused) was left in place. The new
  `prompt_id` / `prompt_version` are the populated fields.
- `backend/src/app/agents/prompts/system.md` is unreferenced (pre-existing) and was not touched.

## Griller conditions (review of eec8d05b)

- **C1.** `interactive_agent_system` is now **v2**. One sentence changed: bracket labels such as
  `[K1]` are cited inline, only for labels that were supplied, and detailed chunk ids and
  identities stay in Stored evidence. Every other sentence is byte-identical to v1, and a test
  checks v2 == v1 with that one edit. The knowledge facts prefix asks for the same bracket form.
  Citation-audit limitations (the uncited-sources note and the removed-marker note) are filtered
  out of what the voice model receives by `contracts.speech_limitations`. That covers the live
  commentary and the realtime `function_call_output`. The durable result, the `turn.result`
  client event and the ledger keep them. This supersedes "No voice code was changed" in
  decision 6.
- **C2 (code part).** `a28promptsha001` runs `SET LOCAL lock_timeout = '5s'` before its DDL and
  `SET LOCAL lock_timeout = DEFAULT` after it, in both upgrade and downgrade, on PostgreSQL only
  (`op.get_context().dialect.name`, which also works offline). env.py runs the migration in one
  transaction, so the setting cannot outlive that transaction. A blocked `ADD/DROP COLUMN` fails
  after 5s instead of queueing traffic behind it. Rerun the deploy once the blocking transaction
  is gone. The ordering problem remains: new app code needs the migration applied before it
  serves traffic, and that is an operational condition.
- **C3.** `strict_json_schema` emits only keywords that OpenAI strict mode supports. It drops
  `minLength`/`maxLength`/`uniqueItems`/`contains`/`min|maxContains`/`min|maxProperties`/
  `patternProperties`/`propertyNames`/`unevaluated*`/`dependent*` and any `format` outside
  {date-time, time, date, duration, email, hostname, ipv4, ipv6, uuid}. It raises
  `UnsupportedStrictSchemaError` at build time for `allOf`/`not`/`if`/`then`/`else`. A
  single-element `allOf` is collapsed into its node instead (Pydantic 2.13 emits none for
  capture or narrative). `enum`/`const`/`examples` values are copied verbatim. Constraint
  enforcement now relies on server-side Pydantic validation:
  `CapturePlan.model_validate_json` and `TradingNarrativeDetail.model_validate` still reject
  an over-long title. The capture request body therefore changes: `minLength`/`maxLength` are
  gone.
