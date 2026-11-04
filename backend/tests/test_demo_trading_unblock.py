"""Demo unblock: existing leverage under a ceiling, demo-only symbol-cap bypass, band, window."""

from __future__ import annotations

from datetime import UTC, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.core.config import Settings
from app.providers.exchange.governed_blofin import demo_order_margin_mode
from app.services.autonomous_trading import (
    configured_risk_refusal,
    symbol_book_amount,
    symbol_cap_exempt,
    symbol_key,
)
from app.services.execution_claim import ReservationIntent, evaluate_claim_predicate
from app.services.governed_blofin_demo import _plan_window, demo_max_leverage, stop_loss_band


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
    for low, high in (("0", "5"), ("5", "1"), ("1", "101"), ("1", "6"), ("0.5", "5"), ("NaN", "5")):
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
    assert demo_order_margin_mode("verified-demo-existing-leverage-cross") == "cross"


def test_plan_window_has_a_configurable_maximum() -> None:
    assert Settings(_env_file=None).governed_demo_plan_valid_max_seconds == 300
    assert _plan_window(SimpleNamespace(governed_demo_plan_valid_seconds=86400)) == timedelta(  # type: ignore[arg-type]
        seconds=300
    )
    capped = SimpleNamespace(
        governed_demo_plan_valid_seconds=500, governed_demo_plan_valid_max_seconds=120
    )
    assert _plan_window(capped) == timedelta(seconds=120)  # type: ignore[arg-type]


def test_leverage_ceiling_defaults_to_20x_and_is_configurable() -> None:
    assert demo_max_leverage(Settings(_env_file=None)) == Decimal("20")
    assert demo_max_leverage(SimpleNamespace(governed_demo_max_leverage=Decimal("25"))) == 20  # type: ignore[arg-type]
    assert demo_max_leverage(SimpleNamespace(governed_demo_max_leverage=Decimal("5"))) == Decimal(  # type: ignore[arg-type]
        "5"
    )
    for bad in ("NaN", "0", "-3"):
        assert demo_max_leverage(SimpleNamespace(governed_demo_max_leverage=Decimal(bad))) == 1  # type: ignore[arg-type]
    assert demo_max_leverage(SimpleNamespace(governed_demo_max_leverage=Decimal("500"))) == 20  # type: ignore[arg-type]


def test_symbol_cap_exempt_matches_written_venue_values() -> None:
    import inspect

    import app.services.governed_blofin_demo as demo
    import app.services.internal_paper_plan_terms as paper
    from app.services.safety_epoch import BLOFIN_DEMO_VENUE, INTERNAL_PAPER_VENUE

    # The exemption keys are exactly the venue strings the plan builders write.
    assert '"execution_venue": "BLOFIN_DEMO"' in inspect.getsource(demo)
    assert '"execution_venue": "PAPER_INTERNAL"' in inspect.getsource(paper)
    assert (BLOFIN_DEMO_VENUE, INTERNAL_PAPER_VENUE) == ("BLOFIN_DEMO", "PAPER_INTERNAL")
    assert symbol_cap_exempt(BLOFIN_DEMO_VENUE)
    assert symbol_cap_exempt(INTERNAL_PAPER_VENUE)
    for venue in (
        None,
        "",
        "PAPER",
        "INTERNAL_PAPER",
        "blofin_demo",
        " BLOFIN_DEMO",
        "BLOFIN",
        "BLOFIN_LIVE",
        "BINANCE",
        "LIVE",
        1,
    ):
        assert not symbol_cap_exempt(venue)  # type: ignore[arg-type]


def test_symbol_cap_kept_in_shared_code_and_bypassed_only_on_demo() -> None:
    """Mutation guard: dropping the venue check or the cap flips these asserts."""

    from tests.test_autonomous_trading_modes import _limits

    tight = {"max_symbol_notional": Decimal("0")}
    assert configured_risk_refusal(_limits(**tight)) == "max_position_size"
    live = _limits(**tight, execution_venue="BLOFIN_LIVE")
    assert configured_risk_refusal(live) == "max_position_size"
    demo = _limits(**tight, execution_venue="BLOFIN_DEMO")
    assert configured_risk_refusal(demo) is None
    over = _limits(**tight, execution_venue="BLOFIN_DEMO", pending_notional=Decimal("1001"))
    assert configured_risk_refusal(over) == "max_exposure"


def _accounting(**updates: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "daily_locked": False,
        "max_notional": Decimal("1000"),
        "reserved_notional": Decimal("0"),
        "actual_notional": Decimal("0"),
        "max_daily_loss": Decimal("100"),
        "reserved_daily_loss": Decimal("0"),
        "actual_daily_loss": Decimal("0"),
        "max_trade_slots": 5,
        "reserved_trade_slots": 0,
        "actual_trade_count": 0,
        "max_symbol_notional": Decimal("10"),
        "symbol_reserved": {"BTC-USDT": "5"},
        "symbol_actual": {},
    }
    values.update(updates)
    return SimpleNamespace(**values)


