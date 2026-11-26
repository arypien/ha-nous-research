"""Tests for integration setup, services, diagnostics and sensors."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any
from unittest.mock import patch

import pytest

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API, CONF_PROMPT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.nous_research import (
    DOMAIN,
    _model_issue_id,
    async_migrate_entry,
)
from custom_components.nous_research.api import NousAuthError
from custom_components.nous_research.config_flow import LLM_API_ASSIST
from custom_components.nous_research.conversation import NousConversationEntity
from custom_components.nous_research.sensor import _price_per_mtok
from custom_components.nous_research.const import (
    CONF_BASE_URL,
    CONF_CHAT_MODEL,
    CONF_COST_SENSORS,
    DEFAULT_BASE_URL,
    SERVICE_RESET_USAGE,
    SUBENTRY_TYPE_MODEL,
)
from custom_components.nous_research.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.nous_research.sensor import (
    MODEL_SENSOR_DESCRIPTIONS,
    _async_remove_stale_entities,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

MODEL_ID = "nousresearch/hermes-4-405b"
OTHER_MODEL_ID = "nousresearch/hermes-4-70b"
SUBENTRY_TITLE = "Hermes 4 405B"

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


def _model_subentry(model: str = MODEL_ID, title: str = SUBENTRY_TITLE) -> ConfigSubentry:
    """Return a model subentry the way the subentry flow stores it."""
    return ConfigSubentry(
        data=MappingProxyType({CONF_CHAT_MODEL: model}),
        subentry_type=SUBENTRY_TYPE_MODEL,
        title=title,
        unique_id=model,
    )


def _entry_data() -> dict[str, Any]:
    """Return the data of an account entry: key and endpoint only."""
    return {CONF_API_KEY: "sk-test-123", CONF_BASE_URL: DEFAULT_BASE_URL}


def _entry_options(**overrides: Any) -> dict[str, Any]:
    """Return the shared options of an account entry."""
    options: dict[str, Any] = {
        CONF_PROMPT: "You are a helpful assistant.",
        CONF_COST_SENSORS: True,
        CONF_BASE_URL: DEFAULT_BASE_URL,
    }
    options.update(overrides)
    return options


@pytest.fixture
async def setup_integration(hass: HomeAssistant) -> MockConfigEntry:
    """Set up an account entry with one model subentry."""
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data=_entry_data(),
        options=_entry_options(),
        title="Nous Research",
        subentries_data=[_model_subentry().as_dict()],
    )
    entry.add_to_hass(hass)
    with patch("custom_components.nous_research.NousClient", FakeClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _state_id(
    hass: HomeAssistant, entry: MockConfigEntry, unique_id: str
) -> str | None:
    """Return the entity id registered under ``unique_id``."""
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)


async def test_setup_and_unload(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The entry loads its platforms, data and services and unloads cleanly."""
    entry = setup_integration
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.client is not None
    assert entry.runtime_data.tracker is not None
    assert entry.runtime_data.catalog.data[0]["id"] == MODEL_ID

    # The account device carries the aggregate sensors.
    assert hass.states.get("sensor.nous_research_cost_total") is not None
    assert hass.states.get("sensor.nous_research_input_tokens_total") is not None

    # One agent and one AI Task entity per model subentry, nothing at entry level.
    registry = er.async_get(hass)
    by_domain: dict[str, set[str]] = {}
    for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
        by_domain.setdefault(registered.domain, set()).add(registered.unique_id)
    subentry_id = next(iter(entry.subentries))
    assert by_domain["conversation"] == {f"{subentry_id}_conversation"}
    assert by_domain["ai_task"] == {f"{subentry_id}_ai_task"}

    assert hass.services.has_service(DOMAIN, SERVICE_RESET_USAGE)
    assert not hass.services.has_service(DOMAIN, "set_balance")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not hass.services.has_service(DOMAIN, SERVICE_RESET_USAGE)


