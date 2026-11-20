"""Bounded paper Watcher values and status projection.

Production ownership/persistence is organization-scoped in the DB repository.
File and memory stores remain test/legacy utilities, not production authority.
Symbols are enabled, disabled, replaced, and reordered without deployment.
BTC is a normal slot, not an identity
that is injected into every other market.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import Settings
from app.market_contracts.enums import VenueId
from app.market_contracts.errors import WrongInstrumentError
from app.market_contracts.identity import require_linear_usdt_symbol
from app.market_contracts.provider_contracts import (
    ContractBook,
    ContractVerdict,
    availability_for_symbol,
    default_contract_book,
)

MAX_WATCHLIST_SLOTS = 5
DEFAULT_WATCHLIST_SYMBOLS = (
    "BTCUSDT",
    "ZECUSDT",
    "ETHUSDT",
    "TAOUSDT",
    "HYPEUSDT",
)
_SLOT_POSITIONS = (1, 2, 3, 4, 5)


class WatchlistValidationError(ValueError):
    """The requested watchlist cannot be stored."""


@dataclass(frozen=True, slots=True)
class WatchlistSlot:
    """One logical symbol slot. Position is 1-based and stable for the UI."""

    position: int
    symbol: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class WatchlistConfiguration:
    """Ordered slots. Revision increments on every accepted change."""

    slots: tuple[WatchlistSlot, ...]
    revision: int
    updated_at: datetime

    def enabled_symbols(self) -> tuple[str, ...]:
        return tuple(slot.symbol for slot in self.slots if slot.enabled)


@dataclass(frozen=True, slots=True)
class SymbolRuntimeStatus:
    """Bounded per-symbol status. This is not the editable configuration."""

    position: int
    symbol: str
    enabled: bool
    market_source: str
    freshness: str
    last_successful_scan: datetime | None
    last_failed_scan: datetime | None
    setup_state: str
    strategy_matches: tuple[str, ...]
    alert_state: str
    error_state: str | None


@dataclass(frozen=True, slots=True)
class SymbolScanOutcome:
    symbol: str
    status: str
    error: str | None = None


class SymbolHistoryBudget:
    """Allows one historical window in memory at a time."""

    def __init__(self) -> None:
        self._held: str | None = None
        self._lock = threading.Lock()
        self.peak_in_flight = 0
        self.completed: deque[str] = deque(maxlen=MAX_WATCHLIST_SLOTS)
        self.rejected_overlaps = 0

    @property
    def held_symbol(self) -> str | None:
        with self._lock:
            return self._held

    def acquire(self, symbol: str) -> None:
        token = symbol.strip().upper()
        with self._lock:
            if self._held is not None:
                self.rejected_overlaps += 1
                raise RuntimeError(
                    f"Refusing to load historical data for {token} "
                    f"while {self._held} is still in memory."
                )
            self._held = token
            self.peak_in_flight = 1

    def release(self, symbol: str) -> None:
        token = symbol.strip().upper()
        with self._lock:
            if self._held != token:
                return
            self._held = None
            self.completed.append(token)


class SymbolStatusBook:
    """Latest status for each slot. Scan history is one timestamp per outcome."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: dict[int, SymbolRuntimeStatus] = {}

    def restore(self, rows: Sequence[SymbolRuntimeStatus]) -> None:
        with self._lock:
            self._rows = {row.position: row for row in rows[:MAX_WATCHLIST_SLOTS]}

    def project(
        self,
        config: WatchlistConfiguration,
        *,
        source_mode: str,
        book: ContractBook | None = None,
        verdicts: Mapping[tuple[str, VenueId], ContractVerdict] | None = None,
    ) -> tuple[SymbolRuntimeStatus, ...]:
        """Refresh availability from the contract book without erasing scan times."""

        contracts = book if book is not None else default_contract_book()
        with self._lock:
            previous_by_symbol = {row.symbol: row for row in self._rows.values()}
            refreshed: dict[int, SymbolRuntimeStatus] = {}
            for slot in config.slots:
                previous = previous_by_symbol.get(slot.symbol)
                source, error = availability_for_symbol(
                    slot.symbol,
                    source_mode=source_mode,
                    book=contracts,
                    verdicts=verdicts,
                )
                retained = previous
                if not slot.enabled:
                    setup = "disabled"
                    freshness = "not_evaluated"
                    error_state = None
                elif error is not None:
                    setup = "unavailable"
                    freshness = "unavailable"
                    error_state = error
                elif retained is None:
                    setup = "not_scanned"
                    freshness = "unknown"
                    error_state = None
                else:
                    setup = retained.setup_state
                    freshness = retained.freshness
                    error_state = retained.error_state
                refreshed[slot.position] = SymbolRuntimeStatus(
                    position=slot.position,
                    symbol=slot.symbol,
                    enabled=slot.enabled,
                    market_source=source,
                    freshness=freshness,
                    last_successful_scan=(
                        None if retained is None else retained.last_successful_scan
                    ),
                    last_failed_scan=None if retained is None else retained.last_failed_scan,
                    setup_state=setup,
                    strategy_matches=(
                        ()
                        if retained is None or not slot.enabled or error
                        else retained.strategy_matches
                    ),
                    alert_state=(
                        "none"
                        if retained is None or not slot.enabled or error
                        else retained.alert_state
                    ),
                    error_state=error_state,
                )
            self._rows = refreshed
            return self._ordered_rows()

    def record_scan(
        self,
        *,
        symbol: str,
        succeeded: bool,
        setup_state: str,
        freshness: str,
        strategy_matches: Sequence[str],
        alert_state: str,
        error_state: str | None,
        scanned_at: datetime,
        market_source: str | None = None,
    ) -> None:
        token = symbol.strip().upper()
        matches = tuple(strategy_matches[:8])
        with self._lock:
            for position, row in list(self._rows.items()):
                if row.symbol != token or not row.enabled:
                    continue
                self._rows[position] = replace(
                    row,
                    freshness=freshness,
                    market_source=market_source or row.market_source,
                    last_successful_scan=scanned_at if succeeded else row.last_successful_scan,
                    last_failed_scan=None if succeeded else scanned_at,
                    setup_state=setup_state,
                    strategy_matches=matches,
                    alert_state=alert_state,
                    error_state=error_state,
                )

    def mark_state(self, symbol: str, *, setup_state: str, error_state: str | None) -> None:
        """An eligibility/no-op state is not a market scan."""
        with self._lock:
            for position, row in list(self._rows.items()):
                if row.symbol == symbol:
                    self._rows[position] = replace(
                        row,
                        setup_state=setup_state,
                        freshness="not_evaluated",
                        error_state=error_state,
                        strategy_matches=(),
                        alert_state="none",
                    )

    def mark_unscanned(self, symbol: str, *, setup_state: str, error_state: str | None) -> None:
        token = symbol.strip().upper()
        with self._lock:
            for position, row in list(self._rows.items()):
                if row.symbol != token or not row.enabled:
                    continue
                if row.setup_state not in {"not_scanned", "unknown"}:
                    continue
                self._rows[position] = replace(
                    row,
                    setup_state=setup_state,
                    error_state=error_state,
                    strategy_matches=(),
                    alert_state="none",
                )

    def snapshot(self) -> tuple[SymbolRuntimeStatus, ...]:
        with self._lock:
            return self._ordered_rows()

    def _ordered_rows(self) -> tuple[SymbolRuntimeStatus, ...]:
        return tuple(self._rows[position] for position in _SLOT_POSITIONS if position in self._rows)


