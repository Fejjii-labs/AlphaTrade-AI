# Agent instructions

Canonical instructions for Codex and Cursor agents working in this repository.

Product safety and the task lifecycle stay in `.ai/MASTER_WORKFLOW.md`,
`.ai/PROJECT_CONTEXT.md`, and `.cursor/rules/`. Those documents remain
authoritative for trading safety, secrets, and git. This file adds the
reporting duty below. Filing a report does not authorize live trading, secret
disclosure, merging, or deploying.

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
