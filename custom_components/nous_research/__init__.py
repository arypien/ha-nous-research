"""The Nous Research integration.

Sets up the API client, the local cost tracker and the model catalog
coordinator, then forwards the conversation, ai_task and sensor platforms.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import inspect
import logging
from typing import TYPE_CHECKING, Any, cast

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    NousApiError,
    NousAuthError,
    NousClient,
    NousConnectionError,
    NousPaymentRequiredError,
    NousRateLimitError,
)
from .catalog import known_model
from .const import (
    CONF_BASE_URL,
    CONF_CATALOG_INTERVAL,
    CONF_CHAT_MODEL,
    COST_UPDATE_SIGNAL,
    DEFAULT_BASE_URL,
    DEFAULT_CATALOG_INTERVAL,
    DOMAIN,
    SERVICE_RESET_USAGE,
)

if TYPE_CHECKING:
    from .cost import CostTracker

_LOGGER = logging.getLogger(__package__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [Platform.CONVERSATION, Platform.AI_TASK, Platform.SENSOR]

ATTR_ENTRY_ID = "entry_id"

CATALOG_UNSUB_KEY = f"{DOMAIN}_catalog_unsub"

RESET_USAGE_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): cv.string})


@dataclass
class NousRuntimeData:
    """Runtime data attached to a Nous Research config entry."""

    client: NousClient
    tracker: CostTracker
    catalog: DataUpdateCoordinator[list[dict[str, Any]]]


NousConfigEntry = ConfigEntry[NousRuntimeData]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the integration from configuration.yaml (config entry only)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: NousConfigEntry) -> bool:
    """Set up Nous Research from a config entry."""
    # Imported lazily so the package stays importable without the tracker module.
    from .cost import CostTracker

    data = dict(entry.data)
    options = dict(entry.options)

    api_key: str | None = data.get(CONF_API_KEY)
    if not api_key:
        raise ConfigEntryAuthFailed("No API key configured")

    base_url = data.get(CONF_BASE_URL) or options.get(CONF_BASE_URL) or DEFAULT_BASE_URL

    client = NousClient(hass, api_key, base_url)

    tracker = CostTracker(hass, entry.entry_id)
    await tracker.async_setup()

    async def _async_update_data() -> list[dict[str, Any]]:
        """Fetch the model catalog."""
        try:
            return await client.async_list_models()
        except NousAuthError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except NousConnectionError as err:
            raise UpdateFailed(f"Error communicating with the Nous API: {err}") from err
        except (NousPaymentRequiredError, NousRateLimitError, NousApiError) as err:
            raise UpdateFailed(f"Error fetching the model catalog: {err}") from err

    catalog: DataUpdateCoordinator[list[dict[str, Any]]] = DataUpdateCoordinator(
        hass,
        _LOGGER,
        config_entry=entry,
        name="Nous Research catalog",
        update_interval=timedelta(
            hours=options.get(CONF_CATALOG_INTERVAL, DEFAULT_CATALOG_INTERVAL)
        ),
        update_method=_async_update_data,
    )
    await catalog.async_config_entry_first_refresh()

    tracker.set_pricing(catalog.data or [])
    _async_sync_model_issue(hass, entry, catalog.data or [])

    def _async_pricing_updated() -> None:
        """Refresh prices from the latest catalog."""
        tracker.set_pricing(catalog.data or [])
        _async_sync_model_issue(hass, entry, catalog.data or [])

    unsub = catalog.async_add_listener(_async_pricing_updated)
    hass.data.setdefault(CATALOG_UNSUB_KEY, {})[entry.entry_id] = unsub

    entry.runtime_data = NousRuntimeData(
        client=client, tracker=tracker, catalog=catalog
    )

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await _async_register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NousConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    unsub = hass.data.get(CATALOG_UNSUB_KEY, {}).pop(entry.entry_id, None)
    if unsub is not None:
        unsub()

    loaded_entries = [
        other
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.state is ConfigEntryState.LOADED
    ]
    if not loaded_entries:
        _async_unregister_services(hass)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: NousConfigEntry) -> None:
    """Reload a config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an old config entry (no migrations needed yet)."""
    return True


async def _async_options_updated(hass: HomeAssistant, entry: NousConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


@callback
def _async_sync_model_issue(
    hass: HomeAssistant, entry: NousConfigEntry, catalog: list[dict[str, Any]]
) -> None:
    """Raise an issue while the configured model is absent from the catalog.

    The catalog is namespaced (``vendor/model``) and evolves, so a hardcoded or
    renamed id silently breaks every request. The issue tells the user to pick
    a model again instead of failing without explanation.
    """
    configured = str(({**entry.data, **entry.options}).get(CONF_CHAT_MODEL) or "")
    issue_id = f"unknown_model_{entry.entry_id}"
    if configured and catalog and not known_model(configured, catalog):
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="unknown_model",
            translation_placeholders={"model": configured},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


def _async_resolve_entry(hass: HomeAssistant, entry_id: str | None) -> NousConfigEntry:
    """Return the targeted config entry, defaulting to the first loaded one."""
    if entry_id:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise HomeAssistantError(
                f"Config entry '{entry_id}' does not belong to {DOMAIN}"
            )
    else:
        loaded = [
            other
            for other in hass.config_entries.async_entries(DOMAIN)
            if other.state is ConfigEntryState.LOADED
        ]
        if not loaded:
            raise HomeAssistantError(f"No loaded {DOMAIN} config entry found")
        entry = loaded[0]

    if entry.state is not ConfigEntryState.LOADED:
        raise HomeAssistantError(f"Config entry '{entry.title}' is not loaded")
    return cast(NousConfigEntry, entry)


async def _async_invoke_tracker(
    tracker: Any, method_names: tuple[str, ...], *args: Any
) -> None:
    """Call the first available tracker method, sync or async."""
    for method_name in method_names:
        method = getattr(tracker, method_name, None)
        if method is None:
            continue
        result = method(*args)
        if inspect.isawaitable(result):
            await result
        return
    raise HomeAssistantError(f"Cost tracker does not implement {method_names[0]}")


async def _async_handle_reset_usage(call: ServiceCall) -> None:
    """Handle the reset_usage service."""
    entry = _async_resolve_entry(call.hass, call.data.get(ATTR_ENTRY_ID))
    await _async_invoke_tracker(
        entry.runtime_data.tracker, ("async_reset_usage", "reset_usage")
    )
    async_dispatcher_send(call.hass, COST_UPDATE_SIGNAL.format(entry.entry_id))


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register the integration services once per Home Assistant instance."""
    if hass.services.has_service(DOMAIN, SERVICE_RESET_USAGE):
        return

    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_USAGE,
        _async_handle_reset_usage,
        schema=RESET_USAGE_SCHEMA,
    )


def _async_unregister_services(hass: HomeAssistant) -> None:
    """Remove the integration services."""
    for service in (SERVICE_RESET_USAGE,):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
