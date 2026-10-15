# AlphaTrade AI Master Workflow and Handoff Standard

**Version:** 2.1 (Autonomous AI Delivery Playbook v1.1 reconciliation)

**Project:** AlphaTrade AI  
**Project slug:** `alphatrade-ai`  
**Task prefix:** `AT`  
**Repository:** `~/Developer/AlphaTrade-AI`  
**Capability profiles:** `base`, `agentic`, `high_risk`, `trading`

---

## 1. Purpose

This document is the existing operating standard for AlphaTrade AI's accountable delivery owner, repository and mobile handoff. The Autonomous AI Delivery Playbook v1.1 is adopted here as process guidance, preserving current work, authority and platform controls. It grants no credentials, spending, deployment or exchange permission.

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
- how the owner continues a complete milestone and reports exact acceptance and blockers.

This document is not product code. Installing or updating this workflow must not alter AlphaTrade AI application behavior unless a later task explicitly authorizes implementation work.

The routine approval, per-edit publication and manual ChatGPT ↔ Cursor relay requirements in the former installation workflow are superseded by §§8–12 and §§20–24 below. Existing authorized implementation continues; adopting process guidance is not a new audit or installation project.

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
9. **Record blockers promptly; pause the affected operation and continue independent authorized milestone work.**
10. **Real trading remains disabled unless a separate, explicit, safety-reviewed future task authorizes a controlled change.**
11. **One owner implements and integrates; up to two workers may handle independent scoped work.**
12. **Fix named acceptance criteria before implementation and separate implemented, integrated, deployed and live-accepted evidence.**
13. **Reuse valid evidence, batch related publication and preserve the single final reviewed release gate.**

---

## 3. Source-of-truth precedence

For authority, platform controls and applicable safety constraints remain binding. Apply the user's current task and standing grants within those boundaries; local process guidance cannot revoke an already authorized routine step or create new permissions. Preserve explicit scope and safety limits when reconciling obsolete ceremony.

For factual claims, prefer current repository evidence for implementation, actual deployment receipts for installed revisions, and observed user-route results for live acceptance. Match tests to their exact revision and inputs. Use `.ai/DECISIONS.md`, `.ai/TASKS.md`, the current HANDOFF.md and CHANGELOG_SESSION.md as concise durable context; worker reports and chat supply intent and leads, not proof of deployed behavior.

Never change code merely to make an outdated document appear correct. Update the document to reflect verified reality.

---

## 4. Canonical paths

### Repository and execution environment

```text
~/Developer/AlphaTrade-AI
```

This is the existing Mac checkout path, not a cloud prerequisite. Verify the actual task workspace path and revision. A managed cloud worktree is valid; do not relocate or restart active work merely to match the Mac path. If the active checkout itself is inside a filesystem syncing service, pause unsafe Git writes and prepare a safe relocation while preserving work.

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

The delivery owner follows this lifecycle through the whole agreed milestone. The user does not relay worker outputs or provide a new prompt for each internal step.

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
2. Record outcome, scope, owner, execution environment, baseline, safety posture, existing authority and a small named pass/fail acceptance checklist in the existing task/handoff. Fix its denominator before implementation; show justified scope changes rather than removing failures or padding progress.
3. Resume the existing CHANGELOG_SESSION.md or start the current session record without discarding useful work or valid evidence. Keep technical choices flexible.
4. Perform one focused prerequisite pass for the next operations. Reuse valid access receipts; distinguish an inventory denial from absent credentials. Bundle missing access by exact operation/target, never secret value.
5. Sync when available per §12. Unavailable Mac/iCloud access is recorded, not a prerequisite stopping independent cloud work.

### Step 3: Implement in scoped phases

At meaningful milestones, blockers and interruption boundaries:

