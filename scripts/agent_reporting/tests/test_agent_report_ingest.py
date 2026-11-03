"""Ingest tests: no network; GitHub and Notion are mocked."""

from __future__ import annotations

import io
import json
from pathlib import Path

import agent_report_ingest as ingest
import agent_report_runner as runner
import pytest

RunnerError = ingest.RunnerError
REPORT = {
    "status": "complete",
    "findings": ["Found X"],
    "fixes": ["Fixed Y"],
    "solutions": ["Guard Z"],
    "blockers": [],
    "tests": ["pytest tests/test_x.py: 3 passed"],
    "next_step": "Review PR",
    "final_output": "Synthetic final findings that are long enough to satisfy the bound."
    * 2,
}
GIT = {"branch": "claude/b", "head_sha": "a" * 40, "dirty": False}


def test_extract_codex_raw() -> None:
    assert ingest.extract(json.dumps(REPORT))["status"] == "complete"


def test_extract_claude_envelope_fenced() -> None:
    env = {"type": "result", "result": f"Done.\n```json\n{json.dumps(REPORT)}\n```"}
    assert ingest.extract(json.dumps(env))["tests"] == REPORT["tests"]


def test_extract_rejects_error_and_missing() -> None:
    with pytest.raises(RunnerError):
        ingest.extract(json.dumps({"is_error": True, "result": "x"}))
    with pytest.raises(RunnerError):
        ingest.extract("no report here")


def test_missing_report_becomes_failed_report() -> None:
    out = ingest.load_output("crashed with no report")
    rep = runner.build_report(
        out, task_id="t1", agent="Claude", baseline_sha="", git_state=GIT, source="x"
    )
    assert rep["status"] == "failed" and rep["blockers"]
    body = runner.render(rep)
    assert runner.report_status(body, repo=runner.REPO, head_sha="a" * 40) == "failed"


def test_blocked_status_is_kept() -> None:
    out = ingest.load_output(
        json.dumps(dict(REPORT, status="blocked", blockers=["needs Sofien"]))
    )
    assert out["status"] == "blocked"


def test_build_report_sanitizes_and_blocks_dirty(monkeypatch) -> None:
    monkeypatch.setenv("SOME_TOKEN", "supersecretvalue")
    out = dict(
        REPORT,
        findings=[
            "leaked supersecretvalue ghp_abcdefghij postgres://u:p@h/db api_key=abc123 me@x.io",
        ],
    )
    rep = runner.build_report(
        out,
        task_id="t1",
        agent="Claude",
        baseline_sha="",
        git_state=dict(GIT, dirty=True),
        source="x",
    )
    dumped = json.dumps(rep)
    for leak in ("supersecretvalue", "ghp_", "u:p@", "abc123", "me@x.io"):
        assert leak not in dumped
    assert rep["status"] == "blocked" and rep["baseline_sha"] == "a" * 40
    assert rep["head_sha"] == "a" * 40 and rep["task_id"] == "t1"


def test_render_has_marker_and_all_sections() -> None:
    rep = runner.build_report(
        dict(REPORT),
        task_id="t1",
        agent="A",
        baseline_sha="",
        git_state=GIT,
        source="x",
    )
    body = runner.render(rep)
    assert body.startswith("<!-- alphatrade-agent-report:v1 -->")
    for h in (
        "Findings",
        "Fixes",
        "Solutions",
        "Blockers",
        "Tests",
        "Next Step",
        "Output",
    ):
        assert f"## {h}\n" in body


def test_notion_skipped_without_env(monkeypatch) -> None:
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    assert ingest.mirror_notion({}, "u").startswith("skipped")


def test_notion_mocked_post(monkeypatch) -> None:
    monkeypatch.setenv("NOTION_TOKEN", "ntn_fake_token_value")
    monkeypatch.setenv("NOTION_AGENT_REPORTS_DB", "db123")
    rep = runner.build_report(
        dict(REPORT),
        task_id="t1",
        agent="Claude",
        baseline_sha="",
        git_state=GIT,
        source="x",
    )
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        seen["body"] = json.loads(req.data)
        return Resp(b'{"url": "https://notion.so/p"}')

    assert (
        ingest.mirror_notion(rep, "https://gh/c", opener)
        == "created: https://notion.so/p"
    )
    assert seen["body"]["parent"]["database_id"] == "db123"
    assert "Task Name" in seen["body"]["properties"]
    assert len(seen["body"]["children"]) <= 100

    def boom(req, timeout):
        raise OSError("ntn_fake_token_value leaked")

    assert "ntn_fake" not in ingest.mirror_notion(rep, "u", boom)


def test_main_publishes_failed_run_via_mocked_gh(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(ingest, "snapshot", lambda cwd: GIT)
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    calls = []

    def fake_publish(directory, gh, target):
        calls.append((target, json.loads((directory / "report.json").read_text())))
        state = json.loads((directory / "state.json").read_text())
        state["comment_url"] = "https://github.com/x#c1"
        (directory / "state.json").write_text(json.dumps(state))
        return 2

    monkeypatch.setattr(ingest, "publish", fake_publish)
    src = tmp_path / "out.json"
    src.write_text(json.dumps({"result": "I gave up"}))
    code = ingest.main(
        [
            "--task-id",
            "t1",
            "--agent",
            "Claude Code",
            "--target",
            "7",
            "--input",
            str(src),
            "--report-dir",
            str(tmp_path / "r"),
        ]
    )
    assert code == 2 and calls[0][0] == 7 and calls[0][1]["status"] == "failed"
    assert "skipped" in capsys.readouterr().out


def test_publish_posts_and_reads_back(tmp_path: Path, monkeypatch) -> None:
    rep = runner.build_report(
        dict(REPORT),
        task_id="t1",
        agent="A",
        baseline_sha="",
        git_state=GIT,
        source="x",
    )
    runner.save(tmp_path / "state.json", {"status": "report_pending"})
    runner.save(tmp_path / "report.json", rep)
    store: dict = {}

    def fake_api(gh, path, payload=None):
        if payload is not None:
            store["body"] = payload["body"]
            assert path.endswith("/issues/42/comments")
            return {"id": 9}
        if "/comments?" in path:
            return []
        return {"body": store["body"], "author_association": "OWNER"}

    monkeypatch.setattr(runner, "api", fake_api)
    assert runner.publish(tmp_path, "gh", 42) == 0
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["publication"] == "verified" and state["comment_url"].endswith(
        "42#issuecomment-9"
    )
