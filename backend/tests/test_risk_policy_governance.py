"""Governance, grading, adjusters, breakers, calibration, and the risk_percent rule."""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import jwt
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.analysis.types import Indicators, MarketStructure
from app.core.config import Settings
from app.db.base import Base
from app.db.models import AuditLog, Organization
from app.db.risk_policy import (
    RiskPolicyBreakerReset,
    RiskPolicyBreakerState,
    RiskPolicyProposal,
    RiskPolicyVersion,
    RiskPolicyVersionCancellation,
)
from app.schemas.common import AuditEventType, RiskAction, RiskRuleId, TradeDirection
from app.schemas.risk import RiskCheckRequest
from app.schemas.risk_policy import RiskPolicyApplyRequest
from app.services.audit_service import AuditService
from app.services.risk.engine import RiskEngine
from app.services.risk.rules import RiskEvaluationContext
from app.services.risk_policy.adjusters import AdjusterInputs, apply_adjusters
from app.services.risk_policy.breakers import (
    BreakerConfig,
    BreakerSnapshot,
    evaluate_breakers,
    reset_drawdown,
)
from app.services.risk_policy.calibration import (
    CalibrationSample,
    PromotionReport,
    promote,
    promotion_report,
)
from app.services.risk_policy.clock import loosening_effective_at
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.governance import (
    HumanApplyProof,
    PolicyActor,
    PolicyChannel,
    assert_can_apply,
    channel_from_access_payload,
)
from app.services.risk_policy.grader import GRADER_VERSION, GradeBook, apply_ai_grade, grade_setup
from app.services.risk_policy.ledger import caps_for_ledger
from app.services.risk_policy.persistence import RiskPolicyService
from app.services.risk_policy.profile import (
    GRADE_ORDER,
    Grade,
    PolicyKind,
    placeholder_profile,
    profile_with,
)
from app.services.risk_policy.proof import decode_apply_proof

NOW = datetime(2026, 10, 18, 15, 0, tzinfo=UTC)
ORG = uuid.UUID("00000000-0000-4000-8000-000000000107")


def _human() -> PolicyActor:
    return PolicyActor("sofien", PolicyChannel.HUMAN, True)


def _agent() -> PolicyActor:
    return PolicyActor("agent-1", PolicyChannel.AGENT, True)


def _proof(proposal: RiskPolicyProposal, now: datetime = NOW) -> HumanApplyProof:
    proposal_id = str(proposal.id)
    content_hash = str(proposal.content_hash)
    return HumanApplyProof(
        method="interactive_step_up",
        subject_user_id="sofien",
        proposal_id=proposal_id,
        content_hash=content_hash,
        expires_at=now + timedelta(minutes=10),
        session_id="interactive-session",
        authenticated_at=now - timedelta(minutes=1),
        step_up_at=now - timedelta(seconds=20),
    )


def _apply(
    service: RiskPolicyService,
    proposal: RiskPolicyProposal,
    actor: PolicyActor,
    now: datetime = NOW,
):
    return service.apply(
        organization_id=ORG,
        proposal_id=proposal.id,
        actor=actor,
        proof=_proof(proposal, now),
        owner_user_id="sofien",
        now=now,
    )


def _indicators(rsi: float | None, hist: float | None) -> Indicators:
    return Indicators(
        sma_fast=None,
        sma_slow=None,
        ema_fast=None,
        ema_slow=None,
        rsi=rsi,
        macd=None,
        macd_signal=None,
        macd_hist=hist,
        atr=1.0,
        vwap=None,
        volume=None,
        volume_avg=None,
        volume_ratio=None,
        volatility=None,
        funding_rate=None,
    )


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(Organization(id=ORG, name="Risk Policy Org"))
    session.commit()
    return session


def _service(session: Session) -> RiskPolicyService:
    return RiskPolicyService(session, AuditService(session))


def test_placeholders_are_one_percent_and_flags_stay_off() -> None:
    for kind in PolicyKind:
        profile = placeholder_profile(kind)
        for grade in (Grade.A_PLUS, Grade.A, Grade.B_PLUS, Grade.B):
            assert profile.risk_percent(grade) == Decimal("1")
        assert profile.risk_percent(Grade.C) == Decimal("0")
        assert profile.max_leverage == Decimal("1")
        assert profile.autonomous_internal_paper_enabled is False
        assert profile.autonomous_blofin_demo_enabled is False
    with pytest.raises(RiskPolicyRejected) as caught:
        profile_with(PolicyKind.AGGRESSIVE, max_leverage=Decimal("21"))
    assert caught.value.code == "leverage_exceeds_ceiling"
    with pytest.raises(RiskPolicyRejected) as risk:
        profile_with(
            PolicyKind.STANDARD,
            risk_percent_by_grade={grade: Decimal("5") for grade in GRADE_ORDER},
        )
    assert risk.value.code == "risk_per_trade_exceeds_ceiling"


def test_grader_uses_trend_and_momentum_and_the_first_grade_binds() -> None:
    up = MarketStructure(trend="uptrend", last_label="HH")
    best = grade_setup(direction="long", structure=up, indicators=_indicators(60, 1))
    assert best.grade is Grade.A_PLUS
    assert best.grader_version == GRADER_VERSION
    weaker = grade_setup(direction="long", structure=up, indicators=_indicators(40, -1))
    assert weaker.grade is Grade.B_PLUS
    book = GradeBook()
    assert book.bind("setup-1", best) is best
    assert book.bind("setup-1", weaker).grade is Grade.A_PLUS
    ungraded = grade_setup(direction=None, structure=up, indicators=_indicators(60, 1))
    assert ungraded.refusal == "ungraded"
    kept, text = apply_ai_grade(Grade.B, Grade.A_PLUS, "looks stronger")
    assert kept is Grade.B
    assert text == "looks stronger"


def test_adjusters_only_reduce_and_missing_inputs_refuse() -> None:
    reduced = apply_adjusters(Decimal("1"), AdjusterInputs("high", False, True))
    assert reduced.adjusted_risk_percent == Decimal("0.25")
    assert reduced.product < 1
    unchanged = apply_adjusters(Decimal("1"), AdjusterInputs("normal", True, True))
    assert unchanged.adjusted_risk_percent == Decimal("1")
    with pytest.raises(RiskPolicyRejected) as missing:
        apply_adjusters(Decimal("1"), AdjusterInputs(None, True, True))
    assert missing.value.code == "adjuster_input_missing"
    with pytest.raises(RiskPolicyRejected) as bad:
        apply_adjusters(Decimal("1"), AdjusterInputs("chaos", True, True))
    assert bad.value.code == "adjuster_input_invalid"


