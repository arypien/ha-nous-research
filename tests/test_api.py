"""Tests for the Nous Research API client."""

from __future__ import annotations


import json

import pytest

from homeassistant.core import HomeAssistant

from custom_components.nous_research.api import (
    NousApiError,
    NousAuthError,
    NousClient,
    NousPaymentRequiredError,
    NousRateLimitError,
)
from custom_components.nous_research.const import DEFAULT_BASE_URL

BASE = DEFAULT_BASE_URL
MODELS_URL = f"{BASE}/models"
CHAT_URL = f"{BASE}/chat/completions"


@pytest.fixture
def client(hass: HomeAssistant) -> NousClient:
    """Return a client bound to the mocked session."""
    return NousClient(hass, "sk-test-123", BASE)


async def test_list_models(hass: HomeAssistant, aioclient_mock, client: NousClient) -> None:
    """The catalog is parsed from the API response."""
    aioclient_mock.get(
        MODELS_URL, json={"data": [{"id": "Hermes-4-70B"}, {"id": "other"}, "junk"]}
    )
    models = await client.async_list_models()
    assert [model["id"] for model in models] == ["Hermes-4-70B", "other"]
    assert aioclient_mock.mock_calls[0][0] == "GET"


@pytest.mark.parametrize(
    ("status", "exc"),
    [
        (401, NousAuthError),
        (403, NousAuthError),
        (402, NousPaymentRequiredError),
        (429, NousRateLimitError),
        (500, NousApiError),
        (404, NousApiError),
    ],
)
async def test_list_models_errors(
    hass: HomeAssistant, aioclient_mock, client: NousClient, status: int, exc: type[Exception]
) -> None:
    """HTTP failures map to typed exceptions."""
    aioclient_mock.get(MODELS_URL, status=status, json={"error": {"message": "nope"}})
    with pytest.raises(exc):
        await client.async_list_models()


async def test_invalid_catalog_json(hass: HomeAssistant, aioclient_mock, client: NousClient) -> None:
    """A non JSON catalog is reported as an API error."""
    aioclient_mock.get(MODELS_URL, text="<html>oops</html>")
    with pytest.raises(NousApiError):
        await client.async_list_models()


async def test_chat_completion(hass: HomeAssistant, aioclient_mock, client: NousClient) -> None:
    """A non streaming completion returns text, usage and tool calls."""
    aioclient_mock.post(
        CHAT_URL,
        json={
            "model": "Hermes-4-70B",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Hello there",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "HassTurnOn",
                                    "arguments": '{"name": "kitchen"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 7,
                "total_tokens": 19,
                "prompt_tokens_details": {"cached_tokens": 4},
            },
        },
    )
    result = await client.async_chat("Hermes-4-70B", [{"role": "user", "content": "hi"}])
    assert result.text == "Hello there"
    assert result.model == "Hermes-4-70B"
    assert result.finish_reason == "stop"
    assert result.usage is not None and result.usage.cached_tokens == 4
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].parsed_arguments() == {"name": "kitchen"}


async def test_chat_request_carries_the_gateway_tags(
    hass: HomeAssistant, aioclient_mock, client: NousClient
) -> None:
    """Without a ``user=`` tag the gateway answers 400 "missing tags" and no tokens."""
    aioclient_mock.post(
        CHAT_URL,
        json={"model": "upstage/solar-mini4:free", "choices": [{"message": {"content": "p"}}]},
    )

    await client.async_chat(
        "upstage/solar-mini4:free", [{"role": "user", "content": "hi"}], max_tokens=1
    )

    body = aioclient_mock.mock_calls[0][2]
    assert isinstance(body, dict)
    assert body["tags"] == ["user=home-assistant"]
    assert body["model"] == "upstage/solar-mini4:free"
    assert body["max_tokens"] == 1
