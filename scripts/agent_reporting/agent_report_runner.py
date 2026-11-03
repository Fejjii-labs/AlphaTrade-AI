"""Supervise a local `codex exec` run and publish a verified AlphaTrade report.

A report is published on EVERY run outcome: complete, blocked (dirty tree,
agent-declared blocker) and failed (non-zero exit, timeout, invalid output).
Only the supported --output-last-message file is read; no transcripts, cloud
sessions or raw logs. Everything posted is sanitized. Requires `codex` and `gh`.

Target: the PR or issue number given with --target (GitHub is canonical).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_report_gate import MARKER, report_status

REPO = "Fejjii-labs/AlphaTrade-AI"
SCHEMA = Path(__file__).resolve().parent / "agent_completion.schema.json"
TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}
LIST_FIELDS = ("findings", "fixes", "solutions", "blockers", "tests")
STR_FIELDS = ("next_step", "final_output")
MAX_BODY = 60000


class RunnerError(ValueError):
    """Completion remains pending until the publisher can prove delivery."""


def sanitize(text: str) -> str:
    # Known credential values are removed without recording or printing them.
    for name, value in os.environ.items():
        if len(value) >= 4 and re.search(
            r"TOKEN|SECRET|PASSWORD|PASSPHRASE|API_KEY|PRIVATE_KEY|DSN|CREDENTIAL|DATABASE_URL",
            name,
            re.IGNORECASE,
        ):
            text = text.replace(value, "[REDACTED]")
    patterns = (
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        (
            r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{16,}"
            r"|ntn_[A-Za-z0-9]+|secret_[A-Za-z0-9]{20,}|rnd_[A-Za-z0-9]{16,}"
            r"|xox[abprs]-[A-Za-z0-9-]+|\d{8,10}:[A-Za-z0-9_-]{30,})\b"
        ),
        r"(?i)\bBearer\s+[^\s\"'<>]+",
        r"(?i)\b(?:https?|postgres(?:ql)?|redis|rediss|amqp)://[^\s/@]+:[^\s/@]+@",
        r"(?i)\b(?:api[_-]?key|api[_-]?secret|passphrase|password)\s*[=:]\s*[^\s\"']+",
        r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        r"(?<!\w)\+\d[\d ().-]{8,}\d",
        r"(?i)(?:/Users/|/home/)[^/\s]+",
    )
    for pattern in patterns:
        text = re.sub(pattern, "[REDACTED]", text, flags=re.DOTALL)
    return text


def save(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL
    ).strip()


def snapshot(cwd: Path) -> dict[str, Any]:
    origin = git(cwd, "remote", "get-url", "origin").removesuffix(".git").lower()
    if not origin.endswith(("/alphatrade-ai", ":fejjii-labs/alphatrade-ai")):
        raise RunnerError("Checkout origin must be the AlphaTrade-AI repository")
    branch = git(cwd, "branch", "--show-current")
    if not branch or not re.fullmatch(r"[A-Za-z0-9_./-]+", branch):
        raise RunnerError("A named engineering branch is required")
    return {
        "branch": branch,
        "head_sha": git(cwd, "rev-parse", "HEAD"),
        "dirty": bool(git(cwd, "status", "--porcelain")),
    }


def validate_output(output: Any) -> None:
    schema = json.loads(SCHEMA.read_text())
    if not isinstance(output, dict) or set(output) != set(schema["required"]):
        raise RunnerError("Missing or unexpected final-report fields")
    if output["status"] not in {"complete", "blocked", "failed"}:
        raise RunnerError("Invalid agent execution status")
    for field in LIST_FIELDS:
        if not isinstance(output[field], list) or not all(
            isinstance(item, str) and item.strip() for item in output[field]
        ):
            raise RunnerError("Invalid structured report list")
    if not output["findings"] or not output["tests"]:
        raise RunnerError("Findings and tests (or not-run reasons) are required")
    if not isinstance(output["next_step"], str) or not output["next_step"].strip():
        raise RunnerError("A next step is required")
    if (
        not isinstance(output["final_output"], str)
        or len(output["final_output"].strip()) < 80
    ):
        raise RunnerError("Final output must contain at least 80 characters")


def sanitize_output(output: dict[str, Any]) -> dict[str, Any]:
    for field in LIST_FIELDS:
        output[field] = [sanitize(item) for item in output[field]]
    for field in STR_FIELDS:
        output[field] = sanitize(output[field])
    return output


def failure_output(reason: str) -> dict[str, Any]:
    """Synthesize a report for a run that produced no valid structured output."""
    return {
        "status": "failed",
        "findings": [f"Run did not produce a valid structured report: {reason}"],
        "fixes": [],
        "solutions": [],
        "blockers": [reason],
        "tests": ["Not run or unknown: agent run failed before reporting"],
        "next_step": "Inspect the run locally and re-run the task.",
        "final_output": (
            "Supervisor-generated failure report. The agent run ended without a "
            f"valid final report ({reason}). No completion is claimed."
        ),
    }


def build_report(
    output: dict[str, Any],
    *,
    task_id: str,
    agent: str,
    git_state: dict[str, Any],
    baseline_sha: str,
    source: str,
) -> dict[str, Any]:
    validate_output(output)
    output = sanitize_output(output)
    if git_state["dirty"] and output["status"] == "complete":
        output["status"] = "blocked"
    if git_state["dirty"]:
        output["blockers"].append(
            "Uncommitted changes present; the head SHA describes only the committed tree."
        )
    return {
        "task_id": task_id,
        "run_id": uuid.uuid4().hex,
        "agent": sanitize(agent),
        "repo": REPO,
        "baseline_sha": baseline_sha or git_state["head_sha"],
        **git_state,
        **output,
        "transcript_availability": {
            "final_output": f"{source}; sanitized",
            "full_transcript": "not read",
            "cloud_sessions": "not covered",
        },
    }


def render(report: dict[str, Any]) -> str:
    def lines(field: str) -> str:
        return "\n".join(f"- {item}" for item in report[field]) or "None reported."

    structured = json.dumps(report, indent=2).replace("`", "\\u0060")
    return (
        f"{MARKER}\n"
        f"<!-- alphatrade-run:{report['run_id']} -->\n"
        f"Task ID: {report['task_id']}\nAgent: {report['agent']}\nRepo: {REPO}\n"
        f"Branch: {report['branch']}\nHead SHA: {report['head_sha']}\n"
        f"Status: {report['status']}\n\n"
        f"## Findings\n{lines('findings')}\n\n"
        f"## Fixes\n{lines('fixes')}\n\n"
        f"## Solutions\n{lines('solutions')}\n\n"
        f"## Blockers\n{lines('blockers')}\n\n"
        "## Tests\nAgent-reported results (not independently attested):\n"
        f"{lines('tests')}\n\n"
        f"## Next Step\n{report['next_step']}\n\n"
        f"## Output\nSanitized structured report:\n```json\n{structured}\n```\n"
    )


def api(gh: str, path: str, payload: Any = None) -> Any:
    command = [gh, "api", "--hostname", "github.com", path]
    data = None
    if payload is not None:
        command += ["--method", "POST", "--input", "-"]
        data = json.dumps(payload)
    result = subprocess.run(
        command, input=data, capture_output=True, text=True, timeout=60, check=False
    )
    if result.returncode:
        raise RunnerError("GitHub request failed; publication remains report_pending")
    return json.loads(result.stdout)


def publish(directory: Path, gh: str, target: int) -> int:
    """Post (idempotently) and read back the report on issue/PR ``target``."""
    lock = directory / "publication.lock"
    with lock.open("x"):
        pass
    try:
        state = json.loads((directory / "state.json").read_text())
        report = json.loads((directory / "report.json").read_text())
        body = render(report)
        if len(body.encode("utf-8")) > MAX_BODY:
            raise RunnerError("Report exceeds the bounded comment size; not posted")
        base = f"/repos/{REPO}/issues"
        comment_id = state.get("comment_id")
        if not comment_id:
            # Reconcile ambiguous earlier POSTs before retrying.
            marker = f"<!-- alphatrade-run:{report['run_id']} -->"
            for page in range(1, 101):
                comments = api(gh, f"{base}/{target}/comments?per_page=100&page={page}")
                matches = [c for c in comments if marker in (c.get("body") or "")]
                if matches:
                    if len(matches) != 1 or matches[0].get("body") != body:
                        raise RunnerError(
                            "Conflicting publication found; inspect first"
                        )
                    comment_id = matches[0]["id"]
                    break
                if len(comments) < 100:
                    break
            else:
                raise RunnerError(
                    "Comment search limit reached; refusing duplicate POST"
                )
        if not comment_id:
            comment_id = api(gh, f"{base}/{target}/comments", {"body": body})["id"]
        state.update(status="report_pending", comment_id=comment_id, target=target)
        save(directory / "state.json", state)
        fetched = api(gh, f"{base}/comments/{int(comment_id)}")
        if (
            fetched.get("body") != body
            or fetched.get("author_association") not in TRUSTED
        ):
            raise RunnerError(
                "Published report readback mismatch or untrusted publisher"
            )
        if (
            report_status(body, repo=REPO, head_sha=report["head_sha"])
            != report["status"]
        ):
            raise RunnerError(
                "Published report does not satisfy the report gate format"
            )
        state.update(
            status=report["status"],
            publication="verified",
            verified_at=datetime.now(UTC).isoformat(),
            comment_url=(
                f"https://github.com/{REPO}/issues/{target}#issuecomment-{int(comment_id)}"
            ),
        )
        save(directory / "state.json", state)
        print(f"Publication verified: {state['comment_url']} ({state['status']})")
        return 0 if state["status"] == "complete" else 2
    finally:
        lock.unlink()


def run(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve()
    before = snapshot(cwd)
    directory = Path(args.report_dir).resolve()
    root = Path(git(cwd, "rev-parse", "--show-toplevel"))
    if (
        directory.is_relative_to(root)
        and subprocess.run(
            ["git", "check-ignore", "--quiet", str(directory)], cwd=cwd, check=False
        ).returncode
    ):
        raise RunnerError("Receipt directory inside the checkout must be gitignored")
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    save(
        directory / "state.json", {"task_id": args.task_id, "status": "report_pending"}
    )
    prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    prompt += (
        Path(__file__).resolve().parents[2]
        / "docs/infrastructure/agent-reporting/PROMPT_FOOTER.md"
    ).read_text(encoding="utf-8")
    output: dict[str, Any]
    with tempfile.TemporaryDirectory(prefix="alphatrade-agent-final-") as temporary:
        final = Path(temporary) / "final.json"
        try:
            with subprocess.Popen(
                [
                    args.codex,
                    "exec",
                    "--ephemeral",
                    "--sandbox",
                    args.sandbox,
                    "--output-schema",
                    str(SCHEMA),
                    "--output-last-message",
                    str(final),
                    "-",
                ],
                cwd=cwd,
                stdin=subprocess.PIPE,
                text=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=os.name == "posix",
            ) as process:
                try:
                    process.communicate(input=prompt, timeout=args.timeout)
                except (subprocess.TimeoutExpired, KeyboardInterrupt):
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait()
                    raise RunnerError("Codex interrupted or timed out") from None
            if process.returncode:
                raise RunnerError("Codex exited unsuccessfully")
            if final.stat().st_size > MAX_BODY:
                raise RunnerError("Final output exceeds report size bound")
            output = json.loads(final.read_text(encoding="utf-8"))
            validate_output(output)
        except (RunnerError, OSError, ValueError) as exc:
            # Failed runs are reported too; never claim completion.
            reason = (
                str(exc) if isinstance(exc, RunnerError) else "no valid final output"
            )
            output = failure_output(reason)
    after = snapshot(cwd)
    if after["branch"] != before["branch"]:
        output = failure_output("Agent changed branches; inspect before trusting")
    report = build_report(
        output,
        task_id=args.task_id,
        agent=args.agent,
        git_state=after,
        baseline_sha=before["head_sha"],
        source="codex --output-last-message",
    )
    save(directory / "report.json", report)
    return publish(directory, args.gh, args.target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    launch = sub.add_parser("run")
    launch.add_argument("--task-id", required=True)
    launch.add_argument("--cwd", default=".")
    launch.add_argument("--prompt-file", required=True)
    launch.add_argument("--codex", default="codex")
    launch.add_argument("--agent", default="Codex exec (local supervised)")
    launch.add_argument(
        "--sandbox", choices=["read-only", "workspace-write"], default="read-only"
    )
    launch.add_argument("--timeout", type=int, default=1800)
    retry = sub.add_parser("publish", help="Retry saved publication without rerunning")
    for command in (launch, retry):
        command.add_argument("--report-dir", required=True)
        command.add_argument(
            "--target", type=int, required=True, help="PR or issue number"
        )
        command.add_argument("--gh", default="gh")
    args = parser.parse_args(argv)
    if args.command == "run" and not re.fullmatch(
        r"[A-Za-z0-9_.-]{1,100}", args.task_id
    ):
        parser.error("task ID must be a short engineering identifier")
    try:
        if args.command == "run":
            return run(args)
        return publish(Path(args.report_dir), args.gh, args.target)
    except RunnerError as exc:
        print(f"agent-report-runner: report_pending; {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print(
            "agent-report-runner: report_pending; inspect private receipt",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
