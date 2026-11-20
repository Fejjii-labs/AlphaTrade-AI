"""Importing the API must not load the agent graph stack (LangGraph, LangChain).

The graph is needed only when a chat turn runs. Loading it at import costs
about 20 MB RSS and 500 modules on a 512 MB instance, so ``app.agents``
exposes it lazily (PEP 562) and the agent service imports it on first call.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

HEAVY = ("langgraph", "langchain_core", "langsmith", "app.agents.graph", "app.agents.nodes")


def _loaded_after(statement: str) -> set[str]:
    probe = ",".join(HEAVY)
    code = "\n".join(
        [
            "import sys",
            statement,
            f"heavy = {probe!r}.split(',')",
            "print('LOADED:' + ','.join(m for m in heavy if m in sys.modules))",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    # Startup logging may print to stdout; the marker line is the last one.
    last = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    assert last.startswith("LOADED:"), result.stdout[-500:]
    return {m for m in last.removeprefix("LOADED:").split(",") if m}


def test_importing_the_api_does_not_load_the_agent_graph() -> None:
    assert _loaded_after("import app.main; app.main.create_app()") == set()


def test_agents_package_still_exports_the_graph_api_lazily() -> None:
    import app.agents as agents
    from app.agents import graph

    assert agents.invoke_agent_graph is graph.invoke_agent_graph
    assert agents.get_agent_graph is graph.get_agent_graph
    assert agents.build_agent_graph is graph.build_agent_graph
    assert agents.compile_agent_graph is graph.compile_agent_graph
    with pytest.raises(AttributeError):
        _ = agents.not_an_export  # type: ignore[attr-defined]


def test_agent_service_wrapper_delegates_to_the_graph(monkeypatch) -> None:
    from app.agents import graph
    from app.services import agent_service

    calls: list[tuple[object, object]] = []
    monkeypatch.setattr(
        graph,
        "invoke_agent_graph",
        lambda state, runtime: calls.append((state, runtime)) or {"ok": 1},
    )

    assert agent_service.invoke_agent_graph({"a": 1}, "rt") == {"ok": 1}
    assert calls == [({"a": 1}, "rt")]
