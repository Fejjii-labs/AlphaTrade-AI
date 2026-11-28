# PR review checklist

A short pass for every PR. `AGENTS.md` and `.cursor/rules/*.mdc` stay canonical; this list
is how a reviewer checks them.

## Tests

- [ ] **Bug fixes, trading/money logic, auth/consent and migrations: test first.** A
      regression test that fails without the fix must exist (in an earlier commit, or
      shown failing in the PR description), and pass with it.
- [ ] **Everything else: tests ship with the code** in the same PR, covering behaviour
      and failure paths, not only the happy path.
- [ ] Tests are deterministic and offline: no real OpenAI, Binance, BloFin or other
      network calls (pytest-socket blocks non-loopback hosts).

## Scope and intent

- [ ] Acceptance criteria are stated in the PR description, and each one is met or
      explicitly deferred.
- [ ] One concern per PR, about 400 changed lines or fewer (lockfiles and generated
      migrations excluded).

## Migrations

- [ ] Reversible: `downgrade()` is implemented, and upgrade -> downgrade -> upgrade was
      run (in a test or shown in the PR).
- [ ] Only one open migration PR at a time.

## Safety and secrets

- [ ] No secrets in code, logs, fixtures, commits or the PR body (keys, tokens, DSNs).
      New log lines don't print credentials or full provider payloads.
- [ ] No live trading path enabled: `ENABLE_REAL_TRADING` is untouched, with no live keys
      or live order endpoints.
- [ ] Defaults stay paper-only (paper, BloFin demo, exchange testnet); both trade caps
      (account cap and bot cap) remain.

## External calls

- [ ] Cost and rate-limit impact of new or changed OpenAI and Binance calls are stated:
      calls per request/job, model, retries/backoff, caching, and the worst case.
- [ ] Paid calls are env-guarded and capped; nothing paid runs in CI by default.

## Evaluation gates

- [ ] Deterministic CI gates are unchanged unless the PR says why: recall@k / MRR /
      no-relevant-source thresholds in `evaluation/evaluate_rag.py`, eval datasets, and
      schema/contract checks (`npm run api:check`, strict JSON schemas).
- [ ] LLM-judge evals (for example `evaluation/deepeval_answer_relevancy.py`) are
      non-blocking: manual or nightly only, never a required check, and with a spend cap.
