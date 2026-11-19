"""Demo leverage is the owner's choice from 1x to 10x; the risk profile never caps it.

Live paths keep the profile and hard ceilings unchanged.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.routes.risk_policy import DemoBotRiskSettings
from app.core.config import Settings
from app.db.models import BotRiskSettings
from app.providers.exchange import governed_blofin
from app.services.demo_leverage_config import (
    DEMO_LEVERAGE_CEILING,
    DemoLeverageConfig,
    DemoLeverageValidationError,
    validate_demo_leverage,
)
from app.services.governed_blofin_demo import DEMO_LEVERAGE_HARD_CAP, demo_max_leverage
from app.services.risk_policy.grader import GradeDecision
from app.services.risk_policy.plan_sizing import ActiveProfile, size_for_profile
from app.services.risk_policy.profile import Grade, PolicyKind, sofien_aggressive_profile
from tests.support.postgres_persistence import requires_postgres

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "src/app/db/migrations/versions/a31riskmodelv2001_demo_risk_settings.py"
)


def _bot_settings(leverage: object) -> DemoBotRiskSettings:
    return DemoBotRiskSettings.model_validate(
        {
            "risk_per_trade_pct": "1",
            "daily_stop_pct": "5",
            "leverage": leverage,
            "margin_mode": "isolated",
        }
    )


def test_every_demo_ceiling_is_ten() -> None:
    assert DEMO_LEVERAGE_CEILING == 10
    assert DEMO_LEVERAGE_HARD_CAP == 10
    assert governed_blofin._DEMO_LEVERAGE_CEILING == 10
    assert Settings.model_fields["governed_demo_max_leverage"].default == Decimal("10")
    assert demo_max_leverage(SimpleNamespace(governed_demo_max_leverage=Decimal("20"))) == 10  # type: ignore[arg-type]


@pytest.mark.parametrize("leverage", range(1, 11))
def test_one_to_ten_is_valid_for_settings_and_bot_row(leverage: int) -> None:
    assert validate_demo_leverage(leverage) == leverage
    assert _bot_settings(leverage).leverage == leverage


@pytest.mark.parametrize("leverage", [0, 11, 20])
def test_outside_one_to_ten_is_rejected_clearly(leverage: int) -> None:
    with pytest.raises(DemoLeverageValidationError) as exc:
        validate_demo_leverage(leverage)
    assert exc.value.code in {"demo_leverage_below_one", "demo_leverage_exceeds_ceiling"}
    assert ("1x" if leverage < 1 else "10x") in str(exc.value)
    with pytest.raises(ValidationError) as api:
        _bot_settings(leverage)
    assert api.value.errors()[0]["loc"] == ("leverage",)


def test_bot_risk_row_check_is_one_to_ten() -> None:
    checks = {
        str(constraint.sqltext)
        for constraint in BotRiskSettings.__table__.constraints
        if str(getattr(constraint, "name", "")).endswith("ck_bot_risk_leverage")
    }
    assert checks == {"leverage BETWEEN 1 AND 10"}
    assert '"leverage BETWEEN 1 AND 10"' in MIGRATION.read_text()


def _size(profile, *, channel: str, leverage: Decimal):  # type: ignore[no-untyped-def]
    return size_for_profile(
        active=ActiveProfile(profile=profile, version_id=uuid4()),
        grade=GradeDecision(
            Grade.A_PLUS, "deterministic-grade/v1", Decimal("1"), Decimal("1"), None
        ),
        equity=Decimal("10000"),
        entry=Decimal("100"),
        stop=Decimal("98"),
        target=Decimal("104"),
        direction="long",
        atr=Decimal("1"),
        lot_step=Decimal("0.00000001"),
        min_quantity=Decimal("0.00000001"),
        open_risk_percent=Decimal("0"),
        open_notional=Decimal("0"),
        open_positions=0,
        remaining_daily_budget_percent=Decimal("6"),
        breaker_refusing=False,
        execution_channel=channel,
        leverage=leverage,
    )


def test_profile_max_leverage_does_not_cap_demo_sizing() -> None:
    profile = replace(
        sofien_aggressive_profile(PolicyKind.DEMO),
        max_leverage=Decimal("3"),
        default_leverage=Decimal("2"),
    )
    sized = _size(profile, channel="demo", leverage=Decimal("8"))
    assert not isinstance(sized, str), sized
    assert sized.leverage == Decimal("8")
    # Not raised past the demo ceiling, and other channels keep the profile cap.
    assert _size(profile, channel="demo", leverage=Decimal("11")) == "leverage_exceeds_ceiling"
    assert _size(profile, channel="internal_paper", leverage=Decimal("8")) == (
        "leverage_exceeds_ceiling"
    )


def test_demo_loop_has_no_profile_max_leverage_blocker() -> None:
    from app.services import governed_blofin_demo

    source = Path(governed_blofin_demo.__file__).read_text()
    assert "demo_leverage_exceeds_profile_max" not in source


@requires_postgres
def test_configured_leverage_above_profile_max_is_not_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker cycle: a demo profile capped at 3x does not refuse a configured 8x."""

    from tests.test_demo_configured_leverage import LeverageVenue, _price, _run_worker

    profile = replace(
        sofien_aggressive_profile(PolicyKind.DEMO),
        max_leverage=Decimal("3"),
        default_leverage=Decimal("2"),
    )
    monkeypatch.setattr(
        "app.services.automated_paper_loop._load_active_profile",
        lambda *_a: ActiveProfile(profile=profile, version_id=uuid4()),
    )
    venue = LeverageVenue(_price())
    _factory, scan = _run_worker(monkeypatch, venue, DemoLeverageConfig(8, "isolated", "account"))
    assert scan.paper_loop_reason not in {
        "demo_leverage_exceeds_profile_max",
        "leverage_exceeds_ceiling",
    }
