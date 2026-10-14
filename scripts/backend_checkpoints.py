"""Small immutable progress artifacts while the unfiltered test process is alive."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

# Never walk the workspace, collect environment variables or copy credentials.
FILES = (
    "provenance.json",
    "collection.json",
    "active.json",
    "reports.jsonl",
    "postgres.jsonl",
    "pytest.log",
    "stacks.log",
    "failures.txt",
    "before-cancellation.json",
    "result.json",
    "release-launch.json",
    "release-result.json",
    "checkpoint-uploads.json",
)
MAX_FILE_BYTES = 256 * 1024
WORKER_SCRIPT = os.path.abspath(__file__)


def snapshot(root: Path, destination: Path, sequence: int) -> list[str]:
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {}
    for name in FILES:
        source = root / name
        try:
            # O_NOFOLLOW also closes the symlink race between stat and open.
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as handle:
                import stat

                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode):
                    continue
                handle.seek(max(0, info.st_size - MAX_FILE_BYTES))
                data = handle.read(MAX_FILE_BYTES)
        except OSError:
            continue
        truncated = info.st_size > MAX_FILE_BYTES
        target = name + ".tail" if truncated else name
        (destination / target).write_bytes(data)
        manifest[name] = {"file": target, "bytes": len(data), "truncated": truncated}
    (destination / "checkpoint.json").write_text(
        json.dumps({"sequence": sequence, "captured_at": time.time(), "files": manifest}) + "\n"
    )
    return sorted(p.name for p in destination.iterdir())


def _message(value: dict) -> None:
    # Only this worker writes protocol output. SDK output never reaches this pipe.
    print(json.dumps(value), flush=True)


def _worker(value: dict) -> int:
    """All checkpoint filesystem/network work shares one killable process budget."""
    root = Path(value["root"])
    destination = Path(value["destination"])
    receipt = Path(value["receipt"])
    sequence = value["sequence"]
    artifacts = value["artifacts"]
    failure = None
    artifact_id = None
    try:
        _message({"phase": "snapshot"})
        snapshot(root, destination, sequence)
        _message({"phase": "upload"})
        code = subprocess.call(
            [*value["command"], str(destination), str(receipt), str(sequence)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if code:
            failure = "checkpoint_upload_failed"
        else:
            _message({"phase": "receipt"})
            try:
                artifact_id = json.loads(receipt.read_text())["artifact_id"]
                if type(artifact_id) is not int or artifact_id <= 0:
                    raise ValueError("missing artifact identity")
                artifacts.append({"sequence": sequence, "artifact_id": artifact_id})
            except (OSError, ValueError, KeyError, TypeError):
                failure = "checkpoint_receipt_missing"
        _message({"phase": "ledger"})
        path = root / "checkpoint-uploads.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"failure": failure, "artifacts": artifacts}) + "\n")
        temporary.replace(path)
    except OSError:
        failure = "checkpoint_launch_failed"
    _message({"failure": failure, "artifact_id": artifact_id})
    return 1 if failure else 0


class CheckpointPublisher:
    """The deadline loop polls only a process and a nonblocking protocol pipe."""

    def __init__(
        self,
        root: Path,
        command: list[str],
        *,
        interval: float = 900,
        upload_seconds: float = 30,
        maximum: int = 7,
    ) -> None:
        self.root = root.resolve()
        self.command = command
        self.interval = interval
        self.upload_seconds = upload_seconds
        self.maximum = maximum
        self.sequence = 0
        self.started = 0.0
        self.next_at = 0.0
        self.proc: subprocess.Popen | None = None
        self.failure: str | None = None
        self.artifacts: list[dict] = []
        self.phase: str | None = None
        self.worker_pid: int | None = None
        self.worker_exitcode: int | None = None
        self._buffer = b""
        self._result: dict | None = None

    def state(self) -> dict:
        return {
            "sequence": self.sequence,
            "phase": self.phase,
            "worker_pid": self.worker_pid,
            "worker_exitcode": self.worker_exitcode,
            "worker_exit_signal": (
                -self.worker_exitcode
                if self.worker_exitcode is not None and self.worker_exitcode < 0
                else None
            ),
            "failure": self.failure,
            "artifacts": self.artifacts,
        }

    def _messages(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        try:
            self._buffer += os.read(self.proc.stdout.fileno(), 4096)
        except BlockingIOError:
            return
        if len(self._buffer) > 4096:
            self.failure = "checkpoint_receipt_missing"
            return
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("invalid worker result")
                if "phase" in value:
                    self.phase = value["phase"]
                else:
                    self._result = value
            except (ValueError, TypeError):
                self.failure = "checkpoint_receipt_missing"

    def _reap(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        # A killed worker can leave its SDK subprocess alive in the owned group.
        with suppress(ProcessLookupError):
            os.killpg(self.proc.pid, signal.SIGKILL)
        self.worker_exitcode = self.proc.returncode
        self.proc.stdout.close()
        self.proc = None

    def _stop(self) -> None:
        if self.proc is not None:
            with suppress(ProcessLookupError):
                os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait(timeout=2)
            self._reap()

    def tick(self, *, start: bool = True) -> None:
        now = time.monotonic()
        if self.proc is not None:
            self._messages()
            code = self.proc.poll()
            if code is None and now - self.started < self.upload_seconds:
                return
            if code is not None:
                # Drain the final status even if exit raced the first pipe read.
                self._messages()
            if code is None:
                self.failure = "checkpoint_upload_timeout"
                self._stop()
            elif code:
                self.failure = (self._result or {}).get("failure") or "checkpoint_upload_failed"
                self._reap()
            else:
                self._reap()
                try:
                    value = self._result or {}
                    if value.get("failure"):
                        self.failure = value["failure"]
                        return
                    artifact_id = value["artifact_id"]
                    if type(artifact_id) is not int or artifact_id <= 0:
                        raise ValueError("missing artifact identity")
                    self.artifacts.append({"sequence": self.sequence, "artifact_id": artifact_id})
                except (OSError, ValueError, KeyError, TypeError):
                    self.failure = "checkpoint_receipt_missing"
        if self.failure:
            return
        if (
            not start
            or self.proc is not None
            or now < self.next_at
            or self.sequence >= self.maximum
        ):
            return
        self.sequence += 1
        destination = self.root / "checkpoints" / f"{self.sequence:02d}"
        self.receipt = self.root / f"checkpoint-upload-{self.sequence:02d}.json"
        try:
            # Start the existing bound BEFORE any snapshot/receipt/ledger I/O.
            self.started = time.monotonic()
            self.next_at = self.started + self.interval
            self.phase = "starting"
            self._buffer = b""
            self._result = None
            self.proc = subprocess.Popen(
                [
                    sys.executable,
                    WORKER_SCRIPT,
                    "--worker",
                    json.dumps(
                        {
                            "root": str(self.root),
                            "destination": str(destination),
                            "receipt": str(self.receipt),
                            "sequence": self.sequence,
                            "command": self.command,
                            "artifacts": self.artifacts,
                        }
                    ),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            assert self.proc.stdout is not None
            os.set_blocking(self.proc.stdout.fileno(), False)
            self.worker_pid = self.proc.pid
            self.worker_exitcode = None
        except OSError:
            self.failure = "checkpoint_launch_failed"

    def finish(self) -> None:
        # Reap only an already started upload. Never start a final upload here:
        # the workflow always retains the complete final diagnostic directory.
        while self.proc is not None:
            self.tick(start=False)
            if self.proc is not None:
                time.sleep(0.05)

    def close(self) -> None:
        self._stop()


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit("Expected an internal checkpoint worker request")
    raise SystemExit(_worker(json.loads(sys.argv[2])))
