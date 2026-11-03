"""Network-free tests for the AlphaTrade agent report gate and workflow."""

from __future__ import annotations

from pathlib import Path

import agent_report_gate as gate
import agent_report_runner as runner
import pytest
import yaml

REPO = "Fejjii-labs/AlphaTrade-AI"
SHA = "b" * 40
REPORT = {
    "status": "complete",
    "findings": ["f"],
    "fixes": ["x"],
    "solutions": ["s"],
    "blockers": [],
    "tests": ["t: passed"],
    "next_step": "merge after review",
    "final_output": "A sufficiently long synthetic final output for the gate check."
    * 2,
}


def body(status: str = "complete", sha: str = SHA) -> str:
    rep = runner.build_report(
        dict(REPORT, status=status),
        task_id="t1",
        agent="A",
        baseline_sha="",
        git_state={"branch": "claude/x", "head_sha": sha, "dirty": False},
        source="x",
    )
    return runner.render(rep)


def getter_for(comments):
    def getter(path, token):
        return comments if path.endswith("page=1") else []

    return getter


def event(draft=False, pr_body=""):
    return {
        "pull_request": {
            "number": 5,
            "draft": draft,
            "body": pr_body,
            "head": {"sha": SHA},
        }
    }


def test_workflow_is_separate_cheap_and_read_only() -> None:
    root = Path(__file__).resolve().parents[3]
    doc = yaml.safe_load((root / ".github/workflows/agent-reports.yml").read_text())
    events = doc.get("on", doc.get(True))
    assert set(events) == {"pull_request"}  # no workflow_dispatch / pull_request_target
    assert doc["permissions"] == {"contents": "read"}
    job = doc["jobs"]["agent-report"]
    assert set(job["permissions"].values()) == {"read"}
    assert job["timeout-minutes"] <= 5
    assert job["steps"][0]["with"]["persist-credentials"] is False
    assert job["steps"][0]["with"]["sparse-checkout"] == "scripts/agent_reporting"
    text = (root / ".github/workflows/agent-reports.yml").read_text()
    for expensive in ("pytest", "npm", "uv ", "pip install", "secrets.", "docker"):
        assert expensive not in text


def test_valid_complete_report_passes() -> None:
    assert gate.valid_report(body(), repo=REPO, head_sha=SHA, task_id="t1")


@pytest.mark.parametrize("status", ["blocked", "failed"])
def test_non_complete_does_not_unblock(status) -> None:
    assert gate.report_status(body(status), repo=REPO, head_sha=SHA) == status
    assert not gate.valid_report(body(status), repo=REPO, head_sha=SHA)


def test_wrong_sha_marker_or_section_rejected() -> None:
    assert not gate.valid_report(body(sha="c" * 40), repo=REPO, head_sha=SHA)
    assert not gate.valid_report(
        body().replace(gate.MARKER, ""), repo=REPO, head_sha=SHA
    )
    assert not gate.valid_report(
        body().replace("## Solutions", "## X"), repo=REPO, head_sha=SHA
    )
    assert not gate.valid_report(
        body().replace("sof", ""), repo="Fejjii-labs/SOF", head_sha=SHA
    )


def test_untrusted_comment_ignored() -> None:
    comments = [{"id": 1, "author_association": "NONE", "body": body()}]
    assert gate.find_report(REPO, 5, SHA, "t", getter=getter_for(comments)) is None
    comments[0]["author_association"] = "MEMBER"
    assert gate.find_report(REPO, 5, SHA, "t", getter=getter_for(comments)) == 1


def test_draft_deferred_and_warn_mode(capsys) -> None:
    assert gate.check_event(event(draft=True), REPO, "", True) == 0
    assert gate.check_event(event(), REPO, "t", False, getter=getter_for([])) == 0
    assert "::warning::" in capsys.readouterr().out


def test_enforced_missing_report_fails_and_present_passes() -> None:
    with pytest.raises(gate.ReportGateError):
        gate.check_event(event(), REPO, "t", True, getter=getter_for([]))
    ok = [{"id": 3, "author_association": "OWNER", "body": body()}]
    assert gate.check_event(event(), REPO, "t", True, getter=getter_for(ok)) == 0


def test_linked_issue_checked_and_foreign_repo_rejected() -> None:
    line = f"- Agent report issue: https://github.com/{REPO}/issues/9"
    seen = []

    def getter(path, token):
        seen.append(path)
        return (
            [{"id": 4, "author_association": "OWNER", "body": body()}]
            if "/9/" in path
            else []
        )

    assert gate.check_event(event(pr_body=line), REPO, "t", True, getter=getter) == 0
    assert any("/issues/9/" in p for p in seen)
    with pytest.raises(gate.ReportGateError):
        gate.linked_issue("- Agent report issue: https://github.com/x/y/issues/1", REPO)
