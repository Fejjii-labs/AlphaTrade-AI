# AlphaTrade AI Master Workflow and Handoff Standard

**Version:** 2.0  
**Recommended Cursor model for installation:** Opus 4.8  
**Project:** AlphaTrade AI  
**Project slug:** `alphatrade-ai`  
**Task prefix:** `AT`  
**Repository:** `~/Developer/AlphaTrade-AI`  
**Capability profiles:** `base`, `agentic`, `high_risk`, `trading`

---

## 1. Purpose

This document is the single master standard for the AlphaTrade AI collaboration workflow between ChatGPT, Cursor, the local repository, and the mobile iCloud handoff.

It replaces and supersedes:

1. `ALPHATRADE_AI_WORKFLOW_CATCHUP_PROMPT.md`
2. `ALPHATRADE_MOBILE_BLOCKER_HANDOFF_ADDENDUM.md`

It defines:

- the permanent project governance layer;
- normal task execution;
- blocked, failed, and human-review workflows;
- handoff and session-document requirements;
- mobile-first iCloud synchronization;
- repository and Git safety;
- trading and broker/exchange safety;
- what is tracked, ignored, retained, regenerated, archived, or deleted;
- how Cursor must finish every task before ChatGPT continues planning.

This document is not product code. Installing or updating this workflow must not alter AlphaTrade AI application behavior unless a later task explicitly authorizes implementation work.

---

## 2. Core operating principles

1. **The repository is the technical source of truth.**
2. **Tracked governance files define durable project rules.**
3. **`HANDOFF.md` is the current operational state.**
4. **`CHANGELOG_SESSION.md` is the latest execution record.**
5. **Chat transcripts are context, not durable project truth.**
6. **Every claim must be based on verified repository, test, deployment, or runtime evidence.**
7. **Unknown or unverified facts must be marked explicitly.**
8. **Safety rules take precedence over speed, convenience, and feature completion.**
9. **A blocker must be synchronized immediately rather than waiting for task completion.**
10. **Real trading remains disabled unless a separate, explicit, safety-reviewed future task authorizes a controlled change.**

---

## 3. Source-of-truth precedence

When information conflicts, use this order:

1. Safety and security constraints in tracked project rules
2. Current repository code and configuration
3. Automated test and validation evidence
4. Deployment/runtime evidence
5. `.ai/DECISIONS.md`
6. `.ai/TASKS.md`
7. Current `HANDOFF.md`
8. Current `CHANGELOG_SESSION.md`
9. ChatGPT or Cursor conversation text

Never change code merely to make an outdated document appear correct. Update the document to reflect verified reality.

---

## 4. Canonical paths

### Repository

```text
~/Developer/AlphaTrade-AI
```

If the active repository is under Desktop, Documents, Downloads, or iCloud Drive, stop and create a safe relocation plan before implementation.

### Canonical local handoff files

```text
~/Developer/AlphaTrade-AI/HANDOFF.md
~/Developer/AlphaTrade-AI/CHANGELOG_SESSION.md
```

### Canonical iCloud destination (local worktree mirror)

```text
~/Library/Mobile Documents/com~apple~CloudDocs/AI-Projects/AlphaTrade AI/HANDOFF.md
~/Library/Mobile Documents/com~apple~CloudDocs/AI-Projects/AlphaTrade AI/CHANGELOG_SESSION.md
```

### Sync script (local worktree → iCloud)

```text
~/.local/bin/sync-alphatrade-ai-handoff.sh
```

### LaunchAgent (local worktree → iCloud)

```text
~/Library/LaunchAgents/com.sofien.alphatrade-ai-handoff-sync.plist
```

### Cloud / GitHub → iPhone path (Mac LaunchAgent)

Cursor Cloud agents (and local agents without the Mac worktree) must push
`HANDOFF.md` to GitHub so the Mac LaunchAgent can publish it to iCloud:

```text
GitHub remote HANDOFF.md
  → com.alphatrade.handoff-sync
  → ~/Library/Mobile Documents/com~apple~CloudDocs/AlphaTrade AI/HANDOFF.md
  → iPhone
```

