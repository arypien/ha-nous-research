"""Tests for the local cost tracker."""

from __future__ import annotations


import pytest

from homeassistant.core import HomeAssistant

from custom_components.nous_research.api import NousUsage
from custom_components.nous_research.cost import CostTracker, ModelPricing

CATALOG = [
    {
        "id": "Hermes-4-70B",
        "pricing": {
            "prompt": "0.0000003",
            "completion": "0.0000012",
            "input_cache_read": "0.00000003",
        },
        "supported_parameters": ["tools", "reasoning"],
        "context_length": 128000,
    },
    {
        "id": "Free-Model",
        "pricing": {"prompt": "0", "completion": "0"},
        "supported_parameters": [],
        "context_length": 8192,
    },
]


@pytest.fixture
async def tracker(hass: HomeAssistant) -> CostTracker:
    """Return a tracker with the test catalog loaded."""
    tracker = CostTracker(hass, "test_entry")
    await tracker.async_setup()
    tracker.set_pricing(CATALOG)
    return tracker


async def test_pricing_parsing(tracker: CostTracker) -> None:
    """Catalog prices are converted to USD per token."""
    pricing = tracker.pricing_for("Hermes-4-70B")
    assert isinstance(pricing, ModelPricing)
    assert pricing.prompt == pytest.approx(3e-7)
    assert pricing.completion == pytest.approx(1.2e-6)
    assert pricing.cache_read == pytest.approx(3e-8)
    assert tracker.pricing_for("missing") is None


async def test_cost_math_with_cache(tracker: CostTracker) -> None:
    """Cached prompt tokens are billed at the cache price."""
    usage = NousUsage(
        prompt_tokens=1000, completion_tokens=500, total_tokens=1500, cached_tokens=200
    )
    cost, known = tracker.cost_for_usage("Hermes-4-70B", usage)
    expected = 800 * 3e-7 + 200 * 3e-8 + 500 * 1.2e-6
    assert known is True
    assert cost == pytest.approx(expected)


async def test_record_accumulates_totals(tracker: CostTracker) -> None:
    """Recording requests updates totals, the day bucket and last request."""
    usage = NousUsage(prompt_tokens=100, completion_tokens=50, cached_tokens=0)
    cost = await tracker.async_record("Hermes-4-70B", usage)

    assert cost == pytest.approx(100 * 3e-7 + 50 * 1.2e-6)
    assert tracker.total_cost == pytest.approx(cost)
    assert tracker.total_tokens_in == 100
    assert tracker.total_tokens_out == 50
    assert tracker.total_requests == 1
    assert tracker.cost_today == pytest.approx(cost)
    assert tracker.last_request is not None
    assert tracker.last_request["model"] == "Hermes-4-70B"
    assert tracker.last_request["when"]


async def test_unknown_model_is_tracked_without_cost(tracker: CostTracker) -> None:
    """Unknown models are counted but not priced."""
    usage = NousUsage(prompt_tokens=10, completion_tokens=5, cached_tokens=0)
    cost = await tracker.async_record("Unknown-Model", usage)

    assert cost == 0.0
    assert "Unknown-Model" in tracker.unknown_models
    assert tracker.total_tokens_in == 10
    assert tracker.total_requests == 1


async def test_record_without_usage(tracker: CostTracker) -> None:
    """A response without usage data still counts as a request."""
    assert await tracker.async_record("Hermes-4-70B", None) == 0.0
    assert tracker.total_requests == 1
    assert tracker.total_tokens_in == 0


async def test_top_models_and_reset(tracker: CostTracker) -> None:
    """Aggregations per model work and reset clears the state."""
    await tracker.async_record(
        "Hermes-4-70B", NousUsage(prompt_tokens=1000, completion_tokens=1000)
    )
    await tracker.async_record(
        "Free-Model", NousUsage(prompt_tokens=10, completion_tokens=10)
    )

    top = tracker.top_models(1)
    assert top[0]["model"] == "Hermes-4-70B"
    assert len(top) == 2
    assert tracker.top_model(1) == "Hermes-4-70B"

    await tracker.async_reset()
    assert tracker.total_cost == 0.0
    assert tracker.total_requests == 0
    assert tracker.cost_last_days(30) == 0.0
    assert tracker.top_models(30) == []
