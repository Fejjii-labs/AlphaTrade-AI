"""End to end through BloFinSyncService with BloFin's real ``{readOnly: 0, type: 3}`` reply."""

from tests.test_at037_tradingview_blofin import _login
from tests.test_at037_tradingview_blofin import client as demo_client
from tests.test_dashboard_demo_account import configure, wire

__all__ = ["demo_client"]

TRADE_KEY = {"readOnly": 0, "type": 3, "uid": "dashboard-native-uid"}


def _refresh(demo_client, monkeypatch, *, override: bool):
    client, _, settings = demo_client
    configure(settings)
    settings.blofin_demo_allow_trade_capable_sync_key = override
    seen: list[tuple[str, str]] = []
    wire(monkeypatch, seen, identity=TRADE_KEY)
    owner = _login(client, "at037-a@test.example")
    return client.post("/dashboard/demo-account/refresh", headers=owner).json(), seen


def test_trade_capable_key_is_refused_with_override_off(demo_client, monkeypatch):
    data, _ = _refresh(demo_client, monkeypatch, override=False)
    assert data["status"] != "ok"


def test_trade_capable_key_syncs_read_only_with_override_on(demo_client, monkeypatch):
    data, seen = _refresh(demo_client, monkeypatch, override=True)
    assert data["status"] == "ok", data
    assert data["read_only"] is True
    assert seen and all(method == "GET" for method, _ in seen)
