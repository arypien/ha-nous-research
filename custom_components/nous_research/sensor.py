"""Sensor platform for the Nous Research integration."""

from __future__ import annotations

from collections.abc import Callable
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .catalog import resolve_model
from .const import (
    CONF_BUDGET,
    CONF_CHAT_MODEL,
    CONF_COST_SENSORS,
    COST_UPDATE_SIGNAL,
    DEFAULT_CHAT_MODEL,
    DOMAIN,
)
from .entity import NousBaseEntity

if TYPE_CHECKING:
    from . import NousConfigEntry

# Entities are push based (dispatcher) or coordinator driven; never poll.
PARALLEL_UPDATES: Final = 0

_PRICE_FACTOR: Final = 1_000_000  # API prices are USD per token
_PRICE_DIGITS: Final = 4
#: Maximum decimals kept when converting a per-token price to USD per 1M tokens.
_PRICE_RESOLUTION: Final = 6
_CURRENCY_USD: Final = "USD"

_CONTEXT_LENGTH_KEYS: Final = (
    "context_length",
    "max_context_length",
    "context_window",
    "max_model_len",
    "max_input_tokens",
)
_TOOL_KEYS: Final = (
    "tools",
    "supports_tools",
    "tool_calling",
    "tool_use",
    "function_calling",
)
_MODEL_ID_KEYS: Final = ("id", "model", "name")


def _no_cost_value(entry: NousConfigEntry) -> None:
    """Return no value (placeholder for the description default)."""
    return None


def _no_catalog_value(
    models: list[dict[str, Any]], entry: NousConfigEntry
) -> None:
    """Return no value (placeholder for the description default)."""
    return None


def _value_cost_total(entry: NousConfigEntry) -> float:
    """Return the lifetime cost."""
    return float(entry.runtime_data.tracker.total_cost)


def _value_cost_today(entry: NousConfigEntry) -> float:
    """Return the cost accumulated today."""
    return float(entry.runtime_data.tracker.cost_today)


def _value_cost_7d(entry: NousConfigEntry) -> float:
    """Return the cost of the last seven days."""
    return float(entry.runtime_data.tracker.cost_last_days(7))


def _value_cost_30d(entry: NousConfigEntry) -> float:
    """Return the cost of the last thirty days."""
    return float(entry.runtime_data.tracker.cost_last_days(30))


def _value_tokens_in(entry: NousConfigEntry) -> int:
    """Return the number of prompt tokens used."""
    return int(entry.runtime_data.tracker.total_tokens_in)


def _value_tokens_out(entry: NousConfigEntry) -> int:
    """Return the number of completion tokens used."""
    return int(entry.runtime_data.tracker.total_tokens_out)


def _value_requests(entry: NousConfigEntry) -> int:
    """Return the number of requests made."""
    return int(entry.runtime_data.tracker.total_requests)


def _value_top_model(entry: NousConfigEntry) -> str | None:
    """Return the most used model of the day."""
    tracker = entry.runtime_data.tracker
    try:
        return tracker.top_model(1)
    except (KeyError, TypeError):  # pragma: no cover - defensive
        return None


def _attributes_top_model(entry: NousConfigEntry) -> dict[str, Any]:
    """Return attributes for the top model sensor."""
    tracker = entry.runtime_data.tracker
    return {
        "models": tracker.top_models(1),
        "unknown_models": sorted(tracker.unknown_models),
    }


def _last_request(entry: NousConfigEntry) -> dict[str, Any] | None:
    """Return the last recorded request."""
    last_request = entry.runtime_data.tracker.last_request
    return dict(last_request) if isinstance(last_request, dict) else None


def _value_last_request(entry: NousConfigEntry) -> str:
    """Return the model of the last request."""
    last_request = _last_request(entry)
    if not last_request:
        return "none"
    model = last_request.get("model")
    return str(model) if model else "none"


def _attributes_last_request(entry: NousConfigEntry) -> dict[str, Any]:
    """Return attributes for the last request sensor."""
    return _last_request(entry) or {}


def _value_budget_remaining(entry: NousConfigEntry) -> float | None:
    """Return the remaining budget."""
    budget = _budget(entry)
    if budget is None:
        return None
    return round(budget - float(entry.runtime_data.tracker.total_cost), 6)


def _attributes_budget(entry: NousConfigEntry) -> dict[str, Any]:
    """Return attributes for the budget sensor."""
    budget = _budget(entry)
    return {
        "budget": budget,
        "spent": float(entry.runtime_data.tracker.total_cost),
    }