- Script: `~/Library/Application Support/AlphaTradeAI/handoff-sync.sh`
- LaunchAgent: `~/Library/LaunchAgents/com.alphatrade.handoff-sync.plist`
  (`RunAtLoad`, ~120s interval, automatic `git fetch --all --prune`)
- Selection: newest valid METADATA `Generated At UTC` across remote branches
  that contain `HANDOFF.md` (not branch name, mtime, or commit-time primary)
- Provenance: `HANDOFF_SOURCE.json` beside the iCloud handoff
- Prefer dedicated `cursor/*handoff*` branches; do **not** open a PR for
  handoff-only branches

---

## 5. Permanent project governance

Create or maintain these durable project files:

```text
.ai/MASTER.md
.ai/MASTER_WORKFLOW.md
.ai/PROJECT_CONTEXT.md
.ai/ARCHITECTURE.md
.ai/AUDIT.md
.ai/IMPLEMENT.md
.ai/BUGFIX.md
.ai/REFACTOR.md
.ai/SECURITY.md
.ai/RELEASE.md
.ai/LINKEDIN_DEMO.md
.ai/HANDOFF_TEMPLATE.md
.ai/SESSION_TEMPLATE.md
.ai/DECISIONS.md
.ai/TASKS.md
```

Create or maintain Cursor rules under:

```text
.cursor/rules/
```

Required rule areas:

1. Repository and Git safety
2. Architecture, modularity, and strong typing
3. Trading safety and paper-first operation
4. Security, privacy, and secrets
5. Testing and AI evaluation
6. Observability and auditability
7. Documentation truthfulness
8. Mandatory handoff generation
9. Mandatory mobile sync and verification
10. Blocker, review, and failure protocol

### Tracking policy

The durable, sanitized governance layer should be version-controlled so it survives a fresh clone:

```text
.ai/
.cursor/rules/
```

Generated or machine-local operational files remain ignored:

```text
HANDOFF.md
CHANGELOG_SESSION.md
.ai/local/
.ai/private/
*.local.md
.env*
```

Exceptions such as safe example environment files may be tracked only when they contain placeholders and no secrets.

Before tracking `.ai/` or `.cursor/rules/`, remove:

- API keys, tokens, passwords, or credentials;
- personal financial data;
- private account identifiers;
- machine-specific temporary paths;
- transient timestamps and hashes;
- generated session output;
- confidential broker or exchange data.

If the repository is public, keep only public-safe governance in tracked files and move private material into ignored `.ai/private/`.

---

## 6. AlphaTrade safety baseline

These rules are mandatory unless changed by a separate future authorization task:

1. `execution_mode = paper`
2. `real_trading_enabled = false`
3. No automatic live orders
4. No withdrawals or transfers
5. No leverage mutations
6. No broker or exchange account mutation
7. No worker, scanner, Telegram, scheduler, or autonomous execution path unless separately approved
8. No proposal-to-execution shortcut
9. No approval bypass
10. No guarantee of profit or autonomous profitability

Any future sensitive action requires all of the following:

- explicit human approval;
- deterministic validation;
- idempotency;
- audit logging;
- least privilege;
- kill switch;
- conservative failure behavior;
- tested rollback;
- clear operator visibility.

Risk calculations must be deterministic and tested. Market and account data must include source and freshness timestamps. Missing, stale, conflicting, degraded, or unauthenticated data must trigger conservative behavior.

Preserve these product objectives:

- human-versus-system comparison;
- paper validation;
- position sizing;
- stop-loss discipline;
- take-profit discipline;
- runner logic;
- behavioral coaching;
- analysis of early exits and failure to accept losses.

---

## 7. Broker and exchange operating modes

### Mode A: No broker or exchange connected

Allowed:

- internal paper portfolio;
- public market data;
- mock providers;
- backtests;
- paper validation;
- strategy and coaching analysis.

Not allowed:

- external account reads;
- external account mutations;
- live order placement.

### Mode B: Read-only broker or exchange connection

Allowed only when explicitly configured:

- balances;
- positions;
- order history;
- funding and market data;
- account health checks.

