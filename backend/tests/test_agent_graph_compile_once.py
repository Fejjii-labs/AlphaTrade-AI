"""The agent graph compiles once per process; runtimes travel per invoke (review item 11)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents import graph as agent_graph
from app.agents import nodes
from app.agents.runtime import AgentRuntime
from app.agents.state_utils import state_to_dict
from app.core.config import Settings
from app.db.base import Base
from app.schemas.agent import AgentState
from app.services.agent_service import AgentInvokeContext, AgentService
from app.services.risk_service import RiskService
from app.services.strategy_service import StrategyService
from app.services.turn_context import turn_context
from app.strategies.registry import build_default_registry
from app.tools.registry import build_default_registry as build_tools


def _runtime(*, quota_exceeded: bool = False) -> AgentRuntime:
    settings = Settings(execution_mode="paper", enable_real_trading=False, log_json=False)
    return AgentRuntime(
        settings=settings,
        risk_service=RiskService(),
        strategy_service=StrategyService(registry=build_default_registry()),
        tool_registry=build_tools(settings),
        quota_exceeded=quota_exceeded,
    )


def _context() -> AgentInvokeContext:
    return AgentInvokeContext(
        request_id=f"compile-once-{uuid.uuid4()}",
        user_id=uuid.UUID("00000000-0000-0000-0000-00000000e001"),
        organization_id=uuid.UUID("00000000-0000-0000-0000-00000000e002"),
    )


@pytest.fixture
def fresh_graph_cache(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Drop the process cache and count compilations."""
    monkeypatch.setattr(agent_graph, "_compiled", None)
    calls: list[int] = []
    original = agent_graph.compile_agent_graph

    def counting() -> object:
        calls.append(1)
        return original()

    monkeypatch.setattr(agent_graph, "compile_agent_graph", counting)
    return calls


def test_graph_compiles_once_across_turns_and_services(fresh_graph_cache: list[int]) -> None:
    for _ in range(3):
        AgentService(runtime=_runtime()).run("hello there", _context())
    AgentService(runtime=_runtime()).run("analyze BTC", _context(), symbol="BTCUSDT")
    assert len(fresh_graph_cache) == 1


def test_concurrent_first_use_compiles_once(fresh_graph_cache: list[int]) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        graphs = list(pool.map(lambda _: agent_graph.get_agent_graph(), range(16)))
    assert len(fresh_graph_cache) == 1
    assert all(g is graphs[0] for g in graphs)


def test_runtimes_do_not_leak_between_sequential_turns() -> None:
    blocked, allowed = _runtime(quota_exceeded=True), _runtime()
    statuses = [
        AgentService(runtime=runtime).run("hello there", _context()).approval_status
        for runtime in (blocked, allowed, blocked, allowed)
    ]
    # quota_exceeded on the runtime blocks the turn; only that runtime's turns block.
    assert statuses == ["blocked", "not_required", "blocked", "not_required"]


def test_runtimes_do_not_leak_between_concurrent_turns() -> None:
    flags = [index % 2 == 0 for index in range(12)]

    def turn(blocked: bool) -> bool:
        service = AgentService(runtime=_runtime(quota_exceeded=blocked))
        return service.run("hi", _context()).approval_status == "blocked"

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(turn, flags)) == flags


def test_invoking_without_a_runtime_fails_loudly() -> None:
    state = state_to_dict(AgentState(request_id="no-runtime", message="hi"))
    with pytest.raises(RuntimeError, match="without an AgentRuntime"):
        agent_graph.get_agent_graph().invoke(state)


def test_turn_node_scope_runtime_takes_precedence_per_node() -> None:
    """Durable chat turns swap the runtime per node; the invoke runtime is only the seed."""
    seed, phase_runtime = _runtime(), _runtime(quota_exceeded=True)
    scoped: list[str] = []

    @contextmanager
    def scope(name: str) -> Iterator[AgentRuntime]:
        scoped.append(name)
        yield phase_runtime

    # Provider phases check quota through short-lived turn sessions.
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    context = _context()
    state = state_to_dict(
        AgentState(
            request_id="scoped",
            message="hi",
            user_id=context.user_id,
            organization_id=context.organization_id,
        )
    )
    with turn_context(uuid.uuid4(), sessions) as turn:
        turn.node_scope = scope
        result = agent_graph.invoke_agent_graph(state, seed)
    # The seed runtime would pass; the phase runtime (quota exceeded) must be the one used.
    assert result["safety_verdict"] == "block"
    assert scoped[:3] == ["receive_request", "auth_context", "quota_check"]
    engine.dispose()


def test_node_replaced_after_compile_is_the_one_that_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nodes resolve from app.agents.nodes per call; compile-once must not pin them."""
    agent_graph.get_agent_graph()  # Compiled before the replacement below.
    original = nodes.context_retrieval
    seen: list[str] = []

    def replaced(state: dict, runtime: AgentRuntime) -> dict:
        seen.append(state["request_id"])
        return original(state, runtime)

    monkeypatch.setattr(nodes, "context_retrieval", replaced)
    context = _context()
    AgentService(runtime=_runtime()).run("hello there", context)
    assert seen == [context.request_id]
