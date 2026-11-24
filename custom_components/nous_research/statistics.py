"""Recorder long term statistics for the Nous Research integration."""

from __future__ import annotations

from datetime import datetime
import logging
import re
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN, STATISTICS_MIN_INTERVAL

_LOGGER = logging.getLogger(__name__)

#: Monotonic timestamp of the last successful push, keyed by statistic_id.
_LAST_PUSH: dict[str, float] = {}


def _to_float(value: Any, default: float = 0.0) -> float:
    """Return ``value`` as a float, or ``default``."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _short_id(entry_id: str) -> str:
    """Return a short, still unique, statistic name suffix."""
    return entry_id[:8]


def _statistic_slug(entry_id: str) -> str:
    """Return a statistic-safe identifier fragment.

    Home Assistant rejects external statistic ids that are not entirely
    lowercase alphanumerics/underscores, while config entry ids are ULIDs.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", entry_id.lower()).strip("_")
    return slug or "entry"


def _statistic_start(moment: datetime | None = None) -> datetime:
    """Return a UTC timestamp truncated to the hour.

    External statistics are stored in hourly buckets and the recorder rejects
    any other alignment.
    """
    local = dt_util.as_local(moment) if moment is not None else dt_util.now()
    truncated = local.replace(minute=0, second=0, microsecond=0)
    return dt_util.as_utc(truncated)


def _statistics_module() -> tuple[Any, Any, Any, Any]:
    """Import the recorder statistics API lazily.

    The recorder is an optional dependency: importing it at module level would
    break setups where it is not loaded (and would slow down startup).
    """
    try:
        from homeassistant.components.recorder.models import (  # noqa: PLC0415
            StatisticData,
            StatisticMeanType,
            StatisticMetaData,
        )
    except ImportError:  # pragma: no cover - older recorder layouts
        from homeassistant.components.recorder.models import (  # noqa: PLC0415
            StatisticMeanType,
        )
        from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
            StatisticData,
            StatisticMetaData,
        )
    from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
        async_add_external_statistics,
    )

    return (
        StatisticData,
        StatisticMetaData,
        StatisticMeanType,
        async_add_external_statistics,
    )


def _metadata(
    metadata_class: Any,
    statistic_id: str,
    name: str,
    unit: str | None,
    *,
    mean_type: Any,
    unit_class: str | None = None,
) -> Any:
    """Build the metadata of one external statistic.

    ``source`` must equal the domain part of ``statistic_id``; the recorder
    rejects anything else with "Invalid source".
    """
    return metadata_class(
        has_mean=False,
        mean_type=mean_type,
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_class=unit_class,
        unit_of_measurement=unit,
    )


async def async_push_statistics(
    hass: HomeAssistant,
    entry_id: str,
    *,
    total_cost: float,
    total_tokens: int,
) -> None:
    """Push cumulative cost and token totals to the recorder.

    A no-op when the recorder is not loaded. Pushes are throttled per
    statistic id to at most one every ``STATISTICS_MIN_INTERVAL`` seconds.
    """
    if "recorder" not in hass.config.components:
        return

    cost_id = f"{DOMAIN}:cost_{_statistic_slug(entry_id)}"
    tokens_id = f"{DOMAIN}:tokens_{_statistic_slug(entry_id)}"
    cost = round(max(_to_float(total_cost), 0.0), 6)
    tokens = max(int(total_tokens), 0)

    cost_name = f"Nous Research cost {_short_id(entry_id)}"
    tokens_name = f"Nous Research tokens {_short_id(entry_id)}"
    now = time.monotonic()
    pending: list[tuple[str, str, str | None, str | None, float]] = []
    for statistic_id, name, unit, unit_class, value in (
        (cost_id, cost_name, "USD", None, cost),
        (tokens_id, tokens_name, "tokens", None, float(tokens)),
    ):
        last = _LAST_PUSH.get(statistic_id)
        if last is not None and now - last < STATISTICS_MIN_INTERVAL:
            continue
        pending.append((statistic_id, name, unit, unit_class, value))

    if not pending:
        return

    start = _statistic_start()
    try:
        (
            statistic_data,
            statistic_metadata,
            mean_type,
            add_statistics,
        ) = _statistics_module()
    except ImportError:  # pragma: no cover - recorder present but unusable
        _LOGGER.debug("Recorder statistics API unavailable, skipping push")
        return

    for statistic_id, name, unit, unit_class, value in pending:
        payload = [
            statistic_data(
                start=start,
                state=value,
                sum=value,
            )
        ]
        try:
            add_statistics(
                hass,
                _metadata(
                    statistic_metadata,
                    statistic_id,
                    name,
                    unit,
                    mean_type=mean_type.NONE,
                    unit_class=unit_class,
                ),
                payload,
            )
        except Exception:  # noqa: BLE001 - statistics must never break a request
            _LOGGER.debug(
                "Could not push statistics %s", statistic_id, exc_info=True
            )
            continue
        _LAST_PUSH[statistic_id] = now


async def async_import_history(
    hass: HomeAssistant, entry_id: str, days: list[tuple[datetime, float]]
) -> None:
    """Seed the cost statistic with historical day totals.

    ``days`` holds ``(timestamp, cost_increment)`` pairs; increments are turned
    into a cumulative series. Best effort: failures are logged and ignored.
    """
    if not days:
        return
    if "recorder" not in hass.config.components:
        _LOGGER.debug("Recorder not loaded, cannot import cost history")
        return

    try:
        (
            statistic_data,
            statistic_metadata,
            mean_type,
            add_statistics,
        ) = _statistics_module()
    except ImportError:  # pragma: no cover - recorder present but unusable
        _LOGGER.debug("Recorder statistics API unavailable, skipping import")
        return

    points: list[tuple[datetime, float]] = []
    for item in days:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            continue
        moment, cost = item
        if isinstance(moment, datetime):
            points.append((moment, _to_float(cost)))

    running = 0.0
    seen: set[datetime] = set()
    series: list[Any] = []
    for moment, cost in sorted(points, key=lambda point: point[0]):
        start = _statistic_start(moment)
        if start in seen:
            continue
        seen.add(start)
        running += max(_to_float(cost), 0.0)
        value = round(running, 6)
        series.append(statistic_data(start=start, state=value, sum=value))

    if not series:
        return

    statistic_id = f"{DOMAIN}:cost_{_statistic_slug(entry_id)}"
    try:
        add_statistics(
            hass,
            _metadata(
                statistic_metadata,
                statistic_id,
                f"Nous Research cost {_short_id(entry_id)}",
                "USD",
                mean_type=mean_type.NONE,
                unit_class=None,
            ),
            series,
        )
    except Exception:  # noqa: BLE001 - history import is optional
        _LOGGER.debug("Could not import cost history", exc_info=True)
        return
    _LOGGER.debug("Imported %s historical cost points", len(series))
