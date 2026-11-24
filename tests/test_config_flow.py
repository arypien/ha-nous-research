"""Tests for the Nous Research config flow."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_PROMPT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

from custom_components.nous_research import config_flow as cf
from custom_components.nous_research.api import (
    NousApiError,
    NousAuthError,
    NousConnectionError,
    NousPaymentRequiredError,
    NousRateLimitError,
)
from custom_components.nous_research.const import (
    CONF_BASE_URL,
    CONF_CATALOG_INTERVAL,
    CONF_CHAT_MODEL,
    CONF_COST_SENSORS,
    CONF_MAX_TOKENS,
    CONF_REASONING_MODE,
    CONF_TEMPERATURE,
    DEFAULT_BASE_URL,
    DOMAIN,
)

#: Mirrors the aggregated, namespaced catalog the API really returns.
CATALOG = [
    {"id": "vendor/embed-1", "architecture": {"output_modalities": ["text"]}},
    {"id": "vendor/model-b:batch"},
    {"id": "vendor/model-a", "architecture": {"output_modalities": ["text"]}},
]

CHAT_MODEL = "vendor/model-a"


class FakeClient:
    """Stand-in for NousClient used by the config flow."""

    error: Exception | None = None
    models: list[dict[str, Any]] = CATALOG

    def __init__(self, hass: HomeAssistant, api_key: str, base_url: str) -> None:
        self.hass = hass
        self.api_key = api_key
        self.base_url = base_url

    async def async_list_models(self) -> list[dict[str, Any]]:
        error = type(self).error
        if error is not None:
            raise error
        return self.models

    async def async_validate(self, model: str) -> None:
        error = type(self).error
        if error is not None:
            raise error


@pytest.fixture(autouse=True)
async def setup_homeassistant(hass: HomeAssistant) -> None:
    """Load the integrations our config flow depends on."""
    assert await async_setup_component(hass, "homeassistant", {})
    await hass.async_block_till_done()


@pytest.fixture(autouse=True)
def fake_client() -> Any:
    """Patch the client used by the flow."""
    with patch.object(cf, "NousClient", FakeClient):
        FakeClient.error = None
        FakeClient.models = CATALOG
        yield


def _credentials(**overrides: Any) -> dict[str, Any]:
    data = {
        CONF_API_KEY: "sk-test-123",
        CONF_BASE_URL: DEFAULT_BASE_URL,
    }
    data.update(overrides)
    return data


async def _async_start(hass: HomeAssistant) -> dict[str, Any]:
    """Start the flow and submit valid credentials."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], _credentials()
    )


async def test_form_shown(hass: HomeAssistant) -> None:
    """The initial step renders the credentials form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_model_step_follows_credentials(hass: HomeAssistant) -> None:
    """Valid credentials lead to the catalog driven model step."""
    result = await _async_start(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "model"
    selector = next(iter(result["data_schema"].schema.values()))
    values = [option["value"] for option in selector.config["options"]]
    assert CHAT_MODEL in values
    # Batch endpoints are useless for chat and stay out of the list.
    assert "vendor/model-b:batch" not in values


async def test_default_model_skips_embeddings(hass: HomeAssistant) -> None:
    """An empty answer falls back to the first chat capable model."""
    started = await _async_start(hass)
    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], {CONF_CHAT_MODEL: ""}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CHAT_MODEL] == CHAT_MODEL


async def test_successful_setup(hass: HomeAssistant) -> None:
    """A valid key and a picked model create the entry with defaults."""
    started = await _async_start(hass)
    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], {CONF_CHAT_MODEL: CHAT_MODEL}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_KEY] == "sk-test-123"
    assert result["data"][CONF_CHAT_MODEL] == CHAT_MODEL

    options = result["options"]
    assert options[CONF_PROMPT]
    assert options[CONF_MAX_TOKENS] > 0
    assert 0 <= options[CONF_TEMPERATURE] <= 2
    assert options[CONF_REASONING_MODE]
    assert options[CONF_COST_SENSORS] is True
    assert options[CONF_CATALOG_INTERVAL] > 0

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == cf._unique_id("sk-test-123")


@pytest.mark.parametrize(
    ("overrides", "field", "error"),
    [
        ({CONF_API_KEY: "  "}, CONF_API_KEY, "invalid_auth"),
        ({CONF_BASE_URL: "ftp://nous"}, CONF_BASE_URL, "invalid_url"),
    ],
)
async def test_credentials_validation(
    hass: HomeAssistant, overrides: dict[str, Any], field: str, error: str
) -> None:
    """Local validation errors are reported on the right field."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _credentials(**overrides)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {field: error}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_empty_catalog_blocks_setup(hass: HomeAssistant) -> None:
    """Without a catalog there is nothing to pick from."""
    FakeClient.models = []
    result = await _async_start(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (NousAuthError("boom"), "invalid_auth"),
        (NousPaymentRequiredError("boom"), "no_credits"),
        (NousRateLimitError("boom"), "rate_limited"),
        (NousConnectionError("boom"), "cannot_connect"),
        (NousApiError(500, "boom"), "cannot_connect"),
    ],
)
async def test_api_errors_are_mapped(
    hass: HomeAssistant, exc: Exception, error: str
) -> None:
    """API failures surface as translated form errors."""
    started = await _async_start(hass)
    assert started["step_id"] == "model"

    FakeClient.error = exc
    result = await hass.config_entries.flow.async_configure(
        started["flow_id"], {CONF_CHAT_MODEL: CHAT_MODEL}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "model"
    assert result["errors"] == {"base": error}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_duplicate_key_aborts(hass: HomeAssistant) -> None:
    """The same API key cannot be added twice."""
    first = await _async_start(hass)
    await hass.config_entries.flow.async_configure(
        first["flow_id"], {CONF_CHAT_MODEL: CHAT_MODEL}
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _credentials()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CHAT_MODEL: CHAT_MODEL}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
