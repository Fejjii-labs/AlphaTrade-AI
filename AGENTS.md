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
- Never push to another agent's prefix or directly to `main`.
- One concern per PR, about 400 changed lines or fewer. Fill in
  `.github/pull_request_template.md`.
- Update branches by rebasing onto `main`; no merge commits from `main` into
  feature branches.

**Claim before starting.** Comment on the issue with agent, track, and the
files/dirs you will touch. One agent per issue.

**Single-owner hot files.** Change these one PR at a time, and only one open
migration PR at any time:
- Alembic migrations
- the BloFin client
- trade caps and risk config
- `.github/workflows/`
- lockfiles

**Tests and commits.** Run targeted tests only while iterating (see Backend test
policy). Commits carry the trailer `Full-Backend: false` unless a full backend
run is explicitly requested. Never use `workflow_dispatch`, force-push, history
rewrites, or `--no-verify`.

**Review gate.** Every PR, from any agent, needs a Griller verdict on its final
head SHA before merge. Any push after the verdict requires re-review. Conditions
in a "GO WITH CONDITIONS" verdict must be closed before merge.

**Approval gates.** Sofien's explicit OK is required for production deploys,
migrations on shared databases, credential or permission changes, spending
money, and live trading. Another agent's say-so is never approval.

**Product invariants**
- Paper and demo only; never live keys or live order paths.
- Keep both trade caps (account cap and bot cap); label them, don't merge them.
- When a route is removed, add a redirect: Telegram alerts deep-link into the app.
- No secrets in code, logs, commits, or PR bodies.