def test_breakers_pause_drawdown_and_human_reset() -> None:
    config = BreakerConfig(Decimal("1"), Decimal("10"), 4)
    daily = evaluate_breakers(
        config,
        BreakerSnapshot(
            equity=Decimal("100"),
            equity_peak=Decimal("100"),
            reference_equity=Decimal("100"),
            realized_pnl_today=Decimal("-2"),
            unrealized_pnl_today=Decimal("0"),
            consecutive_losses=0,
            drawdown_latched=False,
            now=NOW,
        ),
    )
    assert daily.daily_loss_active
    assert daily.paused_until == datetime(2026, 10, 19, tzinfo=UTC)
    streak = evaluate_breakers(
        config,
        BreakerSnapshot(
            equity=Decimal("100"),
            equity_peak=Decimal("100"),
            reference_equity=Decimal("100"),
            realized_pnl_today=Decimal("0"),
            unrealized_pnl_today=Decimal("0"),
            consecutive_losses=4,
            drawdown_latched=False,
            now=NOW,
        ),
    )
    assert streak.consecutive_loss_active
    assert streak.paused_until == datetime(2026, 10, 19, tzinfo=UTC)
    drawdown = evaluate_breakers(
        config,
        BreakerSnapshot(
            equity=Decimal("89"),
            equity_peak=Decimal("100"),
            reference_equity=Decimal("100"),
            realized_pnl_today=Decimal("0"),
            unrealized_pnl_today=Decimal("0"),
            consecutive_losses=0,
            drawdown_latched=False,
            now=NOW,
        ),
    )
    assert drawdown.trip_kill_switch
    assert drawdown.human_reset_required
    breached = BreakerSnapshot(
        equity=Decimal("89"),
        equity_peak=Decimal("100"),
        reference_equity=Decimal("100"),
        realized_pnl_today=Decimal("0"),
        unrealized_pnl_today=Decimal("0"),
        consecutive_losses=0,
        drawdown_latched=True,
        now=NOW,
    )
    with pytest.raises(RiskPolicyRejected) as agent:
        reset_drawdown(_agent(), config, breached)
    assert agent.value.code == "drawdown_reset_requires_human"
    with pytest.raises(RiskPolicyRejected) as still:
        reset_drawdown(_human(), config, breached)
    assert still.value.code == "drawdown_still_active"
    recovered = BreakerSnapshot(
        equity=Decimal("95"),
        equity_peak=Decimal("100"),
        reference_equity=Decimal("100"),
        realized_pnl_today=Decimal("0"),
        unrealized_pnl_today=Decimal("0"),
        consecutive_losses=0,
        drawdown_latched=True,
        now=NOW,
    )
    cleared = reset_drawdown(_human(), config, recovered)
    assert cleared.drawdown_latched is False


def test_agent_cannot_apply_loosening_waits_and_a_breaker_blocks_it() -> None:
    session = _session()
    service = _service(session)
    profile, _active, _scheduled = service.read(organization_id=ORG, kind=PolicyKind.DEMO, now=NOW)
    assert profile.max_leverage == Decimal("1")
    proposal = service.propose(
        organization_id=ORG,
        actor=_agent(),
        profile=placeholder_profile(PolicyKind.DEMO),
        reason="Ship the placeholder.",
        now=NOW,
    )
    with pytest.raises(RiskPolicyRejected) as refused:
        service.apply(
            organization_id=ORG,
            proposal_id=proposal.id,
            actor=_agent(),
            proof=_proof(proposal),
            owner_user_id="sofien",
            now=NOW,
        )
    assert refused.value.code == "apply_refuses_agent_token"
    assert session.scalar(select(RiskPolicyVersion.id)) is None
    refusal = session.scalar(
        select(AuditLog).where(AuditLog.action == AuditEventType.RISK_POLICY_APPLY_REFUSED)
    )
    assert refusal is not None
    assert refusal.result.value == "blocked"
    bot = service.propose(
        organization_id=ORG,
        actor=PolicyActor("bot-1", PolicyChannel.BOT, True),
        profile=placeholder_profile(PolicyKind.DEMO),
        reason="Bot proposal only.",
        now=NOW,
    )
    with pytest.raises(RiskPolicyRejected) as bot_refused:
        service.apply(
            organization_id=ORG,
            proposal_id=bot.id,
            actor=PolicyActor("bot-1", PolicyChannel.BOT, True),
            proof=None,
            owner_user_id="sofien",
            now=NOW,
        )
    assert bot_refused.value.code == "apply_refuses_bot_token"
    applied = _apply(service, proposal, _human())
    assert applied.change_class == "initial"
    assert applied.effective_at == NOW
    assert applied.actor_channel == "human"
    tighter = profile_with(PolicyKind.DEMO, daily_loss_limit_percent=Decimal("0.5"))
    tight_proposal = service.propose(
        organization_id=ORG, actor=_human(), profile=tighter, reason="Cut the daily loss.", now=NOW
    )
    tight = _apply(service, tight_proposal, _human())
    assert tight.change_class == "tightening"
    assert tight.effective_at == NOW
    looser = placeholder_profile(PolicyKind.DEMO)
    loose_proposal = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=looser,
        reason="Restore the daily loss.",
        now=NOW,
    )
    with pytest.raises(RiskPolicyRejected) as unconfirmed:
        service.apply(
            organization_id=ORG,
            proposal_id=loose_proposal.id,
            actor=_human(),
            proof=None,
            owner_user_id="sofien",
            now=NOW,
        )
    assert unconfirmed.value.code == "apply_proof_missing"
    account = uuid.uuid4()
    service.latch_drawdown(
        organization_id=ORG, account_id=account, equity_peak=Decimal("10000"), now=NOW
    )
    with pytest.raises(RiskPolicyRejected) as blocked:
        _apply(service, loose_proposal, _human())
    assert blocked.value.code == "loosening_blocked_by_breaker"
    session.query(RiskPolicyVersion).all()  # keep the import path honest
    state = session.scalars(select(RiskPolicyVersion)).all()
    latched = next(row for row in state if row.id)
    service.reset_drawdown_latch(
        organization_id=ORG,
        account_id=account,
        actor=_human(),
        owner_user_id="sofien",
        equity=Decimal("9500"),
        drawdown_breaker_percent=Decimal("10"),
        now=NOW,
    )
    scheduled = _apply(service, loose_proposal, _human())
    assert scheduled.change_class == "loosening"
    assert scheduled.effective_at == datetime(2026, 10, 19, 15, tzinfo=UTC)
    current, active, waiting = service.read(organization_id=ORG, kind=PolicyKind.DEMO, now=NOW)
    assert active is not None and active.id == tight.id
    assert current.daily_loss_limit_percent == Decimal("0.5")
    assert waiting is not None and waiting.id == scheduled.id
    later, active_later, _waiting = service.read(
        organization_id=ORG,
        kind=PolicyKind.DEMO,
        now=datetime(2026, 10, 19, 15, tzinfo=UTC),
    )
    assert active_later is not None and active_later.id == scheduled.id
    assert later.daily_loss_limit_percent == Decimal("1")
    scheduled.reason = "rewrite"
    with pytest.raises(ValueError, match="immutable"):
        session.flush()
    _ = latched


def test_profile_caps_are_independent_of_the_258_notional_fallback() -> None:
    caps = caps_for_ledger(placeholder_profile(PolicyKind.DEMO), Decimal("10000"))
    assert caps.symbol_cap_independent
    assert caps.max_symbol_notional < caps.max_notional
    assert caps.max_daily_loss < caps.max_notional


def test_risk_percent_rule_enforces_the_ceiling_and_the_ledger() -> None:
    engine = RiskEngine()

    def check(**kwargs: object):
        request = RiskCheckRequest(
            symbol="BTCUSDT",
            direction=TradeDirection.LONG,
            entry_price=Decimal("60000"),
            position_size=Decimal("0.005"),
            leverage=Decimal("1"),
            account_equity=Decimal("10000"),
            stop_loss=Decimal("59000"),
            volume_24h=Decimal("50000000"),
            risk_percent=kwargs.get("risk_percent"),  # type: ignore[arg-type]
        )
        context = RiskEvaluationContext(
            is_weekend=False,
            autonomous_sizing=True,
            profile_risk_percent_cap=kwargs.get("cap"),  # type: ignore[arg-type]
            ledger_max_symbol_notional=kwargs.get("symbol_cap"),  # type: ignore[arg-type]
            ledger_max_notional=kwargs.get("notional"),  # type: ignore[arg-type]
        )
        return engine.evaluate(request, context=context)

    assert check(risk_percent=Decimal("1")).action is RiskAction.ALLOW
    blocked = check(risk_percent=Decimal("3"))
    assert blocked.action is RiskAction.BLOCK
    assert any(item.rule_id is RiskRuleId.RISK_PERCENT for item in blocked.triggered_rules)
    profile_block = check(risk_percent=Decimal("1"), cap=Decimal("0.5"))
    assert profile_block.action is RiskAction.BLOCK
    equal = check(
        risk_percent=Decimal("1"),
        symbol_cap=Decimal("10000"),
        notional=Decimal("10000"),
    )
    assert equal.action is RiskAction.BLOCK
    independent = check(
        risk_percent=Decimal("1"),
        symbol_cap=Decimal("1000"),
        notional=Decimal("2500"),
    )
    assert independent.action is RiskAction.ALLOW
    partial = check(symbol_cap=Decimal("1000"))
    assert partial.action is RiskAction.BLOCK


