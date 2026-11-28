#!/usr/bin/env bash
# Example Claude Code Stop hook for AlphaTrade AI. Install via
# .claude/settings.local.json (not committed; see README.md). Reads only the LAST
# assistant message from the hook's transcript_path, never the full log.
set -euo pipefail
input="$(cat)"
# Avoid loops: Claude sets stop_hook_active when a Stop hook already continued.
[ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false')" = "true" ] && exit 0
transcript="$(printf '%s' "$input" | jq -r '.transcript_path')"
task="${ALPHATRADE_TASK_ID:?set ALPHATRADE_TASK_ID before starting claude}"
target="${ALPHATRADE_REPORT_TARGET:?set ALPHATRADE_REPORT_TARGET (PR or issue number)}"
last="$(mktemp)"; trap 'rm -f "$last"' EXIT
jq -rs '[.[] | select(.type=="assistant") | .message.content[]? | select(.type=="text") | .text] | last // ""' \
  "$transcript" > "$last"
# Every stop is reported: without a structured block, ingest publishes a
# sanitized "failed" report instead of skipping.
python3 "$CLAUDE_PROJECT_DIR/scripts/agent_reporting/agent_report_ingest.py" \
  --task-id "$task" --target "$target" --agent "Claude Code (local, Stop hook)" \
  --input "$last" --cwd "$CLAUDE_PROJECT_DIR" \
  --report-dir "${ALPHATRADE_REPORT_ROOT:-$HOME/.alphatrade-agent-reports}/$task-$(date +%s)" >&2 || true