def _value_catalog_models(
    models: list[dict[str, Any]], entry: NousConfigEntry
) -> int:
    """Return the number of models in the catalog."""
    return len(models)


def _attributes_catalog_models(
    models: list[dict[str, Any]], entry: NousConfigEntry
) -> dict[str, Any]:
    """Return attributes for the catalog size sensor."""
    tracker = entry.runtime_data.tracker
    tool_capable = 0
    with_pricing = 0
    cheapest: float | None = None

    for model in models:
        if _model_supports_tools(model):
            tool_capable += 1
        pricing = tracker.pricing_for(_model_id(model))
        if pricing is None:
            continue
        with_pricing += 1
        price = _price_per_mtok(pricing.prompt)
        if price is not None and (cheapest is None or price < cheapest):
            cheapest = price

    return {
        "tool_capable": tool_capable,
        "cheapest_input_per_mtok": cheapest,
        "with_pricing": with_pricing,
    }


def _value_selected_model_input_price(
    models: list[dict[str, Any]], entry: NousConfigEntry
) -> float | None:
    """Return the input price of the configured model per 1M tokens."""
    pricing = _selected_model_pricing(models, entry)
    return None if pricing is None else _price_per_mtok(pricing.prompt)


def _value_selected_model_output_price(
    models: list[dict[str, Any]], entry: NousConfigEntry
) -> float | None:
    """Return the output price of the configured model per 1M tokens."""
    pricing = _selected_model_pricing(models, entry)
    return None if pricing is None else _price_per_mtok(pricing.completion)


def _attributes_selected_model_price(
    models: list[dict[str, Any]], entry: NousConfigEntry
) -> dict[str, Any]:
    """Return pricing attributes for the selected model sensors."""
    model_id, model = _selected_model(models, entry)
    pricing = _selected_model_pricing(models, entry)
    return {
        "model": model_id,
        "input_per_mtok": (
            None if pricing is None else _price_per_mtok(pricing.prompt)
        ),
        "output_per_mtok": (
            None if pricing is None else _price_per_mtok(pricing.completion)
        ),
        "cache_read_per_mtok": (
            None if pricing is None else _price_per_mtok(pricing.cache_read)
        ),
        "context_length": _context_length(model),
    }


def _entry_option(entry: NousConfigEntry, key: str) -> Any:
    """Return a config value, preferring the options over the data."""
    if key in entry.options:
        return entry.options[key]
    return entry.data.get(key)


def _budget(entry: NousConfigEntry) -> float | None:
    """Return the configured budget, if any."""
    raw = _entry_option(entry, CONF_BUDGET)
    if raw is None or raw == "":
        return None
    try:
        budget = float(raw)
    except (TypeError, ValueError):
        return None
    return budget if budget > 0 else None


def _cost_sensors_enabled(entry: NousConfigEntry) -> bool:
    """Return True when the cost sensors are not explicitly disabled."""
    return _entry_option(entry, CONF_COST_SENSORS) is not False


def _price_per_mtok(value: Any) -> float | None:
    """Convert an USD per token price to USD per 1M tokens."""
    if value is None:
        return None
    try:
        price = round(float(value) * _PRICE_FACTOR, _PRICE_RESOLUTION)
        # ``round`` already drops trailing zeros: 0.03 stays "0.03", not "0.0300".
        return price
    except (TypeError, ValueError):
        return None


def _model_id(model: dict[str, Any]) -> str:
    """Return the identifier of a catalog model."""
    for key in _MODEL_ID_KEYS:
        value = model.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _model_supports_tools(model: dict[str, Any]) -> bool:
    """Return True when a catalog model advertises tool support."""
    for key in _TOOL_KEYS:
        if model.get(key) is True:
            return True

    capabilities = model.get("capabilities")
    if isinstance(capabilities, dict) and any(
        capabilities.get(key) is True
        for key in ("tools", "tool_calling", "function_calling", "tool_use")
    ):
        return True

    features = model.get("supported_features") or model.get("features")
    if isinstance(features, (list, tuple, set, frozenset)):
        return any(
            str(feature).lower() in {"tools", "tool_calling", "function_calling"}
            for feature in features
        )
    return False


def _context_length(model: dict[str, Any] | None) -> int | None:
    """Return the context window of a catalog model."""
    if not isinstance(model, dict):
        return None
    for key in _CONTEXT_LENGTH_KEYS:
        value = model.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _selected_model_id(entry: NousConfigEntry) -> str:
    """Return the configured chat model, resolved against the catalog."""
    value = _entry_option(entry, CONF_CHAT_MODEL)
    runtime = getattr(entry, "runtime_data", None)
    coordinator = getattr(runtime, "catalog", None)
    return resolve_model(
        str(value or DEFAULT_CHAT_MODEL), getattr(coordinator, "data", None)
    )


