"""Every strategy (current and future) renders the same short alert layout."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.candidate_alerts.content import (
    format_candidate_alert_text,
    render_strategy_alert_text,
    strategy_display_name,
)
from app.candidate_alerts.sfp import SfpAlertSummary
from app.schemas.telegram_policy import NotificationEventType

AT = datetime(2026, 11, 3, 18, 45, 37, tzinfo=UTC)
LABELS = ["Symbol", "Strategy", "Direction", "Entry", "Stop", "Target", "Timeframe"]
DROPPED = ("Strategy version", "Setup ID", "Risk state", "Risk reasons", "Reason", "Candidate:")


def _labels(text: str) -> list[str]:
    return [line.split(":", 1)[0] for line in text.splitlines()]


def _assert_layout(text: str, *, stage_label: str | None) -> None:
    expected = [*LABELS, *([stage_label] if stage_label else []), "Time"]
    # Plain-reason and key-level lines are optional extras; the core order holds.
    labels = [label for label in _labels(text) if label in expected]
    assert labels[: len(expected)] == expected
    lines = text.splitlines()
    time_index = next(i for i, line in enumerate(lines) if line.startswith("Time: "))
    assert lines[time_index + 1] == "PAPER alert. Not an order."
    for dropped in DROPPED:
        assert dropped not in text


def test_made_up_future_strategy_gets_identical_layout() -> None:
    kwargs = {
        "symbol": "ZECUSDT",
        "direction": "short",
        "entry": Decimal("41.5"),
        "stop": Decimal("43"),
        "target": Decimal("38"),
        "timeframe": "1h",
        "at": AT,
    }
    future = render_strategy_alert_text(strategy="volatility_breakout_v2", **kwargs)
    known = render_strategy_alert_text(strategy="sfp", **kwargs)
    _assert_layout(future, stage_label=None)
    assert "Strategy: Volatility Breakout V2" in future
    assert _labels(future) == _labels(known)
    assert future.replace("Volatility Breakout V2", "X") == known.replace(
        "Swing Failure Pattern", "X"
    )
    assert "Time: 19:45 CET" in future


@pytest.mark.parametrize(
    ("key", "name"),
    [
        ("nested", "Nested Continuation"),
        ("SFP", "Swing Failure Pattern"),
        ("mean-reversion", "Mean Reversion"),
        (f"compiled-setup:{uuid4()}", "Compiled setup"),
        ("Already Readable Name", "Already Readable Name"),
    ],
)
def test_display_name_registry_and_fallback(key: str, name: str) -> None:
    assert strategy_display_name(key) == name


@pytest.mark.parametrize(
    "event_type", [NotificationEventType.SFP_CONFIRMED, NotificationEventType.SFP_LEVEL_APPROACH]
)
def test_sfp_uses_shared_layout(event_type) -> None:
    version, setup = uuid4(), uuid4()
    text = SfpAlertSummary(
        organization_id=uuid4(),
        strategy_version_id=version,
        setup_id=setup,
        event_type=event_type,
        occurred_at=AT,
        evidence_at=AT,
        symbol="TAOUSDT",
        venue="binance",
        timeframe="1h",
        direction="long",
        level_type="swing_low",
        structural_level="300",
        sweep_extreme="295",
        reclaim_state="reclaimed",
        quality_coverage=(),
        missing_evidence=(),
        risk_state="blocked",
        risk_reasons=("long reason text",),
    ).text()
    _assert_layout(text, stage_label="Stage")
    assert "Strategy: Swing Failure Pattern" in text
    assert str(version) not in text and str(setup) not in text
    assert "long reason text" not in text and "blocked" not in text


def test_generic_candidate_uses_shared_layout() -> None:
    candidate_id = uuid4()
    content = SimpleNamespace(
        nested=None,
        instrument="BTCUSDT",
        direction=SimpleNamespace(value="short"),
        setup_name=f"compiled-setup:{uuid4()}",
        trigger_context=SimpleNamespace(timeframe=SimpleNamespace(value="15m"), interval_end=AT),
        candidate_id=candidate_id,
    )
    text = format_candidate_alert_text(content)
    _assert_layout(text, stage_label=None)
    assert "Strategy: Compiled setup" in text
    assert str(candidate_id) not in text
    assert "Entry: unavailable" in text


def test_sfp_invalidated_reads_plainly_without_ids_or_iso() -> None:
    import re

    from app.schemas.telegram_policy import NotificationSeverity, TelegramNotificationPolicyV2

    summary = SfpAlertSummary(
        organization_id=uuid4(),
        strategy_version_id=uuid4(),
        setup_id=uuid4(),
        event_type=NotificationEventType.SFP_INVALIDATED,
        occurred_at=AT,
        evidence_at=AT,
        symbol="ZECUSDT",
        venue="binance",
        timeframe="15m",
        direction="short",
        level_type="swing_high",
        structural_level="1294.00",
        sweep_extreme="1301.2",
        reclaim_state="invalidated",
        quality_coverage=(),
        missing_evidence=("cvd:UNSUPPORTED",),
        risk_state="not_evaluated",
    )
    text = summary.text()
    print(text)
    assert "Short cancelled: price closed back above 1294.00" in text
    assert "Level: swing high 1294.00" in text
    assert "Time: 19:45 CET" in text
    assert "Entry:" not in text
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text)
    for raw in ("T18:45", "UNSUPPORTED", "not_evaluated", "Setup ID", "Risk reasons"):
        assert raw not in text
    from app.services.notifications.telegram_policy import TERMINAL_SETUP_EVENTS

    assert NotificationSeverity  # imported for parity with the policy schema
    assert TelegramNotificationPolicyV2().event_types is None
    assert NotificationEventType.SFP_INVALIDATED in TERMINAL_SETUP_EVENTS
