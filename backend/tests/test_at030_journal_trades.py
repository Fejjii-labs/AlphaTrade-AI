"""AT-030 — canonical journal trades (journal intelligence foundation)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    AuditLog,
    JournalTrade,
    JournalTradeEvidence,
    JournalTradeObservation,
    JournalTradeRuleCheck,
    Membership,
    Organization,
    PaperTrade,
    PaperValidationRun,
    Position,
    TradeProposal,
    User,
    UserStrategy,
    UserStrategyVersion,
)
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import (
    AuditEventType,
    JournalTradeSource,
    JournalTradeStatus,
    MarketRegime,
    MembershipRole,
    PaperTradeStatus,
    RiskSeverity,
    StrategyId,
    TradeDirection,
    TradeResult,
)
from app.security.passwords import hash_password
from app.security.rate_limit import reset_rate_limiter

ORG_A = uuid.UUID("00000000-0000-0000-0000-000000009001")
ORG_B = uuid.UUID("00000000-0000-0000-0000-000000009002")
USER_A = uuid.UUID("00000000-0000-0000-0000-000000009011")
USER_B = uuid.UUID("00000000-0000-0000-0000-000000009012")
VIEWER_A = uuid.UUID("00000000-0000-0000-0000-000000009013")
OTHER_TRADER_A = uuid.UUID("00000000-0000-0000-0000-000000009014")

_BASE = {
    "environment": "local",
    "log_json": False,
    "execution_mode": "paper",
    "enable_real_trading": False,
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "journal-trades-test-secret-abc-32ch",
    "rate_limit_use_redis": False,
    "access_token_denylist_use_redis": False,
    "provider_mode": "mock",
    "market_data_provider": "mock",
    "alert_delivery_enabled": False,
    "telegram_alerts_enabled": False,
    "worker_enabled": False,
    "market_watcher_enabled": False,
}


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    reset_rate_limiter()


@pytest.fixture
def client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
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
        session.add(Organization(id=ORG_A, name="Journal Org A"))
        session.add(Organization(id=ORG_B, name="Journal Org B"))
        for user_id, email in (
            (USER_A, "journal-a@test.example"),
            (USER_B, "journal-b@test.example"),
            (VIEWER_A, "journal-viewer@test.example"),
        ):
            session.add(
                User(
                    id=user_id,
                    email=email,
                    hashed_password=hash_password("SecurePass123!", settings),
                    email_verified=True,
                )
            )
        session.flush()
        session.add(Membership(user_id=USER_A, organization_id=ORG_A, role=MembershipRole.OWNER))
        session.add(Membership(user_id=USER_B, organization_id=ORG_B, role=MembershipRole.OWNER))
        session.add(Membership(user_id=VIEWER_A, organization_id=ORG_A, role=MembershipRole.VIEWER))
        session.commit()

    app = create_app(settings=settings)

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session

    with TestClient(app) as test_client:
        yield test_client, factory

    app.dependency_overrides.clear()
    engine.dispose()


def _seed_other_trader_in_org_a(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        if session.get(User, OTHER_TRADER_A) is not None:
            return
        settings = Settings(**_BASE)
        session.add(
            User(
                id=OTHER_TRADER_A,
                email="journal-other-trader-a@test.example",
                hashed_password=hash_password("SecurePass123!", settings),
                email_verified=True,
            )
        )
        session.flush()
        session.add(
            Membership(
                user_id=OTHER_TRADER_A,
                organization_id=ORG_A,
                role=MembershipRole.TRADER,
            )
        )
        session.commit()


def _auth(client: TestClient, email: str, password: str = "SecurePass123!") -> dict[str, str]:
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["tokens"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _manual_trade_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "source": "manual",
        "status": "planned",
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "direction": "long",
        "market_regime": "trending_up",
        "thesis": "HTF uptrend, pullback into demand.",
        "trigger": "Bullish engulfing on 1h at level.",
        "entry_plan": "Limit at 64500 after sweep.",
        "invalidation": "Close below 64000 on 1h.",
        "planned_entry_price": "64500",
        "planned_stop_price": "64000",
        "planned_targets": [
            {"price": "65500", "size_fraction": 0.5, "label": "TP1"},
            {"price": "66500", "size_fraction": 0.5, "label": "TP2"},
        ],
        "runner_enabled": True,
        "runner_plan": "Trail remainder behind 4h swing lows.",
        "planned_risk_amount": "100",
        "leverage": "3",
        "tags": ["demand-zone", "session:london"],
    }
    body.update(overrides)
    return body


def _seed_position(
    factory: sessionmaker[Session],
    *,
    organization_id: uuid.UUID = ORG_A,
    user_id: uuid.UUID = USER_A,
    with_proposal: bool = True,
    closed: bool = True,
) -> uuid.UUID:
    with factory() as session:
        proposal_id: uuid.UUID | None = None
        if with_proposal:
            proposal = TradeProposal(
                organization_id=organization_id,
                user_id=user_id,
                strategy_id=StrategyId.HTF_TREND_PULLBACK,
                symbol="ETHUSDT",
                timeframe="4h",
                direction=TradeDirection.LONG,
                entry_price=Decimal("3000"),
                position_size=Decimal("1"),
                leverage=Decimal("2"),
                stop_loss=Decimal("2900"),
                take_profits=[{"price": "3200", "size_fraction": 1.0}],
                invalidation="4h close below 2900.",
                confidence=0.8,
                risk_level=RiskSeverity.LOW,
                rationale="Trend pullback at support.",
                runner_enabled=True,
                runner_notes="Trail behind swing lows.",
            )
            session.add(proposal)
            session.flush()
            proposal_id = proposal.id
        position = Position(
            organization_id=organization_id,
            user_id=user_id,
            strategy_id=StrategyId.HTF_TREND_PULLBACK,
            linked_proposal_id=proposal_id,
            symbol="ETHUSDT",
            direction=TradeDirection.LONG,
            size=Decimal("1"),
            entry_price=Decimal("3010"),
            leverage=Decimal("2"),
            stop_loss=Decimal("2900"),
            realized_pnl=Decimal("150") if closed else Decimal("0"),
            opened_at=datetime(2026, 7, 1, 10, 0, tzinfo=UTC),
            closed_at=datetime(2026, 7, 2, 15, 0, tzinfo=UTC) if closed else None,
        )
        session.add(position)
        session.commit()
        return position.id


def _seed_paper_trade(
    factory: sessionmaker[Session],
    *,
    organization_id: uuid.UUID = ORG_A,
    user_id: uuid.UUID = USER_A,
) -> uuid.UUID:
    with factory() as session:
        strategy = UserStrategy(
            organization_id=organization_id,
            user_id=user_id,
            name=f"Sweep reversal {uuid.uuid4().hex[:6]}",
            setup_type=StrategyId.LIQUIDITY_SWEEP_REVERSAL,
        )
        session.add(strategy)
        session.flush()
        version = UserStrategyVersion(
            strategy_id=strategy.id,
            version=1,
            card={"name": strategy.name},
        )
        session.add(version)
        session.flush()
        run = PaperValidationRun(
            strategy_id=strategy.id,
            strategy_version_id=version.id,
            organization_id=organization_id,
            user_id=user_id,
        )
        session.add(run)
        session.flush()
        trade = PaperTrade(
            paper_validation_run_id=run.id,
            strategy_id=strategy.id,
            strategy_version_id=version.id,
            organization_id=organization_id,
            user_id=user_id,
            symbol="SOLUSDT",
            exchange="binance",
            timeframe="15m",
            direction=TradeDirection.SHORT,
            entry_price=Decimal("150"),
            entry_time=datetime(2026, 7, 3, 9, 0, tzinfo=UTC),
            size=Decimal("10"),
            stop_loss=Decimal("155"),
            invalidation="15m close above 155.",
            status=PaperTradeStatus.CLOSED,
            exit_price=Decimal("145"),
            exit_time=datetime(2026, 7, 3, 14, 0, tzinfo=UTC),
            exit_reason="tp_hit",
            gross_pnl=Decimal("50"),
            net_pnl=Decimal("48"),
            fees=Decimal("1.5"),
            slippage=Decimal("0.5"),
        )
        session.add(trade)
        session.commit()
        return trade.id


# --------------------------------------------------------------------------- #
# Authorization
# --------------------------------------------------------------------------- #


def test_journal_trades_require_auth(client: tuple[TestClient, sessionmaker[Session]]) -> None:
    test_client, _ = client
    assert test_client.get("/journal/trades").status_code == 401
    assert test_client.post("/journal/trades", json=_manual_trade_body()).status_code == 401


def test_viewer_can_read_but_not_write(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, _ = client
    owner = _auth(test_client, "journal-a@test.example")
    viewer = _auth(test_client, "journal-viewer@test.example")

    created = test_client.post("/journal/trades", json=_manual_trade_body(), headers=owner)
    assert created.status_code == 201, created.text

    listed = test_client.get("/journal/trades", headers=viewer)
    assert listed.status_code == 200

    denied = test_client.post("/journal/trades", json=_manual_trade_body(), headers=viewer)
    assert denied.status_code == 403


# --------------------------------------------------------------------------- #
# CRUD lifecycle
# --------------------------------------------------------------------------- #


def test_create_manual_trade_persists_plan_and_audits(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "journal-a@test.example")

    response = test_client.post("/journal/trades", json=_manual_trade_body(), headers=headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source"] == "manual"
    assert body["status"] == "planned"
    assert body["market_regime"] == "trending_up"
    assert body["thesis"].startswith("HTF uptrend")
    assert body["invalidation"].startswith("Close below")
    assert Decimal(body["planned_entry_price"]) == Decimal("64500")
    assert [t["label"] for t in body["planned_targets"]] == ["TP1", "TP2"]
    assert body["runner_enabled"] is True
    assert body["tags"] == ["demand-zone", "session:london"]
    assert body["organization_id"] == str(ORG_A)
    assert body["user_id"] == str(USER_A)

    with factory() as session:
        audit = session.scalars(
            select(AuditLog).where(AuditLog.action == AuditEventType.JOURNAL_TRADE_CREATED)
        ).all()
        assert len(audit) == 1
        assert audit[0].resource_type == "journal_trade"
        assert audit[0].resource_id == body["id"]


def test_update_close_trade_derives_realized_vs_available(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "journal-a@test.example")
    created = test_client.post("/journal/trades", json=_manual_trade_body(), headers=headers)
    trade_id = created.json()["id"]

    updated = test_client.patch(
        f"/journal/trades/{trade_id}",
        json={
            "status": "closed",
            "entry_price": "64500",
            "entry_time": "2026-07-05T10:00:00Z",
            "exit_price": "65500",
            "exit_time": "2026-07-05T16:00:00Z",
            "exit_reason": "tp1_hit",
            "size": "0.5",
            "fees": "3.25",
            "funding": "0.75",
            "slippage": "1.10",
            "gross_pnl": "500",
            "net_pnl": "494.90",
            "result": "win",
            "mfe_price": "66400",
            "mae_price": "64350",
            "mfe_amount": "950",
            "mae_amount": "-75",
            "available_profit": "950",
            "excursion_source": "manual",
        },
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["status"] == "closed"
    assert body["result"] == "win"
    assert Decimal(body["net_pnl"]) == Decimal("494.90")
    assert Decimal(body["available_profit"]) == Decimal("950")
    # 494.90 / 950 * 100
    assert body["realized_vs_available_pct"] == pytest.approx(52.0947, rel=1e-4)

    with factory() as session:
        audit = session.scalars(
            select(AuditLog).where(AuditLog.action == AuditEventType.JOURNAL_TRADE_UPDATED)
        ).all()
        assert len(audit) == 1


def test_list_filters_and_pagination(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, _ = client
    headers = _auth(test_client, "journal-a@test.example")
    for symbol in ("BTCUSDT", "BTCUSDT", "ETHUSDT"):
        response = test_client.post(
            "/journal/trades", json=_manual_trade_body(symbol=symbol), headers=headers
        )
        assert response.status_code == 201

    all_trades = test_client.get("/journal/trades", headers=headers).json()
    assert all_trades["total"] == 3

    btc = test_client.get("/journal/trades", params={"symbol": "BTCUSDT"}, headers=headers)
    assert btc.json()["total"] == 2

    paged = test_client.get(
        "/journal/trades", params={"limit": 2, "offset": 2}, headers=headers
    ).json()
    assert paged["total"] == 3
    assert len(paged["items"]) == 1

    manual = test_client.get("/journal/trades", params={"source": "manual"}, headers=headers)
    assert manual.json()["total"] == 3
    imported = test_client.get("/journal/trades", params={"source": "imported"}, headers=headers)
    assert imported.json()["total"] == 0


def test_delete_removes_trade_and_children(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "journal-a@test.example")
    trade_id = test_client.post(
        "/journal/trades", json=_manual_trade_body(), headers=headers
    ).json()["id"]
    assert (
        test_client.post(
            f"/journal/trades/{trade_id}/evidence",
            json={"kind": "note", "caption": "Entry context."},
            headers=headers,
        ).status_code
        == 201
    )

    deleted = test_client.delete(f"/journal/trades/{trade_id}", headers=headers)
    assert deleted.status_code == 204
    assert test_client.get(f"/journal/trades/{trade_id}", headers=headers).status_code == 404

    with factory() as session:
        assert session.scalars(select(JournalTrade)).all() == []
        assert session.scalars(select(JournalTradeEvidence)).all() == []


# --------------------------------------------------------------------------- #
# Tenant isolation & link validation
# --------------------------------------------------------------------------- #


def test_cross_org_access_fails_closed(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, _ = client
    headers_a = _auth(test_client, "journal-a@test.example")
    headers_b = _auth(test_client, "journal-b@test.example")

    trade_id = test_client.post(
        "/journal/trades", json=_manual_trade_body(), headers=headers_a
    ).json()["id"]

    assert test_client.get(f"/journal/trades/{trade_id}", headers=headers_b).status_code == 404
    assert (
        test_client.patch(
            f"/journal/trades/{trade_id}", json={"notes": "x"}, headers=headers_b
        ).status_code
        == 404
    )
    assert test_client.delete(f"/journal/trades/{trade_id}", headers=headers_b).status_code == 404
    assert test_client.get("/journal/trades", headers=headers_b).json()["total"] == 0


def test_linked_records_must_belong_to_tenant(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers_b = _auth(test_client, "journal-b@test.example")
    foreign_position_id = _seed_position(factory, organization_id=ORG_A, user_id=USER_A)

    denied = test_client.post(
        "/journal/trades",
        json=_manual_trade_body(links={"linked_position_id": str(foreign_position_id)}),
        headers=headers_b,
    )
    assert denied.status_code == 404

    own_position_id = _seed_position(factory, organization_id=ORG_B, user_id=USER_B)
    allowed = test_client.post(
        "/journal/trades",
        json=_manual_trade_body(links={"linked_position_id": str(own_position_id)}),
        headers=headers_b,
    )
    assert allowed.status_code == 201
    assert allowed.json()["linked_position_id"] == str(own_position_id)


def test_strategy_version_must_match_strategy(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "journal-a@test.example")
    with factory() as session:
        strategy_one = UserStrategy(
            organization_id=ORG_A,
            user_id=USER_A,
            name="Strategy one",
            setup_type=StrategyId.HTF_TREND_PULLBACK,
        )
        strategy_two = UserStrategy(
            organization_id=ORG_A,
            user_id=USER_A,
            name="Strategy two",
            setup_type=StrategyId.HTF_TREND_PULLBACK,
        )
        session.add_all([strategy_one, strategy_two])
        session.flush()
        version_two = UserStrategyVersion(strategy_id=strategy_two.id, version=1, card={})
        session.add(version_two)
        session.commit()
        strategy_one_id, version_two_id = strategy_one.id, version_two.id

    mismatched = test_client.post(
        "/journal/trades",
        json=_manual_trade_body(
            user_strategy_id=str(strategy_one_id),
            strategy_version_id=str(version_two_id),
        ),
        headers=headers,
    )
    assert mismatched.status_code == 422


# --------------------------------------------------------------------------- #
# Integration with existing records
# --------------------------------------------------------------------------- #


def test_create_from_position_prefills_plan_and_is_idempotent(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "journal-a@test.example")
    position_id = _seed_position(factory)

    first = test_client.post(f"/journal/trades/from-position/{position_id}", headers=headers)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["source"] == "paper_execution"
    assert body["status"] == "closed"
    assert body["symbol"] == "ETHUSDT"
    assert body["timeframe"] == "4h"
    assert body["thesis"] == "Trend pullback at support."
    assert body["invalidation"] == "4h close below 2900."
    assert Decimal(body["planned_entry_price"]) == Decimal("3000")
    assert Decimal(body["planned_stop_price"]) == Decimal("2900")
    assert body["runner_enabled"] is True
    assert Decimal(body["net_pnl"]) == Decimal("150")
    assert body["result"] == "win"
    assert body["linked_position_id"] == str(position_id)
    assert body["linked_proposal_id"] is not None

    second = test_client.post(f"/journal/trades/from-position/{position_id}", headers=headers)
    assert second.status_code == 201
    assert second.json()["id"] == body["id"]

    # Foreign tenant cannot journal someone else's position.
    headers_b = _auth(test_client, "journal-b@test.example")
    denied = test_client.post(f"/journal/trades/from-position/{position_id}", headers=headers_b)
    assert denied.status_code == 404


def test_create_from_paper_trade_prefills_and_is_idempotent(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "journal-a@test.example")
    paper_trade_id = _seed_paper_trade(factory)

    first = test_client.post(f"/journal/trades/from-paper-trade/{paper_trade_id}", headers=headers)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["source"] == "paper_validation"
    assert body["status"] == "closed"
    assert body["symbol"] == "SOLUSDT"
    assert body["exchange"] == "binance"
    assert body["timeframe"] == "15m"
    assert body["direction"] == "short"
    assert Decimal(body["net_pnl"]) == Decimal("48")
    assert Decimal(body["fees"]) == Decimal("1.5")
    assert Decimal(body["slippage"]) == Decimal("0.5")
    assert body["result"] == "win"
    assert body["user_strategy_id"] is not None
    assert body["strategy_version_id"] is not None
    assert body["linked_paper_trade_id"] == str(paper_trade_id)
    assert body["linked_paper_validation_run_id"] is not None

    second = test_client.post(f"/journal/trades/from-paper-trade/{paper_trade_id}", headers=headers)
    assert second.status_code == 201
    assert second.json()["id"] == body["id"]


# --------------------------------------------------------------------------- #
# Evidence, rule checks, observations
# --------------------------------------------------------------------------- #


def test_detail_includes_evidence_rule_checks_and_observations(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "journal-a@test.example")
    trade_id = test_client.post(
        "/journal/trades", json=_manual_trade_body(), headers=headers
    ).json()["id"]

    evidence = test_client.post(
        f"/journal/trades/{trade_id}/evidence",
        json={
            "kind": "screenshot",
            "ref": "https://charts.example/btc-1h-entry.png",
            "caption": "Entry chart with demand zone.",
        },
        headers=headers,
    )
    assert evidence.status_code == 201, evidence.text
    assert evidence.json()["kind"] == "screenshot"
    assert evidence.json()["recorded_by"] == str(USER_A)

    rule_check = test_client.post(
        f"/journal/trades/{trade_id}/rule-checks",
        json={
            "rule_key": "stop_loss_placed_before_entry",
            "rule_source": "strategy_version",
            "status": "followed",
            "notes": "Stop set with the entry order.",
        },
        headers=headers,
    )
    assert rule_check.status_code == 201, rule_check.text
    assert rule_check.json()["status"] == "followed"
    assert rule_check.json()["assessed_at"] is not None

    observation = test_client.post(
        f"/journal/trades/{trade_id}/observations",
        json={
            "category": "behavioral",
            "observation": "Waited for the trigger candle; no FOMO entry.",
            "emotion_tags": ["calm", "patient"],
        },
        headers=headers,
    )
    assert observation.status_code == 201, observation.text
    assert observation.json()["emotion_tags"] == ["calm", "patient"]

    detail = test_client.get(f"/journal/trades/{trade_id}", headers=headers)
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["trade"]["id"] == trade_id
    assert len(payload["evidence"]) == 1
    assert len(payload["rule_checks"]) == 1
    assert len(payload["observations"]) == 1

    with factory() as session:
        for model, expected_event in (
            (JournalTradeEvidence, AuditEventType.JOURNAL_TRADE_EVIDENCE_ADDED),
            (JournalTradeRuleCheck, AuditEventType.JOURNAL_TRADE_RULE_CHECKED),
            (JournalTradeObservation, AuditEventType.JOURNAL_TRADE_OBSERVED),
        ):
            assert len(session.scalars(select(model)).all()) == 1
            audit = session.scalars(select(AuditLog).where(AuditLog.action == expected_event)).all()
            assert len(audit) == 1


def test_get_journal_trade_detail_includes_exit_reason_and_tags(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "journal-a@test.example")
    with factory() as session:
        trade = JournalTrade(
            organization_id=ORG_A,
            user_id=USER_A,
            source=JournalTradeSource.MANUAL,
            status=JournalTradeStatus.CLOSED,
            symbol="BTCUSDT",
            timeframe="manual",
            market_regime=MarketRegime.UNKNOWN,
            direction=TradeDirection.LONG,
            exit_reason="venue_close",
            tags=["blofin-reconciled", "session:london"],
            result=TradeResult.WIN,
        )
        session.add(trade)
        session.commit()
        trade_id = str(trade.id)

    detail = test_client.get(f"/journal/trades/{trade_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()["trade"]
    assert body["exit_reason"] == "venue_close"
    assert body["tags"] == ["blofin-reconciled", "session:london"]


def test_update_and_delete_journal_trade_require_owner(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    _seed_other_trader_in_org_a(factory)
    headers_owner = _auth(test_client, "journal-a@test.example")
    headers_other_org = _auth(test_client, "journal-b@test.example")
    headers_other_user = _auth(test_client, "journal-other-trader-a@test.example")
    trade_id = test_client.post(
        "/journal/trades", json=_manual_trade_body(), headers=headers_owner
    ).json()["id"]

    updated = test_client.patch(
        f"/journal/trades/{trade_id}",
        json={"notes": "Owner notes"},
        headers=headers_owner,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["notes"] == "Owner notes"

    denied_org_patch = test_client.patch(
        f"/journal/trades/{trade_id}",
        json={"notes": "Foreign org"},
        headers=headers_other_org,
    )
    assert denied_org_patch.status_code == 404
    denied_user_patch = test_client.patch(
        f"/journal/trades/{trade_id}",
        json={"notes": "Same org intruder"},
        headers=headers_other_user,
    )
    assert denied_user_patch.status_code == 404
    with factory() as session:
        row = session.get(JournalTrade, uuid.UUID(trade_id))
        assert row is not None
        assert row.notes == "Owner notes"

    denied_org_delete = test_client.delete(f"/journal/trades/{trade_id}", headers=headers_other_org)
    assert denied_org_delete.status_code == 404
    denied_user_delete = test_client.delete(
        f"/journal/trades/{trade_id}", headers=headers_other_user
    )
    assert denied_user_delete.status_code == 404
    with factory() as session:
        assert session.get(JournalTrade, uuid.UUID(trade_id)) is not None

    owner_delete = test_client.delete(f"/journal/trades/{trade_id}", headers=headers_owner)
    assert owner_delete.status_code == 204
    with factory() as session:
        assert session.get(JournalTrade, uuid.UUID(trade_id)) is None


def test_reconciled_mirror_annotation_scopes_organization_and_owner(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers_owner = _auth(test_client, "journal-a@test.example")
    headers_other_org = _auth(test_client, "journal-b@test.example")
    _seed_other_trader_in_org_a(factory)
    headers_other_user = _auth(test_client, "journal-other-trader-a@test.example")
    with factory() as session:
        trade = JournalTrade(
            organization_id=ORG_A,
            user_id=USER_A,
            source=JournalTradeSource.MANUAL,
            status=JournalTradeStatus.CLOSED,
            symbol="BTCUSDT",
            timeframe="manual",
            market_regime=MarketRegime.UNKNOWN,
            direction=TradeDirection.LONG,
            external_ref="blofin-demo-history:h-reconciled",
            exit_reason="venue_close",
            tags=["blofin-reconciled"],
            result=TradeResult.WIN,
        )
        demo_test = JournalTrade(
            organization_id=ORG_A,
            user_id=USER_A,
            source=JournalTradeSource.MANUAL_DEMO_TEST,
            status=JournalTradeStatus.CLOSED,
            symbol="ETHUSDT",
            timeframe="manual",
            market_regime=MarketRegime.UNKNOWN,
            direction=TradeDirection.LONG,
            external_ref="blofin-demo-history:h-demo-test",
            exit_reason="venue_close",
            tags=["blofin-reconciled"],
            result=TradeResult.LOSS,
            execution_lifecycle_id=uuid.uuid4(),
        )
        session.add_all([trade, demo_test])
        session.commit()
        manual_id = str(trade.id)
        demo_id = str(demo_test.id)

    assert (
        test_client.get(f"/journal/trades/{manual_id}", headers=headers_other_user).status_code
        == 404
    )
    assert (
        test_client.get(f"/journal/trades/{demo_id}", headers=headers_other_user).status_code == 404
    )

    for trade_id in (manual_id, demo_id):
        detail = test_client.get(f"/journal/trades/{trade_id}", headers=headers_owner)
        assert detail.status_code == 200, detail.text
        assert detail.json()["manual_demo"] is None

        tagged = test_client.patch(
            f"/journal/trades/{trade_id}/annotation",
            json={"strategy_label": "Breakout", "grade": "B+"},
            headers=headers_owner,
        )
        assert tagged.status_code == 200, tagged.text
        assert tagged.json()["strategy_label"] == "Breakout"
        assert tagged.json()["grade"] == "B+"

        denied_org = test_client.patch(
            f"/journal/trades/{trade_id}/annotation",
            json={"grade": "F"},
            headers=headers_other_org,
        )
        assert denied_org.status_code == 404

        denied_user = test_client.patch(
            f"/journal/trades/{trade_id}/annotation",
            json={"grade": "F"},
            headers=headers_other_user,
        )
        assert denied_user.status_code == 404
        with factory() as session:
            row = session.get(JournalTrade, uuid.UUID(trade_id))
            assert row is not None
            assert row.strategy_label == "Breakout"
            assert row.grade == "B+"


def test_children_cross_org_fails_closed(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, _ = client
    headers_a = _auth(test_client, "journal-a@test.example")
    headers_b = _auth(test_client, "journal-b@test.example")
    trade_id = test_client.post(
        "/journal/trades", json=_manual_trade_body(), headers=headers_a
    ).json()["id"]

    denied = test_client.post(
        f"/journal/trades/{trade_id}/observations",
        json={"category": "behavioral", "observation": "should not exist"},
        headers=headers_b,
    )
    assert denied.status_code == 404


def test_journal_trade_child_routes_require_owner(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    _seed_other_trader_in_org_a(factory)
    headers_owner = _auth(test_client, "journal-a@test.example")
    headers_other_org = _auth(test_client, "journal-b@test.example")
    headers_other_user = _auth(test_client, "journal-other-trader-a@test.example")
    trade_id = test_client.post(
        "/journal/trades", json=_manual_trade_body(), headers=headers_owner
    ).json()["id"]

    assert test_client.get(f"/journal/trades/{trade_id}", headers=headers_owner).status_code == 200
    assert (
        test_client.get(f"/journal/trades/{trade_id}", headers=headers_other_org).status_code == 404
    )
    assert (
        test_client.get(f"/journal/trades/{trade_id}", headers=headers_other_user).status_code
        == 404
    )

    write_cases = (
        (
            "evidence",
            f"/journal/trades/{trade_id}/evidence",
            {"kind": "note", "caption": "intruder"},
        ),
        (
            "rule_check",
            f"/journal/trades/{trade_id}/rule-checks",
            {
                "rule_key": "stop_loss_placed_before_entry",
                "rule_source": "strategy_version",
                "status": "followed",
            },
        ),
        (
            "observation",
            f"/journal/trades/{trade_id}/observations",
            {"category": "behavioral", "observation": "intruder"},
        ),
    )
    for _label, path, payload in write_cases:
        assert test_client.post(path, json=payload, headers=headers_owner).status_code == 201
        denied_org = test_client.post(path, json=payload, headers=headers_other_org)
        assert denied_org.status_code == 404
        denied_user = test_client.post(path, json=payload, headers=headers_other_user)
        assert denied_user.status_code == 404

    with factory() as session:
        assert len(session.scalars(select(JournalTradeEvidence)).all()) == 1
        assert len(session.scalars(select(JournalTradeRuleCheck)).all()) == 1
        assert len(session.scalars(select(JournalTradeObservation)).all()) == 1