def test_claim_predicate_keeps_symbol_cap_off_demo_venue() -> None:
    intent = SimpleNamespace(
        pending_notional=Decimal("8"), daily_loss=Decimal("1"), trade_slots=1, instrument="BTCUSDT"
    )
    kwargs = {"blocking_epoch": False, "kill_active": False}
    acct = _accounting()
    # BTC-USDT reserved 5 counts against BTCUSDT (normalized key): 8 > 10 - 5.
    assert (
        evaluate_claim_predicate(accounting=acct, intent=intent, **kwargs)  # type: ignore[arg-type]
        == "insufficient_symbol_exposure"
    )
    assert (
        evaluate_claim_predicate(
            accounting=acct,
            intent=intent,
            execution_venue="BLOFIN_LIVE",
            **kwargs,  # type: ignore[arg-type]
        )
        == "insufficient_symbol_exposure"
    )
    assert (
        evaluate_claim_predicate(
            accounting=acct,
            intent=intent,
            execution_venue="BLOFIN_DEMO",
            **kwargs,  # type: ignore[arg-type]
        )
        is None
    )
    assert ReservationIntent is not None


def test_symbol_keys_normalize_btcusdt_spellings() -> None:
    assert symbol_key("BTC-USDT") == symbol_key("BTCUSDT") == symbol_key("btc/usdt")
    assert symbol_book_amount({"BTC-USDT": "3", "BTCUSDT": "2", "ETHUSDT": "9"}, "BTCUSDT") == 5


def _band_plan(monkeypatch, *, leverage: str, stop: str, worst: str) -> object:
    """Drive GovernedBloFinDemoLoop._plan_terms (unprofiled path) to the band check."""

    from datetime import datetime
    from uuid import uuid4

    from app.services.governed_blofin_demo import GovernedBloFinDemoLoop

    now = datetime(2026, 11, 4, 8, 0, tzinfo=UTC)
    side = SimpleNamespace(value="BUY")

    def _dump(**_kwargs: object) -> dict[str, object]:
        raise AssertionError("plan passed the stop-loss band")

    terms = SimpleNamespace(
        side=side,
        account_id=uuid4(),
        risk_and_exits=SimpleNamespace(
            stop=SimpleNamespace(value=Decimal(stop)),
            targets=[SimpleNamespace(price=SimpleNamespace(value=Decimal("110")))],
        ),
        model_dump=_dump,
    )
    monkeypatch.setattr("app.services.governed_blofin_demo._plan_terms", lambda **_k: terms)
    snapshot = SimpleNamespace(
        equity=Decimal("10000"),
        available=Decimal("10000"),
        book=SimpleNamespace(side=side, worst_price=lambda *_a, **_k: Decimal(worst)),
        observed_at=now,
        tick=Decimal("0.1"),
        price=Decimal("100"),
        account_leverage=Decimal(leverage),
        margin_mode="cross",
        multiplier=Decimal("1"),
        maximum=Decimal("1000000"),
        lot=Decimal("1"),
        minimum=Decimal("1"),
        instrument="BTC-USDT",
    )
    loop = GovernedBloFinDemoLoop(None, Settings(_env_file=None), SimpleNamespace())  # type: ignore[arg-type]
    loop._remaining_exposure = Decimal("1000000")  # ledger headroom that does not bind
    monkeypatch.setattr(loop, "_require_snapshot", lambda: snapshot)
    return loop._plan_terms(
        candidate=SimpleNamespace(),  # type: ignore[arg-type]
        assembled=SimpleNamespace(),  # type: ignore[arg-type]
        policy=SimpleNamespace(),  # type: ignore[arg-type]
        account_id=uuid4(),
        equity=Decimal("10000"),
        now=now,
        eligibility_valid_until=now,
        eligibility_id=uuid4(),
    )


def test_plan_refuses_loss_below_band_floor(monkeypatch) -> None:
    # 1x leverage caps notional at 9500; a 0.5% stop then risks ~66 < 1% floor (100).
    assert _band_plan(monkeypatch, leverage="1", stop="99.5", worst="100") == (
        "stop_loss_below_band"
    )


def test_plan_refuses_loss_above_band_ceiling(monkeypatch) -> None:
    # Thin book: 116 contracts fill up to 104, so stop loss ~580+ > 5% ceiling (500).
    assert _band_plan(monkeypatch, leverage="10", stop="99", worst="104") == (
        "stop_loss_above_band"
    )
