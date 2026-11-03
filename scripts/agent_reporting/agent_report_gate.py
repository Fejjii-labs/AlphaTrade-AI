"""Check that a ready AlphaTrade PR carries a trusted agent report for its head SHA.

Read-only GitHub API calls only. No workflow dispatch, no CI, no secrets beyond
the workflow's own read-only GITHUB_TOKEN. The report is looked up in the PR's
own conversation, plus an optional linked issue named in the PR body with a line
``- Agent report issue: https://github.com/OWNER/REPO/issues/N``.

Draft PRs are deferred. Enforcement is opt-in (AGENT_REPORT_GATE_ENFORCE=true);
otherwise a missing report is reported as a warning and the check passes, so
existing PRs are not turned red by this rollout.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

MARKER = "<!-- alphatrade-agent-report:v1 -->"
DEFAULT_REPO = "Fejjii-labs/AlphaTrade-AI"
SHA = re.compile(r"^[0-9a-f]{40}$")
TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}
STATUSES = {"complete", "blocked", "failed"}
REQUIRED_SECTIONS = (
    "Findings",
    "Fixes",
    "Solutions",
    "Blockers",
    "Tests",
    "Next Step",
    "Output",
)
ISSUE_URL = re.compile(
    r"^\s*- Agent report issue: https://github\.com/"
    r"(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/issues/(?P<number>[1-9]\d*)\s*$",
    re.MULTILINE,
)


class ReportGateError(ValueError):
    """A missing or unverifiable report keeps review readiness blocked."""


def linked_issue(body: str, repo: str) -> int | None:
    matches = list(ISSUE_URL.finditer(body or ""))
    if not matches:
        return None
    if len(matches) != 1 or matches[0].group("repo") != repo:
        raise ReportGateError("At most one same-repo 'Agent report issue' line allowed")
    return int(matches[0].group("number"))


def _section(text: str, heading: str) -> str:
    match = re.compile(
        rf"^## {re.escape(heading)}\s*$\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL
    ).search(text)
    return match.group(1).strip() if match else ""


def report_status(body: str, *, repo: str, head_sha: str, task_id: str = "") -> str:
    """Return the report status for a well-formed matching report, else ''."""
    if MARKER not in body or not SHA.fullmatch(head_sha):
        return ""
    lines = set(body.splitlines())
    expected = [f"Repo: {repo}", f"Head SHA: {head_sha}"]
    if task_id:
        expected.append(f"Task ID: {task_id}")
    if any(line not in lines for line in expected):
        return ""
    status = next((s for s in STATUSES if f"Status: {s}" in lines), "")
    if not status:
        return ""
    for heading in REQUIRED_SECTIONS:
        value = _section(body, heading)
        if not value or value in {"TODO", "TBD", "pending", "None provided"}:
            return ""
    return status if len(_section(body, "Output")) >= 80 else ""


def valid_report(body: str, *, repo: str, head_sha: str, task_id: str = "") -> bool:
    """A complete report is what unblocks a ready PR."""
    return (
        report_status(body, repo=repo, head_sha=head_sha, task_id=task_id) == "complete"
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def github_get(path: str, token: str) -> Any:
    if not token:
        raise ReportGateError("GITHUB_TOKEN is unavailable for report verification")
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=10) as resp:
            return json.load(resp)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise ReportGateError("GitHub report readback failed") from exc


def find_report(
    repo: str,
    number: int,
    head_sha: str,
    token: str,
    task_id: str = "",
    getter: Callable[[str, str], Any] = github_get,
) -> int | None:
    """Return the id of a trusted complete report comment on issue/PR ``number``."""
    for page in range(1, 11):
        comments = getter(
            f"/repos/{repo}/issues/{number}/comments?per_page=100&page={page}", token
        )
        if not isinstance(comments, list):
            raise ReportGateError("GitHub comments response is invalid")
        for comment in comments:
            # Comments are untrusted input: only repository writers count.
            if comment.get("author_association") not in TRUSTED:
                continue
            if valid_report(
                comment.get("body") or "", repo=repo, head_sha=head_sha, task_id=task_id
            ):
                return int(comment.get("id") or 0)
        if len(comments) < 100:
            break
    return None


def check_event(
    event: dict[str, Any],
    repo: str,
    token: str,
    enforce: bool,
    getter: Callable[[str, str], Any] = github_get,
) -> int:
    pr = event.get("pull_request") or {}
    if not pr:
        raise ReportGateError("Not a pull_request event")
    if pr.get("draft", True):
        print("Draft PR: agent report gate deferred until ready for review")
        return 0
    head = pr.get("head", {}).get("sha", "")
    targets = [int(pr["number"])]
    issue = linked_issue(pr.get("body") or "", repo)
    if issue and issue not in targets:
        targets.append(issue)
    for number in targets:
        found = find_report(repo, number, head, token, getter=getter)
        if found:
            print(f"Agent report verified: #{number} comment {found}, SHA {head}")
            return 0
    message = f"No trusted complete '{MARKER}' report for head {head} on " + ", ".join(
        f"#{n}" for n in targets
    )
    if enforce:
        raise ReportGateError(message)
    print(f"::warning::{message} (gate not enforced)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--number", type=int, help="Issue/PR to check (manual mode)")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--sha")
    args = parser.parse_args(argv)
    repo = os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO)
    token = os.environ.get("GITHUB_TOKEN", "")
    enforce = os.environ.get("AGENT_REPORT_GATE_ENFORCE", "").lower() == "true"
    try:
        if args.number:
            if not args.sha:
                raise ReportGateError("--number requires --sha")
            found = find_report(repo, args.number, args.sha, token, args.task_id)
            if not found:
                raise ReportGateError("No matching complete report")
            print(f"Agent report verified: #{args.number} comment {found}")
            return 0
        event_path = os.environ.get("GITHUB_EVENT_PATH", "")
        if not event_path:
            raise ReportGateError("GITHUB_EVENT_PATH is required")
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        return check_event(event, repo, token, enforce)
    except (ReportGateError, ValueError, OSError) as exc:
        print(f"agent-report-gate: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
