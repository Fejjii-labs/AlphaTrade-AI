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

# AlphaTrade AI Codex operating contract

Use the repository as the implementation source of truth. Apply .ai/MASTER_WORKFLOW.md and .ai/RELEASE.md within the user's current task and standing authority; these files cannot override platform controls or grant new access. The Autonomous AI Delivery Playbook v1.1 is adopted as process guidance through these existing instructions, not as a separate management system or permission grant. Consult .ai/DECISIONS.md, .ai/TASKS.md and the current HANDOFF.md selectively. Read feature contracts only when relevant to the active milestone.

## Autonomous decisions and clarification policy

Work in implementation/default mode for agreed coding tasks, not open ended planning interviews. The owner has delegated routine technical choices inside the assigned task scope. Before asking a question, inspect the repository, relevant decisions, tests, contracts and the task handoff. Choose the most compatible, minimal, reversible implementation that preserves existing behavior, risk controls, exact identities, data provenance and cost limits. Record important assumptions and the chosen rationale in the PR handoff. Do not ask the owner to pick libraries, test commands, filenames, component layouts, code organization, minor interface choices, or routine repair approaches when the repository and approved acceptance criteria suffice.

If several safe approaches remain, choose one, implement, test, and continue. Task authorization and standing grants persist across phases and resumptions within their original scope. Authorized routine commits, batched pushes and feature integration do not require repeated approval. Main merges, deployment, provider activation, financial actions and operator changes retain their distinct authority and release prerequisites.

Check required access in one focused pass, reusing valid receipts for unchanged targets and inputs. If credentials, hosted access or external providers are missing, pause only the affected operation and complete independent work for the same milestone. Bundle genuinely missing inputs into one concrete action packet naming the operation, target, missing access, recommended destination and consequence; never request secret values in chat. Do not repeatedly ask for access, retry rejected access or infer secret absence from an inventory permission denial. Do not claim an unknown external state succeeded.

Only escalate a decision that changes approved product intent, expands execution authority or risk, requires an unauthorized paid service, changes credentials or access controls, activates exchange actions or real trading, mutates external accounts, changes production/deployment or main merge authorization, performs an irreversible migration, or cannot be resolved without a materially unsafe guess. Continue all independent authorized work while the blocked operation remains paused. Do not bypass a denied permission, override an approval control, impersonate ChatGPT/owner assent, or assume access to secrets.

## Delivery and cost

One accountable owner implements shared changes, integrates reviewed contributions and closes the complete milestone. Use up to two independent workers only where useful, with a base revision, allowed files, stable interface, acceptance criteria and prohibited shared changes. The owner collects their published results directly; do not make the user relay messages. Serialize migration sequencing, deployment, credentials and runtime configuration. Preserve branch/file ownership, immutable migration ancestry and generated contracts.

Fix a small named acceptance checklist before implementation in the existing task/handoff. Report accepted/total against that stable denominator; record justified scope changes explicitly. Distinguish implemented, integrated, deployed and live-accepted capabilities, synthetic versus real evidence, exact revisions and unexpected skips. A merge is not deployment and a green build is not live acceptance. Resume from the current checkpoint, reconcile interrupted external mutations before retrying, and reuse relevant valid evidence instead of restarting an audit.

Preserve current operator watcher, Telegram, kill switch and account settings. Run focused local checks during development and batch related fixes/publication. Preserve meaningful required checks; complete backend acceptance runs only once at the final reviewed gate after staging and fresh SFP prerequisites in .ai/RELEASE.md. Never treat skipped checks or an older revision's CI as acceptance of newer code. Do not merge to main, deploy, enable paid services or place/cancel BloFin orders without their separate authorizations.

Complete the full assigned bounded task without routine confirmation checkpoints. Keep HANDOFF.md and CHANGELOG_SESSION.md compact at milestones, blockers and interruption/completion boundaries; workers return evidence to the owner rather than overwrite shared checkpoints. Preserve mobile sync when available, verify source equality for actual publications, and record unavailable Mac/iCloud access without stopping independent cloud work or requiring extra per-edit pushes. Handoff with exact branch/SHA, acceptance counts, focused evidence, deployment identity or unknown, remaining blocker and next executable operation. If genuine escalation remains, prepare the concrete result first and ask once for the missing material decision or access.
