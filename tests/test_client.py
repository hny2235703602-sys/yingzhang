import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from openai import OpenAI

from novel_anime.core.errors import AppError
from novel_anime.domain.models import ModelProfile, Review
from novel_anime.integrations.language_models import client as module
from novel_anime.integrations.language_models.client import LLMClient


def profile(protocol: str = "chat_completions", **overrides: Any) -> ModelProfile:
    return ModelProfile.model_validate(
        {
            "provider": "test",
            "protocol": protocol,
            "base_url": "https://mock.test/v1",
            "model": "example",
            **overrides,
        }
    )


def chat(text: str = '{"issues": []}', finish: str = "stop", **extra: Any) -> dict[str, Any]:
    return {
        "id": "chat-1",
        "object": "chat.completion",
        "created": 1,
        "model": "actual",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": {"role": "assistant", "content": text, **extra},
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
    }


def install(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def factory(**kwargs: Any) -> OpenAI:
        calls.append(kwargs.copy())
        return OpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setenv("NOVEL_LLM_API_KEY", "secret-sentinel")
    monkeypatch.setattr(module, "OpenAI", factory)
    monkeypatch.setattr(module.time, "sleep", lambda delay: None)
    return calls


def test_chat_schema_instruction_usage_and_no_sensitive_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=chat(), headers={"x-request-id": "req-1"})

    calls = install(monkeypatch, handler)
    with caplog.at_level(logging.INFO):
        result, metadata = LLMClient(profile()).generate(
            Review, "审阅", {"text": "private-novel-sentinel"}, "review"
        )
    assert isinstance(result, Review)
    assert metadata["usage"]["total_tokens"] == 25
    assert metadata["model"] == "actual"
    assert metadata["request_id"] == "req-1"
    assert calls[0]["max_retries"] == 0
    assert "JSON Schema" in requests[0]["messages"][0]["content"]
    assert "response_format" not in requests[0]
    assert "private-novel-sentinel" not in caplog.text
    assert "secret-sentinel" not in caplog.text
    assert "secret-sentinel" not in json.dumps(metadata)


def test_unlimited_output_omits_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=chat())

    install(monkeypatch, handler)
    LLMClient(profile(max_output_tokens=0, context_budget=0)).generate(Review, "审阅", {}, "review")
    assert "max_tokens" not in requests[0]


def test_responses_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/v1/responses"
        assert body["max_output_tokens"] == 4000
        assert "messages" not in body
        return httpx.Response(
            200,
            json={
                "id": "resp-1",
                "object": "response",
                "created_at": 1,
                "model": "actual",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "msg-1",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": '{"issues": []}', "annotations": []}
                        ],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
            },
        )

    install(monkeypatch, handler)
    result, metadata = LLMClient(profile("responses")).generate(Review, "test", {}, "test")
    assert result == Review()
    assert metadata["usage"]["input_tokens"] == 10