async def test_account_sensors_start_at_zero(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """A fresh account reports zero cost, zero tokens and zero requests."""
    for entity_id in (
        "sensor.nous_research_cost_total",
        "sensor.nous_research_cost_today",
        "sensor.nous_research_cost_last_7_days",
        "sensor.nous_research_cost_last_30_days",
        "sensor.nous_research_input_tokens_total",
        "sensor.nous_research_output_tokens_total",
        "sensor.nous_research_requests_total",
    ):
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert float(state.state) == 0, entity_id
    assert hass.states.get("sensor.nous_research_balance") is None


async def test_model_sensors_start_at_zero(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Every model subentry gets its own sensors, also starting at zero."""
    entry = setup_integration
    subentry_id = next(iter(entry.subentries))
    for description in MODEL_SENSOR_DESCRIPTIONS:
        unique_id = f"{entry.entry_id}_{subentry_id}_{description.key}"
        entity_id = _state_id(hass, entry, unique_id)
        assert entity_id is not None, description.key
        state = hass.states.get(entity_id)
        assert state is not None, description.key
        if description.key.endswith("_price"):
            continue  # prices come from the catalog, not from usage
        assert float(state.state) == 0, description.key
        # The model device is separate from the account device.
        assert state.name.startswith("Nous Research Hermes 4 405B ")


async def test_model_price_sensors(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Catalog prices are exposed per model, rendered without padding zeros."""
    entry = setup_integration
    subentry_id = next(iter(entry.subentries))
    inp = hass.states.get(
        _state_id(hass, entry, f"{entry.entry_id}_{subentry_id}_model_input_price")
    )
    out = hass.states.get(
        _state_id(hass, entry, f"{entry.entry_id}_{subentry_id}_model_output_price")
    )
    assert inp is not None and out is not None
    assert inp.state == "0.3"  # 0.0000003 USD per token
    assert out.state == "1.2"  # 0.0000012 USD per token
    assert inp.attributes["input_per_mtok"] == pytest.approx(0.3)
    assert inp.attributes["output_per_mtok"] == pytest.approx(1.2)
    assert inp.attributes["model"] == MODEL_ID
    # The entry level price sensors are gone: prices belong to a model.
    assert (
        _state_id(hass, entry, f"{entry.entry_id}_selected_model_input_price") is None
    )


def _agent(hass: HomeAssistant, entry: MockConfigEntry) -> NousConversationEntity:
    """Return the Assist agent of the single model subentry of ``entry``."""
    subentry_id = next(iter(entry.subentries))
    entity_id = er.async_get(hass).async_get_entity_id(
        "conversation", DOMAIN, f"{subentry_id}_conversation"
    )
    assert entity_id is not None
    agent = conversation.async_get_agent(hass, entity_id)
    assert isinstance(agent, NousConversationEntity)
    return agent


async def test_usage_is_booked_under_the_configured_model(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """A model echoed by the API does not relabel the counters or the price."""
    agent = _agent(hass, setup_integration)

    async def stream() -> Any:
        yield {"type": "delta", "text": "hi"}
        yield {
            "type": "usage",
            "model": "vendor/served-by-someone-else",
            "usage": {"prompt_tokens": 1000, "completion_tokens": 1000},
        }

    async for _ in agent._transform(stream()):
        pass

    last = agent.tracker.last_request
    assert last is not None
    assert last["model"] == MODEL_ID
    # Priced with the configured model, so the free/paid split stays truthful.
    assert agent.tracker.cost_today > 0
    assert agent.tracker.unknown_models == []


async def test_unknown_model_fails_loudly(hass: HomeAssistant) -> None:
    """A model missing from the catalog is reported, never swapped for another."""
    assert await async_setup_component(hass, "homeassistant", {})
    subentry = _model_subentry("vendor/gone-from-catalog", "Gone")
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data=_entry_data(),
        options=_entry_options(),
        title="Nous Research",
        subentries_data=[subentry.as_dict()],
    )
    entry.add_to_hass(hass)
    with patch("custom_components.nous_research.NousClient", FakeClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    agent = _agent(hass, entry)
    # The device tells the truth about what is configured...
    assert agent._device_model == "vendor/gone-from-catalog"
    # ...and a request fails instead of being answered by the catalog default.
    with pytest.raises(HomeAssistantError):
        _ = agent._model


async def test_services(hass: HomeAssistant, setup_integration: MockConfigEntry) -> None:
    """reset_usage clears the tracker through the service layer."""
    entry = setup_integration
    tracker = entry.runtime_data.tracker

    await tracker.async_record(MODEL_ID, None)
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
    subentry_id = next(iter(entry.subentries))
    model_unique_id = f"{entry.entry_id}_{subentry_id}_model_cost_total"

    _async_remove_stale_entities(hass, entry)

    assert registry.async_get(stale.entity_id) is None
    # Live sensors of this version survive the sweep, per model ones included.
    assert registry.async_get("sensor.nous_research_cost_total") is not None
    assert registry.async_get_entity_id("sensor", DOMAIN, model_unique_id) is not None


async def test_unknown_model_issue(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """A subentry model that left the catalog raises one issue."""
    entry = setup_integration
    known_id = _model_issue_id(entry, MODEL_ID)
    assert ir.async_get(hass).async_get_issue(DOMAIN, known_id) is None

    # An id the catalog does not know is reported per entry and model.
    other = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data=_entry_data(),
        options=_entry_options(),
        title="Second account",
        unique_id="second-account",
        subentries_data=[_model_subentry("vendor/ghost", "Ghost").as_dict()],
    )
    other.add_to_hass(hass)
    with patch("custom_components.nous_research.NousClient", FakeClient):
        assert await hass.config_entries.async_setup(other.entry_id)
        await hass.async_block_till_done()

    other_entry = next(iter(other.subentries.values()))
    ghost_id = _model_issue_id(other, "vendor/ghost")
    ghost = ir.async_get(hass).async_get_issue(DOMAIN, ghost_id)
    assert ghost is not None
    assert ghost.translation_key == "unknown_model"
    assert ghost.translation_placeholders == {"model": "vendor/ghost"}

    # Pointing the subentry at a catalog model clears the issue again.
    with patch("custom_components.nous_research.NousClient", FakeClient):
        assert hass.config_entries.async_update_subentry(
            other,
            other_entry,
            data={CONF_CHAT_MODEL: MODEL_ID},
        )
        await hass.async_block_till_done()
        await hass.config_entries.async_reload(other.entry_id)
        await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, ghost_id) is None


async def test_migrate_entry_drops_model(hass: HomeAssistant) -> None:
    """A 0.1.x entry loses its model key and keeps the key and its history."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={**_entry_data(), CONF_CHAT_MODEL: MODEL_ID},
        options={**_entry_options(), CONF_CHAT_MODEL: MODEL_ID},
        title="Nous Research",
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)
    await hass.async_block_till_done()

    entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert entry.version == 2
    assert CONF_CHAT_MODEL not in entry.data
    assert CONF_CHAT_MODEL not in entry.options
    assert entry.data[CONF_API_KEY] == "sk-test-123"
    assert entry.options[CONF_PROMPT] == "You are a helpful assistant."


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


async def test_one_ai_task_entity_per_model(hass: HomeAssistant) -> None:
    """Every model subentry gets exactly one AI Task entity and its own sensors."""
    assert await async_setup_component(hass, "homeassistant", {})
    subentries = [
        _model_subentry(model, f"Extra {index}")
        for index, model in enumerate((OTHER_MODEL_ID, MODEL_ID))
    ]
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data=_entry_data(),
        options=_entry_options(),
        title="Nous Research",
        subentries_data=[subentry.as_dict() for subentry in subentries],
    )
    entry.add_to_hass(hass)
    with patch("custom_components.nous_research.NousClient", FakeClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    ai_task_ids = {
        entity.unique_id for entity in entities if entity.domain == "ai_task"
    }
    assert ai_task_ids == {
        f"{subentry.subentry_id}_ai_task" for subentry in subentries
    }

    conversation_ids = {
        entity.unique_id for entity in entities if entity.domain == "conversation"
    }
    assert conversation_ids == {
        f"{subentry.subentry_id}_conversation" for subentry in subentries
    }

    sensor_ids = {
        entity.unique_id for entity in entities if entity.domain == "sensor"
    }
    for subentry in subentries:
        assert {
            f"{entry.entry_id}_{subentry.subentry_id}_{description.key}"
            for description in MODEL_SENSOR_DESCRIPTIONS
        } <= sensor_ids

    component = hass.data["entity_components"]["ai_task"]
    for subentry in subentries:
        entity_id = registry.async_get_entity_id(
            "ai_task", DOMAIN, f"{subentry.subentry_id}_ai_task"
        )
        assert entity_id is not None
        entity = component.get_entity(entity_id)
        assert entity.subentry.subentry_id == subentry.subentry_id
        assert entity._settings[CONF_CHAT_MODEL] == subentry.data[CONF_CHAT_MODEL]


async def test_conversation_agent_per_model(hass: HomeAssistant) -> None:
    """A model subentry gets its own Assist agent, selectable by entity id."""
    assert await async_setup_component(hass, "homeassistant", {})
    subentry = _model_subentry(OTHER_MODEL_ID, "Fast model")
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data=_entry_data(),
        options=_entry_options(**{CONF_LLM_HASS_API: LLM_API_ASSIST}),
        title="Nous Research",
        subentries_data=[subentry.as_dict()],
    )
    entry.add_to_hass(hass)
    with patch("custom_components.nous_research.NousClient", FakeClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "conversation", DOMAIN, f"{subentry.subentry_id}_conversation"
    )
    assert entity_id is not None

    # Named by the model device plus the subentry title.
    assert hass.states.get(entity_id).name == "Nous Research Fast model"

    # The agent is reachable by entity id, i.e. selectable in Assist.
    agent = conversation.async_get_agent(hass, entity_id)
    assert isinstance(agent, NousConversationEntity)
    assert agent.entity_id == entity_id
    assert agent.supported_features & conversation.ConversationEntityFeature.CONTROL
    # Subentry data only carries the model; the rest inherits the entry options.
    assert agent._settings[CONF_CHAT_MODEL] == OTHER_MODEL_ID
    assert agent._settings[CONF_LLM_HASS_API] == LLM_API_ASSIST
    # No agent is registered for the account itself.
    assert conversation.async_get_agent(hass, entry.entry_id) is None


class RevokedKeyClient(FakeClient):
    """Client whose catalog call is answered with 401/403."""

    async def async_list_models(self) -> list[dict[str, Any]]:
        raise NousAuthError("401 Unauthorized: invalid_api_key")


async def test_a_revoked_key_starts_the_reauth_flow(hass: HomeAssistant) -> None:
    """A dead key must ask for a new one instead of retrying setup forever."""
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data=_entry_data(),
        options=_entry_options(),
        title="Nous Research",
        subentries_data=[_model_subentry().as_dict()],
    )
    entry.add_to_hass(hass)

    with patch("custom_components.nous_research.NousClient", RevokedKeyClient):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    reauth = [flow for flow in flows if flow["context"].get("source") == "reauth"]
    assert len(reauth) == 1
    assert reauth[0]["context"]["entry_id"] == entry.entry_id