1. Update `HANDOFF.md` with completed work and current phase.
2. Update `CHANGELOG_SESSION.md` with commands and results.
3. Preserve a compact checkpoint; batch authorized publication and verify actual source equality per §12.
4. Continue within standing authority without routine reconfirmation. Select the next unmet criterion, diagnose, make the smallest sufficient change and run a check that distinguishes success from failure.
5. Use up to two independent workers only where useful. Supply base revision, allowed files, interface, acceptance criteria and prohibited shared changes; collect their commits/evidence directly. Keep one writer for schema sequencing, deployment, credentials and runtime configuration.
6. Before retrying an interrupted external mutation, reconcile actual state and operation identity; a timeout does not prove nothing happened. After repeated unchanged failure, change the diagnosis or report the specific dependency.

### Step 4: Validate

Run focused checks for changed behavior and concrete adjacent risks. Required consolidated and full-release checks follow .ai/RELEASE.md; do not run the complete backend suite after each fix or repeat green checks merely for progress.

Validation evidence must include:

- exact command;
- exit status;
- relevant result counts;
- skipped or unavailable checks;
- honest explanation of what was not run;
- exact tested revision and relevant input/configuration equivalence when reusing evidence;
- named accepted/total criteria and separate implementation, integration, deployment and live acceptance.

### Step 5: Finish

When the task is complete:

1. Set `HANDOFF.md` to `READY`.
2. Regenerate all current-state sections.
3. Finalize `CHANGELOG_SESSION.md`.
4. Sync/publish when available and authorized per §12.
5. Verify actual published bytes using SHA256 and `cmp` or `diff`; record unavailable downstream verification explicitly.
6. Report Git status and whether code was committed or pushed.
7. Record exact candidate/deployment revisions or unknown, acceptance counts, remaining external dependency and next executable operation. Finish when the complete agreed scope passes or only specific external dependencies remain.

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

A genuinely new material decision, missing authority or required human/platform action prevents an operation. Already authorized routine engineering does not require this status.

Pause before that protected operation; continue independent authorized work for the same milestone. Missing access is reported precisely without implying a secret is absent.

#### `BLOCKED`

A specific technical or environmental dependency remains after scoped diagnostics and prevents the remaining operation. Continue independent work before returning the blocker.

#### `FAILED`

The task or a critical validation failed and cannot be represented as successful. The repository must be left in a safe, documented state.

#### `READY`

The current stated scope is complete and validated at its declared evidence level, with a durable checkpoint and truthful publication status. READY does not imply deployment or live acceptance unless their criteria actually passed.

Do not use `DRAFT`. `IN_PROGRESS` replaces it.

---

## 10. Normal workflow without blockers

```text
Task received
→ Verify current branch, checkpoint and relevant instructions
→ Fix named acceptance and confirm standing authority
→ HANDOFF = IN_PROGRESS; check actual prerequisites once
→ Implement the complete path; integrate scoped worker results directly
→ Run focused checks; update checkpoint at meaningful milestones
→ Close material findings; perform authorized release gates when due
→ Record accepted/total and implementation/integration/deployment/live evidence
→ HANDOFF = READY for completed stated scope
→ Batch authorized publication; verify source equality or record unavailable sync
→ Report exact evidence and next executable operation
```

The owner resumes this same assignment across interruptions. Manual user uploads or a new ChatGPT/Cursor prompt are not prerequisites for each phase.

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

1. Pause safely before the affected operation; prepare a concrete reviewable result first where possible.
2. Set `HANDOFF.md` to `REVIEW_REQUIRED`.
3. Document the exact human action needed.
4. Record what was completed and what remains unchanged.
5. Record whether code or configuration was modified.
6. Record uncommitted work.
7. Record the blocker promptly and sync when available per §12.
8. Bundle genuine missing inputs into one packet: blocked operation, missing access/authority, recommended action, exact verified target/destination, consequence and completed preparation. Continue independent authorized milestone work; resume the protected operation only after the required approval/access is actually available.

### Recoverable technical blocker

1. Run only safe, scoped diagnostics.
2. Do not make speculative destructive changes.
3. If unresolved, set `HANDOFF.md` to `BLOCKED`.
4. Record the failed command and concise error evidence.
5. Record the last successful step.
6. Record and publish the checkpoint per §12 without repeated access prompts.
7. Continue independent work. Retry only with a corrected input, changed configuration, new explanation or bounded transient policy; after repeated identical failure, change strategy or identify the external dependency.

