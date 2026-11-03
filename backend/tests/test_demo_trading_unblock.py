"""Demo unblock: existing leverage, no symbol cap, stop-loss band, plan window."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.core.config import Settings
from app.providers.exchange.governed_blofin import demo_order_margin_mode
from app.services.autonomous_trading import configured_risk_refusal
from app.services.governed_blofin_demo import _plan_window, stop_loss_band


def test_stop_loss_band_defaults_are_one_to_five_percent() -> None:
    settings = Settings(_env_file=None)
    assert settings.governed_demo_stop_loss_min_percent == Decimal("1")
    assert settings.governed_demo_stop_loss_max_percent == Decimal("5")
    assert stop_loss_band(settings, Decimal("10000")) == (Decimal("100"), Decimal("500"))


def test_stop_loss_band_reads_configured_percents() -> None:
    custom = SimpleNamespace(
        governed_demo_stop_loss_min_percent=Decimal("2"),
        governed_demo_stop_loss_max_percent=Decimal("3"),
    )
    assert stop_loss_band(custom, Decimal("5000")) == (Decimal("100"), Decimal("150"))  # type: ignore[arg-type]


def test_stop_loss_band_refuses_unusable_settings() -> None:
    for low, high in (("0", "5"), ("5", "1"), ("1", "101"), ("NaN", "5")):
        bad = SimpleNamespace(
            governed_demo_stop_loss_min_percent=Decimal(low),
            governed_demo_stop_loss_max_percent=Decimal(high),
        )
        assert stop_loss_band(bad, Decimal("10000")) == (None, None)  # type: ignore[arg-type]
    assert stop_loss_band(Settings(_env_file=None), Decimal("0")) == (None, None)


def test_plan_window_is_configurable_with_a_floor() -> None:
    assert _plan_window(Settings(_env_file=None)) == timedelta(seconds=60)
    assert _plan_window(SimpleNamespace(governed_demo_plan_valid_seconds=120)) == timedelta(  # type: ignore[arg-type]
        seconds=120
    )
    assert _plan_window(SimpleNamespace(governed_demo_plan_valid_seconds=1)) == timedelta(  # type: ignore[arg-type]
        seconds=10
    )


def test_order_margin_mode_follows_the_account_mode_read_at_plan_time() -> None:
    assert demo_order_margin_mode("verified-demo-existing-1x") == "cross"
    assert demo_order_margin_mode("verified-demo-existing-leverage-isolated") == "isolated"
    assert demo_order_margin_mode("verified-demo-read-leverage") == "isolated"


def test_symbol_cap_no_longer_refuses_but_exposure_and_daily_loss_do() -> None:
    from tests.test_autonomous_trading_modes import _limits

    assert configured_risk_refusal(_limits(max_symbol_notional=Decimal("0"))) is None
    assert configured_risk_refusal(_limits(pending_notional=Decimal("1001"))) == "max_exposure"
    assert configured_risk_refusal(_limits(proposed_loss=Decimal("101"))) == "daily_loss_limit"
    assert configured_risk_refusal(_limits(daily_locked=True)) == "daily_loss_limit"