def scan_symbols_sequentially(
    symbols: Sequence[str],
    evaluate: Callable[[str], SymbolScanOutcome],
    budget: SymbolHistoryBudget,
) -> tuple[SymbolScanOutcome, ...]:
    """Evaluate one symbol at a time. One failure does not cancel the rest."""

    outcomes: list[SymbolScanOutcome] = []
    for raw in symbols:
        token = raw.strip().upper()
        budget.acquire(token)
        try:
            outcomes.append(evaluate(token))
        except Exception:
            outcomes.append(
                SymbolScanOutcome(symbol=token, status="failed", error="evaluation_exception")
            )
        finally:
            budget.release(token)
    return tuple(outcomes)


def release_symbol_history(source: object | None, symbol: str) -> None:
    """Ask a market source to drop this symbol's trade window, when it can."""

    if source is None:
        return
    method = getattr(source, "release_symbol_history", None)
    if callable(method):
        method(symbol)


def default_watchlist(*, now: datetime | None = None) -> WatchlistConfiguration:
    moment = now or datetime.now(UTC)
    slots = tuple(
        WatchlistSlot(position=index + 1, symbol=symbol, enabled=True)
        for index, symbol in enumerate(DEFAULT_WATCHLIST_SYMBOLS)
    )
    return WatchlistConfiguration(slots=slots, revision=0, updated_at=moment)