### Failed task or critical validation

1. Preserve evidence; diagnose and repair the specific failure with focused checks. Stop only unsafe or unauthorized operations.
2. Preserve evidence and current work.
3. Set `HANDOFF.md` to `FAILED`.
4. Explain the failure without claiming success.
5. State whether rollback occurred or is needed.
6. Record the checkpoint promptly and sync when available per §12.
7. Continue authorized repairs and independent work; do not claim acceptance or advance a release through failed gates. Diagnostic publication requires its own task scope and truthful failed status.

---

## 12. Checkpoints and publication

Update the existing HANDOFF.md and CHANGELOG_SESSION.md promptly at:

1. Task start
2. Meaningful acceptance/integration milestones
3. Any blocker
4. Any review or approval requirement
5. Any failed command or test that prevents progress
6. Any permission or environment issue
7. Any meaningful safety-state change
8. Before a destructive action
9. After a destructive action
10. Final task completion
11. Owner interruption and completion boundaries — cloud or local

Every checkpoint includes current task/owner, revision, named accepted/total criteria,
evidence, completed and pending work, installed deployment identity or unknown, safety state,
the specific blocked operation and exact next executable action. Keep the existing template
and Generated At UTC; do not create another management system or a document after each edit.
Workers return scoped results to the owner rather than competing to overwrite the checkpoint.

### Local Mac worktree sync

When this Mac worktree and sync script are available, run:

```bash
~/.local/bin/sync-alphatrade-ai-handoff.sh
```

Then verify:

- source SHA256;
- destination SHA256;
- `cmp` or `diff` success;
- expected destination paths;
- nonzero failure if verification fails.

Record an actual sync failure honestly and preserve its evidence. If the Mac/iCloud/script
is unavailable in a cloud environment, record mobile publication as unexecuted; this does
not stop independent implementation, focused checks or already authorized Git publication.

### GitHub publish for cloud → iPhone sync

When publication is authorized and Git access is available, batch the regenerated handoff
with the next meaningful milestone/blocker/completion publication (dedicated `cursor/*handoff*` branch
when a product PR must stay clean; force-add only the ignored handoff files;
do not open a PR for handoff-only branches). The Mac LaunchAgent
`com.alphatrade.handoff-sync` discovers the newest `Generated At UTC` and
copies it to iCloud automatically.

Verify the intended published revision, normalized Source File SHA256 and actual remote
document bytes against the source with ordinary SHA256 plus cmp/diff. This proves source
publication, not the downstream Mac/iCloud copy; verify that copy when accessible and mark
it unknown otherwise. Preserve the existing discovery behavior and LaunchAgent.

Record a blocker immediately; do not require an extra push for each edit or internal phase,
nor ask the user to manually relay the checkpoint before continuing authorized work.

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

These existing template fields remain for compatibility. Record the current owner/model
and exact next operation; use "No intervention required" when appropriate. They do not
require a model switch, user upload or new ChatGPT/Cursor prompt for each internal phase.

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

Apply the current task and standing grants once within their defined scope. Authorized
routine commits, batched pushes, isolated branches/worktrees, PR updates and reviewed feature
integration need no repeated approval at internal phase boundaries. Do not infer authority
for main merge, deployment, destructive history changes, credential replacement, financial
actions or new paid resources from permission to implement.

Without the applicable authorization, the owner must not:

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

Before an authorized Git write action:

1. inspect the full diff;
2. confirm unrelated changes are excluded;
3. scan for secrets;
4. run required validation;
5. document the intended commit or deployment;
6. preserve the current checkpoint and record the resulting revision at the meaningful
   publication boundary; unavailable mobile sync does not gate the Git action.

A dirty working tree is not automatically an error. It must be described accurately and preserved unless the current task owns the changes.

---

## 18. Testing and validation standard

For each task:

1. Identify the smallest relevant validation set.
2. Run targeted tests during implementation.
3. Run broader affected regressions before completion.
4. Follow .ai/RELEASE.md for consolidated validation: the complete backend gate runs once
   on the frozen candidate after supervising review, coordinated staging and fresh SFP
   prerequisites, with explicit full-acceptance authority. No full suite during repairs.
