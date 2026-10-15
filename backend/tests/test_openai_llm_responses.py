"""Official SDK transport, output validation, budgets and sanitized health."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import httpx2
import pytest
from openai import OpenAI

from app.core.errors import ServiceUnavailableError
from app.providers.base import ProviderHealth
from app.providers.llm import (
    LLMCompletionRequest,
    LLMMessage,
    OpenAILLMProvider,
    model_requires_responses_api,
)


@pytest.fixture
def sdk_transport():
    calls, replies = [], []

    def handler(request):
        calls.append(request)
        reply = replies.pop(0) if len(replies) > 1 else replies[0]
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            return reply(request)
        status, payload = reply
        return httpx2.Response(status, json=payload)

    def sdk(**kwargs):
        assert kwargs["max_retries"] == 0
        assert kwargs["timeout"] is not None
        return OpenAI(**kwargs, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))

    with patch("app.providers.llm.OpenAI", side_effect=sdk):
        yield calls, replies


def provider(**kwargs):
    return OpenAILLMProvider(
        api_key="fixture-key",
        base_url="https://fixture.invalid/v1",
        model="gpt-6-astra",
        fail_closed=True,
        **kwargs,
    )


def request(**kwargs):
    return LLMCompletionRequest(
        messages=[LLMMessage("system", "Be brief."), LLMMessage("user", "OK")],
        model=kwargs.pop("model", "gpt-6-astra"),
        **kwargs,
    )


def response(**kwargs):
    return {
        "id": "resp_fixture",
        "object": "response",
        "status": "completed",
        "model": "gpt-6-astra",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "OK", "annotations": []}],
            }
        ],
        "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        **kwargs,
    }


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-6-astra", "gpt-6.1-sol", "o3-mini"])
def test_responses_routing(model):
    assert model_requires_responses_api(model)


def test_responses_sdk_budget_and_usage(sdk_transport):
    calls, replies = sdk_transport
    replies.append(
        (
            200,
            response(
                output=[
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": '{"summary":"OK"}', "annotations": []}
                        ],
                    }
                ]
            ),
        )
    )
    result = provider().complete(
        request(
            max_tokens=25000,
            reasoning_effort="high",
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "fixture", "schema": {"type": "object"}, "strict": True},
            },
        )
    )
    body = json.loads(calls[0].content)
    assert calls[0].url.path == "/v1/responses"
    assert body["model"] == "gpt-6-astra"
    assert body["reasoning"] == {"effort": "high"}
    assert body["max_output_tokens"] == 25000 and body["store"] is False
    assert "temperature" not in body and body["instructions"] == "Be brief."
    assert body["text"]["format"]["name"] == "fixture"
    assert (result.parsed_json, result.input_tokens, result.output_tokens) == (
        {"summary": "OK"},
        3,
        2,
    )


def test_classic_chat_still_supported(sdk_transport):
    calls, replies = sdk_transport
    replies.append(
        (
            200,
            {
                "id": "chat_fixture",
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "chat reply"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            },
        )
    )
    result = provider().complete(request(model="gpt-4o-mini", max_tokens=33))
    assert result.content == "chat reply" and result.input_tokens == 4
    assert calls[0].url.path == "/v1/chat/completions"
    assert json.loads(calls[0].content)["max_tokens"] == 33


@pytest.mark.parametrize(
    "status,reason",
    [
        (401, "permission_denied"),
        (403, "permission_denied"),
        (404, "model_unavailable"),
        (429, "rate_limited"),
        (500, "upstream_error"),
    ],
)
def test_sdk_errors_are_sanitized_and_never_hide_retries(sdk_transport, status, reason):
    calls, replies = sdk_transport
    replies.append(
        (
            status,
            {"error": {"message": "sensitive payload fixture-key", "code": "sensitive-payload"}},
        )
    )
    with pytest.raises(ServiceUnavailableError) as err:
        provider().complete(request())
    assert err.value.details["reason"] == "openai_llm_" + reason
    assert "sensitive" not in str(err.value.details)
    assert "fixture-key" not in str(err.value.details)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        response(status="incomplete"),
        response(output=[]),
        response(
            output=[
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "refusal", "refusal": "private refusal"}],
                }
            ]
        ),
    ],
)
def test_incomplete_refusal_and_empty_output_preserve_billed_usage(sdk_transport, payload):
    _, replies = sdk_transport
    replies.append((200, payload))
    with pytest.raises(ServiceUnavailableError) as err:
        provider().complete(request())
    assert err.value.details["input_tokens"] == 3
    assert err.value.details["output_tokens"] == 2
    assert "private refusal" not in str(err.value.details)


def test_sdk_timeout_is_one_attempt(sdk_transport):
    calls, replies = sdk_transport
    replies.append(httpx2.ReadTimeout("sensitive timeout"))
    with pytest.raises(ServiceUnavailableError) as err:
        provider(timeout_seconds=0.1).complete(request())
    assert err.value.details["reason"] == "openai_llm_timeout"
    assert len(calls) == 1


def test_generation_health_is_cached_and_coalesced(sdk_transport):
    calls, replies = sdk_transport
    entered, release = Event(), Event()

    def handle(req):
        if req.url.path.endswith("/models"):
            return httpx2.Response(200, json={"data": [{"id": "gpt-6-astra"}]})
        entered.set()
        assert release.wait(3)
        return httpx2.Response(200, json=response())

    replies.append(handle)
    item = provider()
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [pool.submit(item.status) for _ in range(4)]
        assert entered.wait(2)
        release.set()
        assert all(job.result().health == ProviderHealth.HEALTHY for job in jobs)
    assert len([c for c in calls if c.url.path.endswith("/responses")]) == 1


def test_models_reachable_does_not_prove_generation(sdk_transport):
    _, replies = sdk_transport
    replies.extend([(200, {"data": [{"id": "gpt-6-astra"}]}), (403, {"error": {}})])
    assert provider().status().health == ProviderHealth.UNAVAILABLE


def test_malformed_structured_output_retains_usage(sdk_transport):
    _, replies = sdk_transport
    replies.append((200, response()))
    with pytest.raises(ServiceUnavailableError) as err:
        provider().complete(request(response_format={"type": "json_object"}))
    assert err.value.details["input_tokens"] == 3
