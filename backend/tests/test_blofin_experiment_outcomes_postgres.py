"""Actual consumer and reconciliation on disposable PostgreSQL; no external IO."""

from datetime import timedelta
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.blofin_identity import connection_binding
from app.core.errors import ConflictError, NotFoundError
from app.db.blofin_activity import BloFinActivityCursor, BloFinActivityFact
from app.db.models import (
    AuditLog,
    ExecutionCommand,
    ExecutionReceipt,
    TradeProposal,
    VenueSubmitEffect,
)
from app.experiments.models import ExperimentSampleRow
from app.experiments.native_lineage import NativeEntryBinding, NativeExitLineage
from app.experiments.outcomes import BloFinExperimentReads
from app.experiments.service import semantic_hash
from app.repositories.blofin_activity import BloFinActivityRepository, fact_hash
from app.schemas.blofin_activity import NativeActivityFact
from app.schemas.execution_protocol import ExecutionCommandOutcome
from app.schemas.experiments import ExperimentMode, ExperimentSampleCreate
from app.services.blofin_activity_config import ActivityScope
from tests.support.experiment_fixtures import World
from tests.support.experiment_fixtures import experiment_engine as _experiment_engine  # noqa: F401
from tests.support.phase1_plan_fixtures import (
    approve_plan,
    persist_plan,
    plan_request,
    seed_support,
)
from tests.test_experiment_native_identity import original_execution_proof