def test_promotion_report_stays_manual_and_flags_demotion() -> None:
    def samples(grade: Grade, r_multiple: str) -> list[CalibrationSample]:
        return [
            CalibrationSample(
                grade=grade,
                grader_version=GRADER_VERSION,
                r_multiple=Decimal(r_multiple),
                in_sample=False,
                walk_forward_fold=1 if index < 50 else 2,
                slippage_r=Decimal("0.01"),
            )
            for index in range(100)
        ]

    good = (
        samples(Grade.A_PLUS, "0.40")
        + samples(Grade.A, "0.30")
        + samples(Grade.B_PLUS, "0.20")
        + samples(Grade.B, "0.10")
    )
    report = promotion_report(good)
    assert report.eligible
    assert report.ordered
    assert report.walk_forward_ok
    assert report.promotion_applied is False
    assert report.auto_demotion == ()
    with pytest.raises(RiskPolicyRejected) as manual:
        promote(report)
    assert manual.value.code == "promotion_is_manual"
    short = promotion_report(good[:50])
    assert short.eligible is False
    assert short.auto_demotion == ()
    broken = samples(Grade.A_PLUS, "-0.20") + good[100:]
    demoted = promotion_report(broken)
    assert demoted.eligible is False
    assert Grade.A_PLUS in demoted.auto_demotion


def test_exchange_margin_module_imports_no_blofin_client() -> None:
    path = Path(__file__).resolve().parents[1] / "src/app/services/risk_policy/exchange_margin.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert all("blofin" not in name for name in imported)


def _grades(a_plus: str = "1") -> dict[Grade, Decimal]:
    grades = {grade: Decimal("1") for grade in GRADE_ORDER}
    grades[Grade.A_PLUS] = Decimal(a_plus)
    grades[Grade.C] = Decimal("0")
    return grades


def _eligible_report() -> PromotionReport:
    def samples(grade: Grade, r_multiple: str) -> list[CalibrationSample]:
        return [
            CalibrationSample(
                grade=grade,
                grader_version=GRADER_VERSION,
                r_multiple=Decimal(r_multiple),
                in_sample=False,
                walk_forward_fold=1 if index < 50 else 2,
                slippage_r=Decimal("0.01"),
            )
            for index in range(100)
        ]

    return promotion_report(
        samples(Grade.A_PLUS, "0.40")
        + samples(Grade.A, "0.30")
        + samples(Grade.B_PLUS, "0.20")
        + samples(Grade.B, "0.10")
    )


def test_human_paper_and_backtest_risk_percent_stay_idle() -> None:
    """Settings allow 10% and replays use 2-5%. Only autonomous sizing is capped."""

    engine = RiskEngine()

    def check(risk: Decimal) -> object:
        request = RiskCheckRequest(
            symbol="BTCUSDT",
            direction=TradeDirection.LONG,
            entry_price=Decimal("60000"),
            position_size=Decimal("0.005"),
            leverage=Decimal("1"),
            account_equity=Decimal("10000"),
            stop_loss=Decimal("59000"),
            volume_24h=Decimal("50000000"),
            risk_percent=risk,
        )
        return engine.evaluate(request, context=RiskEvaluationContext(is_weekend=False))

    paper = check(Decimal("3"))
    backtest = check(Decimal("5"))
    assert paper.action is RiskAction.ALLOW  # type: ignore[attr-defined]
    assert backtest.action is RiskAction.ALLOW  # type: ignore[attr-defined]
    assert all(item.rule_id is not RiskRuleId.RISK_PERCENT for item in paper.triggered_rules)
    assert all(item.rule_id is not RiskRuleId.RISK_PERCENT for item in backtest.triggered_rules)


def test_apply_requires_owner_proof_and_refuses_service_and_agent_tokens() -> None:
    missing = channel_from_access_payload({"type": "access", "sub": "sofien"})
    assert missing is PolicyChannel.UNDECLARED
    agent = channel_from_access_payload({"type": "access", "actor": "agent"})
    assert agent is PolicyChannel.AGENT
    service_channel = channel_from_access_payload({"type": "access", "actor": "service"})
    assert service_channel is PolicyChannel.SERVICE
    with pytest.raises(ValidationError):
        RiskPolicyApplyRequest.model_validate({"confirm": True})
    session = _session()
    service = _service(session)
    with pytest.raises(RiskPolicyRejected) as service_token:
        service.propose(
            organization_id=ORG,
            actor=PolicyActor("svc", PolicyChannel.SERVICE, True),
            profile=placeholder_profile(PolicyKind.DEMO),
            reason="Service tokens do not propose.",
            now=NOW,
        )
    assert service_token.value.code == "service_token_refused"
    proposal = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=placeholder_profile(PolicyKind.DEMO),
        reason="Owner apply with a proof.",
        now=NOW,
    )
    stale = HumanApplyProof(
        method="interactive_step_up",
        subject_user_id="sofien",
        proposal_id=str(proposal.id),
        content_hash=proposal.content_hash,
        expires_at=NOW + timedelta(minutes=10),
        session_id="interactive-session",
        authenticated_at=NOW - timedelta(minutes=16),
        step_up_at=NOW - timedelta(minutes=16),
    )
    with pytest.raises(RiskPolicyRejected) as fresh:
        service.apply(
            organization_id=ORG,
            proposal_id=proposal.id,
            actor=_human(),
            proof=stale,
            owner_user_id="sofien",
            now=NOW,
        )
    assert fresh.value.code == "apply_proof_not_fresh"
    with pytest.raises(RiskPolicyRejected) as owner:
        service.apply(
            organization_id=ORG,
            proposal_id=proposal.id,
            actor=PolicyActor("other-owner", PolicyChannel.HUMAN, True),
            proof=_proof(proposal),
            owner_user_id="sofien",
            now=NOW,
        )
    assert owner.value.code == "apply_owner_mismatch"
    wrong_chat = HumanApplyProof(
        method="telegram_owner",
        subject_user_id="sofien",
        proposal_id=str(proposal.id),
        content_hash=proposal.content_hash,
        expires_at=NOW + timedelta(minutes=10),
        telegram_chat_id="someone-else",
        telegram_confirmation_id="confirm-1",
    )
    with pytest.raises(RiskPolicyRejected) as chat:
        service.apply(
            organization_id=ORG,
            proposal_id=proposal.id,
            actor=PolicyActor("sofien", PolicyChannel.UNDECLARED, True),
            proof=wrong_chat,
            owner_user_id="sofien",
            pinned_telegram_chat_id="pinned-owner-chat",
            now=NOW,
        )
    assert chat.value.code == "apply_proof_chat_mismatch"
    with pytest.raises(RiskPolicyRejected) as service_apply:
        assert_can_apply(
            PolicyActor("svc", PolicyChannel.SERVICE, True),
            _proof(proposal),
            owner_user_id="sofien",
            pinned_telegram_chat_id="pinned-owner-chat",
            proposal_id=str(proposal.id),
            content_hash=proposal.content_hash,
            now=NOW,
        )
    assert service_apply.value.code == "apply_refuses_service_token"
    telegram = HumanApplyProof(
        method="telegram_owner",
        subject_user_id="sofien",
        proposal_id=str(proposal.id),
        content_hash=proposal.content_hash,
        expires_at=NOW + timedelta(minutes=10),
        issued_at=NOW - timedelta(seconds=30),
        telegram_chat_id="pinned-owner-chat",
        telegram_confirmation_id="confirm-1",
    )
    applied = service.apply(
        organization_id=ORG,
        proposal_id=proposal.id,
        actor=PolicyActor("sofien", PolicyChannel.UNDECLARED, True),
        proof=telegram,
        owner_user_id="sofien",
        pinned_telegram_chat_id="pinned-owner-chat",
        now=NOW,
    )
    assert applied.actor_channel == "human"
    assert applied.change_class == "initial"


