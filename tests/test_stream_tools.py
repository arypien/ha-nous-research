"""Streaming tests: tool call assembly."""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant

from custom_components.nous_research.api import NousClient
from custom_components.nous_research.const import DEFAULT_BASE_URL

CHAT_URL = f"{DEFAULT_BASE_URL}/chat/completions"

SSE_TOOLS = (
    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
    '"function":{"name":"HassTurnOn","arguments":"{\\"name\\":"}}]}}]}\n\n'
    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
    '"function":{"arguments":" \\"kitchen\\"}"}}]}}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}],'
    '"usage":{"prompt_tokens":5,"completion_tokens":9}}\n\n'
)


@pytest.fixture
def client(hass: HomeAssistant) -> NousClient:
    """Return a client bound to the mocked session."""
    return NousClient(hass, "sk-test-123", DEFAULT_BASE_URL)


async def test_stream_tool_calls(
    hass: HomeAssistant, aioclient_mock, client: NousClient
) -> None:
    """Fragmented tool call arguments are joined per index."""
    aioclient_mock.post(
        CHAT_URL, text=SSE_TOOLS, headers={"Content-Type": "text/event-stream"}
    )
    events = [event async for event in client.async_chat_stream("Hermes-4-70B", [])]
    calls = [e for e in events if e["type"] == "tool_calls"][0]["tool_calls"]
    assert len(calls) == 1
    assert calls[0].id == "call_1"
    assert calls[0].name == "HassTurnOn"
    assert calls[0].parsed_arguments() == {"name": "kitchen"}

    usage = events[-1]
    assert usage["type"] == "usage"
    assert usage["finish_reason"] == "tool_calls"
    assert usage["usage"].prompt_tokens == 5
    assert usage["usage"].completion_tokens == 9
