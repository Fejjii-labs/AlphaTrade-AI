"""Partial-exit flags (Griller condition 8): default off, fail closed, never live."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import ExchangeMode, Settings
from app.services.partial_exits.flags import (
    BLOFIN_DEMO_VENUE,
    PAPER_INTERNAL_VENUE,
    partial_exits_enabled,
)


def _settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_both_venue_flags_default_off() -> None:
    settings = _settings()
    assert settings.paper_partial_exits_enabled is False
    assert settings.governed_demo_partial_exits_enabled is False
    assert partial_exits_enabled(settings, PAPER_INTERNAL_VENUE) is False
    assert partial_exits_enabled(settings, BLOFIN_DEMO_VENUE) is False


@pytest.mark.parametrize("raw", ["1", "yes", "on", "enabled", "tru", " ", "2"])
@pytest.mark.parametrize(
    "env_name", ["PAPER_PARTIAL_EXITS_ENABLED", "GOVERNED_DEMO_PARTIAL_EXITS_ENABLED"]
)
def test_ambiguous_flag_values_fail_closed(
    monkeypatch: pytest.MonkeyPatch, env_name: str, raw: str
) -> None:
    monkeypatch.setenv(env_name, raw)
    with pytest.raises(ValidationError):
        _settings()


@pytest.mark.parametrize("raw", ["true", "TRUE", "True"])
def test_explicit_true_enables_only_its_own_venue(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("PAPER_PARTIAL_EXITS_ENABLED", raw)
    settings = _settings()
    assert partial_exits_enabled(settings, PAPER_INTERNAL_VENUE) is True
    assert partial_exits_enabled(settings, BLOFIN_DEMO_VENUE) is False


def test_demo_flag_requires_demo_exchange_mode() -> None:
    settings = _settings(governed_demo_partial_exits_enabled=True)
    assert settings.exchange_mode is ExchangeMode.PAPER_INTERNAL
    assert partial_exits_enabled(settings, BLOFIN_DEMO_VENUE) is False


def test_real_money_venues_are_never_enabled() -> None:
    settings = _settings(
        paper_partial_exits_enabled=True,
        governed_demo_partial_exits_enabled=True,
    )
    for venue in ("BLOFIN", "BLOFIN_LIVE", "BINANCE", "", "blofin_demo", "paper_internal"):
        assert partial_exits_enabled(settings, venue) is False


def test_real_trading_switch_disables_every_venue() -> None:
    settings = _settings(paper_partial_exits_enabled=True).model_copy(
        update={"enable_real_trading": True}
    )
    assert partial_exits_enabled(settings, PAPER_INTERNAL_VENUE) is False


def test_live_configuration_cannot_load_and_a_bypassed_one_still_disables() -> None:
    """Settings refuse live trading at load; a model_copy bypass is still refused."""

    for live in ({"enable_real_trading": True}, {"exchange_mode": "trade_live"}):
        with pytest.raises(ValidationError):
            _settings(paper_partial_exits_enabled=True, **live)
    bypassed = _settings(
        paper_partial_exits_enabled=True, governed_demo_partial_exits_enabled=True
    ).model_copy(update={"exchange_mode": ExchangeMode.TRADE_LIVE})
    assert partial_exits_enabled(bypassed, BLOFIN_DEMO_VENUE) is False
