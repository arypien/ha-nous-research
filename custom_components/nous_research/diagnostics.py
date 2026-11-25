"""Diagnostics support for the Nous Research integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from .const import CONF_CHAT_MODEL

if TYPE_CHECKING:
    from . import NousConfigEntry

_LOGGER = logging.getLogger(__package__)

TO_REDACT = {CONF_API_KEY}

REDACTED = "**REDACTED**"

TOP_MODELS_DAYS = 30
TOP_MODELS_LIMIT = 30


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NousConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data

    catalog = runtime.catalog.data or []

    return {
        "entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "title": entry.title,
            "state": str(entry.state),
            "unique_id": entry.unique_id,
            "data": _redact(dict(entry.data)),
            "options": _redact(dict(entry.options)),
        },
        "subentries": [
            {
                "subentry_id": subentry.subentry_id,
                "subentry_type": subentry.subentry_type,
                "title": subentry.title,
                "unique_id": subentry.unique_id,
                "model": subentry.data.get(CONF_CHAT_MODEL),
                "data": _redact(dict(subentry.data)),
            }
            for subentry in entry.subentries.values()
        ],
        "catalog": {
            "models": len(catalog),
            "last_update_success": runtime.catalog.last_update_success,
            "model_ids": sorted(
                str(model["id"])
                for model in catalog
                if isinstance(model, dict) and model.get("id")
            ),
        },
        "tracker": _tracker_snapshot(runtime.tracker),
        "client": {
            "base_url": runtime.client.base_url,
            "rate_limits": dict(runtime.client.rate_limits),
        },
    }


def _redact(data: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive values from a mapping."""
    redacted = async_redact_data(dict(data), TO_REDACT)
    for key in TO_REDACT:
        if key in data:
            redacted[key] = REDACTED
    return redacted


def _tracker_snapshot(tracker: Any) -> dict[str, Any]:
    """Return the interesting parts of the local cost tracker state."""
    try:
        return {
            "totals": {
                "cost": float(tracker.total_cost),
                "requests": int(tracker.total_requests),
                "tokens_in": int(tracker.total_tokens_in),
                "tokens_out": int(tracker.total_tokens_out),
            },
            "cost_today": float(tracker.cost_today),
            "top_models": list(
                tracker.top_models(TOP_MODELS_DAYS, TOP_MODELS_LIMIT)
            ),
            "unknown_models": sorted(str(model) for model in tracker.unknown_models),
        }
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Could not read the Nous Research cost tracker state")
        return {"error": "unavailable"}
