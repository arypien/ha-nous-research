"""Tests for the catalog helpers."""

from __future__ import annotations

from custom_components.nous_research.catalog import (
    chat_model_ids,
    is_chat_model,
    known_model,
    model_ids,
    sorted_chat_models,
)
from custom_components.nous_research.const import (
    SORT_NAME,
    SORT_NEWEST,
    SORT_PRICE_DESC,
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


def test_known_model_requires_an_exact_id() -> None:
    """Only a byte exact catalog id counts as known: no aliases, no trimming."""
    assert known_model("vendor/model-a", CATALOG) is True
    assert known_model("vendor/model-b:batch", CATALOG) is True
    assert known_model("Vendor/Model-A", CATALOG) is False
    assert known_model("vendor/model-a ", CATALOG) is False
    assert known_model("", CATALOG) is False
    assert known_model("vendor/model-a", None) is False


def test_free_variant_is_its_own_id() -> None:
    """A ``:free`` id is a catalog entry of its own, never folded into its base."""
    catalog = [
        {"id": "upstage/solar-mini-4-5", "pricing": {"prompt": "0.00000001"}},
        {"id": "upstage/solar-mini4:free", "pricing": {"prompt": "0"}},
    ]
    assert known_model("upstage/solar-mini4:free", catalog) is True
    assert chat_model_ids(catalog) == [
        "upstage/solar-mini-4-5",
        "upstage/solar-mini4:free",
    ]


PRICED = [
    {
        "id": "vendor/expensive",
        "name": "Expensive",
        "pricing": {"prompt": "0.00001", "completion": "0.00004"},
    },
    {
        "id": "vendor/free",
        "name": "Free one",
        "pricing": {"prompt": "0", "completion": "0"},
    },
    {
        "id": "vendor/cheap",
        "name": "Cheap",
        "pricing": {"prompt": "0.0000001", "completion": "0.0000002"},
    },
    {"id": "vendor/no-price", "name": "Unpriced"},
    {"id": "vendor/embed", "pricing": {"prompt": "0", "completion": "0"}},
]


def test_sorted_chat_models_defaults_to_cheapest_first() -> None:
    """The default order starts with the free models, unpriced ones go last."""
    order = [model["id"] for model in sorted_chat_models(PRICED)]
    assert order == [
        "vendor/free",
        "vendor/cheap",
        "vendor/expensive",
        "vendor/no-price",
    ]
    # An embed model never shows up in a chat list, whatever it costs.
    assert "vendor/embed" not in order


def test_sorted_chat_models_expensive_first() -> None:
    """Descending price puts the priciest model on top and unpriced ones last."""
    order = [model["id"] for model in sorted_chat_models(PRICED, SORT_PRICE_DESC)]
    assert order == [
        "vendor/expensive",
        "vendor/cheap",
        "vendor/free",
        "vendor/no-price",
    ]


def test_sorted_chat_models_by_name_and_by_age() -> None:
    """Alphabetical and newest-first orders are stable alternatives."""
    by_name = [model["id"] for model in sorted_chat_models(PRICED, SORT_NAME)]
    assert by_name == [
        "vendor/cheap",
        "vendor/expensive",
        "vendor/free",
        "vendor/no-price",
    ]
    fresh = [
        {"id": "vendor/old", "name": "Old", "created": 1},
        {"id": "vendor/new", "name": "New", "created": 99},
    ]
    assert [m["id"] for m in sorted_chat_models(fresh, SORT_NEWEST)] == [
        "vendor/new",
        "vendor/old",
    ]
