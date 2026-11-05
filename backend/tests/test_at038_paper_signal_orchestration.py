"""AT-038 — Automated paper-signal orchestration (paper-only)."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    KillSwitchState,
    Membership,
    Organization,
    PaperSignalOrchestrationDecision,
    PaperValidationCandidate,
    Position,
    TradeProposal,
    TradingViewSignal,
    User,
    UserStrategy,
    UserStrategyVersion,
)
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import (
    MembershipRole,
    PaperSignalOrchestrationStatus,
    PositionStatus,
    StrategyId,
    TradeDirection,
    TradingViewSignalStatus,
)
from app.schemas.paper_signal_orchestration import APPROVE_PAPER_SIGNAL_PROPOSAL_CONFIRM
from app.security.tradingview_webhook import compute_tradingview_signature
from app.services.execution_service import ExecutionService

ORG_A = uuid.UUID("00000000-0000-0000-0000-000000038101")
ORG_B = uuid.UUID("00000000-0000-0000-0000-000000038102")
USER_A = uuid.UUID("00000000-0000-0000-0000-000000038111")
USER_B = uuid.UUID("00000000-0000-0000-0000-000000038112")
VIEWER_A = uuid.UUID("00000000-0000-0000-0000-000000038113")
TRADER_A = uuid.UUID("00000000-0000-0000-0000-000000038114")
STRATEGY_A = uuid.UUID("00000000-0000-0000-0000-000000038121")
VERSION_A = uuid.UUID("00000000-0000-0000-0000-000000038122")

WEBHOOK_SECRET = "at038-tradingview-webhook-secret-32chars"

_BASE: dict[str, Any] = {
    "environment": "local",
    "log_json": False,
    "execution_mode": "paper",
    "enable_real_trading": False,
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "at038-paper-signal-orch-secret-32xxxx",
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
    "paper_signal_orchestration_enabled": True,
    "paper_signal_orchestration_mode": "observe_only",
    "paper_signal_max_age_seconds": 900,
    "paper_signal_min_confidence": 0.0,
    "paper_signal_require_setup_when_named": True,
    "paper_signal_require_strategy_when_provided": True,
    "paper_signal_cooldown_after_loss_seconds": 3600,
    "paper_signal_conflict_window_seconds": 3600,
    "paper_signal_create_run_plan": True,
}

_CARD: dict[str, Any] = {
    "strategy_name": "AT038 TV",
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
        session.add(Organization(id=ORG_A, name="AT038 Org A"))
        session.add(Organization(id=ORG_B, name="AT038 Org B"))
        for user_id, email in (
            (USER_A, "at038-a@test.example"),
            (USER_B, "at038-b@test.example"),
            (VIEWER_A, "at038-viewer@test.example"),
            (TRADER_A, "at038-trader@test.example"),
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
                name="AT038 Strategy",
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
        "alert_id": f"tv-orch-{uuid.uuid4().hex[:8]}",
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "direction": "long",
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


def _post_webhook(client: TestClient, body: dict[str, Any]) -> Any:
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    timestamp = int(time.time())
    signature = compute_tradingview_signature(raw, timestamp=timestamp, secret=WEBHOOK_SECRET)
    return client.post(
        "/webhooks/tradingview",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-AT-Timestamp": str(timestamp),
            "X-AT-Signature": signature,
        },
    )


def _ingest(client: TestClient, **overrides: Any) -> str:
    response = _post_webhook(client, _payload(**overrides))
    assert response.status_code == 200, response.text
    assert response.json()["signal"]["status"] == TradingViewSignalStatus.VALIDATED.value
    return str(response.json()["signal"]["id"])


def test_observe_only_eligible_no_candidate(
    client: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    test_client, factory, settings = client
    assert settings.paper_signal_orchestration_mode == "observe_only"
    signal_id = _ingest(test_client, alert_id="tv-observe-1")
    headers = _login(test_client, "at038-trader@test.example")

    response = test_client.post(
        f"/paper-signal-orchestration/signals/{signal_id}/orchestrate",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"]["status"] == PaperSignalOrchestrationStatus.ELIGIBLE.value
    assert body["decision"]["links"]["candidate_id"] is None

    with factory() as session:
        assert session.scalars(select(PaperValidationCandidate)).first() is None
        assert session.scalars(select(TradeProposal)).first() is None


def test_idempotent_orchestrate_converges(
    client: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    test_client, factory, _settings = client
    signal_id = _ingest(test_client, alert_id="tv-idem-orch")
    headers = _login(test_client, "at038-trader@test.example")

    first = test_client.post(
        f"/paper-signal-orchestration/signals/{signal_id}/orchestrate",
        headers=headers,
    )
    second = test_client.post(
        f"/paper-signal-orchestration/signals/{signal_id}/orchestrate",
        headers=headers,
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["decision"]["id"] == second.json()["decision"]["id"]

    with factory() as session:
        rows = session.scalars(select(PaperSignalOrchestrationDecision)).all()
        assert len(rows) == 1


def test_stale_and_conflicting_signal_rejection(
    client: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    test_client, factory, _settings = client
    headers = _login(test_client, "at038-trader@test.example")

    stale_id = _ingest(test_client, alert_id="tv-stale-1")
    with factory() as session:
        row = session.get(TradingViewSignal, uuid.UUID(stale_id))
        assert row is not None
        row.occurred_at = datetime.now(UTC) - timedelta(hours=2)
        session.commit()

    stale = test_client.post(
        f"/paper-signal-orchestration/signals/{stale_id}/evaluate",
        headers=headers,
    )
    assert stale.status_code == 200
    assert stale.json()["decision"]["status"] == PaperSignalOrchestrationStatus.EXPIRED.value
    assert "signal_stale" in (stale.json()["decision"]["reason_codes"] or [])

    long_id = _ingest(test_client, alert_id="tv-conflict-long", direction="long")
    _ingest(test_client, alert_id="tv-conflict-short", direction="short")
    conflict = test_client.post(
        f"/paper-signal-orchestration/signals/{long_id}/evaluate",
        headers=headers,
    )
    assert conflict.status_code == 200
    assert conflict.json()["decision"]["status"] == PaperSignalOrchestrationStatus.BLOCKED.value
    assert "conflicting_signal" in (conflict.json()["decision"]["reason_codes"] or [])

    bad_levels = _ingest(
        test_client,
        alert_id="tv-bad-levels",
        direction="long",
        trigger_level=65000.0,
        stop_loss_level=66000.0,
        invalidation_level=66000.0,
        take_profit_level=67000.0,
    )
    rejected = test_client.post(
        f"/paper-signal-orchestration/signals/{bad_levels}/evaluate",
        headers=headers,
    )
    assert rejected.status_code == 200
    assert rejected.json()["decision"]["status"] == PaperSignalOrchestrationStatus.REJECTED.value
    assert "levels_contradictory" in (rejected.json()["decision"]["reason_codes"] or [])


def test_tenant_isolation_and_rbac(
    client: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    test_client, _factory, _settings = client
    signal_id = _ingest(test_client, alert_id="tv-rbac-1")
    trader = _login(test_client, "at038-trader@test.example")
    viewer = _login(test_client, "at038-viewer@test.example")
    owner_b = _login(test_client, "at038-b@test.example")

    created = test_client.post(
        f"/paper-signal-orchestration/signals/{signal_id}/evaluate",
        headers=trader,
    )
    assert created.status_code == 200
    decision_id = created.json()["decision"]["id"]

    listed = test_client.get("/paper-signal-orchestration/decisions", headers=viewer)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    mutate = test_client.post(
        f"/paper-signal-orchestration/signals/{signal_id}/orchestrate",
        headers=viewer,
    )
    assert mutate.status_code == 403

    cross = test_client.get(
        f"/paper-signal-orchestration/decisions/{decision_id}",
        headers=owner_b,
    )
    assert cross.status_code == 404


def test_kill_switch_blocks_and_recent_loss_does_not(
    client: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    test_client, factory, _settings = client
    headers = _login(test_client, "at038-trader@test.example")
    signal_id = _ingest(test_client, alert_id="tv-kill-1")

    with factory() as session:
        session.add(
            KillSwitchState(
                organization_id=ORG_A,
                active=True,
                reason="test kill",
                activated_by=USER_A,
                activated_at=datetime.now(UTC),
            )
        )
        session.commit()

    blocked = test_client.post(
        f"/paper-signal-orchestration/signals/{signal_id}/evaluate",
        headers=headers,
    )
    assert blocked.status_code == 200
    assert blocked.json()["decision"]["status"] == PaperSignalOrchestrationStatus.BLOCKED.value
    assert "kill_switch" in (blocked.json()["decision"]["reason_codes"] or [])

    with factory() as session:
        row = session.scalars(select(KillSwitchState)).first()
        assert row is not None
        row.active = False
        session.add(
            Position(
                organization_id=ORG_A,
                user_id=TRADER_A,
                symbol="BTCUSDT",
                direction=TradeDirection.LONG,
                size=1,
                entry_price=100,
                leverage=1,
                realized_pnl=-10,
                status=PositionStatus.CLOSED,
                opened_at=datetime.now(UTC) - timedelta(minutes=30),
                closed_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        )
        session.commit()

    # A losing close 5 minutes ago is inside the 3600 s window, but the
    # after-loss cooldown is advisory only (AT-ADR-152, R6) and never blocks.
    signal2 = _ingest(test_client, alert_id="tv-cooldown-1")
    cooldown = test_client.post(
        f"/paper-signal-orchestration/signals/{signal2}/evaluate",
        headers=headers,
    )
    assert cooldown.status_code == 200
    decision = cooldown.json()["decision"]
    assert decision["status"] == PaperSignalOrchestrationStatus.ELIGIBLE.value
    assert "cooldown_active" not in (decision["reason_codes"] or [])
    check = next(c for c in decision["risk_checks"] if c["code"] == "cooldown_clear")
    assert check["passed"] is True
    assert "advisory only" in check["detail"]


def test_candidate_only_and_approval_required_modes(
    client: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    test_client, factory, settings = client
    headers = _login(test_client, "at038-trader@test.example")

    settings.paper_signal_orchestration_mode = "candidate_only"
    signal_id = _ingest(test_client, alert_id="tv-cand-only")
    cand = test_client.post(
        f"/paper-signal-orchestration/signals/{signal_id}/orchestrate",
        headers=headers,
    )
    assert cand.status_code == 200, cand.text
    assert (
        cand.json()["decision"]["status"]
        == PaperSignalOrchestrationStatus.PAPER_CANDIDATE_CREATED.value
    )
    assert cand.json()["decision"]["links"]["candidate_id"] is not None
    assert cand.json()["decision"]["links"]["run_plan_id"] is not None

    # Idempotent second call
    again = test_client.post(
        f"/paper-signal-orchestration/signals/{signal_id}/orchestrate",
        headers=headers,
    )
    assert again.status_code == 200
    assert again.json()["decision"]["id"] == cand.json()["decision"]["id"]

    with factory() as session:
        assert len(session.scalars(select(PaperValidationCandidate)).all()) == 1
        assert session.scalars(select(TradeProposal)).first() is None

    settings.paper_signal_orchestration_mode = "approval_required"
    signal2 = _ingest(test_client, alert_id="tv-approval-1")
    awaiting = test_client.post(
        f"/paper-signal-orchestration/signals/{signal2}/orchestrate",
        headers=headers,
    )
    assert awaiting.status_code == 200, awaiting.text
    assert (
        awaiting.json()["decision"]["status"]
        == PaperSignalOrchestrationStatus.AWAITING_REVIEW.value
    )
    decision_id = awaiting.json()["decision"]["id"]

    bad_confirm = test_client.post(
        f"/paper-signal-orchestration/decisions/{decision_id}/approve-paper-proposal",
        headers=headers,
        json={"confirm": "WRONG"},
    )
    assert bad_confirm.status_code == 422

    approved = test_client.post(
        f"/paper-signal-orchestration/decisions/{decision_id}/approve-paper-proposal",
        headers=headers,
        json={"confirm": APPROVE_PAPER_SIGNAL_PROPOSAL_CONFIRM},
    )
    assert approved.status_code == 200, approved.text
    assert (
        approved.json()["decision"]["status"]
        == PaperSignalOrchestrationStatus.PAPER_PROPOSAL_CREATED.value
    )
    proposal_id = approved.json()["proposal_id"]

    replay = test_client.post(
        f"/paper-signal-orchestration/decisions/{decision_id}/approve-paper-proposal",
        headers=headers,
        json={"confirm": APPROVE_PAPER_SIGNAL_PROPOSAL_CONFIRM},
    )
    assert replay.status_code == 200
    assert replay.json()["already_exists"] is True
    assert replay.json()["proposal_id"] == proposal_id

    with factory() as session:
        proposals = session.scalars(select(TradeProposal)).all()
        assert len(proposals) == 1
        assert proposals[0].approval_required is True


def test_no_live_order_invariant(
    client: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    test_client, _factory, settings = client
    settings.paper_signal_orchestration_mode = "approval_required"
    headers = _login(test_client, "at038-trader@test.example")
    signal_id = _ingest(test_client, alert_id="tv-no-live")
    orch = test_client.post(
        f"/paper-signal-orchestration/signals/{signal_id}/orchestrate",
        headers=headers,
    )
    assert orch.status_code == 200
    decision_id = orch.json()["decision"]["id"]
    approved = test_client.post(
        f"/paper-signal-orchestration/decisions/{decision_id}/approve-paper-proposal",
        headers=headers,
        json={"confirm": APPROVE_PAPER_SIGNAL_PROPOSAL_CONFIRM},
    )
    assert approved.status_code == 200

    with patch.object(ExecutionService, "place_paper_order", MagicMock()) as mock_place:
        # Re-orchestrate / list must never call order placement.
        test_client.post(
            f"/paper-signal-orchestration/signals/{signal_id}/orchestrate",
            headers=headers,
        )
        test_client.get("/paper-signal-orchestration/decisions", headers=headers)
        mock_place.assert_not_called()

    assert settings.execution_mode.value == "paper"
    assert settings.enable_real_trading is False


def test_disabled_fail_closed(
    client: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    test_client, _factory, settings = client
    settings.paper_signal_orchestration_enabled = False
    headers = _login(test_client, "at038-trader@test.example")
    signal_id = _ingest(test_client, alert_id="tv-disabled")
    response = test_client.post(
        f"/paper-signal-orchestration/signals/{signal_id}/evaluate",
        headers=headers,
    )
    assert response.status_code == 503
