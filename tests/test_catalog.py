"""Tests for the catalog helpers."""

from __future__ import annotations

from custom_components.nous_research.catalog import (
    chat_model_ids,
    is_chat_model,
    model_ids,
    pick_default_model,
    resolve_model,
)

CATALOG = [
    {"id": "vendor/embed-1", "architecture": {"output_modalities": ["text"]}},
    {"id": "vendor/whisper-1"},
    {"id": "vendor/model-b:batch"},
    {"id": "vendor/image-out", "architecture": {"output_modalities": ["image"]}},
    {"id": "vendor/model-a", "architecture": {"output_modalities": ["text"]}},
]


def test_model_ids_drops_entries_without_id() -> None:
    """Entries without an id are ignored."""
    assert model_ids([{"id": "a"}, {}, {"id": ""}]) == ["a"]
    assert model_ids(None) == []


def test_is_chat_model_filters_non_chat_models() -> None:
    """Batch, embedding, audio and image-only models are not chat models."""
    assert is_chat_model({"id": "vendor/model-a"}) is True
    assert is_chat_model({"id": "vendor/model-b:batch"}) is False
    assert is_chat_model({"id": "vendor/embed-1"}) is False
    assert is_chat_model({"id": "vendor/whisper-1"}) is False
    assert (
        is_chat_model(
            {"id": "vendor/image-out", "architecture": {"output_modalities": ["image"]}}
        )
        is False
    )


def test_chat_model_ids_keep_catalog_order() -> None:
    """Only chat capable ids survive, in catalog order."""
    assert chat_model_ids(CATALOG) == ["vendor/model-a"]


def test_pick_default_model_prefers_first_chat_model() -> None:
    """The default is the first chat model, not just the first id."""
    assert pick_default_model(CATALOG) == "vendor/model-a"
    assert pick_default_model(None) == ""
    assert pick_default_model([{"id": "vendor/model-b:batch"}]) == "vendor/model-b:batch"


def test_resolve_model_keeps_known_ids() -> None:
    """A model listed in the catalog is used as configured."""
    assert resolve_model("vendor/model-a", CATALOG) == "vendor/model-a"


def test_resolve_model_falls_back_for_stale_ids() -> None:
    """A stale id falls back to the catalog default instead of failing."""
    assert resolve_model("Hermes-4.3-36B", CATALOG) == "vendor/model-a"


def test_resolve_model_without_catalog() -> None:
    """Without a catalog the configured value is kept verbatim."""
    assert resolve_model("Hermes-4.3-36B", None) == "Hermes-4.3-36B"
    assert resolve_model("", None) == ""
