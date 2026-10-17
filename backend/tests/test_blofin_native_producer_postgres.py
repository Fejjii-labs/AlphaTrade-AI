"""Actual PostgreSQL origin/position producer; only synthetic restricted GET IO.

Positive cases prove immutable origin and pre-entry receipts, not live venue
performance. The absent protected-parent/monetary producer stays unavailable.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.api.routes.experiments import get_native_reads, router
from app.core.auth import get_current_tenant
from app.core.blofin_readonly_access import get_readonly_client
from app.core.dependencies import get_settings
from app.core.errors import ConflictError, NotFoundError, register_exception_handlers
from app.db.base import Base
from app.db.models import (
    ApprovalAuthorization,
    AuditLog,
    ExecutionAccount,
    ExecutionCommand,
    ExecutionReceipt,
    Membership,
    TradePlanRevision,
    UserStrategy,
    UserStrategyVersion,
    VenueSubmitEffect,
)
from app.db.session import get_session
from app.experiments.native_producer import (
    DIAGNOSTIC,
    ENTRY,
    ORIGIN,
    POSITION,
    BloFinNativeOutcomeProducer,
    NativeProducerDiagnostic,
)
from app.experiments.outcomes import BloFinExperimentReads
from app.experiments.service import ExperimentService
from app.providers.exchange.blofin_activity import BloFinActivityProvider
from app.providers.exchange.errors import ExchangeError
from app.schemas.common import MembershipRole
from app.schemas.execution_protocol import ExecutionCommandOutcome, VenueSubmitEffectState
from app.schemas.nested_continuation import NestedContinuationSpec
from app.schemas.trade_plan import AuthorizationState
from app.security.tenant import TenantContext
from tests.support import phase7_postgres
from tests.support.experiment_fixtures import World
from tests.support.phase5_market import EVALUATED_AT
from tests.support.phase6_fusion import ACCOUNT_ID, ORG_ID, STRATEGY_VERSION_ID, USER_ID
from tests.support.phase7_trade_plan import plan_command
from tests.support.phase8_runtime import authorize_canonical_plan
from tests.test_experiment_native_identity import original_execution_proof


@pytest.fixture
def producer_engine():
    url = os.environ.get("EXPERIMENT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Explicit disposable EXPERIMENT_TEST_POSTGRES_URL required")
    schema = "native_producer_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(make_url(url).update_query_dict({"options": "-csearch_path=" + schema}))
    try:
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


class ProducerWorld:
    def __init__(self, engine, monkeypatch):
        self.factory = sessionmaker(bind=engine, expire_on_commit=False)
        spec = NestedContinuationSpec(symbol="BTCUSDT")
        # The canonical fixture normally has no authored pattern. Supply an
        # authored version at initial creation, never mutate immutable history.
        original = UserStrategyVersion
        monkeypatch.setattr(
            phase7_postgres,
            "UserStrategyVersion",
            lambda **values: original(**values, pattern_spec=spec.model_dump(mode="json")),
        )
        self.plans = phase7_postgres.postgres_plan_world(self.factory)
        self.envelope = self.plans.plans.create(plan_command(self.plans))
        self.session = session = self.factory()
        self.w = w = World.__new__(World)
        w.session = session
        w.now = EVALUATED_AT
        w.tenant = TenantContext(
            organization_id=ORG_ID,
            user_id=USER_ID,
            email="native-producer@fixture.test",
            membership_role=MembershipRole.OWNER,
        )
        w.account = session.get(ExecutionAccount, ACCOUNT_ID)
        w.strategy_version = session.get(UserStrategyVersion, STRATEGY_VERSION_ID)
        w.strategy = session.get(UserStrategy, w.strategy_version.strategy_id)
        w.spec = spec
        session.add(Membership(organization_id=ORG_ID, user_id=USER_ID, role=MembershipRole.OWNER))
        session.flush()
        settings, self.identity, account, self.identity_requests = original_execution_proof(w)
        w.settings = settings
        w.service = ExperimentService(session, settings, clock=lambda: w.now)
        self.version = w.running(w.configuration(account=account))
        self.authorization = authorize_canonical_plan(
            session, self.envelope, plans=self.plans.plans, clock=w.now
        )
        self.producer = self.make_producer(session)
        self.requests = []
        self.positions = []
        self.uid_sequence = [account.native_uid, account.native_uid]
        self.metadata = [
            {
                "instId": "BTC-USDT",
                "state": "live",
                "instType": "SWAP",
                "contractType": "linear",
                "baseCurrency": "BTC",
                "quoteCurrency": "USDT",
                "settleCurrency": "USDT",
                "contractValueCurrency": "BTC",
                "contractValue": "1",
            }
        ]
        session.commit()

    def make_producer(self, session):
        return BloFinNativeOutcomeProducer(
            session, self.w.settings, self.w.tenant, clock=lambda: self.w.now
        )

    def register(self):
        return self.producer.register_plan(
            self.version.experiment_id, self.version.id, "baseline", self.envelope.plan.revision_id
        )

    def command(self):
        self.session.commit()  # Origin must exist durably before this fixture command.
        self.w.now += timedelta(seconds=1)
        plan = self.envelope.plan
        command = ExecutionCommand(
            id=uuid4(),
            organization_id=ORG_ID,
            user_id=USER_ID,
            account_id=ACCOUNT_ID,
            operation="SUBMIT_ENTRY",
            operation_namespace="alphatrade/submit-entry/v1",
            plan_id=plan.plan_id,
            revision_id=plan.revision_id,
            authorization_id=self.authorization.authorization_id,
            plan_content_hash=plan.content_hash,
            canonical_payload_hash="a" * 64,
            opaque_idempotency_key=uuid4().hex,
            correlation_id=uuid4(),
            outcome=ExecutionCommandOutcome.ALLOW,
            created_at=self.w.now,
            updated_at=self.w.now,
        )
        self.session.add(command)
        self.session.flush()
        authorization = self.session.get(ApprovalAuthorization, command.authorization_id)
        authorization.state = AuthorizationState.CONSUMED
        authorization.consumed_by_execution_command_id = command.id
        authorization.consumed_at = self.w.now
        receipt = ExecutionReceipt(
            command_id=command.id,
            operation="SUBMIT_ENTRY",
            authorization_id=command.authorization_id,
            organization_id=ORG_ID,
            user_id=USER_ID,
            account_id=ACCOUNT_ID,
        )
        self.session.add(receipt)
        self.session.flush()
        self.effect = VenueSubmitEffect(
            command_id=command.id,
            receipt_id=receipt.id,
            client_order_id="synthetic-" + uuid4().hex,
            safety_epoch=0,
        )
        self.session.add(self.effect)
        self.session.flush()
        self.command_row = command
        return command

    def provider(self):
        def handle(request):
            self.requests.append((request.method, request.url.path))
            assert request.method == "GET"
            if request.url.path == "/api/v1/user/query-apikey":
                uid = self.uid_sequence.pop(0) if self.uid_sequence else "uid-a"
                data = {"readOnly": 1, "uid": uid}
            elif request.url.path == "/api/v1/account/positions":
                data = self.positions
            elif request.url.path == "/api/v1/market/instruments":
                data = self.metadata
            else:
                raise AssertionError("Unapproved qualification IO")
            return httpx.Response(200, json={"code": "0", "data": data})

        return BloFinActivityProvider(
            get_readonly_client(
                self.w.settings, transport=httpx.MockTransport(handle), activity_history=True
            )
        )

    def bind(self):
        return self.producer.bind_entry(self.command_row.id, provider=self.provider())

    def operations(self):
        return list(
            self.session.scalars(
                select(AuditLog).where(AuditLog.resource_type == "experiment_execution")
            )
        )


@pytest.fixture
def world(producer_engine, monkeypatch):
    world = ProducerWorld.__new__(ProducerWorld)
    try:
        world.__init__(producer_engine, monkeypatch)
        yield world
    finally:
        if hasattr(world, "session"):
            world.session.close()


def test_server_origin_and_signed_pre_entry_receipts_survive_fresh_session_without_io(world):
    w = world
    origin = w.register()
    w.session.commit()  # Explicit coordinator ownership before command creation.
    command = w.command()
    w.session.commit()
    binding = w.bind()
    assert binding.pre_entry_position_flat is True
    assert binding.ownership_command_id == command.id
    assert binding.strategy_content_hash == origin.strategy_content_hash
    w.session.commit()
    receipts = {r.redacted_metadata["operation"]: r.id for r in w.operations()}
    assert set(receipts) == {ORIGIN, POSITION, ENTRY}
    assert len(w.requests) == 4 and all(method == "GET" for method, _ in w.requests)
    with w.factory() as fresh:
        producer = w.make_producer(fresh)
        assert (
            producer.register_plan(
                w.version.experiment_id, w.version.id, "baseline", w.envelope.plan.revision_id
            )
            == origin
        )
        assert producer.bind_entry(command.id, provider=w.provider()) == binding
        fresh.commit()
    assert len(w.requests) == 4
    assert {r.redacted_metadata["operation"]: r.id for r in w.operations()} == receipts
    # Qualification does not change the execution effect or dispatch it.
    assert w.effect.attempt == 0 and w.effect.state is VenueSubmitEffectState.CREATED


def test_advancing_clock_preserves_exact_origin_and_position_receipt_times(world):
    w = world

    def advancing_clock():
        w.w.now += timedelta(microseconds=137)
        return w.w.now

    w.producer = BloFinNativeOutcomeProducer(
        w.session, w.w.settings, w.w.tenant, clock=advancing_clock
    )
    origin = w.register()
    command = w.command()
    binding = w.bind()
    w.session.commit()
    operations = {r.redacted_metadata["operation"]: r for r in w.operations()}
    assert operations[ORIGIN].created_at == origin.recorded_at
    position = w.producer._position(command, operations[ORIGIN])
    assert position.observed_at == operations[POSITION].created_at == operations[ENTRY].created_at
    with w.factory() as fresh:
        assert w.make_producer(fresh).bind_entry(command.id, provider=w.provider()) == binding
        fresh.commit()
    assert len(w.requests) == 4


def test_caller_true_flags_cannot_enter_producer_contract(world):
    world.register()
    command = world.command()
    with pytest.raises(TypeError):
        world.producer.bind_entry(
            command.id, provider=world.provider(), pre_entry_position_flat=True
        )
    assert world.requests == []


def test_historical_command_cannot_be_retroactively_registered(world):
    world.command()
    with pytest.raises(ConflictError) as error:
        world.register()
    assert error.value.code == "experiment_origin_must_precede_command"
    assert world.operations() == []


def test_uncommitted_origin_does_not_establish_preexisting_execution_authority(world):
    world.register()
    with patch.object(world.session, "commit", lambda: None):
        world.command()
    with pytest.raises(ConflictError) as error:
        world.bind()
    assert error.value.code == "experiment_origin_not_committed"
    assert world.requests == []


def test_manual_plan_and_client_echo_never_become_an_experiment_origin(world):
    plan = world.session.scalar(
        select(TradePlanRevision).where(TradePlanRevision.plan_authority == "manual_demo_test")
    )
    with pytest.raises(ConflictError) as error:
        world.producer.register_plan(
            world.version.experiment_id, world.version.id, "baseline", plan.id
        )
    assert error.value.code == "experiment_canonical_plan_required"
    assert world.operations() == []


@pytest.mark.parametrize("case", ["stale", "identity_error", "rotated_unverified"])
def test_existing_identity_uncertainty_blocks_the_producer_before_readonly_io(world, case):
    world.register()
    world.command()
    if case == "stale":
        world.identity.identity_verified_at = world.w.now - timedelta(seconds=301)
    elif case == "identity_error":
        world.identity.identity_error = "unverified"
    else:
        world.identity.credential_binding = "d" * 64
    world.session.flush()
    with pytest.raises(ConflictError) as error:
        world.bind()
    assert error.value.code == "experiment_account_unverified"
    assert world.requests == []
    assert {r.redacted_metadata["operation"] for r in world.operations()} == {ORIGIN}


@pytest.mark.parametrize("case", ["foreign_tenant", "foreign_user", "simulation", "new_version"])
def test_origin_cannot_change_owner_source_or_experiment_version(world, case):
    w = world
    w.register()
    if case in {"foreign_tenant", "foreign_user"}:
        tenant = replace(
            w.w.tenant, **{"organization_id" if case == "foreign_tenant" else "user_id": uuid4()}
        )
        producer = BloFinNativeOutcomeProducer(
            w.session, w.w.settings, tenant, clock=lambda: w.w.now
        )
        with pytest.raises(NotFoundError):
            producer.register_plan(
                w.version.experiment_id, w.version.id, "baseline", w.envelope.plan.revision_id
            )
    elif case == "simulation":
        other = w.w.running(w.w.configuration())
        with pytest.raises(ConflictError) as error:
            w.producer.register_plan(
                other.experiment_id, other.id, "baseline", w.envelope.plan.revision_id
            )
        assert error.value.code == "experiment_native_source_required"
    else:
        other = w.w.running(w.version.configuration)
        with pytest.raises(ConflictError) as error:
            w.producer.register_plan(
                other.experiment_id, other.id, "baseline", w.envelope.plan.revision_id
            )
        assert error.value.code == "experiment_lineage_conflict"
    assert len(w.operations()) == 1


@pytest.mark.parametrize(
    "positions",
    [
        [{"instId": "BTC-USDT", "positionSide": "net", "positions": "1"}],
        [{"instId": "ETH-USDT", "positionSide": "net", "positions": "1"}],
        [{"instId": "BTC-USDT", "positionSide": "long", "positions": "0"}],
        [{"instId": "BTC-USDT", "positionSide": "net", "positions": "NaN"}],
        [{"instId": "BTC-USDT", "positionSide": "net", "positions": True}],
        [{"instId": "BTC-USDT", "positionSide": "net"}],
        [{"instId": "BTC-USDT", "positionSide": "net", "positions": "0"}] * 100,
    ],
)
def test_unreadable_hedged_or_nonflat_positions_never_sign_an_entry(world, positions):
    w = world
    w.register()
    w.command()
    w.positions = positions
    with pytest.raises((ConflictError, ValueError)):
        w.bind()
    assert {r.redacted_metadata["operation"] for r in w.operations()} == {ORIGIN}


@pytest.mark.parametrize(
    "case", ["uid_changed", "late_uid_change", "contract_mismatch", "missing_currency"]
)
def test_identity_or_contract_uncertainty_never_signs_position_or_binding(world, case):
    w = world
    w.register()
    w.command()
    if case == "uid_changed":
        w.uid_sequence = ["uid-b"]
    elif case == "late_uid_change":
        w.uid_sequence = ["uid-a", "uid-b"]
    elif case == "contract_mismatch":
        w.metadata[0]["contractValue"] = "0.01"
    else:
        w.metadata[0].pop("settleCurrency")
    with pytest.raises((ConflictError, ExchangeError)):
        w.bind()
    assert {r.redacted_metadata["operation"] for r in w.operations()} == {ORIGIN}


@pytest.mark.parametrize(
    "state", [VenueSubmitEffectState.DISPATCH_AUTHORIZED, VenueSubmitEffectState.SEND_ATTEMPTED]
)
def test_post_transport_state_cannot_become_original_pre_entry_snapshot(world, state):
    world.register()
    world.command()
    world.effect.state = state
    world.session.flush()
    with pytest.raises(ConflictError) as error:
        world.bind()
    assert error.value.code == "native_position_capture_must_precede_dispatch"
    assert world.requests == []


def test_concurrent_origin_registration_converges_on_one_durable_identity(world):
    w = world
    barrier = Barrier(2)

    def register():
        with w.factory() as session:
            producer = w.make_producer(session)
            barrier.wait(timeout=5)
            value = producer.register_plan(
                w.version.experiment_id, w.version.id, "baseline", w.envelope.plan.revision_id
            )
            session.commit()
            return value

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(register) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert results[0] == results[1]
    assert len(w.operations()) == 1


def test_concurrent_binding_and_reconciliation_deduplicate_receipts_without_new_transport(world):
    w = world
    w.register()
    w.command()
    w.session.commit()

    def concurrently(operation):
        barrier = Barrier(2)

        def run():
            with w.factory() as session:
                producer = w.make_producer(session)
                barrier.wait(timeout=5)
                result = operation(producer)
                session.commit()
                return result

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run) for _ in range(2)]
            values = [future.result(timeout=10) for future in futures]
        assert values[0] == values[1]
        return values[0]

    concurrently(lambda p: p.bind_entry(w.command_row.id, provider=w.provider()))
    result = concurrently(lambda p: p.reconcile(w.command_row.id))
    assert result.status == "unavailable"
    assert len(w.requests) == 4
    assert len(w.operations()) == 4  # origin, position, entry, one diagnostic


def test_original_receipts_replay_after_plan_expiry_without_new_readonly_io(world):
    w = world
    origin = w.register()
    command = w.command()
    binding = w.bind()
    w.session.commit()
    w.w.now = w.envelope.plan.valid_until + timedelta(seconds=1)
    # A verified same-UID identity refresh does not rewrite original evidence.
    w.identity.identity_verified_at = w.w.now
    w.session.commit()
    with w.factory() as fresh:
        producer = w.make_producer(fresh)
        assert (
            producer.register_plan(
                w.version.experiment_id, w.version.id, "baseline", w.envelope.plan.revision_id
            )
            == origin
        )
        assert producer.bind_entry(command.id, provider=w.provider()) == binding
        fresh.commit()
    assert len(w.requests) == 4 and len(w.operations()) == 3


def test_missing_exit_remains_unavailable_and_diagnostics_deduplicate_after_restart(world):
    w = world
    w.register()
    w.session.commit()
    w.command()
    w.bind()
    w.session.commit()
    result = w.producer.reconcile(w.command_row.id)
    assert result.status == "unavailable" and result.reason == "native_entry_receipt_unavailable"
    assert result.proof is None and result.reported_realized_pnl is None
    assert result.fee_cost is None and result.realized_pnl_after_fees_excluding_funding is None
    assert "native_exit_command_or_protected_parent_lineage_unverified" in result.limitations
    assert "native_fee_sign_convention_unverified" in result.limitations
    assert "native_fill_pnl_fee_and_funding_basis_unverified" in result.limitations
    w.session.commit()
    with w.factory() as fresh:
        assert w.make_producer(fresh).reconcile(w.command_row.id) == result
        fresh.commit()
    diagnostics = [
        r for r in w.operations() if r.redacted_metadata["operation"].startswith(DIAGNOSTIC + ":")
    ]
    assert len(diagnostics) == 1
    body = w.producer._body(diagnostics[0])
    assert body["contract_version"] == "experiment-native-producer-diagnostic/v2"
    assert body["missing_evidence"] == tuple(
        code
        for code in result.limitations
        if code not in type(result).model_fields["limitations"].default
    )


def test_existing_v1_diagnostic_receipt_hash_remains_readable(world):
    w = world
    w.register()
    w.session.commit()
    w.command()
    w.bind()
    origin = next(row for row in w.operations() if row.redacted_metadata["operation"] == ORIGIN)
    position = next(row for row in w.operations() if row.redacted_metadata["operation"] == POSITION)
    legacy = NativeProducerDiagnostic(
        command_id=w.command_row.id,
        origin_hash=origin.payload_hash,
        position_hash=position.payload_hash,
        reason="native_entry_receipt_unavailable",
    )
    row = w.producer._store(w.command_row.id, DIAGNOSTIC + ":legacy-fixture", legacy)
    w.session.commit()
    with w.factory() as fresh:
        producer = w.make_producer(fresh)
        assert producer._body(fresh.get(AuditLog, row.id)) == legacy.model_dump()


def test_raw_closing_pointer_and_numbers_cannot_supply_missing_provider_contracts(world):
    w = world
    w.register()
    w.session.commit()
    w.command()
    w.bind()
    # A synthetic diagnostic source only: never store an exit attestation or
    # claim these raw fields establish native position or monetary semantics.
    resolution = SimpleNamespace(
        evidence_payload={
            "exit_fills": [{"fee": "-0.03", "fee_currency": "USDT", "fillPnl": "1.25"}],
            "exit_order_protection_ids": [{"order_id": "child", "tpsl_id": "parent"}],
        }
    )
    missing = w.producer._missing_closing_evidence(
        w.command_row, resolution, "native_exit_parent_unverified"
    )
    assert "native_exit_command_or_protected_parent_lineage_unverified" in missing
    assert "native_complete_position_fill_lineage_unverified" in missing
    assert "native_fee_sign_convention_unverified" in missing
    assert "native_fill_pnl_fee_and_funding_basis_unverified" in missing
    assert "native_historical_entry_fee_currency_unavailable" in missing
    assert "native_historical_exit_fee_currency_unavailable" not in missing
    assert "native_exit_fill_pnl_unavailable" not in missing


def test_http_reader_is_scoped_database_only_and_sample_admission_stays_closed(world):
    w = world
    app = FastAPI()
    app.include_router(router)
    register_exception_handlers(app)
    actor = [w.w.tenant]
    app.dependency_overrides[get_current_tenant] = lambda: actor[0]
    app.dependency_overrides[get_session] = lambda: w.session
    app.dependency_overrides[get_settings] = lambda: w.w.settings
    app.dependency_overrides[get_native_reads] = lambda: BloFinExperimentReads(
        w.session, w.w.settings, actor[0], clock=lambda: w.w.now
    )
    prefix = f"/experiments/{w.version.experiment_id}/versions/{w.version.id}"
    count = w.session.scalar(select(func.count()).select_from(AuditLog))
    with TestClient(app) as client:
        actor[0] = replace(w.w.tenant, membership_role=MembershipRole.VIEWER)
        outcome = client.get(prefix + "/outcomes/" + str(uuid4()))
        assert outcome.status_code == 200 and outcome.json()["status"] == "unavailable"
        assert outcome.json()["reported_realized_pnl"] is None
        performance = client.get(prefix + "/performance")
        assert performance.status_code == 200 and performance.json()["status"] == "unavailable"
        assert performance.json()["reason"] == "insufficient_samples"
        assert client.get(prefix + "/performance?variant_key=unknown").status_code == 409
        assert client.get(prefix + "/performance?variant_key=INVALID").status_code == 422
        actor[0] = replace(w.w.tenant, organization_id=uuid4())
        assert client.get(prefix + "/performance").status_code == 404
        assert client.get(prefix + "/outcomes/" + str(uuid4())).status_code == 404
        actor[0] = w.w.tenant
        denied = client.post(
            prefix + "/samples", json={"variant_key": "baseline", "source_record_id": str(uuid4())}
        )
        assert denied.status_code == 503
        assert denied.json()["error"]["code"] == "experiment_source_adapter_unavailable"
    assert w.session.scalar(select(func.count()).select_from(AuditLog)) == count
    assert w.requests == []
