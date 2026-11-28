# AlphaTrade agent reporting (Codex CLI, Claude Code, Cursor, Codex web)

Ported from Fejjii-labs/SOF PR 238 and branch `agent-reporter/shared-ingest`.

Every agent run, including failed and blocked runs, publishes one sanitized
report as a comment on the relevant PR or issue (`--target N`), marked
`<!-- alphatrade-agent-report:v1 -->` and read back to verify. Fields:
findings, fixes, solutions, blockers, tests, next step, head SHA, task ID
(schema: `scripts/agent_reporting/agent_completion.schema.json`).
GitHub is canonical. Notion **Agent Reports** is an optional mirror. This
complements, and does not replace, the Agent Reports duty in `AGENTS.md`.

## Codex CLI (supervised)
    python3 scripts/agent_reporting/agent_report_runner.py run --task-id T \
      --target 123 --prompt-file prompt.md --report-dir ~/.alphatrade-agent-reports/T-1
A timeout, non-zero exit or invalid final output still publishes a `failed`
report. Retry publication only: `... publish --report-dir DIR --target 123`.

## Codex exec / Claude Code headless
    claude -p "$(cat prompt.md docs/infrastructure/agent-reporting/PROMPT_FOOTER.md)" \
      --output-format json > out.json
    python3 scripts/agent_reporting/agent_report_ingest.py --task-id T --target 123 \
      --agent "Claude Code (local)" --input out.json \
      --report-dir ~/.alphatrade-agent-reports/T-1

## Claude Code interactive (Stop hook)
Add to `.claude/settings.local.json` (not committed):

    {"hooks": {"Stop": [{"hooks": [{"type": "command",
      "command": "$CLAUDE_PROJECT_DIR/docs/infrastructure/agent-reporting/claude-stop-hook.sh"}]}]}}

Export `ALPHATRADE_TASK_ID` and `ALPHATRADE_REPORT_TARGET` first. Needs `jq`, `gh`.

## Cursor / Codex web
No hook or export. Append `PROMPT_FOOTER.md` to the prompt and pipe the final
JSON block into the ingest script with `--input -`. Coverage stays manual.

## Gate
`.github/workflows/agent-reports.yml` is read-only and cheap (sparse checkout,
stdlib Python, no installs, no CI jobs, 5-minute timeout). Drafts are deferred.
For ready PRs it looks for a trusted (owner/member/collaborator) complete report
for the exact head SHA on the PR, or on an issue linked with
`- Agent report issue: https://github.com/Fejjii-labs/AlphaTrade-AI/issues/N`.
It only warns unless the repository variable `AGENT_REPORT_GATE_ENFORCE=true`
is set (a repo-settings change that needs Sofien's approval).

## Notion (optional)
Set `NOTION_TOKEN` (an integration shared with the Agent Reports database) and
`NOTION_AGENT_REPORTS_DB` in the local environment; optional
`NOTION_AGENT_REPORTS_TITLE_PROP` (default `Task Name`). Without them the mirror
is skipped with a notice. A Notion failure never affects the GitHub report.
