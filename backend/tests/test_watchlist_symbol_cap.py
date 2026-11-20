"""WATCHER_WATCHLIST_MAX_SYMBOLS caps the enabled watchlist at read time (API and worker)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.base import Base
from app.db.models import Organization
from app.repositories.watcher_watchlist import WatcherWatchlistRepository
from app.workers.watcher_watchlist import (
    DEFAULT_WATCHLIST_SYMBOLS,
    WatchlistConfiguration,
    WatchlistSlot,
    cap_enabled_slots,
    default_watchlist,
)

ORG = UUID("00000000-0000-0000-0000-00000000c0de")


def _config(*enabled: bool) -> WatchlistConfiguration:
    slots = tuple(
        WatchlistSlot(position=i + 1, symbol=symbol, enabled=flag)
        for i, (symbol, flag) in enumerate(zip(DEFAULT_WATCHLIST_SYMBOLS, enabled, strict=True))
    )
    return WatchlistConfiguration(
        slots=slots, revision=7, updated_at=datetime(2026, 1, 1, tzinfo=UTC)
    )


def test_cap_keeps_first_enabled_slots_in_order_and_disables_the_rest() -> None:
    capped = cap_enabled_slots(default_watchlist(), 2)

    assert capped.enabled_symbols() == ("BTCUSDT", "ZECUSDT")
    assert [slot.enabled for slot in capped.slots] == [True, True, False, False, False]
    # Slot identity (position, symbol) is unchanged so the UI layout is stable.
    assert [slot.symbol for slot in capped.slots] == list(DEFAULT_WATCHLIST_SYMBOLS)


def test_cap_skips_already_disabled_slots() -> None:
    capped = cap_enabled_slots(_config(False, True, True, False, True), 2)

    assert capped.enabled_symbols() == ("ZECUSDT", "ETHUSDT")


def test_cap_preserves_revision_and_timestamp() -> None:
    config = _config(True, True, True, True, True)
    capped = cap_enabled_slots(config, 1)

    assert capped.revision == config.revision
    assert capped.updated_at == config.updated_at
    assert capped.enabled_symbols() == ("BTCUSDT",)


def test_cap_at_or_above_slot_count_is_identity() -> None:
    config = _config(True, False, True, True, True)

    assert cap_enabled_slots(config, 5) is config
    assert cap_enabled_slots(config, 9) is config


@pytest.fixture
def session(tmp_path):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'cap.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(Organization(id=ORG, name="Cap"))
        db.commit()
        yield db
    engine.dispose()


def test_repository_default_is_uncapped_five_symbols(session) -> None:
    get_settings.cache_clear()
    assert get_settings().watcher_watchlist_max_symbols == 5

    config = WatcherWatchlistRepository(session).load(ORG)

    assert config.enabled_symbols() == DEFAULT_WATCHLIST_SYMBOLS


def test_repository_load_applies_the_configured_cap(session, monkeypatch) -> None:
    monkeypatch.setenv("WATCHER_WATCHLIST_MAX_SYMBOLS", "1")
    get_settings.cache_clear()
    try:
        repository = WatcherWatchlistRepository(session)

        # Default (no row) and stored rows are both capped on read.
        assert repository.load(ORG).enabled_symbols() == ("BTCUSDT",)
        stored = repository.replace(
            ORG, [(symbol, True) for symbol in DEFAULT_WATCHLIST_SYMBOLS], expected_revision=0
        )
        session.commit()
        assert stored.revision == 1
        loaded = repository.load(ORG)
        assert loaded.revision == 1
        assert loaded.enabled_symbols() == ("BTCUSDT",)
        # The stored rows keep what the owner saved; only the read view is capped.
        assert repository._load_stored(ORG).enabled_symbols() == DEFAULT_WATCHLIST_SYMBOLS
    finally:
        get_settings.cache_clear()


def test_cap_setting_bounds() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(watcher_watchlist_max_symbols=0)
    with pytest.raises(ValidationError):
        Settings(watcher_watchlist_max_symbols=6)
