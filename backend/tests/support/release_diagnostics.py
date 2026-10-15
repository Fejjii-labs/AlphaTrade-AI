"""Unfiltered release-run diagnostics, enabled only by the release supervisor."""

from __future__ import annotations

import faulthandler
import json
import os
import signal
import time
from pathlib import Path

import pytest

ROOT = Path(os.environ["BACKEND_RELEASE_DIAGNOSTICS"])
STACKS = (ROOT / "stacks.log").open("w")
faulthandler.register(signal.SIGUSR1, file=STACKS, all_threads=True)


def _write(name: str, value: object) -> None:
    temporary = ROOT / f"{name}.tmp"
    temporary.write_text(json.dumps(value, default=str) + "\n")
    temporary.replace(ROOT / name)


def pytest_collection_finish(session) -> None:
    (ROOT / "ordered-nodeids.txt").write_text(
        "\n".join(item.nodeid for item in session.items) + "\n"
    )
    _write("collection.json", {"count": len(session.items)})


def pytest_runtest_logstart(nodeid, location) -> None:
    _write("active.json", {"nodeid": nodeid, "started": time.time(), "phase": "setup"})


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item) -> None:
    _phase(item.nodeid, "setup")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item) -> None:
    _phase(item.nodeid, "call")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item) -> None:
    _phase(item.nodeid, "teardown")


def _phase(nodeid: str, phase: str) -> None:
    _write("active.json", {"nodeid": nodeid, "started": time.time(), "phase": phase})


def pytest_runtest_logreport(report) -> None:
    with (ROOT / "reports.jsonl").open("a") as handle:
        handle.write(
            json.dumps(
                {
                    "nodeid": report.nodeid,
                    "phase": report.when,
                    "outcome": report.outcome,
                    "duration": report.duration,
                }
            )
            + "\n"
        )
    if report.failed:
        with (ROOT / "failures.txt").open("a") as handle:
            handle.write(str(report.longrepr) + "\n")


def pytest_sessionfinish(session, exitstatus) -> None:
    _write("active.json", {"nodeid": None, "started": time.time(), "phase": "shutdown"})
    _write("pytest-result.json", {"exitcode": int(exitstatus)})