def cap_enabled_slots(config: WatchlistConfiguration, max_symbols: int) -> WatchlistConfiguration:
    """Keep at most ``max_symbols`` enabled slots, in slot order; disable the rest.

    Revision and timestamps are preserved so optimistic concurrency and the UI
    still see the stored revision. A cap at or above the slot count returns the
    configuration unchanged.
    """

    if max_symbols >= len(config.slots):
        return config
    kept = 0
    slots: list[WatchlistSlot] = []
    for slot in config.slots:
        if slot.enabled and kept < max_symbols:
            kept += 1
            slots.append(slot)
        elif slot.enabled:
            slots.append(WatchlistSlot(position=slot.position, symbol=slot.symbol, enabled=False))
        else:
            slots.append(slot)
    return WatchlistConfiguration(
        slots=tuple(slots), revision=config.revision, updated_at=config.updated_at
    )


def canonical_perpetual_symbol(symbol: str) -> str:
    try:
        token, _base = require_linear_usdt_symbol(symbol)
    except WrongInstrumentError as exc:
        raise WatchlistValidationError(str(exc)) from exc
    return token


def replace_symbol(
    config: WatchlistConfiguration,
    position: int,
    symbol: str,
    *,
    now: datetime | None = None,
) -> WatchlistConfiguration:
    token = canonical_perpetual_symbol(symbol)
    slots = _replace_at(config, position, symbol=token)
    return _validated(slots, revision=config.revision + 1, now=now)


def set_enabled(
    config: WatchlistConfiguration,
    position: int,
    enabled: bool,
    *,
    now: datetime | None = None,
) -> WatchlistConfiguration:
    slots = _replace_at(config, position, enabled=enabled)
    return _validated(slots, revision=config.revision + 1, now=now)


def reorder_slots(
    config: WatchlistConfiguration,
    positions: Sequence[int],
    *,
    now: datetime | None = None,
) -> WatchlistConfiguration:
    if sorted(int(item) for item in positions) != list(range(1, len(config.slots) + 1)):
        raise WatchlistValidationError("Reorder must be a permutation of slots 1 through 5.")
    by_position = {slot.position: slot for slot in config.slots}
    ordered = tuple(
        WatchlistSlot(
            position=index,
            symbol=by_position[int(old)].symbol,
            enabled=by_position[int(old)].enabled,
        )
        for index, old in enumerate(positions, start=1)
    )
    return _validated(ordered, revision=config.revision + 1, now=now)


def replace_watchlist(
    slots: Sequence[tuple[str, bool]],
    *,
    revision: int = 1,
    now: datetime | None = None,
) -> WatchlistConfiguration:
    """Replace the five slots in display order."""

    if len(slots) > MAX_WATCHLIST_SLOTS:
        raise WatchlistValidationError("Watcher watchlist allows at most five slots.")
    built = tuple(
        WatchlistSlot(position=index, symbol=canonical_perpetual_symbol(symbol), enabled=enabled)
        for index, (symbol, enabled) in enumerate(slots, start=1)
    )
    return _validated(built, revision=revision, now=now)


def ordered_watch_symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    """Preserve caller order. Do not inject BTCUSDT."""

    ordered: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        token = raw.strip().upper()
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return tuple(ordered)


def watchlist_path(settings: Settings) -> Path:
    raw = settings.watcher_watchlist_path.strip()
    if not raw:
        return Path("var") / "watcher-watchlist.json"
    return Path(raw)


