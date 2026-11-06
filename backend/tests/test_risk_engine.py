"""Deterministic risk engine: allow and block cases."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.schemas.common import RiskAction, RiskRuleId, TradeDirection
from app.schemas.risk import RiskCheckRequest
from app.services.risk.engine import RiskEngine
from app.services.risk.limits import RiskLimits
from app.services.risk.rules import RiskEvaluationContext
from app.services.risk_service import RiskService


def _request(**kwargs: object) -> RiskCheckRequest:
    base = {
        "symbol": "BTCUSDT",
        "direction": TradeDirection.LONG,
        "entry_price": Decimal("60000"),
        "position_size": Decimal("0.005"),
        "leverage": Decimal("3"),
        "account_equity": Decimal("10000"),
        "stop_loss": Decimal("58000"),
        "volume_24h": Decimal("50000000"),
    }
    base.update(kwargs)
    return RiskCheckRequest(**base)  # type: ignore[arg-type]


def test_risk_engine_allow_case() -> None:
    result = RiskService(RiskEngine(RiskLimits())).check(
        _request(),
        context=RiskEvaluationContext(is_weekend=False, kill_switch_active=False),
    )
    assert result.action is RiskAction.ALLOW
    assert not result.approval_required


def test_risk_engine_block_no_stop_loss() -> None:
    result = RiskService().check(_request(stop_loss=None))
    assert result.action is RiskAction.BLOCK
    assert any(t.rule_id is RiskRuleId.NO_STOP_LOSS for t in result.triggered_rules)


def test_risk_engine_block_kill_switch() -> None:
    result = RiskService().check(
        _request(),
        context=RiskEvaluationContext(kill_switch_active=True, is_weekend=False),
    )
    assert result.action is RiskAction.BLOCK
    assert any(t.rule_id is RiskRuleId.KILL_SWITCH for t in result.triggered_rules)


def test_risk_engine_block_excessive_leverage() -> None:
    result = RiskService(RiskEngine(RiskLimits(max_leverage=Decimal("5")))).check(
        _request(leverage=Decimal("50"))
    )
    assert result.action is RiskAction.BLOCK


@pytest.mark.parametrize("bad_leverage", [Decimal("0"), Decimal("200")])
def test_schema_rejects_invalid_leverage(bad_leverage: Decimal) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _request(leverage=bad_leverage)


@pytest.mark.parametrize(
    "context,rule,action",
    [
        # Daily loss is a warning only; the 10% equity stop halts (AT-ADR-152).
        (RiskEvaluationContext(daily_locked=True), RiskRuleId.MAX_DAILY_LOSS, RiskAction.WARN),
        (RiskEvaluationContext(trades_today=20), RiskRuleId.OVERTRADING, RiskAction.WARN),
        (
            RiskEvaluationContext(protect_green_day=True),
            RiskRuleId.STRONG_GREEN_DAY,
            RiskAction.WARN,
        ),
        (
            RiskEvaluationContext(open_exposure_notional=Decimal("864.19")),
            RiskRuleId.MAX_POSITION_SIZE,
            RiskAction.BLOCK,
        ),
    ],
)
def test_strategy_discipline_and_portfolio_rules_remain_enforced(context, rule, action):
    result = RiskEngine().evaluate(_request(), context=context)
    assert result.action is action
    assert any(trigger.rule_id is rule for trigger in result.triggered_rules)


@pytest.mark.parametrize(
    "context,message",
    [
        (RiskEvaluationContext(daily_locked=True), "Daily loss limit reached"),
        # Cash limit reached and exceeded with the lock still false (Griller R5 C1).
        (
            RiskEvaluationContext(
                realized_pnl_today=Decimal("-250"), daily_loss_limit=Decimal("250")
            ),
            "Realized PnL has reached the daily loss limit",
        ),
        (
            RiskEvaluationContext(
                realized_pnl_today=Decimal("-260"), daily_loss_limit=Decimal("250")
            ),
            "Realized PnL has reached the daily loss limit",
        ),
        # RiskLimits.max_daily_loss_pct (3%) with no cash limit.
        (RiskEvaluationContext(realized_pnl_today=Decimal("-300")), "Daily loss 3.00%"),
    ],
    ids=["lock", "cash_limit_reached", "cash_limit_exceeded", "max_daily_loss_pct"],
)
def test_daily_loss_lock_warns_and_never_blocks(context, message):
    """Every check_daily_loss_lock branch is WARN, never BLOCK (AT-ADR-152)."""

    from app.services.risk.rules import check_daily_loss_lock

    request = _request()
    rule = check_daily_loss_lock(request, RiskLimits(), context)
    assert rule is not None
    assert rule.rule_id is RiskRuleId.MAX_DAILY_LOSS
    assert rule.action is RiskAction.WARN
    assert rule.message.startswith(message)
    result = RiskEngine().evaluate(request, context=context)
    assert result.action is RiskAction.WARN
    assert [t.action for t in result.triggered_rules] == [RiskAction.WARN]
