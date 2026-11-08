"""Tool registry API (for agent workspace and debugging)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import Field

from app.core.dependencies import ToolRegistryDep
from app.schemas.common import StrictModel
from app.schemas.tools import ToolOutput, ToolSpec
from app.security.rbac import ReaderDep, TraderDep
from app.tools.registry import HTTP_EXECUTABLE_READ_ONLY_TOOLS, HTTP_STRIPPED_TOOL_ARGUMENTS

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolExecuteRequest(StrictModel):
    tool_name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.get("", response_model=list[ToolSpec], summary="List registered tools")
async def list_tools(registry: ToolRegistryDep, _tenant: ReaderDep) -> list[ToolSpec]:
    return registry.list_specs()


@router.post("/execute", response_model=ToolOutput, summary="Execute a tool (debug/agent)")
async def execute_tool(
    body: ToolExecuteRequest,
    registry: ToolRegistryDep,
    tenant: TraderDep,
) -> ToolOutput:
    # Read-only tools only; mutations go through the proposal/confirm flow.
    if body.tool_name not in HTTP_EXECUTABLE_READ_ONLY_TOOLS:
        return ToolOutput(
            tool_name=body.tool_name,
            success=False,
            error="Tool is not executable via this route (read-only tools only).",
        )
    # Bind tenant from JWT; never trust client-supplied identity or consent
    # (organization_id/user_id/confirm/user_message are dropped).
    arguments = {
        **{k: v for k, v in body.arguments.items() if k not in HTTP_STRIPPED_TOOL_ARGUMENTS},
        "organization_id": str(tenant.organization_id),
        "user_id": str(tenant.user_id),
    }
    return registry.execute(body.tool_name, arguments)