class FileWatchlistStore:
    """JSON file store. Load does not create the file. Save is atomic."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> WatchlistConfiguration:
        with self._lock:
            if not self._path.is_file():
                return default_watchlist()
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        return configuration_from_payload(payload)

    def save(self, config: WatchlistConfiguration) -> WatchlistConfiguration:
        encoded = json.dumps(_payload(config), separators=(",", ":"), sort_keys=True)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(self._path.suffix + ".tmp")
            temporary.write_text(encoded, encoding="utf-8")
            temporary.replace(self._path)
        return config


class MemoryWatchlistStore:
    """Process-local store for tests."""

    def __init__(self, config: WatchlistConfiguration | None = None) -> None:
        self._config = config if config is not None else default_watchlist()
        self._lock = threading.Lock()

    def load(self) -> WatchlistConfiguration:
        with self._lock:
            return self._config

    def save(self, config: WatchlistConfiguration) -> WatchlistConfiguration:
        with self._lock:
            self._config = config
        return config


def configuration_from_payload(payload: object) -> WatchlistConfiguration:
    if not isinstance(payload, dict):
        raise WatchlistValidationError("Watchlist file is not an object.")
    raw_slots = payload.get("slots")
    if not isinstance(raw_slots, list):
        raise WatchlistValidationError("Watchlist file is missing slots.")
    pairs: list[tuple[str, bool]] = []
    for item in raw_slots:
        if not isinstance(item, dict):
            raise WatchlistValidationError("Watchlist slot is not an object.")
        pairs.append((str(item.get("symbol", "")), bool(item.get("enabled", False))))
    revision = payload.get("revision", 0)
    updated = payload.get("updated_at")
    moment = datetime.fromisoformat(str(updated)) if isinstance(updated, str) else None
    config = replace_watchlist(pairs, revision=int(revision) if isinstance(revision, int) else 0)
    if moment is not None:
        return replace(config, updated_at=moment)
    return config


def enabled_watch_symbols(
    settings: Settings,
    *,
    store: FileWatchlistStore | None = None,
) -> tuple[str, ...]:
    resolved = store if store is not None else FileWatchlistStore(watchlist_path(settings))
    return resolved.load().enabled_symbols()


def _payload(config: WatchlistConfiguration) -> dict[str, object]:
    return {
        "revision": config.revision,
        "updated_at": config.updated_at.astimezone(UTC).isoformat(),
        "slots": [
            {"position": slot.position, "symbol": slot.symbol, "enabled": slot.enabled}
            for slot in config.slots
        ],
    }


def _replace_at(
    config: WatchlistConfiguration,
    position: int,
    *,
    symbol: str | None = None,
    enabled: bool | None = None,
) -> tuple[WatchlistSlot, ...]:
    if position not in _SLOT_POSITIONS:
        raise WatchlistValidationError("Watchlist slot must be between 1 and 5.")
    updated: list[WatchlistSlot] = []
    found = False
    for slot in config.slots:
        if slot.position != position:
            updated.append(slot)
            continue
        found = True
        updated.append(
            WatchlistSlot(
                position=slot.position,
                symbol=slot.symbol if symbol is None else symbol,
                enabled=slot.enabled if enabled is None else enabled,
            )
        )
    if not found:
        raise WatchlistValidationError(f"Watchlist slot {position} does not exist.")
    return tuple(updated)


def _validated(
    slots: tuple[WatchlistSlot, ...],
    *,
    revision: int,
    now: datetime | None,
) -> WatchlistConfiguration:
    if len(slots) > MAX_WATCHLIST_SLOTS:
        raise WatchlistValidationError("Watcher watchlist allows at most five slots.")
    positions = tuple(slot.position for slot in slots)
    if positions != tuple(range(1, len(slots) + 1)):
        raise WatchlistValidationError("Watchlist slots must stay in positions 1 through 5.")
    enabled = [slot for slot in slots if slot.enabled]
    if len(enabled) > MAX_WATCHLIST_SLOTS:
        raise WatchlistValidationError("At most five symbols can be enabled.")
    seen: set[str] = set()
    for slot in slots:
        token = canonical_perpetual_symbol(slot.symbol)
        if token in seen:
            raise WatchlistValidationError(f"Duplicate watchlist symbol {token}.")
        seen.add(token)
        if token != slot.symbol:
            raise WatchlistValidationError("Watchlist symbols must already be canonical.")
    return WatchlistConfiguration(
        slots=slots,
        revision=revision,
        updated_at=now or datetime.now(UTC),
    )
