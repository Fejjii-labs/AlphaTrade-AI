"""BLOFIN_IMPORT_START_AT: a clean-start floor for BloFin demo imports."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.config import Settings


def test_import_start_defaults_to_none_and_parses_utc_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(_env_file=None).blofin_import_start_at is None
    monkeypatch.setenv("BLOFIN_IMPORT_START_AT", "2026-11-20T12:45:00Z")
    parsed = Settings(_env_file=None).blofin_import_start_at
    assert parsed == datetime(2026, 11, 20, 12, 45, tzinfo=UTC)


def test_import_start_without_timezone_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLOFIN_IMPORT_START_AT", "2026-11-20T12:45:00")
    with pytest.raises(ValueError, match="BLOFIN_IMPORT_START_AT"):
        Settings(_env_file=None)


def test_import_start_ms_helper() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    settings = Settings(_env_file=None, blofin_import_start_at=start)
    assert settings.blofin_import_start_ms == int(start.timestamp() * 1000)
    assert Settings(_env_file=None).blofin_import_start_ms is None
