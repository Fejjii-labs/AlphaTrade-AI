"""Tests for order_block and breakout_retest setup detectors (Slice 76)."""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.analysis.types import SetupDetection
from app.core.config import Settings
from app.db.models import PaperValidationAlert
from app.providers.market_data import MockMarketDataProvider, OHLCVBar
from app.schemas.common import AlertDeliveryChannel, AlertDeliveryStatus
from app.schemas.market_watcher import (
    CREATE_IN_APP_ALERTS_CONFIRM_PHRASE,
    SCAN_CONFIRM_PHRASE,
    MarketWatcherScanRequest,
)
from app.services.market_data_service import MarketDataService
from app.services.market_watcher_service import MarketWatcherService
from app.services.market_watcher_setup_detectors import (
    SETUP_DETECTOR_VERSIONS,
    detect_setup_candidates,
    detectors_enabled,
)
from tests.test_market_watcher_scanner_slice_74 import (
    _BASE,
    ORG_ID,
    USER_ID,
    _analysis_result,
    _flat_bars,
)


def test_detectors_enabled_includes_new_conditions() -> None:
    assert detectors_enabled() == [
        "breakout_retest",
        "liquidity_sweep",
        "order_block",
        "sfp",
        "trend_pullback",
    ]


def test_order_block_candidate_maps_correctly() -> None:
    detection = SetupDetection(
        name="order_block",
        detected=True,
        direction="long",
        reason="Bullish order block: down candle before an impulsive up move.",
        metrics={"ob_low": 97.0, "ob_high": 98.5, "impulse": 4.5},
    )
    bars = _flat_bars(price=99.0)
    result = _analysis_result(detections=(detection,))
    candidates = detect_setup_candidates(
        symbol="BTCUSDT",
        timeframe="15m",
        bars=bars,
        result=result,
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.condition == "order_block"
    assert candidate.direction == "long"
    assert candidate.trigger_level == 98.5
    assert candidate.invalidation_level == 97.0
    assert candidate.reason == detection.reason
    assert candidate.confidence == 72.5
    assert candidate.source == "market_watcher"
    assert candidate.detector_version == SETUP_DETECTOR_VERSIONS["order_block"]
    assert candidate.metrics["latest_price"] == 99.0
    assert candidate.metrics["source"] == "market_watcher"


def test_breakout_retest_candidate_maps_correctly() -> None:
    detection = SetupDetection(
        name="breakout_retest",
        detected=True,
        direction="long",
        reason="Broke resistance then retested it as support.",
        metrics={"level": 105.0, "close": 105.2},
    )
    bars = _flat_bars(price=105.2)
    bars[-1] = OHLCVBar(
        open=104.8,
        high=105.5,
        low=104.5,
        close=105.2,
        volume=1000.0,
        timestamp=bars[-1].timestamp,
    )
    result = _analysis_result(detections=(detection,))
    candidates = detect_setup_candidates(
        symbol="ETHUSDT",
        timeframe="1h",
        bars=bars,
        result=result,
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.condition == "breakout_retest"
    assert candidate.direction == "long"
    assert candidate.trigger_level == 105.0
    assert candidate.invalidation_level == 104.5
    assert candidate.detector_version == SETUP_DETECTOR_VERSIONS["breakout_retest"]
    assert candidate.metrics["latest_price"] == 105.2


def test_non_dry_run_creates_in_app_alerts_only_for_order_block(
    slice74_db: sessionmaker[Session],
) -> None:
    detection = SetupDetection(
        name="order_block",
        detected=True,
        direction="long",
        reason="Bullish order block: down candle before an impulsive up move.",
        metrics={"ob_low": 97.0, "ob_high": 98.5, "impulse": 4.5},
    )
    stub = detect_setup_candidates(
        symbol="BTCUSDT",
        timeframe="15m",
        bars=_flat_bars(),
        result=_analysis_result(detections=(detection,)),
    )
    with slice74_db() as session:
        settings = Settings(**_BASE)
        market_data = MarketDataService(MockMarketDataProvider())
        svc = MarketWatcherService(session, settings, market_data=market_data)
        with patch(
            "app.services.market_watcher_scanner.detect_setup_candidates",
            return_value=stub,
        ):
            result = svc.scan(
                organization_id=ORG_ID,
                user_id=USER_ID,
                request=MarketWatcherScanRequest(
                    confirm=SCAN_CONFIRM_PHRASE,
                    create_in_app_alerts_confirm=CREATE_IN_APP_ALERTS_CONFIRM_PHRASE,
                    symbols=["BTCUSDT"],
                    timeframes=["15m"],
                    dry_run=False,
                ),
            )
        session.commit()
        assert result.alerts_created >= 1
        rows = list(session.scalars(select(PaperValidationAlert)).all())
        for row in rows:
            assert row.delivery_channel == AlertDeliveryChannel.IN_APP
            assert row.delivery_status in (
                AlertDeliveryStatus.DISABLED,
                AlertDeliveryStatus.PENDING,
                AlertDeliveryStatus.SKIPPED,
            )
            assert row.metadata_json.get("source") == "market_watcher"
            assert row.metadata_json.get("condition") == "order_block"


def test_dedupe_prevents_repeated_order_block_alerts(slice74_db: sessionmaker[Session]) -> None:
    detection = SetupDetection(
        name="order_block",
        detected=True,
        direction="long",
        reason="Bullish order block: down candle before an impulsive up move.",
        metrics={"ob_low": 97.0, "ob_high": 98.5, "impulse": 4.5},
    )
    stub = detect_setup_candidates(
        symbol="BTCUSDT",
        timeframe="15m",
        bars=_flat_bars(),
        result=_analysis_result(detections=(detection,)),
    )
    with slice74_db() as session:
        settings = Settings(**_BASE)
        market_data = MarketDataService(MockMarketDataProvider())
        svc = MarketWatcherService(session, settings, market_data=market_data)
        body = MarketWatcherScanRequest(
            confirm=SCAN_CONFIRM_PHRASE,
            create_in_app_alerts_confirm=CREATE_IN_APP_ALERTS_CONFIRM_PHRASE,
            symbols=["BTCUSDT"],
            timeframes=["15m"],
            dry_run=False,
        )
        with patch(
            "app.services.market_watcher_scanner.detect_setup_candidates",
            return_value=stub,
        ):
            first = svc.scan(organization_id=ORG_ID, user_id=USER_ID, request=body)
            session.commit()
            second = svc.scan(organization_id=ORG_ID, user_id=USER_ID, request=body)
            session.commit()
        assert first.alerts_created == 1
        assert second.alerts_deduped >= 1
        assert second.alerts_created == 0


def test_dedupe_prevents_repeated_breakout_retest_alerts(slice74_db: sessionmaker[Session]) -> None:
    detection = SetupDetection(
        name="breakout_retest",
        detected=True,
        direction="short",
        reason="Broke support then retested it as resistance.",
        metrics={"level": 95.0, "close": 94.8},
    )
    bars = _flat_bars(price=94.8)
    stub = detect_setup_candidates(
        symbol="BTCUSDT",
        timeframe="15m",
        bars=bars,
        result=_analysis_result(detections=(detection,)),
    )
    with slice74_db() as session:
        settings = Settings(**_BASE)
        market_data = MarketDataService(MockMarketDataProvider())
        svc = MarketWatcherService(session, settings, market_data=market_data)
        body = MarketWatcherScanRequest(
            confirm=SCAN_CONFIRM_PHRASE,
            create_in_app_alerts_confirm=CREATE_IN_APP_ALERTS_CONFIRM_PHRASE,
            symbols=["BTCUSDT"],
            timeframes=["15m"],
            dry_run=False,
        )
        with patch(
            "app.services.market_watcher_scanner.detect_setup_candidates",
            return_value=stub,
        ):
            first = svc.scan(organization_id=ORG_ID, user_id=USER_ID, request=body)
            session.commit()
            second = svc.scan(organization_id=ORG_ID, user_id=USER_ID, request=body)
            session.commit()
        assert first.alerts_created == 1
        assert second.alerts_deduped >= 1
        assert second.alerts_created == 0


def test_detector_error_degrades_without_crashing_scan() -> None:
    bad = SetupDetection(
        name="order_block",
        detected=True,
        direction="long",
        reason="bad metrics",
        metrics={"ob_low": "not-a-number", "ob_high": 98.5},  # type: ignore[arg-type]
    )
    good = SetupDetection(
        name="breakout_retest",
        detected=True,
        direction="long",
        reason="Broke resistance then retested it as support.",
        metrics={"level": 105.0, "close": 105.2},
    )
    result = _analysis_result(detections=(bad, good))
    candidates = detect_setup_candidates(
        symbol="BTCUSDT",
        timeframe="15m",
        bars=_flat_bars(price=105.2),
        result=result,
    )
    assert len(candidates) == 1
    assert candidates[0].condition == "breakout_retest"
