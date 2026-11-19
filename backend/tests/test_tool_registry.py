"""Tool registry registration and execution tests."""

from __future__ import annotations

import pytest

from app.tools.registry import ToolRegistry, build_default_registry


def test_registry_registers_all_mvp_tools() -> None:
    registry = build_default_registry()
    names = {t.name for t in registry.list_specs()}
    expected = {
        "rag_retriever",
        "market_data",
        "indicator",
        "funding",
        "risk_checker",
        "strategy_evaluator",
        "scenario_simulator",
        "journal_writer",
        "position_reader",
        "paper_execution",
    }
    assert expected <= names
    assert names.isdisjoint(
        {"analytics_summary_tool", "human_vs_system_tool", "paper_validation_tool"}
    )


def test_registry_rejects_duplicate() -> None:
    from app.schemas.common import ToolRiskLevel
    from app.tools.base import ToolDefinition

    registry = ToolRegistry()
    stub = ToolDefinition(
        name="dup",
        description="x",
        risk_level=ToolRiskLevel.READ,
        requires_approval=False,
        provider_dependencies=(),
        has_fallback=False,
        enabled=True,
        execute=lambda _a: None,  # type: ignore[return-value]
    )
    registry.register(stub)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(stub)


def test_paper_execution_tool_is_sensitive_and_requires_approval() -> None:
    from app.tools.fail_closed import ToolFailureCode

    registry = build_default_registry()
    tool = registry.get("paper_execution")
    assert tool is not None
    assert tool.requires_approval
    out = registry.execute("paper_execution", {})
    assert out.success is False
    assert out.result is not None
    assert out.result.get("code") == ToolFailureCode.NOT_IMPLEMENTED.value
    assert out.result.get("mode") != "trade"


def test_risk_checker_tool_runs_engine() -> None:
    registry = build_default_registry()
    out = registry.execute(
        "risk_checker",
        {
            "request": {
                "symbol": "BTCUSDT",
                "direction": "long",
                "entry_price": "60000",
                "position_size": "0.01",
                "leverage": "3",
                "account_equity": "10000",
                "stop_loss": "58000",
            }
        },
    )
    assert out.success
    assert out.result is not None
    assert out.result["action"] in {"allow", "warn", "block"}
