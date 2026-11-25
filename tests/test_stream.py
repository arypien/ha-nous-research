"""Streaming tests for the Nous Research API client."""

from __future__ import annotations

import json

import pytest

from homeassistant.core import HomeAssistant

from custom_components.nous_research.api import NousClient
from custom_components.nous_research.const import DEFAULT_BASE_URL

BASE = DEFAULT_BASE_URL
CHAT_URL = f"{BASE}/chat/completions"

SSE = (
    'data: {"model":"Hermes-4-70B","choices":[{"delta":{"content":"Hel"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
    'data: {"choices":[{"delta":{"reasoning_content":"hmm"}}]}\n\n'
)


@pytest.fixture
def client(hass: HomeAssistant) -> NousClient:
    """Return a client bound to the mocked session."""
    return NousClient(hass, "sk-test-123", BASE)


async def test_stream_deltas(hass: HomeAssistant, aioclient_mock, client: NousClient) -> None:
    """Streamed deltas are yielded in order."""
    aioclient_mock.post(CHAT_URL, text=SSE, headers={"Content-Type": "text/event-stream"})
    events = [event async for event in client.async_chat_stream("Hermes-4-70B", [])]
    text = "".join(e["text"] for e in events if e["type"] == "delta")
    thinking = "".join(e.get("thinking") or "" for e in events if e["type"] == "delta")
    assert text == "Hello"
    assert thinking == "hmm"
    assert events[-1]["type"] == "usage"
    # The gateway refuses a streamed call without the user tag as well.
    assert aioclient_mock.mock_calls[0][2]["tags"] == ["user=home-assistant"]