@pytest.mark.parametrize(
    "status,code",
    [(401, "llm_auth"), (403, "llm_auth"), (400, "llm_configuration"), (404, "llm_configuration")],
)
def test_permanent_errors_do_not_retry(
    monkeypatch: pytest.MonkeyPatch, status: int, code: str
) -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(status, json={"error": {"message": "secret-sentinel"}})

    install(monkeypatch, handler)
    with pytest.raises(AppError) as caught:
        LLMClient(profile()).generate(Review, "test", {}, "test")
    assert caught.value.code == code
    assert "secret" not in str(caught.value)
    assert count == 1


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retry_budget_and_retry_after(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    count = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(
            status, json={"error": {"message": "busy"}}, headers={"Retry-After": "2"}
        )

    install(monkeypatch, handler)
    monkeypatch.setattr(module.time, "sleep", delays.append)
    with pytest.raises(AppError):
        LLMClient(profile(retries=2)).generate(Review, "test", {}, "test")
    assert count == 3
    assert delays == [2, 2]


def test_quota_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(429, json={"error": {"code": "insufficient_quota"}})

    install(monkeypatch, handler)
    with pytest.raises(AppError, match="配额"):
        LLMClient(profile()).generate(Review, "test", {}, "test")
    assert count == 1


def test_timeout_does_not_resend(monkeypatch: pytest.MonkeyPatch) -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        raise httpx.ReadTimeout("sensitive", request=request)

    install(monkeypatch, handler)
    with pytest.raises(AppError) as caught:
        LLMClient(profile()).generate(Review, "test", {}, "test")
    assert caught.value.code == "llm_timeout"
    assert count == 1


@pytest.mark.parametrize(
    "finish,extra,code",
    [
        ("length", {}, "llm_truncated"),
        ("stop", {"refusal": "no"}, "llm_refusal"),
        ("content_filter", {}, "llm_refusal"),
    ],
)
def test_truncation_and_refusal(
    monkeypatch: pytest.MonkeyPatch, finish: str, extra: dict[str, Any], code: str
) -> None:
    install(monkeypatch, lambda request: httpx.Response(200, json=chat(finish=finish, **extra)))
    with pytest.raises(AppError) as caught:
        LLMClient(profile()).generate(Review, "test", {}, "test")
    assert caught.value.code == code


@pytest.mark.parametrize("bad", ["not json", '{"issues": 3}', '{"extra": true}'])
def test_output_repair_once(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(200, json=chat(bad if count == 1 else '{"issues": []}'))

    install(monkeypatch, handler)
    _, metadata = LLMClient(profile()).generate(Review, "test", {}, "test")
    assert count == 2
    assert metadata["repairs"] == 1
    assert len(metadata["attempts"]) == 2


def test_invalid_output_repair_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(200, json=chat("not json"))

    install(monkeypatch, handler)
    with pytest.raises(AppError) as caught:
        LLMClient(profile()).probe()
    assert caught.value.code == "llm_invalid_output"
    assert count == 2


def test_local_profile_uses_placeholder_without_mutating_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = install(monkeypatch, lambda request: httpx.Response(200, json=chat()))
    monkeypatch.delenv("NOVEL_LLM_API_KEY", raising=False)
    assert LLMClient(profile(api_key_env="")).probe()["ok"]
    assert calls[0]["api_key"] == "lm-studio"
    import os

    assert "NOVEL_LLM_API_KEY" not in os.environ


@pytest.mark.parametrize(
    "reason,code",
    [
        ("max_output_tokens", "llm_truncated"),
        ("content_filter", "llm_refusal"),
    ],
)
def test_responses_incomplete_classification(
    monkeypatch: pytest.MonkeyPatch, reason: str, code: str
) -> None:
    install(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "id": "r",
                "object": "response",
                "created_at": 1,
                "model": "m",
                "status": "incomplete",
                "output": [],
                "incomplete_details": {"reason": reason},
            },
        ),
    )
    with pytest.raises(AppError) as caught:
        LLMClient(profile("responses")).probe()
    assert caught.value.code == code


def test_retries_shared_across_json_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        if count in {1, 3}:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return httpx.Response(200, json=chat("bad json"))

    install(monkeypatch, handler)
    with pytest.raises(AppError) as caught:
        LLMClient(profile(retries=1)).probe()
    assert caught.value.code == "llm_unavailable"
    assert count == 3


def test_structured_output_unsupported_is_not_silently_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        assert json.loads(request.content)["response_format"]["type"] == "json_schema"
        return httpx.Response(400, json={"error": {"message": "unsupported"}})

    install(monkeypatch, handler)
    with pytest.raises(AppError) as caught:
        LLMClient(profile(structured_output=True)).probe()
    assert caught.value.code == "llm_configuration"
    assert count == 1


def test_missing_key_never_constructs_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOVEL_LLM_API_KEY", raising=False)
    with pytest.raises(AppError) as caught:
        LLMClient(profile()).probe()
    assert caught.value.code == "llm_missing_api_key"


def test_model_catalog_uses_models_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "beta", "object": "model", "created": 1, "owned_by": "test"},
                    {"id": "alpha", "object": "model", "created": 1, "owned_by": "test"},
                    {"id": "beta", "object": "model", "created": 1, "owned_by": "test"},
                ],
            },
        )

    install(monkeypatch, handler)
    assert LLMClient(profile()).list_models() == ["alpha", "beta"]


@pytest.mark.parametrize("status,code", [(401, "llm_auth"), (404, "llm_models_unavailable")])
def test_model_catalog_errors_are_safe(
    monkeypatch: pytest.MonkeyPatch, status: int, code: str
) -> None:
    install(
        monkeypatch,
        lambda request: httpx.Response(status, json={"error": {"message": "secret-sentinel"}}),
    )
    with pytest.raises(AppError) as caught:
        LLMClient(profile()).list_models()
    assert caught.value.code == code
    assert "secret-sentinel" not in str(caught.value)


def test_connection_diagnostic_classifies_without_leaking_payload(monkeypatch, caplog):
    def handler(request):
        raise httpx.ReadError("secret-sentinel private novel content", request=request)

    install(monkeypatch, handler)
    caplog.set_level(logging.WARNING)
    with pytest.raises(AppError) as caught:
        LLMClient(profile(retries=0)).generate(Review, "test", {}, "review")
    assert caught.value.code == "llm_connection"
    assert "响应读取中断" in caught.value.message
    assert "secret-sentinel" not in caplog.text + caught.value.message
    assert "private novel content" not in caplog.text + caught.value.message
