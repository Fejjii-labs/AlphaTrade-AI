# Agent instructions

Canonical instructions for Codex and Cursor agents working in this repository.

Product safety and the task lifecycle stay in `.ai/MASTER_WORKFLOW.md`,
`.ai/PROJECT_CONTEXT.md`, and `.cursor/rules/`. Those documents remain
authoritative for trading safety, secrets, and git. This file adds the
reporting duty below. Filing a report does not authorize live trading, secret
disclosure, merging, or deploying.

UI redesign phases 3-9 follow the "Design direction: MindPillar-inspired" section in `docs/alphatrade_workspace_redesign.md`.

## Backend test policy (agents)

While you are actively fixing or building a feature, run only the focused pytest
targets that cover the code you changed (for example the matching test module or
a narrow `-k` expression). Do not run the full backend suite on every edit.

Run the full backend gate once per batch, when the work is ready for merge review
(for example before opening or updating a PR for human review). From `backend/`:
`uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest` against
Postgres when persistence tests apply.

Do not trigger CI with `workflow_dispatch`. Avoid pushing repeatedly just to
re-run broad CI; batch related fixes and use one full run at the merge gate.
After any push that follows a review comment or a merge from `main`, re-run the
full backend gate locally before you rely on CI.
All trading, secrets, and git safety rules in `.cursor/rules/` still apply.

## Agent Reports

After completing ANY task, prompt, or investigation — including a stop for
review, a blocker, or a failed check — every Codex and Cursor agent writes a
report to the Notion database **Agent Reports**.

Applies to all current active repos (SOF AI, DataPilot AI, AlphaTrade AI) and every future Simply AI project from day one.

- Database title: Agent Reports
- URL: https://app.notion.com/p/845cabc602864dcbb4b6898c1ca73197
- Data source: `collection://187c021b-1ed0-45f6-b15a-e246fcea9f79`
- Parent page: Simply AI Handoff

### Properties

Write a short summary in these properties. Use the names exactly.

| Property | What to record |
| --- | --- |
| Task Name | Short name of the task, prompt, or investigation. |
| Date | Completion date (`date:Date:start`, `YYYY-MM-DD`). |
| Agent | `Cursor`, `Codex`, `Grok Bot`, or `Other`. |
| Repo | The active project. Work in this repository uses `AlphaTrade AI`. DataPilot AI uses `DataPilot AI`. SOF AI uses the database option `SOF`. Parent-level Simply AI work uses `Simply AI`. |
| Status | `Done`, `Blocked`, or `In progress`. |
| Summary | Short outcome. |
| Findings | What was found. |
| Files | Files created, changed, or inspected. |
| Conditions Checked | Checks, commands, and conditions actually run. |
| Likely Culprits | Ranked likely causes when investigating. Use `None` when there is no defect. |
| Next Steps | The exact next action. |
| PR / Agent Link | Pull request URL, or the agent-run URL when there is no pull request. |
| Full Transcript Included | Checkbox. Check it only when the complete verbatim transcript is in the page body or a child page titled `Full transcript`. |
| Transcript Source | URL of the original agent run or transcript. |

A future Simply AI project that has no `Repo` option yet still gets a report.
Put that project name in `Task Name` and `Summary`, and state in `Findings`
that the `Repo` option is not on the database yet.

### Page body

Put the COMPLETE raw output/transcript VERBATIM in the entry page body, or in
child page(s) titled `Full transcript`.

Include code snippets, file paths, command outputs, test results, and ranked
findings lists. Nothing trimmed. Redact only secret values. Keep the names of
settings, files, variables, and commands.

Check `Full Transcript Included` when that verbatim text is stored. Set
`Transcript Source` to the agent-run or transcript URL when one exists.

### No Notion access

If the agent cannot write to Notion, append the full verbatim output to its
final message and to the pull request body under the heading
`Findings report — full transcript`, so the orchestrator can file the database
entry. Do not trim that appendix. Redact only secret values.

