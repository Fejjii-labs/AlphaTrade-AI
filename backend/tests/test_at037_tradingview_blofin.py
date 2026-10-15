"""AT-037 — TradingView signal intake + BloFin demo read-only sync."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.errors import ExchangeDemoInactiveError
from app.db.base import Base
from app.db.models import (
    BloFinDemoSyncSnapshot,
    Membership,
    Organization,
    PaperValidationCandidate,
    TradingViewSignal,
    User,
    UserStrategy,
    UserStrategyVersion,
)
from app.db.session import get_session
from app.main import create_app
from app.providers.base import ProviderHealth, ProviderKind, ProviderStatus
from app.providers.exchange.base import AccountPermissions, ExchangeBalance, ExchangePositionData
from app.schemas.common import MembershipRole, StrategyId, TradingViewSignalStatus
from app.schemas.tradingview_signal import CREATE_TRADINGVIEW_CANDIDATE_CONFIRM
from app.security.tradingview_webhook import compute_tradingview_signature
from app.services.blofin_sync_service import BloFinSyncService

ORG_A = uuid.UUID("00000000-0000-0000-0000-000000037101")
ORG_B = uuid.UUID("00000000-0000-0000-0000-000000037102")
USER_A = uuid.UUID("00000000-0000-0000-0000-000000037111")
USER_B = uuid.UUID("00000000-0000-0000-0000-000000037112")
VIEWER_A = uuid.UUID("00000000-0000-0000-0000-000000037113")
TRADER_A = uuid.UUID("00000000-0000-0000-0000-000000037114")
STRATEGY_A = uuid.UUID("00000000-0000-0000-0000-000000037121")
VERSION_A = uuid.UUID("00000000-0000-0000-0000-000000037122")

WEBHOOK_SECRET = "at037-tradingview-webhook-secret-32chars"

_BASE: dict[str, Any] = {
    "environment": "local",
    "log_json": False,
    "execution_mode": "paper",
    "enable_real_trading": False,
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "at037-tradingview-blofin-secret-32xx",
    "rate_limit_use_redis": False,
    "access_token_denylist_use_redis": False,
    "provider_mode": "mock",
    "market_data_provider": "mock",
    "alert_delivery_enabled": False,
    "telegram_alerts_enabled": False,
    "worker_enabled": False,
    "market_watcher_enabled": False,
    "tradingview_webhook_enabled": True,
    "tradingview_webhook_secret": WEBHOOK_SECRET,
    "tradingview_auto_create_candidate": False,
    "exchange_mode": "paper_internal",
    "blofin_demo_enabled": False,
}

_CARD: dict[str, Any] = {
    "strategy_name": "AT037 TV",
    "market_type": "crypto_perp",
    "asset_universe": ["BTCUSDT"],
    "timeframes": ["15m"],
    "entry_conditions": ["Pullback"],
    "confirmation_conditions": ["RSI reset"],
    "invalidation": ["Close below swing"],
    "stop_loss": ["2% below entry"],
    "take_profit_plan": ["TP1 at 1R"],
    "runner_plan": [],
    "position_sizing": ["Max 1%"],
    "add_rules": [],
    "no_trade_rules": [],
    "backtest_rules": [],
    "success_criteria": ["Win rate > 45%"],
    "validation_status": "draft",
}


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    from app.security.rate_limit import reset_rate_limiter

    reset_rate_limiter()


@pytest.fixture
def client() -> Iterator[tuple[TestClient, sessionmaker[Session], Settings]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(**_BASE)

    with factory() as session:
        session.add(Organization(id=ORG_A, name="AT037 Org A"))
        session.add(Organization(id=ORG_B, name="AT037 Org B"))
        for user_id, email in (
            (USER_A, "at037-a@test.example"),
            (USER_B, "at037-b@test.example"),
            (VIEWER_A, "at037-viewer@test.example"),
            (TRADER_A, "at037-trader@test.example"),
        ):
            session.add(
                User(
                    id=user_id,
                    email=email,
                    hashed_password=__import__(
                        "app.security.passwords", fromlist=["hash_password"]
                    ).hash_password("SecurePass123!", settings),
                    email_verified=True,
                )
            )
        session.flush()
        session.add(Membership(user_id=USER_A, organization_id=ORG_A, role=MembershipRole.OWNER))
        session.add(Membership(user_id=USER_B, organization_id=ORG_B, role=MembershipRole.OWNER))
        session.add(Membership(user_id=VIEWER_A, organization_id=ORG_A, role=MembershipRole.VIEWER))
        session.add(Membership(user_id=TRADER_A, organization_id=ORG_A, role=MembershipRole.TRADER))
        session.add(
            UserStrategy(
                id=STRATEGY_A,
                organization_id=ORG_A,
                user_id=USER_A,
                name="AT037 Strategy",
                setup_type=StrategyId.HTF_TREND_PULLBACK,
                current_version=1,
            )
        )
        session.add(
            UserStrategyVersion(
                id=VERSION_A,
                strategy_id=STRATEGY_A,
                version=1,
                card=_CARD,
            )
        )
        session.commit()

    app = create_app(settings=settings)

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as test_client:
        yield test_client, factory, settings


def _login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/auth/login",
        json={"email": email, "password": "SecurePass123!"},
    )
    assert response.status_code == 200, response.text
    token = response.json()["tokens"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "organization_id": str(ORG_A),
        "alert_id": "tv-alert-100",
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "direction": "long",
        "setup_name": "HTF Pullback",
        "setup_version": 1,
        "strategy_id": str(STRATEGY_A),
        "strategy_version_id": str(VERSION_A),
        "trigger_level": 65000.0,
        "invalidation_level": 64000.0,
        "take_profit_level": 67000.0,
        "stop_loss_level": 64000.0,
        "confidence": 0.8,
        "source": {"chart": "BTCUSDT.P"},
    }
    body.update(overrides)
    return body


def _post_webhook(
    client: TestClient,
    body: dict[str, Any],
    *,
    secret: str = WEBHOOK_SECRET,
    skew: int = 0,
    bad_signature: bool = False,
    omit_headers: bool = False,
) -> Any:
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    timestamp = int(time.time()) + skew
    signature = compute_tradingview_signature(raw, timestamp=timestamp, secret=secret)
    if bad_signature:
        signature = "0" * 64
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if not omit_headers:
        headers["X-AT-Timestamp"] = str(timestamp)
        headers["X-AT-Signature"] = signature
    return client.post("/webhooks/tradingview", content=raw, headers=headers)


def test_webhook_signature_and_replay_protection(
    client: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    test_client, _factory, _settings = client
    body = _payload()

    missing = _post_webhook(test_client, body, omit_headers=True)
    assert missing.status_code == 401

    bad = _post_webhook(test_client, body, bad_signature=True)
    assert bad.status_code == 401

    stale = _post_webhook(test_client, body, skew=-10_000)
    assert stale.status_code == 401

    ok = _post_webhook(test_client, body)
    assert ok.status_code == 200, ok.text
    assert ok.json()["signal"]["status"] == TradingViewSignalStatus.VALIDATED.value
    assert ok.json()["already_exists"] is False


def test_webhook_idempotency_converges(
    client: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    test_client, factory, _settings = client
    body = _payload(alert_id="tv-idem-1")
    first = _post_webhook(test_client, body)
    second = _post_webhook(test_client, body)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["already_exists"] is True
    assert second.json()["duplicate"] is True
    assert first.json()["signal"]["id"] == second.json()["signal"]["id"]

    conflict = _post_webhook(
        test_client,
        _payload(alert_id="tv-idem-1", confidence=0.1),
    )
    assert conflict.status_code == 422

    with factory() as session:
        count = len(session.scalars(select(TradingViewSignal)).all())
        assert count == 1


def test_malformed_and_bounded_payload_rejected(
    client: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    test_client, _factory, _settings = client
    oversized = _payload(symbol="X" * 40)
    response = _post_webhook(test_client, oversized)
    assert response.status_code == 422

    deep_source = _payload(source={"a": {"b": {"c": {"d": 1}}}})
    deep = _post_webhook(test_client, deep_source)
    assert deep.status_code == 422

    unknown_org = _payload(organization_id=str(uuid.uuid4()), alert_id="tv-unknown-org")
    missing_org = _post_webhook(test_client, unknown_org)
    assert missing_org.status_code == 422


def test_tenant_isolation_and_rbac(
    client: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    test_client, _factory, _settings = client
    created = _post_webhook(test_client, _payload(alert_id="tv-tenant-1"))
    assert created.status_code == 200
    signal_id = created.json()["signal"]["id"]

    owner_a = _login(test_client, "at037-a@test.example")
    owner_b = _login(test_client, "at037-b@test.example")
    viewer = _login(test_client, "at037-viewer@test.example")
    trader = _login(test_client, "at037-trader@test.example")

    listed = test_client.get("/tradingview/signals", headers=owner_a)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    cross = test_client.get(f"/tradingview/signals/{signal_id}", headers=owner_b)
    assert cross.status_code == 404

    viewer_list = test_client.get("/tradingview/signals", headers=viewer)
    assert viewer_list.status_code == 200

    bad_confirm = test_client.post(
        f"/tradingview/signals/{signal_id}/create-candidate",
        headers=trader,
        json={"confirm": "NOPE"},
    )
    assert bad_confirm.status_code == 422

    viewer_create = test_client.post(
        f"/tradingview/signals/{signal_id}/create-candidate",
        headers=viewer,
        json={"confirm": CREATE_TRADINGVIEW_CANDIDATE_CONFIRM},
    )
    assert viewer_create.status_code == 403


def test_candidate_creation_is_paper_only_and_safe(
    client: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    test_client, factory, _settings = client
    created = _post_webhook(test_client, _payload(alert_id="tv-candidate-1"))
    signal_id = created.json()["signal"]["id"]
    trader = _login(test_client, "at037-trader@test.example")

    result = test_client.post(
        f"/tradingview/signals/{signal_id}/create-candidate",
        headers=trader,
        json={"confirm": CREATE_TRADINGVIEW_CANDIDATE_CONFIRM},
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["already_exists"] is False
    assert body["signal"]["status"] == TradingViewSignalStatus.CANDIDATE_CREATED.value
    assert "Paper-validation candidate only" in body["note"]

    again = test_client.post(
        f"/tradingview/signals/{signal_id}/create-candidate",
        headers=trader,
        json={"confirm": CREATE_TRADINGVIEW_CANDIDATE_CONFIRM},
    )
    assert again.status_code == 200
    assert again.json()["already_exists"] is True

    with factory() as session:
        candidates = session.scalars(select(PaperValidationCandidate)).all()
        assert len(candidates) == 1
        assert candidates[0].promotion_source == "tradingview_signal"
        assert candidates[0].candidate_status == "queued"


def test_foreign_strategy_is_rejected(
    client: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    test_client, _factory, _settings = client
    response = _post_webhook(
        test_client,
        _payload(
            alert_id="tv-bad-strategy",
            strategy_id=str(uuid.uuid4()),
            strategy_version_id=None,
        ),
    )
    assert response.status_code == 200
    assert response.json()["signal"]["status"] == TradingViewSignalStatus.REJECTED.value
    assert response.json()["signal"]["rejection_reason"]


def test_webhook_disabled_fail_closed(
    client: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    test_client, _factory, settings = client
    settings.tradingview_webhook_enabled = False
    response = _post_webhook(test_client, _payload(alert_id="tv-disabled"))
    assert response.status_code == 503


def test_blofin_sync_read_only_contract(
    client: tuple[TestClient, sessionmaker[Session], Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, factory, settings = client
    settings.blofin_api_key = "sync-fixture-key"
    settings.blofin_api_secret = "sync-fixture-secret"
    settings.blofin_api_passphrase = "sync-fixture-passphrase"
    owner = _login(test_client, "at037-a@test.example")

    balances = [
        ExchangeBalance(asset="USDT", total=Decimal("1000"), available=Decimal("900")),
    ]
    positions = [
        ExchangePositionData(
            symbol="BTCUSDT",
            inst_id="BTC-USDT",
            side="long",
            size=Decimal("0.1"),
            entry_price=Decimal("65000"),
            mark_price=Decimal("65100"),
            unrealized_pnl=Decimal("10"),
            leverage=Decimal("2"),
        )
    ]
    permissions = AccountPermissions(
        can_read=True,
        can_trade=False,
        can_withdraw=False,
        can_transfer=False,
        raw_scopes=("read",),
        response_keys=("readOnly",),
    )
    provider = MagicMock()
    provider.name = "blofin_demo"
    provider.native_account_uid = "sync-fixture-native-uid"
    provider.get_balances.return_value = balances
    provider.get_positions.return_value = positions
    provider.get_account_permissions.return_value = permissions
    provider.status.return_value = ProviderStatus(
        name="blofin_demo",
        kind=ProviderKind.EXCHANGE,
        health=ProviderHealth.HEALTHY,
        using_fallback=False,
        is_mock=True,
        detail="ok",
    )

    monkeypatch.setattr(
        "app.services.blofin_sync_service.ensure_demo_exchange_access",
        lambda _settings: None,
    )
    monkeypatch.setattr(
        "app.services.blofin_sync_service.get_demo_account_provider",
        lambda _settings: provider,
    )

    synced = test_client.post("/exchange/blofin/sync", headers=owner)
    assert synced.status_code == 200, synced.text
    snapshot = synced.json()["snapshot"]
    assert snapshot["health_status"] == "ok"
    assert snapshot["position_count"] == 1
    assert snapshot["balance_count"] == 1
    assert snapshot["provenance"]["read_only"] is True
    assert snapshot["provenance"]["order_mutations"] is False
    assert "api_key" not in json.dumps(snapshot).lower()
    assert "secret" not in json.dumps(snapshot["account_snapshot"]).lower()

    # Account provider only — never execution / order APIs.
    provider.get_balances.assert_called()
    provider.get_positions.assert_called()
    assert not hasattr(provider, "place_order") or not provider.place_order.called

    latest = test_client.get("/exchange/blofin/sync/latest", headers=owner)
    assert latest.status_code == 200
    assert latest.json()["id"] == snapshot["id"]

    with factory() as session:
        rows = session.scalars(select(BloFinDemoSyncSnapshot)).all()
        assert len(rows) == 1
        assert rows[0].organization_id == ORG_A

    # Unavailable path when demo gate fails.
    monkeypatch.setattr(
        "app.services.blofin_sync_service.ensure_demo_exchange_access",
        lambda _settings: (_ for _ in ()).throw(
            ExchangeDemoInactiveError("BloFin demo exchange is not active.")
        ),
    )
    degraded = test_client.post("/exchange/blofin/sync", headers=owner)
    assert degraded.status_code == 200
    assert degraded.json()["snapshot"]["health_status"] == "unavailable"

    # Cross-tenant: org B sees empty/not found.
    owner_b = _login(test_client, "at037-b@test.example")
    missing = test_client.get("/exchange/blofin/sync/latest", headers=owner_b)
    assert missing.status_code == 404

    # Service-level stale marking — age every org snapshot so latest is stale.
    with factory() as session:
        rows = list(
            session.scalars(
                select(BloFinDemoSyncSnapshot).where(
                    BloFinDemoSyncSnapshot.organization_id == ORG_A
                )
            ).all()
        )
        assert rows
        for row in rows:
            row.synced_at = datetime(2020, 1, 1, tzinfo=UTC)
            row.health_status = "ok"
        session.commit()
    with factory() as session:
        service = BloFinSyncService(session, settings)
        stale = service.latest(organization_id=ORG_A)
        assert stale.is_stale is True
        assert stale.health_status.value == "stale"


def test_real_trading_remains_disabled_in_settings(
    client: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    _test_client, _factory, settings = client
    assert settings.enable_real_trading is False
    assert settings.real_trading_enabled is False
    assert settings.execution_mode.value == "paper"
