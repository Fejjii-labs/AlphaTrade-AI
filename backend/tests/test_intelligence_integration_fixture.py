"""User-reachable intelligence integration loop. Paper/replay only.

Conversation → preview → explicit confirm → draft → explicit compile →
explicit approval → persisted executable policy → canonical evidence →
deterministic SetupAssessment. Watcher and Telegram stay disabled.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.persistence_firewall import install_persistence_firewall
from app.db.base import Base
from app.db.models import Membership, Organization, User, UserStrategyVersion
from app.db.session import get_session
from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.evidence_pipeline.canonical import is_first_slice_read_projection
from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
from app.main import create_app
from app.market_contracts.adapters.replay import ReplayPerpetualSource
from app.market_monitor.backoff import BackoffPolicy
from app.market_monitor.monitor import PerpetualMarketMonitor
from app.schemas.common import MembershipRole, StrategyId, StrategyLifecycleState
from app.schemas.strategy_library import StrategyCard
from app.schemas.strategy_pattern_spec import canonical_first_slice_authored_spec
from app.schemas.structured_rules import StructureFromTextRequest
from app.security.passwords import hash_password
from app.services.canonical_strategy_evaluation import resolve_executable_strategy_policy
from app.services.structure_from_text_service import StructureFromTextService
from app.signal_fusion.errors import StrategyEvaluationPolicyError
from app.signal_fusion.strategy_evaluation_policy import evaluate_canonical_strategy
from app.watcher.contracts import EvaluationCommand, EvaluationMode, ScanRequest, ScanTrigger
from app.watcher.fusion_evaluation import (
    ExecutablePolicyAuthority,
    build_fusion_evaluation_service,
)
from app.watcher.hashing import evaluation_input_hash, policy_content_hash, scan_request_hash
from app.watcher.memory import FakeClock, InMemoryWatcherStore
from tests.support.first_slice_preview import (
    INCOMPLETE_FIRST_SLICE_TEXT,
    UNSUPPORTED_STRATEGY_TEXT,
    complete_first_slice_preview_text,
)

ORG = uuid.UUID("00000000-0000-0000-0000-000000000063")
USER = uuid.UUID("00000000-0000-0000-0000-000000000163")
ORG_B = uuid.UUID("00000000-0000-0000-0000-000000000263")
USER_B = uuid.UUID("00000000-0000-0000-0000-000000000363")
PASSWORD = "TestPassword123!"
EMAIL = "at063@test.example"
EMAIL_B = "at063-b@test.example"


def _card() -> StrategyCard:
    spec = canonical_first_slice_authored_spec()
    return StrategyCard.model_validate(
        {
            "strategy_name": spec.name,
            "market_type": "crypto_perp",
            "asset_universe": ["BTCUSDT"],
            "timeframes": ["15m", "4h"],
            "entry_conditions": ["Bearish liquidity sweep at 4h resistance"],
            "confirmation_conditions": ["CVD divergence"],
            "invalidation": ["Close back above sweep high"],
            "stop_loss": ["Above sweep high"],
            "take_profit_plan": ["TP1 at 1R"],
            "runner_plan": [],
            "position_sizing": ["1%"],
            "add_rules": [],
            "no_trade_rules": [],
            "backtest_rules": [],
            "success_criteria": [],
            "validation_status": "draft",
        }
    )


def _settings() -> Settings:
    return Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="intelligence-fixture-test-secret",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        provider_mode="mock",
        market_data_provider="mock",
        watcher_orchestration_enabled=False,
        perpetual_evidence_source="replay",
    )


@pytest.fixture
def client_env() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
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
    install_persistence_firewall()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = _settings()
    with factory() as session:
        session.add(Organization(id=ORG, name="AT063 Org"))
        session.add(Organization(id=ORG_B, name="AT063 Org B"))
        session.add(
            User(
                id=USER,
                email=EMAIL,
                hashed_password=hash_password(PASSWORD, settings),
                email_verified=True,
            )
        )
        session.add(
            User(
                id=USER_B,
                email=EMAIL_B,
                hashed_password=hash_password(PASSWORD, settings),
                email_verified=True,
            )
        )
        session.flush()
        session.add(Membership(organization_id=ORG, user_id=USER, role=MembershipRole.TRADER))
        session.add(Membership(organization_id=ORG_B, user_id=USER_B, role=MembershipRole.TRADER))
        session.commit()

    app = create_app(settings=settings)

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as client:
        yield client, factory
    app.dependency_overrides.clear()


def _auth(client: TestClient, email: str = EMAIL) -> None:
    login = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    token = login.json()["tokens"]["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})


def _confirm_payload(body: dict[str, object]) -> dict[str, object]:
    return {
        "confirm": "I confirm",
        "expected_content_hash": body["content_hash"],
        "expected_parent_version_id": body.get("parent_version_id"),
        "expected_target_strategy_id": body.get("target_strategy_id"),
        "expected_organization_id": body["organization_id"],
        "expected_user_id": body["user_id"],
        "expected_conversation_id": body["conversation_id"],
    }


def test_unsupported_and_incomplete_inputs_remain_blocked() -> None:
    service = StructureFromTextService()
    incomplete = service.draft_preview(StructureFromTextRequest(text=INCOMPLETE_FIRST_SLICE_TEXT))
    assert incomplete.pattern_spec_draft is None
    assert incomplete.pattern_spec_errors
    assert incomplete.is_preview is True
    unsupported = service.draft_preview(StructureFromTextRequest(text=UNSUPPORTED_STRATEGY_TEXT))
    assert unsupported.pattern_spec_draft is None
    assert unsupported.is_preview is True
    assert unsupported.persists_strategy is False


def test_fixture_discussion_preview_confirm_compile_approve_evidence(
    client_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = client_env
    _auth(client)
    created = client.post(
        "/strategies",
        json={
            "name": "AT063 Sweep",
            "setup_type": StrategyId.LIQUIDITY_SWEEP_REVERSAL.value,
            "card": _card().model_dump(mode="json"),
        },
    )
    assert created.status_code == 200, created.text
    strategy_id = created.json()["id"]

    conversation = client.post(
        "/conversations", json={"strategy_id": strategy_id, "title": "First-slice"}
    )
    assert conversation.status_code == 200
    conv_id = conversation.json()["id"]
    discussed = client.post(
        "/chat/message",
        json={
            "message": complete_first_slice_preview_text(),
            "conversation_id": conv_id,
            "strategy_id": strategy_id,
        },
    )
    assert discussed.status_code in {200, 422}

    preview = client.post(
        f"/conversations/{conv_id}/proposals",
        json={"text": complete_first_slice_preview_text(), "strategy_id": strategy_id},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["status"] == "draft"
    assert body["is_preview"] is True
    assert body["mutates_strategy_authority"] is False
    assert body["proposed_pattern_spec"] is not None
    proposal_id = body["id"]

    quoted = client.post(
        f"/conversations/{conv_id}/proposals/{proposal_id}/confirm",
        json={**_confirm_payload(body), "confirm": "> I confirm"},
    )
    assert quoted.status_code == 422

    confirmed = client.post(
        f"/conversations/{conv_id}/proposals/{proposal_id}/confirm",
        json=_confirm_payload(body),
    )
    assert confirmed.status_code == 200, confirmed.text
    confirmed_body = confirmed.json()
    assert confirmed_body["status"] == "confirmed"
    version_id = confirmed_body["resulting_version_id"]
    assert version_id

    with (
        factory() as session,
        pytest.raises(StrategyEvaluationPolicyError, match=r"Draft|approved"),
    ):
        resolve_executable_strategy_policy(
            session, organization_id=ORG, strategy_version_id=uuid.UUID(version_id)
        )

    compiled_too_soon = client.post(f"/strategies/{strategy_id}/versions/{version_id}/compile")
    assert compiled_too_soon.status_code == 200, compiled_too_soon.text
    compiled_body = compiled_too_soon.json()
    assert compiled_body["status"] == "executable"
    assert compiled_body["compiled"] is not None

    with factory() as session, pytest.raises(StrategyEvaluationPolicyError, match=r"approved"):
        resolve_executable_strategy_policy(
            session, organization_id=ORG, strategy_version_id=uuid.UUID(version_id)
        )

    approve = client.post(
        f"/strategies/{strategy_id}/versions/{version_id}/approve",
        json={"confirm": "I confirm"},
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["new_state"] == StrategyLifecycleState.APPROVED.value

    with factory() as session:
        executable = resolve_executable_strategy_policy(
            session, organization_id=ORG, strategy_version_id=uuid.UUID(version_id)
        )
        assert executable.lifecycle_state is StrategyLifecycleState.APPROVED
        assert not is_first_slice_read_projection(
            strategy_version_id=executable.strategy_version_id,
            setup_definition_id=executable.compiled_setup_definition_id,
        )
        assembled = FirstSliceEvidenceAssembler(ReplayPerpetualSource(), replay=True).assemble(
            organization_id=ORG,
            policy=executable.fusion_policy,
        )
        assembled_again = FirstSliceEvidenceAssembler(
            ReplayPerpetualSource(), replay=True
        ).assemble(
            organization_id=ORG,
            policy=executable.fusion_policy,
        )
        first = evaluate_canonical_strategy(
            executable_policy=executable,
            command=assembled.assessment_command,
            evidence=assembled.bundle,
            evaluated_at=assembled.evaluated_at,
        )
        second = evaluate_canonical_strategy(
            executable_policy=executable,
            command=assembled_again.assessment_command,
            evidence=assembled_again.bundle,
            evaluated_at=assembled_again.evaluated_at,
        )
        assert first.evidence_window_hash == assembled.evidence_window_hash
        assert first.evidence_window_hash == assembled_again.evidence_window_hash
        assert first.state is second.state
        assert first.evidence_window_hash == second.evidence_window_hash
        assert first.content_hash == second.content_hash
        versions = list(session.scalars(select(UserStrategyVersion)).all())
        assert [item.id for item in versions if str(item.id) == version_id]


@pytest.mark.parametrize("blocked_text", [INCOMPLETE_FIRST_SLICE_TEXT, UNSUPPORTED_STRATEGY_TEXT])
def test_incomplete_and_unsupported_cannot_become_executable_policy(
    client_env: tuple[TestClient, sessionmaker[Session]],
    blocked_text: str,
) -> None:
    client, factory = client_env
    _auth(client)
    created = client.post(
        "/strategies",
        json={
            "name": "Blocked strategy",
            "setup_type": StrategyId.HTF_TREND_PULLBACK.value,
            "card": _card().model_dump(mode="json"),
        },
    )
    strategy_id = created.json()["id"]
    conversation = client.post("/conversations", json={"strategy_id": strategy_id})
    conv_id = conversation.json()["id"]
    preview = client.post(
        f"/conversations/{conv_id}/proposals",
        json={"text": blocked_text, "strategy_id": strategy_id},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["is_preview"] is True
    assert body["mutates_strategy_authority"] is False
    assert body.get("proposed_pattern_spec") is None
    payload = {
        "confirm": "I confirm",
        "expected_content_hash": body.get("content_hash") or ("0" * 64),
        "expected_parent_version_id": body.get("parent_version_id"),
        "expected_target_strategy_id": body.get("target_strategy_id"),
        "expected_organization_id": body["organization_id"],
        "expected_user_id": body["user_id"],
        "expected_conversation_id": body["conversation_id"],
    }
    confirmed = client.post(
        f"/conversations/{conv_id}/proposals/{body['id']}/confirm",
        json=payload,
    )
    assert confirmed.status_code in {200, 409, 422}, confirmed.text
    version_id = None
    if confirmed.status_code == 200:
        version_id = confirmed.json().get("resulting_version_id")
    if version_id:
        compiled = client.post(f"/strategies/{strategy_id}/versions/{version_id}/compile")
        compiled_body = (
            compiled.json()
            if compiled.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        assert not (
            compiled.status_code == 200
            and compiled_body.get("status") == "executable"
            and compiled_body.get("compiled") is not None
        )
        approve = client.post(
            f"/strategies/{strategy_id}/versions/{version_id}/approve",
            json={"confirm": "I confirm"},
        )
        assert approve.status_code in {404, 409, 422}, approve.text
        with factory() as session, pytest.raises(StrategyEvaluationPolicyError):
            resolve_executable_strategy_policy(
                session, organization_id=ORG, strategy_version_id=uuid.UUID(version_id)
            )
    else:
        assert confirmed.status_code in {409, 422}, confirmed.text


def test_persisted_watcher_path_uses_canonical_resolver(
    client_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = client_env
    _auth(client)
    created = client.post(
        "/strategies",
        json={
            "name": "Watcher lineage",
            "setup_type": StrategyId.LIQUIDITY_SWEEP_REVERSAL.value,
            "card": _card().model_dump(mode="json"),
        },
    )
    strategy_id = created.json()["id"]
    conversation = client.post("/conversations", json={"strategy_id": strategy_id})
    conv_id = conversation.json()["id"]
    preview = client.post(
        f"/conversations/{conv_id}/proposals",
        json={"text": complete_first_slice_preview_text(), "strategy_id": strategy_id},
    )
    body = preview.json()
    confirmed = client.post(
        f"/conversations/{conv_id}/proposals/{body['id']}/confirm",
        json=_confirm_payload(body),
    )
    version_id = uuid.UUID(confirmed.json()["resulting_version_id"])
    compile_resp = client.post(f"/strategies/{strategy_id}/versions/{version_id}/compile")
    assert compile_resp.json()["status"] == "executable"
    approve = client.post(
        f"/strategies/{strategy_id}/versions/{version_id}/approve",
        json={"confirm": "I confirm"},
    )
    assert approve.status_code == 200

    settings = _settings()
    assert settings.watcher_orchestration_enabled is False
    clock = FakeClock()
    store = InMemoryWatcherStore()
    with factory() as session:
        executable = resolve_executable_strategy_policy(
            session, organization_id=ORG, strategy_version_id=version_id
        )
        identity_user = uuid.uuid4()
        from app.watcher.contracts import WatcherPolicyIdentity, WatcherPolicyVersion

        draft = WatcherPolicyVersion(
            identity=WatcherPolicyIdentity(
                policy_id=uuid.uuid4(),
                organization_id=ORG,
                user_id=identity_user,
                watchlist_item_id=uuid.uuid4(),
            ),
            version=1,
            timeframe="15m",
            strategy_version_id=version_id,
            fusion_policy_version="first-slice-fusion/v1",
            enabled=True,
            created_by=identity_user,
            created_at=clock.now(),
            content_hash="0" * 64,
        )
        policy = draft.model_copy(update={"content_hash": policy_content_hash(draft)})
        store.put_policy_version(policy)
        request = ScanRequest(
            organization_id=ORG,
            scan_scope="first-slice-btcusdt-15m",
            policy_id=policy.identity.policy_id,
            policy_version=policy.version,
            policy_content_hash=policy.content_hash,
            watchlist_item_ids=(policy.identity.watchlist_item_id,),
            timeframe="15m",
            idempotency_key="watcher-persisted-lineage",
        )
        command = EvaluationCommand(
            command_id=uuid.uuid4(),
            request=request,
            request_hash=scan_request_hash(request),
            evaluation_input_hash=evaluation_input_hash(request),
            mode=EvaluationMode.PREVIEW,
            trigger=ScanTrigger.MANUAL,
            correlation_id=uuid.uuid4(),
        )
        replay_monitor = PerpetualMarketMonitor(
            ReplayPerpetualSource(),
            replay=True,
            backoff=BackoffPolicy(initial_seconds=0.25, max_seconds=4.0),
            poll_seconds=0.01,
        )
        port = AssemblingWatcherScanEvidence(
            FirstSliceEvidenceAssembler(ReplayPerpetualSource(), replay=True),
            executable_resolver=lambda _command: None,
            session=session,
            watcher_store=store,
            monitor=replay_monitor,
        )
        snapshot = port.load(command)
        assert snapshot is not None
        assert snapshot.policy_authority is ExecutablePolicyAuthority.PERSISTED_APPROVED_COMPILED
        assert snapshot.executable_policy.strategy_version_id == version_id
        assert snapshot.executable_policy.compiled_setup_definition_id == (
            executable.compiled_setup_definition_id
        )
        injected = AssemblingWatcherScanEvidence(
            FirstSliceEvidenceAssembler(ReplayPerpetualSource(), replay=True),
            executable_resolver=lambda _command: executable,
        )
        injected_snapshot = injected.load(command)
        assert injected_snapshot is not None
        assert injected_snapshot.policy_authority is ExecutablePolicyAuthority.IN_MEMORY_TEST_HELPER
        service = build_fusion_evaluation_service(evidence=port)
        outcome = service.evaluate(command)
        assert outcome.reason_code != "missing_canonical_evidence"
        preview_persist = service.persist_confirmed_setup(command, outcome)
        assert preview_persist.candidate_ids == ()
        persist_command = command.model_copy(update={"mode": EvaluationMode.PERSIST_EVIDENCE})
        persist = service.persist_confirmed_setup(persist_command, outcome)
        if outcome.reason_code == "confirmed_setup":
            assert len(persist.candidate_ids) == 1
            assert persist.status.value == "succeeded"
        else:
            assert persist.candidate_ids == ()
        injected_service = build_fusion_evaluation_service(evidence=injected)
        injected_outcome = injected_service.evaluate(persist_command)
        injected_persist = injected_service.persist_confirmed_setup(
            persist_command, injected_outcome
        )
        assert injected_persist.candidate_ids == ()
        if injected_outcome.reason_code == "confirmed_setup":
            assert injected_persist.reason_code == "candidate_creation_failed"


def test_paper_bot_scan_without_approved_lineage_creates_no_trade(
    client_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _factory = client_env
    _auth(client)
    created = client.post(
        "/strategies",
        json={
            "name": "Paper lineage gate",
            "setup_type": StrategyId.HTF_TREND_PULLBACK.value,
            "card": _card().model_dump(mode="json"),
        },
    )
    strategy_id = created.json()["id"]
    client.patch(
        f"/strategies/{strategy_id}/structured-rules",
        json={
            "primary_timeframe": "15m",
            "entry_rules": [{"trigger_type": "ema_pullback"}],
            "exit_rules": [
                {"rule_type": "fixed_stop", "value": "2"},
                {"rule_type": "tp_multiple", "r_multiple": "1"},
            ],
            "no_trade_rules": [],
        },
    )
    client.post(
        f"/strategies/{strategy_id}/backtests",
        json={
            "assumptions": {
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "exchange": "mock",
                "initial_capital": "10000",
                "fees_bps": 10,
                "slippage_bps": 5,
                "risk_per_trade_pct": 1,
            }
        },
    )
    started = client.post(
        f"/strategies/{strategy_id}/paper-validation/start",
        json={"runtime_mode": "auto_paper"},
    )
    assert started.status_code == 200, started.text
    scan = client.post(f"/paper-validation/{started.json()['id']}/scan")
    assert scan.status_code == 200, scan.text
    assert scan.json()["trade_created"] is False
    signals = client.get(f"/paper-validation/{started.json()['id']}/signals")
    assert signals.json()["total"] >= 1
    assert signals.json()["items"][0]["status"] == "not_testable"
    trades = client.get(f"/paper-validation/{started.json()['id']}/trades")
    assert trades.json()["total"] == 0


def test_cross_tenant_compile_and_approve_fail_closed(
    client_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _factory = client_env
    _auth(client)
    created = client.post(
        "/strategies",
        json={
            "name": "Tenant A compiled",
            "setup_type": StrategyId.LIQUIDITY_SWEEP_REVERSAL.value,
            "card": _card().model_dump(mode="json"),
        },
    )
    strategy_id = created.json()["id"]
    conversation = client.post("/conversations", json={"strategy_id": strategy_id})
    conv_id = conversation.json()["id"]
    preview = client.post(
        f"/conversations/{conv_id}/proposals",
        json={"text": complete_first_slice_preview_text(), "strategy_id": strategy_id},
    )
    confirmed = client.post(
        f"/conversations/{conv_id}/proposals/{preview.json()['id']}/confirm",
        json=_confirm_payload(preview.json()),
    )
    version_id = confirmed.json()["resulting_version_id"]
    compile_ok = client.post(f"/strategies/{strategy_id}/versions/{version_id}/compile")
    assert compile_ok.status_code == 200, compile_ok.text

    _auth(client, EMAIL_B)
    foreign_strategy = client.post(
        "/strategies",
        json={
            "name": "Tenant B",
            "setup_type": StrategyId.LIQUIDITY_SWEEP_REVERSAL.value,
            "card": _card().model_dump(mode="json"),
        },
    )
    foreign_id = foreign_strategy.json()["id"]
    compile_foreign = client.post(f"/strategies/{foreign_id}/versions/{version_id}/compile")
    assert compile_foreign.status_code in {403, 404, 409, 422}, compile_foreign.text
    approve_foreign = client.post(
        f"/strategies/{foreign_id}/versions/{version_id}/approve",
        json={"confirm": "I confirm"},
    )
    assert approve_foreign.status_code in {403, 404, 409, 422}, approve_foreign.text
    compile_a = client.post(f"/strategies/{strategy_id}/versions/{version_id}/compile")
    assert compile_a.status_code in {403, 404, 409, 422}, compile_a.text


def test_cross_org_compile_and_approve_match_missing_version(
    client_env: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _factory = client_env
    _auth(client)
    created = client.post(
        "/strategies",
        json={
            "name": "Tenant A oracle",
            "setup_type": StrategyId.LIQUIDITY_SWEEP_REVERSAL.value,
            "card": _card().model_dump(mode="json"),
        },
    )
    strategy_id = created.json()["id"]
    conv_id = client.post("/conversations", json={"strategy_id": strategy_id}).json()["id"]
    preview = client.post(
        f"/conversations/{conv_id}/proposals",
        json={"text": complete_first_slice_preview_text(), "strategy_id": strategy_id},
    )
    confirmed = client.post(
        f"/conversations/{conv_id}/proposals/{preview.json()['id']}/confirm",
        json=_confirm_payload(preview.json()),
    )
    version_id = confirmed.json()["resulting_version_id"]

    _auth(client, EMAIL_B)
    missing_id = uuid.uuid4()
    strip = ("request_id", "trace_id")

    def _error(response: object) -> dict[str, object]:
        body = response.json()["error"]  # type: ignore[attr-defined]
        return {k: v for k, v in body.items() if k not in strip}

    for action, payload in (("compile", None), ("approve", {"confirm": "I confirm"})):
        cross_org = client.post(
            f"/strategies/{strategy_id}/versions/{version_id}/{action}", json=payload
        )
        missing = client.post(
            f"/strategies/{strategy_id}/versions/{missing_id}/{action}", json=payload
        )
        assert cross_org.status_code == missing.status_code == 404, cross_org.text
        assert _error(cross_org) == _error(missing)
        assert _error(cross_org)["message"] == "Strategy version not found."
