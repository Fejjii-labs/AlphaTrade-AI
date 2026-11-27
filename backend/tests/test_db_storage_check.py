"""Database storage alert at 80% and 90% of the configured disk (Sofien, 27 Nov 2026)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from app.core.config import Settings
from app.services import safety_alerts
from app.workers.db_storage_check import (
    DATABASE_STORAGE_HIGH,
    DbStorageCheck,
    storage_level,
    storage_message,
)

NOW = datetime(2026, 11, 27, 10, tzinfo=UTC)


def test_levels_and_message() -> None:
    assert storage_level(790_000_000, 1000) is None
    assert storage_level(800_000_000, 1000) == 80
    assert storage_level(899_000_000, 1000) == 80
    assert storage_level(950_000_000, 1000) == 90
    text = storage_message(920_000_000, 1000)
    assert text.splitlines()[0].endswith("Database storage at 92% (920 MB of 1,000 MB)")
    assert "trades, orders and the Journal are kept" in text


class _Session:
    def __init__(self, size: int, dialect: str = "postgresql") -> None:
        self._size, self._dialect = size, dialect

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get_bind(self) -> Any:
        return SimpleNamespace(dialect=SimpleNamespace(name=self._dialect))

    def execute(self, statement: object) -> Any:
        assert "pg_database_size" in str(statement)
        return SimpleNamespace(scalar_one=lambda: self._size)

    def rollback(self) -> None:
        return None


def _check(size: int, limit: int | None, sent: list[Any], dialect: str = "postgresql") -> Any:
    settings = Settings(_env_file=None, database_disk_limit_mb=limit)
    check = DbStorageCheck(
        settings,
        session_factory=lambda: _Session(size, dialect),  # type: ignore[arg-type,return-value]
        now=lambda: NOW,
        send=lambda settings, alert, now: sent.append(alert) or True,
    )
    return check()


def test_no_limit_means_no_check() -> None:
    sent: list[Any] = []
    assert _check(10**12, None, sent).status == "disabled" and sent == []
    assert Settings(_env_file=None).database_disk_limit_mb is None


def test_below_80_sends_nothing_and_above_sends_one_alert_per_level() -> None:
    sent: list[Any] = []
    assert _check(500_000_000, 1000, sent).status == "ok" and sent == []
    assert _check(850_000_000, 1000, sent).status == "alert_80"
    assert _check(930_000_000, 1000, sent).status == "alert_90"
    assert [a.fields["dedup_key"] for a in sent] == [
        f"{DATABASE_STORAGE_HIGH}:80",
        f"{DATABASE_STORAGE_HIGH}:90",
    ]
    assert [a.severity for a in sent] == ["warning", "critical"]
    assert all(a.telegram_verbatim for a in sent)


def test_only_postgres_is_measured() -> None:
    sent: list[Any] = []
    assert _check(10**12, 1000, sent, dialect="sqlite").status == "unsupported_database"
    assert sent == []


def test_the_alert_is_on_by_default_with_a_six_hour_cooldown() -> None:
    assert DATABASE_STORAGE_HIGH in safety_alerts.TELEGRAM_SAFETY_DEFAULT_CODES
    assert safety_alerts._cooldown(DATABASE_STORAGE_HIGH).total_seconds() == 6 * 3600
