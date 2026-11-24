"""Tests that keep translations and the two README files in sync."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.translation import async_get_translations

from custom_components.nous_research import DOMAIN

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "nous_research"
STRINGS = COMPONENT / "strings.json"
TRANSLATIONS = COMPONENT / "translations"
PLACEHOLDER = re.compile(r"\{[a-zA-Z0-9_]+\}")


def flatten(data: object, prefix: str = "") -> dict[str, str]:
    """Return a flat ``key -> string`` mapping of a nested translation file."""
    if isinstance(data, dict):
        flat: dict[str, str] = {}
        for key, value in data.items():
            flat.update(flatten(value, f"{prefix}{key}."))
        return flat
    if isinstance(data, list):
        flat = {}
        for index, value in enumerate(data):
            flat.update(flatten(value, f"{prefix}{index}."))
        return flat
    return {prefix[:-1]: data if isinstance(data, str) else ""}


def load(path: Path) -> dict[str, str]:
    """Load a translation file."""
    return flatten(json.loads(path.read_text(encoding="utf-8")))


TRANSLATION_FILES = sorted(TRANSLATIONS.glob("*.json"))


def test_languages_present() -> None:
    """English and Polish ship with the integration."""
    assert {path.stem for path in TRANSLATION_FILES} >= {"en", "pl"}


def test_entity_sections_are_mappings() -> None:
    """Entity translations must be ``{"name": ...}`` mappings, never bare strings.

    Home Assistant needs the ``.name`` leaf to name an entity; a bare string
    silently falls back to the device class name (e.g. "Monetary balance").
    """
    data = json.loads(STRINGS.read_text(encoding="utf-8"))
    for platform, entries in data["entity"].items():
        for key, value in entries.items():
            if key == "name":  # platform level name, e.g. entity.conversation
                continue
            assert isinstance(value, dict), f"entity.{platform}.{key} is a bare string"
            assert value.get("name"), f"entity.{platform}.{key} has no name"


def test_english_mirrors_strings() -> None:
    """Hassfest expects translations/en.json to be identical to strings.json."""
    assert json.loads(STRINGS.read_text(encoding="utf-8")) == json.loads(
        (TRANSLATIONS / "en.json").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("path", TRANSLATION_FILES, ids=lambda path: path.stem)
def test_keys_match_strings(path: Path) -> None:
    """Every language carries exactly the keys of strings.json."""
    reference = load(STRINGS)
    current = load(path)
    assert set(current) == set(reference), (
        f"{path.stem} missing={sorted(set(reference) - set(current))} "
        f"extra={sorted(set(current) - set(reference))}"
    )


@pytest.mark.parametrize("path", TRANSLATION_FILES, ids=lambda path: path.stem)
def test_no_empty_values(path: Path) -> None:
    """No untranslated placeholders are left behind."""
    empty = [key for key, value in load(path).items() if not value.strip()]
    assert not empty, f"{path.stem} has empty strings: {empty}"


@pytest.mark.parametrize("path", TRANSLATION_FILES, ids=lambda path: path.stem)
def test_placeholders_preserved(path: Path) -> None:
    """Translations keep the placeholders of the source string."""
    reference = load(STRINGS)
    current = load(path)
    broken = sorted(
        key
        for key in current
        if set(PLACEHOLDER.findall(current[key]))
        != set(PLACEHOLDER.findall(reference[key]))
    )
    assert not broken, f"{path.stem} changed placeholders: {broken}"


@pytest.mark.parametrize("name", ["README.md", "README.pl.md", "docs/TRANSLATING.md"])
def test_docs_exist(name: str) -> None:
    """Bilingual documentation and the translation guide are shipped."""
    assert (ROOT / name).is_file(), f"{name} is missing"


def test_readme_language_switch() -> None:
    """Both README files link to each other."""
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    polish = (ROOT / "README.pl.md").read_text(encoding="utf-8")
    assert "README.pl.md" in english
    assert "README.md" in polish


async def test_home_assistant_loads_both_languages(hass: HomeAssistant) -> None:
    """Home Assistant itself can load the files and returns translated strings."""
    polish = await async_get_translations(
        hass, "pl", "config", integrations=[DOMAIN], config_flow=True
    )
    english = await async_get_translations(
        hass, "en", "config", integrations=[DOMAIN], config_flow=True
    )
    assert polish, "Polish config flow strings did not load"
    assert english, "English config flow strings did not load"
    assert polish != english, "Polish strings fell back to English"
    assert any("Klucz API" in value for value in polish.values())
