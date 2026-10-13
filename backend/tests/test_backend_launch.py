"""Real process checks for the release launcher; no app, database or provider needed."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import suppress
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts/run_backend_launch.py"


class BackendLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        evidence = os.environ.get("BACKEND_LAUNCH_PROOF_DIR")
        self.output = (
            (Path(evidence) / self._testMethodName) if evidence else self.work / "evidence"
        )

    def command(self, *args, seconds=3):
        return [
            sys.executable,
            str(LAUNCHER),
            "--output",
            str(self.output),
            "--stage",
            "probe",
            "--seconds",
            str(seconds),
            "--",
            *args,
        ]

    def check_run(self, args, expected, **kwargs):
        result = subprocess.run(args, capture_output=True, text=True, timeout=9, **kwargs)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        receipt = json.loads((self.output / "probe-result.json").read_text())
        self.assertEqual(receipt["exitcode"], expected)
        return receipt

    def assert_stopped(self, pid):
        stat = Path(f"/proc/{pid}/stat")
        # A reparented zombie has stopped executing; the host reaps it separately.
        self.assertTrue(not stat.exists() or stat.read_text().split(") ", 1)[1][0] == "Z")

    def test_prepare_retains_source_before_command_start(self):
        result = subprocess.run(
            [sys.executable, str(LAUNCHER), "--output", str(self.output), "--prepare-only"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads((self.output / "provenance.json").read_text())
        self.assertEqual(len(data["checkout_sha"]), 40)
        self.assertIn("scripts/run_backend_release.py", data["sha256"])
        self.assertFalse((self.output / "probe-launch.json").exists())

    def test_workflow_collection_imports_diagnostics_before_conftest(self):
        workflow = yaml.load(
            (ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
        )
        step = next(
            step
            for step in workflow["jobs"]["backend"]["steps"]
            if step.get("name") == "Collect backend release nodes within a startup budget"
        )
        command = shlex.split(step["run"])
        command[command.index("--output") + 1] = str(self.output)
        marker = self.work / "executed"
        source = self.work / "test_collection.py"
        source.write_text(
            "from pathlib import Path\n"
            f"def test_first(): Path({str(marker)!r}).touch()\n"
            "def test_second(): pass\n"
        )
        # Exercise the workflow entry point, without application fixtures or a
        # inherited PYTHONPATH masking its early plugin-import behavior.
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [*command, "--noconftest", str(source)],
            cwd=ROOT / "backend",
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads((self.output / "collection.json").read_text())["count"], 2)
        nodes = (self.output / "ordered-nodeids.txt").read_text().splitlines()
        self.assertEqual([node.rsplit("::", 1)[1] for node in nodes], ["test_first", "test_second"])
        self.assertFalse(marker.exists(), "collection must not execute test bodies")

    def test_failure_preserves_exact_exit(self):
        receipt = self.check_run(self.command(sys.executable, "-c", "raise SystemExit(7)"), 7)
        self.assertEqual(receipt["reason"], "command_exit")

    def test_stall_before_pytest_is_bounded(self):
        receipt = self.check_run(
            self.command(sys.executable, "-c", "import time; time.sleep(60)", seconds=0.3), 124
        )
        self.assertEqual(receipt["reason"], "launch_deadline")
        self.assertLess(receipt["seconds"], 5)

    def test_external_cancellation_retains_status_and_kills_descendants(self):
        child_pid = self.work / "grandchild.pid"
        child = (
            "import os,signal,time; from pathlib import Path; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"Path({str(child_pid)!r}).write_text(str(os.getpid())); time.sleep(60)"
        )
        program = (
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)"
        )
        proc = subprocess.Popen(
            self.command(sys.executable, "-c", program, seconds=30),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 4
            while not child_pid.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(child_pid.exists(), "grandchild failed to start")
            proc.send_signal(signal.SIGTERM)
            self.assertEqual(proc.wait(timeout=6), 143)
            receipt = json.loads((self.output / "probe-result.json").read_text())
            self.assertEqual(receipt["reason"], "SIGTERM")
            self.assert_stopped(int(child_pid.read_text()))
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=2)
            if child_pid.exists():
                with suppress(ProcessLookupError):
                    os.kill(int(child_pid.read_text()), signal.SIGKILL)

    def test_real_supervisor_keeps_pytest_in_owned_group(self):
        child_pid = self.work / "pytest.pid"
        (self.work / "pytest.py").write_text(
            "import json,os,signal,time\nfrom pathlib import Path\n"
            "signal.signal(signal.SIGUSR1, signal.SIG_IGN)\n"
            f"Path({str(child_pid)!r}).write_text(json.dumps("
            "dict(pid=os.getpid(),supervisor=os.getppid())))\n"
            # Prove the outer launcher can clean up even when the watchdog freezes.
            "os.kill(os.getppid(),signal.SIGSTOP)\n"
            "time.sleep(60)\n"
        )
        receipt = self.check_run(
            self.command(
                sys.executable,
                str(ROOT / "scripts/run_backend_release.py"),
                "--output",
                str(self.output),
                "--run-seconds",
                "30",
                seconds=0.8,
            ),
            124,
            env=dict(os.environ, PYTHONPATH=str(self.work)),
            cwd=self.work,
        )
        self.assertEqual(receipt["reason"], "launch_deadline")
        self.assertTrue(child_pid.exists())
        participants = json.loads(child_pid.read_text())
        self.assert_stopped(participants["pid"])
        self.assert_stopped(participants["supervisor"])

    def test_actual_uv_launch_completes_without_project_or_network(self):
        uv = shutil.which("uv")
        self.assertIsNotNone(uv, "the proof requires the same uv launcher installed by CI")
        self.check_run(
            self.command(
                uv,
                "run",
                "--offline",
                "--no-project",
                "--python",
                sys.executable,
                "python",
                "-c",
                "print('uv launch verified')",
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
