# Testing and evaluation

AlphaTrade uses unit/integration regressions, frontend browser checks, offline evaluation scripts and separately supervised runtime acceptance. Each answers a different question. This guide reflects main `b58beda`, October 7, 2026; no application suite or broad CI dispatch was run for the documentation change.

## Choose the evidence you need

| Layer | Repository tooling | What it establishes |
| --- | --- | --- |
| Deterministic backend behavior | pytest, Ruff, scoped strict type checks where used. | The selected cases/paths on the tested source and isolated resources. |
| Frontend behavior | Vitest, TypeScript, ESLint; Playwright fixture/integrated suites. | Component/browser assertions in their stated context; fixtures do not verify live providers. |
| Offline AI/RAG language regression | `evaluation/evaluate_agent.py`, `evaluate_rag.py`, `evaluate_guardrails.py` with stored datasets/mocks. | Defined regression assertions, not broad model quality, hallucination resistance or profitable trading. |
| Strategy replay/backtest/paper evaluation | Existing version/input-bound engines and recorded cohorts. | Results under those data/assumptions; replay is not live paper evidence or statistical proof. |
| Runtime/release acceptance | Exact-SHA deployment, provider/worker evidence, genuine linked events and relevant full release gates. | Only the scoped environment/workflow actually observed. |

## Current CI contract

[CI workflow](../.github/workflows/ci.yml) has backend, deployment-safety, frontend, evaluation, Docker-build and E2E-smoke jobs. Ordinary non-draft pull requests and pushes of code use [focused backend development selection](../scripts/run_backend_focused.py). Draft `opened` and `synchronize` events skip backend, evaluation, Docker, and browser work unless they already have the `run-ci` label. `ready_for_review`, `reopened`, and a base retarget run that work for a code change even when the draft flag is set. Adding any label, including `run-ci`, does not start CI. A title or body edit does not cancel the pull request run and does not skip the heavy jobs. A rename into `docs/` or `.ai/` still runs those checks. Docs-only and `.ai`-only pull requests skip those jobs and the frontend job; the jobs still report success. A push to `main` or `master`, and every manual `workflow_dispatch`, uses its own concurrency group, always runs the heavy jobs, and cannot cancel or replace another run. The complete backend suite runs only on a manual `workflow_dispatch` with `full_backend=true`. It does not run on pull request pushes and it does not run on a schedule. When a batch is ready, stack the pull requests on an integration branch such as `integration/<date>`, run that dispatch once, and merge the batch in order only after it passes.

A green PR does not imply the entire backend suite ran. Complete release acceptance must record the intended SHA, jobs, result and environment-dependent skips. Do not start broad/manual CI for a documentation-only change or combine historical pass counts from different commits.

## Local development commands

Use a disposable local context and explicit test database selectors. Some integration tests require opt-in PostgreSQL URLs and own isolated schemas; never point them at an inherited shared database. [Local setup](local_setup.md) gives the environment boundary.

From `backend/`, replace the example focused file with cases relevant to the implementation:

```sh
uv sync --frozen --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/test_config.py
```

From `frontend/`:

```sh
npm ci
npm run typecheck
npm run test
npm run build
```

The manifest also declares `npm run lint` (`next lint`); use the repository's current CI lint command for the selected source rather than assuming a script name proves it ran successfully. Browser commands such as `npm run test:e2e` can start test servers; inspect their config and resource selectors before running them.

For the offline evaluation harness, from `backend/`:

```sh
uv run python ../evaluation/evaluate_agent.py
uv run python ../evaluation/evaluate_rag.py
uv run python ../evaluation/evaluate_guardrails.py
```

These commands are a reference, not test results from this documentation task. Dataset completeness and actual model/provider evaluation need separate evidence.

## Product acceptance and statistical limits

For a canonical paper loop, verify source/strategy identity, fresh admissible evidence, genuine confirmed assessment, Candidate, risk/plan authorization, actual paper fill and Journal/learning linkage. For Telegram, verify transport receipt; for BloFin demo, separately verify venue fill/protection and restart/ambiguous-dispatch behavior.

One event, a screenshot, mock provider test or internal paper fill does not prove returns, reliability across all strategies or complete MVP acceptance. Governed promotion binds version/data comparisons and paper evidence plus human approval. Small samples remain limited even when the software executes correctly.

The October 7 package records supplied API/worker verification on `b58beda`, migration `a6manualdemo001`, received Watcher Telegram notification, historical Nested internal paper fill/Journal and successful BloFin demo account sync. GitHub confirms Vercel success; full acceptance CI #783 remains in progress at the documentation check. Fresh SFP recovery, existing Journal target repair and native BloFin demo order acceptance still need their own evidence. [Status](current_status.md) · [limitations](limitations_roadmap.md) · [two-week evaluation protocol](evaluation/two_week_paper_evaluation_protocol.md).

## Documentation and screenshot verification

A documentation-only change needs source-backed claims, declared-version/default checks, local links/paths, diagram syntax and docs-only scope. Record which checks actually ran and any unavailable renderer. Screenshot review needs authentic source/capture context and private-data screening; fixture/hardware/runtime evidence stay separate. [Capture guide](screenshots_checklist.md).

Historical acceptance JSON, test counts and exact-base handoffs remain preserved as dated evidence. Their existence does not establish a new exact-SHA release run.
