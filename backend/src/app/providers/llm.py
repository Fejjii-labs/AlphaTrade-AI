"""OpenAI-compatible LLM provider abstraction.

Classic chat models use ``/v1/chat/completions``. GPT-5.x / o-series reasoning
models use ``/v1/responses`` (required for reliable ``gpt-5.6-sol`` generation).
The public :class:`LLMProvider` interface stays OpenAI-agnostic.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx
import httpx2
import structlog
from openai import APIStatusError, APITimeoutError, OpenAI

from app.providers.base import BaseMockProvider, ProviderHealth, ProviderKind, ProviderStatus

logger = structlog.get_logger(__name__)

# Public /providers/status is unauthenticated — keep generation probes cheap and rare.
_GENERATION_PROBE_TTL_SECONDS = 300.0
_GENERATION_PROBE_TIMEOUT_SECONDS = 10.0
_GENERATION_PROBE_MAX_OUTPUT_TOKENS = 64
_GENERATION_PROBE_PROMPT = "Reply with OK"
_GENERATION_PROBE_MAX_RETRIES = 0


@dataclass(frozen=True)
class LLMMessage:
    role: str
    content: str


@dataclass(frozen=True)
class LLMCompletionRequest:
    messages: list[LLMMessage]
    model: str
    temperature: float = 0.0
    max_tokens: int = 512
    response_format: dict[str, Any] | None = None
    # Legacy callers may keep provider-owned mock fallback. ModelRouter sets False
    # so failed upstream attempts remain visible in actual-call telemetry.
    allow_internal_fallback: bool = True
    reasoning_effort: str = "low"


@dataclass(frozen=True)
class LLMCompletionResult:
    content: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    fallback_used: bool = False
    parsed_json: dict[str, Any] | None = None


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    kind: ProviderKind

    def complete(self, request: LLMCompletionRequest) -> LLMCompletionResult: ...

    def status(self) -> ProviderStatus: ...


def model_requires_responses_api(model: str) -> bool:
    """True when the model should use ``/v1/responses`` instead of chat completions.

    GPT-5.x (including ``gpt-5.6-sol``) and o-series reasoning models are routed
    to Responses. Classic chat models keep Chat Completions for local/default
    compatibility (e.g. ``gpt-4o-mini``).
    """
    name = (model or "").strip().lower()
    if not name:
        return False
    prefixes = (
        "gpt-5",
        "gpt-6",
        "o1",
        "o3",
        "o4",
    )
    return any(name == p or name.startswith(f"{p}-") or name.startswith(p) for p in prefixes)


def _sanitize_openai_http_error(exc: BaseException) -> dict[str, Any]:
    """Map provider failures to client-safe categories (no secrets / bodies)."""
    if isinstance(exc, (APITimeoutError, httpx.TimeoutException, httpx2.TimeoutException)):
        return {"reason": "openai_llm_timeout", "http_status": None, "error_code": None}
    if isinstance(exc, (APIStatusError, httpx.HTTPStatusError)):
        status = int(
            exc.status_code if isinstance(exc, APIStatusError) else exc.response.status_code
        )
        # Remote codes/messages may contain arbitrary sensitive text. Export only categories.
        error_code: str | None = None
        if status in {401, 403}:
            reason = "openai_llm_permission_denied"
        elif status == 404:
            reason = "openai_llm_model_unavailable"
        elif status == 429:
            reason = "openai_llm_rate_limited"
        elif status >= 500:
            reason = "openai_llm_upstream_error"
        else:
            reason = "openai_llm_request_rejected"
        return {"reason": reason, "http_status": status, "error_code": error_code}
    if isinstance(exc, (json.JSONDecodeError, KeyError, TypeError, ValueError)):
        return {"reason": "openai_llm_malformed_response", "http_status": None, "error_code": None}
    return {"reason": "openai_llm_unavailable", "http_status": None, "error_code": None}


class MockLLMProvider(BaseMockProvider):
    """Deterministic LLM responses for offline tests and fallback mode."""

    def __init__(self) -> None:
        super().__init__(
            "mock-llm",
            ProviderKind.LLM,
            detail="Deterministic mock LLM — no external API calls.",
        )

    def complete(self, request: LLMCompletionRequest) -> LLMCompletionResult:
        system_text = next((m.content for m in request.messages if m.role == "system"), "")
        user_text = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        if "__narrative_task__:" in system_text:
            payload = self._mock_narrative_payload(system_text, user_text)
        else:
            summary = user_text[:120] or "No user message."
            payload = {
                "summary": f"Mock analysis for: {summary}",
                "setup_type": "deterministic_scaffold",
                "evidence": ["Strategy signals evaluated deterministically."],
            }
        content = json.dumps(payload)
        input_tokens = max(len(content) // 4, 1)
        return LLMCompletionResult(
            content=content,
            model=request.model,
            provider=self.name,
            input_tokens=input_tokens,
            output_tokens=len(content) // 4,
            latency_ms=1.0,
            fallback_used=True,
            parsed_json=payload,
        )

    def _mock_narrative_payload(self, system_text: str, user_text: str) -> dict[str, Any]:
        """Deterministic narrative JSON for offline tests and fallback mode."""
        from app.guardrails.testing import TEST_INVALID_NARRATIVE, TEST_UNSAFE_NARRATIVE

        combined = f"{system_text} {user_text}"
        if TEST_INVALID_NARRATIVE in combined:
            return {"invalid": True}
        if TEST_UNSAFE_NARRATIVE in combined:
            return {
                "summary": "Guaranteed profit on this setup — buy now at market.",
                "setup_interpretation": "All-in now.",
                "evidence_explanation": "Sure win.",
                "risk_explanation": "Low risk guaranteed.",
                "invalidation_explanation": "N/A",
                "next_decision_point": "Enter immediately.",
                "caution_notes": [],
                "limitations": [],
                "paper_mode_disclaimer": "Paper mode.",
                "citations_used": [],
            }

        approval_status = "not_required"
        risk_level = "medium"
        market_quality = "mock"
        pending_markers = ('"approval_status": "pending"', '"approval_status":"pending"')
        if any(m in system_text for m in pending_markers):
            approval_status = "pending"
        if '"approval_status": "blocked"' in system_text:
            approval_status = "blocked"
        if '"risk_level": "high"' in system_text or '"risk_level":"high"' in system_text:
            risk_level = "high"
        elif '"risk_level": "medium"' in system_text or '"risk_level":"medium"' in system_text:
            risk_level = "medium"
        elif '"risk_level": "low"' in system_text or '"risk_level":"low"' in system_text:
            risk_level = "low"
        if '"market_data_quality": "stale"' in system_text:
            market_quality = "stale"

        data_disclosure = (
            "Mock market data — prices are not live."
            if market_quality == "mock"
            else "Stale market data — verify timestamps before acting."
        )
        has_proposal = '"has_trade_proposal": true' in system_text
        invalidation = (
            "Invalidation: close below stop or HTF structure breaks."
            if has_proposal
            else "No trade proposal — invalidation not applicable."
        )

        return {
            "summary": (
                "Mock narrative clarifies the deterministic analysis without changing facts. "
                f"{data_disclosure}"
            ),
            "setup_interpretation": "Deterministic setup interpretation from structured context.",
            "evidence_explanation": "Evidence follows strategy signals and indicator context.",
            "risk_explanation": (
                f"Risk level {risk_level} per deterministic engine; approval {approval_status}."
            ),
            "invalidation_explanation": invalidation,
            "next_decision_point": "Review plan; human approval required before paper execution.",
            "caution_notes": [
                "Mock LLM narrative — deterministic layer remains authoritative.",
                f"Approval status: {approval_status}.",
            ],
            "limitations": [
                "Real exchange execution disabled.",
                data_disclosure,
                f"Market data quality: {market_quality}.",
            ],
            "paper_mode_disclaimer": (
                "Paper mode only — no real exchange execution. "
                "Approval required for sensitive actions."
            ),
            "citations_used": ["mock-citation-1: playbook excerpt"],
        }


class OpenAILLMProvider:
    """Official SDK adapter. Retry ownership is exclusively in ModelRouter."""

    name = "openai-llm"
    kind = ProviderKind.LLM

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        fallback: MockLLMProvider | None = None,
        timeout_seconds: float = 60.0,
        fail_closed: bool = False,
        generation_probe_ttl_seconds: float = _GENERATION_PROBE_TTL_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._default_model = model
        self._fail_closed = fail_closed
        self._fallback = None if fail_closed else (fallback or MockLLMProvider())
        self._timeout = timeout_seconds
        self._probe_ttl = max(generation_probe_ttl_seconds, 0.0)
        self._probe_cache: tuple[float, bool, str] | None = None
        self._probe_lock = threading.Lock()
        self._probe_cond = threading.Condition(self._probe_lock)
        self._probe_in_flight = False

    def uses_responses_api(self, model: str | None = None) -> bool:
        return model_requires_responses_api(model or self._default_model)

    def _internal_fallback(self, request: LLMCompletionRequest) -> MockLLMProvider | None:
        if self._fail_closed or not request.allow_internal_fallback:
            return None
        return self._fallback

    def complete(self, request: LLMCompletionRequest) -> LLMCompletionResult:
        from app.core.errors import ServiceUnavailableError

        fallback = self._internal_fallback(request)
        if not self._api_key:
            if fallback is None:
                raise ServiceUnavailableError(
                    "LLM provider is unavailable.",
                    details={"reason": "openai_api_key_missing", "provider": self.name},
                )
            return fallback.complete(request)

        started = time.perf_counter()
        model = (request.model or self._default_model).strip() or self._default_model
        payload: dict[str, Any] = {}
        try:
            if model_requires_responses_api(model):
                payload = self._post_responses(request, model=model)
                content, input_tokens, output_tokens, resolved_model = (
                    self._parse_responses_payload(payload, fallback_model=model)
                )
            else:
                payload = self._post_chat_completions(request, model=model)
                content, input_tokens, output_tokens, resolved_model = (
                    self._parse_chat_completions_payload(payload, fallback_model=model)
                )
            if (request.response_format or {}).get("type") in {
                "json_object",
                "json_schema",
            } and _try_parse_json(content) is None:
                raise ValueError("structured output is not a JSON object")
        except Exception as exc:
            details = _sanitize_openai_http_error(exc)
            usage = payload.get("usage") or {}
            details["input_tokens"] = _usage_tokens(usage, "input_tokens", "prompt_tokens")
            details["output_tokens"] = _usage_tokens(usage, "output_tokens", "completion_tokens")
            details["provider"] = self.name
            details["api"] = (
                "responses" if model_requires_responses_api(model) else "chat_completions"
            )
            logger.warning(
                "openai_llm_request_failed",
                error=type(exc).__name__,
                reason=details.get("reason"),
                http_status=details.get("http_status"),
                error_code=details.get("error_code"),
                api=details.get("api"),
            )
            if fallback is None:
                raise ServiceUnavailableError(
                    "LLM provider is unavailable.",
                    details=details,
                ) from exc
            result = fallback.complete(request)
            return LLMCompletionResult(
                content=result.content,
                model=result.model,
                provider=self.name,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                fallback_used=True,
                parsed_json=result.parsed_json,
            )

        if not content.strip():
            details = {
                "reason": "openai_llm_malformed_response",
                "http_status": None,
                "error_code": "empty_output",
                "provider": self.name,
            }
            if fallback is None:
                raise ServiceUnavailableError(
                    "LLM provider is unavailable.",
                    details=details,
                )
            result = fallback.complete(request)
            return LLMCompletionResult(
                content=result.content,
                model=result.model,
                provider=self.name,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                fallback_used=True,
                parsed_json=result.parsed_json,
            )

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return LLMCompletionResult(
            content=content,
            model=resolved_model,
            provider=self.name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            fallback_used=False,
            parsed_json=_try_parse_json(content),
        )

    def _post_chat_completions(
        self,
        request: LLMCompletionRequest,
        *,
        model: str,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.response_format is not None:
            body["response_format"] = request.response_format
        return self._post_json(
            f"{self._base_url}/chat/completions",
            body,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )

    def _post_responses(
        self,
        request: LLMCompletionRequest,
        *,
        model: str,
        reasoning_effort: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        instructions, input_items = _split_messages_for_responses(request.messages)
        body: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "max_output_tokens": request.max_tokens,
            "store": False,
            # Honor the caller reasoning budget; explicit provider overrides take precedence.
            "reasoning": {"effort": reasoning_effort or request.reasoning_effort},
        }
        if instructions:
            body["instructions"] = instructions
        # Reasoning models frequently reject temperature; omit for Responses path.
        if request.response_format is not None:
            body["text"] = {"format": _map_response_format(request.response_format)}
        return self._post_json(
            f"{self._base_url}/responses",
            body,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )

    def _post_json(
        self,
        url: str,
        body: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> dict[str, Any]:

        # Each adapter call is one observable upstream attempt. SDK automatic retries
        # would hide billable attempts from our ledger, so they stay disabled.
        del max_retries
        timeout = self._timeout if timeout_seconds is None else timeout_seconds
        with OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            max_retries=0,
            timeout=httpx2.Timeout(timeout, connect=min(5.0, timeout)),
        ) as client:
            if url.endswith("/responses"):
                response = client.responses.create(**body)
            else:
                response = client.chat.completions.create(**body)
            payload: dict[str, Any] = response.model_dump(mode="json")
        return payload

    def _parse_chat_completions_payload(
        self, payload: dict[str, Any], *, fallback_model: str
    ) -> tuple[str, int, int, str]:
        choice = (payload.get("choices") or [{}])[0]
        if not isinstance(choice, dict):
            raise ValueError("malformed choices")
        message = choice.get("message") or {}
        if not isinstance(message, dict):
            raise ValueError("malformed message")
        if message.get("refusal") or choice.get("finish_reason") in {"length", "content_filter"}:
            raise ValueError("refused or incomplete generation")
        raw_content = message.get("content")
        if raw_content is not None and not isinstance(raw_content, str):
            raise ValueError("malformed content")
        content = raw_content or ""
        if not content.strip():
            raise ValueError("empty generation")
        usage = payload.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        return (
            content,
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
            str(payload.get("model") or fallback_model),
        )

    def _parse_responses_payload(
        self, payload: dict[str, Any], *, fallback_model: str
    ) -> tuple[str, int, int, str]:
        if payload.get("status") in {"incomplete", "failed", "cancelled", "in_progress", "queued"}:
            raise ValueError("incomplete generation")
        for item in payload.get("output") or []:
            if isinstance(item, dict) and any(
                isinstance(block, dict) and block.get("type") == "refusal"
                for block in item.get("content") or []
            ):
                raise ValueError("refused generation")
        content = _extract_responses_output_text(payload)
        if not content.strip():
            raise ValueError("empty generation")
        usage = payload.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        return (
            content,
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            str(payload.get("model") or fallback_model),
        )

    def status(self) -> ProviderStatus:
        if not self._api_key:
            return ProviderStatus(
                name=self.name,
                kind=self.kind,
                health=ProviderHealth.UNAVAILABLE,
                using_fallback=not self._fail_closed,
                is_mock=False,
                detail=(
                    "OPENAI_API_KEY not configured."
                    if self._fail_closed
                    else "OPENAI_API_KEY not configured — using mock-llm fallback."
                ),
            )

        listing_ok, listing_detail = self._probe_model_listing()
        if not listing_ok:
            return ProviderStatus(
                name=self.name,
                kind=self.kind,
                health=(
                    ProviderHealth.UNAVAILABLE if self._fail_closed else ProviderHealth.DEGRADED
                ),
                using_fallback=not self._fail_closed,
                is_mock=False,
                detail=listing_detail,
            )

        generation_ok, generation_detail = self._probe_generation_cached()
        api_label = "responses" if self.uses_responses_api() else "chat_completions"
        if generation_ok:
            return ProviderStatus(
                name=self.name,
                kind=self.kind,
                health=ProviderHealth.HEALTHY,
                using_fallback=False,
                is_mock=False,
                detail=(f"OpenAI LLM generation ready ({self._default_model}, {api_label})."),
            )
        # Listing works but generation fails — do not report healthy.
        return ProviderStatus(
            name=self.name,
            kind=self.kind,
            health=ProviderHealth.UNAVAILABLE if self._fail_closed else ProviderHealth.DEGRADED,
            using_fallback=not self._fail_closed,
            is_mock=False,
            detail=(
                f"OpenAI model listing OK but generation unavailable "
                f"({self._default_model}, {api_label}): {generation_detail}."
            ),
            error_message=generation_detail,
        )

    def _probe_model_listing(self) -> tuple[bool, str]:
        try:
            with OpenAI(
                api_key=self._api_key, base_url=self._base_url, max_retries=0, timeout=5.0
            ) as client:
                payload = client.models.list().model_dump(mode="json")
            data = payload.get("data")
            if (
                not isinstance(data, list)
                or not data
                or not all(
                    isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"]
                    for row in data
                )
            ):
                return False, "OpenAI models endpoint returned malformed data."
            return True, "OpenAI models endpoint reachable."
        except Exception as exc:
            reason = _sanitize_openai_http_error(exc)["reason"]
            return False, f"OpenAI models endpoint unavailable ({reason})."

    def _probe_generation_cached(self) -> tuple[bool, str]:
        """Return cached generation readiness; coalesce concurrent probes.

        Public status polling must not fan out into unbounded OpenAI spend:
        TTL cache + single-flight condition keep at most one probe in flight.
        """
        with self._probe_cond:
            while True:
                now = time.monotonic()
                cached = self._probe_cache
                if cached is not None and self._probe_ttl > 0 and now - cached[0] < self._probe_ttl:
                    return cached[1], cached[2]
                if not self._probe_in_flight:
                    self._probe_in_flight = True
                    break
                # Another thread owns the probe; wait for cache or completion.
                self._probe_cond.wait(timeout=max(_GENERATION_PROBE_TIMEOUT_SECONDS, 1.0))

        try:
            ok, detail = self._probe_generation()
            with self._probe_cond:
                self._probe_cache = (time.monotonic(), ok, detail)
                self._probe_in_flight = False
                self._probe_cond.notify_all()
            return ok, detail
        except Exception:
            with self._probe_cond:
                self._probe_in_flight = False
                self._probe_cond.notify_all()
            raise

    def _probe_generation(self) -> tuple[bool, str]:
        """Cheap generation probe used only by ``status()`` (not agent chat)."""
        request = LLMCompletionRequest(
            messages=[LLMMessage(role="user", content=_GENERATION_PROBE_PROMPT)],
            model=self._default_model,
            temperature=0.0,
            max_tokens=_GENERATION_PROBE_MAX_OUTPUT_TOKENS,
        )
        model = self._default_model
        try:
            if model_requires_responses_api(model):
                payload = self._post_responses(
                    request,
                    model=model,
                    reasoning_effort="low",
                    timeout_seconds=_GENERATION_PROBE_TIMEOUT_SECONDS,
                    max_retries=_GENERATION_PROBE_MAX_RETRIES,
                )
                content, _, _, _ = self._parse_responses_payload(payload, fallback_model=model)
            else:
                payload = self._post_chat_completions(
                    request,
                    model=model,
                    timeout_seconds=_GENERATION_PROBE_TIMEOUT_SECONDS,
                    max_retries=_GENERATION_PROBE_MAX_RETRIES,
                )
                content, _, _, _ = self._parse_chat_completions_payload(
                    payload, fallback_model=model
                )
        except Exception as exc:
            details = _sanitize_openai_http_error(exc)
            logger.warning(
                "openai_llm_generation_probe_failed",
                error=type(exc).__name__,
                reason=details.get("reason"),
                http_status=details.get("http_status"),
            )
            return False, str(details.get("reason") or "generation_failed")
        if not content.strip():
            return False, "empty_generation"
        return True, "generation_ok"


def _split_messages_for_responses(
    messages: list[LLMMessage],
) -> tuple[str | None, list[dict[str, str]]]:
    """Map chat messages to Responses ``instructions`` + ``input`` items."""
    instructions_parts: list[str] = []
    input_items: list[dict[str, str]] = []
    for message in messages:
        role = message.role.strip().lower()
        content = message.content
        if role in {"system", "developer"}:
            instructions_parts.append(content)
            continue
        mapped_role = "assistant" if role == "assistant" else "user"
        input_items.append({"role": mapped_role, "content": content})
    if not input_items:
        input_items = [{"role": "user", "content": " "}]
    instructions = "\n\n".join(part for part in instructions_parts if part.strip()) or None
    return instructions, input_items


def _map_response_format(response_format: dict[str, Any]) -> dict[str, Any]:
    """Map Chat Completions ``response_format`` to Responses ``text.format``."""
    fmt_type = response_format.get("type")
    if fmt_type == "json_object":
        return {"type": "json_object"}
    if fmt_type == "json_schema":
        # Pass through schema payload when already Responses-shaped or nested.
        if "schema" in response_format:
            return {
                "type": "json_schema",
                "name": str(response_format.get("name") or "response"),
                "schema": response_format["schema"],
                "strict": bool(response_format.get("strict", True)),
            }
        nested = response_format.get("json_schema")
        if isinstance(nested, dict):
            return {
                "type": "json_schema",
                "name": str(nested.get("name") or "response"),
                "schema": nested.get("schema") or {},
                "strict": bool(nested.get("strict", True)),
            }
    return {"type": "text"}


def _extract_responses_output_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    parts: list[str] = []
    output = payload.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in {"output_text", "text"}:
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


def _try_parse_json(content: str) -> dict[str, Any] | None:
    text = content.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def validate_llm_json(
    parsed: dict[str, Any] | None,
    *,
    required_fields: tuple[str, ...] = ("summary",),
) -> bool:
    """Reject malformed LLM JSON before it enters agent state."""
    if parsed is None:
        return False
    return all(
        isinstance(parsed.get(name), str) and parsed[name].strip() for name in required_fields
    )


def _usage_tokens(usage: Any, *keys: str) -> int:
    if not isinstance(usage, dict):
        return 0
    for key in keys:
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return 0