def test_apply_proof_rejects_a_missing_or_unequal_proposal_and_a_stale_telegram() -> None:
    settings = Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="test-secret-key-for-auth-slice-32b-min",
        access_token_expire_minutes=15,
        refresh_token_expire_days=7,
        access_token_denylist_use_redis=False,
    )
    session = _session()
    service = _service(session)
    proposal = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=placeholder_profile(PolicyKind.DEMO),
        reason="Proof must name this proposal.",
        now=NOW,
    )
    other = uuid.uuid4()

    def token(claims: dict[str, object]) -> str:
        payload = {
            key: int(value.timestamp()) if isinstance(value, datetime) else value
            for key, value in claims.items()
        }
        encoded = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
        assert isinstance(encoded, str)
        return encoded

    def step_up(**overrides: object) -> dict[str, object]:
        claims: dict[str, object] = {
            "type": "risk_policy_step_up",
            "sub": "sofien",
            "proposal_id": str(proposal.id),
            "content_hash": proposal.content_hash,
            "exp": NOW + timedelta(minutes=5),
            "session_id": "interactive-session",
            "auth_time": NOW - timedelta(minutes=1),
            "step_up_at": NOW - timedelta(seconds=20),
        }
        claims.update(overrides)
        return claims

    def telegram(**overrides: object) -> dict[str, object]:
        claims: dict[str, object] = {
            "type": "risk_policy_telegram",
            "sub": "sofien",
            "proposal_id": str(proposal.id),
            "content_hash": proposal.content_hash,
            "exp": NOW + timedelta(days=1),
            "iat": NOW - timedelta(minutes=1),
            "chat_id": "pinned-owner-chat",
            "confirmation_id": "confirm-1",
        }
        claims.update(overrides)
        return claims

    matched = decode_apply_proof(token(step_up()), settings, proposal_id=proposal.id, now=NOW)
    assert matched.proposal_id == str(proposal.id)
    fresh = decode_apply_proof(token(telegram()), settings, proposal_id=proposal.id, now=NOW)
    assert fresh.issued_at == NOW - timedelta(minutes=1)
    applied = service.apply(
        organization_id=ORG,
        proposal_id=proposal.id,
        actor=_human(),
        proof=fresh,
        owner_user_id="sofien",
        pinned_telegram_chat_id="pinned-owner-chat",
        now=NOW,
    )
    assert applied.change_class == "initial"

    for claims in (step_up(), telegram()):
        omitted = dict(claims)
        del omitted["proposal_id"]
        with pytest.raises(RiskPolicyRejected) as missing:
            decode_apply_proof(token(omitted), settings, proposal_id=proposal.id, now=NOW)
        assert missing.value.code == "apply_proof_missing"
        with pytest.raises(RiskPolicyRejected) as unequal:
            decode_apply_proof(
                token({**claims, "proposal_id": str(other)}),
                settings,
                proposal_id=proposal.id,
                now=NOW,
            )
        assert unequal.value.code == "apply_proof_proposal_mismatch"

    with pytest.raises(RiskPolicyRejected) as stale:
        decode_apply_proof(
            token(telegram(iat=NOW - timedelta(minutes=6))),
            settings,
            proposal_id=proposal.id,
            now=NOW,
        )
    assert stale.value.code == "apply_proof_not_fresh"
    with pytest.raises(RiskPolicyRejected) as future:
        decode_apply_proof(
            token(telegram(iat=NOW + timedelta(seconds=1))),
            settings,
            proposal_id=proposal.id,
            now=NOW,
        )
    assert future.value.code == "apply_proof_not_fresh"
    without_issued = dict(telegram())
    del without_issued["iat"]
    with pytest.raises(RiskPolicyRejected) as no_iat:
        decode_apply_proof(token(without_issued), settings, proposal_id=proposal.id, now=NOW)
    assert no_iat.value.code == "apply_proof_not_fresh"
    for overrides in (
        {"auth_time": NOW + timedelta(days=1), "step_up_at": NOW + timedelta(days=1)},
        {"auth_time": NOW + timedelta(seconds=1)},
        {"step_up_at": NOW + timedelta(seconds=1)},
        {"auth_time": NOW - timedelta(minutes=16), "step_up_at": NOW - timedelta(minutes=16)},
    ):
        with pytest.raises(RiskPolicyRejected) as shifted:
            decode_apply_proof(
                token(step_up(exp=NOW + timedelta(days=2), **overrides)),
                settings,
                proposal_id=proposal.id,
                now=NOW,
            )
        assert shifted.value.code == "apply_proof_not_fresh"
    still_fresh = decode_apply_proof(
        token(
            step_up(
                exp=NOW + timedelta(days=1),
                auth_time=NOW - timedelta(minutes=15),
                step_up_at=NOW - timedelta(minutes=15),
            )
        ),
        settings,
        proposal_id=proposal.id,
        now=NOW,
    )
    assert still_fresh.authenticated_at == NOW - timedelta(minutes=15)

    later = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=profile_with(PolicyKind.DEMO, daily_loss_limit_percent=Decimal("0.5")),
        reason="A second proposal for the apply-path freshness check.",
        now=NOW,
    )
    old = HumanApplyProof(
        method="telegram_owner",
        subject_user_id="sofien",
        proposal_id=str(later.id),
        content_hash=later.content_hash,
        expires_at=NOW + timedelta(days=1),
        issued_at=NOW - timedelta(minutes=6),
        telegram_chat_id="pinned-owner-chat",
        telegram_confirmation_id="confirm-2",
    )
    with pytest.raises(RiskPolicyRejected) as aged:
        service.apply(
            organization_id=ORG,
            proposal_id=later.id,
            actor=_human(),
            proof=old,
            owner_user_id="sofien",
            pinned_telegram_chat_id="pinned-owner-chat",
            now=NOW,
        )
    assert aged.value.code == "apply_proof_not_fresh"
    ahead = HumanApplyProof(
        method="telegram_owner",
        subject_user_id="sofien",
        proposal_id=str(later.id),
        content_hash=later.content_hash,
        expires_at=NOW + timedelta(minutes=10),
        issued_at=NOW + timedelta(seconds=1),
        telegram_chat_id="pinned-owner-chat",
        telegram_confirmation_id="confirm-3",
    )
    with pytest.raises(RiskPolicyRejected) as dated:
        service.apply(
            organization_id=ORG,
            proposal_id=later.id,
            actor=_human(),
            proof=ahead,
            owner_user_id="sofien",
            pinned_telegram_chat_id="pinned-owner-chat",
            now=NOW,
        )
    assert dated.value.code == "apply_proof_not_fresh"
    ahead_step = HumanApplyProof(
        method="interactive_step_up",
        subject_user_id="sofien",
        proposal_id=str(later.id),
        content_hash=later.content_hash,
        expires_at=NOW + timedelta(days=1),
        session_id="interactive-session",
        authenticated_at=NOW + timedelta(days=1),
        step_up_at=NOW + timedelta(days=1, seconds=1),
    )
    with pytest.raises(RiskPolicyRejected) as future_step:
        service.apply(
            organization_id=ORG,
            proposal_id=later.id,
            actor=_human(),
            proof=ahead_step,
            owner_user_id="sofien",
            pinned_telegram_chat_id="pinned-owner-chat",
            now=NOW,
        )
    assert future_step.value.code == "apply_proof_not_fresh"
    future_auth_only = HumanApplyProof(
        method="interactive_step_up",
        subject_user_id="sofien",
        proposal_id=str(later.id),
        content_hash=later.content_hash,
        expires_at=NOW + timedelta(days=1),
        session_id="interactive-session",
        authenticated_at=NOW + timedelta(seconds=1),
        step_up_at=NOW - timedelta(seconds=20),
    )
    with pytest.raises(RiskPolicyRejected) as future_auth:
        service.apply(
            organization_id=ORG,
            proposal_id=later.id,
            actor=_human(),
            proof=future_auth_only,
            owner_user_id="sofien",
            pinned_telegram_chat_id="pinned-owner-chat",
            now=NOW,
        )
    assert future_auth.value.code == "apply_proof_not_fresh"


