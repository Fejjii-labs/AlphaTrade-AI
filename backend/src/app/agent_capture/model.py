"""Frontier reasoning for capture suggestions; model output has no tool authority."""

import json
from typing import Any
from uuid import UUID

from app.agent_capture.contracts import CapturePlan
from app.core.config import Settings
from app.core.prompt_registry import load_prompt_record
from app.interactive_agent.conversation import _correlation
from app.providers.factory import resolve_providers
from app.providers.llm import LLMMessage, LLMProvider
from app.schemas.model_routing import (
    ModelCallerScope,
    ModelContextScope,
    ModelResourceType,
    ModelRoutingPurpose,
    ModelTaskRequest,
)
from app.services.model_call_telemetry import ModelCallTelemetryService
from app.services.model_router import ModelRouter

SYSTEM_PROMPT = load_prompt_record("agent_capture_system")
SYSTEM = SYSTEM_PROMPT.text


class CaptureUnavailableError(Exception):
    pass


class CaptureModel:
    def __init__(self, settings: Settings, *, llm_provider: LLMProvider | None = None) -> None:
        self.settings = settings
        self._llm_provider = llm_provider
        self.last_usage: dict[str, Any] = {}

    def plan(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        conversation_id: UUID,
        content: dict[str, Any],
    ) -> CapturePlan:
        llm = self._llm_provider or resolve_providers(self.settings).llm
        router = ModelRouter(
            llm,
            tier_a_model=self.settings.agent_reasoning_model,
            tier_b_model=self.settings.agent_reasoning_model,
            fail_closed=True,
            telemetry=ModelCallTelemetryService(None),
        )
        schema = CapturePlan.model_json_schema()
        result = router.complete(
            ModelTaskRequest(
                purpose=ModelRoutingPurpose.GENERAL_AGENT_SYNTHESIS,
                context=ModelContextScope(
                    organization_id=organization_id,
                    user_id=user_id,
                    resource_type=ModelResourceType.CONVERSATION,
                    resource_id=conversation_id,
                    purpose=ModelRoutingPurpose.GENERAL_AGENT_SYNTHESIS,
                ),
                caller_scope=ModelCallerScope.USER,
                caller_organization_id=organization_id,
                caller_user_id=user_id,
                caller_resource_type=ModelResourceType.CONVERSATION,
                caller_resource_id=conversation_id,
                correlation_id=_correlation("capture"),
                reasoning_effort=self.settings.agent_reasoning_effort,
                max_output_tokens=self.settings.agent_max_output_tokens,
                prompt_id=SYSTEM_PROMPT.id,
                prompt_version=SYSTEM_PROMPT.version,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "agent_capture", "strict": True, "schema": schema},
                },
            ),
            [
                LLMMessage(role="system", content=SYSTEM),
                LLMMessage(role="user", content=json.dumps(content, ensure_ascii=False)),
            ],
        )
        self.last_usage = {
            "model": result.resolved_model,
            "fallback_used": result.fallback_used,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "latency_ms": result.total_latency_ms,
            "cost_source": result.cost_source.value,
            "cost": str(result.total_cost) if result.cost_source.value != "unavailable" else None,
            "unavailable": result.unavailable,
        }
        if result.unavailable or result.fallback_used or not result.content:
            raise CaptureUnavailableError("Capture reasoning provider is unavailable.")
        return CapturePlan.model_validate_json(result.content)
