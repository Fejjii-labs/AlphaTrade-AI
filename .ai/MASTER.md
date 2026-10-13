# AlphaTrade AI — MASTER (AI Collaboration Index)

> Version-controlled project governance for AI assistants (not application code).
> This `.ai/` layer and `.cursor/rules/` are committed so any clone gets the same guidance.
> Generated handoff artifacts (`HANDOFF.md`, `CHANGELOG_SESSION.md`) stay gitignored/local.
> Purpose: give any AI assistant (Cursor / ChatGPT) a consistent, truthful entry point.

## Authoritative workflow standard

**`.ai/MASTER_WORKFLOW.md` (v2.0) is the single source of truth for the ChatGPT ↔ Cursor
workflow.** It supersedes any earlier catch-up prompt or blocker addendum. When this index or
any workflow doc conflicts with `MASTER_WORKFLOW.md`, follow `MASTER_WORKFLOW.md` and update the
other file. It defines the task lifecycle, the five-status model, mandatory sync moments, the
handoff/changelog formats, the normalized self-hash rule, Git/security/broker-exchange safety,
and cleanup policy. Machine-local or sensitive material lives under ignored `.ai/local/` or
`.ai/private/`.

## Project identity

| Field | Value |
|-------|-------|
| Project name | AlphaTrade AI |
| Slug | alphatrade-ai |
| Task prefix | AT |
| Capability profiles | base, agentic, high_risk, trading |
| Remote | https://github.com/Fejjii/AlphaTrade-AI.git |
| Local checkout | Path varies per machine; clone of the remote above |

## How to use this layer

1. Read `PROJECT_CONTEXT.md` for what the system is and hard safety rules.
2. Read `ARCHITECTURE.md` for the verified technical map.
3. Read `MASTER_WORKFLOW.md` for the authoritative lifecycle, statuses, and sync rules.
4. Pick the workflow doc for the task type:
   - `AUDIT.md` — inspect / report only
   - `IMPLEMENT.md` — build a feature (paper-safe)
   - `BUGFIX.md` — fix a defect
   - `REFACTOR.md` — restructure without behavior change
   - `SECURITY.md` — security / trading-safety hardening
   - `RELEASE.md` — release / deploy validation
   - `LINKEDIN_DEMO.md` — portfolio / demo packaging
5. Track durable decisions in `DECISIONS.md` (AT-ADR-XXX).
6. Track work in `TASKS.md` (AT-XXX).
7. Use the handoff status model: `IN_PROGRESS`, `REVIEW_REQUIRED`, `BLOCKED`, `FAILED`, `READY`
   (no `DRAFT`). Sync at task start, every phase boundary, every blocker/review/failure, and
   completion — never only at the end.
8. End every task by regenerating root `HANDOFF.md` + `CHANGELOG_SESSION.md` (per templates)
   and running the handoff sync, then verifying source/destination SHA256 + `cmp`.
9. After every task, prompt, or investigation, follow root `AGENTS.md` (Agent Reports)
   and `.cursor/rules/95-agent-reports.mdc`. Applies to all current active repos
   (SOF AI, DataPilot AI, AlphaTrade AI) and every future Simply AI project from day one.

## Non-negotiable safety (summary — full text in PROJECT_CONTEXT.md)

- Paper only. `EXECUTION_MODE=paper`, `ENABLE_REAL_TRADING=false`, `EXCHANGE_MODE=paper_internal`, `PROVIDER_MODE=fallback` (staging).
- No live orders, withdrawals, transfers, leverage changes, or exchange account mutations.
- No commit / push / merge / rebase / history rewrite unless the task explicitly authorizes it.
- No secrets printed or committed.
- Verified repository facts only. Mark unknowns as UNKNOWN.

## Canonical handoff files

Generated per session; gitignored (never committed):

- Source (repo root): `HANDOFF.md`, `CHANGELOG_SESSION.md`
- Optional local mirror + automation (machine-specific, outside the repo):
  - iCloud mirror folder under `~/Library/Mobile Documents/com~apple~CloudDocs/...`
  - Sync script: `~/.local/bin/sync-alphatrade-ai-handoff.sh`
  - LaunchAgent: `~/Library/LaunchAgents/<your-label>.alphatrade-ai-handoff-sync.plist`
    (expected: `WatchPaths` on the two handoff files, `RunAtLoad`, low-frequency
    `StartInterval` fallback)

Normalized self-hash for operational docs (§13.3 of `MASTER_WORKFLOW.md`):

```bash
grep -v '^Source File SHA256:' HANDOFF.md | shasum -a 256
grep -v '^Source File SHA256:' CHANGELOG_SESSION.md | shasum -a 256
```