def test_apply_proof_rejects_bad_claims_type_confusion_and_replay() -> None:
    settings = Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="test-secret-key-for-auth-slice-32b-min",
        access_token_expire_minutes=15,
        refresh_token_expire_days=7,
        access_token_denylist_use_redis=False,
    )
    session = _session()
    service = _service(session)
    proposal = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=placeholder_profile(PolicyKind.DEMO),
        reason="Proof claims must be bound and single-use per proposal.",
        now=NOW,
    )
    other = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=profile_with(PolicyKind.DEMO, daily_loss_limit_percent=Decimal("0.5")),
        reason="A second proposal cannot reuse the first proof.",
        now=NOW,
    )

    def token(claims: dict[str, object]) -> str:
        payload = {
            key: int(value.timestamp()) if isinstance(value, datetime) else value
            for key, value in claims.items()
        }
        encoded = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
        assert isinstance(encoded, str)
        return encoded

    step = {
        "type": "risk_policy_step_up",
        "sub": "sofien",
        "proposal_id": str(proposal.id),
        "content_hash": proposal.content_hash,
        "exp": NOW + timedelta(minutes=5),
        "session_id": "interactive-session",
        "auth_time": NOW - timedelta(minutes=1),
        "step_up_at": NOW - timedelta(seconds=20),
    }
    proof = decode_apply_proof(token(step), settings, proposal_id=proposal.id, now=NOW)
    applied = service.apply(
        organization_id=ORG,
        proposal_id=proposal.id,
        actor=_human(),
        proof=proof,
        owner_user_id="sofien",
        now=NOW,
    )
    assert applied.change_class == "initial"
    with pytest.raises(RiskPolicyRejected) as unchanged:
        service.apply(
            organization_id=ORG,
            proposal_id=proposal.id,
            actor=_human(),
            proof=proof,
            owner_user_id="sofien",
            now=NOW,
        )
    assert unchanged.value.code == "no_change"
    with pytest.raises(RiskPolicyRejected) as reused:
        decode_apply_proof(token(step), settings, proposal_id=other.id, now=NOW)
    assert reused.value.code == "apply_proof_proposal_mismatch"
    with pytest.raises(RiskPolicyRejected) as other_org:
        service.apply(
            organization_id=uuid.uuid4(),
            proposal_id=other.id,
            actor=_human(),
            proof=decode_apply_proof(
                token({**step, "proposal_id": str(other.id), "content_hash": other.content_hash}),
                settings,
                proposal_id=other.id,
                now=NOW,
            ),
            owner_user_id="sofien",
            now=NOW,
        )
    assert other_org.value.code == "proposal_not_found"

    blank_proposal = dict(step)
    blank_proposal["proposal_id"] = "  "
    with pytest.raises(RiskPolicyRejected) as blank:
        decode_apply_proof(token(blank_proposal), settings, proposal_id=proposal.id, now=NOW)
    assert blank.value.code == "apply_proof_missing"
    with pytest.raises(RiskPolicyRejected) as numeric_proposal:
        decode_apply_proof(
            token({**step, "proposal_id": 12345}),
            settings,
            proposal_id=proposal.id,
            now=NOW,
        )
    assert numeric_proposal.value.code == "apply_proof_missing"
    for claim in ("sub", "content_hash"):
        omitted = dict(step)
        del omitted[claim]
        with pytest.raises(RiskPolicyRejected) as missing_binding:
            decode_apply_proof(token(omitted), settings, proposal_id=proposal.id, now=NOW)
        assert missing_binding.value.code == "apply_proof_missing"
    wrong_subject = HumanApplyProof(
        method="interactive_step_up",
        subject_user_id="someone-else",
        proposal_id=str(other.id),
        content_hash=other.content_hash,
        expires_at=NOW + timedelta(minutes=10),
        session_id="interactive-session",
        authenticated_at=NOW - timedelta(minutes=1),
        step_up_at=NOW - timedelta(seconds=20),
    )
    with pytest.raises(RiskPolicyRejected) as subject:
        service.apply(
            organization_id=ORG,
            proposal_id=other.id,
            actor=_human(),
            proof=wrong_subject,
            owner_user_id="sofien",
            now=NOW,
        )
    assert subject.value.code == "apply_owner_mismatch"
    wrong_hash = HumanApplyProof(
        method="interactive_step_up",
        subject_user_id="sofien",
        proposal_id=str(other.id),
        content_hash="0" * 64,
        expires_at=NOW + timedelta(minutes=10),
        session_id="interactive-session",
        authenticated_at=NOW - timedelta(minutes=1),
        step_up_at=NOW - timedelta(seconds=20),
    )
    with pytest.raises(RiskPolicyRejected) as hashed:
        service.apply(
            organization_id=ORG,
            proposal_id=other.id,
            actor=_human(),
            proof=wrong_hash,
            owner_user_id="sofien",
            now=NOW,
        )
    assert hashed.value.code == "apply_proof_proposal_mismatch"

    for bad_time in (float("nan"), float("inf"), "1700000000", True, 10**30):
        with pytest.raises(RiskPolicyRejected) as non_numeric:
            decode_apply_proof(
                token({**step, "proposal_id": str(other.id), "auth_time": bad_time}),
                settings,
                proposal_id=other.id,
                now=NOW,
            )
        assert non_numeric.value.code == "apply_proof_not_fresh"
    with pytest.raises(RiskPolicyRejected) as bad_exp:
        decode_apply_proof(
            token({**step, "proposal_id": str(other.id), "exp": float("nan")}),
            settings,
            proposal_id=other.id,
            now=NOW,
        )
    assert bad_exp.value.code == "apply_proof_expired"
    with pytest.raises(RiskPolicyRejected) as access_as_proof:
        decode_apply_proof(
            token({**step, "type": "access", "actor": "user"}),
            settings,
            proposal_id=proposal.id,
            now=NOW,
        )
    assert access_as_proof.value.code == "apply_proof_missing"
    with pytest.raises(RiskPolicyRejected) as list_type:
        decode_apply_proof(
            token({**step, "type": ["risk_policy_step_up"]}),
            settings,
            proposal_id=proposal.id,
            now=NOW,
        )
    assert list_type.value.code == "apply_proof_missing"
    from app.core.errors import AuthError
    from app.security.tokens import decode_access_token

    with pytest.raises(AuthError):
        decode_access_token(token(step), settings)
    telegram_as_step = dict(step)
    telegram_as_step["type"] = "risk_policy_telegram"
    with pytest.raises(RiskPolicyRejected) as swapped:
        decode_apply_proof(token(telegram_as_step), settings, proposal_id=proposal.id, now=NOW)
    assert swapped.value.code == "apply_proof_not_fresh"
    missing_session = dict(step)
    del missing_session["session_id"]
    with pytest.raises(RiskPolicyRejected) as no_session:
        decode_apply_proof(
            token({**missing_session, "proposal_id": str(other.id)}),
            settings,
            proposal_id=other.id,
            now=NOW,
        )
    assert no_session.value.code == "apply_proof_not_fresh"
    tightened = decode_apply_proof(
        token({**step, "proposal_id": str(other.id), "content_hash": other.content_hash}),
        settings,
        proposal_id=other.id,
        now=NOW,
    )
    second = service.apply(
        organization_id=ORG,
        proposal_id=other.id,
        actor=_human(),
        proof=tightened,
        owner_user_id="sofien",
        now=NOW,
    )
    assert second.change_class == "tightening"
    with pytest.raises(RiskPolicyRejected) as replayed:
        service.apply(
            organization_id=ORG,
            proposal_id=proposal.id,
            actor=_human(),
            proof=proof,
            owner_user_id="sofien",
            now=NOW,
        )
    assert replayed.value.code == "version_conflict"


