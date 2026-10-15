"""Loopback-only synthetic screening dependency for the real local API/browser.

The application routes/auth/database are real. Public acquisition alone is replaced
with original tagged synthetic receipts. Runtime settings remain disarmed; no
production dependency or operator flag is changed. Never deploy this test harness.
"""

import json
import os
import re
from dataclasses import replace

from sqlalchemy.engine import make_url
from tests.support.trendpulse_screening import DelayedReplayAcquirer, after_close

from app.api.routes.trendpulse_screening import get_screening_service
from app.core.config import get_settings
from app.core.dependencies import SessionDep
from app.main import create_app
from app.strategy_brain.trendpulse_screening.service import TrendPulseScreeningService

settings = get_settings()
url = make_url(settings.database_url)
if not (
    settings.environment == "local"
    and settings.provider_mode == "mock"
    and not settings.worker_enabled
    and not settings.enable_real_trading
    and not settings.trendpulse_screening_enabled
    and url.host in {"127.0.0.1", "localhost"}
    and url.database == "alphatrade_test"
):
    raise RuntimeError("Research browser fixture requires disarmed disposable local/mock settings.")

app = create_app(settings)


@app.middleware("http")
async def fixture_identity(request, call_next):
    response = await call_next(request)
    response.headers["X-Synthetic-Fixture-Process"] = str(os.getpid())
    return response


def synthetic_service(session: SessionDep):
    return TrendPulseScreeningService(
        session,
        settings.model_copy(update={"trendpulse_screening_enabled": True}),
        acquirer=DelayedReplayAcquirer(),
        clock=after_close,
    )


app.dependency_overrides[get_screening_service] = synthetic_service


if os.getenv("SYNTHETIC_CAPTURE_BROWSER") == "true":
    # Explicit test-only responses still pass through the production CaptureModel,
    # ModelRouter, validation, turn coordinator and PostgreSQL persistence. No
    # production provider configuration or paid model is changed.
    import app.agent_capture.model as capture_model
    from app.agent_capture.contracts import CapturePlan, CaptureSuggestion, StrategyDraft
    from app.providers.llm import LLMCompletionResult, MockLLMProvider

    original_capture_providers = capture_model.resolve_providers

    class SyntheticCaptureProvider(MockLLMProvider):
        def complete(self, request):
            schema_name = (request.response_format or {}).get("json_schema", {}).get("name")
            if schema_name != "agent_capture":
                return super().complete(request)
            content = json.loads(next(m.content for m in request.messages if m.role == "user"))
            source = "\n".join(
                [content["current_user_content"], content.get("uploaded_reference_content", "")]
            )
            suggestions = []
            for category, quote in re.findall(
                r"^\[(journal|rules|strategies|news_analysis|lessons)\] (.+)$", source, re.M
            ):
                suggestions.append(
                    CaptureSuggestion(
                        category=category,
                        title=f"Synthetic {category.replace('_', ' ')} note",
                        summary=quote,
                        evidence_quotes=[quote],
                        tags=["synthetic-browser"],
                        confidence=0.99,
                        target_entry_id=None,
                        draft=StrategyDraft(
                            family="synthetic observation",
                            market=None,
                            direction=None,
                            timeframe=None,
                            entry_rules=[quote],
                            exit_rules=[],
                            invalidation=[],
                            missing_fields=[
                                "market",
                                "direction",
                                "timeframe",
                                "exit",
                                "invalidation",
                            ],
                        )
                        if category == "strategies"
                        else None,
                    )
                )
            plan = CapturePlan(entries=suggestions, clarification=None)
            payload = plan.model_dump(mode="json")
            return LLMCompletionResult(
                content=json.dumps(payload),
                model=request.model,
                provider=self.name,
                parsed_json=payload,
                fallback_used=False,
                input_tokens=10,
                output_tokens=10,
                latency_ms=1,
            )

    def synthetic_capture_providers(settings):
        return replace(original_capture_providers(settings), llm=SyntheticCaptureProvider())

    capture_model.resolve_providers = synthetic_capture_providers
