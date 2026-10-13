"""Small immutable progress artifacts while the unfiltered test process is alive."""

from __future__ import annotations

import json
import os
import signal
import subprocess
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
)
MAX_FILE_BYTES = 256 * 1024


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


class CheckpointPublisher:
    """Upload in a killable child; network failure never stalls the test watchdog."""

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

    def _record(self) -> None:
        path = self.root / "checkpoint-uploads.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"failure": self.failure, "artifacts": self.artifacts}) + "\n"
        )
        temporary.replace(path)

    def _stop(self) -> None:
        if self.proc is not None:
            with suppress(ProcessLookupError):
                os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait(timeout=2)
            self.proc = None

    def tick(self, *, start: bool = True) -> None:
        now = time.monotonic()
        if self.proc is not None:
            code = self.proc.poll()
            if code is None and now - self.started < self.upload_seconds:
                return
            if code is None:
                self.failure = "checkpoint_upload_timeout"
                self._stop()
            elif code:
                self.failure = "checkpoint_upload_failed"
                self.proc = None
            else:
                self.proc = None
                try:
                    value = json.loads(self.receipt.read_text())
                    artifact_id = value["artifact_id"]
                    if type(artifact_id) is not int or artifact_id <= 0:
                        raise ValueError("missing artifact identity")
                    self.artifacts.append({"sequence": self.sequence, "artifact_id": artifact_id})
                except (OSError, ValueError, KeyError, TypeError):
                    self.failure = "checkpoint_receipt_missing"
                self._record()
        if self.failure:
            self._record()
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
            snapshot(self.root, destination, self.sequence)
            self.started = time.monotonic()
            self.next_at = self.started + self.interval
            self.proc = subprocess.Popen(
                [*self.command, str(destination), str(self.receipt), str(self.sequence)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            self.failure = "checkpoint_launch_failed"
            self._record()

    def finish(self) -> None:
        # Reap only an already started upload. Never start a final upload here:
        # the workflow always retains the complete final diagnostic directory.
        while self.proc is not None:
            self.tick(start=False)
            if self.proc is not None:
                time.sleep(0.05)
        self._record()

    def close(self) -> None:
        self._stop()
        self._record()
