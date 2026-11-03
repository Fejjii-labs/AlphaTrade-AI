
---
FINAL REPORT REQUIREMENT (AlphaTrade AI): End your final message with exactly one
fenced ```json block matching scripts/agent_reporting/agent_completion.schema.json,
also when the run failed or is blocked:
{"status": "complete|blocked|failed", "findings": [...], "fixes": [...],
 "solutions": [...], "blockers": [...], "tests": [...commands and results, or not-run reasons...],
 "next_step": "<exact next action>", "final_output": "<80+ char summary>"}
No credentials, API keys, personal data, or connection strings. Do not post it yourself.
