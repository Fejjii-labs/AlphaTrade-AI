"""Governance, grading, adjusters, breakers, calibration, and the risk_percent rule."""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.analysis.types import Indicators, MarketStructure
from app.db.base import Base
from app.db.models import AuditLog, Organization
from app.db.risk_policy import RiskPolicyVersion
from app.schemas.common import AuditEventType, RiskAction, RiskRuleId, TradeDirection
from app.schemas.risk import RiskCheckRequest
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
    promote,
    promotion_report,
)
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.governance import PolicyActor, PolicyChannel
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

NOW = datetime(2026, 10, 18, 15, 0, tzinfo=UTC)
ORG = uuid.UUID("00000000-0000-4000-8000-000000000107")


def _human() -> PolicyActor:
    return PolicyActor("sofien", PolicyChannel.HUMAN, True)


def _agent() -> PolicyActor:
    return PolicyActor("agent-1", PolicyChannel.AGENT, True)


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
            confirmed=True,
            now=NOW,
        )
    assert refused.value.code == "apply_requires_human"
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
            confirmed=True,
            now=NOW,
        )
    assert bot_refused.value.code == "apply_requires_human"
    applied = service.apply(
        organization_id=ORG,
        proposal_id=proposal.id,
        actor=_human(),
        confirmed=False,
        now=NOW,
    )
    assert applied.change_class == "initial"
    assert applied.effective_at == NOW
    assert applied.actor_channel == "human"
    tighter = profile_with(PolicyKind.DEMO, daily_loss_limit_percent=Decimal("0.5"))
    tight_proposal = service.propose(
        organization_id=ORG, actor=_human(), profile=tighter, reason="Cut the daily loss.", now=NOW
    )
    tight = service.apply(
        organization_id=ORG,
        proposal_id=tight_proposal.id,
        actor=_human(),
        confirmed=False,
        now=NOW,
    )
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
            confirmed=False,
            now=NOW,
        )
    assert unconfirmed.value.code == "loosening_requires_confirmation"
    service.latch_drawdown(
        organization_id=ORG, account_id=uuid.uuid4(), equity_peak=Decimal("10000"), now=NOW
    )
    with pytest.raises(RiskPolicyRejected) as blocked:
        service.apply(
            organization_id=ORG,
            proposal_id=loose_proposal.id,
            actor=_human(),
            confirmed=True,
            now=NOW,
        )
    assert blocked.value.code == "loosening_blocked_by_breaker"
    session.query(RiskPolicyVersion).all()  # keep the import path honest
    state = session.scalars(select(RiskPolicyVersion)).all()
    latched = next(row for row in state if row.id)
    from app.db.risk_policy import RiskPolicyBreakerState

    breaker = session.scalar(select(RiskPolicyBreakerState))
    assert breaker is not None
    breaker.drawdown_latched = False
    session.flush()
    scheduled = service.apply(
        organization_id=ORG,
        proposal_id=loose_proposal.id,
        actor=_human(),
        confirmed=True,
        now=NOW,
    )
    assert scheduled.change_class == "loosening"
    assert scheduled.effective_at == datetime(2026, 10, 19, tzinfo=UTC)
    current, active, waiting = service.read(organization_id=ORG, kind=PolicyKind.DEMO, now=NOW)
    assert active is not None and active.id == tight.id
    assert current.daily_loss_limit_percent == Decimal("0.5")
    assert waiting is not None and waiting.id == scheduled.id
    later, active_later, _waiting = service.read(
        organization_id=ORG,
        kind=PolicyKind.DEMO,
        now=datetime(2026, 10, 19, tzinfo=UTC),
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
