"""Tests for the Nous Research API client."""

from __future__ import annotations


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