Requirements:

- read-only API credentials where supported;
- no withdrawal permission;
- no trade permission unless separately required for a future approved sandbox;
- source and freshness timestamps;
- graceful degradation;
- no mutation endpoints.

### Mode C: Paper or demo broker/exchange

Allowed only after validation:

- simulated orders;
- demo-account interaction;
- paper positions;
- reconciliation and audit checks.

Requirements:

- clearly labeled paper/demo mode;
- separate credentials and configuration;
- no route to production trading;
- full audit trail;
- deterministic safeguards.

### Mode D: Real broker or exchange execution

Disabled by default.

It may not be enabled through an ordinary implementation task. It requires a separate architecture, security, risk, legal, operational-readiness, approval, rollback, and kill-switch program.

---

## 8. Task lifecycle

Every Cursor task follows this lifecycle.

### Step 1: Inspect

Before changing anything, verify:

- absolute repository path;
- current branch and commit;
- Git status;
- active uncommitted work;
- relevant project rules;
- current task and dependencies;
- existing tests and latest verifiable state;
- safety posture;
- deployment scope, if relevant.

Do not overwrite or discard unrelated work.

### Step 2: Start the handoff

Before implementation:

1. Set `HANDOFF.md` to `IN_PROGRESS`.
2. Record the current task, phase, baseline, goal, safety posture, and intended validation.
3. Start or replace `CHANGELOG_SESSION.md` for the current execution session.
4. Run the sync script.
5. Verify source and iCloud copies match.

### Step 3: Implement in scoped phases

At each major phase boundary:

1. Update `HANDOFF.md` with completed work and current phase.
2. Update `CHANGELOG_SESSION.md` with commands and results.
3. Sync immediately.
4. Continue only when the next phase is safe and authorized.

### Step 4: Validate

Use the narrowest relevant checks first, then broader regression checks.

Validation evidence must include:

- exact command;
- exit status;
- relevant result counts;
- skipped or unavailable checks;
- honest explanation of what was not run;
- last known full-suite result when relevant.

### Step 5: Finish

When the task is complete:

1. Set `HANDOFF.md` to `READY`.
2. Regenerate all current-state sections.
3. Finalize `CHANGELOG_SESSION.md`.
4. Run the sync script.
5. Verify source and iCloud copies using SHA256 and `cmp` or `diff`.
6. Report Git status and whether code was committed or pushed.
7. Include the next recommended model and exact next Cursor prompt.

---

## 9. Handoff status model

Use only these exact statuses:

```text
IN_PROGRESS
REVIEW_REQUIRED
BLOCKED
FAILED
READY
```

### Status meanings

#### `IN_PROGRESS`

Work is active and can continue safely without human intervention.

#### `REVIEW_REQUIRED`

A human decision, approval, permission, credential, secret, external action, or destructive confirmation is required.

Cursor must stop before the protected action.

#### `BLOCKED`

A technical or environmental issue prevents safe progress after scoped diagnostics.

#### `FAILED`

The task or a critical validation failed and cannot be represented as successful. The repository must be left in a safe, documented state.

#### `READY`

The current task is complete, validated to the stated scope, synchronized, and ready for ChatGPT review or the next task.

Do not use `DRAFT`. `IN_PROGRESS` replaces it.

---

## 10. Normal workflow without blockers

```text
Task received
→ Inspect repository and rules
→ HANDOFF = IN_PROGRESS
→ Sync and verify
→ Implement phase 1
→ Update handoff and session log
→ Sync and verify
→ Implement remaining phases
→ Run validation
→ HANDOFF = READY
→ Final sync and verification
→ Upload HANDOFF.md to ChatGPT
→ ChatGPT reviews and prepares the next task
```

At no point should Cursor wait until the final response to create the handoff.

---

## 11. Workflow with blockers, approvals, or failures

### Human approval or operator action

Examples:

- macOS permission;
- secret or credential entry;
- Render environment change;
- destructive collection recreation;
- external-service action;
- broker or exchange permission;
- commit, push, deployment, or release approval when not already authorized.

Required behavior:

1. Stop safely.
2. Set `HANDOFF.md` to `REVIEW_REQUIRED`.
3. Document the exact human action needed.
4. Record what was completed and what remains unchanged.
5. Record whether code or configuration was modified.
6. Record uncommitted work.
7. Sync and verify immediately.
8. Do not continue until approval or confirmation is provided.

### Recoverable technical blocker

1. Run only safe, scoped diagnostics.
2. Do not make speculative destructive changes.
3. If unresolved, set `HANDOFF.md` to `BLOCKED`.
4. Record the failed command and concise error evidence.
5. Record the last successful step.
6. Sync and verify immediately.
7. Wait for ChatGPT or human guidance.

### Failed task or critical validation

1. Stop further changes.
2. Preserve evidence and current work.
3. Set `HANDOFF.md` to `FAILED`.
4. Explain the failure without claiming success.
5. State whether rollback occurred or is needed.
6. Sync and verify immediately.
7. Do not commit, push, or deploy failed work unless explicitly directed for diagnostic purposes.

---

## 12. Mandatory synchronization moments

Regenerate and synchronize `HANDOFF.md` and update `CHANGELOG_SESSION.md` at:

1. Task start
2. Completion of every major phase
3. Any blocker
4. Any review or approval requirement
5. Any failed command or test that prevents progress
6. Any permission or environment issue
7. Any meaningful safety-state change
8. Before a destructive action
9. After a destructive action
10. Final task completion
11. End of every Cursor agent run (success, failure, blocked, early stop, or
    human-action required) — Cloud or local

Every regenerated handoff must include: current task, result, blocker/error if
any, evidence, what was completed, what was not completed, repository safety
state, exact human action required, exact recommended next Cursor prompt, and
`Generated At UTC`.

### Local Mac worktree sync

Run:

```bash
~/.local/bin/sync-alphatrade-ai-handoff.sh
```

Then verify:

- source SHA256;
- destination SHA256;
- `cmp` or `diff` success;
- expected destination paths;
- nonzero failure if verification fails.

### GitHub publish for cloud → iPhone sync

Push the regenerated handoff to GitHub (dedicated `cursor/*handoff*` branch
when a product PR must stay clean; force-add only the ignored handoff files;
do not open a PR for handoff-only branches). The Mac LaunchAgent
`com.alphatrade.handoff-sync` discovers the newest `Generated At UTC` and
copies it to iCloud automatically.

Never wait for the task to end before synchronizing a blocker.

---

## 13. `HANDOFF.md` format

`HANDOFF.md` must remain a compact current-state snapshot, not a full historical log.

### 13.1 Mobile Status block

Place this at the top:

```text
Status:
Last Updated:
Task:
Current Phase:
Progress:
Blocker:
Human Action Needed:
Next Step:
```

Use `None` when a field is not applicable. Do not leave ambiguous blanks.

### 13.2 Machine-readable metadata

Include:

```text
Project: AlphaTrade AI
Document Type: HANDOFF
Schema Version: 2.0
Generated At Local: <ISO-8601-with-offset>
Generated At UTC: <ISO-8601-Z>
Timezone: <host-IANA-timezone>
Session ID: AT-SESSION-YYYYMMDD-HHMMSS
Task ID: AT-XXX
Current Branch: <branch>
Current Commit: <short-sha-or-UNCOMMITTED>
Working Tree Status: CLEAN or DIRTY
Generated By: Cursor
Handoff Status: IN_PROGRESS | REVIEW_REQUIRED | BLOCKED | FAILED | READY
Source File SHA256: <normalized-content-hash>
```

Use the host system's actual IANA timezone. Do not hardcode `Europe/Berlin` or `Europe/Paris` when the machine reports another valid timezone.

### 13.3 Hash rule

A file cannot contain an ordinary hash of its complete final bytes without changing its own hash field.

Therefore, calculate `Source File SHA256` over normalized document content with the entire `Source File SHA256:` line removed. The same algorithm must be used by the generator and verifier.

The sync script must still verify the actual source and destination file bytes independently using ordinary SHA256 plus `cmp` or `diff`.

### 13.4 Required content sections