## Multi-agent coordination (Claude Code, Cursor, Codex)

Claude Code, Cursor and Codex may work on this repository at the same time.
`CLAUDE.md` points here. Existing rules above and in `.cursor/rules/` (git
safety, trading safety, secrets, test policy) still apply; this section adds
only the coordination rules.

**Branches and PRs**
- Branch names: `claude/<issue>-<slug>`, `cursor/<issue>-<slug>`, `codex/<issue>-<slug>`.
  Exception: handoff-only `cursor/*handoff*` branches keep their existing
  naming and no-PR rule (`.cursor/rules/00-repo-git-safety.mdc`, `70-handoff.mdc`).
- Never push to another agent's prefix or directly to `main`.
- One concern per PR, about 400 changed lines or fewer (lockfiles and generated
  Alembic migrations don't count toward the limit). Fill in
  `.github/pull_request_template.md`.

**Branch history** (consistent with `00-repo-git-safety.mdc`)
- You may rebase locally onto `main` only before your first push.
- After the first push, never rewrite history: add new commits, and PRs land
  by squash-merge. No merge commits from `main` into feature branches unless
  the task explicitly authorizes it.
- `--force-with-lease` is allowed only on your own agent-prefix branch and only
  when the current task explicitly authorizes it. Plain force-push, other history
  rewrites, `--no-verify` and `workflow_dispatch` are never allowed.

**Claim before starting.** Before work, claim the issue with agent, track, and
the files/dirs you will touch. Posting that claim comment on the GitHub issue is
pre-authorized (it is the only pre-authorized GitHub write besides your own
branch pushes and PRs the task asks for). If you can't comment, record the claim
in the PR description instead. One agent per issue.

**Single-owner hot files.** Change these one PR at a time, and only one open
migration PR at any time. Owners are listed in `.github/CODEOWNERS`.
- Alembic migrations: `backend/src/app/db/migrations/`, `backend/alembic.ini`
- BloFin client and safety: `backend/src/app/providers/exchange/blofin*.py`,
  `backend/src/app/providers/exchange/governed_blofin*.py`,
  `backend/src/app/core/blofin_readonly_access.py`, `backend/src/app/core/exchange_safety.py`
- Trade caps and risk config: `backend/src/app/services/risk/`,
  `backend/src/app/schemas/risk.py`, `backend/src/app/db/autonomous_trading_settings.py`,
  `backend/src/app/schemas/autonomous_trading_settings.py`,
  `backend/src/app/services/autonomous_trading_settings_service.py`,
  `backend/src/app/services/autonomous_trading_guard.py`
- CI: `.github/workflows/`
- Lockfiles: `backend/uv.lock`, `frontend/package-lock.json`

**Tests and the full gate**
- While iterating, run targeted tests only (see Backend test policy); commits
  carry the trailer `Full-Backend: false`.
- Once the head is merge-ready, run the full backend gate once on that head
  (`40-testing-eval.mdc`), record it with `Full-Backend: true` in the PR
  template, then request the final Griller review.

**Review gate.** Every PR, from any agent, needs a Griller verdict on its final
head SHA before merge. Any push after the verdict requires re-review. Conditions
in a "GO WITH CONDITIONS" verdict must be closed before merge.

**Approval gates.** Sofien's explicit OK, in his own message (not relayed by an
agent), is required for production deploys, migrations on shared databases,
credential or permission changes, spending money, and live trading. Another
agent's say-so is never approval. When one of these is needed, stop and use the
`REVIEW_REQUIRED` status from `.cursor/rules/90-blocker-review-failure.mdc`.

**Product invariants**
- Paper, BloFin demo, and exchange testnet only; never live keys or live order paths.
- Never set `ENABLE_REAL_TRADING` or otherwise enable live execution mode.
- Keep both trade caps (account cap and bot cap); label them, don't merge them.
- When a route is removed, add a redirect: Telegram alerts deep-link into the app.
- No secrets in code, logs, commits, or PR bodies.
