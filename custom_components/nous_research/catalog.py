"""Catalog helpers: read the API model list and validate model ids.

The Nous API returns an aggregated, OpenRouter-style catalog where ids are
namespaced (``vendor/model``), so no model id can be hardcoded in the
integration.
"""

from __future__ import annotations

from typing import Any, Iterable

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
