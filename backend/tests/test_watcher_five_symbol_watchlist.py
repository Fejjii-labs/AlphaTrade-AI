"""Configurable five-symbol paper Watcher watchlist."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.config import ExecutionMode, Settings
from app.market_contracts.adapters.aggtrade_cache import ClosedAggTradeCache
from app.market_contracts.adapters.bybit_usdt_perpetual import BybitUsdtPerpetualSource
from app.market_contracts.contract_discovery import fetch_binance_usdm_exchange_info
from app.market_contracts.enums import VenueId
from app.market_contracts.errors import WrongInstrumentError
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.identity import (
    binance_usdm_btcusdt,
    bybit_usdt_perpetual,
    bybit_usdt_perpetual_btcusdt,
    instrument_matches_requested_symbol,
)
from app.market_contracts.provider_contracts import (
    ContractCheck,
    ContractProviderUnreachableError,
    ContractVerdict,
    apply_binance_exchange_info,
    availability_for_symbol,
    contract_from_binance_exchange_info,
    contract_from_bybit_instruments,
    default_contract_book,
    unreachable_verdicts,
)
from app.schemas.common import Timeframe
from app.signal_fusion.assessment import SetupAssessmentState
from app.signal_fusion.evaluator import evaluate_setup
from app.signal_fusion.lifecycle import CandidateLifecycleService
from app.signal_fusion.memory import InMemoryCandidateRepository
from app.signal_fusion.symbol_strategy_match import (
    SymbolMarketEvidence,
    match_strategy_to_evidence,
)
from app.watcher.fusion_evaluation import BoundEvaluationClock
from app.watcher.memory import FakeClock, InMemoryWatcherStore
from app.workers.watcher_paper import WatcherPaperRuntime
from app.workers.watcher_paper_targets import (
    FIRST_SLICE_SYMBOL,
    PaperScanTarget,
    normalize_paper_symbols,
)
from app.workers.watcher_watchlist import (
    FileWatchlistStore,
    MemoryWatchlistStore,
    SymbolHistoryBudget,
    SymbolScanOutcome,
    SymbolStatusBook,
    WatchlistValidationError,
    default_watchlist,
    reorder_slots,
    replace_symbol,
    replace_watchlist,
    scan_symbols_sequentially,
    set_enabled,
)
from tests.support.phase6_evaluator import make_world
from tests.support.phase6_fusion import eth_evidence_identity

ORG = uuid4()
USER = uuid4()
NOW = datetime(2026, 9, 30, tzinfo=UTC)


def test_default_slots_and_reorder_replace_disable() -> None:
    config = default_watchlist(now=NOW)
    assert tuple(slot.symbol for slot in config.slots) == (
        "BTCUSDT",
        "ZECUSDT",
        "ETHUSDT",
        "TAOUSDT",
        "HYPEUSDT",
    )
    replaced = replace_symbol(config, 2, "solusdt", now=NOW)
    assert replaced.slots[1].symbol == "SOLUSDT"
    assert replaced.slots[0].symbol == "BTCUSDT"
    disabled = set_enabled(replaced, 3, False, now=NOW)
    assert disabled.slots[2].enabled is False
    assert disabled.enabled_symbols() == ("BTCUSDT", "SOLUSDT", "TAOUSDT", "HYPEUSDT")
    ordered = reorder_slots(disabled, (3, 1, 2, 5, 4), now=NOW)
    assert tuple(slot.symbol for slot in ordered.slots) == (
        "ETHUSDT",
        "BTCUSDT",
        "SOLUSDT",
        "HYPEUSDT",
        "TAOUSDT",
    )
    assert ordered.slots[0].enabled is False


def test_maximum_five_slots_and_duplicate_rejection() -> None:
    assert len(replace_watchlist([("BTCUSDT", True), ("ETHUSDT", True)], now=NOW).slots) == 2
    assert replace_watchlist([], now=NOW).slots == ()
    with pytest.raises(WatchlistValidationError, match="at most five"):
        replace_watchlist(
            [
                ("BTCUSDT", True),
                ("ETHUSDT", True),
                ("ZECUSDT", True),
                ("TAOUSDT", True),
                ("HYPEUSDT", True),
                ("SOLUSDT", True),
            ],
            now=NOW,
        )
    with pytest.raises(WatchlistValidationError, match="Duplicate"):
        replace_watchlist(
            [
                ("BTCUSDT", True),
                ("ETHUSDT", True),
                ("btcusdt", True),
                ("TAOUSDT", True),
                ("HYPEUSDT", True),
            ],
            now=NOW,
        )


def test_invalid_symbol_format_is_rejected() -> None:
    config = default_watchlist(now=NOW)
    with pytest.raises(WatchlistValidationError):
        replace_symbol(config, 1, "BTC-USD", now=NOW)
    with pytest.raises(WatchlistValidationError):
        replace_symbol(config, 1, "ETH", now=NOW)


def test_watchlist_persists_without_a_database(tmp_path: object) -> None:
    from pathlib import Path

    path = Path(str(tmp_path)) / "watchlist.json"
    store = FileWatchlistStore(path)
    saved = store.save(replace_symbol(default_watchlist(now=NOW), 5, "SOLUSDT", now=NOW))
    loaded = FileWatchlistStore(path).load()
    assert loaded.slots[4].symbol == "SOLUSDT"
    assert loaded.revision == saved.revision
    memory = MemoryWatchlistStore()
    memory.save(set_enabled(memory.load(), 1, False, now=NOW))
    assert memory.load().slots[0].enabled is False


def test_proven_binance_contracts_are_scannable_and_bybit_is_not_substituted() -> None:
    book = default_contract_book()
    for symbol, base, price_precision, quantity_precision in (
        ("BTCUSDT", "BTC", 2, 3),
        ("ETHUSDT", "ETH", 2, 3),
        ("ZECUSDT", "ZEC", 2, 3),
        ("TAOUSDT", "TAO", 2, 3),
        ("HYPEUSDT", "HYPE", 5, 2),
    ):
        source, error = availability_for_symbol(symbol, source_mode="binance_usdm", book=book)
        contract = book.contract_for(symbol, VenueId.BINANCE)
        assert source == "binance_usdm"
        assert error is None
        assert contract is not None
        assert contract.base_asset == base
        assert contract.contract_type == "PERPETUAL"
        assert contract.quote_asset == "USDT"
        assert contract.status == "TRADING"
        assert contract.price_precision == price_precision
        assert contract.quantity_precision == quantity_precision
    bybit_source, bybit_error = availability_for_symbol(
        "BTCUSDT", source_mode="bybit_usdt_perpetual", book=book
    )
    assert bybit_source == "bybit_usdt_perpetual"
    assert bybit_error is None
    eth_on_bybit, eth_on_bybit_error = availability_for_symbol(
        "ETHUSDT", source_mode="bybit_usdt_perpetual", book=book
    )
    assert eth_on_bybit == "unavailable"
    assert eth_on_bybit_error == "awaiting_contract_check"
    replay_eth, replay_eth_error = availability_for_symbol(
        "ETHUSDT", source_mode="replay", book=book
    )
    assert replay_eth == "binance_usdm"
    assert replay_eth_error is None


def test_binance_and_bybit_payloads_keep_provenance_and_reject_substitution() -> None:
    eth = contract_from_binance_exchange_info(
        {
            "symbols": [
                {
                    "symbol": "ETHUSDT",
                    "contractType": "PERPETUAL",
                    "quoteAsset": "USDT",
                    "status": "TRADING",
                    "baseAsset": "ETH",
                }
            ]
        },
        requested_symbol="ETHUSDT",
    )
    assert eth.venue is VenueId.BINANCE
    assert eth.symbol == "ETHUSDT"
    assert eth.source == "binance_usdm"
    with pytest.raises(WrongInstrumentError, match="does not list ETHUSDT"):
        contract_from_binance_exchange_info(
            {
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "contractType": "PERPETUAL",
                        "quoteAsset": "USDT",
                        "status": "TRADING",
                    }
                ]
            },
            requested_symbol="ETHUSDT",
        )
    bybit = contract_from_bybit_instruments(
        {
            "retCode": 0,
            "result": {
                "category": "linear",
                "list": [
                    {
                        "symbol": "ETHUSDT",
                        "contractType": "LinearPerpetual",
                        "quoteCoin": "USDT",
                        "status": "Trading",
                    }
                ],
            },
        },
        requested_symbol="ethusdt",
    )
    assert bybit.venue is VenueId.BYBIT
    assert bybit.source == "bybit_usdt_perpetual"
    book = default_contract_book().with_contract(eth).with_contract(bybit)
    source, error = availability_for_symbol("ETHUSDT", source_mode="binance_usdm", book=book)
    assert source == "binance_usdm"
    assert error is None
    bybit_only, bybit_only_error = availability_for_symbol(
        "ETHUSDT", source_mode="bybit_usdt_perpetual", book=book
    )
    assert bybit_only == "bybit_usdt_perpetual"
    assert bybit_only_error is None


def test_bybit_instance_does_not_serve_a_different_symbol() -> None:
    default = BybitUsdtPerpetualSource()
    assert default.active_instrument().instrument_id == bybit_usdt_perpetual_btcusdt().instrument_id
    eth = BybitUsdtPerpetualSource(instrument=bybit_usdt_perpetual("ETHUSDT"))
    assert eth.active_instrument().provider_symbol == "ETHUSDT"
    with pytest.raises(WrongInstrumentError, match="BTCUSDT"):
        default._assert_request(
            _identity_for(bybit_usdt_perpetual("ETHUSDT")),
            bybit_usdt_perpetual("ETHUSDT"),
        )


def test_market_identity_follows_the_requested_symbol_and_btc_stays_btc() -> None:
    btc = _identity_for(binance_usdm_btcusdt())
    eth = eth_evidence_identity()
    bybit_btc = _identity_for(bybit_usdt_perpetual_btcusdt())
    assert instrument_matches_requested_symbol(btc, "BTCUSDT") is True
    assert instrument_matches_requested_symbol(eth, "BTCUSDT") is False
    assert instrument_matches_requested_symbol(eth, "ETHUSDT") is True
    assert instrument_matches_requested_symbol(bybit_btc, "BTCUSDT") is True
    assert instrument_matches_requested_symbol(bybit_btc, "ETHUSDT") is False
    assert instrument_matches_requested_symbol(btc, "BTCUSDT") is True
    world = make_world()
    assessment = evaluate_setup(
        policy=world.policy,
        command=world.command.model_copy(update={"evidence_identity": eth}),
        evidence=world.evidence,
        evaluated_at=world.evaluated_at,
    )
    assert assessment.state is not SetupAssessmentState.CONFIRMED_SETUP
    fresh = evaluate_setup(
        policy=world.policy,
        command=world.command,
        evidence=world.evidence,
        evaluated_at=world.evaluated_at,
    )
    assert fresh.state is SetupAssessmentState.CONFIRMED_SETUP


def test_stale_evidence_does_not_confirm_a_setup() -> None:
    world = make_world(stale_command=True)
    assessment = evaluate_setup(
        policy=world.policy,
        command=world.command,
        evidence=world.evidence,
        evaluated_at=world.evaluated_at + timedelta(seconds=30),
    )
    assert assessment.state is SetupAssessmentState.NO_SETUP
    assert assessment.state is not SetupAssessmentState.CONFIRMED_SETUP


def test_one_symbol_failure_does_not_stop_the_others() -> None:
    seen: list[str] = []

    def evaluate(symbol: str) -> SymbolScanOutcome:
        seen.append(symbol)
        if symbol == "ZECUSDT":
            raise RuntimeError("symbol failed")
        return SymbolScanOutcome(symbol=symbol, status="ok")

    budget = SymbolHistoryBudget()
    outcomes = scan_symbols_sequentially(
        ("BTCUSDT", "ZECUSDT", "ETHUSDT", "TAOUSDT", "HYPEUSDT"),
        evaluate,
        budget,
    )
    assert [item.symbol for item in outcomes] == [
        "BTCUSDT",
        "ZECUSDT",
        "ETHUSDT",
        "TAOUSDT",
        "HYPEUSDT",
    ]
    assert outcomes[1].error == "evaluation_exception"
    assert outcomes[2].status == "ok"
    assert budget.held_symbol is None
    assert budget.peak_in_flight == 1
    assert seen == ["BTCUSDT", "ZECUSDT", "ETHUSDT", "TAOUSDT", "HYPEUSDT"]


def test_runtime_isolates_symbol_failures_and_releases_windows() -> None:
    config = reorder_slots(default_watchlist(now=NOW), (3, 2, 1, 4, 5), now=NOW)
    symbols = config.enabled_symbols()
    called: list[str] = []

    def factory(_session: object, _store: object, symbol: str) -> object:
        called.append(symbol)
        raise RuntimeError(symbol)

    runtime = WatcherPaperRuntime(
        store=InMemoryWatcherStore(),
        lifecycle=CandidateLifecycleService(
            repository=InMemoryCandidateRepository(),
            clock=BoundEvaluationClock(),
        ),
        clock=FakeClock(NOW),
        enabled=True,
        symbols=symbols,
        watchlist_mode=True,
        watchlist=config,
        watchlist_store=MemoryWatchlistStore(config),
        target_loader=lambda _session: tuple(_target(symbol) for symbol in symbols),
        evidence_factory=factory,  # type: ignore[arg-type]
        kill_switch_probe=lambda _org: False,
        settings=Settings(),
    )
    report = runtime.run_cycle()
    assert [scan.symbol for scan in report.scans] == list(symbols)
    assert all(scan.status == "failed" for scan in report.scans)
    assert called == list(symbols)
    assert runtime.history.held_symbol is None
    assert runtime.history.peak_in_flight == 1
    assert runtime.snapshot().symbols[0] == "ETHUSDT"


def test_trade_windows_stay_bounded_across_repeated_cycles() -> None:
    cache = ClosedAggTradeCache(max_entries=8, ttl_seconds=60)
    budget = SymbolHistoryBudget()
    symbols = ("BTCUSDT", "ZECUSDT", "ETHUSDT", "TAOUSDT", "HYPEUSDT")
    start = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    end = start + timedelta(minutes=15)

    def evaluate(symbol: str) -> SymbolScanOutcome:
        key = ("policy", symbol, start.isoformat(), end.isoformat())
        cache.put(key, tuple({"id": index} for index in range(50)))
        assert cache.get(key) is not None
        cache.drop_symbol(symbol)
        assert cache.get(key) is None
        return SymbolScanOutcome(symbol=symbol, status="ok")

    for _cycle in range(5):
        scan_symbols_sequentially(symbols, evaluate, budget)
    assert len(cache) == 0
    assert budget.held_symbol is None
    assert budget.peak_in_flight == 1
    assert budget.rejected_overlaps == 0


def test_strategy_match_follows_symbol_and_rejects_stale_evidence() -> None:
    btc = first_slice_identity(timeframe=Timeframe.M15, replay=True, is_live=False)
    aligned = match_strategy_to_evidence(
        strategy_symbol="BTCUSDT",
        evidence=SymbolMarketEvidence(
            symbol="BTCUSDT",
            identity=btc,
            source="replay",
            freshness="fresh",
        ),
    )
    assert aligned.matched is True
    assert aligned.reason == "symbol_aligned"
    mismatched = match_strategy_to_evidence(
        strategy_symbol="BTCUSDT",
        evidence=SymbolMarketEvidence(
            symbol="ETHUSDT",
            identity=btc,
            source="replay",
            freshness="fresh",
        ),
    )
    assert mismatched.matched is False
    assert mismatched.reason == "symbol_mismatch"
    stale = match_strategy_to_evidence(
        strategy_symbol="BTCUSDT",
        evidence=SymbolMarketEvidence(
            symbol="BTCUSDT",
            identity=btc,
            source="replay",
            freshness="stale",
        ),
    )
    assert stale.matched is False
    assert stale.reason == "stale_evidence"


def _usdm_row(symbol: str, base: str) -> dict[str, object]:
    return {
        "symbol": symbol,
        "contractType": "PERPETUAL",
        "status": "TRADING",
        "baseAsset": base,
        "quoteAsset": "USDT",
        "marginAsset": "USDT",
        "pricePrecision": 2,
        "quantityPrecision": 3,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
        ],
    }


def test_exchange_info_matches_exact_symbol_and_rejects_a_btc_substitute() -> None:
    payload = {
        "futuresType": "U_MARGINED",
        "symbols": [
            _usdm_row("BTCUSDT", "BTC"),
            _usdm_row("ETHUSDT", "ETH"),
            _usdm_row("ZECUSDT", "ZEC"),
            _usdm_row("TAOUSDT", "TAO"),
            _usdm_row("HYPEUSDT", "HYPE"),
        ],
    }
    book, verdicts = apply_binance_exchange_info(
        default_contract_book(),
        payload,
        ("ETHUSDT", "ZECUSDT", "TAOUSDT", "HYPEUSDT", "NOTAREALUSDT"),
    )
    by_symbol = {item.symbol: item for item in verdicts}
    assert by_symbol["ETHUSDT"].state.value == "verified"
    assert by_symbol["ETHUSDT"].contract is not None
    assert by_symbol["ETHUSDT"].contract.base_asset == "ETH"
    assert by_symbol["ZECUSDT"].state.value == "verified"
    assert by_symbol["TAOUSDT"].state.value == "verified"
    assert by_symbol["HYPEUSDT"].state.value == "verified"
    assert by_symbol["NOTAREALUSDT"].state.value == "unsupported"
    assert book.contract_for("NOTAREALUSDT", VenueId.BINANCE) is None
    assert book.contract_for("ETHUSDT", VenueId.BINANCE) is not None
    with pytest.raises(WrongInstrumentError, match="does not list ETHUSDT"):
        contract_from_binance_exchange_info(
            {"futuresType": "U_MARGINED", "symbols": [_usdm_row("BTCUSDT", "BTC")]},
            requested_symbol="ETHUSDT",
        )


def test_provider_unreachable_does_not_drop_a_proven_contract() -> None:
    book = default_contract_book()
    verdicts = unreachable_verdicts(
        ("ETHUSDT", "ZECUSDT", "TAOUSDT", "HYPEUSDT"),
        venue=VenueId.BINANCE,
        reason="provider_unreachable",
    )
    assert all(item.state.value == "unreachable" for item in verdicts)
    mapped = {(item.symbol, item.venue): item for item in verdicts}
    source, error = availability_for_symbol(
        "ETHUSDT",
        source_mode="binance_usdm",
        book=book,
        verdicts=mapped,
    )
    assert source == "binance_usdm"
    assert error is None
    missing, missing_error = availability_for_symbol(
        "NOTAREALUSDT",
        source_mode="binance_usdm",
        book=book,
        verdicts={
            ("NOTAREALUSDT", VenueId.BINANCE): ContractVerdict(
                symbol="NOTAREALUSDT",
                venue=VenueId.BINANCE,
                state=ContractCheck.UNREACHABLE,
                reason="provider_unreachable",
            )
        },
    )
    assert missing == "unavailable"
    assert missing_error == "provider_unreachable"


def test_verified_symbols_scan_sequentially_when_one_fails() -> None:
    failed: list[str] = []

    from app.workers.watcher_market import MarketProbeResult

    def probe(symbol: str) -> MarketProbeResult:
        failed.append(symbol)
        if symbol == "TAOUSDT":
            raise RuntimeError("tao failed")
        return MarketProbeResult(symbol, "binance_usdm", "fresh_closed_candle", NOW)

    runtime = WatcherPaperRuntime(
        store=InMemoryWatcherStore(),
        lifecycle=CandidateLifecycleService(
            repository=InMemoryCandidateRepository(),
            clock=BoundEvaluationClock(),
        ),
        clock=FakeClock(NOW),
        enabled=True,
        symbols=default_watchlist(now=NOW).enabled_symbols(),
        watchlist_mode=True,
        watchlist=default_watchlist(now=NOW),
        watchlist_store=MemoryWatchlistStore(default_watchlist(now=NOW)),
        target_loader=lambda _session: (),
        evidence_factory=lambda *_args: object(),  # type: ignore[arg-type]
        kill_switch_probe=lambda _org: False,
        settings=Settings(),
        symbol_probe=probe,
    )
    runtime.run_cycle()
    rows = {row.symbol: row for row in runtime.symbol_status.snapshot()}
    assert failed == ["BTCUSDT", "ZECUSDT", "ETHUSDT", "TAOUSDT", "HYPEUSDT"]
    assert rows["TAOUSDT"].error_state == "provider_unreachable"
    assert rows["HYPEUSDT"].error_state is None
    assert rows["HYPEUSDT"].freshness == "fresh_closed_candle"
    assert rows["ETHUSDT"].market_source == "binance_usdm"
    assert rows["BTCUSDT"].last_successful_scan is not None
    assert runtime.history.held_symbol is None
    assert runtime.history.peak_in_flight == 1
    assert runtime.history.rejected_overlaps == 0


def test_catalog_fetch_uses_a_later_host_after_regional_rejection() -> None:
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "fapi.binance.com":
            return httpx.Response(451, json={"code": 0, "msg": "restricted"})
        if request.url.host == "www.binance.com":
            return httpx.Response(
                200,
                json={
                    "futuresType": "U_MARGINED",
                    "symbols": [_usdm_row("ETHUSDT", "ETH")],
                },
            )
        return httpx.Response(403, json={"error": "unexpected host"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    payload = fetch_binance_usdm_exchange_info(client=client)
    contract = contract_from_binance_exchange_info(payload, requested_symbol="ETHUSDT")
    assert contract.symbol == "ETHUSDT"
    assert contract.base_asset == "ETH"
    blocked = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(451, json={"code": 0}))
    )
    with pytest.raises(ContractProviderUnreachableError):
        fetch_binance_usdm_exchange_info(client=blocked)


def test_legacy_btc_symbol_order_helper_is_unchanged() -> None:
    assert normalize_paper_symbols([]) == (FIRST_SLICE_SYMBOL,)
    assert normalize_paper_symbols(["ethusdt", "BTCUSDT"]) == ("BTCUSDT", "ETHUSDT")
    assert normalize_paper_symbols(["ETHUSDT"])[0] == "BTCUSDT"


def test_paper_only_and_real_trading_stays_disabled() -> None:
    settings = Settings()
    assert settings.execution_mode is ExecutionMode.PAPER
    assert settings.enable_real_trading is False
    assert settings.real_trading_enabled is False
    store = MemoryWatchlistStore(default_watchlist(now=NOW))
    store.save(set_enabled(store.load(), 1, False, now=NOW))
    assert settings.real_trading_enabled is False
    assert settings.enable_real_trading is False


def _target(symbol: str) -> PaperScanTarget:
    return PaperScanTarget(
        organization_id=ORG,
        user_id=USER,
        strategy_id=uuid4(),
        strategy_version_id=uuid4(),
        compiled_setup_definition_id=uuid4(),
        compiled_content_hash="ab" * 32,
        fusion_policy_version="fusion-policy/v1",
        symbol=symbol,
    )


def _identity_for(instrument: object) -> object:
    from app.market_contracts.first_slice import first_slice_identity
    from app.schemas.common import Timeframe

    venue = getattr(instrument, "venue", None)
    if instrument == binance_usdm_btcusdt() or venue is VenueId.BINANCE:
        return first_slice_identity(
            timeframe=Timeframe.M15,
            replay=True,
            is_live=False,
            instrument=instrument,  # type: ignore[arg-type]
        )
    from app.market_contracts.enums import SourceFamily
    from app.market_contracts.identity import (
        BYBIT_ADAPTER_VERSION,
        BYBIT_AGGRESSOR_CONVENTION,
        EvidenceMarketIdentity,
        ProviderProvenance,
        SourceIdentity,
    )

    source = SourceIdentity(
        family=SourceFamily.BYBIT_USDT_PERPETUAL_PUBLIC,
        provider_name="bybit-usdt-perpetual",
        adapter_version=BYBIT_ADAPTER_VERSION,
        aggressor_convention=BYBIT_AGGRESSOR_CONVENTION,
    )
    return EvidenceMarketIdentity(
        venue=VenueId.BYBIT,
        market_type=instrument.market_type,  # type: ignore[attr-defined]
        instrument=instrument,  # type: ignore[arg-type]
        timeframe=Timeframe.M15,
        source=source,
        provenance=ProviderProvenance(
            provider_name=source.provider_name,
            source_family=source.family,
            adapter_version=source.adapter_version,
            is_live=True,
            fallback_used=False,
            is_mock=False,
            detail="Bybit test identity.",
        ),
    )


def test_repeated_scans_keep_only_one_cycle_of_completion_history() -> None:
    budget = SymbolHistoryBudget()
    symbols = default_watchlist(now=NOW).enabled_symbols()
    for _ in range(1000):
        scan_symbols_sequentially(
            symbols, lambda symbol: SymbolScanOutcome(symbol=symbol, status="ok"), budget
        )
    assert tuple(budget.completed) == symbols
    assert budget.held_symbol is None
    assert budget.peak_in_flight == 1


def test_runtime_status_follows_symbol_across_reorder_and_replacement() -> None:
    config = default_watchlist(now=NOW)
    book = SymbolStatusBook()
    book.project(config, source_mode="binance_usdm")
    book.record_scan(
        symbol="BTCUSDT",
        succeeded=True,
        setup_state="no_setup",
        freshness="fresh",
        strategy_matches=("btc_strategy",),
        alert_state="none",
        error_state=None,
        scanned_at=NOW,
    )
    book.record_scan(
        symbol="ETHUSDT",
        succeeded=False,
        setup_state="scan_failed",
        freshness="unknown",
        strategy_matches=(),
        alert_state="none",
        error_state="provider_unreachable",
        scanned_at=NOW,
    )
    reordered = reorder_slots(config, (3, 2, 1, 4, 5), now=NOW)
    rows = {row.symbol: row for row in book.project(reordered, source_mode="binance_usdm")}
    assert rows["BTCUSDT"].position == 3
    assert rows["BTCUSDT"].last_successful_scan == NOW
    assert rows["BTCUSDT"].strategy_matches == ("btc_strategy",)
    assert rows["ETHUSDT"].position == 1
    assert rows["ETHUSDT"].last_failed_scan == NOW
    assert rows["ETHUSDT"].error_state == "provider_unreachable"
    replaced = replace_symbol(reordered, 3, "SOLUSDT", now=NOW)
    rows = {row.symbol: row for row in book.project(replaced, source_mode="binance_usdm")}
    assert "BTCUSDT" not in rows
    assert rows["SOLUSDT"].last_successful_scan is None
    assert rows["SOLUSDT"].strategy_matches == ()
    assert rows["ETHUSDT"].last_failed_scan == NOW


def test_allocator_is_released_between_symbols_after_a_market_read(monkeypatch) -> None:
    """M12: a symbol's aggTrade window is returned before the next symbol reads."""

    from dataclasses import replace as dc_replace

    import app.observability.process_memory as process_memory

    config = reorder_slots(default_watchlist(now=NOW), (3, 2, 1, 4, 5), now=NOW)
    symbols = config.enabled_symbols()
    releases: list[int] = []
    monkeypatch.setattr(process_memory, "release_allocator_memory", lambda: releases.append(1))

    def factory(_session: object, _store: object, symbol: str) -> object:
        raise RuntimeError(symbol)

    runtime = WatcherPaperRuntime(
        store=InMemoryWatcherStore(),
        lifecycle=CandidateLifecycleService(
            repository=InMemoryCandidateRepository(),
            clock=BoundEvaluationClock(),
        ),
        clock=FakeClock(NOW),
        enabled=True,
        symbols=symbols,
        watchlist_mode=True,
        watchlist=config,
        watchlist_store=MemoryWatchlistStore(config),
        target_loader=lambda _session: tuple(_target(symbol) for symbol in symbols),
        evidence_factory=factory,  # type: ignore[arg-type]
        kill_switch_probe=lambda _org: False,
        settings=Settings(),
    )
    real_scan = WatcherPaperRuntime._scan_one

    def scan_with_read(self, session, target):
        report = real_scan(self, session, target)
        return dc_replace(report, market_read_completed=target.symbol in symbols[:2])

    monkeypatch.setattr(WatcherPaperRuntime, "_scan_one", scan_with_read)
    runtime.run_cycle()
    assert len(releases) == 2  # only after the two symbols that read the market
