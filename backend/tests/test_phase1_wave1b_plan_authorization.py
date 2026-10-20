"""Phase 1 Wave 1B: immutable plans, exact authorizations, and canonical payload V1."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, TradingPolicyError, ValidationAppError
from app.db.base import Base
from app.db.models import (
    ApprovalAuthorization as ApprovalAuthorizationModel,
)
from app.db.models import (
    AuditLog,
    ExchangeAccount,
    ExecutionAccount,
    ExecutionCommand,
    ExecutionIdempotencyBinding,
    JournalLifecycleEvent,
    Order,
    Organization,
    PaperValidationAlert,
    PaperValidationCandidate,
    PaperValidationDraft,
    PlanEntryExecutionClaim,
    SetupDefinition,
    User,
    UserStrategy,
    UserStrategyVersion,
    VenueSubmitEffect,
)
from app.db.models import (
    TradePlanRevision as TradePlanRevisionModel,
)
from app.db.models import TradeProposal as TradeProposalModel
from app.learning_attribution.contracts import DecisionActor
from app.learning_attribution.quality import decision_actor_for, human_plan_approved
from app.schemas.approval import (
    ApprovalAuthorization,
    ApprovalAuthorizationAssertion,
    ApprovalAuthorizationIssuance,
    ApprovalDecisionRequest,
)
from app.schemas.canonical_execution import (
    CanonicalAccountBindingV1,
    CanonicalExecutionPayloadV1,
    CanonicalExecutionPrincipalV1,
)
from app.schemas.common import (
    ApprovalAction,
    AuditEventType,
    ExchangeAccountStatus,
    JournalLifecycleEventType,
    JournalTradeStatus,
    PaperAlertType,
    RiskSeverity,
    SetupCategory,
    StrategyId,
)
from app.schemas.execution_protocol import ExecutePaperPlanRequest, ExecutionCommandOutcome
from app.schemas.proposal import ExitCriteria, TakeProfitLevel, TradeProposalCreate
from app.schemas.risk import KillSwitchMutationRequest
from app.schemas.trade_plan import (
    AccountMode,
    AuthorizationChannel,
    AuthorizationDecision,
    AuthorizationState,
    EntryOrderType,
    EntrySide,
    ExecutionMode,
    MarginMode,
    PlanPresentationMetadata,
    SemanticAmount,
    TimeInForce,
    TradePlanRevision,
    TradePlanRevisionCreate,
    TradePlanRevisionSemantic,
)
from app.services.approval_authorization_hash import (
    authorization_issuance,
    verify_authorization_issuance_hash,
)
from app.services.approval_service import (
    CHANNEL_MISMATCH_REASON,
    ApprovalService,
    assert_human_may_execute,
)
from app.services.audit_service import AuditService
from app.services.automated_paper_loop import AutomatedPaperLoop, _execute_request
from app.services.canonical_execution_payload import CanonicalExecutionPayloadSerializerV1
from app.services.canonical_paper_execution import _attach_approval_channel
from app.services.canonical_serialization import canonical_json_bytes, canonical_sha256
from app.services.governed_blofin_demo import DEMO_POLICY, GovernedBloFinDemoLoop
from app.services.mappers.trade_plan_mapper import trade_plan_revision_to_schema
from app.services.proposal_service import ProposalService
from app.services.risk.kill_switch import KillSwitchService
from tests.support.phase1_plan_fixtures import (
    EXECUTE_AT,
    execute_request,
    execution_service,
    prepared_authorized_plan,
)

NOW = datetime(2026, 9, 15, 14, 0, tzinfo=UTC)
CORRELATION_ID = uuid.UUID("09000000-0000-0000-0000-000000000001")


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        yield db
    engine.dispose()


def _seed_support(session: Session) -> dict[str, Any]:
    organization = Organization(name=f"Wave 1B {uuid.uuid4()}")
    other_organization = Organization(name=f"Other {uuid.uuid4()}")
    user = User(email=f"plan-{uuid.uuid4()}@example.com", hashed_password="not-a-real-hash")
    other_user = User(email=f"other-{uuid.uuid4()}@example.com", hashed_password="not-a-real-hash")
    session.add_all([organization, other_organization, user, other_user])
    session.flush()

    strategy = UserStrategy(
        organization_id=organization.id,
        user_id=user.id,
        name="Wave 1B strategy",
        setup_type=StrategyId.HTF_TREND_PULLBACK,
    )
    session.add(strategy)
    session.flush()
    strategy_version = UserStrategyVersion(
        strategy_id=strategy.id,
        version=1,
        card={"strategy_name": "Wave 1B"},
    )
    setup = SetupDefinition(
        name=f"Wave 1B setup {uuid.uuid4()}",
        strategy_id=StrategyId.HTF_TREND_PULLBACK,
        category=SetupCategory.TREND,
        version=1,
    )
    session.add_all([strategy_version, setup])
    session.flush()

    alert = PaperValidationAlert(
        organization_id=organization.id,
        user_id=user.id,
        alert_type=PaperAlertType.SETUP_SIGNAL_DETECTED,
        message="Complete deterministic evidence is available.",
    )
    session.add(alert)
    session.flush()
    draft = PaperValidationDraft(
        organization_id=organization.id,
        source_alert_id=alert.id,
        symbol="BTCUSDT",
        timeframe="4h",
        direction="long",
        review_status="approved",
        created_by=user.id,
        status="ready",
    )
    session.add(draft)
    session.flush()
    candidate = PaperValidationCandidate(
        organization_id=organization.id,
        draft_id=draft.id,
        source_alert_id=alert.id,
        symbol="BTCUSDT",
        timeframe="4h",
        direction="long",
        created_by=user.id,
        strategy_id=strategy.id,
        strategy_version_id=strategy_version.id,
    )
    account_one = ExecutionAccount(
        organization_id=organization.id,
        user_id=user.id,
        name="Primary paper account",
        execution_mode=ExecutionMode.PAPER,
        account_mode=AccountMode.NET,
    )
    account_two = ExecutionAccount(
        organization_id=organization.id,
        user_id=user.id,
        name="Secondary paper account",
        execution_mode=ExecutionMode.PAPER,
        account_mode=AccountMode.NET,
    )
    wrong_user_account = ExecutionAccount(
        organization_id=organization.id,
        user_id=other_user.id,
        name="Other principal paper account",
        execution_mode=ExecutionMode.PAPER,
        account_mode=AccountMode.NET,
    )
    exchange_account = ExchangeAccount(
        organization_id=organization.id,
        user_id=user.id,
        exchange="blofin",
        api_key_ref="secret-store://demo-key",
        has_withdrawal_permission=False,
        status=ExchangeAccountStatus.ACTIVE,
    )
    other_exchange_account = ExchangeAccount(
        organization_id=organization.id,
        user_id=other_user.id,
        exchange="blofin",
        api_key_ref="secret-store://other-demo-key",
        has_withdrawal_permission=False,
        status=ExchangeAccountStatus.ACTIVE,
    )
    session.add_all(
        [
            candidate,
            account_one,
            account_two,
            wrong_user_account,
            exchange_account,
            other_exchange_account,
        ]
    )
    session.flush()

    proposal = ProposalService(session, AuditService(session)).create(
        TradeProposalCreate(
            organization_id=organization.id,
            user_id=user.id,
            strategy_id=StrategyId.HTF_TREND_PULLBACK,
            user_strategy_id=strategy.id,
            symbol="BTCUSDT",
            timeframe="4h",
            direction="long",
            entry_price=Decimal("100"),
            position_size=Decimal("2"),
            leverage=Decimal("2"),
            exit=ExitCriteria(
                invalidation="Close below the exact stop.",
                stop_loss=Decimal("95"),
                take_profits=[
                    TakeProfitLevel(price=Decimal("110"), size_fraction=0.5),
                    TakeProfitLevel(price=Decimal("120"), size_fraction=0.5),
                ],
            ),
            confidence=0.8,
            risk_level=RiskSeverity.MEDIUM,
            rationale="Complete deterministic test plan.",
            approval_required=True,
        )
    )
    session.commit()
    return {
        "organization": organization,
        "other_organization": other_organization,
        "user": user,
        "other_user": other_user,
        "strategy_version": strategy_version,
        "setup": setup,
        "candidate": candidate,
        "account_one": account_one,
        "account_two": account_two,
        "wrong_user_account": wrong_user_account,
        "exchange_account": exchange_account,
        "other_exchange_account": other_exchange_account,
        "proposal_id": proposal.id,
        "correlation_id": CORRELATION_ID,
    }


def _plan_request(ids: dict[str, Any], **updates: Any) -> TradePlanRevisionCreate:
    payload: dict[str, Any] = {
        "account_id": ids["account_one"].id,
        "exchange_account_id": ids["exchange_account"].id,
        "strategy_version_id": ids["strategy_version"].id,
        "setup_definition_id": ids["setup"].id,
        "candidate_id": ids["candidate"].id,
        "permission_attestation_id": uuid.UUID("10000000-0000-0000-0000-000000000001"),
        "permission_attestation_version": "blofin-demo-permissions-v1",
        "evidence_ids": [
            uuid.UUID("20000000-0000-0000-0000-000000000001"),
            uuid.UUID("20000000-0000-0000-0000-000000000002"),
        ],
        "evidence_venue": "BINANCE",
        "evidence_market": "PERPETUAL",
        "evidence_instrument": "BTC-USDT-PERP",
        "evidence_observed_at": NOW - timedelta(seconds=30),
        "evidence_freshness_seconds": 60,
        "evidence_is_live": True,
        "evidence_fallback_used": False,
        "evidence_sequence_complete": True,
        "evidence_final": True,
        "execution_venue": "BLOFIN_DEMO",
        "execution_market": "PERPETUAL",
        "execution_instrument": "BTC-USDT",
        "timeframe": "4h",
        "instrument_mapping_version": "blofin-btc-usdt-v1",
        "side": "BUY",
        "quantity": {"value": "2.000", "unit": "CONTRACTS"},
        "quantity_unit": "CONTRACTS",
        "order_type": "MARKET",
        "time_in_force": "IOC",
        "limit_price": None,
        "market_marker": True,
        "entry_zone": {"lower": "99.50", "upper": "100.50", "price_unit": "USDT"},
        "entry_zone_derivation": {
            "formula_id": "pattern-trigger-intersection",
            "formula_version": "1",
        },
        "slippage_policy": {
            "policy_id": "conservative-entry",
            "policy_version": "1",
            "maximum_bps": "15.0",
        },
        "reduce_only": False,
        "margin_mode": "CROSS",
        "position_mode": "NET",
        "instrument_rules": {
            "contract_multiplier": "1",
            "contract_type": "LINEAR",
            "base_currency": "BTC",
            "quote_currency": "USDT",
            "settlement_currency": "USDT",
            "tick_size": "0.10",
            "lot_size": "1",
            "minimum_quantity": "1",
            "minimum_notional": "5",
            "rules_version": "blofin-rules-2026-09-15",
        },
        "basis_policy": {
            "policy_id": "cross-venue-basis",
            "policy_version": "1",
            "evidence_price": {"value": "100.00", "unit": "USDT"},
            "execution_price": {"value": "100.10", "unit": "USDT"},
            "formula": "(execution_price-evidence_price)/evidence_price",
            "timestamp": NOW - timedelta(seconds=20),
            "tolerance_bps": "20",
            "freshness_seconds": 60,
        },
        "risk_and_exits": {
            "risk_budget": {"value": "10", "unit": "USDT"},
            "maximum_loss": {"value": "12", "unit": "USDT"},
            "fee_allowance": {"value": "1", "unit": "USDT"},
            "funding_allowance": {"value": "0.25", "unit": "USDT"},
            "slippage_allowance": {"value": "0.75", "unit": "USDT"},
            "stop": {"value": "95", "unit": "USDT"},
            "targets": [
                {
                    "order": 1,
                    "price": {"value": "110", "unit": "USDT"},
                    "quantity_fraction": "0.50",
                    "derivation": {"formula_id": "nearest-structure", "formula_version": "1"},
                },
                {
                    "order": 2,
                    "price": {"value": "120", "unit": "USDT"},
                    "quantity_fraction": "0.25",
                    "derivation": {"formula_id": "next-structure", "formula_version": "1"},
                },
            ],
            "runner": {
                "enabled": True,
                "activation_target_order": 2,
                "remaining_quantity_fraction": "0.25",
                "rule_id": "atr-trailing-runner",
                "rule_version": "1",
                "expression": "trail_by_atr_after_target_2",
            },
            "leverage": "2",
            "margin_assumption_id": "cross-margin-conservative",
            "margin_assumption_version": "1",
        },
        "valid_from": NOW,
        "valid_until": NOW + timedelta(hours=1),
        "calculation_inputs": [
            {
                "name": "rounded_contract_quantity",
                "input_value": "2.000",
                "result_value": "2",
                "unit": "CONTRACTS",
                "formula_id": "floor-to-lot",
                "formula_version": "1",
                "precision": 0,
                "rounding_mode": "ROUND_FLOOR",
                "conservative_remainder": "0",
            }
        ],
        "execution_policy_version": "paper-entry-policy-v1",
        "presentation_metadata": {"channel": "WEB", "display_title": "BTC setup"},
    }
    payload.update(updates)
    return TradePlanRevisionCreate.model_validate(payload)


def _semantic(
    request: TradePlanRevisionCreate,
    ids: dict[str, Any],
    *,
    revision_id: uuid.UUID | None = None,
) -> TradePlanRevisionSemantic:
    return TradePlanRevisionSemantic(
        plan_id=ids["proposal_id"],
        revision_id=revision_id or uuid.UUID("30000000-0000-0000-0000-000000000001"),
        organization_id=ids["organization"].id,
        user_id=ids["user"].id,
        **request.semantic_terms(),
    )


def _persist_plan(
    session: Session,
    ids: dict[str, Any],
    request: TradePlanRevisionCreate | None = None,
) -> TradePlanRevision:
    """Persist a trusted test fixture without representing a production authority."""
    data = request or _plan_request(ids)
    semantic = _semantic(data, ids, revision_id=uuid.uuid4())
    row = TradePlanRevisionModel(
        id=semantic.revision_id,
        plan_id=semantic.plan_id,
        organization_id=semantic.organization_id,
        user_id=semantic.user_id,
        account_id=semantic.account_id,
        exchange_account_id=semantic.exchange_account_id,
        schema_version=semantic.schema_version,
        operation=semantic.operation,
        strategy_version_id=semantic.strategy_version_id,
        setup_definition_id=semantic.setup_definition_id,
        candidate_id=semantic.candidate_id,
        expected_account_mode=semantic.expected_account_mode,
        permission_attestation_id=semantic.permission_attestation_id,
        permission_attestation_version=semantic.permission_attestation_version,
        execution_venue=semantic.execution_venue,
        execution_instrument=semantic.execution_instrument,
        execution_policy_version=semantic.execution_policy_version,
        valid_from=semantic.valid_from,
        valid_until=semantic.valid_until,
        semantic_payload=semantic.model_dump(mode="json"),
        correlation_id=ids["correlation_id"],
        content_hash=canonical_sha256(semantic),
        presentation_metadata=data.presentation_metadata.model_dump(mode="json"),
    )
    proposal = session.get(TradeProposalModel, ids["proposal_id"])
    assert proposal is not None
    session.add(row)
    proposal.latest_plan_revision_id = row.id
    session.flush()
    return trade_plan_revision_to_schema(row)


def _approve_plan(
    session: Session,
    ids: dict[str, Any],
    plan: TradePlanRevision,
    *,
    clock: datetime = NOW + timedelta(minutes=1),
) -> tuple[ApprovalService, ApprovalAuthorization]:
    service = ApprovalService(session, AuditService(session), clock=lambda: clock)
    approval = service.create_for_plan_revision(
        revision_id=plan.revision_id,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
    )
    decided = service.decide(
        approval.id,
        ApprovalDecisionRequest(action=ApprovalAction.APPROVE),
        principal_organization_id=plan.organization_id,
        principal_user_id=plan.user_id,
    )
    assert decided.authorization is not None
    return service, decided.authorization


def test_trade_plan_hash_binds_every_mutated_execution_semantic(session: Session) -> None:
    ids = _seed_support(session)
    base = _semantic(_plan_request(ids), ids)
    mutations = [
        base.model_copy(update={"side": EntrySide.SELL}),
        base.model_copy(update={"quantity": SemanticAmount(value=Decimal("3"), unit="CONTRACTS")}),
        base.model_copy(update={"time_in_force": TimeInForce.FOK}),
        base.model_copy(update={"execution_instrument": "ETH-USDT"}),
        base.model_copy(update={"instrument_mapping_version": "mapping-v2"}),
        base.model_copy(update={"account_id": ids["account_two"].id}),
    ]
    base_hash = canonical_sha256(base)
    assert all(canonical_sha256(mutated) != base_hash for mutated in mutations)


def test_presentation_metadata_is_excluded_and_decimal_encoding_is_canonical(
    session: Session,
) -> None:
    ids = _seed_support(session)
    first = _plan_request(
        ids,
        presentation_metadata={"channel": "WEB", "display_title": "First title"},
    )
    second = _plan_request(
        ids,
        quantity={"value": "2.0", "unit": "CONTRACTS"},
        presentation_metadata={"channel": "TELEGRAM", "display_title": "Second title"},
    )
    first_semantic = _semantic(first, ids)
    second_semantic = _semantic(second, ids)
    assert canonical_sha256(first_semantic) == canonical_sha256(second_semantic)
    assert b'"value":{"scale":0,"value":"2"}' in canonical_json_bytes(first_semantic)


def test_ordered_evidence_and_targets_are_stable_and_semantic(session: Session) -> None:
    ids = _seed_support(session)
    plan = _semantic(_plan_request(ids), ids)
    assert canonical_json_bytes(plan) == canonical_json_bytes(plan.model_copy())
    reversed_evidence = plan.model_copy(update={"evidence_ids": tuple(reversed(plan.evidence_ids))})
    assert canonical_sha256(reversed_evidence) != canonical_sha256(plan)
    changed_target = plan.risk_and_exits.targets[0].model_copy(
        update={"price": SemanticAmount(value=Decimal("111"), unit="USDT")}
    )
    changed_exits = plan.risk_and_exits.model_copy(
        update={"targets": (changed_target, *plan.risk_and_exits.targets[1:])}
    )
    assert canonical_sha256(plan.model_copy(update={"risk_and_exits": changed_exits})) != (
        canonical_sha256(plan)
    )


def test_incomplete_or_binary_float_plan_input_cannot_be_executable(session: Session) -> None:
    ids = _seed_support(session)
    payload = _plan_request(ids).model_dump(mode="python")
    payload.pop("quantity")
    with pytest.raises(ValidationError):
        TradePlanRevisionCreate.model_validate(payload)
    with pytest.raises(ValidationError, match="Binary floating-point"):
        _plan_request(ids, quantity={"value": 2.5, "unit": "CONTRACTS"})
    with pytest.raises(ValidationError):
        _plan_request(ids, evidence_fallback_used=True)
    with pytest.raises(ValidationError):
        _plan_request(ids, order_type="MARKET", market_marker=False, limit_price=None)


def test_executable_plan_construction_fails_closed_without_authoritative_inputs(
    session: Session,
) -> None:
    ids = _seed_support(session)
    caller_supplied = _plan_request(
        ids,
        permission_attestation_id=uuid.UUID("ffffffff-0000-0000-0000-000000000001"),
        permission_attestation_version="caller-invented",
    )
    with pytest.raises(
        ValidationAppError,
        match="ANALYSIS_ONLY_CANNOT_CREATE_EXECUTABLE_PLAN",
    ):
        ProposalService(session, AuditService(session)).create_revision(
            ids["proposal_id"],
            caller_supplied,
            organization_id=ids["organization"].id,
            user_id=ids["user"].id,
        )
    assert session.query(TradePlanRevisionModel).count() == 0


def test_plan_revision_is_tenant_account_scoped_and_immutable(session: Session) -> None:
    ids = _seed_support(session)
    with pytest.raises(IntegrityError), session.begin_nested():
        _persist_plan(
            session,
            ids,
            _plan_request(ids, account_id=ids["wrong_user_account"].id),
        )
    with pytest.raises(IntegrityError), session.begin_nested():
        _persist_plan(
            session,
            ids,
            _plan_request(ids, exchange_account_id=ids["other_exchange_account"].id),
        )

    plan = _persist_plan(session, ids)
    session.commit()
    row = session.get(TradePlanRevisionModel, plan.revision_id)
    assert row is not None
    row.execution_instrument = "MUTATED"
    with pytest.raises(ValueError, match="immutable"):
        session.flush()
    session.rollback()


def test_approve_issues_only_one_authorization_and_has_no_order_side_effect(
    session: Session,
) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    service = ApprovalService(
        session,
        AuditService(session),
        clock=lambda: NOW + timedelta(minutes=1),
    )
    approval = service.create_for_plan_revision(
        revision_id=plan.revision_id,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
    )
    before_orders = session.query(Order).count()
    first = service.decide(
        approval.id,
        ApprovalDecisionRequest(action=ApprovalAction.APPROVE),
        principal_organization_id=plan.organization_id,
        principal_user_id=plan.user_id,
    )
    second = service.decide(
        approval.id,
        ApprovalDecisionRequest(action=ApprovalAction.APPROVE),
        principal_organization_id=plan.organization_id,
        principal_user_id=plan.user_id,
    )
    assert first.authorization is not None
    assert second.authorization is not None
    assert first.authorization.authorization_id == second.authorization.authorization_id
    assert first.authorization.correlation_id == plan.correlation_id
    assert verify_authorization_issuance_hash(first.authorization)
    assert session.query(ApprovalAuthorizationModel).count() == 1
    assert session.query(Order).count() == before_orders == 0


def test_wrong_principal_or_organization_cannot_authorize(session: Session) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    service = ApprovalService(
        session,
        AuditService(session),
        clock=lambda: NOW + timedelta(minutes=1),
    )
    approval = service.create_for_plan_revision(
        revision_id=plan.revision_id,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
    )
    with pytest.raises(ValidationAppError, match="user"):
        service.decide(
            approval.id,
            ApprovalDecisionRequest(action=ApprovalAction.APPROVE),
            principal_organization_id=plan.organization_id,
            principal_user_id=ids["other_user"].id,
        )
    with pytest.raises(ValidationAppError, match="organization"):
        service.decide(
            approval.id,
            ApprovalDecisionRequest(action=ApprovalAction.APPROVE),
            principal_organization_id=ids["other_organization"].id,
            principal_user_id=plan.user_id,
        )
    with pytest.raises(NotFoundError):
        service.issue_authorization(
            approval_id=approval.id,
            decision=AuthorizationDecision.APPROVE,
            organization_id=ids["other_organization"].id,
            user_id=plan.user_id,
            channel=AuthorizationChannel.API,
        )
    assert session.query(ApprovalAuthorizationModel).count() == 0


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("organization_id", uuid.UUID("40000000-0000-0000-0000-000000000005")),
        ("user_id", uuid.UUID("40000000-0000-0000-0000-000000000006")),
        ("account_id", uuid.UUID("40000000-0000-0000-0000-000000000001")),
        ("exchange_account_id", uuid.UUID("40000000-0000-0000-0000-000000000002")),
        ("revision_id", uuid.UUID("40000000-0000-0000-0000-000000000003")),
        ("plan_id", uuid.UUID("40000000-0000-0000-0000-000000000004")),
        ("plan_content_hash", "f" * 64),
    ],
)
def test_wrong_plan_or_account_assertion_is_rejected(
    session: Session,
    field: str,
    wrong_value: object,
) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    service = ApprovalService(
        session,
        AuditService(session),
        clock=lambda: NOW + timedelta(minutes=1),
    )
    approval = service.create_for_plan_revision(
        revision_id=plan.revision_id,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
    )
    assertion = ApprovalAuthorizationAssertion(
        organization_id=plan.organization_id,
        user_id=plan.user_id,
        account_id=plan.account_id,
        exchange_account_id=plan.exchange_account_id,
        operation=plan.operation,
        plan_id=plan.plan_id,
        revision_id=plan.revision_id,
        plan_content_hash=plan.content_hash,
    ).model_copy(update={field: wrong_value})
    with pytest.raises(ValidationAppError, match="does not match"):
        service.decide(
            approval.id,
            ApprovalDecisionRequest(
                action=ApprovalAction.APPROVE,
                authorization_assertion=assertion,
            ),
            principal_organization_id=plan.organization_id,
            principal_user_id=plan.user_id,
        )
    assert session.query(ApprovalAuthorizationModel).count() == 0


def test_post_approval_recheck_revoke_writes_an_audit_row(session: Session) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    issuing_service, authorization = _approve_plan(session, ids, plan)
    revoked = issuing_service.revoke_authorization(
        authorization.authorization_id,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
        reason="post_approval_recheck",
    )
    assert revoked.state is AuthorizationState.REVOKED
    revoke_audits = [
        row
        for row in session.scalars(select(AuditLog)).all()
        if row.action is AuditEventType.APPROVAL_DECISION
        and (row.redacted_metadata or {}).get("reason") == "post_approval_recheck"
        and (row.redacted_metadata or {}).get("action") == "revoke"
    ]
    assert len(revoke_audits) == 1
    assert revoke_audits[0].resource_id == str(authorization.authorization_id)
    assert revoke_audits[0].resource_type == "approval"
    issuing_service.revoke_authorization(
        authorization.authorization_id,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
        reason="post_approval_recheck",
    )
    again = [
        row
        for row in session.scalars(select(AuditLog)).all()
        if row.action is AuditEventType.APPROVAL_DECISION
        and (row.redacted_metadata or {}).get("reason") == "post_approval_recheck"
    ]
    assert len(again) == 1


def _approve_autonomous(
    session: Session,
    ids: dict[str, Any],
    plan: TradePlanRevision,
) -> tuple[ApprovalService, ApprovalAuthorization]:
    service = ApprovalService(
        session, AuditService(session), clock=lambda: NOW + timedelta(minutes=1)
    )
    approval = service.create_for_plan_revision(
        revision_id=plan.revision_id,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
    )
    decided = service.decide(
        approval.id,
        ApprovalDecisionRequest(action=ApprovalAction.APPROVE),
        principal_organization_id=plan.organization_id,
        principal_user_id=plan.user_id,
        channel=AuthorizationChannel.AUTONOMOUS,
        actor_type="SYSTEM",
    )
    assert decided.authorization is not None
    return service, decided.authorization


def test_create_all_accepts_autonomous_and_rejects_an_invalid_channel(session: Session) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    _service, authorization = _approve_autonomous(session, ids, plan)
    assert authorization.channel is AuthorizationChannel.AUTONOMOUS
    table_sql = session.scalar(
        text("SELECT sql FROM sqlite_master WHERE name = 'approval_authorizations'")
    )
    assert isinstance(table_sql, str)
    assert "AUTONOMOUS" in table_sql
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(text("UPDATE approval_authorizations SET channel = 'NOT_A_CHANNEL'"))


def test_blocked_autonomous_revoke_reasons_write_an_audit_row(session: Session) -> None:
    ids = _seed_support(session)
    for reason in ("claim_blocked", "autonomous_precheck_blocked"):
        plan = _persist_plan(session, ids)
        service, authorization = _approve_autonomous(session, ids, plan)
        revoked = service.revoke_authorization(
            authorization.authorization_id,
            organization_id=plan.organization_id,
            user_id=plan.user_id,
            reason=reason,
        )
        assert revoked.state is AuthorizationState.REVOKED
        assert revoked.channel is AuthorizationChannel.AUTONOMOUS
        matches = [
            row
            for row in session.scalars(select(AuditLog)).all()
            if row.action is AuditEventType.APPROVAL_DECISION
            and (row.redacted_metadata or {}).get("reason") == reason
            and (row.redacted_metadata or {}).get("action") == "revoke"
            and (row.redacted_metadata or {}).get("channel") == "AUTONOMOUS"
        ]
        assert len(matches) == 1
        assert matches[0].resource_id == str(authorization.authorization_id)


def test_reject_and_skip_never_authorize(session: Session) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    service = ApprovalService(
        session,
        AuditService(session),
        clock=lambda: NOW + timedelta(minutes=1),
    )
    approval = service.create_for_plan_revision(
        revision_id=plan.revision_id,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
    )
    rejected = service.decide(
        approval.id,
        ApprovalDecisionRequest(action=ApprovalAction.REJECT),
        principal_organization_id=plan.organization_id,
        principal_user_id=plan.user_id,
    )
    assert rejected.authorization is None
    with pytest.raises(ValidationAppError, match="Only APPROVE"):
        service.issue_authorization(
            approval_id=approval.id,
            decision=AuthorizationDecision.SKIP,
            organization_id=plan.organization_id,
            user_id=plan.user_id,
            channel=AuthorizationChannel.API,
        )
    assert session.query(ApprovalAuthorizationModel).count() == 0


def test_expired_and_revoked_authorizations_are_unavailable(session: Session) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    issuing_service, authorization = _approve_plan(session, ids, plan)
    revoked = issuing_service.revoke_authorization(
        authorization.authorization_id,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
    )
    assert revoked.state is AuthorizationState.REVOKED
    assert revoked.authorization_content_hash == authorization.authorization_content_hash
    assert verify_authorization_issuance_hash(revoked)
    assert not issuing_service.is_authorization_available(
        authorization.authorization_id,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
        account_id=plan.account_id,
    )

    second_plan = _persist_plan(
        session,
        ids,
        _plan_request(ids, account_id=ids["account_two"].id),
    )
    _, expiring_authorization = _approve_plan(session, ids, second_plan)
    expired_service = ApprovalService(
        session,
        AuditService(session),
        clock=lambda: NOW + timedelta(minutes=20),
    )
    expired = expired_service.get_authorization(
        expiring_authorization.authorization_id,
        organization_id=second_plan.organization_id,
        user_id=second_plan.user_id,
    )
    assert expired.state is AuthorizationState.EXPIRED
    assert expired.authorization_content_hash == expiring_authorization.authorization_content_hash
    assert verify_authorization_issuance_hash(expired)
    assert not expired_service.is_authorization_available(
        expiring_authorization.authorization_id,
        organization_id=second_plan.organization_id,
        user_id=second_plan.user_id,
        account_id=second_plan.account_id,
    )


def test_future_consumed_state_does_not_change_issuance_hash(session: Session) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    _, authorization = _approve_plan(session, ids, plan)
    consumed = authorization.model_copy(
        update={
            "state": AuthorizationState.CONSUMED,
            "consumed_at": NOW + timedelta(minutes=2),
            "consumed_by_execution_command_id": uuid.uuid4(),
        }
    )
    assert consumed.authorization_content_hash == authorization.authorization_content_hash
    assert verify_authorization_issuance_hash(consumed)


def test_two_accounts_for_same_user_cannot_share_authorization(session: Session) -> None:
    ids = _seed_support(session)
    first_plan = _persist_plan(session, ids)
    _, first_auth = _approve_plan(session, ids, first_plan)

    second_proposal = ProposalService(session, AuditService(session)).create(
        TradeProposalCreate(
            organization_id=ids["organization"].id,
            user_id=ids["user"].id,
            strategy_id=StrategyId.HTF_TREND_PULLBACK,
            symbol="BTCUSDT",
            timeframe="4h",
            direction="long",
            entry_price=Decimal("100"),
            position_size=Decimal("2"),
            leverage=Decimal("2"),
            exit=ExitCriteria(
                invalidation="Exact stop.",
                stop_loss=Decimal("95"),
                take_profits=[TakeProfitLevel(price=Decimal("110"), size_fraction=1.0)],
            ),
            confidence=0.8,
            risk_level=RiskSeverity.MEDIUM,
            rationale="Second account.",
            approval_required=True,
            user_strategy_id=ids["strategy_version"].strategy_id,
        )
    )
    second_ids = {**ids, "proposal_id": second_proposal.id}
    second_plan = _persist_plan(
        session,
        second_ids,
        _plan_request(second_ids, account_id=ids["account_two"].id),
    )
    _, second_auth = _approve_plan(session, second_ids, second_plan)
    assert first_auth.account_id != second_auth.account_id
    assert first_auth.authorization_id != second_auth.authorization_id


def _rehash_plan(plan: TradePlanRevision, **updates: Any) -> TradePlanRevision:
    changed = plan.model_copy(update=updates)
    semantic = TradePlanRevisionSemantic.model_validate(
        {name: getattr(changed, name) for name in TradePlanRevisionSemantic.model_fields}
    )
    return changed.model_copy(update={"content_hash": canonical_sha256(semantic)})


def _bind_authorization(
    authorization: ApprovalAuthorization,
    plan: TradePlanRevision,
) -> ApprovalAuthorization:
    changed = authorization.model_copy(
        update={
            "organization_id": plan.organization_id,
            "user_id": plan.user_id,
            "account_id": plan.account_id,
            "exchange_account_id": plan.exchange_account_id,
            "operation": plan.operation,
            "plan_id": plan.plan_id,
            "revision_id": plan.revision_id,
            "plan_content_hash": plan.content_hash,
            "execution_venue": plan.execution_venue,
            "execution_instrument": plan.execution_instrument,
            "verified_account_mode": plan.expected_account_mode,
            "permission_attestation_id": plan.permission_attestation_id,
            "permission_attestation_version": plan.permission_attestation_version,
            "correlation_id": plan.correlation_id,
        }
    )
    content_values = {
        name: getattr(changed, name) for name in ApprovalAuthorizationIssuance.model_fields
    }
    content_values["created_at"] = _aware(content_values["created_at"])
    content_values["expires_at"] = _aware(content_values["expires_at"])
    content = ApprovalAuthorizationIssuance.model_validate(content_values)
    return changed.model_copy(update={"authorization_content_hash": canonical_sha256(content)})


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def test_canonical_payload_ignores_transport_metadata_and_round_trips(session: Session) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    _, authorization = _approve_plan(session, ids, plan)
    first = CanonicalExecutionPayloadSerializerV1.derive_from_plan(
        plan,
        authorization,
        at=NOW + timedelta(minutes=2),
    )
    request_id = "request-a"
    idempotency_key = "opaque-idempotency-a"
    second = CanonicalExecutionPayloadSerializerV1.derive_from_plan(
        plan.model_copy(
            update={
                "presentation_metadata": PlanPresentationMetadata(
                    channel=AuthorizationChannel.TELEGRAM,
                    display_title=request_id,
                )
            }
        ),
        authorization,
        at=NOW + timedelta(minutes=2),
    )
    assert request_id not in first.canonical_bytes.decode()
    assert idempotency_key not in first.canonical_bytes.decode()
    assert first.canonical_bytes == second.canonical_bytes
    assert first.sha256 == second.sha256
    assert CanonicalExecutionPayloadSerializerV1.deserialize(first.canonical_bytes) == first.payload


def test_correlation_lineage_is_bound_to_issuance_but_excluded_from_execution_identity(
    session: Session,
) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    _, authorization = _approve_plan(session, ids, plan)
    baseline = CanonicalExecutionPayloadSerializerV1.derive_from_plan(
        plan,
        authorization,
        at=NOW + timedelta(minutes=2),
    )

    changed_plan = plan.model_copy(update={"correlation_id": uuid.uuid4()})
    changed_authorization = _bind_authorization(authorization, changed_plan)
    changed = CanonicalExecutionPayloadSerializerV1.derive_from_plan(
        changed_plan,
        changed_authorization,
        at=NOW + timedelta(minutes=2),
    )

    assert changed_plan.content_hash == plan.content_hash
    assert changed.canonical_bytes == baseline.canonical_bytes
    assert changed.sha256 == baseline.sha256
    assert (
        changed_authorization.authorization_content_hash != authorization.authorization_content_hash
    )


@pytest.mark.parametrize(
    "mutation",
    ["quantity", "side", "account", "price_type_tif", "instrument"],
)
def test_canonical_payload_hash_changes_for_execution_semantics(
    session: Session,
    mutation: str,
) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    _, authorization = _approve_plan(session, ids, plan)
    baseline = CanonicalExecutionPayloadSerializerV1.derive_from_plan(
        plan,
        authorization,
        at=NOW + timedelta(minutes=2),
    )
    if mutation == "quantity":
        changed_plan = _rehash_plan(
            plan,
            quantity=SemanticAmount(value=Decimal("3"), unit="CONTRACTS"),
        )
    elif mutation == "side":
        changed_plan = _rehash_plan(plan, side=EntrySide.SELL)
    elif mutation == "account":
        changed_plan = _rehash_plan(plan, account_id=ids["account_two"].id)
    elif mutation == "price_type_tif":
        changed_plan = _rehash_plan(
            plan,
            order_type=EntryOrderType.LIMIT,
            time_in_force=TimeInForce.GTC,
            limit_price=SemanticAmount(value=Decimal("100.25"), unit="USDT"),
            market_marker=False,
        )
    else:
        changed_rules = plan.instrument_rules.model_copy(
            update={"rules_version": "blofin-rules-v2"}
        )
        changed_plan = _rehash_plan(
            plan,
            execution_instrument="ETH-USDT",
            instrument_rules=changed_rules,
        )
    changed_authorization = _bind_authorization(authorization, changed_plan)
    changed = CanonicalExecutionPayloadSerializerV1.derive_from_plan(
        changed_plan,
        changed_authorization,
        at=NOW + timedelta(minutes=2),
    )
    assert changed.canonical_bytes != baseline.canonical_bytes
    assert changed.sha256 != baseline.sha256


def test_canonical_payload_v1_golden_fixture_has_no_json_numbers_for_decimals() -> None:
    payload = CanonicalExecutionPayloadV1(
        organization_id=uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001"),
        principal=CanonicalExecutionPrincipalV1(
            user_id=uuid.UUID("aaaaaaaa-0000-0000-0000-000000000002"),
            account_id=uuid.UUID("aaaaaaaa-0000-0000-0000-000000000003"),
            exchange_account_id=None,
        ),
        plan_id=uuid.UUID("aaaaaaaa-0000-0000-0000-000000000004"),
        immutable_plan_revision_id=uuid.UUID("aaaaaaaa-0000-0000-0000-000000000005"),
        approval_authorization_id=uuid.UUID("aaaaaaaa-0000-0000-0000-000000000006"),
        plan_content_hash="1" * 64,
        execution_venue="BLOFIN_DEMO",
        execution_instrument="BTC-USDT",
        side=EntrySide.BUY,
        order_type=EntryOrderType.MARKET,
        time_in_force=TimeInForce.IOC,
        quantity=SemanticAmount(value=Decimal("2.000"), unit="CONTRACTS"),
        price=None,
        market_marker=True,
        account_binding=CanonicalAccountBindingV1(
            account_id=uuid.UUID("aaaaaaaa-0000-0000-0000-000000000003"),
            exchange_account_id=None,
            execution_mode=ExecutionMode.PAPER,
            expected_account_mode=AccountMode.NET,
            margin_mode=MarginMode.CROSS,
            position_mode=AccountMode.NET,
        ),
        instrument_rule_version="rules-v1",
        execution_policy_version="paper-entry-v1",
    )
    serialization = CanonicalExecutionPayloadSerializerV1.serialize(payload)
    fixture = (
        (Path(__file__).parent / "fixtures" / "canonical_execution_payload_v1.json")
        .read_text(encoding="utf-8")
        .strip()
        .encode("utf-8")
    )
    assert serialization.canonical_bytes == fixture
    decoded = json.loads(serialization.canonical_bytes)
    assert decoded["serializer_version"] == "CanonicalExecutionPayloadV1"
    assert decoded["quantity"]["value"] == {"scale": 0, "value": "2"}
    assert decoded["price"] is None
    assert decoded["market_marker"] is True
    assert b":2.0" not in serialization.canonical_bytes


def _approve_audits(session: Session, authorization_id: uuid.UUID) -> list[AuditLog]:
    return [
        row
        for row in session.scalars(select(AuditLog)).all()
        if row.action is AuditEventType.APPROVAL_DECISION
        and (row.redacted_metadata or {}).get("action") == "approve"
        and (row.redacted_metadata or {}).get("authorization_id") == str(authorization_id)
    ]


def test_revoked_and_expired_authorizations_can_be_reissued(session: Session) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    service, authorization = _approve_autonomous(session, ids, plan)
    approval_id = authorization.approval_request_id
    revoked = service.revoke_authorization(
        authorization.authorization_id,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
        reason="claim_blocked",
    )
    assert revoked.state is AuthorizationState.REVOKED
    assert service.get(approval_id).authorization is None
    reissued = service.decide(
        approval_id,
        ApprovalDecisionRequest(action=ApprovalAction.APPROVE),
        principal_organization_id=plan.organization_id,
        principal_user_id=plan.user_id,
        channel=AuthorizationChannel.AUTONOMOUS,
        actor_type="SYSTEM",
    )
    assert reissued.authorization is not None
    assert reissued.authorization.authorization_id != authorization.authorization_id
    assert reissued.authorization.state is AuthorizationState.AVAILABLE
    old = session.get(ApprovalAuthorizationModel, authorization.authorization_id)
    assert old is not None
    assert old.state is AuthorizationState.REVOKED
    assert len(_approve_audits(session, reissued.authorization.authorization_id)) == 1
    reused = service.decide(
        approval_id,
        ApprovalDecisionRequest(action=ApprovalAction.APPROVE),
        principal_organization_id=plan.organization_id,
        principal_user_id=plan.user_id,
        channel=AuthorizationChannel.AUTONOMOUS,
        actor_type="SYSTEM",
    )
    assert reused.authorization is not None
    assert reused.authorization.authorization_id == reissued.authorization.authorization_id
    assert session.query(ApprovalAuthorizationModel).count() == 2
    assert len(_approve_audits(session, reissued.authorization.authorization_id)) == 1

    expired_plan = _persist_plan(session, ids)
    expired_service, expired = _approve_autonomous(session, ids, expired_plan)
    expired_row = session.get(ApprovalAuthorizationModel, expired.authorization_id)
    assert expired_row is not None
    expired_row.state = AuthorizationState.EXPIRED
    session.flush()
    assert expired_service.get(expired.approval_request_id).authorization is None
    late = ApprovalService(
        session,
        AuditService(session),
        clock=lambda: NOW + timedelta(hours=2),
    )
    with pytest.raises(ValidationAppError, match="expired"):
        late.issue_authorization(
            approval_id=expired.approval_request_id,
            decision=AuthorizationDecision.APPROVE,
            organization_id=expired_plan.organization_id,
            user_id=expired_plan.user_id,
            channel=AuthorizationChannel.AUTONOMOUS,
            actor_type="SYSTEM",
        )
    assert session.get(ApprovalAuthorizationModel, expired.authorization_id).state is (  # type: ignore[union-attr]
        AuthorizationState.EXPIRED
    )
    within_window = expired_service.decide(
        expired.approval_request_id,
        ApprovalDecisionRequest(action=ApprovalAction.APPROVE),
        principal_organization_id=expired_plan.organization_id,
        principal_user_id=expired_plan.user_id,
        channel=AuthorizationChannel.AUTONOMOUS,
        actor_type="SYSTEM",
    )
    assert within_window.authorization is not None
    assert within_window.authorization.authorization_id != expired.authorization_id
    assert session.get(ApprovalAuthorizationModel, expired.authorization_id).state is (  # type: ignore[union-attr]
        AuthorizationState.EXPIRED
    )


def test_only_one_reusable_authorization_can_exist(session: Session) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    _service, authorization = _approve_autonomous(session, ids, plan)
    original = session.get(ApprovalAuthorizationModel, authorization.authorization_id)
    assert original is not None

    def clone(**overrides: object) -> ApprovalAuthorizationModel:
        values = {
            column.key: getattr(original, column.key) for column in original.__table__.columns
        }
        values.update(overrides)
        return ApprovalAuthorizationModel(**values)

    duplicate = clone(
        id=uuid.uuid4(),
        authorization_content_hash=uuid.uuid4().hex + uuid.uuid4().hex,
    )
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(duplicate)
        session.flush()
    original.state = AuthorizationState.REVOKED
    session.flush()
    replacement = clone(
        id=uuid.uuid4(),
        authorization_content_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        state=AuthorizationState.AVAILABLE,
    )
    session.add(replacement)
    session.flush()
    replacement.state = AuthorizationState.CONSUMED
    session.flush()
    third = clone(
        id=uuid.uuid4(),
        authorization_content_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        state=AuthorizationState.AVAILABLE,
    )
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(third)
        session.flush()
    reusable = session.scalars(
        select(ApprovalAuthorizationModel).where(
            ApprovalAuthorizationModel.state.in_(
                (AuthorizationState.AVAILABLE, AuthorizationState.CONSUMED)
            )
        )
    ).all()
    assert [row.id for row in reusable] == [replacement.id]


def test_competing_issue_reuses_the_available_authorization(session: Session) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    service, authorization = _approve_autonomous(session, ids, plan)
    service.revoke_authorization(
        authorization.authorization_id,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
        reason="post_approval_recheck",
    )
    winner = service.issue_authorization(
        approval_id=authorization.approval_request_id,
        decision=AuthorizationDecision.APPROVE,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
        channel=AuthorizationChannel.AUTONOMOUS,
        actor_type="SYSTEM",
    )
    lookups = {"for": 0, "by": 0}
    original_for = service._authorizations.get_reusable_for_approval
    original_by = service._authorizations.get_reusable_by_binding

    def miss_for(approval_id: uuid.UUID) -> object:
        lookups["for"] += 1
        if lookups["for"] == 1:
            return None
        return original_for(approval_id)

    def miss_by(**kwargs: object) -> object:
        lookups["by"] += 1
        if lookups["by"] == 1:
            return None
        return original_by(**kwargs)  # type: ignore[arg-type]

    service._authorizations.get_reusable_for_approval = miss_for  # type: ignore[method-assign]
    service._authorizations.get_reusable_by_binding = miss_by  # type: ignore[method-assign]
    raced = service.issue_authorization(
        approval_id=authorization.approval_request_id,
        decision=AuthorizationDecision.APPROVE,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
        channel=AuthorizationChannel.AUTONOMOUS,
        actor_type="SYSTEM",
    )
    assert raced.authorization_id == winner.authorization_id
    assert raced.authorization_id != authorization.authorization_id
    available = session.scalars(
        select(ApprovalAuthorizationModel).where(
            ApprovalAuthorizationModel.state == AuthorizationState.AVAILABLE
        )
    ).all()
    assert [row.id for row in available] == [winner.authorization_id]


def test_competing_workers_execute_the_reissued_authorization_once(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = ApprovalService(
        session, AuditService(session), clock=lambda: NOW + timedelta(minutes=1)
    )
    service.revoke_authorization(
        authorization.authorization_id,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
        reason="claim_blocked",
    )
    first = service.decide(
        authorization.approval_request_id,
        ApprovalDecisionRequest(action=ApprovalAction.APPROVE),
        principal_organization_id=plan.organization_id,
        principal_user_id=plan.user_id,
        channel=AuthorizationChannel.AUTONOMOUS,
        actor_type="SYSTEM",
    )
    second = service.decide(
        authorization.approval_request_id,
        ApprovalDecisionRequest(action=ApprovalAction.APPROVE),
        principal_organization_id=plan.organization_id,
        principal_user_id=plan.user_id,
        channel=AuthorizationChannel.AUTONOMOUS,
        actor_type="SYSTEM",
    )
    assert first.authorization is not None
    assert second.authorization is not None
    assert first.authorization.authorization_id == second.authorization.authorization_id
    assert first.authorization.authorization_id != authorization.authorization_id
    execution = execution_service(session)
    allowed = execution.execute_paper_plan(
        execute_request(ids, plan, first.authorization, key="worker-a"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    blocked = execution.execute_paper_plan(
        execute_request(ids, plan, second.authorization, key="worker-b"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    assert allowed.outcome is ExecutionCommandOutcome.ALLOW
    assert blocked.outcome is ExecutionCommandOutcome.BLOCKED
    assert blocked.blocked_reason_code in {
        "plan_entry_already_claimed",
        "authorization_unavailable",
    }
    assert session.scalar(select(func.count()).select_from(PlanEntryExecutionClaim)) == 1
    active = session.scalars(
        select(ApprovalAuthorizationModel).where(
            ApprovalAuthorizationModel.state == AuthorizationState.AVAILABLE
        )
    ).all()
    assert active == []
    consumed = session.get(ApprovalAuthorizationModel, first.authorization.authorization_id)
    assert consumed is not None
    assert consumed.state is AuthorizationState.CONSUMED


def test_human_execution_key_stays_on_the_candidate() -> None:
    candidate_id = uuid.uuid4()
    authorization_id = uuid.uuid4()
    target = SimpleNamespace(organization_id=uuid.uuid4(), user_id=uuid.uuid4())
    human = _execute_request(
        target=target,  # type: ignore[arg-type]
        account_id=uuid.uuid4(),
        revision_id=uuid.uuid4(),
        authorization_id=authorization_id,
        candidate_id=candidate_id,
    )
    autonomous = _execute_request(
        target=target,  # type: ignore[arg-type]
        account_id=human.account_id,
        revision_id=human.revision_id,
        authorization_id=authorization_id,
        candidate_id=candidate_id,
        autonomous=True,
    )
    assert human.idempotency_key == f"paper-exec:{candidate_id}"
    assert autonomous.idempotency_key == f"paper-exec:{candidate_id}:{authorization_id}"
    assert len(autonomous.idempotency_key) <= 128


def test_committed_block_then_reissue_allows_one_order(session: Session) -> None:
    """A committed BLOCKED command must not seal the revision after reissue.

    This drives ExecutionService and PaperPlanClaimService. The first claim is
    committed while the kill switch is on. After revoke, a cleared switch, and
    a new authorization, one ALLOW creates one submit effect. A retry and a
    second worker reuse that effect. Another key cannot create a second one.
    """

    ids, plan, authorization = prepared_authorized_plan(session, execution_venue="PAPER_INTERNAL")
    candidate_id = uuid.uuid4()
    target = SimpleNamespace(
        organization_id=plan.organization_id,
        user_id=plan.user_id,
    )
    execution = execution_service(session)
    kill = KillSwitchService(session, AuditService(session), execution._settings)
    kill.activate(
        organization_id=ids["organization"].id,
        actor_user_id=ids["user"].id,
        payload=KillSwitchMutationRequest(confirm=True, reason="halt entries"),
    )
    session.commit()
    blocked_request = _execute_request(
        target=target,  # type: ignore[arg-type]
        account_id=plan.account_id,
        revision_id=plan.revision_id,
        authorization_id=authorization.authorization_id,
        candidate_id=candidate_id,
        autonomous=True,
    )
    blocked = execution.execute_paper_plan(blocked_request, clock=lambda: EXECUTE_AT)
    session.commit()
    assert blocked.outcome is ExecutionCommandOutcome.BLOCKED
    assert blocked.blocked_reason_code == "safety_epoch_blocking"
    assert _effect_count(session) == 0
    assert _claim_count(session) == 0
    stored = session.scalar(
        select(ExecutionIdempotencyBinding).where(
            ExecutionIdempotencyBinding.opaque_key == blocked_request.idempotency_key
        )
    )
    assert stored is not None
    assert stored.outcome is ExecutionCommandOutcome.BLOCKED

    service = ApprovalService(
        session, AuditService(session), clock=lambda: NOW + timedelta(minutes=1)
    )
    service.revoke_authorization(
        authorization.authorization_id,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
        reason="claim_blocked",
    )
    status = kill.get_status(organization_id=ids["organization"].id)
    kill.deactivate(
        organization_id=ids["organization"].id,
        actor_user_id=ids["user"].id,
        payload=KillSwitchMutationRequest(
            confirm=True,
            reason="entries may resume",
            expected_version=status.version,
        ),
    )
    session.commit()
    reissued = service.decide(
        authorization.approval_request_id,
        ApprovalDecisionRequest(action=ApprovalAction.APPROVE),
        principal_organization_id=plan.organization_id,
        principal_user_id=plan.user_id,
        channel=AuthorizationChannel.AUTONOMOUS,
        actor_type="SYSTEM",
    )
    assert reissued.authorization is not None
    assert reissued.authorization.authorization_id != authorization.authorization_id
    session.commit()

    stale = blocked_request.model_copy(
        update={"authorization_id": reissued.authorization.authorization_id}
    )
    with pytest.raises(ConflictError) as conflict:
        execution.execute_paper_plan(stale, clock=lambda: EXECUTE_AT)
    assert conflict.value.details.get("reason") == "idempotency_payload_conflict"
    session.rollback()

    allow_request = _execute_request(
        target=target,  # type: ignore[arg-type]
        account_id=plan.account_id,
        revision_id=plan.revision_id,
        authorization_id=reissued.authorization.authorization_id,
        candidate_id=candidate_id,
        autonomous=True,
    )
    assert allow_request.idempotency_key != blocked_request.idempotency_key
    allowed = execution.execute_paper_plan(allow_request, clock=lambda: EXECUTE_AT)
    session.commit()
    assert allowed.outcome is ExecutionCommandOutcome.ALLOW
    assert allowed.replayed is False
    assert allowed.command_id is not None
    assert _effect_count(session) == 1
    assert _claim_count(session) == 1
    assert _allow_count(session) == 1

    retry = execution.execute_paper_plan(allow_request, clock=lambda: EXECUTE_AT)
    session.commit()
    worker = execution.execute_paper_plan(allow_request, clock=lambda: EXECUTE_AT)
    session.commit()
    assert retry.replayed is True
    assert worker.replayed is True
    assert retry.command_id == allowed.command_id
    assert worker.command_id == allowed.command_id
    assert _effect_count(session) == 1
    assert _claim_count(session) == 1
    assert _allow_count(session) == 1

    raced = execution.execute_paper_plan(
        execute_request(ids, plan, reissued.authorization, key="worker-other-key"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    human_request = _execute_request(
        target=target,  # type: ignore[arg-type]
        account_id=plan.account_id,
        revision_id=plan.revision_id,
        authorization_id=reissued.authorization.authorization_id,
        candidate_id=candidate_id,
    )
    human = execution.execute_paper_plan(human_request, clock=lambda: EXECUTE_AT)
    session.commit()
    assert raced.outcome is ExecutionCommandOutcome.BLOCKED
    assert human.outcome is ExecutionCommandOutcome.BLOCKED
    assert raced.blocked_reason_code in {
        "plan_entry_already_claimed",
        "authorization_unavailable",
    }
    assert human.blocked_reason_code in {
        "plan_entry_already_claimed",
        "authorization_unavailable",
    }
    assert human_request.idempotency_key == f"paper-exec:{candidate_id}"
    assert _effect_count(session) == 1
    assert _claim_count(session) == 1
    assert _allow_count(session) == 1
    session.expire_all()
    revoked = session.get(ApprovalAuthorizationModel, authorization.authorization_id)
    consumed = session.get(ApprovalAuthorizationModel, reissued.authorization.authorization_id)
    assert revoked is not None and revoked.state is AuthorizationState.REVOKED
    assert consumed is not None and consumed.state is AuthorizationState.CONSUMED
    available = session.scalars(
        select(ApprovalAuthorizationModel).where(
            ApprovalAuthorizationModel.state == AuthorizationState.AVAILABLE
        )
    ).all()
    assert available == []


def _effect_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(VenueSubmitEffect)) or 0)


def _claim_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(PlanEntryExecutionClaim)) or 0)


def _allow_count(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(ExecutionCommand)
            .where(ExecutionCommand.outcome == ExecutionCommandOutcome.ALLOW)
        )
        or 0
    )


def _paper_cycle(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    *,
    outcomes: list[SimpleNamespace],
) -> tuple[AutomatedPaperLoop, SimpleNamespace, dict[str, object]]:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    settings = Settings(_env_file=None, autonomous_internal_paper_enabled=True)
    calls: list[uuid.UUID] = []
    requests: list[ExecutePaperPlanRequest] = []

    class _Execution:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def execute_paper_plan(
            self, request: ExecutePaperPlanRequest, **_kwargs: object
        ) -> SimpleNamespace:
            authorization_id = request.authorization_id
            calls.append(authorization_id)
            requests.append(request)
            return outcomes[len(calls) - 1]

    monkeypatch.setattr("app.services.automated_paper_loop.ExecutionService", _Execution)
    monkeypatch.setattr(
        "app.services.automated_paper_loop._find_open_journal",
        lambda *_args, **_kwargs: SimpleNamespace(id=uuid.uuid4(), status=JournalTradeStatus.OPEN),
    )
    loop = AutomatedPaperLoop(
        None,  # type: ignore[arg-type]
        settings,
        SimpleNamespace(now=lambda: NOW + timedelta(minutes=1)),
    )
    loop._plan_service = lambda: SimpleNamespace(  # type: ignore[method-assign]
        get_scoped=lambda *_args, **_kwargs: SimpleNamespace(plan=SimpleNamespace())
    )
    target = SimpleNamespace(
        organization_id=plan.organization_id,
        user_id=plan.user_id,
        scan_scope="scope",
    )
    candidate = SimpleNamespace(candidate_id=uuid.uuid4())
    return (
        loop,
        target,
        {
            "candidate": candidate,
            "revision_id": plan.revision_id,
            "account_id": plan.account_id,
            "calls": calls,
            "requests": requests,
            "plan": plan,
        },
    )


def test_second_cycle_reissues_after_a_blocked_claim(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    command_id = uuid.uuid4()
    loop, target, context = _paper_cycle(
        session,
        monkeypatch,
        outcomes=[
            SimpleNamespace(
                outcome=ExecutionCommandOutcome.BLOCKED,
                blocked_reason_code="daily_loss_limit",
                command_id=None,
                replayed=False,
            ),
            SimpleNamespace(
                outcome=ExecutionCommandOutcome.ALLOW,
                blocked_reason_code=None,
                command_id=command_id,
                replayed=False,
            ),
        ],
    )
    candidate = context["candidate"]
    common = {
        "target": target,
        "candidate": candidate,
        "revision_id": context["revision_id"],
        "account_id": context["account_id"],
        "eligibility_id": uuid.uuid4(),
        "eligibility_state": "eligible",
    }
    blocked = loop._execute_existing(session, **common)  # type: ignore[arg-type]
    assert blocked.stage == "blocked"
    assert blocked.reason_code == "daily_loss_limit"
    revoked_rows = session.scalars(select(ApprovalAuthorizationModel)).all()
    assert len(revoked_rows) == 1
    assert revoked_rows[0].state is AuthorizationState.REVOKED
    filled = loop._execute_existing(session, **common)  # type: ignore[arg-type]
    assert filled.stage == "filled"
    assert filled.execution_command_id == command_id
    rows = session.scalars(select(ApprovalAuthorizationModel)).all()
    available = [row for row in rows if row.state is AuthorizationState.AVAILABLE]
    revoked = [row for row in rows if row.state is AuthorizationState.REVOKED]
    assert len(available) == 1
    assert len(revoked) == 1
    assert available[0].id != revoked[0].id
    calls = context["calls"]
    assert isinstance(calls, list)
    assert calls == [revoked[0].id, available[0].id]
    recorded = context["requests"]
    assert isinstance(recorded, list)
    assert recorded[0].idempotency_key == f"paper-exec:{candidate.candidate_id}:{revoked[0].id}"
    assert recorded[1].idempotency_key == f"paper-exec:{candidate.candidate_id}:{available[0].id}"
    assert recorded[0].idempotency_key != f"paper-exec:{candidate.candidate_id}"
    assert len(_approve_audits(session, available[0].id)) == 1


def test_kill_switch_still_blocks_a_new_authorization(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    _service, authorization = _approve_autonomous(session, ids, plan)
    settings = Settings(_env_file=None, autonomous_internal_paper_enabled=True)

    def _forbid(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a live kill switch must not execute")

    monkeypatch.setattr("app.services.automated_paper_loop.ExecutionService", _forbid)
    loop = AutomatedPaperLoop(
        None,  # type: ignore[arg-type]
        settings,
        SimpleNamespace(now=lambda: NOW + timedelta(minutes=1)),
    )
    loop._plan_service = lambda: SimpleNamespace(  # type: ignore[method-assign]
        get_scoped=lambda *_args, **_kwargs: SimpleNamespace(plan=SimpleNamespace())
    )
    loop._autonomous_approval_refusal = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: "kill_switch_active"
    )
    proof = loop._execute_existing(
        session,
        target=SimpleNamespace(  # type: ignore[arg-type]
            organization_id=plan.organization_id,
            user_id=plan.user_id,
            scan_scope="scope",
        ),
        candidate=SimpleNamespace(candidate_id=uuid.uuid4()),  # type: ignore[arg-type]
        revision_id=plan.revision_id,
        account_id=plan.account_id,
        eligibility_id=uuid.uuid4(),
        eligibility_state="eligible",
    )
    assert proof.stage == "blocked"
    assert proof.reason_code == "kill_switch_active"
    rows = session.scalars(select(ApprovalAuthorizationModel)).all()
    assert len(rows) == 1
    assert rows[0].id == authorization.authorization_id
    assert rows[0].state is AuthorizationState.REVOKED


def _revoke_reason_audits(session: Session, reason: str) -> list[AuditLog]:
    return [
        row
        for row in session.scalars(select(AuditLog)).all()
        if row.action is AuditEventType.APPROVAL_DECISION
        and (row.redacted_metadata or {}).get("action") == "revoke"
        and (row.redacted_metadata or {}).get("reason") == reason
    ]


def _decide(
    service: ApprovalService,
    plan: TradePlanRevision,
    approval_id: uuid.UUID,
    *,
    channel: AuthorizationChannel,
    actor_type: str = "USER",
) -> ApprovalAuthorization:
    decided = service.decide(
        approval_id,
        ApprovalDecisionRequest(action=ApprovalAction.APPROVE),
        principal_organization_id=plan.organization_id,
        principal_user_id=plan.user_id,
        channel=channel,
        actor_type=actor_type,
    )
    assert decided.authorization is not None
    return decided.authorization


def test_autonomous_issuance_replaces_a_leftover_human_authorization(session: Session) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    service, human = _approve_plan(session, ids, plan)
    assert human.channel is AuthorizationChannel.API
    assert human.actor_type == "USER"
    issued = _decide(
        service,
        plan,
        human.approval_request_id,
        channel=AuthorizationChannel.AUTONOMOUS,
        actor_type="SYSTEM",
    )
    assert issued.authorization_id != human.authorization_id
    assert issued.channel is AuthorizationChannel.AUTONOMOUS
    assert issued.actor_type == "SYSTEM"
    assert issued.state is AuthorizationState.AVAILABLE
    leftover = session.get(ApprovalAuthorizationModel, human.authorization_id)
    assert leftover is not None
    assert leftover.state is AuthorizationState.REVOKED
    assert leftover.consumed_at is None
    audits = _revoke_reason_audits(session, CHANNEL_MISMATCH_REASON)
    assert len(audits) == 1
    assert audits[0].resource_id == str(human.authorization_id)
    assert (audits[0].redacted_metadata or {}).get("channel") == "API"
    assert len(_approve_audits(session, issued.authorization_id)) == 1


@pytest.mark.parametrize("channel", [AuthorizationChannel.WEB, AuthorizationChannel.TELEGRAM])
def test_autonomous_issuance_replaces_web_and_telegram_authorizations(
    session: Session,
    channel: AuthorizationChannel,
) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    service, _human = _approve_plan(session, ids, plan)
    prior = _decide(service, plan, _human.approval_request_id, channel=channel)
    assert prior.channel is channel
    issued = _decide(
        service,
        plan,
        prior.approval_request_id,
        channel=AuthorizationChannel.AUTONOMOUS,
        actor_type="SYSTEM",
    )
    assert issued.channel is AuthorizationChannel.AUTONOMOUS
    assert issued.authorization_id != prior.authorization_id
    stored = session.get(ApprovalAuthorizationModel, prior.authorization_id)
    assert stored is not None and stored.state is AuthorizationState.REVOKED


def test_reuse_match_requires_channel_and_actor_type() -> None:
    from app.services.approval_service import stored_issuance_matches

    api_user = SimpleNamespace(channel=AuthorizationChannel.API, actor_type="USER")
    web_user = SimpleNamespace(channel=AuthorizationChannel.WEB, actor_type="USER")
    autonomous = SimpleNamespace(channel=AuthorizationChannel.AUTONOMOUS, actor_type="SYSTEM")
    assert stored_issuance_matches(api_user, channel=AuthorizationChannel.API, actor_type="USER")
    assert stored_issuance_matches(web_user, channel=AuthorizationChannel.WEB, actor_type="USER")
    assert stored_issuance_matches(
        autonomous,
        channel=AuthorizationChannel.AUTONOMOUS,
        actor_type="SYSTEM",
    )
    assert not stored_issuance_matches(
        api_user, channel=AuthorizationChannel.AUTONOMOUS, actor_type="SYSTEM"
    )
    assert not stored_issuance_matches(
        web_user, channel=AuthorizationChannel.AUTONOMOUS, actor_type="SYSTEM"
    )
    assert not stored_issuance_matches(
        autonomous, channel=AuthorizationChannel.API, actor_type="USER"
    )
    assert not stored_issuance_matches(
        autonomous, channel=AuthorizationChannel.WEB, actor_type="USER"
    )
    assert not stored_issuance_matches(
        api_user, channel=AuthorizationChannel.API, actor_type="SYSTEM"
    )
    assert not stored_issuance_matches(
        web_user, channel=AuthorizationChannel.WEB, actor_type="SYSTEM"
    )
    assert not stored_issuance_matches(
        autonomous, channel=AuthorizationChannel.AUTONOMOUS, actor_type="USER"
    )
    assert not stored_issuance_matches(
        SimpleNamespace(channel=AuthorizationChannel.API, actor_type=""),
        channel=AuthorizationChannel.API,
        actor_type="USER",
    )


@pytest.mark.parametrize(
    ("stored_channel", "stored_actor", "requested_channel", "requested_actor"),
    [
        (AuthorizationChannel.API, "USER", AuthorizationChannel.AUTONOMOUS, "SYSTEM"),
        (AuthorizationChannel.WEB, "USER", AuthorizationChannel.AUTONOMOUS, "SYSTEM"),
        (AuthorizationChannel.AUTONOMOUS, "SYSTEM", AuthorizationChannel.API, "USER"),
        (AuthorizationChannel.AUTONOMOUS, "SYSTEM", AuthorizationChannel.WEB, "USER"),
    ],
)
def test_available_authorization_is_not_served_across_channel_and_actor(
    session: Session,
    stored_channel: AuthorizationChannel,
    stored_actor: str,
    requested_channel: AuthorizationChannel,
    requested_actor: str,
) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    if stored_channel is AuthorizationChannel.AUTONOMOUS:
        service, stored = _approve_autonomous(session, ids, plan)
    else:
        service, stored = _approve_plan(session, ids, plan)
        if stored_channel is not AuthorizationChannel.API:
            stored = _decide(
                service,
                plan,
                stored.approval_request_id,
                channel=stored_channel,
                actor_type=stored_actor,
            )
    assert stored.channel is stored_channel
    assert stored.actor_type == stored_actor
    issued = _decide(
        service,
        plan,
        stored.approval_request_id,
        channel=requested_channel,
        actor_type=requested_actor,
    )
    assert issued.authorization_id != stored.authorization_id
    assert issued.channel is requested_channel
    assert issued.actor_type == requested_actor
    assert issued.state is AuthorizationState.AVAILABLE
    leftover = session.get(ApprovalAuthorizationModel, stored.authorization_id)
    assert leftover is not None
    assert leftover.state is AuthorizationState.REVOKED
    assert leftover.consumed_at is None
    assert leftover.channel is stored_channel
    assert leftover.actor_type == stored_actor
    reused = _decide(
        service,
        plan,
        stored.approval_request_id,
        channel=requested_channel,
        actor_type=requested_actor,
    )
    assert reused.authorization_id == issued.authorization_id
    assert reused.channel is requested_channel
    assert reused.actor_type == requested_actor


def test_human_issuance_does_not_consume_an_autonomous_authorization(session: Session) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    service, autonomous = _approve_autonomous(session, ids, plan)
    with pytest.raises(TradingPolicyError, match="autonomous"):
        assert_human_may_execute(autonomous)
    human = _decide(service, plan, autonomous.approval_request_id, channel=AuthorizationChannel.API)
    assert human.channel is AuthorizationChannel.API
    assert human.actor_type == "USER"
    assert human.authorization_id != autonomous.authorization_id
    leftover = session.get(ApprovalAuthorizationModel, autonomous.authorization_id)
    assert leftover is not None
    assert leftover.state is AuthorizationState.REVOKED
    assert leftover.consumed_at is None
    assert_human_may_execute(human)
    blocked = execution_service(session).execute_paper_plan(
        execute_request(ids, plan, autonomous, key="human-must-not-consume-autonomous"),
        clock=lambda: EXECUTE_AT,
    )
    assert blocked.outcome is not ExecutionCommandOutcome.ALLOW
    session.expire_all()
    leftover = session.get(ApprovalAuthorizationModel, autonomous.authorization_id)
    assert leftover is not None
    assert leftover.state is AuthorizationState.REVOKED
    assert leftover.consumed_by_execution_command_id is None


def test_cross_channel_human_authorization_is_replaced(session: Session) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    service, api = _approve_plan(session, ids, plan)
    web = _decide(service, plan, api.approval_request_id, channel=AuthorizationChannel.WEB)
    assert web.channel is AuthorizationChannel.WEB
    assert web.authorization_id != api.authorization_id
    stored = session.get(ApprovalAuthorizationModel, api.authorization_id)
    assert stored is not None and stored.state is AuthorizationState.REVOKED
    telegram = _decide(
        service, plan, api.approval_request_id, channel=AuthorizationChannel.TELEGRAM
    )
    assert telegram.channel is AuthorizationChannel.TELEGRAM
    assert telegram.authorization_id != web.authorization_id


def test_actor_or_channel_half_match_is_not_an_autonomous_reuse(session: Session) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    service, api = _approve_plan(session, ids, plan)
    row = session.get(ApprovalAuthorizationModel, api.authorization_id)
    assert row is not None
    row.actor_type = "SYSTEM"
    row.authorization_content_hash = canonical_sha256(
        authorization_issuance(ApprovalService._authorization_to_schema(row))
    )
    session.flush()
    issued = _decide(
        service,
        plan,
        api.approval_request_id,
        channel=AuthorizationChannel.AUTONOMOUS,
        actor_type="SYSTEM",
    )
    assert issued.channel is AuthorizationChannel.AUTONOMOUS
    assert issued.actor_type == "SYSTEM"
    assert issued.authorization_id != api.authorization_id
    service_human, autonomous = _approve_autonomous(session, ids, _persist_plan(session, ids))
    autonomous_row = session.get(ApprovalAuthorizationModel, autonomous.authorization_id)
    assert autonomous_row is not None
    autonomous_row.actor_type = "USER"
    autonomous_row.authorization_content_hash = canonical_sha256(
        authorization_issuance(ApprovalService._authorization_to_schema(autonomous_row))
    )
    session.flush()
    replaced = _decide(
        service_human,
        plan,
        autonomous.approval_request_id,
        channel=AuthorizationChannel.AUTONOMOUS,
        actor_type="SYSTEM",
    )
    assert replaced.actor_type == "SYSTEM"
    assert replaced.authorization_id != autonomous.authorization_id


def test_consumed_authorization_stays_the_single_allow(session: Session) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    service, human = _approve_plan(session, ids, plan)
    row = session.get(ApprovalAuthorizationModel, human.authorization_id)
    assert row is not None
    row.state = AuthorizationState.CONSUMED
    session.flush()
    reused = _decide(
        service,
        plan,
        human.approval_request_id,
        channel=AuthorizationChannel.AUTONOMOUS,
        actor_type="SYSTEM",
    )
    assert reused.authorization_id == human.authorization_id
    assert reused.state is AuthorizationState.CONSUMED
    available = session.scalars(
        select(ApprovalAuthorizationModel).where(
            ApprovalAuthorizationModel.state == AuthorizationState.AVAILABLE
        )
    ).all()
    assert available == []
    blocked = execution_service(session).execute_paper_plan(
        _execute_request(
            target=SimpleNamespace(  # type: ignore[arg-type]
                organization_id=plan.organization_id,
                user_id=plan.user_id,
            ),
            account_id=plan.account_id,
            revision_id=plan.revision_id,
            authorization_id=human.authorization_id,
            candidate_id=uuid.uuid4(),
            autonomous=True,
        ),
        clock=lambda: EXECUTE_AT,
    )
    assert blocked.outcome is ExecutionCommandOutcome.BLOCKED
    assert blocked.blocked_reason_code == "authorization_unavailable"
    assert _effect_count(session) == 0


def test_expired_mismatched_authorization_is_revoked_and_not_replaced(session: Session) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    _service, human = _approve_plan(session, ids, plan)
    late = ApprovalService(
        session,
        AuditService(session),
        clock=lambda: NOW + timedelta(hours=2),
    )
    with pytest.raises(ValidationAppError, match="expired"):
        late.issue_authorization(
            approval_id=human.approval_request_id,
            decision=AuthorizationDecision.APPROVE,
            organization_id=plan.organization_id,
            user_id=plan.user_id,
            channel=AuthorizationChannel.AUTONOMOUS,
            actor_type="SYSTEM",
        )
    session.expire_all()
    stored = session.get(ApprovalAuthorizationModel, human.authorization_id)
    assert stored is not None and stored.state is AuthorizationState.REVOKED
    assert session.query(ApprovalAuthorizationModel).count() == 1


def test_matching_available_authorization_expires_instead_of_being_reused(
    session: Session,
) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    _service, autonomous = _approve_autonomous(session, ids, plan)
    late = ApprovalService(
        session,
        AuditService(session),
        clock=lambda: NOW + timedelta(hours=2),
    )
    with pytest.raises(ValidationAppError, match="expired"):
        late.issue_authorization(
            approval_id=autonomous.approval_request_id,
            decision=AuthorizationDecision.APPROVE,
            organization_id=plan.organization_id,
            user_id=plan.user_id,
            channel=AuthorizationChannel.AUTONOMOUS,
            actor_type="SYSTEM",
        )
    session.expire_all()
    stored = session.get(ApprovalAuthorizationModel, autonomous.authorization_id)
    assert stored is not None and stored.state is AuthorizationState.EXPIRED
    assert session.query(ApprovalAuthorizationModel).count() == 1


def test_competing_issue_replaces_a_different_channel(session: Session) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    service, human = _approve_plan(session, ids, plan)
    service.revoke_authorization(
        human.authorization_id,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
        reason="claim_blocked",
    )
    winner = service.issue_authorization(
        approval_id=human.approval_request_id,
        decision=AuthorizationDecision.APPROVE,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
        channel=AuthorizationChannel.AUTONOMOUS,
        actor_type="SYSTEM",
    )
    lookups = {"for": 0, "by": 0}
    original_for = service._authorizations.get_reusable_for_approval
    original_by = service._authorizations.get_reusable_by_binding

    def miss_for(approval_id: uuid.UUID) -> object:
        lookups["for"] += 1
        if lookups["for"] == 1:
            return None
        return original_for(approval_id)

    def miss_by(**kwargs: object) -> object:
        lookups["by"] += 1
        if lookups["by"] == 1:
            return None
        return original_by(**kwargs)  # type: ignore[arg-type]

    service._authorizations.get_reusable_for_approval = miss_for  # type: ignore[method-assign]
    service._authorizations.get_reusable_by_binding = miss_by  # type: ignore[method-assign]
    raced = service.issue_authorization(
        approval_id=human.approval_request_id,
        decision=AuthorizationDecision.APPROVE,
        organization_id=plan.organization_id,
        user_id=plan.user_id,
        channel=AuthorizationChannel.API,
        actor_type="USER",
    )
    assert raced.authorization_id != winner.authorization_id
    assert raced.channel is AuthorizationChannel.API
    session.expire_all()
    autonomous = session.get(ApprovalAuthorizationModel, winner.authorization_id)
    human_row = session.get(ApprovalAuthorizationModel, raced.authorization_id)
    assert autonomous is not None and autonomous.state is AuthorizationState.REVOKED
    assert human_row is not None and human_row.state is AuthorizationState.AVAILABLE
    assert human_row.channel is AuthorizationChannel.API


def test_armed_paper_cycle_does_not_execute_or_journal_a_leftover_api_authorization(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids, _plan_request(ids, execution_venue="PAPER_INTERNAL"))
    _service, human = _approve_plan(session, ids, plan)
    command_id = uuid.uuid4()
    requests: list[ExecutePaperPlanRequest] = []

    class _Execution:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def execute_paper_plan(
            self, request: ExecutePaperPlanRequest, **_kwargs: object
        ) -> SimpleNamespace:
            requests.append(request)
            return SimpleNamespace(
                outcome=ExecutionCommandOutcome.ALLOW,
                blocked_reason_code=None,
                command_id=command_id,
                replayed=False,
            )

    monkeypatch.setattr("app.services.automated_paper_loop.ExecutionService", _Execution)
    monkeypatch.setattr(
        "app.services.automated_paper_loop._find_open_journal",
        lambda *_args, **_kwargs: SimpleNamespace(id=uuid.uuid4(), status=JournalTradeStatus.OPEN),
    )
    settings = Settings(_env_file=None, autonomous_internal_paper_enabled=True)
    loop = AutomatedPaperLoop(
        None,  # type: ignore[arg-type]
        settings,
        SimpleNamespace(now=lambda: NOW + timedelta(minutes=1)),
    )
    loop._plan_service = lambda: SimpleNamespace(  # type: ignore[method-assign]
        get_scoped=lambda *_args, **_kwargs: SimpleNamespace(plan=SimpleNamespace())
    )
    candidate_id = uuid.uuid4()
    proof = loop._execute_existing(
        session,
        target=SimpleNamespace(  # type: ignore[arg-type]
            organization_id=plan.organization_id,
            user_id=plan.user_id,
            scan_scope="scope",
        ),
        candidate=SimpleNamespace(candidate_id=candidate_id),  # type: ignore[arg-type]
        revision_id=plan.revision_id,
        account_id=plan.account_id,
        eligibility_id=uuid.uuid4(),
        eligibility_state="eligible",
    )
    assert proof.stage == "filled"
    assert len(requests) == 1
    executed_id = requests[0].authorization_id
    assert executed_id != human.authorization_id
    assert requests[0].idempotency_key == f"paper-exec:{candidate_id}:{executed_id}"
    executed = session.get(ApprovalAuthorizationModel, executed_id)
    assert executed is not None
    assert executed.channel is AuthorizationChannel.AUTONOMOUS
    assert executed.actor_type == "SYSTEM"
    leftover = session.get(ApprovalAuthorizationModel, human.authorization_id)
    assert leftover is not None and leftover.state is AuthorizationState.REVOKED
    payload: dict[str, object] = {}
    _attach_approval_channel(session, payload, executed_id)
    assert payload["approval_channel"] == "AUTONOMOUS"
    assert payload["approval_channel"] != "API"
    assert human_plan_approved(JournalLifecycleEventType.APPROVED_PLAN, None, payload) is False
    assert (
        decision_actor_for(
            JournalLifecycleEventType.APPROVED_PLAN,
            approval_channel=str(payload["approval_channel"]),
        )
        is DecisionActor.AUTONOMOUS_APPROVAL
    )
    for lifecycle in session.scalars(select(JournalLifecycleEvent)).all():
        assert (lifecycle.payload or {}).get("approval_channel") != "API"


def test_reissued_autonomous_claim_journals_autonomous_not_api(session: Session) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids, _plan_request(ids, execution_venue="PAPER_INTERNAL"))
    service, human = _approve_plan(session, ids, plan)
    issued = _decide(
        service,
        plan,
        human.approval_request_id,
        channel=AuthorizationChannel.AUTONOMOUS,
        actor_type="SYSTEM",
    )
    candidate_id = uuid.uuid4()
    target = SimpleNamespace(organization_id=plan.organization_id, user_id=plan.user_id)
    execution = execution_service(session)
    allowed = execution.execute_paper_plan(
        _execute_request(
            target=target,  # type: ignore[arg-type]
            account_id=plan.account_id,
            revision_id=plan.revision_id,
            authorization_id=issued.authorization_id,
            candidate_id=candidate_id,
            autonomous=True,
        ),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    assert allowed.outcome is ExecutionCommandOutcome.ALLOW
    command = session.get(ExecutionCommand, allowed.command_id)
    assert command is not None
    assert command.authorization_id == issued.authorization_id
    assert command.authorization_id != human.authorization_id
    payload: dict[str, object] = {}
    _attach_approval_channel(session, payload, command.authorization_id)
    assert payload["approval_channel"] == "AUTONOMOUS"
    blocked = execution.execute_paper_plan(
        execute_request(ids, plan, human, key="leftover-api-authorization"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    assert blocked.outcome is ExecutionCommandOutcome.BLOCKED
    assert _effect_count(session) == 1
    assert _allow_count(session) == 1
    for lifecycle in session.scalars(select(JournalLifecycleEvent)).all():
        assert (lifecycle.payload or {}).get("approval_channel") != "API"


def test_consumed_human_authorization_is_not_executed_by_an_armed_cycle(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    _service, human = _approve_plan(session, ids, plan)
    row = session.get(ApprovalAuthorizationModel, human.authorization_id)
    assert row is not None
    row.state = AuthorizationState.CONSUMED
    session.flush()

    def _forbid(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a consumed human authorization must not be executed")

    monkeypatch.setattr("app.services.automated_paper_loop.ExecutionService", _forbid)
    loop = AutomatedPaperLoop(
        None,  # type: ignore[arg-type]
        Settings(_env_file=None, autonomous_internal_paper_enabled=True),
        SimpleNamespace(now=lambda: NOW + timedelta(minutes=1)),
    )
    loop._plan_service = lambda: SimpleNamespace(  # type: ignore[method-assign]
        get_scoped=lambda *_args, **_kwargs: SimpleNamespace(plan=SimpleNamespace())
    )
    proof = loop._execute_existing(
        session,
        target=SimpleNamespace(  # type: ignore[arg-type]
            organization_id=plan.organization_id,
            user_id=plan.user_id,
            scan_scope="scope",
        ),
        candidate=SimpleNamespace(candidate_id=uuid.uuid4()),  # type: ignore[arg-type]
        revision_id=plan.revision_id,
        account_id=plan.account_id,
        eligibility_id=uuid.uuid4(),
        eligibility_state="eligible",
    )
    assert proof.stage == "blocked"
    assert proof.reason_code == CHANNEL_MISMATCH_REASON
    session.expire_all()
    stored = session.get(ApprovalAuthorizationModel, human.authorization_id)
    assert stored is not None and stored.state is AuthorizationState.CONSUMED
    assert _effect_count(session) == 0


def test_flag_off_after_an_autonomous_authorization_does_not_consume_it(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    service, autonomous = _approve_autonomous(session, ids, plan)
    human = _decide(service, plan, autonomous.approval_request_id, channel=AuthorizationChannel.API)
    assert human.channel is AuthorizationChannel.API
    requests: list[uuid.UUID] = []

    class _Execution:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def execute_paper_plan(
            self, request: ExecutePaperPlanRequest, **_kwargs: object
        ) -> SimpleNamespace:
            requests.append(request.authorization_id)
            return SimpleNamespace(
                outcome=ExecutionCommandOutcome.BLOCKED,
                blocked_reason_code="execution_blocked",
                command_id=None,
                replayed=False,
            )

    monkeypatch.setattr("app.services.governed_blofin_demo.ExecutionService", _Execution)
    monkeypatch.setattr(
        GovernedBloFinDemoLoop,
        "_demo_auto_approval_refusal",
        lambda *_args, **_kwargs: (None, None),
    )
    settings = SimpleNamespace(
        execution_mode="paper",
        exchange_mode="paper_exchange_demo",
        enable_real_trading=False,
        real_trading_enabled=False,
        autonomous_internal_paper_enabled=False,
        autonomous_blofin_demo_enabled=False,
        environment="staging",
        global_kill_switch_active=False,
    )
    demo = GovernedBloFinDemoLoop(None, settings, SimpleNamespace(now=lambda: NOW))  # type: ignore[arg-type]
    demo._plan_service = lambda: SimpleNamespace(  # type: ignore[method-assign]
        get_scoped=lambda *_args, **_kwargs: SimpleNamespace(
            plan=SimpleNamespace(
                execution_venue="BLOFIN_DEMO",
                execution_policy_version=DEMO_POLICY,
            )
        )
    )
    proof = demo._execute_existing(
        session,
        target=SimpleNamespace(  # type: ignore[arg-type]
            organization_id=plan.organization_id,
            user_id=plan.user_id,
        ),
        candidate=SimpleNamespace(candidate_id=uuid.uuid4()),  # type: ignore[arg-type]
        revision_id=plan.revision_id,
        account_id=plan.account_id,
    )
    assert proof.stage == "blocked"
    assert proof.reason_code == "execution_blocked"
    assert requests == [human.authorization_id]
    session.expire_all()
    stored = session.get(ApprovalAuthorizationModel, autonomous.authorization_id)
    assert stored is not None and stored.state is AuthorizationState.REVOKED
    assert stored.consumed_at is None


def test_armed_demo_cycle_replaces_a_leftover_api_authorization(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    _service, human = _approve_plan(session, ids, plan)
    requests: list[uuid.UUID] = []

    class _Execution:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def execute_paper_plan(
            self, request: ExecutePaperPlanRequest, **_kwargs: object
        ) -> SimpleNamespace:
            requests.append(request.authorization_id)
            return SimpleNamespace(
                outcome=ExecutionCommandOutcome.BLOCKED,
                blocked_reason_code="daily_loss_limit",
                command_id=None,
                replayed=False,
            )

    monkeypatch.setattr("app.services.governed_blofin_demo.ExecutionService", _Execution)
    monkeypatch.setattr(
        GovernedBloFinDemoLoop,
        "_demo_auto_approval_refusal",
        lambda *_args, **_kwargs: (None, None),
    )
    settings = SimpleNamespace(
        execution_mode="paper",
        exchange_mode="paper_exchange_demo",
        enable_real_trading=False,
        real_trading_enabled=False,
        autonomous_internal_paper_enabled=False,
        autonomous_blofin_demo_enabled=True,
        environment="staging",
        global_kill_switch_active=False,
    )
    demo = GovernedBloFinDemoLoop(
        None,  # type: ignore[arg-type]
        settings,  # type: ignore[arg-type]
        SimpleNamespace(now=lambda: NOW + timedelta(minutes=1)),
    )
    demo._plan_service = lambda: SimpleNamespace(  # type: ignore[method-assign]
        get_scoped=lambda *_args, **_kwargs: SimpleNamespace(
            plan=SimpleNamespace(
                execution_venue="BLOFIN_DEMO",
                execution_policy_version=DEMO_POLICY,
            )
        )
    )
    proof = demo._execute_existing(
        session,
        target=SimpleNamespace(  # type: ignore[arg-type]
            organization_id=plan.organization_id,
            user_id=plan.user_id,
        ),
        candidate=SimpleNamespace(candidate_id=uuid.uuid4()),  # type: ignore[arg-type]
        revision_id=plan.revision_id,
        account_id=plan.account_id,
    )
    assert proof.stage == "blocked"
    assert proof.reason_code == "daily_loss_limit"
    assert len(requests) == 1
    assert requests[0] != human.authorization_id
    executed = session.get(ApprovalAuthorizationModel, requests[0])
    assert executed is not None
    assert executed.channel is AuthorizationChannel.AUTONOMOUS
    assert executed.state is AuthorizationState.REVOKED
    leftover = session.get(ApprovalAuthorizationModel, human.authorization_id)
    assert leftover is not None and leftover.state is AuthorizationState.REVOKED
    assert leftover.consumed_at is None
    payload: dict[str, object] = {}
    _attach_approval_channel(session, payload, requests[0])
    assert payload["approval_channel"] == "AUTONOMOUS"


def test_kill_switch_leaves_a_leftover_api_authorization_unconsumed(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _seed_support(session)
    plan = _persist_plan(session, ids)
    _service, human = _approve_plan(session, ids, plan)

    def _forbid(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a live kill switch must not execute")

    monkeypatch.setattr("app.services.automated_paper_loop.ExecutionService", _forbid)
    loop = AutomatedPaperLoop(
        None,  # type: ignore[arg-type]
        Settings(_env_file=None, autonomous_internal_paper_enabled=True),
        SimpleNamespace(now=lambda: NOW + timedelta(minutes=1)),
    )
    loop._plan_service = lambda: SimpleNamespace(  # type: ignore[method-assign]
        get_scoped=lambda *_args, **_kwargs: SimpleNamespace(plan=SimpleNamespace())
    )
    loop._autonomous_approval_refusal = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: "kill_switch_active"
    )
    proof = loop._execute_existing(
        session,
        target=SimpleNamespace(  # type: ignore[arg-type]
            organization_id=plan.organization_id,
            user_id=plan.user_id,
            scan_scope="scope",
        ),
        candidate=SimpleNamespace(candidate_id=uuid.uuid4()),  # type: ignore[arg-type]
        revision_id=plan.revision_id,
        account_id=plan.account_id,
        eligibility_id=uuid.uuid4(),
        eligibility_state="eligible",
    )
    assert proof.stage == "blocked"
    assert proof.reason_code == "kill_switch_active"
    stored = session.get(ApprovalAuthorizationModel, human.authorization_id)
    assert stored is not None
    assert stored.state is AuthorizationState.AVAILABLE
    assert stored.channel is AuthorizationChannel.API
    assert stored.consumed_at is None
