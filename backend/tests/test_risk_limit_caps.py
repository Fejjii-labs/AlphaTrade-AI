"""Risk-cap consistency and symbol-key normalization for demo auto-approval.

Cap values are arbitrary: every limit is user-adjustable, so the predicate
must hold for any numbers, not today's staging configuration.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.autonomous_trading import configured_risk_refusal
from app.services.governed_blofin_demo import _demo_risk_snapshot


def _row(**updates: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "max_notional": Decimal("1000"),
        "max_daily_loss": Decimal("100"),
        "max_symbol_notional": Decimal("250"),
        "max_trade_slots": 5,
        "reserved_notional": Decimal("0"),
        "reserved_daily_loss": Decimal("0"),
        "reserved_trade_slots": 0,
        "actual_notional": Decimal("0"),
        "actual_daily_loss": Decimal("0"),
        "actual_trade_count": 0,
        "symbol_reserved": {},
        "symbol_actual": {},
        "daily_locked": False,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _refusal(row: object, *, pending: str = "100", instrument: str = "BTC-USDT") -> str | None:
    session = SimpleNamespace(scalar=lambda _statement: row)
    target = SimpleNamespace(organization_id=uuid4(), user_id=uuid4())
    plan = SimpleNamespace(account_id=uuid4())
    intent = SimpleNamespace(
        pending_notional=Decimal(pending),
        daily_loss=Decimal("0.01"),
        instrument=instrument,
        trade_slots=1,
    )
    try:
        snapshot = _demo_risk_snapshot(session, target, plan, intent)  # type: ignore[arg-type]
    except Exception:
        # Mirrors GovernedBloFinDemoLoop: an unreadable snapshot is unverified.
        return "risk_limits_unverified"
    return configured_risk_refusal(snapshot)


@pytest.mark.parametrize(
    ("total", "daily"),
    [("1000", "100"), ("10000", "500"), ("37.5", "1.25"), ("123456.78", "999")],
)
def test_symbol_cap_equal_to_total_cap_passes(total: str, daily: str) -> None:
    row = _row(
        max_notional=Decimal(total),
        max_symbol_notional=Decimal(total),
        max_daily_loss=Decimal(daily),
    )
    assert _refusal(row, pending=str(Decimal(total) / 4)) is None


@pytest.mark.parametrize(("total", "symbol"), [("1000", "1000.01"), ("50", "75"), ("8000", "9000")])
def test_symbol_cap_above_total_is_inconsistent(total: str, symbol: str) -> None:
    row = _row(max_notional=Decimal(total), max_symbol_notional=Decimal(symbol))
    assert _refusal(row, pending="1") == "risk_limits_inconsistent"


def test_daily_loss_at_or_above_total_is_inconsistent() -> None:
    row = _row(
        max_notional=Decimal("300"),
        max_daily_loss=Decimal("300"),
        max_symbol_notional=Decimal("100"),
    )
    assert _refusal(row, pending="1") == "risk_limits_inconsistent"


def test_missing_inputs_stay_unverified() -> None:
    session = SimpleNamespace(scalar=lambda _statement: _row())
    intent = SimpleNamespace(
        pending_notional=Decimal("1"), daily_loss=Decimal("1"), instrument="BTCUSDT", trade_slots=1
    )
    missing_account = _demo_risk_snapshot(
        session,  # type: ignore[arg-type]
        SimpleNamespace(organization_id=uuid4(), user_id=uuid4()),  # type: ignore[arg-type]
        SimpleNamespace(account_id=None),
        intent,
    )
    assert configured_risk_refusal(missing_account) == "risk_limits_unverified"
    assert _refusal(_row(max_symbol_notional=Decimal("NaN"))) == "risk_limits_unverified"


@pytest.mark.parametrize("stored_key", ["BTCUSDT", "BTC-USDT", "btc-usdt"])
@pytest.mark.parametrize("instrument", ["BTCUSDT", "BTC-USDT"])
@pytest.mark.parametrize("book", ["symbol_reserved", "symbol_actual"])
def test_exposure_counted_under_either_key_form(
    stored_key: str, instrument: str, book: str
) -> None:
    row = _row(
        max_notional=Decimal("2000"),
        max_symbol_notional=Decimal("500"),
        **{book: {stored_key: "450"}},
    )
    assert _refusal(row, pending="100", instrument=instrument) == "max_position_size"
    row = _row(
        max_notional=Decimal("2000"),
        max_symbol_notional=Decimal("500"),
        **{book: {stored_key: "350"}},
    )
    assert _refusal(row, pending="100", instrument=instrument) is None


def test_exposure_split_across_key_forms_is_summed() -> None:
    row = _row(
        max_notional=Decimal("2000"),
        max_symbol_notional=Decimal("500"),
        symbol_reserved={"BTCUSDT": "200"},
        symbol_actual={"BTC-USDT": "250"},
    )
    assert _refusal(row, pending="100") == "max_position_size"


# --- Griller C1: claim predicate reads exposure under either key form ---


def _accounting(book_key: str, amount: str) -> SimpleNamespace:
    return SimpleNamespace(
        daily_locked=False,
        max_notional=Decimal("5000"),
        reserved_notional=Decimal("0"),
        actual_notional=Decimal("0"),
        max_daily_loss=Decimal("200"),
        reserved_daily_loss=Decimal("0"),
        actual_daily_loss=Decimal("0"),
        max_trade_slots=9,
        reserved_trade_slots=0,
        actual_trade_count=0,
        max_symbol_notional=Decimal("600"),
        symbol_reserved={book_key: amount},
        symbol_actual={},
    )


@pytest.mark.parametrize(("booked", "claimed"), [("BTC-USDT", "BTCUSDT"), ("BTCUSDT", "BTC-USDT")])
def test_claim_predicate_counts_exposure_across_key_forms(booked: str, claimed: str) -> None:
    from app.services.execution_claim import evaluate_claim_predicate

    intent = SimpleNamespace(
        pending_notional=Decimal("100"), daily_loss=Decimal("1"), trade_slots=1, instrument=claimed
    )
    full = _accounting(booked, "550")
    assert (
        evaluate_claim_predicate(
            blocking_epoch=False, kill_active=False, accounting=full, intent=intent
        )
        == "insufficient_symbol_exposure"
    )
    room = _accounting(booked, "400")
    assert (
        evaluate_claim_predicate(
            blocking_epoch=False, kill_active=False, accounting=room, intent=intent
        )
        is None
    )


# --- Griller C2: defaults path runs the same consistency check ---


def test_defaults_path_flags_daily_loss_at_or_above_balance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.risk import settings_service

    class _Svc:
        def __init__(self, *_a: object) -> None:
            pass

        def get(self, **_k: object) -> SimpleNamespace:
            return SimpleNamespace(
                daily_loss_limit=Decimal("750"),
                default_account_balance=Decimal("700"),
                max_trades_per_day=4,
            )

    monkeypatch.setattr(settings_service, "RiskSettingsService", _Svc)
    assert _refusal(None, pending="1") == "risk_limits_inconsistent"


def test_defaults_path_consistent_values_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.risk import settings_service

    class _Svc:
        def __init__(self, *_a: object) -> None:
            pass

        def get(self, **_k: object) -> SimpleNamespace:
            return SimpleNamespace(
                daily_loss_limit=Decimal("33"),
                default_account_balance=Decimal("4321"),
                max_trades_per_day=4,
            )

    monkeypatch.setattr(settings_service, "RiskSettingsService", _Svc)
    assert _refusal(None, pending="10") is None


# --- Griller C3: venue suffixes are rejected, never a separate bucket ---


@pytest.mark.parametrize("bad", ["BTC-USDT-SWAP", "BTCUSDT-PERP", "btcusdtswap", ""])
def test_symbol_key_rejects_non_canonical(bad: str) -> None:
    from app.services.autonomous_trading import symbol_key

    with pytest.raises(ValueError):
        symbol_key(bad)


def test_suffixed_book_key_refuses_instead_of_splitting() -> None:
    row = _row(symbol_reserved={"BTC-USDT-SWAP": "1"})
    assert _refusal(row, pending="1") == "risk_limits_unverified"
    assert _refusal(_row(), pending="1", instrument="BTCUSDT-PERP") == "risk_limits_unverified"