def test_risk_policy_actor_binds_the_access_token_subject_and_org() -> None:
    from fastapi.security import HTTPAuthorizationCredentials

    from app.api.routes.risk_policy import _actor
    from app.core.errors import AuthError, ForbiddenError
    from app.schemas.common import MembershipRole
    from app.security.tenant import TenantContext
    from app.security.tokens import create_access_token

    settings = Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="test-secret-key-for-auth-slice-32b-min",
        access_token_expire_minutes=15,
        refresh_token_expire_days=7,
        access_token_denylist_use_redis=False,
    )
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    access, _expires = create_access_token(
        user_id=user_id,
        organization_id=organization_id,
        email="owner@example.com",
        settings=settings,
        actor="user",
    )
    tenant = TenantContext(
        user_id=user_id,
        organization_id=organization_id,
        email="owner@example.com",
        membership_role=MembershipRole.OWNER,
    )
    actor = _actor(
        tenant,
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=access),
        settings,
    )
    assert actor.actor_id == str(user_id)
    assert actor.channel is PolicyChannel.HUMAN
    with pytest.raises(ForbiddenError):
        _actor(
            TenantContext(
                user_id=uuid.uuid4(),
                organization_id=organization_id,
                email="owner@example.com",
                membership_role=MembershipRole.OWNER,
            ),
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=access),
            settings,
        )
    with pytest.raises(ForbiddenError):
        _actor(
            TenantContext(
                user_id=user_id,
                organization_id=uuid.uuid4(),
                email="owner@example.com",
                membership_role=MembershipRole.OWNER,
            ),
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=access),
            settings,
        )
    wall = datetime.now(UTC)
    proof = jwt.encode(
        {
            "type": "risk_policy_step_up",
            "sub": str(user_id),
            "org_id": str(organization_id),
            "exp": int((wall + timedelta(minutes=5)).timestamp()),
            "iat": int(wall.timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    assert isinstance(proof, str)
    with pytest.raises(AuthError):
        _actor(tenant, HTTPAuthorizationCredentials(scheme="Bearer", credentials=proof), settings)


def test_loosening_waits_for_midnight_and_twenty_four_hours_and_proposals_expire() -> None:
    late = datetime(2026, 10, 18, 23, 59, 59, tzinfo=UTC)
    assert loosening_effective_at(late) == datetime(2026, 10, 19, 23, 59, 59, tzinfo=UTC)
    session = _session()
    service = _service(session)
    initial = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=placeholder_profile(PolicyKind.DEMO),
        reason="Initial profile before the late loosening.",
        now=NOW,
    )
    _apply(service, initial, _human())
    tighter = profile_with(PolicyKind.DEMO, daily_loss_limit_percent=Decimal("0.5"))
    tight = service.propose(
        organization_id=ORG, actor=_human(), profile=tighter, reason="Tighten before late.", now=NOW
    )
    _apply(service, tight, _human())
    restore = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=placeholder_profile(PolicyKind.DEMO),
        reason="Restore after a late apply.",
        now=late,
    )
    scheduled = _apply(service, restore, _human(), late)
    assert scheduled.effective_at == datetime(2026, 10, 19, 23, 59, 59, tzinfo=UTC)
    expired_at = NOW - timedelta(hours=72, seconds=1)
    expired = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=profile_with(PolicyKind.STANDARD, daily_loss_limit_percent=Decimal("0.5")),
        reason="This proposal is older than 72 hours.",
        now=expired_at,
    )
    with pytest.raises(RiskPolicyRejected) as caught:
        _apply(service, expired, _human())
    assert caught.value.code == "proposal_expired"


def test_activation_cancels_a_loosening_when_a_breaker_is_active() -> None:
    session = _session()
    service = _service(session)
    initial = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=placeholder_profile(PolicyKind.DEMO),
        reason="Initial before the cancelled loosening.",
        now=NOW,
    )
    _apply(service, initial, _human())
    tighter = profile_with(PolicyKind.DEMO, daily_loss_limit_percent=Decimal("0.5"))
    tight_proposal = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=tighter,
        reason="The version that must stay active.",
        now=NOW,
    )
    tight = _apply(service, tight_proposal, _human())
    loose = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=placeholder_profile(PolicyKind.DEMO),
        reason="Scheduled loosening that a breaker should cancel.",
        now=NOW,
    )
    scheduled = _apply(service, loose, _human())
    effective = datetime(2026, 10, 19, 15, tzinfo=UTC)
    assert scheduled.effective_at == effective
    service.latch_drawdown(
        organization_id=ORG, account_id=uuid.uuid4(), equity_peak=Decimal("10000"), now=effective
    )
    current, active, _waiting = service.read(
        organization_id=ORG, kind=PolicyKind.DEMO, now=effective
    )
    assert active is not None and active.id == tight.id
    assert current.daily_loss_limit_percent == Decimal("0.5")
    cancelled = session.scalar(select(RiskPolicyVersionCancellation))
    assert cancelled is not None
    assert cancelled.version_id == scheduled.id
    assert cancelled.reason == "breaker_active_at_activation"
    breaker = session.scalar(select(RiskPolicyBreakerState))
    assert breaker is not None
    breaker.drawdown_latched = False
    session.flush()
    again, active_again, _waiting_again = service.read(
        organization_id=ORG, kind=PolicyKind.DEMO, now=effective
    )
    assert active_again is not None and active_again.id == tight.id
    assert again.daily_loss_limit_percent == Decimal("0.5")
    assert session.scalar(select(RiskPolicyVersionCancellation)) is not None


def test_grade_above_one_percent_requires_a_stored_promotion_report() -> None:
    raised = profile_with(PolicyKind.DEMO, risk_percent_by_grade=_grades("1.5"))
    session = _session()
    service = _service(session)
    proposal = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=raised,
        reason="Raise A+ only after a stored report.",
        now=NOW,
    )
    with pytest.raises(RiskPolicyRejected) as refused:
        _apply(service, proposal, _human())
    assert refused.value.code == "grade_above_one_percent_requires_promotion"
    from app.services.risk_policy.sizer import size_trade

    with pytest.raises(RiskPolicyRejected) as sized:
        size_trade(raised, _sizing_order())
    assert sized.value.code == "grade_above_one_percent_requires_promotion"
    report = _eligible_report()
    assert report.eligible
    service.store_promotion_report(organization_id=ORG, report=report, now=NOW)
    applied = _apply(service, proposal, _human())
    assert applied.risk_percent_a_plus == Decimal("1.5")
    allowed = size_trade(raised, _sizing_order(), promotion=report)
    assert allowed.profile_risk_percent == Decimal("1.5")


