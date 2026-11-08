"""L6: an ordinary short cycle writes the paper-worker heartbeat once, not twice."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import app.services.worker_heartbeat as heartbeat
from app.core.config import Settings


def test_short_cycles_write_one_beat_and_long_gaps_write_the_start_beat(monkeypatch) -> None:
    writes: list[str] = []
    monkeypatch.setattr(
        heartbeat,
        "publish_paper_worker_heartbeat",
        lambda _settings, *, now, status, error: writes.append(status),
    )
    moments = iter(
        [
            datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC),  # cycle 1 start (first: beat)
            datetime(2026, 11, 7, 12, 0, 1, tzinfo=UTC),  # cycle 1 end
            datetime(2026, 11, 7, 12, 0, 16, tzinfo=UTC),  # cycle 2 start (15 s later: skip)
            datetime(2026, 11, 7, 12, 0, 17, tzinfo=UTC),  # cycle 2 end
            datetime(2026, 11, 7, 12, 0, 17, tzinfo=UTC) + timedelta(seconds=45),  # long gap
            datetime(2026, 11, 7, 12, 1, 3, tzinfo=UTC),
        ]
    )
    wrapped = heartbeat.watch_cycle_with_heartbeat(
        Settings(_env_file=None),
        lambda: SimpleNamespace(status="running", error=""),
        now=lambda: next(moments),
    )
    wrapped()
    wrapped()
    wrapped()
    assert writes == ["running", "running", "running", "running", "running"]
    assert len(writes) == 5  # 2 + 1 + 2, instead of 6
