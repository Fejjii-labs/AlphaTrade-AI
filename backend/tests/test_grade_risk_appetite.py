"""Grade sizing, ceilings, leverage, and the demo leverage-mismatch block."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.governed_blofin import (
    GovernedBloFinDemoProvider,
    demo_order_margin_mode,
)
from app.schemas.common import RiskRuleId, TradeDirection
from app.schemas.risk import RiskCheckRequest
from app.schemas.trade_plan import EntrySide
from app.services.automated_paper_loop import _quantity
from app.services.execution_claim import (
    ReservationIntent,
    conservative_reservation,
    evaluate_claim_predicate,
)
from app.services.risk.engine import RiskEngine
from app.services.risk.rules import RiskEvaluationContext
from app.services.risk_policy.adjusters import AdjusterInputs
from app.services.risk_policy.breakers import BreakerVerdict
from app.services.risk_policy.claim_caps import accounting_view, assign_profile_claim_caps
from app.services.risk_policy.decision import decide_trade
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.grader import GradeDecision
from app.services.risk_policy.ledger import caps_for_ledger
from app.services.risk_policy.plan_sizing import (
    PAPER_DEMO_MAINTENANCE_TIERS,
    ActiveProfile,
    demo_leverage_block,
    demo_margin_mode_block,
    open_risk_from_stops,
    profile_unit_cost,
    remaining_daily_budget_percent,
    reserved_maximum_loss,
    size_for_profile,
    stop_out_cash,
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
    assert classify_change(placeholder_profile(PolicyKind.DEMO), demo) is ChangeClass.LOOSENING
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


def test_promotion_bypass_is_limited_to_server_paper_and_demo_kinds() -> None:
    from dataclasses import replace

    from app.services.risk_policy.calibration import assert_promotion_allows, promote
    from app.services.risk_policy.ceilings import PAPER_DEMO_KINDS

    for kind in (PolicyKind.INTERNAL_PAPER, PolicyKind.DEMO):
        profile = sofien_aggressive_profile(kind)
        assert profile.kind.value in PAPER_DEMO_KINDS
        assert profile.paper_demo_data_collection is True
        assert profile.autonomous_internal_paper_enabled is False
        assert profile.autonomous_blofin_demo_enabled is False
        assert_promotion_allows(profile, None)
        sized = size_trade(profile, _input(profile, Grade.A_PLUS))
        assert sized.grade_risk_percent == Decimal("5")
        assert sized.profile_risk_percent == Decimal("5")
        plain = profile_with(
            kind,
            risk_percent_by_grade=_grades(kind, **{Grade.A_PLUS: Decimal("5")}),
            paper_demo_data_collection=False,
        )
        with pytest.raises(RiskPolicyRejected) as missing_flag:
            assert_promotion_allows(plain, None)
        assert missing_flag.value.code == "grade_above_one_percent_requires_promotion"
        held = decide_trade(
            profile=profile,
            profile_version_id=uuid4(),
            grade=GradeDecision(
                Grade.A_PLUS, "deterministic-grade/v1", Decimal("1"), Decimal("1"), None
            ),
            adjusters=AdjusterInputs(
                volatility_regime="normal", trend_aligned=True, momentum_aligned=True
            ),
            sizing=_input(profile, Grade.A_PLUS),
            breaker=BreakerVerdict(
                daily_loss_active=True,
                drawdown_active=False,
                consecutive_loss_active=False,
                trip_kill_switch=False,
                paused_until=None,
                equity_peak=Decimal("10000"),
                refuse_new_trades=True,
                human_reset_required=False,
            ),
            tiers=PAPER_DEMO_MAINTENANCE_TIERS,
            preserve_grade_risk=True,
        )
        assert held.accepted is False
        assert held.reason == "breaker_active"
        with pytest.raises(RiskPolicyRejected) as budget:
            size_trade(
                profile,
                _input(
                    profile,
                    Grade.A_PLUS,
                    remaining_daily_budget_percent=Decimal("0"),
                    open_risk_percent=Decimal("6"),
                ),
            )
        assert budget.value.code == "budget_exhausted"

    for kind in (PolicyKind.CONSERVATIVE, PolicyKind.STANDARD, PolicyKind.AGGRESSIVE):
        assert kind.value not in PAPER_DEMO_KINDS
        with pytest.raises(RiskPolicyRejected) as flag:
            profile_with(kind, paper_demo_data_collection=True)
        assert flag.value.code == "data_collection_not_paper_or_demo"
        raised = profile_with(
            kind,
            risk_percent_by_grade=_grades(kind, **{Grade.A_PLUS: Decimal("2")}),
        )
        with pytest.raises(RiskPolicyRejected) as refused:
            assert_promotion_allows(raised, None)
        assert refused.value.code == "grade_above_one_percent_requires_promotion"
        with pytest.raises(RiskPolicyRejected) as sized_other:
            size_trade(raised, _input(raised, Grade.A_PLUS))
        assert sized_other.value.code == "grade_above_one_percent_requires_promotion"
        forged = replace(
            sofien_aggressive_profile(PolicyKind.INTERNAL_PAPER),
            kind=kind,
        )
        with pytest.raises(RiskPolicyRejected) as forged_gate:
            assert_promotion_allows(forged, None)
        assert forged_gate.value.code == "grade_above_one_percent_requires_promotion"

    with pytest.raises(RiskPolicyRejected) as manual:
        promote()
    assert manual.value.code == "promotion_is_manual"


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
    unit = profile_unit_cost(Decimal("100"), Decimal("98"), include_book_buffer=False)
    assert recorded.quantity < Decimal("250")
    assert recorded.quantity * (Decimal("2") + unit) <= Decimal("500")
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
    wired = size_for_profile(
        active=ActiveProfile(profile=profile, version_id=uuid4()),
        grade=GradeDecision(
            Grade.A_PLUS, "deterministic-grade/v1", Decimal("1"), Decimal("1"), None
        ),
        equity=Decimal("10000"),
        entry=Decimal("100"),
        stop=Decimal("90"),
        target=Decimal("120"),
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
    assert wired == "liquidation_inside_buffer"


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
    leverage_modes: list[str] = []
    account_leverage = {"value": "2"}
    account_mode = {"value": "cross"}
    force_cross = {"on": False}
    omit_mode = {"on": False}
    omit_leverage_mode = {"on": False}
    opposite_leverage_mode = {"on": False}

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST":
            posts.append(path)
            return httpx.Response(500, json={"code": "1", "msg": "no writes"})
        if path.endswith("query-apikey"):
            data: object = {"readOnly": 0}
        elif path.endswith("position-mode"):
            data = {"positionMode": "net_mode"}
        elif path.endswith("/margin-mode"):
            if omit_mode["on"]:
                data = {}
            elif force_cross["on"]:
                data = {"marginMode": "cross"}
            else:
                data = {"marginMode": account_mode["value"]}
        elif path.endswith("position-tiers"):
            data = [
                {
                    "instId": "BTC-USDT",
                    "minSize": "0",
                    "maintenanceMarginRate": "0.004",
                }
            ]
        elif path.endswith("leverage-info"):
            requested = request.url.params.get("marginMode") or ""
            leverage_modes.append(requested)
            data = {
                "instId": "BTC-USDT",
                "leverage": account_leverage["value"],
            }
            if omit_leverage_mode["on"]:
                pass
            elif opposite_leverage_mode["on"]:
                data["marginMode"] = "cross" if requested == "isolated" else "isolated"
            else:
                data["marginMode"] = requested
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
    assert snapshot.maintenance_tiers
    assert leverage_modes[-1] == "cross"
    account_mode["value"] = "isolated"
    isolated = provider.snapshot(
        symbol="BTCUSDT",
        now=EVALUATED_AT,
        side=EntrySide.BUY,
        required_leverage=None,
        margin_mode="isolated",
    )
    assert isolated.margin_mode == "isolated"
    assert isolated.account_leverage == Decimal("1")
    assert leverage_modes[-1] == "isolated"
    omit_mode["on"] = True
    with pytest.raises(ValueError, match="margin-mode omitted"):
        provider.snapshot(
            symbol="BTCUSDT",
            now=EVALUATED_AT,
            side=EntrySide.BUY,
            required_leverage=None,
            margin_mode="isolated",
        )
    omit_mode["on"] = False
    force_cross["on"] = True
    with pytest.raises(ValueError, match="not isolated"):
        provider.snapshot(
            symbol="BTCUSDT",
            now=EVALUATED_AT,
            side=EntrySide.BUY,
            required_leverage=None,
            margin_mode="isolated",
        )
    account_mode["value"] = "isolated"
    force_cross["on"] = False
    with pytest.raises(ValueError, match="not cross"):
        provider.snapshot(
            symbol="BTCUSDT",
            now=EVALUATED_AT,
            side=EntrySide.BUY,
            required_leverage=None,
            margin_mode="cross",
        )
    account_mode["value"] = "isolated"
    account_leverage["value"] = "10"
    omit_leverage_mode["on"] = True
    with pytest.raises(ValueError, match="leverage-info omitted margin mode"):
        provider.snapshot(
            symbol="BTCUSDT",
            now=EVALUATED_AT,
            side=EntrySide.BUY,
            required_leverage=Decimal("10"),
            margin_mode="isolated",
        )
    omit_leverage_mode["on"] = False
    opposite_leverage_mode["on"] = True
    with pytest.raises(ValueError, match="does not match the account"):
        provider.snapshot(
            symbol="BTCUSDT",
            now=EVALUATED_AT,
            side=EntrySide.BUY,
            required_leverage=Decimal("10"),
            margin_mode="isolated",
        )
    assert posts == []
    assert demo_order_margin_mode("verified-demo-read-leverage") == "isolated"
    assert demo_order_margin_mode("verified-demo-existing-1x") == "cross"
    assert demo_order_margin_mode("paper-internal-cash") == "cross"


def test_open_book_does_not_invent_daily_budget_or_stop_risk() -> None:
    assert (
        remaining_daily_budget_percent(
            daily_loss_limit_percent=Decimal("6"),
            realized_pnl=None,
            equity=Decimal("10000"),
        )
        == "daily_budget_uncomputed"
    )
    # A $500 realized loss on $10,000 uses 5 points of a 6% daily limit.
    assert remaining_daily_budget_percent(
        daily_loss_limit_percent=Decimal("6"),
        realized_pnl=Decimal("-500"),
        equity=Decimal("10000"),
    ) == Decimal("1")
    assert (
        open_risk_from_stops(
            equity=Decimal("10000"),
            positions=[(Decimal("1"), Decimal("100"), None)],
            open_notional=Decimal("100"),
        )
        == "open_risk_uncomputed"
    )
    flat = open_risk_from_stops(
        equity=Decimal("10000"),
        positions=[],
        open_notional=Decimal("0"),
    )
    assert flat == (Decimal("0"), 0)
    profile = sofien_aggressive_profile(PolicyKind.INTERNAL_PAPER)
    active = ActiveProfile(profile=profile, version_id=uuid4())
    grade = GradeDecision(Grade.A_PLUS, "deterministic-grade/v1", Decimal("1"), Decimal("1"), None)
    sized = size_for_profile(
        active=active,
        grade=grade,
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
        remaining_daily_budget_percent=Decimal("1"),
        breaker_refusing=False,
    )
    assert not isinstance(sized, str)
    assert sized.risk_percent <= Decimal("1")
    unit = profile_unit_cost(Decimal("100"), Decimal("98"), include_book_buffer=False)
    assert sized.quantity < Decimal("50")
    assert sized.quantity * (Decimal("2") + unit) <= Decimal("100")


def test_canonical_open_lifecycle_is_priced_at_its_stop(monkeypatch) -> None:
    from datetime import UTC, datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base
    from app.db.models import JournalTrade, Organization
    from app.schemas.common import JournalTradeSource, JournalTradeStatus, TradeDirection
    from app.services.automated_paper_loop import _profile_open_book
    from app.services.risk_policy.plan_sizing import canonical_open_stop_positions, stop_out_cash
    from app.workers.watcher_paper_targets import PaperScanTarget

    warnings: list[str] = []

    def _record(message: str, *args: object) -> None:
        warnings.append(message % args if args else message)

    monkeypatch.setattr("app.services.risk_policy.plan_sizing.logger.warning", _record)
    missing = canonical_open_stop_positions([(Decimal("1"), Decimal("100"), None)])
    assert missing == "open_risk_uncomputed"
    assert any(
        "canonical open lifecycle row is missing size, entry, or stop" in item for item in warnings
    )
    excess = open_risk_from_stops(
        equity=Decimal("10000"),
        positions=[],
        open_notional=Decimal("100"),
    )
    assert excess == "open_risk_uncomputed"
    assert any("differs from the notional explained by stops" in item for item in warnings)
    within = open_risk_from_stops(
        equity=Decimal("10000"),
        positions=[(Decimal("1"), Decimal("100"), Decimal("90"))],
        open_notional=Decimal("100.000000001"),
    )
    assert isinstance(within, tuple)
    beyond = open_risk_from_stops(
        equity=Decimal("10000"),
        positions=[(Decimal("1"), Decimal("100"), Decimal("90"))],
        open_notional=Decimal("100.00000002"),
    )
    assert beyond == "open_risk_uncomputed"
    short = open_risk_from_stops(
        equity=Decimal("10000"),
        positions=[(Decimal("1"), Decimal("100"), Decimal("90"))],
        open_notional=Decimal("50"),
    )
    assert short == "open_risk_uncomputed"

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    org = uuid4()
    user = uuid4()
    session.add(Organization(id=org, name="Canonical open risk"))
    session.add(
        JournalTrade(
            organization_id=org,
            user_id=user,
            source=JournalTradeSource.PAPER_EXECUTION,
            status=JournalTradeStatus.OPEN,
            symbol="BTCUSDT",
            timeframe="15m",
            direction=TradeDirection.LONG,
            size=Decimal("1"),
            entry_price=Decimal("100"),
            planned_stop_price=Decimal("90"),
            execution_lifecycle_id=uuid4(),
        )
    )
    session.commit()

    session.add(
        JournalTrade(
            organization_id=org,
            user_id=user,
            source=JournalTradeSource.MANUAL_DEMO_TEST,
            status=JournalTradeStatus.CLOSED,
            symbol="BTCUSDT",
            timeframe="15m",
            direction=TradeDirection.LONG,
            size=Decimal("5"),
            entry_price=Decimal("100"),
            planned_stop_price=Decimal("90"),
            execution_lifecycle_id=uuid4(),
        )
    )
    session.commit()

    class _Snapshot:
        realized_pnl = Decimal("0")
        account_equity = Decimal("10000")
        # A closed reconciled row must not be part of this comparison.
        open_exposure_notional = Decimal("600")

    class _Accounting:
        def __init__(self, _session: object, _settings: object, clock: object) -> None:
            return None

        def sync_from_portfolio(self, **_kwargs: object) -> _Snapshot:
            return _Snapshot()

    monkeypatch.setattr("app.services.automated_paper_loop.DailyRiskAccounting", _Accounting)
    target = PaperScanTarget(
        organization_id=org,
        user_id=user,
        strategy_id=uuid4(),
        strategy_version_id=uuid4(),
        compiled_setup_definition_id=uuid4(),
        compiled_content_hash="a" * 64,
        fusion_policy_version="test",
        symbol="BTCUSDT",
    )
    book = _profile_open_book(
        session,
        target,
        now=datetime(2026, 10, 19, tzinfo=UTC),
        daily_loss_limit_percent=Decimal("6"),
    )
    assert not isinstance(book, str)
    cash = stop_out_cash(Decimal("1"), Decimal("100"), Decimal("90"), include_book_buffer=False)
    assert book.open_risk_percent == cash / Decimal("10000") * Decimal("100")
    assert book.open_positions == 1
    assert book.open_notional == Decimal("100")
    engine.dispose()


def test_profile_sizer_uses_the_execution_channel_for_the_five_percent_branch() -> None:
    from app.services.risk_policy.ceilings import risk_ceiling_for

    def sized(kind: PolicyKind, channel: str):
        profile = sofien_aggressive_profile(kind)
        return size_for_profile(
            active=ActiveProfile(profile=profile, version_id=uuid4()),
            grade=GradeDecision(
                Grade.A_PLUS, "deterministic-grade/v1", Decimal("1"), Decimal("1"), None
            ),
            equity=Decimal("10000"),
            entry=Decimal("100"),
            stop=Decimal("90"),
            target=Decimal("120"),
            direction="long",
            atr=Decimal("2"),
            lot_step=Decimal("0.00000001"),
            min_quantity=Decimal("0.00000001"),
            open_risk_percent=Decimal("0"),
            open_notional=Decimal("0"),
            open_positions=0,
            remaining_daily_budget_percent=Decimal("6"),
            breaker_refusing=False,
            leverage=Decimal("1"),
            include_book_buffer=channel == "demo",
            execution_channel=channel,
        )

    paper = sized(PolicyKind.INTERNAL_PAPER, "internal_paper")
    demo = sized(PolicyKind.DEMO, "demo")
    assert not isinstance(paper, str)
    assert not isinstance(demo, str)
    assert Decimal("2") < paper.risk_percent <= Decimal("5")
    assert Decimal("2") < demo.risk_percent <= Decimal("5")
    assert sized(PolicyKind.INTERNAL_PAPER, "live") == "risk_percent_exceeds_channel_ceiling"
    assert risk_ceiling_for(PolicyKind.AGGRESSIVE) == Decimal("2")
    assert risk_ceiling_for("live") == Decimal("2")


def test_claim_notional_ceiling_matches_the_profile() -> None:
    profile = sofien_aggressive_profile(PolicyKind.INTERNAL_PAPER)
    equity = Decimal("10000")
    caps = caps_for_ledger(profile, equity)
    assert caps.max_symbol_notional == Decimal("100000.00000000")
    assert caps.max_notional == Decimal("200000.00000000")
    assert caps.max_daily_loss == Decimal("600.00000000")
    intent = ReservationIntent(
        pending_notional=Decimal("25000"),
        daily_loss=Decimal("500"),
        trade_slots=1,
        instrument="BTCUSDT",
        exposure_unit="USDT",
        remaining_quantity=Decimal("250"),
        quantity_unit="BASE",
        contract_multiplier=Decimal("1"),
        contract_type="LINEAR",
    )

    def accounting(**overrides: object) -> SimpleNamespace:
        values: dict[str, object] = {
            "max_notional": equity,
            "reserved_notional": Decimal("0"),
            "actual_notional": Decimal("0"),
            "max_daily_loss": equity,
            "reserved_daily_loss": Decimal("0"),
            "actual_daily_loss": Decimal("0"),
            "daily_locked": False,
            "max_trade_slots": 10,
            "reserved_trade_slots": 0,
            "actual_trade_count": 0,
            "max_symbol_notional": equity,
            "symbol_reserved": {},
            "symbol_actual": {},
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    cash = evaluate_claim_predicate(
        blocking_epoch=False,
        kill_active=False,
        accounting=accounting(),  # type: ignore[arg-type]
        intent=intent,
    )
    assert cash == "insufficient_total_exposure"
    agreed = evaluate_claim_predicate(
        blocking_epoch=False,
        kill_active=False,
        accounting=accounting(
            max_notional=caps.max_notional,
            max_symbol_notional=caps.max_symbol_notional,
            max_daily_loss=caps.max_daily_loss,
        ),  # type: ignore[arg-type]
        intent=intent,
    )
    assert agreed is None
    second = ReservationIntent(
        pending_notional=Decimal("25000"),
        daily_loss=Decimal("500"),
        trade_slots=1,
        instrument="BTCUSDT",
        exposure_unit="USDT",
        remaining_quantity=Decimal("250"),
        quantity_unit="BASE",
        contract_multiplier=Decimal("1"),
        contract_type="LINEAR",
    )
    blocked = evaluate_claim_predicate(
        blocking_epoch=False,
        kill_active=False,
        accounting=accounting(
            max_notional=caps.max_notional,
            max_symbol_notional=caps.max_symbol_notional,
            max_daily_loss=caps.max_daily_loss,
            reserved_daily_loss=Decimal("500"),
        ),  # type: ignore[arg-type]
        intent=second,
    )
    assert blocked == "insufficient_daily_loss_allocation"


def test_flat_demo_book_keeps_grade_risk_past_the_fee_buffer() -> None:
    profile = sofien_aggressive_profile(PolicyKind.DEMO)
    active = ActiveProfile(profile=profile, version_id=uuid4())
    grade = GradeDecision(Grade.A_PLUS, "deterministic-grade/v1", Decimal("1"), Decimal("1"), None)
    sized = size_for_profile(
        active=active,
        grade=grade,
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
        remaining_daily_budget_percent=profile.daily_loss_limit_percent,
        breaker_refusing=False,
        include_book_buffer=True,
    )
    assert not isinstance(sized, str)
    assert sized.quantity < Decimal("250")
    unit = profile_unit_cost(Decimal("100"), Decimal("98"), include_book_buffer=True)
    inclusive = sized.quantity * (Decimal("2") + unit)
    assert inclusive <= sized.risk_cash_ceiling
    assert inclusive <= Decimal("500")


def test_half_percent_stop_keeps_round_trip_fees_inside_the_grade() -> None:
    # Unadjusted A+ notional at a 0.5% stop is 10x equity. Two taker legs of
    # 0.06% are 1.2% of equity before the sizer shrinks the quantity.
    profile = sofien_aggressive_profile(PolicyKind.INTERNAL_PAPER)
    unadjusted = Decimal("10000") * Decimal("5") / Decimal("0.5")
    assert unadjusted * (Decimal("0.0006") * 2) / Decimal("10000") == Decimal("0.012")
    grade = GradeDecision(Grade.A_PLUS, "deterministic-grade/v1", Decimal("1"), Decimal("1"), None)
    sized = size_for_profile(
        active=ActiveProfile(profile=profile, version_id=uuid4()),
        grade=grade,
        equity=Decimal("10000"),
        entry=Decimal("100"),
        stop=Decimal("99.5"),
        target=Decimal("101"),
        direction="long",
        atr=Decimal("0.4"),
        lot_step=Decimal("0.00000001"),
        min_quantity=Decimal("0.00000001"),
        open_risk_percent=Decimal("0"),
        open_notional=Decimal("0"),
        open_positions=0,
        remaining_daily_budget_percent=Decimal("6"),
        breaker_refusing=False,
    )
    # The 0.0001 per 8h funding floor puts a 0.5% stop over the 20% cost share.
    assert sized == "costs_exceed_r"
    wider = size_for_profile(
        active=ActiveProfile(profile=profile, version_id=uuid4()),
        grade=grade,
        equity=Decimal("10000"),
        entry=Decimal("100"),
        stop=Decimal("99"),
        target=Decimal("102"),
        direction="long",
        atr=Decimal("0.4"),
        lot_step=Decimal("0.00000001"),
        min_quantity=Decimal("0.00000001"),
        open_risk_percent=Decimal("0"),
        open_notional=Decimal("0"),
        open_positions=0,
        remaining_daily_budget_percent=Decimal("6"),
        breaker_refusing=False,
    )
    assert not isinstance(wider, str), wider
    unit = profile_unit_cost(Decimal("100"), Decimal("99"), include_book_buffer=False)
    assert wider.quantity * (Decimal("1") + unit) <= Decimal("500")
    demo = size_for_profile(
        active=ActiveProfile(
            profile=sofien_aggressive_profile(PolicyKind.DEMO), version_id=uuid4()
        ),
        grade=grade,
        equity=Decimal("10000"),
        entry=Decimal("100"),
        stop=Decimal("99.5"),
        target=Decimal("101"),
        direction="long",
        atr=Decimal("0.4"),
        lot_step=Decimal("0.00000001"),
        min_quantity=Decimal("0.00000001"),
        open_risk_percent=Decimal("0"),
        open_notional=Decimal("0"),
        open_positions=0,
        remaining_daily_budget_percent=Decimal("6"),
        breaker_refusing=False,
        include_book_buffer=True,
    )
    assert demo == "costs_exceed_r"


def test_funding_uses_a_verified_live_rate_or_the_eight_hour_floor() -> None:
    from app.services.risk_policy.plan_sizing import (
        PAPER_DEMO_FUNDING_RATE_PER_8H,
        paper_demo_funding,
    )

    floor, one = paper_demo_funding(None)
    assert floor == PAPER_DEMO_FUNDING_RATE_PER_8H == Decimal("0.0001")
    assert one == Decimal("1")
    failed, failed_periods = paper_demo_funding(Decimal("0.0001"), live_verified=False)
    assert failed == Decimal("0.0001") and failed_periods == Decimal("1")
    tiny, _periods = paper_demo_funding(Decimal("0.00001"), live_verified=True)
    assert tiny == Decimal("0.0001")
    live, live_periods = paper_demo_funding(
        Decimal("-0.0003"),
        live_verified=True,
        expected_hold_hours=Decimal("24"),
    )
    assert live == Decimal("0.0003")
    assert live_periods == Decimal("3")
    short_hold, short_periods = paper_demo_funding(None, expected_hold_hours=Decimal("4"))
    assert short_hold == Decimal("0.0001") and short_periods == Decimal("1")
    profile = sofien_aggressive_profile(PolicyKind.INTERNAL_PAPER)
    grade = GradeDecision(Grade.B, "deterministic-grade/v1", Decimal("1"), Decimal("1"), None)
    common = {
        "active": ActiveProfile(profile=profile, version_id=uuid4()),
        "grade": grade,
        "equity": Decimal("10000"),
        "entry": Decimal("100"),
        "stop": Decimal("98"),
        "target": Decimal("104"),
        "direction": "long",
        "atr": Decimal("1"),
        "lot_step": Decimal("0.00000001"),
        "min_quantity": Decimal("0.00000001"),
        "open_risk_percent": Decimal("0"),
        "open_notional": Decimal("0"),
        "open_positions": 0,
        "remaining_daily_budget_percent": Decimal("6"),
        "breaker_refusing": False,
    }
    at_floor = size_for_profile(**common)
    at_live = size_for_profile(**common, funding_rate=Decimal("0.001"), funding_rate_verified=True)
    assert not isinstance(at_floor, str) and not isinstance(at_live, str)
    assert at_live.funding_rate == Decimal("0.001")
    assert at_live.quantity < at_floor.quantity
    held = size_for_profile(**common, expected_hold_hours=Decimal("24"))
    assert not isinstance(held, str)
    assert held.funding_periods == Decimal("3")
    assert held.quantity < at_floor.quantity


def test_blocked_profile_claim_does_not_widen_the_ledger() -> None:
    profile = sofien_aggressive_profile(PolicyKind.INTERNAL_PAPER)
    caps = caps_for_ledger(profile, Decimal("10000"))
    stored = SimpleNamespace(
        daily_locked=False,
        max_notional=Decimal("10000"),
        reserved_notional=Decimal("0"),
        actual_notional=Decimal("0"),
        max_daily_loss=Decimal("10000"),
        reserved_daily_loss=Decimal("0"),
        actual_daily_loss=Decimal("0"),
        max_trade_slots=10,
        reserved_trade_slots=0,
        actual_trade_count=0,
        max_symbol_notional=Decimal("10000"),
        symbol_reserved={},
        symbol_actual={},
    )
    viewed = accounting_view(stored, caps)
    intent = ReservationIntent(
        pending_notional=Decimal("25000"),
        daily_loss=Decimal("500"),
        trade_slots=1,
        instrument="BTCUSDT",
        exposure_unit="USDT",
        remaining_quantity=Decimal("250"),
        quantity_unit="BASE",
        contract_multiplier=Decimal("1"),
        contract_type="LINEAR",
    )
    assert (
        evaluate_claim_predicate(
            blocking_epoch=True,
            kill_active=False,
            accounting=viewed,  # type: ignore[arg-type]
            intent=intent,
        )
        == "safety_epoch_blocking"
    )
    assert stored.max_notional == Decimal("10000")
    assert stored.max_symbol_notional == Decimal("10000")
    assert stored.max_daily_loss == Decimal("10000")
    assert viewed.max_notional == caps.max_notional
    with pytest.raises(RiskPolicyRejected) as written:
        assign_profile_claim_caps(stored, caps)
    assert written.value.code == "profile_caps_are_not_written_to_the_shared_ledger"
    assert stored.max_notional == Decimal("10000")
    assert stored.max_daily_loss == Decimal("10000")


def test_open_risk_and_reserved_loss_include_the_sizer_costs() -> None:
    from datetime import timedelta

    from app.schemas.trade_plan import MarginMode
    from app.services.internal_paper_plan_terms import build_internal_paper_terms
    from tests.support.phase5_market import EVALUATED_AT
    from tests.support.phase6_fusion import ACCOUNT_ID
    from tests.support.phase7_trade_plan import make_world

    size = Decimal("10")
    entry = Decimal("100")
    stop = Decimal("98")
    equity = Decimal("10000")
    price_only = size * (entry - stop)
    paper_cash = stop_out_cash(size, entry, stop, include_book_buffer=False)
    demo_cash = stop_out_cash(size, entry, stop, include_book_buffer=True)
    assert paper_cash > price_only
    assert demo_cash > paper_cash
    assert demo_cash - paper_cash == size * entry * Decimal("0.002")
    paper = open_risk_from_stops(
        equity=equity,
        positions=[(size, entry, stop)],
        open_notional=size * entry,
    )
    demo = open_risk_from_stops(
        equity=equity,
        positions=[(size, entry, stop)],
        open_notional=size * entry,
        include_book_buffer=True,
    )
    assert paper == (paper_cash / equity * Decimal("100"), 1)
    assert demo == (demo_cash / equity * Decimal("100"), 1)
    # At the 0.20 cost-share ceiling, 6% of price risk is 7.5% once costs are in.
    assert Decimal("6") / (Decimal("1") - Decimal("0.20")) == Decimal("7.5")
    profile_loss = reserved_maximum_loss(
        size=size,
        adverse_entry=entry,
        stop=stop,
        worse_price=Decimal("101"),
        profile_costs=True,
    )
    plain_loss = reserved_maximum_loss(
        size=size,
        adverse_entry=entry,
        stop=stop,
        worse_price=Decimal("101"),
        profile_costs=False,
    )
    assert profile_loss == demo_cash
    assert plain_loss == size * ((entry - stop) + Decimal("101") * Decimal("0.002"))
    world = make_world()
    common = {
        "candidate": world.candidate,
        "account_id": ACCOUNT_ID,
        "entry": entry,
        "stop": stop,
        "target": Decimal("104"),
        "quantity": size,
        "observed": EVALUATED_AT,
        "now": EVALUATED_AT,
        "valid_until": EVALUATED_AT + timedelta(minutes=5),
        "freshness": 30,
        "symbol": "BTCUSDT",
        "tick_size": Decimal("0.1"),
        "target_formula": "paper-internal-1r",
        "presentation": {"channel": "API"},
    }
    isolated = build_internal_paper_terms(**common, margin_mode=MarginMode.ISOLATED)
    cross = build_internal_paper_terms(**common, margin_mode=MarginMode.CROSS)
    assert isolated.risk_and_exits.maximum_loss.value == paper_cash
    assert isolated.risk_and_exits.risk_budget.value == price_only
    assert isolated.risk_and_exits.fee_allowance.value == 0
    assert isolated.risk_and_exits.funding_allowance.value == 0
    assert isolated.risk_and_exits.slippage_allowance.value == 0
    assert cross.risk_and_exits.maximum_loss.value == price_only
    assert conservative_reservation(isolated).daily_loss == paper_cash
    assert conservative_reservation(cross).daily_loss == price_only


def test_demo_profile_does_not_stop_on_the_unprofiled_minimum(monkeypatch) -> None:
    from dataclasses import replace

    from app.services import governed_blofin_demo
    from app.services.governed_blofin_demo import GovernedBloFinDemoLoop
    from app.services.risk_policy.liquidation import MaintenanceTier
    from tests.support.phase7_trade_plan import make_world, plan_terms
    from tests.test_demo_preflight_diagnostics import provider
    from tests.test_governed_blofin_demo import Venue

    entry = Decimal("3000000")
    stop = Decimal("2880000")
    distance = entry - stop
    equity = Decimal("10000")
    assert _quantity(equity=equity, entry=entry, distance=distance) is None
    grade = GradeDecision(Grade.A_PLUS, "deterministic-grade/v1", Decimal("1"), Decimal("1"), None)
    profile = sofien_aggressive_profile(PolicyKind.DEMO)
    active = ActiveProfile(profile=profile, version_id=uuid4())
    world = make_world()
    drafted = plan_terms(world.candidate)
    risk = drafted.risk_and_exits.model_copy(
        update={
            "stop": drafted.risk_and_exits.stop.model_copy(update={"value": stop}),
            "targets": (
                drafted.risk_and_exits.targets[0].model_copy(
                    update={
                        "quantity_fraction": Decimal("1"),
                        "price": drafted.risk_and_exits.targets[0].price.model_copy(
                            update={"value": entry + distance}
                        ),
                    }
                ),
            ),
            "runner": drafted.risk_and_exits.runner.model_copy(
                update={
                    "enabled": False,
                    "activation_target_order": None,
                    "remaining_quantity_fraction": Decimal("0"),
                }
            ),
        }
    )
    basis = drafted.basis_policy.model_copy(
        update={
            "evidence_price": drafted.basis_policy.evidence_price.model_copy(
                update={"value": entry}
            ),
            "execution_price": drafted.basis_policy.execution_price.model_copy(
                update={"value": entry}
            ),
        }
    )
    terms = drafted.model_copy(
        update={"side": EntrySide.BUY, "risk_and_exits": risk, "basis_policy": basis}
    )
    venue = Venue(entry - Decimal("0.1"))
    snapshot = provider(venue.handle).snapshot(
        symbol="BTCUSDT", now=EVALUATED_AT, side=EntrySide.BUY, required_leverage=None
    )
    direct = size_for_profile(
        active=active,
        grade=grade,
        equity=equity,
        entry=entry,
        stop=stop,
        target=entry + distance,
        direction="long",
        atr=Decimal("1"),
        lot_step=snapshot.lot * snapshot.multiplier,
        min_quantity=snapshot.minimum * snapshot.multiplier,
        open_risk_percent=Decimal("0"),
        open_notional=Decimal("0"),
        open_positions=0,
        remaining_daily_budget_percent=Decimal("6"),
        breaker_refusing=False,
        include_book_buffer=True,
        maintenance_tiers=tuple(
            MaintenanceTier(notional_floor=floor, maintenance_rate=rate)
            for floor, rate in snapshot.maintenance_tiers
        ),
    )
    assert not isinstance(direct, str)
    assert direct.quantity * entry >= Decimal("5")
    accepted: list[bool] = []

    def paper_terms(**kwargs: object) -> object:
        accepted.append(bool(kwargs.get("accept_unprofiled_minimum")))
        if not kwargs.get("accept_unprofiled_minimum"):
            return "size_below_minimum"
        return terms

    monkeypatch.setattr(governed_blofin_demo, "_plan_terms", paper_terms)
    monkeypatch.setattr(
        "app.services.automated_paper_loop._grade_for_plan",
        lambda _candidate, _assembled: (grade, Decimal("1")),
    )
    loop = GovernedBloFinDemoLoop(None, None, None)  # type: ignore[arg-type]
    loop._snapshot = snapshot
    common = {
        "candidate": world.candidate,
        "assembled": None,
        "policy": None,
        "account_id": uuid4(),
        "equity": equity,
        "now": EVALUATED_AT,
        "eligibility_valid_until": terms.valid_until,
        "eligibility_id": uuid4(),
        "active_profile": active,
        "open_exposure": Decimal("0"),
        "open_risk_percent": Decimal("0"),
        "open_positions": 0,
        "remaining_daily_budget_percent": Decimal("6"),
        "breaker_refusing": False,
    }
    assert paper_terms(accept_unprofiled_minimum=False) == "size_below_minimum"
    blocked = loop._plan_terms(**common)  # type: ignore[arg-type]
    assert blocked == "demo_account_leverage_mismatch"
    assert True in accepted
    loop._snapshot = replace(snapshot, account_leverage=Decimal("10"), margin_mode="isolated")
    planned = loop._plan_terms(**common)  # type: ignore[arg-type]
    assert not isinstance(planned, str)
    assert planned.risk_and_exits.margin_assumption_id == "verified-demo-read-leverage"
    paused = loop._plan_terms(**{**common, "breaker_refusing": True})  # type: ignore[arg-type]
    assert paused == "breaker_active"
    assert venue.post_count == 0