def _selected_model(
    models: list[dict[str, Any]], entry: NousConfigEntry
) -> tuple[str, dict[str, Any] | None]:
    """Return the configured model and its catalog entry."""
    model_id = _selected_model_id(entry)
    for model in models:
        if _model_id(model) == model_id:
            return model_id, model
    return model_id, None


def _selected_model_pricing(
    models: list[dict[str, Any]], entry: NousConfigEntry
) -> Any:
    """Return the pricing of the configured model, if known."""
    model_id, _ = _selected_model(models, entry)
    return entry.runtime_data.tracker.pricing_for(model_id)


@dataclass(frozen=True, kw_only=True)
class NousCostSensorDescription(SensorEntityDescription):
    """Description of a cost tracking sensor."""

    value_fn: Callable[[NousConfigEntry], Any] = _no_cost_value
    attributes_fn: Callable[[NousConfigEntry], dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class NousCatalogSensorDescription(SensorEntityDescription):
    """Description of a model catalog sensor."""

    value_fn: Callable[
        [list[dict[str, Any]], NousConfigEntry], Any
    ] = _no_catalog_value
    attributes_fn: (
        Callable[[list[dict[str, Any]], NousConfigEntry], dict[str, Any]] | None
    ) = None


_LOGGER = logging.getLogger(__package__)

COST_SENSOR_DESCRIPTIONS: Final[tuple[NousCostSensorDescription, ...]] = (
    NousCostSensorDescription(
        key="cost_total",
        translation_key="cost_total",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=_CURRENCY_USD,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=_PRICE_DIGITS,
        icon="mdi:cash-multiple",
        value_fn=_value_cost_total,
    ),
    NousCostSensorDescription(
        key="cost_today",
        translation_key="cost_today",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=_CURRENCY_USD,
        suggested_display_precision=_PRICE_DIGITS,
        icon="mdi:cash-clock",
        value_fn=_value_cost_today,
    ),
    NousCostSensorDescription(
        key="cost_7d",
        translation_key="cost_7d",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=_CURRENCY_USD,
        suggested_display_precision=_PRICE_DIGITS,
        icon="mdi:calendar-week",
        value_fn=_value_cost_7d,
    ),
    NousCostSensorDescription(
        key="cost_30d",
        translation_key="cost_30d",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=_CURRENCY_USD,
        suggested_display_precision=_PRICE_DIGITS,
        icon="mdi:calendar-month",
        value_fn=_value_cost_30d,
    ),
    NousCostSensorDescription(
        key="tokens_in_total",
        translation_key="tokens_in_total",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        icon="mdi:counter",
        value_fn=_value_tokens_in,
    ),
    NousCostSensorDescription(
        key="tokens_out_total",
        translation_key="tokens_out_total",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        icon="mdi:counter",
        value_fn=_value_tokens_out,
    ),
    NousCostSensorDescription(
        key="requests_total",
        translation_key="requests_total",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        icon="mdi:api",
        value_fn=_value_requests,
    ),
    NousCostSensorDescription(
        key="top_model_today",
        translation_key="top_model_today",
        icon="mdi:robot",
        value_fn=_value_top_model,
        attributes_fn=_attributes_top_model,
    ),
    NousCostSensorDescription(
        key="last_request",
        translation_key="last_request",
        icon="mdi:message-text-outline",
        value_fn=_value_last_request,
        attributes_fn=_attributes_last_request,
    ),
)

BUDGET_REMAINING_DESCRIPTION: Final = NousCostSensorDescription(
    key="budget_remaining",
    translation_key="budget_remaining",
    device_class=SensorDeviceClass.MONETARY,
    native_unit_of_measurement=_CURRENCY_USD,
    suggested_display_precision=_PRICE_DIGITS,
    icon="mdi:wallet-outline",
    value_fn=_value_budget_remaining,
    attributes_fn=_attributes_budget,
)

CATALOG_SENSOR_DESCRIPTIONS: Final[tuple[NousCatalogSensorDescription, ...]] = (
    NousCatalogSensorDescription(
        key="catalog_models",
        translation_key="catalog_models",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:format-list-bulleted",
        value_fn=_value_catalog_models,
        attributes_fn=_attributes_catalog_models,
    ),
    NousCatalogSensorDescription(
        key="selected_model_input_price",
        translation_key="selected_model_input_price",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=_CURRENCY_USD,
        # No fixed precision: 0.03 USD stays "0.03" instead of "0.0300".
        suggested_display_precision=None,
        icon="mdi:cash-plus",
        value_fn=_value_selected_model_input_price,
        attributes_fn=_attributes_selected_model_price,
    ),
    NousCatalogSensorDescription(
        key="selected_model_output_price",
        translation_key="selected_model_output_price",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=_CURRENCY_USD,
        # No fixed precision: 0.03 USD stays "0.03" instead of "0.0300".
        suggested_display_precision=None,
        icon="mdi:cash-check",
        value_fn=_value_selected_model_output_price,
        attributes_fn=_attributes_selected_model_price,
    ),
)


class NousCostSensor(NousBaseEntity, SensorEntity):
    """A push based sensor fed by the cost tracker."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, entry: NousConfigEntry, description: NousCostSensorDescription
    ) -> None:
        """Initialize the cost sensor."""
        NousBaseEntity.__init__(self, entry)
        self._entry = entry
        self.entity_description = description
        self._attr_translation_key = description.key
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._unsub_dispatcher: Callable[[], None] | None = None
        if getattr(self, "_attr_device_info", None) is None:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, entry.entry_id)}
            )

    @property
    def native_value(self) -> Any:
        """Return the current value of the sensor."""
        return self.entity_description.value_fn(self._entry)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes of the sensor."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self._entry)

    async def async_added_to_hass(self) -> None:
        """Subscribe to cost updates."""
        await super().async_added_to_hass()
        self._unsub_dispatcher = async_dispatcher_connect(
            self.hass,
            COST_UPDATE_SIGNAL.format(self._entry.entry_id),
            self._handle_cost_update,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from cost updates."""
        if self._unsub_dispatcher is not None:
            self._unsub_dispatcher()
            self._unsub_dispatcher = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_cost_update(self) -> None:
        """Write the new state after a cost update."""
        self.async_write_ha_state()


class NousCatalogSensor(
    CoordinatorEntity[DataUpdateCoordinator[list[dict[str, Any]]]],
    NousBaseEntity,
    SensorEntity,
):
    """A sensor fed by the model catalog coordinator."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, entry: NousConfigEntry, description: NousCatalogSensorDescription
    ) -> None:
        """Initialize the catalog sensor."""
        NousBaseEntity.__init__(self, entry)
        self._entry = entry
        self.entity_description = description
        self._attr_translation_key = description.key
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        if getattr(self, "_attr_device_info", None) is None:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, entry.entry_id)}
            )
        # Both bases derive from Entity and their cooperative __init__ chain is
        # not compatible (NousBaseEntity needs the config entry), so bind the
        # coordinator explicitly. CoordinatorEntity also requires its context
        # attribute, otherwise async_added_to_hass raises AttributeError.
        self.coordinator = entry.runtime_data.catalog
        self.coordinator_context = None

    @property
    def _models(self) -> list[dict[str, Any]]:
        """Return the current catalog data."""
        return list(self.coordinator.data or [])

    @property
    def native_value(self) -> Any:
        """Return the current value of the sensor."""
        return self.entity_description.value_fn(self._models, self._entry)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes of the sensor."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self._models, self._entry)


@callback
def _async_remove_stale_entities(hass: HomeAssistant, entry: NousConfigEntry) -> None:
    """Drop registry rows for sensors this version no longer provides.

    Descriptions come and go between releases; without this the removed entity
    lingers in the registry as ``unavailable`` forever.
    """
    known = {description.key for description in COST_SENSOR_DESCRIPTIONS}
    known.update(description.key for description in CATALOG_SENSOR_DESCRIPTIONS)
    known.add(BUDGET_REMAINING_DESCRIPTION.key)

    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.domain != "sensor" or not reg_entry.unique_id.startswith(prefix):
            continue
        if reg_entry.unique_id.removeprefix(prefix) not in known:
            registry.async_remove(reg_entry.entity_id)
            _LOGGER.info("Removed stale sensor %s", reg_entry.entity_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NousConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Nous Research sensors from a config entry."""
    entities: list[SensorEntity] = []
    _async_remove_stale_entities(hass, entry)

    if _cost_sensors_enabled(entry):
        entities.extend(
            NousCostSensor(entry, description)
            for description in COST_SENSOR_DESCRIPTIONS
        )
        if _budget(entry) is not None:
            entities.append(NousCostSensor(entry, BUDGET_REMAINING_DESCRIPTION))

    entities.extend(
        NousCatalogSensor(entry, description)
        for description in CATALOG_SENSOR_DESCRIPTIONS
    )

    async_add_entities(entities)
