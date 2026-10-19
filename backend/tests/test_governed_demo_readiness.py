"""First-entry readiness: simulated reads and disposable PostgreSQL claims."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.db.models import AccountRiskAccountingState, ExecutionCommand
from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.governed_blofin import GovernedBloFinDemoProvider
from app.schemas.execution_protocol import ExecutionCommandOutcome
from app.schemas.trade_plan import EntrySide
from app.services.audit_service import AuditService
from app.services.demo_account_history import has_demo_entry_history
from app.services.execution_service import ExecutionService
from app.services.governed_blofin_demo import DEMO_POLICY, GovernedBloFinDemoLoop
from tests.support.phase1_plan_fixtures import (
    EXECUTE_AT,
    approve_plan,
    execute_request,
    paper_settings,
    persist_plan,
    plan_request,
    prepared_authorized_plan,
)
from tests.support.phase5_market import EVALUATED_AT
from tests.support.postgres_persistence import phase7_plan_session_factory, requires_postgres
from tests.test_governed_blofin_demo import Venue

MARKETS = ("BTCUSDT", "ETHUSDT", "ZECUSDT", "TAOUSDT", "HYPEUSDT")


@pytest.mark.parametrize(
    "venue,policy", [("PAPER_INTERNAL", DEMO_POLICY), ("BLOFIN_DEMO", "legacy-demo/v1")]
)
def test_existing_plan_cannot_acquire_authorization_for_another_venue(monkeypatch, venue, policy):
    revision_id = uuid4()
    target = SimpleNamespace(organization_id=uuid4(), user_id=uuid4())
    candidate = SimpleNamespace(candidate_id=uuid4())
    envelope = SimpleNamespace(
        plan=SimpleNamespace(execution_venue=venue, execution_policy_version=policy)
    )
    plans = SimpleNamespace(get_scoped=lambda *args, **kwargs: envelope)
    monkeypatch.setattr(GovernedBloFinDemoLoop, "_plan_service", lambda _: plans)
    loop = GovernedBloFinDemoLoop(None, None, SimpleNamespace(now=lambda: EXECUTE_AT))  # type: ignore[arg-type]
    # No Session or provider is needed: refusal precedes any authorization or claim.
    proof = loop._execute_existing(
        None, target=target, candidate=candidate, revision_id=revision_id, account_id=uuid4()
    )
    assert proof.reason_code == "demo_existing_plan_venue_mismatch"
    assert proof.trade_plan_revision_id == revision_id


def provider_for(handler):
    client = BloFinClient(
        base_url="https://demo-trading-openapi.blofin.com",
        api_key="simulated-key",
        api_secret="simulated-secret",
        api_passphrase="simulated-pass",
        transport=httpx.MockTransport(handler),
        sleeper=lambda _: None,
    )
    return GovernedBloFinDemoProvider(client, clock=lambda: EVALUATED_AT)


@pytest.mark.parametrize("symbol", MARKETS)
def test_exact_market_mapping_and_venue_units(symbol):
    venue = Venue(Decimal("100000"))
    instrument = f"{symbol[:-4]}-USDT"

    def handler(request):
        assert request.method == "GET"
        response = venue.handle(request)
        if request.url.path.endswith(("instruments", "books", "leverage-info")):
            assert request.url.params["instId"] == instrument
            payload = response.json()
            data = payload["data"]
            row = data[0] if isinstance(data, list) else data
            row["instId"] = instrument
            if "baseCurrency" in row:
                row.update(baseCurrency=symbol[:-4], contractValue="0.01", minSize="2")
            response = httpx.Response(200, json=payload)
        return response

    snapshot = provider_for(handler).snapshot(symbol=symbol, now=EVALUATED_AT, side=EntrySide.BUY)
    assert snapshot.instrument == instrument
    assert (snapshot.multiplier, snapshot.minimum, snapshot.lot, snapshot.tick) == (
        Decimal("0.01"),
        Decimal("2"),
        Decimal("1"),
        Decimal("0.1"),
    )
    assert venue.post_count == 0


@pytest.mark.parametrize("mutation", ["missing", "wrong_symbol", "suspended", "inverse", "spot"])
def test_unavailable_contract_never_substitutes(mutation):
    venue = Venue(Decimal("100000"))

    def handler(request):
        response = venue.handle(request)
        if request.url.path.endswith("instruments"):
            payload = response.json()
            row = payload["data"][0]
            if mutation == "missing":
                payload["data"] = []
            else:
                key, value = {
                    "wrong_symbol": ("instId", "ETH-USDT"),
                    "suspended": ("state", "suspend"),
                    "inverse": ("contractType", "inverse"),
                    "spot": ("instType", "SPOT"),
                }[mutation]
                row[key] = value
            response = httpx.Response(200, json=payload)
        return response

    with pytest.raises(ValueError):
        provider_for(handler).snapshot(symbol="BTCUSDT", now=EVALUATED_AT, side=EntrySide.BUY)
    assert venue.post_count == 0


@pytest.mark.parametrize("endpoint", ["positions", "orders-pending", "orders-tpsl-pending"])
@pytest.mark.parametrize(
    "data", [None, {}, [None], [{}] * 100, [{"instId": "ETH-USDT", "positions": "1"}]]
)
def test_account_wide_unreadable_exposure_and_pending_orders_refuse(endpoint, data):
    venue = Venue(Decimal("100000"))

    def handler(request):
        assert request.method == "GET"
        if request.url.path.endswith("/" + endpoint):
            assert "instId" not in request.url.params
            return httpx.Response(200, json={"code": "0", "data": data})
        return venue.handle(request)

    with pytest.raises((ValueError, ArithmeticError)):
        provider_for(handler).snapshot(symbol="BTCUSDT", now=EVALUATED_AT, side=EntrySide.BUY)
    assert venue.post_count == 0


@pytest.mark.parametrize("quantity", ["NaN", "Infinity", "missing"])
def test_nonfinite_or_missing_position_cannot_prove_flat(quantity):
    def handler(request):
        return httpx.Response(200, json={"code": "0", "data": [{"positions": quantity}]})

    with pytest.raises((ValueError, ArithmeticError)):
        provider_for(handler).verify_flat_account()


@requires_postgres
@pytest.mark.parametrize("venue", ["PAPER_INTERNAL", "BLOFIN_DEMO"])
def test_history_uses_immutable_plan_venue_with_tenant_and_account_isolation(venue):
    factory = phase7_plan_session_factory()
    with factory() as session:
        ids, plan, auth = prepared_authorized_plan(session, execution_venue=venue)
        settings = paper_settings(database_url="sqlite+pysqlite:///:memory:")
        service = ExecutionService(session, settings, AuditService(session))
        result = service.execute_paper_plan(
            execute_request(ids, plan, auth, key="prior"), clock=lambda: EXECUTE_AT
        )
        assert result.outcome is ExecutionCommandOutcome.ALLOW
        session.commit()
        scope = {"organization_id": plan.organization_id, "account_id": plan.account_id}
        assert has_demo_entry_history(session, **scope) == (venue == "BLOFIN_DEMO")
        assert not has_demo_entry_history(session, **{**scope, "organization_id": uuid4()})
        assert not has_demo_entry_history(session, **{**scope, "account_id": ids["account_two"].id})
        # Enabling the claim predicate changes no historical command, authorization or risk.
        armed = settings.model_copy(update={"governed_blofin_demo_enabled": True})
        next_plan = persist_plan(session, ids, plan_request(ids))
        next_auth = approve_plan(session, ids, next_plan)
        next_result = ExecutionService(session, armed, AuditService(session)).execute_paper_plan(
            execute_request(ids, next_plan, next_auth, key="next"), clock=lambda: EXECUTE_AT
        )
        if venue == "BLOFIN_DEMO":
            assert next_result.blocked_reason_code == "demo_account_already_claimed"
        else:
            assert next_result.outcome is ExecutionCommandOutcome.ALLOW
        assert (
            session.get(ExecutionCommand, result.command_id).outcome
            is ExecutionCommandOutcome.ALLOW
        )


@requires_postgres
def test_paper_history_does_not_bypass_existing_risk_limits():
    factory = phase7_plan_session_factory()
    with factory() as session:
        ids, plan, auth = prepared_authorized_plan(session, execution_venue="PAPER_INTERNAL")
        settings = paper_settings(database_url="sqlite+pysqlite:///:memory:")
        service = ExecutionService(session, settings, AuditService(session))
        service.execute_paper_plan(
            execute_request(ids, plan, auth, key="paper"), clock=lambda: EXECUTE_AT
        )
        accounting = session.scalar(select(AccountRiskAccountingState))
        accounting.max_notional = accounting.reserved_notional
        next_plan = persist_plan(session, ids)
        next_auth = approve_plan(session, ids, next_plan)
        armed = settings.model_copy(update={"governed_blofin_demo_enabled": True})
        result = ExecutionService(session, armed, AuditService(session)).execute_paper_plan(
            execute_request(ids, next_plan, next_auth, key="demo"), clock=lambda: EXECUTE_AT
        )
        assert result.blocked_reason_code == "insufficient_total_exposure"


@requires_postgres
@pytest.mark.parametrize("worker_enabled", [False, True])
def test_competing_market_claims_have_only_one_demo_allow(worker_enabled):
    factory = phase7_plan_session_factory()
    with factory() as session:
        ids, first, first_auth = prepared_authorized_plan(session)
        second = persist_plan(session, ids, plan_request(ids, execution_instrument="ETH-USDT"))
        second_auth = approve_plan(session, ids, second)
        requests = [
            execute_request(ids, p, a, key=str(uuid4()))
            for p, a in ((first, first_auth), (second, second_auth))
        ]
        session.commit()
    settings = paper_settings(database_url="sqlite+pysqlite:///:memory:").model_copy(
        update={"governed_blofin_demo_enabled": worker_enabled}
    )
    barrier = Barrier(2)

    def claim(request):
        with factory() as session:
            barrier.wait(timeout=20)
            result = ExecutionService(session, settings, AuditService(session)).execute_paper_plan(
                request, clock=lambda: EXECUTE_AT
            )
            session.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, requests))
    assert sum(r.outcome is ExecutionCommandOutcome.ALLOW for r in results) == 1
    assert [
        r.blocked_reason_code for r in results if r.outcome is not ExecutionCommandOutcome.ALLOW
    ] == ["demo_account_already_claimed"]