def test_unrealized_loss_trips_the_daily_breaker_and_reset_is_persisted() -> None:
    config = BreakerConfig(Decimal("1"), Decimal("10"), 4)
    verdict = evaluate_breakers(
        config,
        BreakerSnapshot(
            equity=Decimal("100"),
            equity_peak=Decimal("100"),
            reference_equity=Decimal("100"),
            realized_pnl_today=Decimal("0"),
            unrealized_pnl_today=Decimal("-2"),
            consecutive_losses=0,
            drawdown_latched=False,
            now=NOW,
        ),
    )
    assert verdict.daily_loss_active
    assert verdict.paused_until == datetime(2026, 10, 19, tzinfo=UTC)
    session = _session()
    service = _service(session)
    account = uuid.uuid4()
    pause = datetime(2026, 10, 19, tzinfo=UTC)
    service.record_breaker(
        organization_id=ORG,
        account_id=account,
        equity_peak=Decimal("10000"),
        consecutive_losses=4,
        realized_pnl=Decimal("-10"),
        unrealized_pnl=Decimal("-5"),
        daily_paused_until=pause,
        consecutive_paused_until=pause,
        drawdown_latched=True,
        now=NOW,
    )
    assert service.breaker_active(organization_id=ORG, now=NOW)
    with pytest.raises(RiskPolicyRejected) as agent:
        service.reset_drawdown_latch(
            organization_id=ORG,
            account_id=account,
            actor=_agent(),
            owner_user_id="sofien",
            equity=Decimal("9500"),
            drawdown_breaker_percent=Decimal("10"),
            now=NOW,
        )
    assert agent.value.code == "drawdown_reset_requires_human"
    service.reset_drawdown_latch(
        organization_id=ORG,
        account_id=account,
        actor=_human(),
        owner_user_id="sofien",
        equity=Decimal("9500"),
        drawdown_breaker_percent=Decimal("10"),
        now=NOW,
    )
    assert service.breaker_active(organization_id=ORG, now=NOW)
    reset = session.scalar(select(RiskPolicyBreakerReset))
    assert reset is not None
    assert reset.actor_id == "sofien"
    assert reset.actor_channel == "human"
    state = session.scalar(select(RiskPolicyBreakerState))
    assert state is not None
    assert state.drawdown_latched is False
    assert state.consecutive_losses == 4
    assert state.realized_pnl == Decimal("-10")
    assert state.unrealized_pnl == Decimal("-5")


def test_first_stored_profile_looser_than_the_placeholder_is_a_loosening() -> None:
    session = _session()
    service = _service(session)
    same = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=placeholder_profile(PolicyKind.DEMO),
        reason="The placeholder itself is not a loosening.",
        now=NOW,
    )
    applied = _apply(service, same, _human())
    assert applied.change_class == "initial"
    assert applied.effective_at == NOW

    fresh = _session()
    service = _service(fresh)
    proposal = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=profile_with(PolicyKind.DEMO, daily_loss_limit_percent=Decimal("2")),
        reason="First store above the placeholder daily limit.",
        now=NOW,
    )
    with pytest.raises(RiskPolicyRejected) as unconfirmed:
        service.apply(
            organization_id=ORG,
            proposal_id=proposal.id,
            actor=_human(),
            proof=None,
            owner_user_id="sofien",
            now=NOW,
        )
    assert unconfirmed.value.code == "apply_proof_missing"
    assert fresh.scalar(select(RiskPolicyVersion.id)) is None
    service.latch_drawdown(
        organization_id=ORG, account_id=uuid.uuid4(), equity_peak=Decimal("10000"), now=NOW
    )
    with pytest.raises(RiskPolicyRejected) as blocked:
        _apply(service, proposal, _human())
    assert blocked.value.code == "loosening_blocked_by_breaker"
    assert fresh.scalar(select(RiskPolicyVersion.id)) is None
    breaker = fresh.scalar(select(RiskPolicyBreakerState))
    assert breaker is not None
    breaker.drawdown_latched = False
    fresh.flush()
    scheduled = _apply(service, proposal, _human())
    assert scheduled.change_class == "loosening"
    assert scheduled.effective_at == datetime(2026, 10, 19, 15, tzinfo=UTC)
    assert scheduled.autonomous_internal_paper_enabled is False
    assert scheduled.autonomous_blofin_demo_enabled is False
    current, active, waiting = service.read(organization_id=ORG, kind=PolicyKind.DEMO, now=NOW)
    assert active is None
    assert current.daily_loss_limit_percent == Decimal("1")
    assert waiting is not None and waiting.id == scheduled.id


def test_breaker_cancellation_survives_the_read_session_rollback() -> None:
    session = _session()
    service = _service(session)
    initial = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=placeholder_profile(PolicyKind.DEMO),
        reason="Initial before the durable cancellation.",
        now=NOW,
    )
    _apply(service, initial, _human())
    tight = _apply(
        service,
        service.propose(
            organization_id=ORG,
            actor=_human(),
            profile=profile_with(PolicyKind.DEMO, daily_loss_limit_percent=Decimal("0.5")),
            reason="The version that must stay active.",
            now=NOW,
        ),
        _human(),
    )
    loose = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=placeholder_profile(PolicyKind.DEMO),
        reason="Scheduled loosening that must stay cancelled.",
        now=NOW,
    )
    scheduled = _apply(service, loose, _human())
    effective = datetime(2026, 10, 19, 15, tzinfo=UTC)
    scheduled_id = scheduled.id
    tight_id = tight.id
    service.latch_drawdown(
        organization_id=ORG, account_id=uuid.uuid4(), equity_peak=Decimal("10000"), now=effective
    )
    _current, active, _waiting = service.read(
        organization_id=ORG, kind=PolicyKind.DEMO, now=effective
    )
    assert active is not None and active.id == tight_id
    session.rollback()
    cancelled = session.scalar(select(RiskPolicyVersionCancellation))
    assert cancelled is not None
    assert cancelled.version_id == scheduled_id
    assert cancelled.reason == "breaker_active_at_activation"
    breaker = session.scalar(select(RiskPolicyBreakerState))
    assert breaker is not None
    breaker.drawdown_latched = False
    session.commit()
    again, active_again, waiting = service.read(
        organization_id=ORG, kind=PolicyKind.DEMO, now=effective
    )
    assert active_again is not None and active_again.id == tight_id
    assert again.daily_loss_limit_percent == Decimal("0.5")
    assert waiting is None
    with pytest.raises(RiskPolicyRejected) as conflict:
        _apply(service, loose, _human(), effective)
    assert conflict.value.code == "version_conflict"
    session.rollback()
    replacement = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=placeholder_profile(PolicyKind.DEMO),
        reason="A new proposal is required after cancellation.",
        now=effective,
    )
    rescheduled = _apply(service, replacement, _human(), effective)
    assert rescheduled.id != scheduled_id
    assert rescheduled.change_class == "loosening"
    assert rescheduled.effective_at == datetime(2026, 10, 20, 15, tzinfo=UTC)
    held, held_active, held_waiting = service.read(
        organization_id=ORG, kind=PolicyKind.DEMO, now=effective
    )
    assert held_active is not None and held_active.id == tight_id
    assert held.daily_loss_limit_percent == Decimal("0.5")
    assert held_waiting is not None and held_waiting.id == rescheduled.id


