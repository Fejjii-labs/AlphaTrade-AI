"""Grade sizing, ceilings, leverage, and the demo leverage-mismatch block."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.governed_blofin import GovernedBloFinDemoProvider
from app.schemas.common import RiskRuleId, TradeDirection
from app.schemas.risk import RiskCheckRequest
from app.schemas.trade_plan import EntrySide
from app.services.automated_paper_loop import _quantity
from app.services.risk.engine import RiskEngine
from app.services.risk.rules import RiskEvaluationContext
from app.services.risk_policy.adjusters import AdjusterInputs
from app.services.risk_policy.breakers import BreakerVerdict
from app.services.risk_policy.decision import decide_trade
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.grader import GradeDecision
from app.services.risk_policy.plan_sizing import (
    PAPER_DEMO_MAINTENANCE_TIERS,
    ActiveProfile,
    demo_leverage_block,
    demo_margin_mode_block,
    size_for_profile,
)
from app.services.risk_policy.profile import (
    ChangeClass,
    Grade,
    PolicyKind,
    classify_change,
    placeholder_profile,
    profile_with,
    sofien_aggressive_profile,
)
from app.services.risk_policy.sizer import SizingInput, size_trade
from tests.support.phase5_market import EVALUATED_AT

_TS = str(int(EVALUATED_AT.timestamp() * 1000))


def _grades(kind: PolicyKind, **updates: Decimal) -> dict[Grade, Decimal]:
    grades = dict(placeholder_profile(kind).risk_percent_by_grade)
    grades.update(updates)
    return grades


def _input(profile, grade: Grade, **overrides: object) -> SizingInput:
    base: dict[str, object] = {
        "equity": Decimal("10000"),
        "entry": Decimal("100"),
        "stop": Decimal("98"),
        "direction": "long",
        "grade": grade,
        "grade_risk_percent": profile.risk_percent(grade),
        "atr": Decimal("1"),
        "gap_beyond_stop": Decimal("0"),
        "fee_rate": Decimal("0"),
        "funding_rate": Decimal("0"),
        "funding_periods": Decimal("0"),
        "slippage_rate": Decimal("0"),
        "lot_step": Decimal("0.00000001"),
        "min_quantity": Decimal("0.00000001"),
        "remaining_daily_budget_percent": profile.daily_loss_limit_percent,
        "open_risk_percent": Decimal("0"),
        "open_notional": Decimal("0"),
        "open_positions": 0,
        "leverage": profile.default_leverage,
        "margin_mode": "isolated",
    }
    base.update(overrides)
    return SizingInput(**base)  # type: ignore[arg-type]


def _breaker() -> BreakerVerdict:
    return BreakerVerdict(
        daily_loss_active=False,
        drawdown_active=False,
        consecutive_loss_active=False,
        trip_kill_switch=False,
        paused_until=None,
        equity_peak=Decimal("10000"),
        refuse_new_trades=False,
        human_reset_required=False,
    )


def _decide(profile, grade: Grade, **overrides: object):
    return decide_trade(
        profile=profile,
        profile_version_id=uuid4(),
        grade=GradeDecision(grade, "deterministic-grade/v1", Decimal("1"), Decimal("1"), None),
        adjusters=AdjusterInputs(
            volatility_regime="normal", trend_aligned=True, momentum_aligned=True
        ),
        sizing=_input(profile, grade, **overrides),
        breaker=_breaker(),
        tiers=PAPER_DEMO_MAINTENANCE_TIERS,
        preserve_grade_risk=True,
    )


def test_placeholder_stays_conservative_and_aggressive_is_selectable() -> None:
    placeholder = placeholder_profile(PolicyKind.INTERNAL_PAPER)
    assert placeholder.profile_name == ""
    assert placeholder.risk_percent(Grade.A_PLUS) == Decimal("1")
    assert placeholder.default_leverage == Decimal("1")
    assert placeholder.paper_demo_data_collection is False
    assert placeholder.autonomous_internal_paper_enabled is False
    paper = sofien_aggressive_profile(PolicyKind.INTERNAL_PAPER)
    demo = sofien_aggressive_profile(PolicyKind.DEMO)
    assert paper.profile_name == "sofien_aggressive_paper"
    assert demo.profile_name == "sofien_aggressive_demo"
    assert [paper.risk_percent(grade) for grade in Grade] == [
        Decimal("5"),
        Decimal("3"),
        Decimal("2"),
        Decimal("1"),
        Decimal("0"),
    ]
    assert paper.default_leverage == Decimal("10")
    assert paper.max_leverage == Decimal("20")
    assert paper.paper_demo_data_collection is True
    assert paper.autonomous_internal_paper_enabled is False
    assert paper.autonomous_blofin_demo_enabled is False
    assert (
        classify_change(placeholder_profile(PolicyKind.DEMO), demo) is ChangeClass.LOOSENING
    )
    with pytest.raises(RiskPolicyRejected) as caught:
        sofien_aggressive_profile(PolicyKind.STANDARD)
    assert caught.value.code == "aggressive_profile_is_paper_or_demo_only"


def test_ceilings_allow_five_percent_only_for_paper_and_demo() -> None:
    with pytest.raises(RiskPolicyRejected) as standard:
        profile_with(
            PolicyKind.STANDARD,
            risk_percent_by_grade=_grades(PolicyKind.STANDARD, **{Grade.A_PLUS: Decimal("5")}),
        )
    assert standard.value.code == "risk_per_trade_exceeds_ceiling"
    profile_with(
        PolicyKind.DEMO,
        risk_percent_by_grade=_grades(PolicyKind.DEMO, **{Grade.A_PLUS: Decimal("5")}),
        max_leverage=Decimal("20"),
        default_leverage=Decimal("10"),
        daily_loss_limit_percent=Decimal("6"),
        max_aggregate_open_risk_percent=Decimal("6"),
        max_notional_per_trade_percent=Decimal("1000"),
        max_aggregate_notional_percent=Decimal("2000"),
        paper_demo_data_collection=True,
    )
    with pytest.raises(RiskPolicyRejected) as over:
        profile_with(
            PolicyKind.DEMO,
            risk_percent_by_grade=_grades(PolicyKind.DEMO, **{Grade.A_PLUS: Decimal("5.1")}),
            daily_loss_limit_percent=Decimal("6"),
            max_aggregate_open_risk_percent=Decimal("6"),
            max_notional_per_trade_percent=Decimal("1000"),
            max_aggregate_notional_percent=Decimal("2000"),
            paper_demo_data_collection=True,
        )
    assert over.value.code == "risk_per_trade_exceeds_ceiling"
    with pytest.raises(RiskPolicyRejected) as collection:
        profile_with(PolicyKind.CONSERVATIVE, paper_demo_data_collection=True)
    assert collection.value.code == "data_collection_not_paper_or_demo"
    with pytest.raises(RiskPolicyRejected) as flags:
        profile_with(PolicyKind.DEMO, autonomous_blofin_demo_enabled=True)
    assert flags.value.code == "autonomous_flags_must_stay_off"


def test_ten_thousand_dollar_grade_table_replaces_the_binding_notional_cap() -> None:
    # Before: 1% risk cash is $100, the 5% notional cap is $500, stop distance $2.
    # min(100/2, 500/100) = 5. Loss at the stop is $10, which is 0.1% of equity.
    assert _quantity(equity=Decimal("10000"), entry=Decimal("100"), distance=Decimal("2")) == (
        Decimal("5")
    )
    profile = sofien_aggressive_profile(PolicyKind.INTERNAL_PAPER)
    expected = {
        Grade.A_PLUS: (Decimal("250"), Decimal("500")),
        Grade.A: (Decimal("150"), Decimal("300")),
        Grade.B_PLUS: (Decimal("100"), Decimal("200")),
        Grade.B: (Decimal("50"), Decimal("100")),
    }
    for grade, (quantity, loss) in expected.items():
        decision = _decide(profile, grade)
        assert decision.accepted, decision.reason
        assert decision.sizing is not None
        assert decision.sizing.quantity == quantity
        assert decision.sizing.risk_amount == loss
        assert decision.sizing.notional == quantity * Decimal("100")
    recorded = size_for_profile(
        active=ActiveProfile(profile=profile, version_id=uuid4()),
        grade=GradeDecision(
            Grade.A_PLUS, "deterministic-grade/v1", Decimal("1"), Decimal("1"), None
        ),
        equity=Decimal("10000"),
        entry=Decimal("100"),
        stop=Decimal("98"),
        target=Decimal("104"),
        direction="long",
        atr=Decimal("1"),
        lot_step=Decimal("0.00000001"),
        min_quantity=Decimal("0.00000001"),
        open_risk_percent=Decimal("0"),
        open_notional=Decimal("0"),
        open_positions=0,
        remaining_daily_budget_percent=Decimal("6"),
        breaker_refusing=False,
    )
    assert not isinstance(recorded, str)
    assert recorded.record.calculation["entry"] == "100"
    assert recorded.record.calculation["stop"] == "98"
    assert recorded.record.calculation["target"] == "104"
    assert recorded.record.calculation["planned_r"] == "2.00000000"
    assert recorded.record.calculation["outcome"] == "open"
    assert recorded.record.calculation["profile_name"] == "sofien_aggressive_paper"
    refused = decide_trade(
        profile=profile,
        profile_version_id=uuid4(),
        grade=GradeDecision(Grade.C, "deterministic-grade/v1", Decimal("0"), Decimal("0"), None),
        adjusters=AdjusterInputs(
            volatility_regime="normal", trend_aligned=True, momentum_aligned=True
        ),
        sizing=_input(profile, Grade.C),
        breaker=_breaker(),
        tiers=PAPER_DEMO_MAINTENANCE_TIERS,
        preserve_grade_risk=True,
    )
    assert refused.accepted is False
    assert refused.reason == "grade_not_tradable"


def test_leverage_sets_margin_and_liquidation_can_lower_it() -> None:
    profile = sofien_aggressive_profile(PolicyKind.DEMO)
    at_ten = _decide(profile, Grade.B)
    at_one = _decide(profile, Grade.B, leverage=Decimal("1"))
    assert at_ten.accepted and at_one.accepted
    assert at_ten.sizing is not None and at_one.sizing is not None
    assert at_ten.sizing.quantity == at_one.sizing.quantity
    assert at_ten.sizing.margin < at_one.sizing.margin
    lowered = _decide(profile, Grade.A_PLUS, stop=Decimal("90"), atr=Decimal("1"))
    assert lowered.accepted, lowered.reason
    assert lowered.sizing is not None
    assert lowered.sizing.leverage == Decimal("4")
    assert lowered.sizing.quantity == Decimal("50")
    blocked = _decide(profile, Grade.A_PLUS, stop=Decimal("50"), atr=Decimal("1"))
    assert blocked.accepted is False
    assert blocked.reason == "liquidation_inside_buffer"
    too_much_margin = _decide(profile, Grade.A_PLUS, leverage=Decimal("1"))
    assert too_much_margin.accepted is False
    assert too_much_margin.reason == "margin_exceeds_equity"


def test_notional_cap_refuses_instead_of_shrinking_grade_risk() -> None:
    profile = profile_with(
        PolicyKind.DEMO,
        risk_percent_by_grade=_grades(PolicyKind.DEMO, **{Grade.A_PLUS: Decimal("5")}),
        daily_loss_limit_percent=Decimal("6"),
        max_aggregate_open_risk_percent=Decimal("6"),
        max_notional_per_trade_percent=Decimal("10"),
        max_aggregate_notional_percent=Decimal("25"),
        max_leverage=Decimal("20"),
        default_leverage=Decimal("10"),
        paper_demo_data_collection=True,
    )
    with pytest.raises(RiskPolicyRejected) as caught:
        size_trade(profile, _input(profile, Grade.A_PLUS), preserve_grade_risk=True)
    assert caught.value.code == "notional_cap_shrinks_grade_risk"
    shrunk = size_trade(profile, _input(profile, Grade.A_PLUS), preserve_grade_risk=False)
    assert shrunk.quantity < Decimal("250")
    above = profile_with(
        PolicyKind.DEMO,
        risk_percent_by_grade=_grades(PolicyKind.DEMO, **{Grade.A: Decimal("1.5")}),
        daily_loss_limit_percent=Decimal("6"),
        max_aggregate_open_risk_percent=Decimal("6"),
        max_notional_per_trade_percent=Decimal("80"),
        max_aggregate_notional_percent=Decimal("90"),
        paper_demo_data_collection=False,
    )
    with pytest.raises(RiskPolicyRejected) as promotion:
        size_trade(above, _input(above, Grade.A))
    assert promotion.value.code == "grade_above_one_percent_requires_promotion"


def test_risk_engine_five_percent_requires_the_paper_or_demo_channel() -> None:
    engine = RiskEngine()
    request = RiskCheckRequest(
        symbol="BTCUSDT",
        direction=TradeDirection.LONG,
        entry_price=Decimal("100"),
        position_size=Decimal("1"),
        leverage=Decimal("1"),
        account_equity=Decimal("10000"),
        stop_loss=Decimal("98"),
        volume_24h=Decimal("50000000"),
        risk_percent=Decimal("5"),
    )

    def verdict(*, paper: bool, risk: Decimal):
        return engine.evaluate(
            request.model_copy(update={"risk_percent": risk}),
            context=RiskEvaluationContext(
                is_weekend=False,
                autonomous_sizing=True,
                paper_or_demo_channel=paper,
            ),
        )

    blocked = verdict(paper=False, risk=Decimal("5"))
    assert any(rule.rule_id is RiskRuleId.RISK_PERCENT for rule in blocked.triggered_rules)
    allowed = verdict(paper=True, risk=Decimal("5"))
    assert not any(rule.rule_id is RiskRuleId.RISK_PERCENT for rule in allowed.triggered_rules)
    over = verdict(paper=True, risk=Decimal("5.1"))
    assert any(rule.rule_id is RiskRuleId.RISK_PERCENT for rule in over.triggered_rules)


def test_demo_leverage_and_margin_blocks_do_not_describe_a_write() -> None:
    assert demo_leverage_block(Decimal("1"), Decimal("10")) == "demo_account_leverage_mismatch"
    assert demo_leverage_block(Decimal("10"), Decimal("10")) is None
    assert demo_margin_mode_block("cross") == "demo_margin_mode_not_isolated"
    assert demo_margin_mode_block("isolated") is None
    posts: list[str] = []
    account_leverage = {"value": "2"}

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST":
            posts.append(path)
            return httpx.Response(500, json={"code": "1", "msg": "no writes"})
        if path.endswith("query-apikey"):
            data: object = {"readOnly": 0}
        elif path.endswith("position-mode"):
            data = {"positionMode": "net_mode"}
        elif path.endswith("leverage-info"):
            data = {
                "instId": "BTC-USDT",
                "marginMode": "cross",
                "leverage": account_leverage["value"],
            }
        elif path.endswith("instruments"):
            data = [
                {
                    "instId": "BTC-USDT",
                    "baseCurrency": "BTC",
                    "quoteCurrency": "USDT",
                    "contractType": "linear",
                    "contractValue": "0.001",
                    "tickSize": "0.1",
                    "lotSize": "1",
                    "minSize": "1",
                    "maxMarketSize": "1000000",
                    "state": "live",
                    "instType": "SWAP",
                }
            ]
        elif path.endswith("books"):
            data = [
                {
                    "instId": "BTC-USDT",
                    "asks": [["100.1", "1000000"]],
                    "bids": [["100", "1000000"]],
                    "ts": _TS,
                }
            ]
        elif path.endswith(("positions", "orders-pending", "orders-tpsl-pending")):
            data = []
        elif path.endswith("balance"):
            data = [{"currency": "USDT", "balance": "10000", "available": "10000"}]
        else:
            raise AssertionError(path)
        return httpx.Response(200, json={"code": "0", "data": data})

    client = BloFinClient(
        base_url="https://demo-trading-openapi.blofin.com",
        api_key="simulated-key",
        api_secret="simulated-secret",
        api_passphrase="simulated-pass",
        transport=httpx.MockTransport(handle),
    )
    provider = GovernedBloFinDemoProvider(client, clock=lambda: EVALUATED_AT)
    with pytest.raises(ValueError, match="Demo account must already use leverage 1"):
        provider.snapshot(
            symbol="BTCUSDT",
            now=EVALUATED_AT,
            side=EntrySide.BUY,
            required_leverage=Decimal("1"),
        )
    account_leverage["value"] = "1"
    with pytest.raises(ValueError, match="does not match required leverage"):
        provider.snapshot(
            symbol="BTCUSDT",
            now=EVALUATED_AT,
            side=EntrySide.BUY,
            required_leverage=Decimal("10"),
        )
    snapshot = provider.snapshot(
        symbol="BTCUSDT",
        now=EVALUATED_AT,
        side=EntrySide.BUY,
        required_leverage=None,
    )
    assert snapshot.account_leverage == Decimal("1")
    assert snapshot.margin_mode == "cross"
    assert posts == []
