"""Smoke test: the integration package imports and the hass fixture works."""

from __future__ import annotations

from homeassistant.core import HomeAssistant


async def test_hass_fixture(hass: HomeAssistant) -> None:
    """The test harness itself works."""
    assert hass is not None
