"""Nested on the BloFin demo: only the configured timeframes and stages trade.

From the Nov 2026 one-year history study: N1 lost on every timeframe and 15m lost
overall; 30m/1h from stage N2 were positive.
"""

from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.schemas.common import Timeframe, TradeDirection
from app.schemas.nested_continuation import NestedContinuationSpec
from app.services.governed_blofin_demo import GovernedBloFinDemoLoop


def _loop(**settings: object) -> GovernedBloFinDemoLoop:
    return GovernedBloFinDemoLoop(None, Settings(_env_file=None, **settings), SimpleNamespace())


def _policy(timeframe: Timeframe) -> SimpleNamespace:
    return SimpleNamespace(
        authored_spec=NestedContinuationSpec(
            symbol="BTCUSDT", trigger_timeframe=timeframe, direction=TradeDirection.LONG
        )
    )


def _confirmed(monkeypatch: pytest.MonkeyPatch, stage: str, state: str = "CONFIRMED") -> object:
    event = SimpleNamespace(stage=stage, state=SimpleNamespace(value=state))
    monkeypatch.setattr("app.strategy_brain.detector.detect_nested", lambda *_a, **_k: (event,))
    return SimpleNamespace(bundle=SimpleNamespace(bars_15m=()), evaluated_at=None)


def test_defaults_are_30m_1h_from_stage_n2() -> None:
    settings = Settings(_env_file=None)
    assert settings.governed_demo_nested_timeframes == ["30m", "1h"]
    assert settings.governed_demo_nested_min_stage == "N2"
    parsed = Settings(_env_file=None, governed_demo_nested_timeframes="1H, 30m")
    assert parsed.governed_demo_nested_timeframes == ["1h", "30m"]


def test_15m_nested_is_refused_before_any_detection() -> None:
    loop = _loop()
    assert loop._nested_refusal(_policy(Timeframe.M15)) == "nested_timeframe_disabled_on_demo"
    assert loop._nested_refusal(_policy(Timeframe.M30)) is None
    assert loop._nested_refusal(_policy(Timeframe.H1)) is None


@pytest.mark.parametrize(
    "stage,refused",
    [("N1", True), ("N2", False), ("N3", False), ("N4_PLUS", False)],
)
def test_stage_below_the_minimum_is_refused(monkeypatch, stage, refused) -> None:
    assembled = _confirmed(monkeypatch, stage)
    result = _loop()._nested_refusal(_policy(Timeframe.H1), assembled)
    assert result == ("nested_stage_below_minimum_on_demo" if refused else None)


def test_settings_can_reopen_15m_and_n1(monkeypatch) -> None:
    loop = _loop(governed_demo_nested_timeframes=["15m"], governed_demo_nested_min_stage="N1")
    assert loop._nested_refusal(_policy(Timeframe.M15), _confirmed(monkeypatch, "N1")) is None


def test_other_families_and_unconfirmed_setups_are_left_to_the_plan_path(monkeypatch) -> None:
    loop = _loop()
    assert loop._nested_refusal(SimpleNamespace(authored_spec=None)) is None
    assembled = _confirmed(monkeypatch, "N1", state="FORMING")
    assert loop._nested_refusal(_policy(Timeframe.H1), assembled) is None
