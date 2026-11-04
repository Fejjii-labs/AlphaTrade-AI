"""Configurable BloFin demo leverage and margin mode settings."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.autonomous_trading_settings import AutonomousTradingSettingsRow
from app.services.demo_leverage_config import (
    DemoLeverageValidationError,
    config_for_symbol,
    load_demo_leverage_config,
    validate_demo_leverage,
    validate_demo_leverage_overrides,
    validate_demo_margin_mode,
)
from tests import test_autonomous_trading_settings as base
from tests.support.alembic_heads import alembic_script_directory, single_alembic_head

ORG, OWNER, _token = base.ORG, base.OWNER, base._token
# Reuse the sqlite org/owner/trader fixtures from the existing settings tests.
settings_db = base.settings_db
client = base.client


def test_demo_leverage_migration_is_the_single_head_after_binance_gate() -> None:
    script = alembic_script_directory()
    assert single_alembic_head(script) == "a24demoleverage001"
    assert script.get_revision("a24demoleverage001").down_revision == "a24binancegate001"


def test_validation_default_ceiling_and_floor() -> None:
    assert validate_demo_leverage(10) == 10
    assert validate_demo_leverage(20) == 20
    assert validate_demo_leverage(Decimal("5")) == 5
    for bad, code in (
        (21, "demo_leverage_exceeds_ceiling"),
        (125, "demo_leverage_exceeds_ceiling"),
        (0, "demo_leverage_below_one"),
        (-3, "demo_leverage_below_one"),
        (Decimal("2.5"), "demo_leverage_not_whole"),
        (True, "demo_leverage_invalid"),
        ("10", "demo_leverage_invalid"),
    ):
        with pytest.raises(DemoLeverageValidationError) as exc:
            validate_demo_leverage(bad)
        assert exc.value.code == code
    assert validate_demo_margin_mode("ISOLATED") == "isolated"
    with pytest.raises(DemoLeverageValidationError):
        validate_demo_margin_mode("portfolio")


def test_override_map_validation() -> None:
    assert validate_demo_leverage_overrides({"btcusdt": {"leverage": 5}}) == {
        "BTCUSDT": {"leverage": 5}
    }
    for bad in (
        {"BTCUSDT": {"leverage": 21}},
        {"BTC": {"leverage": 5}},
        {"BTCUSDT": {}},
        {"BTCUSDT": {"leverage": 5, "size": 1}},
        {"BTCUSDT": {"leverage": 5}, "btcusdt": {"leverage": 3}},
    ):
        with pytest.raises(DemoLeverageValidationError):
            validate_demo_leverage_overrides(bad)  # type: ignore[arg-type]


def test_config_resolution_default_account_and_symbol_override() -> None:
    default = config_for_symbol(leverage=None, margin_mode=None, overrides=None, symbol="BTCUSDT")
    assert (default.leverage, default.margin_mode, default.source) == (10, "isolated", "default")
    account = config_for_symbol(leverage=3, margin_mode="cross", overrides=None, symbol="BTCUSDT")
    assert (account.leverage, account.margin_mode, account.source) == (3, "cross", "account")
    override = config_for_symbol(
        leverage=3,
        margin_mode="cross",
        overrides={"ETHUSDT": {"leverage": 7}},
        symbol="ETHUSDT",
    )
    assert (override.leverage, override.margin_mode, override.source) == (
        7,
        "cross",
        "symbol_override",
    )
    # Stored data above the ceiling (e.g. edited by hand) refuses, not clamps.
    with pytest.raises(DemoLeverageValidationError):
        config_for_symbol(leverage=50, margin_mode=None, overrides=None, symbol="BTCUSDT")


def test_api_default_is_ten_isolated(client: TestClient) -> None:
    headers = _token(client, "owner-284@test.example")
    body = client.get("/autonomous/settings", headers=headers).json()
    assert body["demo_leverage"] == 10
    assert body["demo_margin_mode"] == "isolated"
    assert body["demo_leverage_overrides"] == {}
    assert body["demo_leverage_using_default"] is True
    assert body["max_demo_leverage_ceiling"] == 20


def test_api_saves_leverage_mode_and_override(
    client: TestClient, settings_db: tuple[sessionmaker[Session], object]
) -> None:
    headers = _token(client, "owner-284@test.example")
    saved = client.patch(
        "/autonomous/settings",
        headers=headers,
        json={
            "demo_leverage": 15,
            "demo_margin_mode": "cross",
            "demo_leverage_overrides": {"ethusdt": {"leverage": 4, "margin_mode": "isolated"}},
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["demo_leverage"] == 15
    assert body["demo_margin_mode"] == "cross"
    assert body["demo_leverage_overrides"] == {
        "ETHUSDT": {"leverage": 4, "margin_mode": "isolated"}
    }
    assert body["demo_leverage_using_default"] is False
    factory, _settings = settings_db
    with factory() as session:
        row = session.scalar(select(AutonomousTradingSettingsRow))
        assert row is not None and row.demo_leverage == 15
        eth = load_demo_leverage_config(
            session, organization_id=ORG, user_id=OWNER, symbol="ETHUSDT"
        )
        btc = load_demo_leverage_config(
            session, organization_id=ORG, user_id=OWNER, symbol="BTCUSDT"
        )
    assert (eth.leverage, eth.margin_mode) == (4, "isolated")
    assert (btc.leverage, btc.margin_mode) == (15, "cross")
    cleared = client.patch(
        "/autonomous/settings", headers=headers, json={"clear_demo_leverage_overrides": True}
    )
    assert cleared.json()["demo_leverage_overrides"] == {}
    assert cleared.json()["demo_leverage"] == 15


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"demo_leverage": 21}, "demo_leverage_exceeds_ceiling"),
        ({"demo_leverage": 0}, "demo_leverage_below_one"),
        ({"demo_margin_mode": "portfolio"}, "demo_margin_mode_invalid"),
        (
            {"demo_leverage_overrides": {"BTCUSDT": {"leverage": 30}}},
            "demo_leverage_exceeds_ceiling",
        ),
        (
            {"demo_leverage_overrides": {"NOPE": {"leverage": 3}}},
            "demo_leverage_override_symbol_invalid",
        ),
    ],
)
def test_api_rejects_out_of_range(
    client: TestClient, payload: dict[str, object], code: str
) -> None:
    headers = _token(client, "owner-284@test.example")
    resp = client.patch("/autonomous/settings", headers=headers, json=payload)
    assert resp.status_code == 422
    assert resp.json()["detail"] == code
    assert client.get("/autonomous/settings", headers=headers).json()["demo_leverage"] == 10


def test_trader_cannot_change_demo_leverage(client: TestClient) -> None:
    headers = _token(client, "trader-284@test.example")
    resp = client.patch("/autonomous/settings", headers=headers, json={"demo_leverage": 5})
    assert resp.status_code == 403
