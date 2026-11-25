"""Tests for the Nous Research config flow."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
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
from custom_components.nous_research.config_flow import CONF_NAME
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
    SUBENTRY_TYPE_MODEL,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

#: Mirrors the aggregated, namespaced catalog the API really returns.
CATALOG = [
    {"id": "vendor/embed-1", "architecture": {"output_modalities": ["text"]}},
    {"id": "vendor/model-b:batch"},
    {"id": "vendor/model-a", "architecture": {"output_modalities": ["text"]}},
    {"id": "vendor/model-c", "architecture": {"output_modalities": ["text"]}},
    {"id": "vendor/model-d", "architecture": {"output_modalities": ["text"]}},
]

CHAT_MODEL = "vendor/model-a"
OTHER_MODEL = "vendor/model-c"
THIRD_MODEL = "vendor/model-d"


class FakeClient:
    """Stand-in for NousClient used by the config flow."""

    error: Exception | None = None
    models: list[dict[str, Any]] = CATALOG
    #: Chat probe failures keyed by model id; the catalog keeps working.
    probe_errors: dict[str, Exception] = {}

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
        error = type(self).probe_errors.get(model) or type(self).error
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
        FakeClient.probe_errors = {}
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


async def test_entry_is_an_api_account(hass: HomeAssistant) -> None:
    """Valid credentials create the entry right away, with no model inside."""
    result = await _async_start(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_API_KEY] == "sk-test-123"
    assert result["data"][CONF_BASE_URL] == DEFAULT_BASE_URL
    assert CONF_CHAT_MODEL not in result["data"]

    options = result["options"]
    assert CONF_CHAT_MODEL not in options
    assert options[CONF_PROMPT]
    assert options[CONF_MAX_TOKENS] > 0
    assert 0 <= options[CONF_TEMPERATURE] <= 2
    assert options[CONF_REASONING_MODE]
    assert options[CONF_COST_SENSORS] is True
    assert options[CONF_CATALOG_INTERVAL] > 0

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == cf._unique_id("sk-test-123")
    assert not entry.subentries


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
    """A key the API does not accept for a catalog call is not stored."""
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
    """API failures surface as translated form errors on the credentials step."""
    FakeClient.error = exc
    result = await _async_start(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_duplicate_key_aborts(hass: HomeAssistant) -> None:
    """The same API key cannot be added twice."""
    first = await _async_start(hass)
    assert first["type"] is FlowResultType.CREATE_ENTRY

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _credentials()
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_legacy_unique_id_aborts(hass: HomeAssistant) -> None:
    """Entries created before 0.1.3 (bare key hash) still block the same key."""
    MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            CONF_API_KEY: "sk-test-123",
            CONF_BASE_URL: DEFAULT_BASE_URL,
            CONF_CHAT_MODEL: CHAT_MODEL,
        },
        title="Legacy",
        unique_id=cf._unique_id("sk-test-123"),
    ).add_to_hass(hass)

    result = await _async_start(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


def _mock_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Add an account entry with no model subentry yet."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_API_KEY: "sk-test-123",
            CONF_BASE_URL: DEFAULT_BASE_URL,
        },
        options={},
        title="Nous Research",
        unique_id=cf._unique_id("sk-test-123"),
    )
    entry.add_to_hass(hass)
    return entry


async def _async_start_subentry(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    *,
    source: str = config_entries.SOURCE_USER,
    subentry_id: str | None = None,
) -> dict[str, Any]:
    """Start the model subentry flow for an entry."""
    context: dict[str, Any] = {"source": source}
    if subentry_id is not None:
        context["subentry_id"] = subentry_id
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_TYPE_MODEL), context=context
    )


async def test_subentry_offers_catalog_models(hass: HomeAssistant) -> None:
    """The model step lists the chat capable models of the catalog."""
    entry = _mock_entry(hass)

    result = await _async_start_subentry(hass, entry)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    selector = next(iter(result["data_schema"].schema.values()))
    values = [option["value"] for option in selector.config["options"]]
    assert CHAT_MODEL in values
    # Batch endpoints are useless for chat and stay out of the list.
    assert "vendor/model-b:batch" not in values


async def test_add_model_subentry(hass: HomeAssistant) -> None:
    """The model subentry flow adds a model to an existing entry."""
    entry = _mock_entry(hass)

    result = await _async_start_subentry(hass, entry)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_CHAT_MODEL: OTHER_MODEL, CONF_NAME: "Fast model"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert len(entry.subentries) == 1
    subentry = next(iter(entry.subentries.values()))
    assert subentry.subentry_type == SUBENTRY_TYPE_MODEL
    assert subentry.data[CONF_CHAT_MODEL] == OTHER_MODEL
    assert subentry.title == "Fast model"
    assert subentry.unique_id == OTHER_MODEL
    # The entry stays model free: the model lives in the subentry only.
    assert CONF_CHAT_MODEL not in entry.data


async def test_subentry_rejects_model_in_use(hass: HomeAssistant) -> None:
    """A model can be added to an entry once."""
    entry = _mock_entry(hass)
    result = await _async_start_subentry(hass, entry)
    await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_CHAT_MODEL: OTHER_MODEL}
    )
    entry = hass.config_entries.async_get_entry(entry.entry_id)

    result = await _async_start_subentry(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_CHAT_MODEL: OTHER_MODEL}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CHAT_MODEL: "model_in_use"}
    assert len(hass.config_entries.async_get_entry(entry.entry_id).subentries) == 1


async def test_subentry_rejects_model_outside_catalog(hass: HomeAssistant) -> None:
    """An id the endpoint does not list is refused, never stored and swapped later."""
    entry = _mock_entry(hass)

    result = await _async_start_subentry(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_CHAT_MODEL: "vendor/not-in-catalog"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CHAT_MODEL: "invalid_model"}
    entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert not entry.subentries


async def test_subentry_rejects_the_model_label(hass: HomeAssistant) -> None:
    """Pasting the dropdown label instead of the id is refused with a clear error."""
    entry = _mock_entry(hass)

    result = await _async_start_subentry(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_CHAT_MODEL: "Vendor: Model A (vendor/model-a)"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CHAT_MODEL: "invalid_model"}


async def test_subentry_requires_an_explicit_model(hass: HomeAssistant) -> None:
    """An untouched form is an error: no model is preselected for you."""
    entry = _mock_entry(hass)

    result = await _async_start_subentry(hass, entry)
    marker = next(iter(result["data_schema"].schema))
    assert marker.default is vol.UNDEFINED

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_CHAT_MODEL: "  "}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CHAT_MODEL: "model_required"}


async def test_subentry_aborts_without_catalog(hass: HomeAssistant) -> None:
    """An empty catalog aborts instead of offering a guess."""
    FakeClient.models = []
    try:
        entry = _mock_entry(hass)
        result = await _async_start_subentry(hass, entry)
    finally:
        FakeClient.models = CATALOG

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_subentry_reconfigure(hass: HomeAssistant) -> None:
    """Reconfigure changes the model, the title and the unique id."""
    entry = _mock_entry(hass)
    result = await _async_start_subentry(hass, entry)
    await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_CHAT_MODEL: OTHER_MODEL, CONF_NAME: "Fast model"}
    )
    entry = hass.config_entries.async_get_entry(entry.entry_id)
    subentry_id = next(iter(entry.subentries))

    result = await _async_start_subentry(
        hass,
        entry,
        source=config_entries.SOURCE_RECONFIGURE,
        subentry_id=subentry_id,
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_CHAT_MODEL: THIRD_MODEL, CONF_NAME: "Renamed"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    entry = hass.config_entries.async_get_entry(entry.entry_id)
    subentry = entry.subentries[subentry_id]
    assert subentry.data[CONF_CHAT_MODEL] == THIRD_MODEL
    assert subentry.title == "Renamed"
    assert subentry.unique_id == THIRD_MODEL


async def test_subentry_starts_with_a_placeholder_row(hass: HomeAssistant) -> None:
    """The dropdown opens on a placeholder, so an untouched form installs nothing."""
    entry = _mock_entry(hass)

    result = await _async_start_subentry(hass, entry)

    options = next(iter(result["data_schema"].schema.values())).config["options"]
    assert options[0] == {"value": "", "label": cf.PLACEHOLDER_LABEL_DEFAULT}
    assert [option["value"] for option in options].count(CHAT_MODEL) == 1


async def test_subentry_labels_tag_free_and_paid_models(hass: HomeAssistant) -> None:
    """Labels carry the price, so a free variant is not confused with its sibling."""
    FakeClient.models = [
        {
            "id": "vendor/free",
            "name": "Vendor: Free",
            "pricing": {"prompt": "0", "completion": "0"},
        },
        {
            "id": "vendor/paid",
            "name": "Vendor: Paid",
            "pricing": {"prompt": "0.0000001", "completion": "0.0000004"},
        },
    ]
    try:
        entry = _mock_entry(hass)
        result = await _async_start_subentry(hass, entry)
    finally:
        FakeClient.models = CATALOG

    options = next(iter(result["data_schema"].schema.values())).config["options"]
    labels = {option["value"]: option["label"] for option in options}
    assert labels["vendor/free"].endswith("FREE")
    assert "$0.1/$0.4 per 1M" in labels["vendor/paid"]


async def test_subentry_offers_to_add_a_model_the_api_refuses(
    hass: HomeAssistant,
) -> None:
    """A gated free model shows the API message and can still be stored by choice."""
    entry = _mock_entry(hass)
    FakeClient.probe_errors = {
        OTHER_MODEL: NousApiError(404, "No endpoints found for vendor/model-c:free")
    }

    result = await _async_start_subentry(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_CHAT_MODEL: OTHER_MODEL}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert "No endpoints found" in result["description_placeholders"]["error"]
    assert not hass.config_entries.async_get_entry(entry.entry_id).subentries

    # Declining shows the model form again and stores nothing.
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {cf.CONF_ADD_ANYWAY: False}
    )
    assert result["step_id"] == "user"
    assert not hass.config_entries.async_get_entry(entry.entry_id).subentries

    # Asking again offers the same choice, accepting stores the requested id.
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_CHAT_MODEL: OTHER_MODEL}
    )
    assert result["step_id"] == "confirm"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {cf.CONF_ADD_ANYWAY: True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert next(iter(entry.subentries.values())).unique_id == OTHER_MODEL


async def test_subentry_still_hard_fails_on_a_bad_key(hass: HomeAssistant) -> None:
    """An auth failure is never papered over with the confirm step."""
    entry = _mock_entry(hass)
    FakeClient.probe_errors = {OTHER_MODEL: NousAuthError("bad key")}

    result = await _async_start_subentry(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_CHAT_MODEL: OTHER_MODEL}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert not hass.config_entries.async_get_entry(entry.entry_id).subentries


async def test_subentry_hard_fails_when_the_network_is_down(
    hass: HomeAssistant,
) -> None:
    """A connection failure is an account-level error, not a model refusal."""
    entry = _mock_entry(hass)
    FakeClient.probe_errors = {OTHER_MODEL: NousConnectionError("network down")}

    result = await _async_start_subentry(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_CHAT_MODEL: OTHER_MODEL}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert not hass.config_entries.async_get_entry(entry.entry_id).subentries