def millis(at):
    return str(int(at.timestamp()) * 1000 + at.microsecond // 1000)


class NativeWorld:
    def __init__(self, session, minimum=1):
        self.w = w = World(session)
        settings, self.identity, account, self.requests = original_execution_proof(w)
        w.settings = settings
        w.service.settings = settings
        self.account = account
        self.version = w.running(
            w.configuration(
                account=account,
                sample_target={"kind": "closed_trade", "minimum": minimum, "maximum": 5},
            )
        )
        self.start = w.now
        self.scope = ActivityScope(w.tenant.organization_id, account.native_uid)
        self.reads = BloFinExperimentReads(session, settings, w.tenant, clock=lambda: w.now)
        ids = seed_support(session)
        original = session.get(TradeProposal, ids["proposal_id"])
        exclude = {
            "id",
            "created_at",
            "updated_at",
            "organization_id",
            "user_id",
            "user_strategy_id",
            "latest_plan_revision_id",
        }
        values = {
            c.name: getattr(original, c.name)
            for c in TradeProposal.__table__.columns
            if c.name not in exclude
        }
        proposal = TradeProposal(
            id=uuid4(),
            organization_id=w.tenant.organization_id,
            user_id=w.tenant.user_id,
            user_strategy_id=w.strategy.id,
            **values,
        )
        session.add(proposal)
        session.flush()
        ids.update(
            organization=session.get(type(ids["organization"]), w.tenant.organization_id),
            user=session.get(type(ids["user"]), w.tenant.user_id),
            account_one=w.account,
            strategy_version=w.strategy_version,
            proposal_id=proposal.id,
        )
        data = plan_request(
            ids,
            exchange_account_id=None,
            timeframe=w.spec.trigger_timeframe.value,
            evidence_instrument=w.spec.symbol,
            valid_from=w.now,
            valid_until=w.now + timedelta(hours=1),
            evidence_observed_at=w.now - timedelta(seconds=5),
            basis_policy={
                **plan_request(ids).basis_policy.model_dump(),
                "timestamp": w.now - timedelta(seconds=4),
            },
        )
        self.plan = persist_plan(session, ids, data)
        authorization = approve_plan(
            session, ids, self.plan, clock=w.now + timedelta(milliseconds=100)
        )
        self.command = ExecutionCommand(
            id=uuid4(),
            organization_id=w.tenant.organization_id,
            user_id=w.tenant.user_id,
            account_id=w.account.id,
            operation="SUBMIT_ENTRY",
            operation_namespace="alphatrade/submit-entry/v1",
            plan_id=self.plan.plan_id,
            revision_id=self.plan.revision_id,
            authorization_id=authorization.authorization_id,
            plan_content_hash=self.plan.content_hash,
            canonical_payload_hash="b" * 64,
            opaque_idempotency_key=uuid4().hex,
            correlation_id=uuid4(),
            outcome=ExecutionCommandOutcome.ALLOW,
            created_at=self.start,
            updated_at=self.start,
        )
        session.add(self.command)
        session.flush()
        receipt = ExecutionReceipt(
            command_id=self.command.id,
            operation="SUBMIT_ENTRY",
            authorization_id=authorization.authorization_id,
            organization_id=w.tenant.organization_id,
            user_id=w.tenant.user_id,
            account_id=w.account.id,
        )
        session.add(receipt)
        session.flush()
        self.effect = VenueSubmitEffect(
            command_id=self.command.id,
            receipt_id=receipt.id,
            client_order_id="experiment-" + uuid4().hex,
            safety_epoch=0,
        )
        session.add(self.effect)
        session.flush()
        self.binding = NativeEntryBinding(
            organization_id=w.tenant.organization_id,
            user_id=w.tenant.user_id,
            experiment_id=self.version.experiment_id,
            version_id=self.version.id,
            configuration_hash=self.version.configuration_hash,
            sample_group_id=self.version.sample_group_id,
            execution_account_id=w.account.id,
            native_uid=account.native_uid,
            strategy_version_id=w.strategy_version.id,
            strategy_content_hash=w.strategy_version.content_hash,
            variant_key="baseline",
            ownership_command_id=self.command.id,
            plan_content_hash=self.plan.content_hash,
            # Synthetic server assertions only; no native position producer is installed.
            pre_entry_position_flat=True,
        )
        self.entry_audit = self.audit("experiment_native_entry_binding", self.binding, self.start)
        self.lineage = NativeExitLineage(
            entry_binding_hash=semantic_hash(self.binding.model_dump()),
            native_entry_order_id="entry",
            native_exit_order_ids=("exit1", "exit2"),
            entry_fill_ids=("entry-f1", "entry-f2"),
            exit_fill_ids=("exit-f1", "exit-f2"),
            position_lineage_verified=True,
            pnl_excludes_fees=True,
            fee_convention="positive_cost",
            monetary_methodology_version="verified-fixture/v1",
        )
        self.exit_audit = self.audit(
            "experiment_native_exit_lineage", self.lineage, self.start + timedelta(seconds=5)
        )
        self.native = {
            "entry": self.order("entry", "buy", 2, False, 1, 3),
            "exit1": self.order("exit1", "sell", 1, True, 3, 4),
            "exit2": self.order("exit2", "sell", 1, True, 4, 5),
            "entry-f1": self.fill("entry-f1", "entry", "buy", 1, 2, None),
            "entry-f2": self.fill("entry-f2", "entry", "buy", 1, 3, None),
            "exit-f1": self.fill("exit-f1", "exit1", "sell", 1, 4, "3.000000000000000001"),
            "exit-f2": self.fill("exit-f2", "exit2", "sell", 1, 5, "-1"),
        }
        w.now = self.start + timedelta(seconds=6)
        self.cursor = BloFinActivityCursor(
            organization_id=w.tenant.organization_id,
            environment="demo",
            account_uid=account.native_uid,
            kind="fill",
            window_begin_ms=int(millis(self.start)),
            window_end_ms=int(millis(w.now)),
            window_complete=True,
            gap_detected=False,
            covered_begin_ms=int(millis(self.start)),
            covered_end_ms=int(millis(w.now)),
            last_successful_sync=w.now,
            seen_cursors=[],
        )
        session.add(self.cursor)
        for fact in self.native.values():
            self.persist(fact)
        session.flush()

    def audit(self, operation, contract, at):
        digest = semantic_hash(contract.model_dump())
        row = AuditLog(
            organization_id=self.w.tenant.organization_id,
            user_id=self.w.tenant.user_id,
            actor="experiment_executor",
            actor_type="system",
            action="demo_lifecycle_reconciled",
            resource_type="experiment_execution",
            resource_id=str(self.command.id),
            payload_hash=digest,
            redacted_metadata={
                "operation": operation,
                "evidence_hash": digest,
                **contract.model_dump(mode="json"),
            },
            created_at=at,
            updated_at=at,
            event_at=at,
        )
        self.w.session.add(row)
        self.w.session.flush()
        return row

    def fill(self, identity, order_id, side, quantity, second, pnl):
        return NativeActivityFact(
            kind="fill",
            native_id=identity,
            trade_id=identity,
            order_id=order_id,
            instrument="BTC-USDT",
            side=side,
            position_side="net",
            quantity=str(quantity),
            price="100",
            occurred_at_ms=millis(self.start + timedelta(seconds=second)),
            fee="0.1",
            fee_currency="USDT",
            realized_pnl=pnl,
        )

    def order(self, identity, side, quantity, reduce, created, updated):
        return NativeActivityFact(
            kind="order",
            native_id=identity,
            order_id=identity,
            client_order_id=self.effect.client_order_id
            if identity == "entry"
            else "exit-" + identity,
            instrument="BTC-USDT",
            side=side,
            position_side="net",
            quantity=str(quantity),
            filled_quantity=str(quantity),
            state="filled",
            reduce_only=str(reduce).lower(),
            occurred_at_ms=millis(self.start + timedelta(seconds=updated)),
            created_at_ms=millis(self.start + timedelta(seconds=created)),
            updated_at_ms=millis(self.start + timedelta(seconds=updated)),
        )

    def persist(self, fact):
        row = BloFinActivityFact(
            organization_id=self.scope.organization_id,
            environment="demo",
            account_uid=self.scope.account_uid,
            kind=fact.kind,
            native_id=fact.native_id,
            order_id=fact.order_id,
            occurred_at_ms=int(fact.occurred_at_ms),
            content_hash=fact_hash(fact),
            payload=fact.model_dump(mode="json"),
            first_observed_at=self.w.now,
        )
        self.w.session.add(row)
        self.w.session.flush()
        return row

    def change(self, identity, **updates):
        fact = self.native[identity].model_copy(update=updates)
        self.native[identity] = fact
        row = self.w.session.get(BloFinActivityFact, (*self.scope.key(), fact.kind, identity))
        row.payload = fact.model_dump(mode="json")
        row.content_hash = fact_hash(fact)
        row.occurred_at_ms = int(fact.occurred_at_ms)
        self.w.session.flush()

    def reseal(self, row, contract):
        operation = row.redacted_metadata["operation"]
        digest = semantic_hash(contract.model_dump())
        row.redacted_metadata = {
            "operation": operation,
            "evidence_hash": digest,
            **contract.model_dump(mode="json"),
        }
        row.payload_hash = digest
        self.w.session.flush()

    def read(self):
        return self.reads.outcome(
            self.version.experiment_id, self.version.id, "baseline", str(self.command.id)
        )

    def sample(self):
        return self.reads.record_sample(
            self.version.experiment_id,
            self.version.id,
            ExperimentSampleCreate(variant_key="baseline", source_record_id=str(self.command.id)),
        )

    def performance(self):
        return self.reads.performance(self.version.experiment_id, self.version.id, "baseline")


@pytest.fixture
def native(experiment_engine, request):
    with Session(experiment_engine, expire_on_commit=False) as session:
        yield NativeWorld(session, minimum=getattr(request, "param", 1))


def test_actual_domain_consumer_admits_one_native_outcome(native):
    n = native
    requests = len(n.requests)
    result = n.read()
    assert result.status == "available", result.reason
    assert result.quantity == 2 and result.quantity_unit == "contracts"
    assert result.reported_realized_pnl == Decimal("2.000000000000000001")
    assert result.fee_cost == Decimal("0.4")
    assert result.realized_pnl_after_fees_excluding_funding == Decimal("1.600000000000000001")
    assert result.funding is None and not result.account_history_complete
    assert len(result.facts) == 7 and result.coverage == "reconciled_lineage_only"
    sample = n.sample()
    assert n.sample().id == sample.id
    performance = n.performance()
    assert (
        performance.status == "available"
        and performance.sample_count == 1
        and performance.win_rate == 1
    )
    assert len(n.requests) == requests


@pytest.mark.parametrize("field", ["pre_entry_position_flat", "position_lineage_verified"])
@pytest.mark.parametrize("value", [None, False])
def test_position_opening_and_complete_lineage_require_verified_server_claim(native, field, value):
    n = native
    binding = n.binding.model_copy(
        update={"pre_entry_position_flat": value} if field == "pre_entry_position_flat" else {}
    )
    lineage = n.lineage.model_copy(
        update={
            "entry_binding_hash": semantic_hash(binding.model_dump()),
            **({field: value} if field == "position_lineage_verified" else {}),
        }
    )
    n.reseal(n.entry_audit, binding)
    n.reseal(n.exit_audit, lineage)
    assert n.read().reason == "native_position_lineage_unverified"
    with pytest.raises(ConflictError):
        n.sample()
    assert n.performance().reason == "insufficient_samples"


@pytest.mark.parametrize("field", ["pre_entry_position_flat", "position_lineage_verified"])
def test_legacy_attestations_without_position_proof_remain_unavailable(native, field):
    n = native
    row = n.entry_audit if field == "pre_entry_position_flat" else n.exit_audit
    body = dict(row.redacted_metadata)
    operation = body.pop("operation")
    body.pop("evidence_hash")
    body.pop(field)
    digest = semantic_hash(body)
    row.payload_hash = digest
    row.redacted_metadata = {"operation": operation, "evidence_hash": digest, **body}
    if field == "pre_entry_position_flat":
        n.reseal(n.exit_audit, n.lineage.model_copy(update={"entry_binding_hash": digest}))
    n.w.session.flush()
    assert n.read().reason == "native_position_lineage_unverified"
    with pytest.raises(ConflictError):
        n.sample()


@pytest.mark.parametrize("field", ["pre_entry_position_flat", "position_lineage_verified"])
@pytest.mark.parametrize("value", ["true", 1])
def test_position_attestations_reject_nonboolean_claims(native, field, value):
    n = native
    row = n.entry_audit if field == "pre_entry_position_flat" else n.exit_audit
    body = dict(row.redacted_metadata)
    operation = body.pop("operation")
    body.pop("evidence_hash")
    body[field] = value
    digest = semantic_hash(body)
    row.payload_hash = digest
    row.redacted_metadata = {"operation": operation, "evidence_hash": digest, **body}
    if field == "pre_entry_position_flat":
        n.reseal(n.exit_audit, n.lineage.model_copy(update={"entry_binding_hash": digest}))
    n.w.session.flush()
    assert n.read().status == "unavailable"
    with pytest.raises(ConflictError):
        n.sample()


@pytest.mark.parametrize("reduce_only", ["true", None, "unknown"])
def test_native_entry_must_be_explicitly_nonreducing(native, reduce_only):
    native.change("entry", reduce_only=reduce_only)
    assert native.read().reason == "native_opening_entry_invalid"
    with pytest.raises(ConflictError):
        native.sample()


@pytest.mark.parametrize("realized_pnl", ["1", "-1"])
def test_entry_closing_pnl_cannot_be_ignored_as_opening_inventory(native, realized_pnl):
    native.change("entry-f1", realized_pnl=realized_pnl)
    assert native.read().reason == "native_opening_entry_invalid"
    with pytest.raises(ConflictError):
        native.sample()
    assert native.performance().reason == "insufficient_samples"


def test_explicit_zero_entry_pnl_preserves_verified_opening(native):
    native.change("entry-f1", realized_pnl="0")
    assert native.read().status == "available"
    assert native.sample().kind == "closed_trade"


@pytest.mark.parametrize(
    "field",
    [
        "organization_id",
        "user_id",
        "experiment_id",
        "version_id",
        "sample_group_id",
        "execution_account_id",
        "strategy_version_id",
        "ownership_command_id",
        "configuration_hash",
        "strategy_content_hash",
        "native_uid",
        "variant_key",
    ],
)
def test_exact_attribution_binding_rejects_mismatch(native, field):
    n = native
    value = (
        uuid4() if field.endswith("_id") else ("c" * 64 if field.endswith("hash") else "different")
    )
    n.reseal(n.entry_audit, n.binding.model_copy(update={field: value}))
    result = n.read()
    assert result.status == "unavailable" and result.reason == "experiment_attribution_mismatch"
    with pytest.raises(ConflictError):
        n.sample()


@pytest.mark.parametrize(
    "case",
    [
        "no_binding",
        "echo_only",
        "user_audit",
        "failed_audit",
        "bad_hash",
        "future_attestation",
        "conflict",
    ],
)
def test_echo_and_untrusted_audits_never_establish_ownership(native, case):
    n = native
    session = n.w.session
    if case in {"no_binding", "echo_only"}:
        session.delete(n.entry_audit)
    elif case == "user_audit":
        n.entry_audit.actor_type = "user"
    elif case == "failed_audit":
        n.entry_audit.result = "failure"
    elif case == "bad_hash":
        n.entry_audit.payload_hash = "d" * 64
    elif case == "future_attestation":
        n.entry_audit.created_at = n.w.now + timedelta(seconds=1)
    else:
        n.audit(
            "experiment_native_entry_binding",
            n.binding.model_copy(update={"variant_key": "other"}),
            n.start,
        )
    session.flush()
    assert n.read().status == "unavailable"
    with pytest.raises(ConflictError):
        n.sample()
    assert n.performance().reason == "insufficient_samples"


def test_ordinary_manual_command_cannot_be_relabelled_as_experiment(native):
    n = native
    identity_audit = n.w.session.get(AuditLog, n.account.execution_identity_audit_id)
    command = n.w.session.get(ExecutionCommand, identity_audit.resource_id)
    binding = n.binding.model_copy(
        update={"ownership_command_id": command.id, "plan_content_hash": command.plan_content_hash}
    )
    n.entry_audit.resource_id = str(command.id)
    n.exit_audit.resource_id = str(command.id)
    n.reseal(n.entry_audit, binding)
    n.reseal(
        n.exit_audit,
        n.lineage.model_copy(update={"entry_binding_hash": semantic_hash(binding.model_dump())}),
    )
    result = n.reads.outcome(n.version.experiment_id, n.version.id, "baseline", str(command.id))
    assert result.reason == "experiment_command_lineage_invalid"


@pytest.mark.parametrize("identity", ["entry-f2", "exit-f2", "entry", "exit2"])
def test_delayed_missing_facts_withhold_then_reconcile(native, identity):
    n = native
    original = n.native[identity]
    n.w.session.execute(
        delete(BloFinActivityFact).where(
            BloFinActivityFact.native_id == identity,
            BloFinActivityFact.organization_id == n.scope.organization_id,
        )
    )
    assert n.read().reason == "native_lineage_incomplete"
    with pytest.raises(ConflictError):
        n.sample()
    n.w.now += timedelta(seconds=1)
    restored = n.persist(original)
    assert n.read().status == "available"
    result = n.sample()
    assert result.completed_at == n.start + timedelta(seconds=5)
    assert restored.first_observed_at == n.w.now


@pytest.mark.parametrize(
    "case",
    ["missing_profit", "missing_fee", "unknown_currency", "wrong_currency", "unknown_method"],
)
def test_unknown_money_is_unavailable_and_never_zero(native, case):
    n = native
    if case == "missing_profit":
        n.change("exit-f2", realized_pnl=None)
    elif case == "missing_fee":
        n.change("entry-f1", fee=None)
    elif case == "unknown_currency":
        n.change("entry-f1", fee_currency=None)
    elif case == "wrong_currency":
        n.change("exit-f2", fee_currency="BTC")
    else:
        n.reseal(n.exit_audit, n.lineage.model_copy(update={"fee_convention": None}))
    result = n.read()
    assert (
        result.status == "unavailable" and result.realized_pnl_after_fees_excluding_funding is None
    )
    assert result.proof is not None and len(result.facts) == 7
    with pytest.raises(ConflictError):
        n.sample()


@pytest.mark.parametrize(
    "case",
    [
        "quantity_gap",
        "live_entry",
        "nonreducing_exit",
        "unexpected_fill",
        "wrong_side",
        "wrong_price",
        "early_exit",
    ],
)
def test_incomplete_closure_and_conflicting_native_facts_fail_closed(native, case):
    n = native
    if case == "quantity_gap":
        n.change("exit-f2", quantity="0.5")
        n.change("exit2", quantity="0.5", filled_quantity="0.5")
    elif case == "live_entry":
        n.change("entry", state="partially_filled")
    elif case == "nonreducing_exit":
        n.change("exit2", reduce_only="false")
    elif case == "unexpected_fill":
        n.persist(n.fill("surprise", "entry", "buy", 1, 3, None))
    elif case == "wrong_side":
        n.change("exit-f2", side="buy")
    elif case == "wrong_price":
        n.change("exit-f2", price="0")
    else:
        n.change("exit-f1", occurred_at_ms=millis(n.start + timedelta(seconds=2)))
    assert n.read().status == "unavailable"
    with pytest.raises(ConflictError):
        n.sample()


def test_terminal_partial_entry_and_partial_exit_quantities_reconcile(native):
    n = native
    n.change("entry", state="partially_canceled")
    n.change("entry-f1", quantity="0.5")
    n.change("entry-f2", quantity="0.5")
    n.change("entry", filled_quantity="1")
    for key in ("exit-f1", "exit-f2"):
        n.change(key, quantity="0.5")
    for key in ("exit1", "exit2"):
        n.change(key, state="partially_canceled", filled_quantity="0.5")
    result = n.read()
    assert result.status == "available" and result.quantity == 1
    assert n.sample().source.value == "blofin_demo"


@pytest.mark.parametrize(
    "case", ["gap", "unexhausted", "outside_window", "stale", "future_sync", "error"]
)
def test_coverage_and_freshness_are_required(native, case):
    n = native
    if case == "gap":
        n.cursor.gap_detected = True
    elif case == "unexhausted":
        n.cursor.window_complete = False
    elif case == "outside_window":
        n.cursor.covered_begin_ms = int(millis(n.start + timedelta(seconds=3)))
    elif case == "stale":
        n.w.now += timedelta(seconds=301)
        n.identity.identity_verified_at = n.w.now
    elif case == "future_sync":
        n.cursor.last_successful_sync = n.w.now + timedelta(seconds=1)
    else:
        n.cursor.last_error_code = "provider_unavailable"
    n.w.session.flush()
    assert n.read().reason == "native_fill_coverage_incomplete_or_stale"


def test_receipt_time_is_retained_and_historical_reads_exclude_delayed_facts(native):
    n = native
    first = n.read()
    assert first.status == "available"
    receipts = {f.native_id: f.first_observed_at for f in first.facts}
    n.w.now -= timedelta(seconds=1)
    assert n.read().reason == "native_fact_provenance_invalid"
    n.w.now += timedelta(seconds=2)
    assert {f.native_id: f.first_observed_at for f in n.read().facts} == receipts


def test_same_uid_credential_rotation_requires_reverification_without_new_history(native):
    n = native
    before = n.read().proof.source_content_hash
    rotated = n.w.settings.model_copy(update={"blofin_readonly_api_key": "fixture-rotated"})
    n.reads.resolver.settings = rotated
    n.reads.domain.settings = rotated
    assert n.read().reason == "experiment_account_unverified"
    n.identity.credential_binding = connection_binding(rotated, readonly=True)
    n.identity.identity_verified_at = n.w.now
    n.w.session.flush()
    assert n.read().proof.source_content_hash == before
    assert n.sample().source_record_id == str(n.command.id)


def test_duplicate_pages_and_attestations_preserve_one_sample_and_original_receipts(native):
    n = native
    before = n.read()
    sample = n.sample()
    n.audit("experiment_native_entry_binding", n.binding, n.start + timedelta(milliseconds=1))
    n.audit("experiment_native_exit_lineage", n.lineage, n.w.now)
    n.cursor.window_complete = False
    n.cursor.native_cursor = None
    BloFinActivityRepository(n.w.session, n.scope).persist_page(
        n.cursor, tuple(f for f in n.native.values() if f.kind == "fill"), {}, n.w.now
    )
    n.cursor.window_complete = True
    n.w.session.flush()
    after = n.read()
    assert (
        after.status == "available"
        and after.proof.source_content_hash == before.proof.source_content_hash
    )
    assert after.facts == before.facts and n.sample().id == sample.id
    assert (
        n.w.session.scalar(
            select(func.count())
            .select_from(ExperimentSampleRow)
            .where(ExperimentSampleRow.version_id == n.version.id)
        )
        == 1
    )


def test_missing_account_or_empty_activity_cannot_infer_closure(native):
    n = native
    n.w.session.execute(
        delete(BloFinActivityFact).where(
            BloFinActivityFact.organization_id == n.scope.organization_id
        )
    )
    assert n.read().reason == "native_lineage_incomplete"
    assert n.performance().reason == "insufficient_samples"


def test_samples_revalidated_after_native_conflict(native):
    n = native
    n.sample()
    n.change("exit-f2", realized_pnl="999")
    result = n.performance()
    assert result.status == "unavailable" and result.sample_count == 0
    assert result.realized_pnl_after_fees_excluding_funding is None


def test_internal_simulation_and_other_tenants_have_no_native_read_path(native):
    n = native
    simulation = n.w.running()
    with pytest.raises(ConflictError, match="Internal simulation"):
        n.reads.performance(simulation.experiment_id, simulation.id, "baseline")
    other = n.w.tenant.__class__(uuid4(), uuid4(), "other@fixture.test", n.w.tenant.membership_role)
    reader = BloFinExperimentReads(n.w.session, n.w.settings, other, clock=lambda: n.w.now)
    with pytest.raises(NotFoundError):
        reader.performance(n.version.experiment_id, n.version.id, "baseline")


def test_paused_entry_and_preexisting_validation_facts_do_not_qualify(native):
    from app.schemas.experiments import ExperimentVersionCreate

    n = native
    # A new Validation version has fresh identity and start; old lineage cannot migrate.
    config = n.version.configuration.model_copy(update={"mode": ExperimentMode.VALIDATION})
    new = n.w.service.fork(
        n.w.tenant,
        n.version.experiment_id,
        ExperimentVersionCreate(parent_version_id=n.version.id, configuration=config),
    )
    new = n.w.transition(n.w.approve(n.w.transition(new, "submit")), "start")
    assert (
        n.reads.outcome(new.experiment_id, new.id, "baseline", str(n.command.id)).reason
        == "experiment_attribution_mismatch"
    )
    assert (
        n.reads.performance(new.experiment_id, new.id, "baseline").reason == "insufficient_samples"
    )


def test_arithmetic_is_decimal_and_independent_of_callers_context(native):
    n = native
    with localcontext() as context:
        context.prec = 4
        result = n.read()
        assert result.realized_pnl_after_fees_excluding_funding == Decimal("1.600000000000000001")
        n.sample()
        assert n.performance().win_rate == 1


def test_signed_cashflow_fees_and_rebates_are_explicit(native):
    n = native
    n.reseal(n.exit_audit, n.lineage.model_copy(update={"fee_convention": "signed_cashflow"}))
    for identity in ("entry-f1", "entry-f2", "exit-f1"):
        n.change(identity, fee="-0.1")
    n.change("exit-f2", fee="0.05")
    assert n.read().fee_cost == Decimal("0.25")
    assert n.read().realized_pnl_after_fees_excluding_funding == Decimal("1.750000000000000001")


@pytest.mark.parametrize("native", [2], indirect=True)
def test_insufficient_reconciled_samples_keep_all_performance_unavailable(native):
    native.sample()
    result = native.performance()
    assert result.status == "unavailable" and result.reason == "insufficient_samples"
    assert result.sample_count == 1 and result.required_sample_count == 2
    assert result.reported_realized_pnl is None and result.win_rate is None


def test_entry_after_pause_is_unavailable_even_with_pre_pause_binding(native):
    n = native
    n.w.now = n.start
    n.w.transition(n.version, "pause")
    n.w.now = n.start + timedelta(seconds=6)
    assert n.read().reason == "experiment_sample_window"
    with pytest.raises(ConflictError):
        n.sample()


def test_fresh_validation_cannot_rebind_preexisting_closed_native_trade(native):
    from app.schemas.experiments import ExperimentVersionCreate

    n = native
    config = n.version.configuration.model_copy(update={"mode": ExperimentMode.VALIDATION})
    child = n.w.service.fork(
        n.w.tenant,
        n.version.experiment_id,
        ExperimentVersionCreate(parent_version_id=n.version.id, configuration=config),
    )
    child = n.w.transition(n.w.approve(n.w.transition(child, "submit")), "start")
    fresh = n.binding.model_copy(
        update={
            "version_id": child.id,
            "sample_group_id": child.sample_group_id,
            "configuration_hash": child.configuration_hash,
        }
    )
    n.reseal(n.entry_audit, fresh)
    n.entry_audit.created_at = child.started_at
    n.reseal(
        n.exit_audit,
        n.lineage.model_copy(update={"entry_binding_hash": semantic_hash(fresh.model_dump())}),
    )
    n.w.session.flush()
    assert (
        n.reads.outcome(child.experiment_id, child.id, "baseline", str(n.command.id)).reason
        == "experiment_sample_window"
    )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1e99", "0.0000000000000000001"])
def test_unbounded_or_nonfinite_native_money_is_unavailable(native, value):
    native.change("exit-f2", realized_pnl=value)
    assert native.read().status == "unavailable"
    with pytest.raises(ConflictError):
        native.sample()


def test_duplicate_native_identity_with_conflicting_content_is_refused(native):
    from app.repositories.blofin_activity import ActivityConflictError

    n = native
    n.cursor.window_complete = False
    n.cursor.native_cursor = None
    conflict = n.native["exit-f2"].model_copy(update={"realized_pnl": "9000"})
    with pytest.raises(ActivityConflictError):
        BloFinActivityRepository(n.w.session, n.scope).persist_page(
            n.cursor, (conflict,), {}, n.w.now
        )
    n.cursor.window_complete = True
    n.w.session.flush()
    assert n.read().reported_realized_pnl == Decimal("2.000000000000000001")


def test_integration_example_uses_actual_domain_consumer_without_network(native):
    from examples.blofin_experiment_outcomes import admit_native_sample, read_native_performance

    n = native
    requests = len(n.requests)
    sample = admit_native_sample(
        n.w.session,
        n.w.settings,
        n.w.tenant,
        n.version.experiment_id,
        n.version.id,
        ExperimentSampleCreate(variant_key="baseline", source_record_id=str(n.command.id)),
        now=n.w.now,
    )
    result = read_native_performance(
        n.w.session,
        n.w.settings,
        n.w.tenant,
        n.version.experiment_id,
        n.version.id,
        "baseline",
        now=n.w.now,
    )
    assert (
        result.status == "available"
        and result.outcomes[0].proof.source_record_id == sample.source_record_id
    )
    assert len(n.requests) == requests


def test_contract_refuses_available_with_missing_profit_or_insufficient_sample(native):
    from pydantic import ValidationError

    from app.experiments.outcome_contract import ExperimentPerformance, NativeOutcome

    native.sample()
    with pytest.raises(ValidationError):
        NativeOutcome.model_validate({**native.read().model_dump(), "reported_realized_pnl": None})
    with pytest.raises(ValidationError):
        ExperimentPerformance.model_validate(
            {**native.performance().model_dump(), "required_sample_count": 2}
        )


def add_independent_trade(n, label, pnl, *, share_exits=False):
    """Second independently bound immutable native command, never a venue submit."""
    old_command = n.command
    command = ExecutionCommand(
        organization_id=old_command.organization_id,
        user_id=old_command.user_id,
        account_id=old_command.account_id,
        operation="SUBMIT_ENTRY",
        operation_namespace=old_command.operation_namespace,
        plan_id=old_command.plan_id,
        revision_id=old_command.revision_id,
        authorization_id=old_command.authorization_id,
        plan_content_hash=old_command.plan_content_hash,
        canonical_payload_hash="e" * 64,
        opaque_idempotency_key=uuid4().hex,
        correlation_id=uuid4(),
        outcome=ExecutionCommandOutcome.ALLOW,
        created_at=n.start,
        updated_at=n.start,
    )
    session = n.w.session
    session.add(command)
    session.flush()
    receipt = ExecutionReceipt(
        command_id=command.id,
        operation="SUBMIT_ENTRY",
        authorization_id=command.authorization_id,
        organization_id=command.organization_id,
        user_id=command.user_id,
        account_id=command.account_id,
    )
    session.add(receipt)
    session.flush()
    effect = VenueSubmitEffect(
        command_id=command.id,
        receipt_id=receipt.id,
        client_order_id=label + "-" + uuid4().hex,
        safety_epoch=0,
    )
    session.add(effect)
    session.flush()
    entries = tuple(label + "-" + i for i in n.lineage.entry_fill_ids)
    exits = (
        n.lineage.exit_fill_ids
        if share_exits
        else tuple(label + "-" + i for i in n.lineage.exit_fill_ids)
    )
    exit_orders = (
        n.lineage.native_exit_order_ids
        if share_exits
        else tuple(label + "-" + i for i in n.lineage.native_exit_order_ids)
    )
    binding = n.binding.model_copy(update={"ownership_command_id": command.id})
    lineage = n.lineage.model_copy(
        update={
            "entry_binding_hash": semantic_hash(binding.model_dump()),
            "native_entry_order_id": label + "-entry",
            "entry_fill_ids": entries,
            "exit_fill_ids": exits,
            "native_exit_order_ids": exit_orders,
        }
    )
    n.command = command
    n.audit("experiment_native_entry_binding", binding, n.start)
    n.audit("experiment_native_exit_lineage", lineage, n.start + timedelta(seconds=5))
    n.command = old_command
    for fact in n.native.values():
        if share_exits and (fact.order_id != "entry"):
            continue
        order_id = label + "-" + fact.order_id
        identity = label + "-" + fact.native_id
        updates = {"order_id": order_id, "native_id": identity}
        if fact.kind == "fill":
            updates["trade_id"] = identity
            if fact.native_id == "exit-f1":
                updates["realized_pnl"] = pnl
            elif fact.native_id == "exit-f2":
                updates["realized_pnl"] = "0"
        elif fact.order_id == "entry":
            updates["client_order_id"] = effect.client_order_id
        n.persist(fact.model_copy(update=updates))
    return command


def test_multiple_reconciled_outcomes_round_win_rate_and_keep_native_funding_unknown(native):
    n = native
    n.sample()
    for label, pnl in [("winner", "2"), ("loser", "-1")]:
        command = add_independent_trade(n, label, pnl)
        n.reads.record_sample(
            n.version.experiment_id,
            n.version.id,
            ExperimentSampleCreate(variant_key="baseline", source_record_id=str(command.id)),
        )
    result = n.performance()
    assert result.status == "available" and result.sample_count == 3
    assert result.win_rate == Decimal("0.666666666666666667")
    assert result.realized_pnl_after_fees_excluding_funding == Decimal("1.800000000000000001")
    assert result.fee_cost == Decimal("1.2") and result.funding is None


def test_shared_exit_order_or_fill_cannot_be_counted_for_another_entry(native):
    n = native
    n.sample()
    second = add_independent_trade(n, "duplicate", "0", share_exits=True)
    result = n.reads.outcome(n.version.experiment_id, n.version.id, "baseline", str(second.id))
    assert result.reason == "native_lineage_already_attributed"
    with pytest.raises(ConflictError):
        n.reads.record_sample(
            n.version.experiment_id,
            n.version.id,
            ExperimentSampleCreate(variant_key="baseline", source_record_id=str(second.id)),
        )
    assert n.performance().sample_count == 1


def test_postgres_attribution_lock_covers_competing_versions_until_commit(
    native, experiment_engine
):
    import hashlib

    from sqlalchemy import text

    n = native
    n.sample()
    scope = f"experiment-outcome:{n.w.tenant.organization_id}:{n.account.native_uid}"
    key = int.from_bytes(hashlib.sha256(scope.encode()).digest()[:8], "big") >> 1
    with experiment_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}) is False
        )