1. Executive Summary
2. Goal
3. Current Status
4. Current Branch, Commit, and Phase
5. Last Completed Task
6. Architecture Summary
7. Files Changed
8. Important Code References
9. Tests and Validation
10. Deployment and Provider Status, when relevant
11. Security and Trading Safety Status
12. Decisions Confirmed
13. Known Issues and Blockers
14. Remaining Prioritized Tasks
15. Recommended Model
16. Exact Next Instruction for ChatGPT
17. Exact Next Cursor Prompt

### 13.5 Additional blocked-state content

When status is `REVIEW_REQUIRED`, `BLOCKED`, or `FAILED`, also include:

- exact blocker;
- exact command or action that failed;
- concise relevant error output;
- what was already completed;
- what remains unchanged;
- risk of continuing;
- whether application code was modified;
- whether uncommitted work exists;
- exact human action required;
- exact Cursor instruction after resolution.

Never include secrets or full sensitive logs.

---

## 14. `CHANGELOG_SESSION.md` format

`CHANGELOG_SESSION.md` contains only the latest execution session.

Required sections:

1. Session Metadata
2. Starting State
3. Work Performed
4. Files Created, Changed, or Removed
5. Commands and Tests Run
6. Exact Results
7. Latest Successful Step
8. Blockers or Review Requests
9. Warnings and Risks
10. Follow-up Actions
11. Final Status

It must record blockers and review pauses immediately, not only at final completion.

Historical durable decisions belong in `.ai/DECISIONS.md`. Persistent backlog belongs in `.ai/TASKS.md`.

---

## 15. Durable decisions and tasks

### Decisions

Use `.ai/DECISIONS.md` with identifiers:

```text
AT-ADR-001
AT-ADR-002
...
```

Each decision should include:

- title;
- status;
- context;
- decision;
- alternatives considered;
- safety impact;
- consequences;
- validation or review requirement.

### Tasks

Use `.ai/TASKS.md` with identifiers:

```text
AT-001
AT-002
...
```

Each task should include:

- title;
- priority;
- status;
- goal;
- dependencies;
- risk;
- safety classification;
- validation criteria;
- recommended model;
- completion evidence.

Do not create duplicate task IDs. Close or supersede tasks explicitly.

---

## 16. iCloud sync requirements

The sync script must:

1. Use absolute, quoted paths.
2. Use strict shell behavior and stop safely on errors.
3. Copy only `HANDOFF.md` and `CHANGELOG_SESSION.md`.
4. Never copy the repository, `.git`, dependencies, virtual environments, caches, datasets, model artifacts, logs, secrets, or build output.
5. Compare content before copying.
6. Skip identical files without rewriting timestamps.
7. Copy atomically through a temporary file and rename.
8. Preserve source modification time.
9. Set readable file permissions.
10. Verify destination content with ordinary SHA256 and `cmp` or `diff`.
11. Write concise logs on failure without secrets.
12. Return nonzero on copy or verification failure.
13. Be idempotent.
14. Avoid deleting unrelated iCloud content.

The LaunchAgent must use:

- `WatchPaths` for the two source files;
- `RunAtLoad`;
- a low-frequency fallback interval;
- safe reload behavior;
- a readable error log.

If atomic file replacement prevents reliable file-level watching, diagnose first and then use the narrowest safe parent-directory watch rather than broad filesystem monitoring.

---

## 17. Git safety

Unless the current task explicitly authorizes them, Cursor must not:

- commit;
- push;
- merge;
- rebase;
- force-push;
- rewrite history;
- apply, drop, or delete stashes;
- discard unrelated changes;
- delete branches;
- deploy;
- alter secrets;
- mutate external services.

Before any approved Git write action:

1. inspect the full diff;
2. confirm unrelated changes are excluded;
3. scan for secrets;
4. run required validation;
5. document the intended commit or deployment;
6. update and sync the handoff.

A dirty working tree is not automatically an error. It must be described accurately and preserved unless the current task owns the changes.

---

## 18. Testing and validation standard

For each task:

1. Identify the smallest relevant validation set.
2. Run targeted tests during implementation.
3. Run broader affected regressions before completion.
4. Run full validation when risk and time justify it.
5. Never claim a suite passed if it was not run.
6. Distinguish local, CI, staging, and production evidence.
7. Include commit SHA for remote validation.
8. Record skipped tests and the reason.
9. Treat flaky or degraded evidence conservatively.
10. Preserve paper-only safety checks for all trading-related changes.

For AI behavior, include deterministic checks where possible and scaled evaluation when appropriate. For risk logic, deterministic unit and property-based tests are preferred.

---

## 19. Security and privacy standard

Never place in handoffs, changelogs, tracked governance, logs, prompts, or screenshots:

- full API keys;
- access tokens;
- passwords;
- broker or exchange secrets;
- private key material;
- full personal financial records;
- sensitive audit contents;
- unredacted authorization headers.

Use variable names, redacted fingerprints, boolean configuration indicators, or secret-manager references.

When a secret is required:

1. set status to `REVIEW_REQUIRED`;
2. state the environment-variable name only;
3. tell the operator where to enter it;
4. do not ask the operator to paste it into ChatGPT or Cursor chat;
5. resume only after confirmation.

---

## 20. One-time installation and reconciliation procedure

Use this section once to install this master standard into AlphaTrade AI.

### Goal

Merge the existing workflow, blocker protocol, and project rules into one coherent system without changing application behavior.

### Procedure

1. Verify the repository is `~/Developer/AlphaTrade-AI`.
2. Inspect Git status and preserve all unrelated work.
3. Back up any existing workflow documents before material replacement.
4. Save this document as:

```text
.ai/MASTER_WORKFLOW.md
```

5. Update `.ai/MASTER.md` to reference this document as the authoritative workflow standard.
6. Merge its requirements into:

```text
.ai/HANDOFF_TEMPLATE.md
.ai/SESSION_TEMPLATE.md
.ai/IMPLEMENT.md
.ai/BUGFIX.md
.ai/REFACTOR.md
.ai/SECURITY.md
.ai/RELEASE.md
.cursor/rules/
```

7. Use the five-status model defined here.
8. Remove the obsolete `DRAFT` status from project templates and rules.
9. Apply the normalized self-hash rule.
10. Ensure durable sanitized governance is tracked, while generated operational files remain ignored.
11. Preserve the existing sync script and LaunchAgent if they already meet this standard.
12. Validate the sync script, LaunchAgent, source/destination hashes, `cmp`, mtimes, and idempotency.
13. Regenerate `HANDOFF.md` as `READY` when installation is complete.
14. Sync and verify.
15. Do not implement product backlog tasks during this installation.

### Installation validation

Report:

1. repository path;
2. branch and commit;
3. initial and final Git status;
4. workflow files created or updated;
5. files tracked and ignored;
6. backups created;
7. secret scan result;
8. LaunchAgent validation;
9. source/destination hash verification;
10. idempotent second-sync result;
11. confirmation that application behavior was unchanged;
12. next task and recommended model.

---

## 21. Cleanup after successful installation

After Cursor confirms the master standard is installed, validated, synchronized, and available in `.ai/MASTER_WORKFLOW.md`, the following source prompt files may be deleted or archived because this document supersedes them:

```text
ALPHATRADE_AI_WORKFLOW_CATCHUP_PROMPT.md
ALPHATRADE_MOBILE_BLOCKER_HANDOFF_ADDENDUM.md
```

Also remove temporary duplicate copies from Downloads or iCloud after confirming the master document is safely stored.

Do **not** delete:

```text
.ai/
.cursor/rules/
HANDOFF.md
CHANGELOG_SESSION.md
~/.local/bin/sync-alphatrade-ai-handoff.sh
~/Library/LaunchAgents/com.sofien.alphatrade-ai-handoff-sync.plist
```

`HANDOFF.md` and `CHANGELOG_SESSION.md` may be regenerated or replaced by Cursor, but they remain part of the active workflow.

Do not delete timestamped backups until the new workflow has completed at least one successful implementation task and one successful mobile handoff cycle.

---

## 22. Standard operating workflow after installation

