"""Price PnL aggregates stay unknown unless one verified currency is confirmed."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.experiments.blofin_source import rounded
from app.experiments.outcomes import price_pnl_fields


def test_price_pnl_fields_require_one_verified_currency() -> None:
    usdt = SimpleNamespace(currency="USDT", price_pnl_excluding_fees_and_funding="1.5")
    usd = SimpleNamespace(currency="USD", price_pnl_excluding_fees_and_funding="2")
    loss = SimpleNamespace(currency="USDT", price_pnl_excluding_fees_and_funding="-0.5")
    missing = SimpleNamespace(currency=None, price_pnl_excluding_fees_and_funding="1")

    assert price_pnl_fields([usdt, usd], minimum=1, sample_count=2) == {}
    assert price_pnl_fields([usdt], minimum=1, sample_count=2) == {}
    assert price_pnl_fields([missing], minimum=1, sample_count=1) == {}
    assert price_pnl_fields([], minimum=1, sample_count=0) == {}

    fields = price_pnl_fields([usdt, loss], minimum=2, sample_count=2)
    assert fields["price_pnl_currency"] == "USDT"
    assert fields["price_pnl_sample_count"] == 2
    assert fields["price_pnl_methodology"] == "verified-linear-fill-prices/v1"
    assert fields["price_pnl_excluding_fees_and_funding"] == rounded(Decimal("1"))
    assert fields["price_pnl_win_rate"] == rounded(Decimal("0.5"))
