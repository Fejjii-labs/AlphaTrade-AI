"""Route handlers that never ``await`` must be plain ``def``.

The API uses a synchronous SQLAlchemy engine, a synchronous Redis client and
synchronous ``httpx`` exchange clients. FastAPI runs a plain ``def`` handler in
its threadpool, but an ``async def`` handler runs on the single event loop, so
one slow query or venue call stalls every other request in flight (one uvicorn
process serves staging). A handler may stay ``async`` only when it awaits.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROUTES = Path(__file__).resolve().parents[1] / "src" / "app" / "api" / "routes"

# Streaming / websocket-adjacent modules owned by the voice and agent tracks.
ALLOWED_ASYNC_WITHOUT_AWAIT: frozenset[str] = frozenset(
    {"agent_voice.py", "interactive_agent.py", "chat.py"}
)
_ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "websocket"}


def _is_route(node: ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        call = decorator if isinstance(decorator, ast.Call) else None
        func = call.func if call is not None else decorator
        if isinstance(func, ast.Attribute) and func.attr in _ROUTE_METHODS:
            return True
    return False


def _awaits(node: ast.AST) -> bool:
    return any(isinstance(x, (ast.Await, ast.AsyncFor, ast.AsyncWith)) for x in ast.walk(node))


def blocking_async_handlers() -> list[str]:
    offenders: list[str] = []
    for path in sorted(ROUTES.glob("*.py")):
        if path.name in ALLOWED_ASYNC_WITHOUT_AWAIT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.AsyncFunctionDef) or not _is_route(node):
                continue
            if not _awaits(node):
                offenders.append(f"{path.name}:{node.lineno} {node.name}")
    return offenders


def test_route_handlers_without_await_are_sync() -> None:
    offenders = blocking_async_handlers()
    assert offenders == [], (
        "async route handlers that never await run blocking work on the event loop; "
        "make them `def` so FastAPI uses the threadpool:\n" + "\n".join(offenders)
    )
