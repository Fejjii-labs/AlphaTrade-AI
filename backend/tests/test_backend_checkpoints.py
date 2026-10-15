"""Bounded process evidence checks; no application, database or provider fixtures."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
try:
    import backend_checkpoints as checkpoints
    import run_backend_launch as launch
finally:
    sys.path.pop(0)


class BackendCheckpointTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.work = Path(work.name)
        self.root = self.work / "diagnostics"
        self.root.mkdir()

    def publisher(self, program, **kwargs):
        path = self.work / "uploader.py"
        path.write_text(program)
        return checkpoints.CheckpointPublisher(self.root, [sys.executable, str(path)], **kwargs)

    def test_snapshot_bounds_content_and_excludes_secrets_and_symlinks(self):
        (self.root / ".env").write_text("PRIVATE_SECRET=do-not-copy")
        (self.root / "active.json").symlink_to(self.root / ".env")
        (self.root / "pytest.log").write_bytes(b"x" * (checkpoints.MAX_FILE_BYTES + 7))
        target = self.work / "snapshot"
        names = checkpoints.snapshot(self.root, target, 1)
        self.assertEqual(names, ["checkpoint.json", "pytest.log.tail"])
        manifest = json.loads((target / "checkpoint.json").read_text())
        self.assertTrue(manifest["files"]["pytest.log"]["truncated"])
        self.assertEqual((target / "pytest.log.tail").stat().st_size, checkpoints.MAX_FILE_BYTES)

    def test_upload_failure_prevents_test_process_start(self):
        marker = self.work / "tests-started"
        publisher = self.publisher("raise SystemExit(1)")
        code = launch.run(
            [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
            self.root,
            3,
            "probe",
            publisher=publisher,
        )
        self.assertEqual(code, 74)
        self.assertFalse(marker.exists())
        self.assertEqual(publisher.failure, "checkpoint_upload_failed")

    def test_hung_upload_is_killed_before_spending_the_test_budget(self):
        pidfile = self.work / "uploader.pid"
        publisher = self.publisher(
            "import os,time; from pathlib import Path; "
            f"Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(60)",
            upload_seconds=0.3,
        )
        started = time.monotonic()
        code = launch.run(
            [sys.executable, "-c", "pass"], self.root, 3, "probe", publisher=publisher
        )
        self.assertEqual(code, 74)
        self.assertEqual(publisher.failure, "checkpoint_upload_timeout")
        self.assertLess(time.monotonic() - started, 2)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)

    def test_periodic_checkpoint_keeps_active_phase_before_forced_timeout(self):
        retained = self.work / "retained"
        retained.mkdir()
        publisher = self.publisher(
            "import shutil,sys,json; from pathlib import Path\n"
            f"shutil.copytree(sys.argv[1], Path({str(retained)!r})/sys.argv[3])\n"
            "Path(sys.argv[2]).write_text(json.dumps({'artifact_id':int(sys.argv[3])}))\n",
            interval=0.2,
        )
        child = (
            "import os,json,time; from pathlib import Path; "
            "root=Path(os.environ['BACKEND_RELEASE_DIAGNOSTICS']); "
            "(root/'active.json').write_text(json.dumps("
            "{'nodeid':'blocked_fixture','phase':'setup'})); "
            "time.sleep(60)"
        )
        code = launch.run(
            [sys.executable, "-c", child], self.root, 0.8, "probe", publisher=publisher
        )
        self.assertEqual(code, 124)
        self.assertGreaterEqual(len(publisher.artifacts), 2)
        evidence = json.loads((retained / "2" / "active.json").read_text())
        self.assertEqual(evidence, {"nodeid": "blocked_fixture", "phase": "setup"})
        self.assertTrue((retained / "1" / "checkpoint.json").exists())

    def test_lost_during_run_upload_stops_suite_and_preserves_failure(self):
        publisher = self.publisher(
            "import sys,json; from pathlib import Path\n"
            "if sys.argv[3] != '1': raise SystemExit(1)\n"
            "Path(sys.argv[2]).write_text(json.dumps({'artifact_id':1}))\n",
            interval=0.2,
        )
        code = launch.run(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            self.root,
            4,
            "probe",
            publisher=publisher,
        )
        self.assertEqual(code, 74)
        self.assertEqual(
            json.loads((self.root / "probe-result.json").read_text())["reason"],
            "checkpoint_upload_failed",
        )

    def test_successful_upload_never_masks_test_exit(self):
        publisher = self.publisher(
            "import sys,json; from pathlib import Path; "
            "Path(sys.argv[2]).write_text(json.dumps({'artifact_id':1}))"
        )
        code = launch.run(
            [sys.executable, "-c", "raise SystemExit(7)"],
            self.root,
            3,
            "probe",
            publisher=publisher,
        )
        self.assertEqual(code, 7)
        self.assertEqual(len(publisher.artifacts), 1)


if __name__ == "__main__":
    unittest.main()
