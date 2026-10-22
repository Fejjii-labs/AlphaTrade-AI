"""Local checkpoint I/O must not occupy the release launcher's deadline loop."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import suppress
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]

# Inject a stall at the real filesystem call in the parent or worker. The first
# checkpoint succeeds; the second blocks in the selected operation. These are
# distinct from the existing network-upload and frozen-test-supervisor proofs.
FAULT = """
import os,time
from pathlib import Path
root=Path(os.environ['CHECKPOINT_IO_TEST_ROOT'])
mode=os.environ['CHECKPOINT_IO_TEST_MODE']
def block(path):
    name=Path(path).name
    match=(mode=='snapshot' and name=='provenance.json') or \\
          (mode=='receipt' and name=='checkpoint-upload-02.json') or \\
          (mode=='ledger' and name=='checkpoint-uploads.tmp')
    if not match:return
    seen=root/'first-operation'
    if mode!='receipt' and not seen.exists():seen.touch();return
    (root/'blocked.pid').write_text(str(os.getpid()))
    time.sleep(30)
original_open=os.open
os.open=lambda p,*a,**k:(block(p),original_open(p,*a,**k))[1]
original_read=Path.read_text
Path.read_text=lambda p,*a,**k:(block(p),original_read(p,*a,**k))[1]
original_write=Path.write_text
Path.write_text=lambda p,*a,**k:(block(p),original_write(p,*a,**k))[1] \\
    if p.name=='checkpoint-uploads.tmp' else original_write(p,*a,**k)
"""


class CheckpointIOTests(unittest.TestCase):
    def test_checkpoint_polling_does_not_access_diagnostic_files(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            from backend_checkpoints import CheckpointPublisher
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            uploader = work / "upload.py"
            uploader.write_text(
                "import json,sys;from pathlib import Path;"
                "Path(sys.argv[2]).write_text(json.dumps({'artifact_id':1}))"
            )
            publisher = CheckpointPublisher(work, [sys.executable, str(uploader)])
            try:
                with (
                    patch.object(Path, "resolve", side_effect=AssertionError("parent stat")),
                    patch.object(Path, "read_text", side_effect=AssertionError("parent read")),
                    patch.object(Path, "write_text", side_effect=AssertionError("parent write")),
                ):
                    publisher.tick()
                    deadline = time.monotonic() + 3
                    while publisher.proc is not None and time.monotonic() < deadline:
                        publisher.tick(start=False)
                        time.sleep(0.01)
                self.assertIsNone(publisher.proc)
                self.assertIsNone(publisher.failure)
                self.assertEqual(publisher.artifacts, [{"sequence": 1, "artifact_id": 1}])
            finally:
                publisher.close()

    def check_blocked_operation(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            diagnostics = work / "diagnostics"
            diagnostics.mkdir()
            (diagnostics / "provenance.json").write_text("{}")
            (work / "sitecustomize.py").write_text(FAULT)
            upload = work / "upload.py"
            upload.write_text(
                "import json,sys;from pathlib import Path;"
                "Path(sys.argv[2]).write_text(json.dumps({'artifact_id':int(sys.argv[3])}))"
            )
            child = (
                "import os,time;from pathlib import Path;"
                f"Path({str(work / 'test.pid')!r}).write_text(str(os.getpid()));time.sleep(30)"
            )
            program = f"""
import sys
from pathlib import Path
from backend_checkpoints import CheckpointPublisher
from run_backend_launch import run
publisher=CheckpointPublisher(Path({str(diagnostics)!r}),
    [sys.executable,{str(upload)!r}],interval=.2,upload_seconds=.4)
raise SystemExit(run([sys.executable,'-c',{child!r}],
                     Path({str(diagnostics)!r}),1.2,'release',publisher=publisher))
"""
            proc = subprocess.Popen(
                [sys.executable, "-c", program],
                env=dict(
                    os.environ,
                    PYTHONPATH=os.pathsep.join((str(work), str(ROOT / "scripts"))),
                    CHECKPOINT_IO_TEST_ROOT=str(work),
                    CHECKPOINT_IO_TEST_MODE=mode,
                ),
                start_new_session=True,
            )
            try:
                self.assertEqual(proc.wait(timeout=4), 74)
                result = json.loads((diagnostics / "release-result.json").read_text())
                self.assertEqual(result["reason"], "checkpoint_upload_timeout")
                self.assertEqual(result["checkpoint"]["phase"], mode)
                self.assertEqual(result["checkpoint"]["worker_exit_signal"], signal.SIGKILL)
                self.assertEqual(
                    result["checkpoint"]["artifacts"], [{"sequence": 1, "artifact_id": 1}]
                )
                self.assertLess(result["seconds"], 1.2)
                self.assertEqual(
                    int((work / "blocked.pid").read_text()),
                    result["checkpoint"]["worker_pid"],
                )
                with self.assertRaises(ProcessLookupError):
                    os.kill(result["checkpoint"]["worker_pid"], 0)
            finally:
                if proc.poll() is None:
                    # Inspect parent/group ownership before killing a failed harness.
                    for name in ("test.pid", "blocked.pid"):
                        path = work / name
                        if path.exists():
                            pid = int(path.read_text())
                            with suppress(ProcessLookupError, FileNotFoundError):
                                raw = Path(f"/proc/{pid}/stat").read_text()
                                fields = raw[raw.rfind(")") + 2 :].split()
                                if int(fields[1]) == proc.pid and int(fields[2]) == pid:
                                    os.killpg(pid, signal.SIGKILL)
                    proc.kill()
                proc.wait(timeout=2)

    def test_worker_signal_retains_identity_and_stops_its_uploader(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            uploader_pid = work / "uploader.pid"
            uploader = work / "upload.py"
            uploader.write_text(
                "import os,signal,time;from pathlib import Path;"
                f"Path({str(uploader_pid)!r}).write_text(str(os.getpid()));"
                "os.kill(os.getppid(),signal.SIGKILL);time.sleep(30)"
            )
            program = f"""
import sys
from pathlib import Path
from backend_checkpoints import CheckpointPublisher
from run_backend_launch import run
p=CheckpointPublisher(Path({str(work)!r}),[sys.executable,{str(uploader)!r}],
                      upload_seconds=.4)
raise SystemExit(run([sys.executable,'-c','pass'],Path({str(work)!r}),1.2,
                     'release',publisher=p))
"""
            result = subprocess.run(
                [sys.executable, "-c", program],
                env=dict(os.environ, PYTHONPATH=str(ROOT / "scripts")),
                timeout=4,
                check=False,
            )
            self.assertEqual(result.returncode, 74)
            receipt = json.loads((work / "release-result.json").read_text())
            self.assertEqual(receipt["checkpoint"]["worker_exit_signal"], signal.SIGKILL)
            self.assertEqual(receipt["checkpoint"]["phase"], "upload")
            pid = int(uploader_pid.read_text())
            # The supervisor can reap its worker; an orphan uploader may remain
            # briefly as a zombie until PID1 reaps it, but cannot keep running.
            status = Path(f"/proc/{pid}/stat")
            if status.exists():
                raw = status.read_text()
                self.assertEqual(raw[raw.rfind(")") + 2 :].split()[0], "Z")

    def test_blocked_snapshot_is_bounded(self):
        self.check_blocked_operation("snapshot")

    def test_blocked_receipt_is_bounded(self):
        self.check_blocked_operation("receipt")

    def test_blocked_ledger_is_bounded(self):
        self.check_blocked_operation("ledger")


if __name__ == "__main__":
    unittest.main()
