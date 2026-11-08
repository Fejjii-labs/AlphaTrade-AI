"""Phase 3 — strategy immutability, AST compiler, Wilder ATR, migrations."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.analysis.wilder_atr_v1 import (
    FinalOhlcvBar,
    OhlcvFinality,
    WilderAtrStatus,
    compute_wilder_atr_v1,
)
from app.core.errors import NotFoundError, PersistencePolicyError
from app.core.operation_policy import operation_scope
from app.core.persistence_firewall import install_persistence_firewall
from app.db.base import Base
from app.db.models import (
    CompiledSetupDefinition,
    GlobalSetupTemplate,
    Membership,
    Organization,
    SetupDefinition,
    SetupMigrationRun,
    User,
    UserStrategy,
    UserStrategyVersion,
)
from app.db.strategy_immutability import (
    ImmutableHistoryError,
    StrategyVersionImmutabilityError,
    backfill_strategy_version_content_hashes,
    strategy_version_content_hash,
)
from app.schemas.agent import Intent, IntentDecision, OperationClass, PrincipalRef, RequestedAction
from app.schemas.common import (
    EntryTriggerType,
    ExitRuleType,
    ManualLevelType,
    MembershipRole,
    SetupCategory,
    SetupCompileStatus,
    StrategyChangeSource,
    StrategyId,
    Timeframe,
    TradeDirection,
)
from app.schemas.manual_levels import ManualChartLevelCreate, ManualChartLevelUpdate
from app.schemas.setup_ast import (
    ALLOWED_FIELD_SPECS,
    AstExpr,
    AstNodeKind,
    AstUnit,
    CompileResult,
    fld,
    lit_decimal,
)
from app.schemas.strategy_library import StrategyCard, UserStrategyCreate, UserStrategyUpdate
from app.schemas.strategy_pattern_spec import (
    FIRST_SLICE_NAME,
    canonical_first_slice_authored_spec,
)
from app.schemas.structured_rules import (
    EntryRuleBlock,
    ExitRuleBlock,
    StructuredRules,
    StructuredRulesPatch,
)
from app.services.canonical_serialization import canonical_sha256
from app.services.compiled_setup_service import CompiledSetupService
from app.services.manual_level_service import ManualLevelService
from app.services.setup_ast_compiler import (
    SetupAstCompileError,
    compile_from_authored,
    compile_from_spec,
    compile_pattern,
    first_slice_mapping_possible,
)
from app.services.setup_ast_compiler import (
    first_slice_bearish_sweep_pattern as compiler_first_slice,
)
from app.services.setup_migration_service import SetupMigrationService
from app.services.strategy_library_service import StrategyLibraryService
from app.services.strategy_versioning import StrategyVersioningService
from app.services.structured_rules_service import StructuredRulesService

ORG_A = uuid.UUID("00000000-0000-0000-0000-00000000c001")
ORG_B = uuid.UUID("00000000-0000-0000-0000-00000000c002")
USER_A = uuid.UUID("00000000-0000-0000-0000-00000000c011")
USER_B = uuid.UUID("00000000-0000-0000-0000-00000000c012")


def _engine() -> sessionmaker[Session]:
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
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session() -> Iterator[Session]:
    factory = _engine()
    with factory() as db:
        db.add_all(
            [
                Organization(id=ORG_A, name="Phase3 Org A"),
                Organization(id=ORG_B, name="Phase3 Org B"),
                User(
                    id=USER_A,
                    email="phase3-a@test.example",
                    hashed_password="not-a-real-hash",
                ),
                User(
                    id=USER_B,
                    email="phase3-b@test.example",
                    hashed_password="not-a-real-hash",
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                Membership(
                    organization_id=ORG_A,
                    user_id=USER_A,
                    role=MembershipRole.TRADER,
                ),
                Membership(
                    organization_id=ORG_B,
                    user_id=USER_B,
                    role=MembershipRole.TRADER,
                ),
            ]
        )
        db.commit()
        yield db


def _card(**overrides: object) -> StrategyCard:
    payload: dict[str, object] = {
        "strategy_name": FIRST_SLICE_NAME,
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
    payload.update(overrides)
    return StrategyCard.model_validate(payload)


def _first_slice_rules() -> StructuredRules:
    return StructuredRules(
        primary_timeframe=Timeframe.M15,
        entry_rules=[
            EntryRuleBlock(
                trigger_type=EntryTriggerType.LIQUIDITY_SWEEP,
                direction=TradeDirection.SHORT,
            )
        ],
        exit_rules=[
            ExitRuleBlock(rule_type=ExitRuleType.FIXED_STOP, value=Decimal("1")),
            ExitRuleBlock(rule_type=ExitRuleType.TP_MULTIPLE, r_multiple=Decimal("1")),
        ],
    )


def _attach_canonical_spec(session: Session, strategy_id: uuid.UUID) -> UserStrategyVersion:
    strategy = session.get(UserStrategy, strategy_id)
    assert strategy is not None
    versioning = StrategyVersioningService(session)
    parent = versioning.selected_version(strategy)
    assert parent is not None
    return versioning.fork_semantic_update(
        strategy,
        parent=parent,
        card=parent.card,
        structured_rules=parent.structured_rules,
        lesson_source_metadata=parent.lesson_source_metadata,
        actor_user_id=USER_A,
        source=StrategyChangeSource.PATTERN_SPEC,
        reason="attach canonical first-slice pattern spec",
        pattern_spec=canonical_first_slice_authored_spec().model_dump(mode="json"),
    )


def _create_strategy(
    session: Session, *, org: uuid.UUID = ORG_A, user: uuid.UUID = USER_A
) -> UserStrategy:
    service = StrategyLibraryService(session)
    return service.create(
        UserStrategyCreate(
            organization_id=org,
            user_id=user,
            name="Phase3 Sweep",
            setup_type=StrategyId.LIQUIDITY_SWEEP_REVERSAL,
            card=_card(),
        )
    )


def _readonly_decision(org: uuid.UUID, user: uuid.UUID) -> IntentDecision:
    return IntentDecision(
        intent=Intent.MARKET_ANALYSIS,
        operation_class=OperationClass.READ_ONLY,
        organization_id=org,
        principal=PrincipalRef(user_id=user),
        requested_action=RequestedAction.NONE,
    )


def test_semantic_update_creates_new_draft_and_preserves_old(session: Session) -> None:
    created = _create_strategy(session)
    versioning = StrategyVersioningService(session)
    parent = versioning.selected_version(session.get(UserStrategy, created.id))  # type: ignore[arg-type]
    assert parent is not None
    parent_id = parent.id
    parent_hash = parent.content_hash
    parent_card = dict(parent.card)

    rules = StructuredRulesService(session)
    rules.patch(
        created.id,
        StructuredRulesPatch(
            primary_timeframe=Timeframe.M15,
            entry_rules=_first_slice_rules().entry_rules,
            exit_rules=_first_slice_rules().exit_rules,
        ),
        organization_id=ORG_A,
        user_id=USER_A,
    )
    session.flush()

    old = session.get(UserStrategyVersion, parent_id)
    assert old is not None
    assert old.content_hash == parent_hash
    assert old.card == parent_card
    assert old.structured_rules is None

    strategy = session.get(UserStrategy, created.id)
    assert strategy is not None
    assert strategy.current_version == 2
    selected = versioning.selected_version(strategy)
    assert selected is not None
    assert selected.id != parent_id
    assert selected.parent_version_id == parent_id
    assert selected.structured_rules is not None


def test_in_place_semantic_mutation_rejected(session: Session) -> None:
    created = _create_strategy(session)
    version = StrategyVersioningService(session).selected_version(
        session.get(UserStrategy, created.id)  # type: ignore[arg-type]
    )
    assert version is not None
    version.structured_rules = {"entry_rules": []}
    with pytest.raises(StrategyVersionImmutabilityError):
        session.flush()


def test_alias_mismatch_rejected(session: Session) -> None:
    created = _create_strategy(session)
    version = StrategyVersioningService(session).selected_version(
        session.get(UserStrategy, created.id)  # type: ignore[arg-type]
    )
    assert version is not None
    with pytest.raises(SetupAstCompileError):
        StrategyVersioningService(session).assert_tenant_version(
            version, organization_id=ORG_A, alias="some-other-name"
        )


def test_one_canonical_persisted_identity(session: Session) -> None:
    created = _create_strategy(session)
    version = StrategyVersioningService(session).selected_version(
        session.get(UserStrategy, created.id)  # type: ignore[arg-type]
    )
    assert version is not None
    compiler = CompiledSetupService(session)
    rules = StructuredRulesService(session)
    rules.patch(
        created.id,
        StructuredRulesPatch(
            primary_timeframe=Timeframe.M15,
            entry_rules=_first_slice_rules().entry_rules,
            exit_rules=_first_slice_rules().exit_rules,
        ),
        organization_id=ORG_A,
        user_id=USER_A,
    )
    session.flush()
    selected = _attach_canonical_spec(session, created.id)
    first = compiler.compile_version(selected.id, organization_id=ORG_A, user_id=USER_A)
    second = compiler.compile_version(selected.id, organization_id=ORG_A, user_id=USER_A)
    assert first.status is SetupCompileStatus.EXECUTABLE
    assert first.compiled is not None
    assert second.compiled is not None
    assert first.compiled.id == second.compiled.id
    rows = list(session.scalars(select(CompiledSetupDefinition)).all())
    assert len(rows) == 1
    assert rows[0].strategy_version_id == selected.id


def test_global_template_remains_global(session: Session) -> None:
    setup = SetupDefinition(
        name="Legacy Sweep",
        strategy_id=StrategyId.LIQUIDITY_SWEEP_REVERSAL,
        category=SetupCategory.REVERSAL,
        version=1,
    )
    session.add(setup)
    session.flush()
    report = SetupMigrationService(session).run(dry_run=False, organization_id=ORG_A)
    assert report.templates_created == 1
    template = session.scalar(select(GlobalSetupTemplate))
    assert template is not None
    assert template.is_global is True
    assert template.organization_id is None
    assert template.setup_definition_id == setup.id


def test_tenant_compiled_definition_is_tenant_scoped(session: Session) -> None:
    created = _create_strategy(session)
    StructuredRulesService(session).patch(
        created.id,
        StructuredRulesPatch(
            primary_timeframe=Timeframe.M15,
            entry_rules=_first_slice_rules().entry_rules,
            exit_rules=_first_slice_rules().exit_rules,
        ),
        organization_id=ORG_A,
        user_id=USER_A,
    )
    session.flush()
    selected = _attach_canonical_spec(session, created.id)
    outcome = CompiledSetupService(session).compile_version(
        selected.id, organization_id=ORG_A, user_id=USER_A
    )
    assert outcome.compiled is not None
    assert outcome.compiled.organization_id == ORG_A
    with pytest.raises(NotFoundError, match=r"Strategy version not found\."):
        CompiledSetupService(session).compile_version(
            selected.id, organization_id=ORG_B, user_id=USER_B
        )


def test_ast_golden_serialization_and_hash_stability() -> None:
    first = compile_pattern(compiler_first_slice())
    second = compile_pattern(compiler_first_slice())
    assert first.document is not None
    assert second.document is not None
    assert first.document.content_hash == second.document.content_hash
    assert first.document.content_hash == canonical_sha256(
        first.document.pattern.model_dump(mode="python")
    )
    dumped = first.document.pattern.model_dump(mode="json")
    assert dumped["symbols"] == ["BTCUSDT"]
    assert dumped["trigger_timeframe"] == "15m"
    assert dumped["context_timeframe"] == "4h"


def test_unsupported_field_rejected() -> None:
    pattern = compiler_first_slice()
    pattern = pattern.model_copy(
        update={"trigger": AstExpr(kind=AstNodeKind.FIELD, path="foo.unknown", unit=AstUnit.PRICE)}
    )
    result = compile_pattern(pattern)
    assert result.status == SetupCompileStatus.NON_EXECUTABLE.value
    assert any(item.code == "unsupported_field" for item in result.failures)


def test_unsupported_operator_rejected() -> None:
    pattern = compiler_first_slice()
    pattern = pattern.model_copy(
        update={
            "trigger": AstExpr(
                kind=AstNodeKind.COMPARE,
                op="approx",
                args=[fld("ohlcv.close"), lit_decimal("1", AstUnit.PRICE)],
            )
        }
    )
    result = compile_pattern(pattern)
    assert any(item.code == "unsupported_operator" for item in result.failures)


def test_missing_feature_rejected() -> None:
    pattern = compiler_first_slice().model_copy(update={"features": []})
    result = compile_pattern(pattern)
    assert any(item.code == "missing_feature" for item in result.failures)


def test_ambiguous_legacy_mapping_fail_closed() -> None:
    card = _card()
    rules = StructuredRules(
        primary_timeframe=Timeframe.H4,
        entry_rules=[EntryRuleBlock(trigger_type=EntryTriggerType.EMA_PULLBACK)],
        exit_rules=[ExitRuleBlock(rule_type=ExitRuleType.FIXED_STOP, value=Decimal("1"))],
    )
    result = compile_from_authored(card=card, rules=rules)
    assert result.status == SetupCompileStatus.NON_EXECUTABLE.value
    assert any(item.code == "missing_pattern_spec" for item in result.failures)
    assert first_slice_mapping_possible(card, rules) is False


def _atr_bar(
    index: int,
    *,
    high: str,
    low: str,
    close: str,
    finality: OhlcvFinality = OhlcvFinality.FINAL,
    revision: uuid.UUID | None = None,
    instrument: str = "BTCUSDT",
    timeframe: str = "15m",
    minutes: int = 15,
    open_time: datetime | None = None,
    close_time: datetime | None = None,
) -> FinalOhlcvBar:
    start = open_time or datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=minutes * index)
    end = close_time or (start + timedelta(minutes=minutes))
    payload = {
        "instrument": instrument,
        "timeframe": timeframe,
        "open_time": start,
        "high": Decimal(high),
        "low": Decimal(low),
        "close": Decimal(close),
        "index": index,
    }
    return FinalOhlcvBar(
        revision_id=revision or uuid.uuid4(),
        content_hash=canonical_sha256(payload),
        venue="binance",
        market="usdm_perp",
        instrument=instrument,
        timeframe=timeframe,
        open_time=start,
        close_time=end,
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("10"),
        finality=finality,
    )


def test_wilder_atr_warmup_and_value() -> None:
    bars = [_atr_bar(i, high="2", low="0", close="1") for i in range(14)]
    feature = compute_wilder_atr_v1(bars)
    assert feature.status is WilderAtrStatus.VALUE
    assert feature.value == Decimal("2").quantize(Decimal("1E-16"))
    incomplete = compute_wilder_atr_v1(bars[:13])
    assert incomplete.status is WilderAtrStatus.MISSING
    assert incomplete.missing_reason == "warmup_incomplete"


def test_wilder_atr_final_candle_and_missing() -> None:
    bars = [_atr_bar(i, high="2", low="0", close="1") for i in range(13)]
    bars.append(_atr_bar(13, high="2", low="0", close="1", finality=OhlcvFinality.FORMING))
    feature = compute_wilder_atr_v1(bars)
    assert feature.status is WilderAtrStatus.MISSING
    assert feature.missing_reason == "forming_candle"


def test_wilder_atr_correction_determinism() -> None:
    bars = [_atr_bar(i, high="2", low="0", close="1") for i in range(14)]
    first = compute_wilder_atr_v1(bars)
    second = compute_wilder_atr_v1(bars)
    assert first.content_hash == second.content_hash
    corrected = list(bars)
    corrected[-1] = _atr_bar(13, high="4", low="0", close="2")
    changed = compute_wilder_atr_v1(corrected)
    assert changed.content_hash != first.content_hash
    assert changed.value != first.value


def test_manual_level_revision_immutability(session: Session) -> None:
    service = ManualLevelService(session)
    created = service.create(
        ManualChartLevelCreate(
            organization_id=ORG_A,
            user_id=USER_A,
            symbol="BTCUSDT",
            exchange="binance",
            timeframe=Timeframe.H4,
            level_type=ManualLevelType.RESISTANCE,
            price=Decimal("68000"),
        )
    )
    first = service.current_revision(created.id, organization_id=ORG_A)
    assert first is not None
    first_id = first.id
    first_hash = first.content_hash
    first_value = first.value
    service.update(
        created.id,
        ManualChartLevelUpdate(price=Decimal("69000")),
        organization_id=ORG_A,
        user_id=USER_A,
    )
    session.flush()
    historical = service.get_revision(first_id, organization_id=ORG_A)
    assert historical.content_hash == first_hash
    assert historical.value == first_value
    current = service.current_revision(created.id, organization_id=ORG_A)
    assert current is not None
    assert current.id != first_id
    assert current.supersedes_revision_id == first_id
    assert current.value == Decimal("69000")
    with pytest.raises(ImmutableHistoryError):
        first.value = Decimal("1")
        session.flush()
    session.rollback()


def test_manual_level_cross_tenant_rejected(session: Session) -> None:
    service = ManualLevelService(session)
    created = service.create(
        ManualChartLevelCreate(
            organization_id=ORG_A,
            user_id=USER_A,
            symbol="BTCUSDT",
            exchange="binance",
            timeframe=Timeframe.H4,
            level_type=ManualLevelType.RESISTANCE,
            price=Decimal("68000"),
        )
    )
    revision = service.current_revision(created.id, organization_id=ORG_A)
    assert revision is not None
    from app.services.manual_level_service import CrossTenantLevelError

    with pytest.raises(CrossTenantLevelError):
        service.get_revision(revision.id, organization_id=ORG_B)


def test_migration_dry_run_apply_idempotency_and_rollback(session: Session) -> None:
    session.add(
        SetupDefinition(
            name="Legacy Global",
            strategy_id=StrategyId.LIQUIDITY_SWEEP_REVERSAL,
            category=SetupCategory.REVERSAL,
            version=1,
        )
    )
    created = _create_strategy(session)
    StructuredRulesService(session).patch(
        created.id,
        StructuredRulesPatch(
            primary_timeframe=Timeframe.M15,
            entry_rules=_first_slice_rules().entry_rules,
            exit_rules=_first_slice_rules().exit_rules,
        ),
        organization_id=ORG_A,
        user_id=USER_A,
    )
    session.flush()
    _attach_canonical_spec(session, created.id)
    migrator = SetupMigrationService(session)
    dry = migrator.run(dry_run=True, organization_id=ORG_A)
    assert dry.templates_created == 1
    assert session.scalar(select(GlobalSetupTemplate)) is None
    applied = migrator.run(dry_run=False, organization_id=ORG_A)
    assert applied.templates_created == 1
    assert applied.compiled_created == 1
    template = session.scalar(select(GlobalSetupTemplate))
    assert template is not None
    assert template.organization_id is None
    again = migrator.run(dry_run=False, organization_id=ORG_A)
    assert again.templates_skipped >= 1
    assert again.compiled_skipped >= 1
    assert again.compiled_created == 0
    retained = migrator.rollback_run(applied.run_id)  # type: ignore[arg-type]
    assert retained == 0
    remaining = list(session.scalars(select(CompiledSetupDefinition)).all())
    assert len(remaining) == 1
    assert session.scalar(select(GlobalSetupTemplate)) is not None
    rollback_rows = list(
        session.scalars(select(SetupMigrationRun).where(SetupMigrationRun.mode == "rollback")).all()
    )
    assert len(rollback_rows) == 1


def test_readonly_cannot_write_compiled_setup(session: Session) -> None:
    created = _create_strategy(session)
    version = StrategyVersioningService(session).selected_version(
        session.get(UserStrategy, created.id)  # type: ignore[arg-type]
    )
    assert version is not None
    with (
        operation_scope(_readonly_decision(ORG_A, USER_A)),
        pytest.raises(PersistencePolicyError),
    ):
        session.add(
            CompiledSetupDefinition(
                organization_id=ORG_A,
                user_id=USER_A,
                strategy_id=created.id,
                strategy_version_id=version.id,
                compiler_version="setup-ast/v1",
                grammar_version="setup-ast-grammar/v1",
                compiled_ast={"pattern": {}},
                content_hash="a" * 64,
            )
        )
        session.flush()


def test_card_update_does_not_rewrite_parent(session: Session) -> None:
    created = _create_strategy(session)
    parent = StrategyVersioningService(session).selected_version(
        session.get(UserStrategy, created.id)  # type: ignore[arg-type]
    )
    assert parent is not None
    parent_id = parent.id
    StrategyLibraryService(session).update(
        created.id,
        UserStrategyUpdate(card=_card(strategy_name="Renamed Sweep")),
        organization_id=ORG_A,
        user_id=USER_A,
    )
    session.flush()
    old = session.get(UserStrategyVersion, parent_id)
    assert old is not None
    assert old.card["strategy_name"] == FIRST_SLICE_NAME


def test_allowlist_contains_first_slice_fields() -> None:
    required = {
        "ohlcv.open",
        "ohlcv.high",
        "ohlcv.low",
        "ohlcv.close",
        "ohlcv.volume",
        "feature.wilder_atr_v1.value",
        "swing.S",
        "manual_level.R",
        "cvd.at_bar_close",
        "flow.signed_quote_delta_T",
        "flow.total_quote_volume_T",
        "evidence.tick_size",
    }
    assert required.issubset(ALLOWED_FIELD_SPECS)


def test_generic_short_liquidity_sweep_does_not_compile() -> None:
    result = compile_from_authored(card=_card(), rules=_first_slice_rules())
    assert result.status == SetupCompileStatus.NON_EXECUTABLE.value
    assert result.document is None
    assert any(item.code == "missing_pattern_spec" for item in result.failures)


def test_symbol_and_timeframes_alone_do_not_compile() -> None:
    card = _card(asset_universe=["BTCUSDT"], timeframes=["15m", "4h"])
    result = compile_from_authored(card=card, rules=_first_slice_rules())
    assert result.document is None
    assert any(item.code == "missing_pattern_spec" for item in result.failures)


def test_missing_cvd_requirement_does_not_compile() -> None:
    spec = canonical_first_slice_authored_spec().model_copy(
        update={"requires_cvd_divergence": False}
    )
    result = compile_from_spec(spec)
    assert result.document is None
    assert any(item.code == "missing_cvd_requirement" for item in result.failures)


def test_missing_volume_threshold_does_not_compile() -> None:
    payload = canonical_first_slice_authored_spec().model_dump(mode="python")
    del payload["volume_ratio_threshold"]
    result = compile_from_authored(card=_card(), rules=_first_slice_rules(), pattern_spec=payload)
    assert result.document is None
    assert any(item.code == "missing_volume_threshold" for item in result.failures)


def test_missing_resistance_rule_does_not_compile() -> None:
    spec = canonical_first_slice_authored_spec().model_copy(
        update={"requires_manual_4h_resistance": False}
    )
    result = compile_from_spec(spec)
    assert result.document is None
    assert any(item.code == "missing_resistance_rule" for item in result.failures)


def test_missing_sequence_semantics_does_not_compile() -> None:
    payload = canonical_first_slice_authored_spec().model_dump(mode="python")
    payload["sequence"] = []
    result = compile_from_authored(card=_card(), rules=_first_slice_rules(), pattern_spec=payload)
    assert result.document is None
    assert any(item.code == "missing_sequence_semantics" for item in result.failures)


def test_exact_typed_first_slice_spec_compiles_from_authored_values() -> None:
    spec = canonical_first_slice_authored_spec()
    result = compile_from_spec(spec)
    assert result.status == SetupCompileStatus.EXECUTABLE.value
    assert result.document is not None
    dumped = result.document.pattern.model_dump(mode="json")
    assert dumped["symbols"] == ["BTCUSDT"]
    assert dumped["trigger_timeframe"] == "15m"
    assert dumped["context_timeframe"] == "4h"
    text = str(dumped)
    assert "1.50" in text or "1.5" in text
    assert "0.50" in text or "0.5" in text
    assert "-0.10" in text or "-0.1" in text
    assert "exhaustion" not in FIRST_SLICE_NAME.lower()
    assert "CVD Divergence" in FIRST_SLICE_NAME
    assert "Aggressive Sell Imbalance" in FIRST_SLICE_NAME


def test_changing_authored_threshold_changes_ast_hash() -> None:
    base = compile_from_spec(canonical_first_slice_authored_spec())
    changed = compile_from_spec(
        canonical_first_slice_authored_spec().model_copy(
            update={"volume_ratio_threshold": Decimal("9.99")}
        )
    )
    assert base.document is not None
    assert changed.document is not None
    assert base.document.content_hash != changed.document.content_hash
    assert "9.99" in str(changed.document.pattern.model_dump(mode="json"))
    assert "1.50" not in str(changed.document.pattern.model_dump(mode="json"))


def test_compiler_does_not_restore_canonical_default_thresholds() -> None:
    spec = canonical_first_slice_authored_spec().model_copy(
        update={
            "resistance_distance_atr_threshold": Decimal("0.77"),
            "sweep_threshold_atr": Decimal("0.33"),
        }
    )
    result = compile_from_spec(spec)
    assert result.document is not None
    dumped = str(result.document.pattern.model_dump(mode="json"))
    assert "0.77" in dumped
    assert "0.33" in dumped
    assert "0.50" not in dumped
    assert "0.25" not in dumped


def _compile_identity_variant(**updates: object) -> CompileResult:
    spec = canonical_first_slice_authored_spec()
    nested: dict[str, object] = dict(updates)
    trigger_update = nested.get("trigger_atr")
    if isinstance(trigger_update, dict):
        nested["trigger_atr"] = spec.trigger_atr.model_copy(update=trigger_update)
    context_update = nested.get("context_atr")
    if isinstance(context_update, dict):
        nested["context_atr"] = spec.context_atr.model_copy(update=context_update)
    return compile_from_spec(spec.model_copy(update=nested))


def _assert_identity_code(code: str, **updates: object) -> None:
    result = _compile_identity_variant(**updates)
    assert result.document is None
    assert any(item.code == code for item in result.failures)


def test_wrong_symbol_does_not_compile() -> None:
    _assert_identity_code("wrong_symbol", symbol="ETHUSDT")


def test_wrong_trigger_timeframe_does_not_compile() -> None:
    _assert_identity_code("wrong_trigger_timeframe", trigger_timeframe="5m")


def test_wrong_context_timeframe_does_not_compile() -> None:
    _assert_identity_code("wrong_context_timeframe", context_timeframe="1h")


def test_wrong_direction_does_not_compile() -> None:
    _assert_identity_code("wrong_direction", direction=TradeDirection.LONG)


def test_wrong_atr_period_does_not_compile() -> None:
    _assert_identity_code("wrong_atr_period", trigger_atr={"period": 21})


def test_wrong_atr_timeframe_does_not_compile() -> None:
    _assert_identity_code("wrong_atr_timeframe", trigger_atr={"timeframe": "1h"})


def test_missing_required_sequence_step_does_not_compile() -> None:
    spec = canonical_first_slice_authored_spec()
    result = compile_from_spec(spec.model_copy(update={"sequence": [spec.sequence[0]]}))
    assert result.document is None
    assert any(item.code == "missing_required_sequence_step" for item in result.failures)


def test_duplicate_sequence_step_does_not_compile() -> None:
    spec = canonical_first_slice_authored_spec()
    result = compile_from_spec(
        spec.model_copy(update={"sequence": [spec.sequence[0], spec.sequence[0]]})
    )
    assert result.document is None
    assert any(item.code == "duplicate_sequence_step" for item in result.failures)


def test_wrong_sequence_order_does_not_compile() -> None:
    spec = canonical_first_slice_authored_spec()
    result = compile_from_spec(spec.model_copy(update={"sequence": list(reversed(spec.sequence))}))
    assert result.document is None
    assert any(item.code == "wrong_sequence_order" for item in result.failures)


def test_wilder_atr_exact_15m_continuity() -> None:
    bars = [_atr_bar(i, high="2", low="0", close="1") for i in range(14)]
    feature = compute_wilder_atr_v1(bars)
    assert feature.status is WilderAtrStatus.VALUE
    assert feature.missing_reason is None


def test_wilder_atr_one_missing_15m_candle() -> None:
    bars = [_atr_bar(i, high="2", low="0", close="1") for i in range(15)]
    del bars[7]
    feature = compute_wilder_atr_v1(bars)
    assert feature.status is WilderAtrStatus.MISSING
    assert feature.missing_reason == "positive_gap"


def test_wilder_atr_large_gap() -> None:
    bars = [_atr_bar(i, high="2", low="0", close="1") for i in range(13)]
    bars.append(_atr_bar(40, high="2", low="0", close="1"))
    feature = compute_wilder_atr_v1(bars)
    assert feature.status is WilderAtrStatus.MISSING
    assert feature.missing_reason == "positive_gap"


def test_wilder_atr_overlap() -> None:
    bars = [_atr_bar(i, high="2", low="0", close="1") for i in range(13)]
    previous = bars[-1]
    bars.append(
        _atr_bar(
            13,
            high="2",
            low="0",
            close="1",
            open_time=previous.open_time + timedelta(minutes=5),
            close_time=previous.close_time + timedelta(minutes=15),
        )
    )
    feature = compute_wilder_atr_v1(bars)
    assert feature.status is WilderAtrStatus.MISSING
    assert feature.missing_reason == "overlap"


def test_wilder_atr_duplicate_interval() -> None:
    bars = [_atr_bar(i, high="2", low="0", close="1") for i in range(13)]
    duplicate = _atr_bar(12, high="3", low="0", close="1")
    bars.append(duplicate)
    feature = compute_wilder_atr_v1(bars)
    assert feature.status is WilderAtrStatus.MISSING
    assert feature.missing_reason == "duplicate_interval"


def test_wilder_atr_unordered_sequence() -> None:
    bars = [_atr_bar(i, high="2", low="0", close="1") for i in range(13)]
    bars.append(
        _atr_bar(
            13,
            high="2",
            low="0",
            close="1",
            open_time=datetime(2025, 12, 31, tzinfo=UTC),
            close_time=datetime(2025, 12, 31, tzinfo=UTC) + timedelta(minutes=15),
        )
    )
    feature = compute_wilder_atr_v1(bars)
    assert feature.status is WilderAtrStatus.MISSING
    assert feature.missing_reason == "unordered_interval"


def test_wilder_atr_4h_continuity() -> None:
    bars = [
        _atr_bar(i, high="2", low="0", close="1", timeframe="4h", minutes=240) for i in range(14)
    ]
    feature = compute_wilder_atr_v1(bars)
    assert feature.status is WilderAtrStatus.VALUE
    assert feature.timeframe == "4h"


def test_wilder_atr_correction_replaces_revision_without_gap() -> None:
    bars = [_atr_bar(i, high="2", low="0", close="1") for i in range(14)]
    first = compute_wilder_atr_v1(bars)
    corrected = list(bars)
    last = bars[-1]
    corrected[-1] = _atr_bar(
        13,
        high="4",
        low="0",
        close="2",
        open_time=last.open_time,
        close_time=last.close_time,
    )
    changed = compute_wilder_atr_v1(corrected)
    assert first.status is WilderAtrStatus.VALUE
    assert changed.status is WilderAtrStatus.VALUE
    assert changed.missing_reason is None
    assert changed.content_hash != first.content_hash


def test_manual_level_revision_hash_binds_created_at_and_venue(session: Session) -> None:
    from app.services.manual_level_service import manual_level_revision_content_hash

    service = ManualLevelService(session)
    created = service.create(
        ManualChartLevelCreate(
            organization_id=ORG_A,
            user_id=USER_A,
            symbol="BTCUSDT",
            exchange="binance",
            timeframe=Timeframe.H4,
            level_type=ManualLevelType.RESISTANCE,
            price=Decimal("68000"),
        )
    )
    revision = service.current_revision(created.id, organization_id=ORG_A)
    assert revision is not None
    expected = manual_level_revision_content_hash(
        level_id=revision.level_id,
        revision_id=revision.id,
        revision_number=revision.revision_number,
        organization_id=revision.organization_id,
        actor_user_id=revision.actor_user_id,
        instrument=revision.instrument,
        exchange=revision.exchange,
        venue=revision.venue,
        market_type=revision.market_type,
        price_unit=revision.price_unit,
        timeframe=revision.timeframe,
        level_type=revision.level_type.value,
        value=revision.value,
        price_low=revision.price_low,
        price_high=revision.price_high,
        valid=revision.valid,
        effective_at=revision.effective_at,
        created_at=revision.created_at,
        supersedes_revision_id=revision.supersedes_revision_id,
    )
    assert revision.content_hash == expected
    assert revision.venue == "binance"
    assert revision.created_at is not None
    other = manual_level_revision_content_hash(
        level_id=revision.level_id,
        revision_id=revision.id,
        revision_number=revision.revision_number,
        organization_id=revision.organization_id,
        actor_user_id=revision.actor_user_id,
        instrument=revision.instrument,
        exchange=revision.exchange,
        venue=revision.venue,
        market_type=revision.market_type,
        price_unit=revision.price_unit,
        timeframe=revision.timeframe,
        level_type=revision.level_type.value,
        value=revision.value,
        price_low=revision.price_low,
        price_high=revision.price_high,
        valid=revision.valid,
        effective_at=revision.effective_at,
        created_at=revision.created_at + timedelta(seconds=1),
        supersedes_revision_id=revision.supersedes_revision_id,
    )
    assert other != revision.content_hash


def test_content_hash_backfill_is_deterministic(session: Session) -> None:
    _create_strategy(session)
    session.commit()
    version = session.scalars(select(UserStrategyVersion)).one()
    expected = strategy_version_content_hash(
        card=version.card,
        structured_rules=version.structured_rules,
        lesson_source_metadata=version.lesson_source_metadata,
        pattern_spec=version.pattern_spec,
    )
    assert version.content_hash == expected
    session.execute(
        text("UPDATE user_strategy_versions SET content_hash = :digest"),
        {"digest": "0" * 64},
    )
    session.commit()
    updated = backfill_strategy_version_content_hashes(session.connection())
    session.commit()
    session.expire_all()
    restored = session.scalars(select(UserStrategyVersion)).one()
    assert updated == 1
    assert restored.content_hash == expected
    again = backfill_strategy_version_content_hashes(session.connection())
    assert again == 0
