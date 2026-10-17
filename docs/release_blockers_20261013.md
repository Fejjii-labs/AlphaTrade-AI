# Targeted release blocker repair

Checked 13 October 2026. This change builds on PR252 at65a81739c847d8659c95fa79f593bdb99e91a226 and therefore already includes its focused selector correction. Target integration branch is PR249, not main.

## Verified blockers

1. Full release run38294470908 was cancelled; backend did not pass and evaluation/browser checks were skipped. Its log lookup returns BlobNotFound and artifact inventory has no backend diagnostics. The historical timeout cause is still unknown.
2. PR252 run38314136225 used CI_BASE_SHA=b165b922, although the PR now targets PR249. The log shows steady progress to67% before cancellation. It tested the cumulative feature stack against main, not just the selector correction. Do not infer a hung test from this cancellation.
3. PR251 remains a separate workflow feature with locally accepted evidence and unaccepted native/runtime outcomes.
4. Vercel inspection is blocked by403 for the established team scope. No authenticated CLI exists in this review executor.
5. New bounded public provider probes: Binance OI returned HTTP451. Bybit OI did not return valid JSON. Do not treat these as accepted market evidence or silently route around provider restrictions.
6. Public staging health returned200, git_sha=b165b92276346f0e0fe3ccdbd2bec3443dc75d40, execution_mode=paper, real_trading_enabled=false, exchange_mode=paper_exchange_demo.

## Reproduced and repaired

The watchdog called PostgreSQL diagnostics on the same thread that enforces deadlines. A deliberately blocked diagnostic callback caused a0.5second run budget to exceed4seconds in a focused reproduction.

Sending SIGTERM to the old supervisor killed it without result.json or before-cancellation.json. This also left its separately grouped child running until test cleanup.

The repair makes diagnostic sampling nonblocking, with at most one in-flight diagnostic thread per supervisor. Reports include the sampling timestamp. SIGINT/SIGTERM record the cancellation reason, retain evidence and stop the child process group. The bounded cleanup also kills descendants that ignore the interrupt. Missing or malformed active metadata cannot disable the overall run deadline.

This repairs demonstrated supervisor weaknesses. It does not establish that either weakness caused the historical GitHub timeout, and cannot guarantee artifact upload after a runner outage or uncatchable SIGKILL.

## Focused verification

1. Before repair: blocked diagnostic and SIGTERM reproductions both failed.
2. After repair:31 existing selector checks passed on the complete PR252 source checkout.
3. Final watchdog module:7 cases passed, including actual nested pytest first-failure behavior, sanitized diagnostic errors, phase cancellation, blocked diagnostics, SIGINT, and SIGTERM with a child ignoring SIGINT.
4. A combined intermediate run passed38 cases; after tightening child cleanup, only the changed7-case watchdog module was repeated. Counts overlap.
5. Changed-file Ruff lint, format and git diff checks passed.
6. These infrastructure-only tests use --noconftest, no application fixtures or database. The isolated runner had pytest9.1.1 and psycopg3.3.6; this is not the complete locked application environment. One warning identifies absent asyncio plugin configuration in the isolated runner; no tests were skipped.
7. No full backend suite or manual CI dispatch was performed. No migrations, provider activation, account changes, orders or deployment were performed.

Commands from backend/:

```sh
python3 -m pytest tests/test_backend_release_diagnostics.py tests/test_backend_ci_scope.py --noconftest -q -x
python3 -m pytest tests/test_backend_release_diagnostics.py --noconftest -q -x
python3 -m ruff check ../scripts/run_backend_release.py tests/test_backend_release_diagnostics.py
python3 -m ruff format --check ../scripts/run_backend_release.py tests/test_backend_release_diagnostics.py
git diff --check
```

The execution used PYTHONPATH pointing to isolated tooling outside the repository. Dependencies were not changed.

## Roadmap and ownership

1. Review this combined PR252 plus watchdog correction. Integrate it once through the release owner. Do not rebuild the selector or repeat unchanged checks.
2. Before another full run, recover historical diagnostics if available or use a bounded run around a concrete suspect. The improved supervisor is not proof that the full suite now fits its time budget.
3. Review PR251 against the corrected candidate, preserving both logger isolation fixes and immutable migration history. Choose the final release composition before paying for release acceptance.
4. Freeze the reviewed candidate, follow existing staging prerequisites and complete one required release gate. Verify actual deployed revisions and a short hosted smoke; no blanket retry loop.
5. Independently bind authorized Vercel team access and provider egress in Cursor. Use approved existing secrets and the private continuation package. Do not paste credentials into chat or infer global absence from one executor's missing bindings.
6. Next product acceptance: real microphone conversation and interruption; BloFin manual activity reflection; Nested/SFP alert diagnostics; valid OI/CVD/book evidence. Then complete native experiment execution and Exploration/Validation sample admission.
7. Continue compact UI refinement after checking the deployed baseline. D Line needs chart examples and explicit rules. News, economic events, fear and greed, heatmap and liquidation remain deferred Agent context.

Progress: targeted code repair and local acceptance complete. Overall release acceptance remains3/6 successful jobs on the prior candidate, not accepted on this new candidate. The larger product percentage is not inferred from test counts.

