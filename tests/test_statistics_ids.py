"""Statistics identifier tests."""

from __future__ import annotations

import re

import pytest

from custom_components.nous_research.statistics import _metadata, _statistic_slug

ULID = "01M877ZG988FM0NJTT463SWHH6"
VALID = re.compile(r"^[a-z0-9_]+$")


@pytest.mark.parametrize("entry_id", [ULID, "01m877zg-988f", "ABC def/ghi"])
def test_slug_is_statistic_safe(entry_id: str) -> None:
    """Entry ids are ULIDs but statistic fragments must be lowercase."""
    slug = _statistic_slug(entry_id)
    assert VALID.match(slug), slug
    assert slug == slug.lower()


def test_slug_keeps_entry_ids_distinct() -> None:
    """Different entries must not collapse to the same statistic id."""
    assert _statistic_slug(ULID) != _statistic_slug("01M877ZG988FM0NJTT463SWHH7")


def test_metadata_source_matches_statistic_id_domain() -> None:
    """The recorder rejects metadata whose source differs from the id domain."""
    statistic_id = f"nous_research:cost_{_statistic_slug(ULID)}"
    data = _metadata(dict, statistic_id, "Nous Research cost", "USD", mean_type=None)
    assert data["source"] == statistic_id.split(":", 1)[0]
    assert data["statistic_id"] == statistic_id
    assert data["has_sum"] is True