def test_undeclared_actor_is_rejected_before_a_proposal_insert() -> None:
    session = _session()
    service = _service(session)
    with pytest.raises(RiskPolicyRejected) as undeclared:
        service.propose(
            organization_id=ORG,
            actor=PolicyActor("sofien", PolicyChannel.UNDECLARED, True),
            profile=placeholder_profile(PolicyKind.DEMO),
            reason="A missing actor must not insert a proposal.",
            now=NOW,
        )
    assert undeclared.value.code == "actor_undeclared"
    assert session.scalar(select(RiskPolicyProposal.id)) is None
    with pytest.raises(RiskPolicyRejected) as blank:
        service.propose(
            organization_id=ORG,
            actor=PolicyActor("", PolicyChannel.HUMAN, True),
            profile=placeholder_profile(PolicyKind.DEMO),
            reason="A blank actor must not insert a proposal.",
            now=NOW,
        )
    assert blank.value.code == "actor_unauthenticated"
    assert session.scalar(select(RiskPolicyProposal.id)) is None


def test_persisted_drawdown_reset_refuses_while_equity_is_beyond_the_threshold() -> None:
    session = _session()
    service = _service(session)
    account = uuid.uuid4()
    service.record_breaker(
        organization_id=ORG,
        account_id=account,
        equity_peak=Decimal("10000"),
        consecutive_losses=0,
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        daily_paused_until=None,
        consecutive_paused_until=None,
        drawdown_latched=True,
        now=NOW,
    )
    with pytest.raises(RiskPolicyRejected) as still:
        service.reset_drawdown_latch(
            organization_id=ORG,
            account_id=account,
            actor=_human(),
            owner_user_id="sofien",
            equity=Decimal("9000"),
            drawdown_breaker_percent=Decimal("10"),
            now=NOW,
        )
    assert still.value.code == "drawdown_still_active"
    state = session.scalar(select(RiskPolicyBreakerState))
    assert state is not None
    assert state.drawdown_latched is True
    assert session.scalar(select(RiskPolicyBreakerReset.id)) is None
    service.reset_drawdown_latch(
        organization_id=ORG,
        account_id=account,
        actor=_human(),
        owner_user_id="sofien",
        equity=Decimal("9500"),
        drawdown_breaker_percent=Decimal("10"),
        now=NOW,
    )
    assert state.drawdown_latched is False
    reset = session.scalar(select(RiskPolicyBreakerReset))
    assert reset is not None
    assert reset.actor_channel == "human"


def test_loosening_is_cancelled_from_breaker_history_at_effective_time() -> None:
    """A late read still cancels a loosening whose effective time was inside a breaker."""

    effective = datetime(2026, 10, 19, 15, tzinfo=UTC)
    session = _session()
    service = _service(session)
    _store_tight_then_loose(service)
    account = uuid.uuid4()
    service.latch_drawdown(
        organization_id=ORG, account_id=account, equity_peak=Decimal("10000"), now=effective
    )
    service.reset_drawdown_latch(
        organization_id=ORG,
        account_id=account,
        actor=_human(),
        owner_user_id="sofien",
        equity=Decimal("9500"),
        drawdown_breaker_percent=Decimal("10"),
        now=datetime(2026, 10, 19, 16, tzinfo=UTC),
    )
    read_at = datetime(2026, 10, 19, 17, tzinfo=UTC)
    assert service.breaker_active(organization_id=ORG, now=read_at) is False
    _current, active, _waiting = service.read(
        organization_id=ORG, kind=PolicyKind.DEMO, now=read_at
    )
    session.rollback()
    assert active is not None
    assert active.daily_loss_limit_percent == Decimal("0.5")
    assert _current.daily_loss_limit_percent == Decimal("0.5")
    cancelled = session.scalar(select(RiskPolicyVersionCancellation))
    assert cancelled is not None
    assert cancelled.reason == "breaker_active_at_activation"
    again, active_again, _waiting_again = service.read(
        organization_id=ORG, kind=PolicyKind.DEMO, now=datetime(2026, 10, 19, 18, tzinfo=UTC)
    )
    assert active_again is not None and active_again.id == active.id
    assert again.daily_loss_limit_percent == Decimal("0.5")

    paused = _session()
    paused_service = _service(paused)
    _store_tight_then_loose(paused_service)
    trip = datetime(2026, 10, 19, 10, tzinfo=UTC)
    paused_service.record_breaker(
        organization_id=ORG,
        account_id=uuid.uuid4(),
        equity_peak=Decimal("10000"),
        consecutive_losses=0,
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        daily_paused_until=datetime(2026, 10, 20, tzinfo=UTC),
        consecutive_paused_until=None,
        drawdown_latched=False,
        now=trip,
    )
    paused_service.record_breaker(
        organization_id=ORG,
        account_id=uuid.uuid4(),
        equity_peak=Decimal("10000"),
        consecutive_losses=0,
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        daily_paused_until=None,
        consecutive_paused_until=None,
        drawdown_latched=False,
        now=datetime(2026, 10, 20, 1, tzinfo=UTC),
    )
    held, held_active, _held_waiting = paused_service.read(
        organization_id=ORG, kind=PolicyKind.DEMO, now=datetime(2026, 10, 20, 2, tzinfo=UTC)
    )
    assert held_active is not None
    assert held.daily_loss_limit_percent == Decimal("0.5")
    assert paused.scalar(select(RiskPolicyVersionCancellation)) is not None

    later = _session()
    later_service = _service(later)
    loose = _store_tight_then_loose(later_service)
    later_service.latch_drawdown(
        organization_id=ORG,
        account_id=uuid.uuid4(),
        equity_peak=Decimal("10000"),
        now=datetime(2026, 10, 19, 16, tzinfo=UTC),
    )
    current, active_later, _waiting_later = later_service.read(
        organization_id=ORG, kind=PolicyKind.DEMO, now=datetime(2026, 10, 19, 16, tzinfo=UTC)
    )
    assert active_later is not None and active_later.id == loose.id
    assert current.daily_loss_limit_percent == Decimal("1")
    assert later.scalar(select(RiskPolicyVersionCancellation)) is None


def _store_tight_then_loose(service: RiskPolicyService):
    initial = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=placeholder_profile(PolicyKind.DEMO),
        reason="Initial before the historical breaker.",
        now=NOW,
    )
    _apply(service, initial, _human())
    tight = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=profile_with(PolicyKind.DEMO, daily_loss_limit_percent=Decimal("0.5")),
        reason="The version that must stay active.",
        now=NOW,
    )
    _apply(service, tight, _human())
    loose = service.propose(
        organization_id=ORG,
        actor=_human(),
        profile=placeholder_profile(PolicyKind.DEMO),
        reason="Loosening whose effective time may fall inside a breaker.",
        now=NOW,
    )
    scheduled = _apply(service, loose, _human())
    assert scheduled.effective_at == datetime(2026, 10, 19, 15, tzinfo=UTC)
    return scheduled


def _sizing_order():
    from app.services.risk_policy.sizer import SizingInput

    return SizingInput(
        equity=Decimal("10000"),
        entry=Decimal("100"),
        stop=Decimal("98"),
        direction="long",
        grade=Grade.A_PLUS,
        grade_risk_percent=Decimal("1.5"),
        atr=Decimal("1"),
        gap_beyond_stop=Decimal("0"),
        fee_rate=Decimal("0"),
        funding_rate=Decimal("0"),
        funding_periods=Decimal("0"),
        slippage_rate=Decimal("0"),
        lot_step=Decimal("0.00000001"),
        min_quantity=Decimal("0.00000001"),
        remaining_daily_budget_percent=Decimal("6"),
        open_risk_percent=Decimal("0"),
        open_notional=Decimal("0"),
        open_positions=0,
        leverage=Decimal("1"),
        margin_mode="isolated",
    )
