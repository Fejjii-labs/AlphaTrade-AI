"""C1a: trader-facing display names for setups, candidates and journal trades."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db.strategy_brain import BrainSetupRow
from app.main import create_app
from app.schemas.common import TradeDirection
from app.schemas.journal_trades import JournalTradeRead
from app.services.display_names import display_name, family_label
from app.strategy_brain.service import setup_view

UUID_OR_HASH = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}|[0-9a-f]{32,}")
AT = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def test_display_name_joins_parts_and_skips_missing_ones() -> None:
    assert display_name("Nested", "btcusdt", TradeDirection.LONG, "15m") == (
        "Nested · BTCUSDT · Long · 15m"
    )
    assert display_name(None, "ETHUSDT", "short", None) == "ETHUSDT · Short"
    assert display_name("  ", "", None, "") == ""


@pytest.mark.parametrize(
    ("family", "label"),
    [("nested_continuation", "Nested"), ("sfp", "SFP"), ("liquidity_sweep", "Liquidity sweep")],
)
def test_family_labels_are_plain_words(family: str, label: str) -> None:
    assert family_label(family) == label


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"timeframe": "15m", "direction": "long"}, "Nested · BTCUSDT · Long · 15m"),
        ({"family": "sfp", "timeframe": "1h", "direction": "short"}, "SFP · BTCUSDT · Short · 1h"),
        ({"timeframe": "4h"}, "Nested · BTCUSDT · 4h"),
    ],
)
def test_brain_setup_view_carries_display_name(payload: dict, expected: str) -> None:
    row = BrainSetupRow(
        id=uuid.UUID(int=710),
        organization_id=uuid.UUID(int=1),
        strategy_id=uuid.UUID(int=700),
        strategy_version_id=uuid.UUID(int=701),
        symbol="BTCUSDT",
        state="FORMING",
        observed_at=AT,
        expires_at=AT + timedelta(hours=1),
        payload={
            "evidence_close_at": AT.isoformat(),
            "required_evidence_max_age_bars": 2,
            "required_evidence": "AVAILABLE",
            **payload,
        },
    )
    # No linked journal trade, so the session is never touched.
    view = setup_view(None, row, now=AT)  # type: ignore[arg-type]
    assert view["display_name"] == expected
    assert not UUID_OR_HASH.search(view["display_name"])


def _trade(**overrides: object) -> JournalTradeRead:
    return JournalTradeRead.model_validate(
        {
            "id": uuid.uuid4(),
            "organization_id": uuid.uuid4(),
            "user_id": uuid.uuid4(),
            "source": "manual",
            "status": "open",
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "market_regime": "unknown",
            "direction": "long",
            "strategy_version_id": uuid.uuid4(),
            "created_at": AT,
            "updated_at": AT,
            **overrides,
        }
    )


def test_journal_trade_read_serializes_display_name() -> None:
    named = _trade(strategy_label="Nested").model_dump(mode="json")
    assert named["display_name"] == "Nested · BTCUSDT · Long · 15m"
    unnamed = _trade(direction="short", timeframe="1h").model_dump(mode="json")
    assert unnamed["display_name"] == "BTCUSDT · Short · 1h"
    assert not UUID_OR_HASH.search(named["display_name"])


def test_display_name_is_in_the_published_contract() -> None:
    schemas = create_app().openapi()["components"]["schemas"]
    trade = next(
        schema
        for name, schema in schemas.items()
        if name.startswith("JournalTradeRead") and "display_name" in schema["properties"]
    )
    assert "display_name" in trade["required"]
    assert "display_name" in schemas["CanonicalCandidateRead"]["properties"]
