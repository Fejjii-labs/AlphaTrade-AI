"""Opt-in that lets demo sync accept a read+trade demo key (GET-only reads)."""

from __future__ import annotations

import pytest

from app.core.config import ExecutionMode, Settings
from app.providers.exchange.base import AccountPermissions
from app.services import blofin_sync_service as svc

DEMO = "https://demo-trading-openapi.blofin.com"


def _settings(**update: object) -> Settings:
    base = Settings(blofin_demo_rest_base_url=DEMO, execution_mode=ExecutionMode.PAPER)
    values = {"blofin_demo_allow_trade_capable_sync_key": True, **update}
    return base.model_copy(update=values)


def _perms(**kw: bool) -> AccountPermissions:
    return AccountPermissions(
        can_read=True,
        can_trade=True,
        can_withdraw=kw.get("withdraw", False),
        can_transfer=kw.get("transfer", False),
        raw_scopes=(),
        response_keys=("readOnly",),
    )


def test_override_off_raises() -> None:
    s = _settings(blofin_demo_allow_trade_capable_sync_key=False)
    with pytest.raises(ValueError, match="read-only"):
        svc.ensure_trade_capable_key_allowed(s, _perms())


def test_override_on_demo_paper_no_withdraw_passes() -> None:
    svc.ensure_trade_capable_key_allowed(_settings(), _perms())


def test_override_on_with_withdraw_raises() -> None:
    with pytest.raises(ValueError):
        svc.ensure_trade_capable_key_allowed(_settings(), _perms(withdraw=True))


def test_override_on_with_transfer_raises() -> None:
    with pytest.raises(ValueError):
        svc.ensure_trade_capable_key_allowed(_settings(), _perms(transfer=True))


def test_override_on_non_demo_host_raises() -> None:
    s = _settings(blofin_demo_rest_base_url="https://openapi.blofin.com")
    with pytest.raises(ValueError):
        svc.ensure_trade_capable_key_allowed(s, _perms())


def test_override_on_real_trading_enabled_raises() -> None:
    s = _settings(enable_real_trading=True)
    with pytest.raises(ValueError):
        svc.ensure_trade_capable_key_allowed(s, _perms())


def test_override_on_live_execution_mode_raises() -> None:
    live = next(m for m in ExecutionMode if m is not ExecutionMode.PAPER)
    with pytest.raises(ValueError):
        svc.ensure_trade_capable_key_allowed(_settings(execution_mode=live), _perms())


def test_real_readonly_only_reply_is_accepted_as_venue_reports_no_withdraw() -> None:
    """BloFin returns only ``readOnly``; the venue reports no withdraw/transfer."""
    from app.providers.exchange.blofin_account import parse_account_permissions

    perms = parse_account_permissions({"readOnly": 0, "type": 3})
    assert perms.can_trade is True
    assert perms.can_withdraw is False and perms.can_transfer is False
    svc.ensure_trade_capable_key_allowed(_settings(), perms)
    with pytest.raises(ValueError):
        svc.ensure_trade_capable_key_allowed(
            _settings(blofin_demo_allow_trade_capable_sync_key=False), perms
        )


def test_override_warning_is_logged_once_per_process(monkeypatch) -> None:
    from structlog.testing import capture_logs

    monkeypatch.setattr(svc, "_TRADE_CAPABLE_OVERRIDE_LOGGED", False)
    with capture_logs() as logs:
        svc.ensure_trade_capable_key_allowed(_settings(), _perms())
        svc.ensure_trade_capable_key_allowed(_settings(), _perms())
    events = [e for e in logs if e["event"] == "blofin_sync_key_trade_capable_demo_override"]
    assert len(events) == 1