def test_large_exact_native_money_survives_output_scale_and_aggregation(native):
    native.change("exit-f1", realized_pnl="123456789012345678901234567890")
    native.change("exit-f2", realized_pnl="0")
    native.sample()
    result = native.performance()
    assert result.status == "available"
    assert result.realized_pnl_after_fees_excluding_funding == Decimal(
        "123456789012345678901234567889.6"
    )


@pytest.mark.parametrize("case", ["provider_event", "actual_receipt", "audit_event"])
def test_future_facts_remain_unavailable_without_backdating(native, case):
    n = native
    if case == "provider_event":
        n.change("exit-f2", occurred_at_ms=millis(n.w.now + timedelta(seconds=1)))
    elif case == "actual_receipt":
        row = n.w.session.get(BloFinActivityFact, (*n.scope.key(), "fill", "exit-f2"))
        row.first_observed_at = n.w.now + timedelta(seconds=1)
    else:
        n.entry_audit.event_at = n.entry_audit.created_at + timedelta(seconds=1)
    n.w.session.flush()
    result = n.read()
    assert result.status == "unavailable"
    with pytest.raises(ConflictError):
        n.sample()


def test_native_trade_identity_alias_must_match_stored_primary_id(native):
    native.change("exit-f2", trade_id="different-trade-id")
    assert native.read().reason == "native_fact_provenance_invalid"
    with pytest.raises(ConflictError):
        native.sample()
