"""Tests for integration setup, services and diagnostics."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY, CONF_PROMPT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.nous_research import DOMAIN
from custom_components.nous_research.sensor import _price_per_mtok
from custom_components.nous_research.const import (
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_COST_SENSORS,
    DEFAULT_BASE_URL,
    SERVICE_RESET_USAGE,
)
from custom_components.nous_research.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.nous_research.sensor import _async_remove_stale_entities
from pytest_homeassistant_custom_component.common import MockConfigEntry

MODEL_ID = "nousresearch/hermes-4-405b"

CATALOG = [
    {
        "id": MODEL_ID,
        "pricing": {"prompt": "0.0000003", "completion": "0.0000012"},
        "supported_parameters": ["tools"],
        "context_length": 128000,
    }
]


class FakeClient:
    """Minimal client used by the coordinator."""

    rate_limits: dict[str, Any] = {}

    def __init__(self, hass: HomeAssistant, api_key: str, base_url: str) -> None:
        self.hass = hass
        self.api_key = api_key
        self.base_url = base_url

    async def async_list_models(self) -> list[dict[str, Any]]:
        return CATALOG

    async def close(self) -> None:
        return None


@pytest.fixture
async def setup_integration(hass: HomeAssistant) -> MockConfigEntry:
    """Set up the integration with a mocked API client."""
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_API_KEY: "sk-test-123",
            CONF_BASE_URL: DEFAULT_BASE_URL,
            CONF_CHAT_MODEL: MODEL_ID,
        },
        options={
            CONF_PROMPT: "You are a helpful assistant.",
            CONF_COST_SENSORS: True,
            CONF_BASE_URL: DEFAULT_BASE_URL,
            CONF_CHAT_MODEL: MODEL_ID,
        },
        title="Nous Research",
    )
    entry.add_to_hass(hass)
    with patch("custom_components.nous_research.NousClient", FakeClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_setup_and_unload(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The entry loads its platforms, data and services and unloads cleanly."""
    entry = setup_integration
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.client is not None
    assert entry.runtime_data.tracker is not None
    assert entry.runtime_data.catalog.data[0]["id"] == MODEL_ID

    assert hass.states.get("conversation.nous_research") is not None
    assert hass.states.get("ai_task.nous_research") is not None
    assert hass.states.get("sensor.nous_research_cost_total") is not None

    assert hass.services.has_service(DOMAIN, SERVICE_RESET_USAGE)
    assert not hass.services.has_service(DOMAIN, "set_balance")
    assert hass.states.get("sensor.nous_research_balance") is None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not hass.services.has_service(DOMAIN, SERVICE_RESET_USAGE)


async def test_services(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    """reset_usage clears the tracker through the service layer."""
    entry = setup_integration
    tracker = entry.runtime_data.tracker

    await tracker.async_record(
        MODEL_ID,
        None,
    )
    assert tracker.total_cost == 0.0
    await tracker.async_reset()
    await hass.services.async_call(DOMAIN, SERVICE_RESET_USAGE, {}, blocking=True)
    assert tracker.total_requests == 0


async def test_diagnostics_redacts_key(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Diagnostics hide the API key and expose tracker totals."""
    entry = setup_integration
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"][CONF_API_KEY] == "**REDACTED**"
    assert "error" not in diag["tracker"]
    assert diag["tracker"]["totals"]["requests"] == 0
    assert diag["tracker"]["totals"]["cost"] == 0.0
    assert isinstance(diag["tracker"]["top_models"], list)
    assert diag["catalog"]["models"] == 1
    assert diag["client"]["base_url"] == DEFAULT_BASE_URL

async def test_stale_sensor_registry_row_is_removed(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """A registry row for a sensor this version dropped is cleaned up."""
    entry = setup_integration
    registry = er.async_get(hass)
    stale = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_balance",
        suggested_object_id="nous_research_balance",
        config_entry=entry,
    )
    assert registry.async_get(stale.entity_id) is not None

    _async_remove_stale_entities(hass, entry)

    assert registry.async_get(stale.entity_id) is None
    # Live sensors of this version survive the sweep.
    assert registry.async_get("sensor.nous_research_cost_total") is not None


@pytest.mark.parametrize(
    ("per_token", "expected", "rendered"),
    [
        ("0.00000003", 0.03, "0.03"),  # official portal: $0.03 / 1M input
        ("0.00000012", 0.12, "0.12"),  # official portal: $0.12 / 1M output
        ("0.0000003", 0.3, "0.3"),
        ("0.00000005", 0.05, "0.05"),
        ("0.0000012", 1.2, "1.2"),
        ("0.000002", 2.0, "2.0"),
    ],
)
def test_price_per_mtok_rounding(
    per_token: str, expected: float, rendered: str
) -> None:
    """Per-token prices are converted to clean per-1M values, no padding zeros."""
    price = _price_per_mtok(per_token)
    assert price == pytest.approx(expected)
    assert str(price) == rendered


async def test_selected_model_price_sensors(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Input and output prices are exposed as separate, translated sensors."""
    inp = hass.states.get("sensor.nous_research_selected_model_input_price")
    out = hass.states.get("sensor.nous_research_selected_model_output_price")
    assert inp is not None and out is not None
    assert inp.name == "Nous Research Selected model input price"
    assert out.name == "Nous Research Selected model output price"
    assert float(inp.state) == pytest.approx(0.3)  # 0.0000003 USD per token
    assert float(out.state) == pytest.approx(1.2)  # 0.0000012 USD per token
    # Rendered without padding zeros ("0.3", never "0.3000").
    assert inp.state == "0.3"
    assert out.state == "1.2"
    assert inp.attributes["input_per_mtok"] == pytest.approx(0.3)
    assert inp.attributes["output_per_mtok"] == pytest.approx(1.2)
    assert out.attributes["model"] == "nousresearch/hermes-4-405b"
    # The older combined price sensor stays gone.
    assert hass.states.get("sensor.nous_research_selected_model_price") is None
