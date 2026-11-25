"""Catalog helpers: read the API model list and validate model ids.

The Nous API returns an aggregated, OpenRouter-style catalog where ids are
namespaced (``vendor/model``), so no model id can be hardcoded in the
integration.
"""

from __future__ import annotations

from typing import Any, Iterable

from .const import (
    DEFAULT_MODEL_SORT,
    SORT_NAME,
    SORT_NEWEST,
    SORT_PRICE_DESC,
)

#: Model ids with this suffix are asynchronous batch endpoints.
BATCH_SUFFIX = ":batch"

#: Substrings identifying models that cannot answer a chat completion.
NON_CHAT_HINTS = ("embed", "-tts", "whisper", "moderation")


def model_id(model: Any) -> str:
    """Return the id of one catalog entry, or an empty string."""
    if not isinstance(model, dict):
        return ""
    identifier = model.get("id")
    return str(identifier) if identifier else ""


def model_ids(catalog: Iterable[Any] | None) -> list[str]:
    """Return the ids of a catalog, dropping entries without an id."""
    return [identifier for model in catalog or [] if (identifier := model_id(model))]


def _output_modalities(model: dict[str, Any]) -> list[str]:
    """Return the declared output modalities of a catalog entry."""
    architecture = model.get("architecture")
    if not isinstance(architecture, dict):
        return []
    modalities = architecture.get("output_modalities")
    return [str(item) for item in modalities or []]


def is_chat_model(model: Any) -> bool:
    """Return True when the model can serve a text chat completion."""
    identifier = model_id(model)
    if not identifier or identifier.endswith(BATCH_SUFFIX):
        return False
    if any(hint in identifier.lower() for hint in NON_CHAT_HINTS):
        return False
    modalities = _output_modalities(model)
    return not modalities or "text" in modalities


def chat_model_ids(catalog: Iterable[Any] | None) -> list[str]:
    """Return the ids of the chat-capable models, in catalog order."""
    return [model_id(model) for model in catalog or [] if is_chat_model(model)]


def known_model(configured: str | None, catalog: Iterable[Any] | None) -> bool:
    """Return True when the configured model is present in the catalog."""
    return bool(configured) and configured in model_ids(catalog)


#: Price used for a model that does not advertise what it costs.
UNKNOWN_PRICE = float("inf")


def _prices(model: dict[str, Any]) -> tuple[float, float]:
    """Return the prompt and completion price per token, or ``inf`` if unknown."""
    pricing = model.get("pricing")
    if not isinstance(pricing, dict):
        return (UNKNOWN_PRICE, UNKNOWN_PRICE)
    values: list[float] = []
    for key in ("prompt", "completion"):
        try:
            values.append(float(pricing[key]))
        except (KeyError, TypeError, ValueError):
            values.append(UNKNOWN_PRICE)
    return (values[0], values[1])


def _display_name(model: dict[str, Any]) -> str:
    """Return a lower-cased name for alphabetical sorting."""
    identifier = model_id(model)
    return str(model.get("name") or identifier).lower()


def _created(model: dict[str, Any]) -> int:
    """Return the catalog creation timestamp, or 0 when missing."""
    try:
        return int(model.get("created") or 0)
    except (TypeError, ValueError):
        return 0


def sorted_chat_models(
    catalog: Iterable[Any] | None, sort: str = DEFAULT_MODEL_SORT
) -> list[dict[str, Any]]:
    """Return the chat-capable models in the requested order.

    The default, ``price_asc``, starts with the free models (price 0) and then
    walks up the price list, which is the only order that makes a 300 entry
    catalog readable. Models that advertise no price go last in both price
    orders instead of pretending to be free.
    """
    models = [model for model in catalog or [] if is_chat_model(model)]
    if sort == SORT_NAME:
        return sorted(models, key=lambda m: (_display_name(m), model_id(m)))
    if sort == SORT_NEWEST:
        return sorted(
            models, key=lambda m: (-_created(m), _display_name(m), model_id(m))
        )
    if sort == SORT_PRICE_DESC:

        def expensive_first(model: dict[str, Any]) -> tuple[Any, ...]:
            prompt, completion = _prices(model)
            unknown = UNKNOWN_PRICE in (prompt, completion)
            return (
                unknown,
                -completion,
                -prompt,
                _display_name(model),
                model_id(model),
            )

        return sorted(models, key=expensive_first)

    def cheap_first(model: dict[str, Any]) -> tuple[Any, ...]:
        prompt, completion = _prices(model)
        unknown = UNKNOWN_PRICE in (prompt, completion)
        return (unknown, prompt, completion, _display_name(model), model_id(model))

    return sorted(models, key=cheap_first)