For every future AlphaTrade task:

1. ChatGPT reviews the uploaded `HANDOFF.md`.
2. ChatGPT recommends the best Cursor model.
3. ChatGPT provides one goal-oriented implementation prompt.
4. Cursor sets `HANDOFF.md` to `IN_PROGRESS` and syncs before coding.
5. Cursor implements in safe phases.
6. Cursor updates and syncs at every phase, blocker, approval, failure, and completion.
7. Cursor runs truthful validation.
8. Cursor sets `HANDOFF.md` to `READY` only when the stated scope is complete.
9. The user uploads the iCloud `HANDOFF.md` to ChatGPT.
10. ChatGPT reviews achievements, remaining work, risks, and progress, then prepares the next task.

This is the permanent ChatGPT ↔ Cursor loop.

---

## 23. Exact Cursor installation prompt

Use **Opus 4.8** and provide this master document to the active AlphaTrade AI Cursor agent with the following instruction:

```text
Final goal:

Install ALPHATRADE_AI_MASTER_WORKFLOW.md as the authoritative AlphaTrade AI ChatGPT ↔ Cursor workflow, reconcile the existing .ai and .cursor rules with it, preserve all stricter safety requirements, and validate the complete mobile handoff system without changing application behavior or implementing product features.

Requirements:

1. Inspect the repository, current Git state, existing .ai files, .cursor/rules, HANDOFF.md, CHANGELOG_SESSION.md, sync script, LaunchAgent, and iCloud destination before changing anything.
2. Preserve unrelated and uncommitted work.
3. Back up any workflow file before material replacement.
4. Save the supplied master document as .ai/MASTER_WORKFLOW.md.
5. Make .ai/MASTER_WORKFLOW.md authoritative from .ai/MASTER.md.
6. Merge its status, metadata, blocker, review, failure, sync, security, Git, broker/exchange, testing, and cleanup rules into the relevant templates and Cursor rules.
7. Use only these handoff statuses: IN_PROGRESS, REVIEW_REQUIRED, BLOCKED, FAILED, READY.
8. Remove obsolete DRAFT status references.
9. Implement the normalized Source File SHA256 rule exactly as documented.
10. Keep HANDOFF.md and CHANGELOG_SESSION.md ignored and mobile-synchronized.
11. Track sanitized durable .ai governance and .cursor/rules unless repository policy proves this unsafe; place sensitive local material under ignored .ai/private or .ai/local.
12. Do not modify application code.
13. Do not implement any product backlog task.
14. Do not expose secrets.
15. Do not commit, push, or deploy unless I explicitly authorize that after reviewing the diff and validation report.
16. Before further changes, regenerate HANDOFF.md with the current task state as IN_PROGRESS and run the sync once.
17. At every major phase, review requirement, blocker, failed validation, and final completion, update both operational documents and sync immediately.
18. Validate the script with bash syntax checks, the plist with plutil, LaunchAgent state, SHA256, cmp or diff, mtimes, and an idempotent second sync.
19. Finish with HANDOFF.md status READY only if the installation and verification succeed.

Final report:

1. verified repository facts
2. conflicts found and how they were reconciled
3. files created, updated, backed up, tracked, and ignored
4. status-model migration result
5. self-hash implementation result
6. sync and LaunchAgent validation
7. secret scan result
8. application-code-change confirmation
9. final Git status
10. whether a commit is recommended
11. exact cleanup list for obsolete source documents
12. next recommended model and task

Stop with REVIEW_REQUIRED before any commit, push, deployment, destructive action, secret entry, or external-service mutation.
```

---

## 24. Definition of done

This workflow is installed only when:

- the master document is stored under `.ai/MASTER_WORKFLOW.md`;
- permanent rules reference it;
- statuses are unified;
- normal and blocked flows are implemented in templates and rules;
- generated handoff files are ignored;
- durable sanitized governance is available after a fresh clone;
- the sync script and LaunchAgent pass validation;
- iCloud source and destination copies match;
- blockers synchronize immediately;
- application behavior remains unchanged;
- a final `READY` handoff is available for ChatGPT.

