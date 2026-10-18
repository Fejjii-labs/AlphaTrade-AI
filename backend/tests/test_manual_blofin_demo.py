"""Real PostgreSQL manual-origin protocol using simulated native venue responses."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.core.config import Environment, Settings
from app.core.errors import ForbiddenError, NotFoundError, TradingPolicyError
from app.db.models import (
    ExecutionAccount,
    ExecutionCommand,
    ExecutionFillFact,
    JournalTrade,
    KillSwitchState,
    Membership,
    Organization,
    RiskReservation,
    TradePlanRevision,
    User,
)
from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.governed_blofin import GovernedBloFinDemoProvider
from app.schemas.common import JournalTradeSource, MembershipRole
from app.schemas.manual_demo import ManualDemoConfirmation, ManualDemoPreviewRequest
from app.security.tenant import TenantContext
from app.services.manual_demo_service import ManualDemoService
from tests.support.phase5_market import EVALUATED_AT
from tests.support.postgres_persistence import phase7_plan_session_factory, requires_postgres
from tests.test_governed_blofin_demo import Venue

pytestmark = requires_postgres


class ManualVenue(Venue):
    cancel_count = 0
    cancelled = False
    open_position = False

    def handle(self, request):
        if (
            request.url.path.endswith("/positions")
            and self.order is not None
            and self.open_position
        ):
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": [
                        {
                            "instId": "BTC-USDT",
                            "positionSide": "net",
                            "marginMode": "cross",
                            "positions": str(self.order["size"]),
                        }
                    ],
                },
            )
        if request.url.path.endswith("orders-tpsl-history"):
            return httpx.Response(200, json={"code": "0", "data": []})
        if request.url.path.endswith("orders-history"):
            if self.order is None:
                data = []
            else:
                detail_request = httpx.Request(
                    "GET",
                    "https://demo-trading-openapi.blofin.com/api/v1/trade/order-detail",
                    params={"clientOrderId": self.order["clientOrderId"]},
                )
                data = self.handle(detail_request).json()["data"]
            return httpx.Response(200, json={"code": "0", "data": data})
        if request.url.path.endswith("cancel-order"):
            assert request.method == "POST"
            self.cancel_count += 1
            self.cancelled = True
            return httpx.Response(200, json={"code": "0", "data": [{"orderId": "demo-1"}]})
        result = super().handle(request)
        if request.url.path.endswith("/books"):
            content = result.json()
            content["data"][0].update(
                asks=[[str(self.price), "1000000"]],
                bids=[[str(self.price - Decimal("0.1")), "1000000"]],
            )
            return httpx.Response(200, json=content)
        if self.cancelled and request.url.path.endswith("order-detail"):
            content = result.json()
            for row in content["data"]:
                row["state"] = "canceled"
            return httpx.Response(200, json=content)
        return result


@pytest.fixture
def world():
    factory = phase7_plan_session_factory()
    tenant = TenantContext(uuid4(), uuid4(), "manual-owner@example.com", MembershipRole.OWNER)
    account_id = uuid4()
    with factory() as session:
        session.add(Organization(id=tenant.organization_id, name="Manual demo test"))
        session.add(User(id=tenant.user_id, email=tenant.email, hashed_password="not-a-real-hash"))
        session.flush()
        session.add(
            Membership(
                organization_id=tenant.organization_id,
                user_id=tenant.user_id,
                role=MembershipRole.OWNER,
            )
        )
        session.add(
            ExecutionAccount(
                id=account_id,
                organization_id=tenant.organization_id,
                user_id=tenant.user_id,
                name="Paper identity",
                execution_mode="PAPER",
                account_mode="NET",
                enabled=True,
            )
        )
        session.commit()
    settings = Settings(
        _env_file=None,
        environment="local",
        execution_mode="paper",
        provider_mode="mock",
        exchange_mode="paper_exchange_demo",
        blofin_demo_enabled=True,
        manual_blofin_demo_enabled=True,
        governed_blofin_demo_enabled=False,
        watcher_orchestration_enabled=False,
        watcher_paper_staging_activation=False,
        enable_real_trading=False,
        global_kill_switch_active=False,
        blofin_api_key="dummy-key",
        blofin_api_secret="dummy-secret",
        blofin_api_passphrase="dummy-passphrase",
        blofin_demo_rest_base_url="https://demo-trading-openapi.blofin.com",
        governed_blofin_demo_organization_id=str(tenant.organization_id),
        governed_blofin_demo_user_id=str(tenant.user_id),
        governed_blofin_demo_account_id=str(account_id),
    ).model_copy(
        update={"environment": Environment.STAGING, "perpetual_evidence_source": "binance_usdm"}
    )
    venue = ManualVenue(Decimal("100000"))
    provider = GovernedBloFinDemoProvider(
        BloFinClient(
            base_url=settings.blofin_demo_rest_base_url,
            api_key="dummy",
            api_secret="dummy",
            api_passphrase="dummy",
            transport=httpx.MockTransport(venue.handle),
            sleeper=lambda _: None,
            max_retries=0,
        ),
        clock=lambda: EVALUATED_AT + timedelta(seconds=venue.clock_delta),
    )
    return factory, tenant, settings, venue, provider


def service(world, session):
    return ManualDemoService(
        session,
        world[2],
        provider=world[4],
        clock=lambda: EVALUATED_AT + timedelta(seconds=world[3].clock_delta),
    )


def preview(world, side="BUY"):
    request = ManualDemoPreviewRequest(
        side=side,
        quantity="2",
        stop="99000" if side == "BUY" else "101000",
        target="102000" if side == "BUY" else "98000",
    )
    with world[0]() as session:
        result = service(world, session).preview(world[1], request)
    assert world[3].post_count == 0
    return result


def confirmation(plan):
    return ManualDemoConfirmation(
        revision_id=plan.revision_id,
        content_hash=plan.content_hash,
        confirm=True,
        label="manual demo test",
    )


def confirm(world, plan):
    with world[0]() as session:
        return service(world, session).confirm(world[1], confirmation(plan))


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_market_fill_protection_provenance_and_repeated_confirmation(world, side):
    plan = preview(world, side)
    result = confirm(world, plan)
    assert result.status == "filled_protected"
    assert result.filled_quantity == Decimal("2")
    assert result.average_fill_price == Decimal("100000")
    assert result.fees == Decimal("0.02")
    assert result.protection == "verified"
    assert result.venue_order_id == "demo-1"
    assert result.protection_order_ids == ("protect-1",)
    assert confirm(world, plan).command_id == result.command_id
    assert world[3].post_count == 1
    with world[0]() as session:
        row = session.get(TradePlanRevision, plan.revision_id)
        assert row.plan_authority == "manual_demo_test"
        assert row.strategy_version_id is row.candidate_id is row.setup_definition_id is None
        assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 1
        trade = session.get(JournalTrade, result.journal_trade_id)
        assert trade.source == JournalTradeSource.MANUAL_DEMO_TEST
        assert trade.strategy_version_id is trade.candidate_id is trade.assessment_id is None
        assert trade.exchange == "BLOFIN_DEMO"
        assert trade.planned_targets[0]["price"] == str(plan.target)
        assert trade.funding is trade.net_pnl is trade.exit_price is None
        from app.db.learning_attribution import LearningAttributionRecordRow

        assert session.scalar(select(func.count()).select_from(LearningAttributionRecordRow)) == 0


def test_wrong_confirmation_never_dispatches(world):
    plan = preview(world)
    with world[0]() as session, pytest.raises(TradingPolicyError, match="exact preview hash"):
        service(world, session).confirm(
            world[1], confirmation(plan).model_copy(update={"content_hash": "f" * 64})
        )
    with pytest.raises(ValidationError):
        ManualDemoConfirmation(
            revision_id=plan.revision_id,
            content_hash=plan.content_hash,
            confirm=False,
            label="manual demo test",
        )
    with world[0]() as session:
        assert session.scalar(select(func.count()).select_from(ExecutionCommand)) == 0
    assert world[3].post_count == 0


@pytest.mark.parametrize("behavior", ["restart", "timeout"])
def test_uncertain_post_and_restart_only_read_reconcile(world, behavior):
    plan = preview(world)
    world[3].behavior = behavior
    first = confirm(world, plan)
    second = confirm(world, plan)
    assert second.command_id == first.command_id
    assert second.status == "filled_protected"
    assert world[3].post_count == 1


@pytest.mark.parametrize(
    "behavior", ["protection_failure", "protection_outage", "protection_wrongid"]
)
def test_fill_survives_failed_protection_and_holds(world, behavior):
    world[3].open_position = True
    plan = preview(world)
    world[3].behavior = behavior
    result = confirm(world, plan)
    assert result.status == "protection_failed_operator_hold"
    assert result.filled_quantity == Decimal("2")
    assert result.journal_trade_id is not None
    with world[0]() as session:
        assert session.scalar(select(KillSwitchState.active))
        assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 1
    assert world[3].post_count == 1


@pytest.mark.parametrize("behavior", ["unfilled", "partial"])
def test_cancel_remainder_preserves_partial_fills_and_never_retries(world, behavior):
    plan = preview(world)
    world[3].behavior = behavior
    first = confirm(world, plan)
    assert first.filled_quantity == (Decimal("1") if behavior == "partial" else 0)
    with world[0]() as session:
        result = service(world, session).cancel(world[1], first.command_id)
    with world[0]() as session:
        service(world, session).cancel(world[1], first.command_id)
        reservation = session.scalar(select(RiskReservation))
        assert reservation.remaining_reserved_notional == 0
        assert result.remaining_quantity == 0
        assert result.filled_quantity == first.filled_quantity
        assert bool(result.journal_trade_id) == (behavior == "partial")
    assert world[3].cancel_count == world[3].post_count == 1


def test_concurrent_confirmation_has_one_post(world):
    plan = preview(world)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: confirm(world, plan), range(2)))
    assert len({r.command_id for r in results}) == 1
    assert world[3].post_count == 1


def test_competing_manual_plans_share_one_account_reservation(world):
    plans = [preview(world), preview(world, "SELL")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda p: confirm(world, p), plans))
    assert sum(r.status == "filled_protected" for r in results) == 1
    assert sum(r.status == "demo_account_already_claimed" for r in results) == 1
    assert world[3].post_count == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("enable_real_trading", True),
        ("manual_blofin_demo_enabled", False),
        ("blofin_demo_rest_base_url", "https://openapi.blofin.com"),
        ("governed_blofin_demo_account_id", str(uuid4())),
    ],
)
def test_unsafe_posture_or_wrong_account_refuses_before_io(world, field, value):
    factory, tenant, settings, venue, provider = world
    with factory() as session, pytest.raises(TradingPolicyError):
        ManualDemoService(
            session, settings.model_copy(update={field: value}), provider=provider
        ).preview(
            tenant,
            ManualDemoPreviewRequest(side="BUY", quantity="2", stop="99000", target="102000"),
        )
    assert venue.ticker_reads == venue.post_count == 0


def test_tenant_user_isolation_and_owner_scope(world):
    plan = preview(world)
    for tenant in (
        replace(world[1], organization_id=uuid4()),
        replace(world[1], user_id=uuid4()),
        replace(world[1], membership_role=MembershipRole.TRADER),
    ):
        with (
            world[0]() as session,
            pytest.raises((TradingPolicyError, NotFoundError, ForbiddenError)),
        ):
            service(world, session).confirm(tenant, confirmation(plan))
    assert world[3].post_count == 0


def test_invalid_tick_and_stale_preview(world):
    for request in (
        ManualDemoPreviewRequest(side="BUY", quantity="2.5", stop="99000", target="102000"),
    ):
        with world[0]() as session, pytest.raises((ValueError, TradingPolicyError)):
            service(world, session).preview(world[1], request)
    world[3].behavior = "stale"
    with world[0]() as session, pytest.raises(TradingPolicyError, match="at least 10 seconds old"):
        service(world, session).preview(
            world[1],
            ManualDemoPreviewRequest(side="BUY", quantity="2", stop="99000", target="102000"),
        )
    assert world[3].post_count == 0


def test_below_one_r_manual_connectivity_has_exact_confirmation_and_protection(world):
    with world[0]() as session:
        plan = service(world, session).preview(
            world[1],
            ManualDemoPreviewRequest(side="BUY", quantity="2", stop="99000", target="100500"),
        )
    assert 0 < plan.gross_reward_risk < 1
    assert any("minimum 1R do not apply" in warning for warning in plan.warnings)
    assert world[3].post_count == 0
    result = confirm(world, plan)
    assert result.status == "filled_protected"
    assert result.protection == "verified"
    assert confirm(world, plan).command_id == result.command_id
    assert world[3].post_count == 1
    with world[0]() as session:
        trade = session.get(JournalTrade, result.journal_trade_id)
        assert trade.source is JournalTradeSource.MANUAL_DEMO_TEST
        assert trade.strategy_version_id is trade.candidate_id is trade.assessment_id is None


@pytest.mark.parametrize(
    "quantity,stop,reason",
    [
        ("2", "50000", "manual_demo_per_trade_risk_limit"),
        ("6", "99800", "manual_demo_notional_limit"),
    ],
)
def test_risk_refusal_includes_calculated_r_and_precise_reason(world, quantity, stop, reason):
    with world[0]() as session, pytest.raises(TradingPolicyError) as caught:
        service(world, session).preview(
            world[1],
            ManualDemoPreviewRequest(side="BUY", quantity=quantity, stop=stop, target="100500"),
        )
    assert caught.value.details["reason"] == reason
    assert Decimal(caught.value.details["gross_reward_risk"]) > 0
    assert world[3].post_count == 0


def test_expired_plan_never_dispatches(world):
    plan = preview(world)
    world[3].clock_delta = 61
    from app.core.errors import ValidationAppError

    with pytest.raises(ValidationAppError, match=r"expiry|expired"):
        confirm(world, plan)
    assert world[3].post_count == 0


def test_authenticated_api_preview_confirm_is_scoped_and_rejects_client_identity(
    world, monkeypatch
):
    from fastapi.testclient import TestClient

    from app.api.routes import execution
    from app.db.session import get_session
    from app.main import create_app
    from app.security.tokens import create_access_token

    safe = Settings(
        _env_file=None,
        environment="local",
        provider_mode="mock",
        execution_mode="paper",
        enable_real_trading=False,
        rate_limit_use_redis=False,
        market_data_cache_use_redis=False,
        access_token_denylist_use_redis=False,
        require_email_verified=False,
    )
    app = create_app(settings=safe)

    def sessions():
        with world[0]() as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    # Real bearer authentication and database memberships remain in place.
    monkeypatch.setattr(
        execution, "ManualDemoService", lambda session, settings: service(world, session)
    )
    token, _ = create_access_token(
        user_id=world[1].user_id,
        organization_id=world[1].organization_id,
        email=world[1].email,
        settings=safe,
    )
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"side": "BUY", "quantity": "2", "stop": "99000", "target": "102000"}
    with TestClient(app) as client:
        assert client.get("/execution/manual-demo/instrument").status_code == 401
        metadata = client.get("/execution/manual-demo/instrument", headers=headers)
        assert metadata.status_code == 200, metadata.text
        assert metadata.json()["quantity_unit"] == "CONTRACTS"
        assert metadata.json()["lot_increment"] == "1"
        assert metadata.json()["contract_multiplier"] == "0.001"
        assert world[3].post_count == 0
        assert client.post("/execution/manual-demo/preview", json=payload).status_code == 401
        assert (
            client.post(
                "/execution/manual-demo/preview",
                json={**payload, "user_id": str(uuid4())},
                headers=headers,
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/execution/manual-demo/preview",
                json={**payload, "order_type": "LIMIT"},
                headers=headers,
            ).status_code
            == 422
        )
        result = client.post("/execution/manual-demo/preview", json=payload, headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()["origin"] == "manual demo test"
        assert world[3].post_count == 0
        body = {
            "revision_id": result.json()["revision_id"],
            "content_hash": result.json()["content_hash"],
            "confirm": True,
            "label": "manual demo test",
        }
        assert (
            client.post(
                "/execution/manual-demo/confirm", json={**body, "confirm": False}, headers=headers
            ).status_code
            == 422
        )
        response = client.post("/execution/manual-demo/confirm", json=body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "filled_protected"
        with world[0]() as session:
            membership = session.scalar(select(Membership))
            membership.role = MembershipRole.TRADER
            session.commit()
        assert (
            client.post("/execution/manual-demo/confirm", json=body, headers=headers).status_code
            == 403
        )
    assert world[3].post_count == 1


def test_generic_proposal_approval_and_execution_cannot_bypass_manual_confirmation(world):
    from app.repositories.proposals import ProposalRepository
    from app.schemas.execution_protocol import ExecutePaperPlanRequest
    from app.schemas.trade_plan import AuthorizationChannel, AuthorizationDecision
    from app.services.approval_service import ApprovalService
    from app.services.audit_service import AuditService
    from app.services.execution_service import ExecutionService

    plan = preview(world)
    with world[0]() as session:
        row = session.get(TradePlanRevision, plan.revision_id)
        assert (
            ProposalRepository(session).list_proposals(organization_id=world[1].organization_id)[1]
            == 0
        )
        approval = ApprovalService(session, AuditService(session), clock=lambda: EVALUATED_AT)
        pending = approval.create_for_plan_revision(
            revision_id=plan.revision_id,
            organization_id=world[1].organization_id,
            user_id=world[1].user_id,
        )
        with pytest.raises(TradingPolicyError, match="exact-plan confirmation"):
            approval.issue_authorization(
                approval_id=pending.id,
                decision=AuthorizationDecision.APPROVE,
                organization_id=world[1].organization_id,
                user_id=world[1].user_id,
                channel=AuthorizationChannel.API,
            )
        with pytest.raises(TradingPolicyError, match="explicit preview/confirmation"):
            ExecutionService(session, world[2], AuditService(session)).execute_paper_plan(
                ExecutePaperPlanRequest(
                    organization_id=world[1].organization_id,
                    user_id=world[1].user_id,
                    account_id=row.account_id,
                    authorization_id=uuid4(),
                    revision_id=row.id,
                    idempotency_key="bypass",
                )
            )
    assert world[3].post_count == 0


def test_kill_switch_during_final_preflight_never_posts(world, monkeypatch):
    plan = preview(world)

    def activate():
        with world[0]() as session:
            from app.schemas.risk import KillSwitchMutationRequest
            from app.services.audit_service import AuditService
            from app.services.risk.kill_switch import KillSwitchService

            KillSwitchService(session, AuditService(session), world[2]).activate(
                organization_id=world[1].organization_id,
                actor_user_id=world[1].user_id,
                payload=KillSwitchMutationRequest(confirm=True, reason="Test final gate"),
            )
            session.commit()

    original = world[3].handle

    def final_preflight(request):
        result = original(request)
        if request.url.path.endswith("books") and world[3].ticker_reads == 3:
            activate()
        return result

    monkeypatch.setattr(world[4]._client._transport, "handler", final_preflight)
    result = confirm(world, plan)
    assert result.filled_quantity == 0
    assert world[3].post_count == 0


def test_decimal_budget_unsupported_market_and_limit_refusal():
    payload = {"side": "BUY", "quantity": "2", "stop": "99000", "target": "102000"}
    for changes in (
        {"symbol": "ETHUSDT"},
        {"order_type": "LIMIT"},
        {"quantity": "1e999999"},
        {"stop": "9" * 100},
        {"target": "NaN"},
    ):
        with pytest.raises(ValidationError):
            ManualDemoPreviewRequest(**{**payload, **changes})


def test_downgrade_refuses_preserved_manual_history(world):
    from importlib import import_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    preview(world)
    migration = import_module("app.db.migrations.versions.a6manualdemo001_manual_demo_origin")
    with (
        world[0]() as session,
        Operations.context(MigrationContext.configure(session.connection())),
        pytest.raises(RuntimeError, match="history exists"),
    ):
        migration.downgrade()


def test_manual_actual_position_still_counts_for_daily_risk(world):
    from app.services.audit_service import AuditService
    from app.services.risk.daily_risk_accounting import DailyRiskAccounting
    from app.services.risk.settings_service import RiskSettingsService

    result = confirm(world, preview(world))
    with world[0]() as session:
        snapshot = DailyRiskAccounting(
            session, RiskSettingsService(session, AuditService(session)), clock=lambda: EVALUATED_AT
        ).sync_from_portfolio(organization_id=world[1].organization_id, user_id=world[1].user_id)
        assert snapshot.trade_count == 1
        assert snapshot.open_exposure_notional == Decimal("200")
        assert result.status == "filled_protected"


def test_uncertain_cancellation_is_never_posted_again(world, monkeypatch):
    plan = preview(world)
    world[3].behavior = "unfilled"
    first = confirm(world, plan)
    calls = []

    def uncertain(**kwargs):
        calls.append(kwargs)
        raise TimeoutError("Cancel response uncertain")

    monkeypatch.setattr(world[4], "cancel_entry", uncertain)
    for _ in range(2):
        with world[0]() as session:
            result = service(world, session).cancel(world[1], first.command_id)
            assert result.remaining_quantity == Decimal("2")
            assert any("Cancellation requested" in message for message in result.missing_evidence)
            assert session.scalar(select(RiskReservation.remaining_reserved_notional)) > 0
    assert len(calls) == 1
    assert world[3].post_count == 1


@pytest.mark.parametrize("open_position", [True, False])
def test_actual_fill_outside_entry_range_is_recorded_and_held(world, open_position):
    world[3].open_position = open_position
    plan = preview(world)
    world[3].price = Decimal("100050")  # Dispatch quote still fits the authorized 10bps.
    original = world[4].reconcile

    def outside(**kwargs):
        evidence = original(**kwargs)
        return replace(
            evidence, fills=tuple(replace(f, price=Decimal("100500")) for f in evidence.fills)
        )

    world[4].reconcile = outside
    result = confirm(world, plan)
    assert result.status == "actual_fill_outside_plan_operator_hold"
    assert result.average_fill_price == Decimal("100500")
    assert result.protection == "verified"
    with world[0]() as session:
        assert bool(session.scalar(select(KillSwitchState.active))) == open_position
        assert session.get(JournalTrade, result.journal_trade_id).entry_price == Decimal("100500")


def test_changed_native_fill_fee_cannot_overwrite_recorded_fee(world):
    plan = preview(world)
    first = confirm(world, plan)
    original = world[4].reconcile

    def conflicting(**kwargs):
        evidence = original(**kwargs)
        return replace(
            evidence, fills=tuple(replace(f, fee=Decimal("0.03")) for f in evidence.fills)
        )

    world[4].reconcile = conflicting
    with world[0]() as session, pytest.raises(TradingPolicyError, match="fee conflicts"):
        service(world, session).reconcile(world[1], first.command_id)
    with world[0]() as session:
        assert session.get(JournalTrade, first.journal_trade_id).fees == Decimal("0.02")


@pytest.mark.parametrize("page_size", [2, 100])
def test_ambiguous_or_bounded_protection_read_keeps_fills_and_holds(world, monkeypatch, page_size):
    world[3].open_position = True
    plan = preview(world)
    original = world[3].handle

    def response(request):
        result = original(request)
        if request.url.path.endswith("orders-tpsl-pending") and world[3].order is not None:
            row = result.json()["data"][0]
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": [{**row, "tpslId": f"protect-{i}"} for i in range(page_size)],
                },
            )
        return result

    monkeypatch.setattr(world[4]._client._transport, "handler", response)
    result = confirm(world, plan)
    assert result.status == "protection_failed_operator_hold"
    assert result.filled_quantity == Decimal("2")
    assert result.protection != "verified"
    assert world[3].post_count == 1
    with world[0]() as session:
        assert session.scalar(select(KillSwitchState.active))


def test_fresh_dispatch_balance_decline_prevents_entry_post(world, monkeypatch):
    plan = preview(world)
    original = world[3].handle

    balances = 0

    def response(request):
        nonlocal balances
        result = original(request)
        if request.url.path.endswith("balance"):
            balances += 1
        if request.url.path.endswith("balance") and balances == 2:
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": [{"currency": "USDT", "balance": "10", "available": "10"}],
                },
            )
        return result

    monkeypatch.setattr(world[4]._client._transport, "handler", response)
    result = confirm(world, plan)
    assert result.filled_quantity == 0
    assert result.protection == "unverified"
    assert world[3].post_count == 0


def response_override(world, monkeypatch, replacements):
    """Native provider fixtures; service and policy decisions remain real."""
    original = world[3].handle

    def handle(request):
        endpoint = request.url.path.rsplit("/", 1)[-1]
        if endpoint in replacements and request.method == "GET":
            return httpx.Response(200, json={"code": "0", "data": replacements[endpoint]})
        return original(request)

    monkeypatch.setattr(world[4]._client._transport, "handler", handle)


def test_internal_paper_exposure_equity_and_discipline_do_not_block_demo(world):
    from app.db.models import AccountRiskAccountingState, DailyRiskState, Position, UserRiskSettings
    from app.schemas.common import PositionStatus, TradeDirection

    with world[0]() as session:
        paper = Position(
            organization_id=world[1].organization_id,
            user_id=world[1].user_id,
            symbol="BTCUSDT",
            direction=TradeDirection.LONG,
            size=Decimal("0.01"),
            entry_price=Decimal("86419"),
            leverage=Decimal("1"),
            unrealized_pnl=Decimal("-20"),
            opened_at=EVALUATED_AT,
            status=PositionStatus.OPEN,
        )
        daily = DailyRiskState(
            organization_id=world[1].organization_id,
            user_id=world[1].user_id,
            day=EVALUATED_AT.date(),
            realized_pnl=Decimal("500"),
            trade_count=100,
            locked=True,
        )
        session.add_all(
            [
                paper,
                daily,
                UserRiskSettings(
                    organization_id=world[1].organization_id,
                    user_id=world[1].user_id,
                    default_account_balance=Decimal("1"),
                    max_trades_per_day=1,
                    max_risk_per_trade_percent=Decimal("0.01"),
                    daily_loss_limit=Decimal("1"),
                    green_day_protection_enabled=True,
                ),
            ]
        )
        account = service(world, session)._scope(world[1])
        accounting = service(world, session).epochs.lock_risk_accounting(
            organization_id=world[1].organization_id,
            account_id=account.id,
            user_id=world[1].user_id,
            exposure_unit="USDT",
        )
        accounting.actual_notional = Decimal("864.19")
        accounting.actual_daily_loss = Decimal("20")
        accounting.actual_trade_count = 100
        accounting.daily_locked = True
        session.commit()
        paper_id, daily_id, accounting_id = paper.id, daily.id, accounting.id
    result = confirm(world, preview(world))
    assert result.status == "filled_protected"
    assert world[3].post_count == 1
    with world[0]() as session:
        assert session.get(Position, paper_id).size == Decimal("0.01")
        unchanged = session.get(DailyRiskState, daily_id)
        assert unchanged.realized_pnl == 500 and unchanged.trade_count == 100 and unchanged.locked
        ledger = session.get(AccountRiskAccountingState, accounting_id)
        assert ledger.actual_notional == Decimal("1064.19")
        assert ledger.actual_trade_count == 101 and ledger.daily_locked
        assert ledger.max_notional == Decimal("1")


@pytest.mark.parametrize("at_confirmation", [False, True])
@pytest.mark.parametrize(
    "endpoint,data,reason",
    [
        ("positions", [{"instId": "ETH-USDT", "positions": "0.5"}], "account_not_flat"),
        (
            "orders-pending",
            [{"instId": "ETH-USDT", "orderId": "pending-venue"}],
            "pending_orders_present",
        ),
        (
            "orders-tpsl-pending",
            [{"instId": "BTC-USDT", "tpslId": "pending-stop"}],
            "pending_orders_present",
        ),
        ("positions", {"incomplete": []}, "account_state_incomplete"),
        ("balance", [], "usdt_balance_unavailable"),
    ],
)
def test_venue_account_capacity_and_unknown_state_fail_closed(
    world, monkeypatch, at_confirmation, endpoint, data, reason
):
    plan = preview(world) if at_confirmation else None
    response_override(world, monkeypatch, {endpoint: data})
    with world[0]() as session, pytest.raises(TradingPolicyError) as caught:
        if plan:
            service(world, session).confirm(world[1], confirmation(plan))
        else:
            service(world, session).preview(
                world[1],
                ManualDemoPreviewRequest(side="BUY", quantity="2", stop="99000", target="102000"),
            )
    assert caught.value.details["preflight"]["reason_code"] == reason
    assert caught.value.details["category"] == "manual_demo_limits"
    assert "demo account" in caught.value.message.lower()
    if plan:
        assert caught.value.details["submission"] == "not_started"
    assert world[3].post_count == 0
    with world[0]() as session:
        assert session.scalar(select(func.count()).select_from(ExecutionCommand)) == 0


@pytest.mark.parametrize("at_confirmation", [False, True])
@pytest.mark.parametrize(
    "balance,available,reason",
    [
        ("10000", "10", "manual_demo_available_funds"),
        ("10000", "1000", "manual_demo_notional_limit"),
    ],
)
def test_preview_and_confirmation_use_same_fresh_venue_limits(
    world, monkeypatch, at_confirmation, balance, available, reason
):
    plan = preview(world) if at_confirmation else None
    response_override(
        world,
        monkeypatch,
        {"balance": [{"currency": "USDT", "balance": balance, "available": available}]},
    )
    with world[0]() as session, pytest.raises(TradingPolicyError) as caught:
        if plan:
            service(world, session).confirm(world[1], confirmation(plan))
        else:
            service(world, session).preview(
                world[1],
                ManualDemoPreviewRequest(side="BUY", quantity="2", stop="99000", target="102000"),
            )
    assert caught.value.details["reason"] == reason
    assert caught.value.details["category"] == "manual_demo_limits"
    assert world[3].post_count == 0


@pytest.mark.parametrize(
    "side,stop,target",
    [
        ("BUY", "100010", "102000"),
        ("BUY", "99000", "100000"),
        ("SELL", "99900", "98000"),
        ("SELL", "101000", "100000"),
    ],
)
def test_invalid_manual_geometry_is_clear_and_never_saved(world, side, stop, target):
    with world[0]() as session, pytest.raises(TradingPolicyError) as caught:
        service(world, session).preview(
            world[1], ManualDemoPreviewRequest(side=side, quantity="2", stop=stop, target=target)
        )
    assert "stop" in caught.value.message.lower() or "target" in caught.value.message.lower()
    assert "1:1" not in caught.value.message
    assert world[3].post_count == 0
    with world[0]() as session:
        assert session.scalar(select(func.count()).select_from(TradePlanRevision)) == 0


def test_fractional_contract_metadata_and_invalid_btc_quantity(world, monkeypatch):
    original = world[3].handle

    def handle(request):
        result = original(request)
        if request.url.path.endswith("instruments"):
            rows = result.json()["data"]
            rows[0].update(lotSize="0.1", minSize="0.1")
            return httpx.Response(200, json={"code": "0", "data": rows})
        return result

    monkeypatch.setattr(world[4]._client._transport, "handler", handle)
    with world[0]() as session:
        metadata = service(world, session).instrument(world[1])
        assert metadata.minimum_quantity == metadata.lot_increment == Decimal("0.1")
        assert metadata.contract_multiplier == Decimal("0.001")
        assert metadata.reference_price == Decimal("100000")
        with pytest.raises(TradingPolicyError) as caught:
            service(world, session).preview(
                world[1],
                ManualDemoPreviewRequest(
                    side="BUY", quantity="0.001", stop="99000", target="100500"
                ),
            )
        assert caught.value.details["preflight"]["reason_code"] == "quote_quantity_invalid"
        plan = service(world, session).preview(
            world[1],
            ManualDemoPreviewRequest(side="BUY", quantity="0.1", stop="99000", target="100500"),
        )
    assert plan.base_quantity == Decimal("0.0001")
    assert plan.gross_reward_risk < 1
    assert confirm(world, plan).status == "filled_protected"
    assert world[3].post_count == 1


def test_protection_recovery_updates_same_journal_and_never_resubmits(world):
    from app.db.models import AuditLog
    from app.schemas.common import AuditEventType

    plan = preview(world)
    world[3].behavior = "unfilled"
    submitted = confirm(world, plan)
    assert submitted.venue_order_id == "demo-1"
    assert submitted.filled_quantity == 0 and submitted.journal_trade_id is None
    with world[0]() as session:
        assert session.scalar(select(func.count()).select_from(JournalTrade)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.resource_id == str(submitted.command_id),
                    AuditLog.action == AuditEventType.EXCHANGE_DEMO_ORDER_CREATED,
                )
            )
            == 1
        )
    world[3].open_position = True
    world[3].behavior = "protection_failure"
    failed = confirm(world, plan)
    assert failed.status == "protection_failed_operator_hold"
    with world[0]() as session:
        assert "protection missing" in session.get(JournalTrade, failed.journal_trade_id).entry_plan
    world[3].behavior = "success"
    recovered = confirm(world, plan)
    assert recovered.status == "filled_protected"
    assert recovered.journal_trade_id == failed.journal_trade_id
    with world[0]() as session:
        assert (
            "protection verified"
            in session.get(JournalTrade, recovered.journal_trade_id).entry_plan
        )
        assert session.scalar(select(KillSwitchState.active))
        assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 1
    assert world[3].post_count == 1


def test_confirmation_stale_quote_cannot_claim_or_submit(world):
    plan = preview(world)
    world[3].behavior = "stale"
    with pytest.raises(TradingPolicyError) as caught:
        confirm(world, plan)
    assert caught.value.details["preflight"]["reason_code"] == "quote_stale_or_future"
    assert caught.value.details["submission"] == "not_started"
    assert world[3].post_count == 0


def test_unknown_post_response_keeps_reservation_and_repeated_confirmation_only_reads(
    world, monkeypatch
):
    original = world[3].handle

    def handle(request):
        if request.method == "POST" and request.url.path.endswith("order"):
            world[3].post_count += 1
            return httpx.Response(200, json={"code": "0", "data": []})
        return original(request)

    monkeypatch.setattr(world[4]._client._transport, "handler", handle)
    plan = preview(world)
    first, second = confirm(world, plan), confirm(world, plan)
    assert first.command_id == second.command_id
    assert second.status == "ambiguous_operator_hold"
    assert second.journal_trade_id is None
    with world[0]() as session:
        assert session.scalar(select(RiskReservation.remaining_reserved_notional)) > 0
        assert session.scalar(select(func.count()).select_from(ExecutionCommand)) == 1
    assert world[3].post_count == 1


def test_local_uncertain_demo_reservation_consumes_preview_capacity(world, monkeypatch):
    first = preview(world)

    def uncertain(**kwargs):
        raise TimeoutError("No reliable submission receipt")

    monkeypatch.setattr(world[4], "submit", uncertain)
    status = confirm(world, first)
    assert status.status == "ambiguous_operator_hold"
    with world[0]() as session, pytest.raises(TradingPolicyError) as caught:
        service(world, session).preview(
            world[1],
            ManualDemoPreviewRequest(side="BUY", quantity="4", stop="99000", target="102000"),
        )
    assert caught.value.details["reason"] == "manual_demo_notional_limit"
    with world[0]() as session:
        reservation = session.scalar(select(RiskReservation))
        # Quote notional at the entry-zone upper (mark * 1.001), plus the existing
        # 0.1% fee allowance and 0.1% slippage allowance: 200.2 + 0.2002 + 0.2002.
        assert reservation.remaining_reserved_notional == Decimal("200.6004")
    assert world[3].post_count == 0


def test_manual_claim_without_fresh_account_policy_cannot_reserve(world):
    from app.schemas.execution_protocol import ExecutePaperPlanRequest
    from app.schemas.trade_plan import AuthorizationChannel, AuthorizationDecision
    from app.services.approval_service import ApprovalService
    from app.services.execution_claim import PaperPlanClaimService

    plan = preview(world)
    with world[0]() as session:
        manual = service(world, session)
        canonical = manual._plan(world[1], plan.revision_id)
        approval = ApprovalService(session, manual.audit, clock=manual.clock)
        pending = approval.create_for_plan_revision(
            revision_id=plan.revision_id,
            organization_id=world[1].organization_id,
            user_id=world[1].user_id,
        )
        authorization = approval.issue_authorization(
            approval_id=pending.id,
            decision=AuthorizationDecision.APPROVE,
            organization_id=world[1].organization_id,
            user_id=world[1].user_id,
            channel=AuthorizationChannel.WEB,
            manual_demo_confirmation_hash=plan.content_hash,
        )
        result = PaperPlanClaimService(session, world[2], manual.epochs, clock=manual.clock).claim(
            ExecutePaperPlanRequest(
                organization_id=world[1].organization_id,
                user_id=world[1].user_id,
                account_id=canonical.account_id,
                revision_id=plan.revision_id,
                authorization_id=authorization.authorization_id,
                idempotency_key="missing-evidence",
            )
        )
        assert result.blocked_reason_code == "manual_demo_account_evidence_required"
        assert session.scalar(select(func.count()).select_from(RiskReservation)) == 0
    assert world[3].post_count == 0


def test_fresh_quote_cannot_mask_stale_account_reads(world, monkeypatch):
    original = world[3].handle

    def handle(request):
        result = original(request)
        if request.url.path.endswith("books"):
            world[3].clock_delta = 11
            content = result.json()
            content["data"][0]["ts"] = str(
                int((EVALUATED_AT + timedelta(seconds=11)).timestamp() * 1000)
            )
            return httpx.Response(200, json=content)
        return result

    monkeypatch.setattr(world[4]._client._transport, "handler", handle)
    with world[0]() as session, pytest.raises(TradingPolicyError) as caught:
        service(world, session).preview(
            world[1],
            ManualDemoPreviewRequest(side="BUY", quantity="2", stop="99000", target="102000"),
        )
    assert caught.value.details["reason"] == "manual_demo_account_state_stale"
    assert world[3].post_count == 0
