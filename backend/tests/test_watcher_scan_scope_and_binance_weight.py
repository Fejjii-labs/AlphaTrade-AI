"""Wider Watcher scan scope without more Binance weight.

Covers opt-in SFP/Nested projection onto watchlist symbols and timeframes, the
closed-candle kline cache and Binance 418/429 backoff.
No real network: every HTTP read goes through a MockTransport.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
from app.market_contracts.adapters.binance_rest_gate import BinanceRestGate
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.adapters.http import ReadOnlyHttpGetClient
from app.market_contracts.catalog import catalog_for_symbols
from app.market_contracts.enums import VenueId
from app.market_contracts.errors import UpstreamBanError
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.identity import binance_usdm_btcusdt
from app.schemas.common import Timeframe
from app.schemas.nested_continuation import NestedContinuationSpec
from app.services.canonical_strategy_evaluation import resolve_executable_strategy_policy
from app.watcher.contracts import EvaluationCommand, EvaluationMode, ScanRequest, ScanTrigger
from app.watcher.errors import WatcherEvidenceUnavailableError
from app.watcher.hashing import evaluation_input_hash, scan_request_hash
from app.workers.watcher_market import WatchlistContractDiscovery
from app.workers.watcher_paper_targets import list_watchlist_scan_targets
from tests.test_nested_timeframes import native_source
from tests.test_strategy_brain_nested import approved
from tests.test_strategy_brain_nested import tenant_store as nested_store

tenant_store = nested_store
WATCHLIST = ["BTCUSDT", "ZECUSDT", "ETHUSDT", "TAOUSDT", "HYPEUSDT"]


def _settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        environment="local",
        execution_mode="paper",
        enable_real_trading=False,
        real_trading_enabled=False,
        database_url="sqlite+pysqlite:///:memory:",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        market_data_cache_use_redis=False,
        watcher_orchestration_enabled=False,
        perpetual_evidence_source="binance_usdm",
        binance_request_max_retries=0,
        **overrides,
    )


# --- Problem 1: multi-symbol / multi-timeframe targets -------------------------


def test_scan_settings_default_off_and_validate_timeframes() -> None:
    settings = _settings()
    assert settings.watcher_apply_strategies_to_watchlist is False
    assert settings.watcher_scan_timeframes == ["15m", "1h"]
    assert _settings(watcher_scan_timeframes=" 15M, 1h,15m ,4h").watcher_scan_timeframes == [
        "15m",
        "1h",
        "4h",
    ]
    with pytest.raises(ValidationError, match="WATCHER_SCAN_TIMEFRAMES"):
        _settings(watcher_scan_timeframes="5m,1h")


def test_default_targets_stay_on_the_authored_market(tenant_store) -> None:
    session, org, user = tenant_store
    approved(session, org, user, NestedContinuationSpec(symbol="BTCUSDT"))
    targets = list_watchlist_scan_targets(session, symbols=WATCHLIST, organization_id=org, limit=20)
    assert [(t.symbol, t.timeframe, t.market_projection) for t in targets] == [
        ("BTCUSDT", "15m", False)
    ]


def test_projection_covers_every_symbol_and_timeframe_deterministically(tenant_store) -> None:
    session, org, user = tenant_store
    long_ = approved(session, org, user, NestedContinuationSpec(symbol="BTCUSDT"))
    short = approved(
        session, org, user, NestedContinuationSpec(symbol="BTCUSDT", direction="short")
    )

    def load(limit: int = 20):
        return list_watchlist_scan_targets(
            session,
            symbols=WATCHLIST,
            organization_id=org,
            limit=limit,
            project_timeframes=("15m", "1h"),
        )

    targets = load()
    # 5 symbols x 2 timeframes x 2 strategies; BTC 15m stays the native scope.
    assert len(targets) == 20
    assert {(t.symbol, t.timeframe) for t in targets} == {
        (s, tf) for s in WATCHLIST for tf in ("15m", "1h")
    }
    native = [t for t in targets if not t.market_projection]
    assert {(t.symbol, t.timeframe) for t in native} == {("BTCUSDT", "15m")}
    assert {t.strategy_version_id for t in native} == {long_["version_id"], short["version_id"]}
    # Exact scopes come first; projections only fill the remaining room.
    assert [t.market_projection for t in targets] == [False, False] + [True] * 18
    # policy_id binds lineage + symbol + timeframe, is unique and stable across loads.
    assert len({t.policy_id for t in targets}) == 20
    assert [t.policy_id for t in load()] == [t.policy_id for t in targets]
    assert len({t.compiled_content_hash for t in targets}) == 2
    # The cycle cap still bounds the scan.
    assert len(load(limit=7)) == 7


def _command(target, *, timeframe: str) -> EvaluationCommand:
    request = ScanRequest(
        organization_id=target.organization_id,
        scan_scope=target.scan_scope,
        policy_id=target.policy_id,
        policy_version=1,
        policy_content_hash="a" * 64,
        watchlist_item_ids=(),
        timeframe=timeframe,
        idempotency_key="projection",
    )
    return EvaluationCommand(
        command_id=uuid4(),
        request=request,
        request_hash=scan_request_hash(request),
        evaluation_input_hash=evaluation_input_hash(request),
        mode=EvaluationMode.PREVIEW,
        trigger=ScanTrigger.MANUAL,
        correlation_id=uuid4(),
    )


def test_projected_scope_reads_and_evaluates_the_scanned_market(tenant_store) -> None:
    session, org, user = tenant_store
    template = approved(session, org, user, NestedContinuationSpec(symbol="BTCUSDT"))
    executable = resolve_executable_strategy_policy(
        session, organization_id=org, strategy_version_id=template["version_id"]
    )
    target = next(
        t
        for t in list_watchlist_scan_targets(
            session,
            symbols=["ETHUSDT"],
            organization_id=org,
            limit=5,
            project_timeframes=("1h",),
        )
    )
    assert (target.symbol, target.timeframe, target.market_projection) == ("ETHUSDT", "1h", True)
    source, now, requests = native_source(Timeframe.H1)
    source.allow_verified_contract("ETHUSDT")
    try:

        def port(projection: bool) -> AssemblingWatcherScanEvidence:
            return AssemblingWatcherScanEvidence(
                FirstSliceEvidenceAssembler(
                    source,
                    replay=False,
                    clock=lambda: now,
                    catalog=catalog_for_symbols(["ETHUSDT"], venue=VenueId.BINANCE),
                ),
                executable_resolver=lambda _: executable,
                session=session,
                symbol="ETHUSDT",
                market_projection=projection,
            )

        # Projection off: a scope off the authored market fails closed, no read.
        with pytest.raises(WatcherEvidenceUnavailableError) as refused:
            port(False).load(_command(target, timeframe="1h"))
        assert refused.value.reason_code == "strategy_market_mismatch"
        assert requests == []

        projected = port(True)
        assert projected.load(_command(target, timeframe="1h")) is not None
        assembled, bound = projected.last_assembly()
        assert (bound.authored_spec.symbol, bound.authored_spec.trigger_timeframe) == (
            "ETHUSDT",
            Timeframe.H1,
        )
        # Same approved lineage and parameters; only the market binding moved.
        assert bound.compiled_content_hash == executable.compiled_content_hash
        assert bound.evaluation_params == executable.evaluation_params
        assert assembled.identity.instrument.provider_symbol == "ETHUSDT"
        assert assembled.identity.timeframe is Timeframe.H1
        klines = [r for r in requests if r.url.path.endswith("klines")]
        assert {(r.url.params["symbol"], r.url.params["interval"]) for r in klines} == {
            ("ETHUSDT", "1h")
        }
    finally:
        source.close()


# --- Problem 2: closed-candle kline cache --------------------------------------

BOUNDARY = datetime(2026, 11, 2, 3, 15, tzinfo=UTC)


def _kline_rows(boundary: datetime, count: int) -> list[list[object]]:
    """``count`` rows ending with the candle forming after ``boundary``."""

    rows = []
    for index in range(count):
        start = boundary - timedelta(minutes=15) * (count - 1 - index)
        rows.append(
            [
                int(start.timestamp() * 1000),
                "100",
                "101",
                "99",
                "100.5",
                "10",
                int((start + timedelta(minutes=15)).timestamp() * 1000) - 1,
                "1000",
                7,
                "0",
                "0",
                "0",
            ]
        )
    return rows


def _kline_source() -> tuple[BinanceUsdmPerpetualSource, list[httpx.Request]]:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/fapi/v1/klines"
        calls.append(request)
        end = datetime.fromtimestamp(int(request.url.params["endTime"]) / 1000, UTC)
        boundary = end.replace(second=0, microsecond=0) - timedelta(minutes=end.minute % 15)
        return httpx.Response(200, json=_kline_rows(boundary, int(request.url.params["limit"])))

    return BinanceUsdmPerpetualSource(transport=httpx.MockTransport(handler)), calls


def _fetch(source: BinanceUsdmPerpetualSource, at: datetime, bars: int):
    return source.fetch_closed_ohlcv(
        identity=first_slice_identity(timeframe=Timeframe.M15, replay=False, is_live=True),
        instrument=binance_usdm_btcusdt(),
        timeframe=Timeframe.M15,
        min_final_bars=bars,
        evaluated_at=at,
    )


def test_many_scopes_on_one_symbol_timeframe_share_one_kline_read() -> None:
    source, calls = _kline_source()
    first = BOUNDARY + timedelta(seconds=20)
    try:
        # SFP + Nested + monitor + more scopes over a whole bar: one confirmation pair.
        series = [
            _fetch(source, first + timedelta(seconds=offset), bars)
            for offset, bars in ((0, 256), (1, 256), (15, 100), (300, 30), (870, 256))
        ]
        assert len(calls) == 2
        assert all(s.bars[-1].interval_end == BOUNDARY for s in series)
        assert [len(s.bars) for s in series] == [256, 256, 100, 30, 256]
    finally:
        source.close()


def test_kline_cache_refetches_only_after_the_next_close() -> None:
    source, calls = _kline_source()
    try:
        _fetch(source, BOUNDARY + timedelta(seconds=20), 100)
        _fetch(source, BOUNDARY + timedelta(minutes=14, seconds=59), 100)
        assert len(calls) == 2
        nxt = BOUNDARY + timedelta(minutes=15)
        series = _fetch(source, nxt + timedelta(seconds=20), 100)
        assert len(calls) == 4
        assert series.bars[-1].interval_end == nxt
        # A larger request than the cached payload is a real read, then shared.
        _fetch(source, nxt + timedelta(seconds=30), 256)
        _fetch(source, nxt + timedelta(seconds=40), 256)
        assert len(calls) == 6
    finally:
        source.close()


def test_payload_without_the_rolled_forming_candle_is_not_cached() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        # Provider has not rolled over yet: the last row is the just-closed bar.
        rows = _kline_rows(BOUNDARY - timedelta(minutes=15), int(request.url.params["limit"]))
        return httpx.Response(200, json=rows)

    source = BinanceUsdmPerpetualSource(transport=httpx.MockTransport(handler))
    try:
        _fetch(source, BOUNDARY + timedelta(seconds=20), 30)
        _fetch(source, BOUNDARY + timedelta(seconds=40), 30)
        assert len(calls) == 4
    finally:
        source.close()


# --- Problem 2: 418/429 process-wide backoff -----------------------------------


class _LogRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, object]]] = []

    def error(self, event: str, **fields: object) -> None:
        self.events.append(("error", event, fields))

    def warning(self, event: str, **fields: object) -> None:
        self.events.append(("warning", event, fields))


def _ban_client(handler, gate: BinanceRestGate) -> ReadOnlyHttpGetClient:
    return ReadOnlyHttpGetClient(
        base_url="https://fapi.binance.com",
        timeout_seconds=1.0,
        transport=httpx.MockTransport(handler),
        rest_gate=gate,
        sleeper=lambda _s: None,
    )


# 10 min, the 40 min staging ban, and a 3 day repeat-offender ban.
@pytest.mark.parametrize("ban_seconds", [600, 40 * 60, 3 * 86_400])
def test_418_body_ban_deadline_closes_the_gate_for_every_client(
    monkeypatch, ban_seconds: int
) -> None:
    now = 1_800_000_000.0
    monkeypatch.setattr("app.market_contracts.adapters.http.time.time", lambda: now)
    log = _LogRecorder()
    monkeypatch.setattr("app.market_contracts.adapters.binance_rest_gate.logger", log)
    clock = [1000.0]
    gate = BinanceRestGate(clock=lambda: clock[0], rng=lambda: 0.0)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        until = int(now + ban_seconds) * 1000
        body = {"code": -1003, "msg": f"Way too many requests; IP banned until {until}."}
        return httpx.Response(418, headers={"Retry-After": "60"}, json=body)

    with pytest.raises(UpstreamBanError):
        _ban_client(handler, gate).get_json("/fapi/v1/ping")
    closed = gate.remaining()
    # The longer body deadline wins over Retry-After: 60, and is not cut to 900 s.
    assert closed is not None and closed[0] == "ban" and closed[1] >= ban_seconds - 1
    with pytest.raises(UpstreamBanError):
        _ban_client(handler, gate).get_json(
            "/fapi/v1/klines", {"symbol": "BTCUSDT", "interval": "15m", "limit": 4}
        )
    # One second before the deadline the process is still banned.
    clock[0] += ban_seconds - 1
    with pytest.raises(UpstreamBanError):
        _ban_client(handler, gate).get_json("/fapi/v1/ping")
    assert calls == ["/fapi/v1/ping"], "no request may be sent while the process is banned"
    long_pauses = [e for e in log.events if e[1] == "binance_rest_gate_long_pause"]
    if ban_seconds > 900:
        assert [level for level, _, _ in long_pauses] == ["error"]
        assert long_pauses[0][2]["pause_seconds"] >= ban_seconds - 1
    else:
        assert long_pauses == []


@pytest.mark.parametrize(
    "body",
    [
        "<html>banned</html>",
        "IP banned until 1799999000000",  # deadline already passed
        "IP banned until 179999900000",  # 12 digits
        " " * 600 + "IP banned until 1800000600000",  # beyond the 512 byte scan
    ],
)
def test_418_without_a_usable_deadline_waits_the_minimum_ban(monkeypatch, body: str) -> None:
    monkeypatch.setattr("app.market_contracts.adapters.http.time.time", lambda: 1_800_000_000.0)
    gate = BinanceRestGate(rng=lambda: 0.0)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            418, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, text=body
        )

    with pytest.raises(UpstreamBanError):
        _ban_client(handler, gate).get_json("/fapi/v1/ping")
    closed = gate.remaining()
    assert closed is not None and closed[0] == "ban" and closed[1] >= 119


def test_contract_discovery_honors_and_feeds_the_process_gate() -> None:
    gate = BinanceRestGate(rng=lambda: 0.0)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.url.host}{request.url.path}")
        return httpx.Response(429, headers={"Retry-After": "120"})

    discovery = WatchlistContractDiscovery(
        _settings(), transport=httpx.MockTransport(handler), rest_gate=gate
    )
    discovery(discovery.book, ["ETHUSDT"])
    assert calls[0] == "fapi.binance.com/fapi/v1/exchangeInfo"
    closed = gate.remaining()
    assert closed is not None and closed[0] == "rate_limit" and closed[1] >= 119
    assert discovery.verdicts[("ETHUSDT", VenueId.BINANCE)].reason == "provider_unreachable"
    # The next refresh during the window sends nothing and keeps failing closed.
    before = len(calls)
    discovery._binance = None
    discovery._cache.clear()
    discovery(discovery.book, ["ETHUSDT"])
    assert len(calls) == before
    assert discovery.verdicts[("ETHUSDT", VenueId.BINANCE)].reason == "provider_unreachable"


def test_exact_scopes_are_never_starved_by_projections(tenant_store) -> None:
    """Staging shape: exact per-symbol versions fill the cap; projections only use leftover room."""

    session, org, user = tenant_store
    exact: set[tuple[str, str]] = set()
    for symbol in WATCHLIST:
        for direction in ("long", "short"):
            approved(session, org, user, NestedContinuationSpec(symbol=symbol, direction=direction))
        exact.add((symbol, "15m"))

    def load(limit: int):
        return list_watchlist_scan_targets(
            session,
            symbols=WATCHLIST,
            organization_id=org,
            limit=limit,
            project_timeframes=("15m", "1h"),
        )

    native_count = 2 * len(WATCHLIST)
    # Cap exactly full of exact scopes: projection adds nothing and drops nothing.
    capped = load(native_count)
    assert len(capped) == native_count
    assert not any(t.market_projection for t in capped)
    assert {(t.symbol, t.timeframe) for t in capped} == exact
    # Cap smaller than the exact set: still only exact scopes.
    assert not any(t.market_projection for t in load(native_count - 3))
    # Extra room: every exact scope is present and listed before any projection.
    roomy = load(100)
    flags = [t.market_projection for t in roomy]
    assert flags == sorted(flags)
    assert sum(not f for f in flags) == native_count
    projected = [t for t in roomy if t.market_projection]
    # Projection skips markets an exact version of the same family already covers.
    assert projected
    assert all(t.timeframe == "1h" for t in projected)
    assert not any((t.symbol, t.timeframe) in exact for t in projected)
