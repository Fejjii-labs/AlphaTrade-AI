"""Publish a structured final report from any local agent run (Codex, Claude Code).

Every run is reported, including failed and blocked ones: when the agent output
holds no valid report, a sanitized supervisor-generated ``failed`` report is
published instead. GitHub (PR/issue comment, marker
``<!-- alphatrade-agent-report:v1 -->``) is canonical and is read back. Notion
Agent Reports is an optional mirror, used only when NOTION_TOKEN and
NOTION_AGENT_REPORTS_DB are set in the environment. Tokens and raw agent output
are never printed.

Accepted --input formats:
  * codex --output-last-message file (the schema JSON object)
  * claude -p --output-format json envelope ({"result": "<text with JSON>"})
  * any text containing exactly one JSON object matching the schema
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_report_runner import (
    REPO,
    RunnerError,
    build_report,
    failure_output,
    publish,
    save,
    snapshot,
    validate_output,
)

NOTION_VERSION = "2022-06-28"
MAX_INPUT = 200000


def extract(raw: str) -> dict[str, Any]:
    """Return the schema object from codex/claude output."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and "status" in data and "final_output" in data:
        return data
    if isinstance(data, dict) and data.get("is_error"):
        raise RunnerError("Agent reported an error result")
    text = data.get("result", "") if isinstance(data, dict) else raw
    if not isinstance(text, str):
        raise RunnerError("No textual result found in agent output")
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = fenced or [text[text.find("{") : text.rfind("}") + 1]]
    found = []
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "final_output" in obj:
            found.append(obj)
    if len(found) != 1:
        raise RunnerError("Expected exactly one structured report object")
    return found[0]


def load_output(raw: str) -> dict[str, Any]:
    """Parse and validate, falling back to a failure report (never raises)."""
    try:
        if len(raw) > MAX_INPUT:
            raise RunnerError("Agent output exceeds size bound")
        output = extract(raw)
        validate_output(output)
        return output
    except RunnerError as exc:
        return failure_output(str(exc))


def _chunks(text: str, size: int = 1900) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def notion_payload(
    report: dict[str, Any], database_id: str, title_prop: str, comment_url: str
) -> dict[str, Any]:
    def rich(text: str) -> list[dict[str, Any]]:
        return [{"type": "text", "text": {"content": text[:1900]}}]

    def para(text: str) -> dict[str, Any]:
        return {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": rich(text)},
        }

    def heading(text: str) -> dict[str, Any]:
        return {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": rich(text)},
        }

    children = [
        para(
            f"Repo {REPO} | Branch {report['branch']} | Head {report['head_sha']} | "
            f"Task {report['task_id']} | Status {report['status']}"
        ),
        para(f"GitHub report (canonical): {comment_url}"),
    ]
    for field, label in (
        ("findings", "Findings"),
        ("fixes", "Fixes"),
        ("solutions", "Solutions"),
        ("blockers", "Blockers"),
        ("tests", "Tests"),
    ):
        children.append(heading(label))
        children += [para(f"- {item}") for item in report[field]] or [para("None.")]
    children += [
        heading("Next step"),
        para(report["next_step"]),
        heading("Final output"),
    ]
    children += [para(chunk) for chunk in _chunks(report["final_output"])]
    title = (
        f"AlphaTrade AI — {report['task_id']} — {report['agent']} — {report['status']}"
    )
    return {
        "parent": {"database_id": database_id},
        "properties": {title_prop: {"title": rich(title[:200])}},
        "children": children[:100],
    }


def mirror_notion(
    report: dict[str, Any],
    comment_url: str,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> str:
    token = os.environ.get("NOTION_TOKEN", "")
    database = os.environ.get("NOTION_AGENT_REPORTS_DB", "")
    if not token or not database:
        return "skipped: NOTION_TOKEN or NOTION_AGENT_REPORTS_DB not set"
    payload = notion_payload(
        report,
        database,
        os.environ.get("NOTION_AGENT_REPORTS_TITLE_PROP", "Task Name"),
        comment_url,
    )
    request = urllib.request.Request(
        "https://api.notion.com/v1/pages",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    try:
        with opener(request, timeout=30) as response:
            page = json.loads(response.read())
    except Exception:  # noqa: BLE001  # never echo provider errors, which may include headers
        return "failed: Notion request failed; GitHub report remains canonical"
    return f"created: {page.get('url', page.get('id', 'unknown'))}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--agent", required=True, help="e.g. 'Claude Code (local)'")
    parser.add_argument("--input", required=True, help="File with agent output, or -")
    parser.add_argument("--target", type=int, required=True, help="PR or issue number")
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--baseline-sha", default="")
    parser.add_argument("--report-dir", required=True, help="Private, untracked dir")
    parser.add_argument("--gh", default="gh")
    parser.add_argument("--no-notion", action="store_true")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", args.task_id):
        parser.error("task ID must be a short engineering identifier")
    try:
        try:
            raw = (
                sys.stdin.read() if args.input == "-" else Path(args.input).read_text()
            )
        except OSError:
            raw = ""
        report = build_report(
            load_output(raw),
            task_id=args.task_id,
            agent=args.agent,
            git_state=snapshot(Path(args.cwd).resolve()),
            baseline_sha=args.baseline_sha,
            source="agent final message",
        )
        directory = Path(args.report_dir).resolve()
        directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        save(
            directory / "state.json",
            {"task_id": args.task_id, "status": "report_pending"},
        )
        save(directory / "report.json", report)
        code = publish(directory, args.gh, args.target)
        state = json.loads((directory / "state.json").read_text())
        notion = (
            "skipped: --no-notion"
            if args.no_notion
            else mirror_notion(report, state.get("comment_url", ""))
        )
        state["notion_mirror"] = notion
        save(directory / "state.json", state)
        print(f"Notion mirror: {notion}")
        return code
    except RunnerError as exc:
        print(f"agent-report-ingest: report_pending; {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError):
        print(
            "agent-report-ingest: report_pending; inspect private receipt",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
