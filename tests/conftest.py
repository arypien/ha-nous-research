"""Fixtures for the Nous Research integration tests."""

from __future__ import annotations

from collections.abc import Generator

import pytest

# Import Home Assistant first so it can alias httpx -> httpx2 before any
# third-party test plugin imports httpx.
import homeassistant  # noqa: F401,E402

# pytest-homeassistant-custom-component 0.13.x still imports a private helper
# that Home Assistant 2026.11 moved/removed. Provide a no-op shim before the
# plugin is imported (``pytest_plugins`` below is processed afterwards).
import homeassistant.components.logger as _logger  # noqa: E402

if not hasattr(_logger, "_clear_logger_overwrites"):
    _logger._clear_logger_overwrites = lambda *args, **kwargs: None  # type: ignore[attr-defined]

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Enable loading custom integrations in all tests."""
    yield
