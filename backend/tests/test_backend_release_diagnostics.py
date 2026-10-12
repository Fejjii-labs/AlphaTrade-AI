"""Failures and stuck fixtures must produce evidence before release cancellation."""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "backend_release", ROOT / "scripts/run_backend_release.py"
)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def test_failed_process_retains_output_and_exact_nonzero_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "postgres_snapshot", lambda: {"available": False})
    code = release.run(
        [sys.executable, "-c", "print('specific assertion failure'); raise SystemExit(3)"],
        tmp_path,
        phase_seconds=5,
        run_seconds=5,
    )
    assert code == 3
    assert "specific assertion failure" in (tmp_path / "pytest.log").read_text()
    assert json.loads((tmp_path / "result.json").read_text())["reason"] is None


def test_blocked_phase_retains_active_test_and_postgres_blockers_before_stop(tmp_path, monkeypatch):
    snapshot = {"available": True, "rows": [[2, "active", "Lock", "relation", [1]]]}
    monkeypatch.setattr(release, "postgres_snapshot", lambda: snapshot)
    child = """
import json,os,signal,time
from pathlib import Path
signal.signal(signal.SIGUSR1, lambda *_: None)
p=Path(os.environ['BACKEND_RELEASE_DIAGNOSTICS'])
(p/'active.json').write_text(json.dumps(dict(nodeid='blocked-fixture',phase='setup',started=time.time())))
while True: time.sleep(.1)
"""
    code = release.run([sys.executable, "-c", child], tmp_path, phase_seconds=0.3, run_seconds=5)
    assert code == 124
    before = json.loads((tmp_path / "before-cancellation.json").read_text())
    assert before["reason"] == "phase_deadline"
    assert before["active"]["nodeid"] == "blocked-fixture"
    assert before["postgres"] == snapshot
    assert json.loads((tmp_path / "result.json").read_text())[
        "diagnostics_saved_before_cancellation"
    ]


def test_postgres_diagnostic_does_not_expose_connection_or_sql_literals(monkeypatch):
    import psycopg

    monkeypatch.setenv("PHASE1_POSTGRES_URL", "postgresql://user:private-password@database/test")

    def rejected_connection(*args, **kwargs):
        raise psycopg.OperationalError("connection for private-password failed")

    monkeypatch.setattr(psycopg, "connect", rejected_connection)
    assert release.postgres_snapshot() == {
        "available": False,
        "error_type": "OperationalError",
    }


def test_real_pytest_keeps_collection_and_first_failure_without_running_later_case(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(release, "postgres_snapshot", lambda: {"available": False})
    marker = tmp_path / "later-case-ran"
    source = tmp_path / "test_release_child.py"
    source.write_text(
        "from pathlib import Path\ndef test_first(): assert False, 'specific failure'\n"
        f"def test_later(): Path({str(marker)!r}).write_text('unsafe')\n"
    )
    diagnostics = tmp_path / "diagnostics"
    code = release.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests.support.release_diagnostics",
            "-x",
            str(source),
        ],
        diagnostics,
        phase_seconds=10,
        run_seconds=30,
    )
    assert code == 1
    assert not marker.exists()
    assert json.loads((diagnostics / "collection.json").read_text())["count"] == 2
    assert len((diagnostics / "ordered-nodeids.txt").read_text().splitlines()) == 2
    assert "specific failure" in (diagnostics / "failures.txt").read_text()
