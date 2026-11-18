"""Live evidence bundles use verified per-symbol ticks; replay keeps the slice default."""

from decimal import Decimal

import pytest

from app.evidence_pipeline.instrument_tick import (
    INSTRUMENT_TICK_SIZE_UNAVAILABLE,
    resolve_bundle_tick_size,
)
from app.market_contracts.enums import VenueId
from app.market_contracts.errors import MarketContractError
from app.market_contracts.provider_contracts import ContractBook, default_contract_book
from app.signal_fusion.first_slice_types import FIRST_SLICE_TICK_SIZE


def test_replay_keeps_first_slice_tick_even_when_book_differs() -> None:
    book = default_contract_book()
    assert (
        resolve_bundle_tick_size(
            symbol="BTCUSDT",
            venue=VenueId.BINANCE,
            book=book,
            replay=True,
        )
        == FIRST_SLICE_TICK_SIZE
    )


def test_live_uses_verified_contract_tick_not_the_slice_default() -> None:
    book = default_contract_book()
    hype = resolve_bundle_tick_size(
        symbol="HYPEUSDT",
        venue=VenueId.BINANCE,
        book=book,
        replay=False,
    )
    assert hype == Decimal("0.00100")
    assert hype != FIRST_SLICE_TICK_SIZE


def test_live_fails_closed_when_tick_metadata_is_missing() -> None:
    # Listing-only books without ticks still resolve known symbols from verified defaults.
    assert resolve_bundle_tick_size(
        symbol="BTCUSDT",
        venue=VenueId.BINANCE,
        book=ContractBook(()),
        replay=False,
    ) == Decimal("0.10")
    with pytest.raises(MarketContractError, match=INSTRUMENT_TICK_SIZE_UNAVAILABLE):
        resolve_bundle_tick_size(
            symbol="UNKNOWNUSDT",
            venue=VenueId.BINANCE,
            book=default_contract_book(),
            replay=False,
        )
    with pytest.raises(MarketContractError, match=INSTRUMENT_TICK_SIZE_UNAVAILABLE):
        resolve_bundle_tick_size(
            symbol="UNKNOWNUSDT",
            venue=VenueId.BINANCE,
            book=None,
            replay=False,
        )


def test_symbol_market_factory_wires_contract_book_into_assembler() -> None:
    from types import SimpleNamespace

    from app.core.config import Settings
    from app.workers.watcher_market import SymbolMarketFactory

    settings = Settings(_env_file=None, perpetual_evidence_source="replay")
    factory = SymbolMarketFactory(settings)
    port = factory(None, SimpleNamespace(), "BTCUSDT")
    assert port._assembler._contract_book is factory._contract_book_for_assembler()


def test_missing_tick_is_a_contract_unavailable_refusal_with_its_own_reason() -> None:
    from app.market_contracts.errors import ContractUnavailableError
    from app.market_contracts.evidence_diagnostics import DiagnosticReason, reason_for_exception

    with pytest.raises(ContractUnavailableError) as failure:
        resolve_bundle_tick_size(
            symbol="UNKNOWNUSDT", venue=VenueId.BYBIT, book=ContractBook(()), replay=False
        )
    assert failure.value.reason == INSTRUMENT_TICK_SIZE_UNAVAILABLE
    # Not hidden behind the invalid_contract catch-all any more.
    assert reason_for_exception(failure.value) is DiagnosticReason.CONTRACT_UNAVAILABLE


def test_undiagnosed_missing_tick_surfaces_its_reason_code() -> None:
    from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
    from app.watcher.errors import WatcherEvidenceUnavailableError

    with pytest.raises(MarketContractError) as tick:
        resolve_bundle_tick_size(
            symbol="UNKNOWNUSDT", venue=VenueId.BYBIT, book=None, replay=False
        )
    with pytest.raises(WatcherEvidenceUnavailableError) as failure:
        AssemblingWatcherScanEvidence._raise_undiagnosed(
            tick.value, symbol="UNKNOWNUSDT", timeframe="15m"
        )
    assert failure.value.reason_code == INSTRUMENT_TICK_SIZE_UNAVAILABLE


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (MarketContractError("x"), "canonical_contract_invalid_contract"),
        (ValueError("x"), "canonical_contract_unexpected_error"),
        (TypeError("x"), "canonical_contract_unexpected_error"),
        (KeyError("x"), "canonical_contract_unexpected_error"),
    ],
)
def test_contract_catch_all_keeps_the_underlying_error_class(error, reason) -> None:
    from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
    from app.watcher.errors import WatcherEvidenceUnavailableError

    with pytest.raises(WatcherEvidenceUnavailableError) as failure:
        AssemblingWatcherScanEvidence._raise_undiagnosed(error)
    # The stable reason code is unchanged for its exact-match consumers.
    assert failure.value.reason_code == reason
    assert failure.value.details["exception_class"] == type(error).__name__
    assert type(error).__name__ in str(failure.value)


def test_diagnosed_catch_all_logs_the_underlying_error_class(monkeypatch) -> None:
    from datetime import UTC, datetime

    from app.market_contracts import evidence_diagnostics
    from app.market_contracts.evidence_diagnostics import (
        DiagnosticReason,
        EvidenceComponent,
        EvidenceDiagnostics,
    )

    logged: list[tuple[str, dict]] = []

    class Logger:
        def warning(self, event, **fields):
            logged.append((event, fields))

    monkeypatch.setattr(evidence_diagnostics, "logger", Logger())
    diagnostics = EvidenceDiagnostics(object(), "HYPEUSDT", datetime.now(UTC))

    def fail():
        raise KeyError("provider payload field")

    with pytest.raises(KeyError):
        diagnostics.run(EvidenceComponent.CONTRACT, fail)
    assert diagnostics.items[-1].reason_code is DiagnosticReason.INVALID_CONTRACT
    event, fields = logged[-1]
    assert event == "canonical_evidence_component"
    assert fields["exception_class"] == "KeyError"
    assert "provider payload field" not in str(fields)


def test_assembler_uses_the_fresher_discovery_book_not_the_first_one() -> None:
    from app.core.config import Settings
    from app.workers.watcher_market import SymbolMarketFactory

    factory = SymbolMarketFactory(Settings(_env_file=None, perpetual_evidence_source="replay"))
    # Before discovery finishes the verified defaults are used.
    factory.discovery.book = ContractBook(())
    assert factory._contract_book_for_assembler().contracts == default_contract_book().contracts
    first = ContractBook(default_contract_book().contracts[:1])
    factory.discovery.book = first
    assert factory._contract_book_for_assembler() is first
    fresher = ContractBook(default_contract_book().contracts)
    factory.discovery.book = fresher
    assert factory._contract_book_for_assembler() is fresher
    # An empty refresh never replaces the last non-empty book.
    factory.discovery.book = ContractBook(())
    assert factory._contract_book_for_assembler() is fresher