5. Never claim a suite passed if it was not run.
6. Distinguish local, CI, staging, and production evidence.
7. Include exact revision for every result; reused evidence requires equivalent relevant
   code, configuration, dependencies and inputs, not merely an older green badge.
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

1. identify the exact blocked operation and executor; distinguish missing access from an
   inability to inspect configuration;
2. state only the identity/secret reference, required scope/expiry and configuration field;
3. give the verified destination in one consolidated action packet, without inventing an
   upload mechanism or assuming reusable environment changes reached the current task;
4. do not ask the operator to paste it into ChatGPT or Cursor chat;
5. pause that operation until its actual required access is available; continue independent
   authorized work and record the truthful checkpoint status.

---

## 20. Reconciliation of the former installation workflow

The one-time installation prompt and permanent manual ChatGPT ↔ Cursor loop from
version 2.0 are obsolete. They must not require renewed routine commit/push approval,
a fresh audit, a model switch, user-relayed worker messages or a mobile upload before
the owner continues the active milestone. Preserve existing authority and work.

Apply the adopted playbook through AGENTS.md, this workflow, the relevant .ai task
instructions and .cursor/rules. Reuse the existing task and compact handoff. Do not
create new control planes, ledgers, infrastructure or unrelated process backlog.
Instruction changes do not prove platform permissions or hosting settings changed.

---

## 21. Preserve existing assets

Keep .ai/, .cursor/rules/, HANDOFF.md, CHANGELOG_SESSION.md and the existing mobile
sync script/LaunchAgent. Preserve safe tracking/ignore conventions, source equality
verification and normalized self-hashes. Do not delete unrelated documents, backups,
branches or cloud resources as part of process adoption.

---

## 22. Accountable owner workflow

1. Read the active task and checkpoint; verify branch, preserved work and relevant evidence.
2. Fix outcome, named acceptance, owner, target environment and existing authority once.
3. Check required prerequisites in one focused pass; bundle genuine missing access.
4. Implement the complete path and directly integrate reviewed independent contributions.
5. Close material review findings in a coherent batch with focused checks and exact evidence.
6. Run authorized release operations only after their repository prerequisites pass.
7. Record accepted/total and implemented/integrated/deployed/live-accepted states separately.
8. Publish the compact checkpoint at meaningful boundaries and verify actual source equality.
9. Continue until the milestone passes or a specific external dependency blocks the remaining
   operation; return the next executable action, not another general roadmap.

Optional product discussion or review may occur through ChatGPT/Cursor. It is not a
mandatory manual relay or repeated authorization loop.

---

## 23. Safe resumption and feedback

Resume the same milestone from the current handoff. Verify subsequent changes and
reconcile external operations left in progress before retrying. Reuse valid evidence;
do not restart an audit because a session changed.

The owner deduplicates review findings, checks their behavior/evidence/severity, then
accepts and fixes, rejects with evidence or defers with a reason. Recheck the changed
area rather than repeat a complete review. Escalate only a material product/authority
tradeoff; do not add reviewers indefinitely or weaken acceptance to hide a failure.

---

## 24. Definition of done for a delivery milestone

- The agreed named acceptance passes at its declared environment and evidence level.
- Reviewed contributions are integrated; shared contracts and immutable migration ancestry
  agree with the actual candidate when affected.
- Focused checks and required release gates have exact revision/count/skip evidence.
- Operator, financial, privacy, secret and execution boundaries remain intact.
- Candidate and installed revisions are explicit; unavailable performance and unknown or
  unexecuted deployment/live acceptance remain explicit.
- The compact existing handoff contains acceptance counts, evidence, rollback reference,
  specific remaining dependency and next executable operation.
- Actual publication/sync equality is verified where available; an unavailable Mac copy is
  recorded without claiming it succeeded or discarding completed cloud work.

An essential failed criterion still blocks acceptance even if the named percentage is
high. Implementation completion, merge, deployment and live acceptance are separate facts.
