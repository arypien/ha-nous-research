"""Persistent cost tracking for the Nous Research integration.

The tracker keeps running totals and per day buckets in a
:class:`homeassistant.helpers.storage.Store` so that sensors stay correct
across restarts without hammering the API for usage data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
import math
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import NousUsage
from .const import (
    COST_UPDATE_SIGNAL,
    HISTORY_DAYS,
    SAVE_DELAY,
    STORAGE_KEY,
    STORAGE_VERSION,
    STORAGE_VERSION_MINOR,
)
from .statistics import async_push_statistics

_LOGGER = logging.getLogger(__name__)

#: Keys of one daily bucket (besides the per model sub mapping).
BUCKET_KEYS = ("cost", "tokens_in", "tokens_out", "requests")

#: Alternative spellings used by OpenRouter style catalogs.
_PROMPT_KEYS = ("prompt", "input", "input_cost_per_token")
_COMPLETION_KEYS = ("completion", "output", "output_cost_per_token")
_CACHE_READ_KEYS = ("input_cache_read", "cache_read", "cached_input")


def _as_float(value: Any, default: float = 0.0) -> float:
    """Return ``value`` as a finite float, or ``default``."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.strip())
        except ValueError:
            return default
    else:
        return default
    return result if math.isfinite(result) else default


def _as_int(value: Any, default: int = 0) -> int:
    """Return ``value`` as a non negative integer, or ``default``."""
    if isinstance(value, bool):
        return default
    amount = _as_float(value, float("nan"))
    if not math.isfinite(amount):
        return default
    return max(int(round(amount)), 0)


def _price(source: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    """Return the first usable price found in ``source``, or ``None``."""
    for key in keys:
        if key not in source:
            continue
        value = _as_float(source[key], float("nan"))
        if not math.isfinite(value) or value < 0:
            # Negative values are used by some catalogs to mean "unknown".
            continue
        return value
    return None


def day_key(when: datetime | None = None) -> str:
    """Return the local calendar day of ``when`` as ``YYYY-MM-DD``."""
    moment = dt_util.as_local(when) if when is not None else dt_util.now()
    return moment.date().isoformat()


@dataclass(slots=True)
class ModelPricing:
    """Price per single token in USD for one model."""

    prompt: float = 0.0
    completion: float = 0.0
    cache_read: float = 0.0

    @classmethod
    def from_api(cls, raw: Any) -> ModelPricing | None:
        """Build pricing from an OpenRouter style model or pricing payload.

        Accepts either the full model dictionary (``{"id": ..., "pricing": {}}``)
        or the bare ``pricing`` mapping. Returns ``None`` when no usable price
        is present.
        """
        if not isinstance(raw, dict):
            return None
        nested = raw.get("pricing")
        source = nested if isinstance(nested, dict) else raw
        prompt = _price(source, _PROMPT_KEYS)
        completion = _price(source, _COMPLETION_KEYS)
        cache_read = _price(source, _CACHE_READ_KEYS)
        if prompt is None and completion is None and cache_read is None:
            return None
        return cls(
            prompt=prompt or 0.0,
            completion=completion or 0.0,
            cache_read=cache_read or 0.0,
        )


class CostTracker:
    """Track token usage and USD cost for a single configuration entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the tracker (no I/O happens here)."""
        self.hass = hass
        self.entry_id = entry_id
        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY}.{entry_id}",
            minor_version=STORAGE_VERSION_MINOR,
        )
        self._pricing: dict[str, ModelPricing] = {}
        self._pricing_lower: dict[str, ModelPricing] = {}
        self._total_cost = 0.0
        self._total_tokens_in = 0
        self._total_tokens_out = 0
        self._total_requests = 0
        self._days: dict[str, dict[str, Any]] = {}
        self._last_request: dict[str, Any] | None = None
        self._unknown_models: set[str] = set()
        self._pruned_day: str | None = None

    # ------------------------------------------------------------------
    # Setup / persistence
    # ------------------------------------------------------------------
    async def async_setup(self) -> None:
        """Load persisted data, migrating older payloads when needed."""
        try:
            data = await self._store.async_load()
        except Exception:  # noqa: BLE001 - a broken store must never break setup
            _LOGGER.warning(
                "Could not read stored cost data for %s, starting empty",
                self.entry_id,
                exc_info=True,
            )
            return
        if not isinstance(data, dict):
            _LOGGER.debug("No stored cost data for %s yet", self.entry_id)
            return
        try:
            data = await self._async_migrate(
                STORAGE_VERSION, STORAGE_VERSION_MINOR, data
            )
            self._restore(data)
        except Exception:  # noqa: BLE001 - defensive: bad payload shapes
            _LOGGER.warning(
                "Stored cost data for %s is invalid, starting empty",
                self.entry_id,
                exc_info=True,
            )
            self._reset_state()
            return
        today = day_key()
        self._prune(today)
        self._pruned_day = today
        _LOGGER.debug(
            "Restored cost data for %s: %.6f USD over %s days",
            self.entry_id,
            self._total_cost,
            len(self._days),
        )

    async def _async_migrate(
        self,
        old_major_version: int,
        old_minor_version: int,
        old_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Normalise the stored payload into the current schema."""
        _LOGGER.debug(
            "Migrating cost data for %s from %s.%s",
            self.entry_id,
            old_major_version,
            old_minor_version,
        )
        data = self._empty_data()
        if not isinstance(old_data, dict):
            return data

        total = old_data.get("total")
        if isinstance(total, dict):
            data["total"] = self._clean_total(total)
        else:
            # Very old layout stored the totals at the top level.
            data["total"] = self._clean_total(old_data)

        days = old_data.get("days")
        if isinstance(days, dict):
            cleaned: dict[str, dict[str, Any]] = {}
            for key, bucket in days.items():
                if isinstance(key, str) and isinstance(bucket, dict):
                    cleaned[key] = self._clean_bucket(bucket)
            data["days"] = cleaned

        last_request = old_data.get("last_request")
        if isinstance(last_request, dict):
            data["last_request"] = dict(last_request)
        return data

    @staticmethod
    def _empty_data() -> dict[str, Any]:
        """Return the empty storage payload."""
        return {
            "total": {"cost": 0.0, "tokens_in": 0, "tokens_out": 0, "requests": 0},
            "days": {},
            "last_request": None,
        }

    @staticmethod
    def _clean_total(raw: dict[str, Any]) -> dict[str, Any]:
        """Return a validated ``total`` block."""
        return {
            "cost": _as_float(raw.get("cost")),
            "tokens_in": _as_int(raw.get("tokens_in")),
            "tokens_out": _as_int(raw.get("tokens_out")),
            "requests": _as_int(raw.get("requests")),
        }

    @classmethod
    def _clean_bucket(cls, raw: dict[str, Any]) -> dict[str, Any]:
        """Return a validated daily bucket."""
        bucket: dict[str, Any] = {
            "cost": _as_float(raw.get("cost")),
            "tokens_in": _as_int(raw.get("tokens_in")),
            "tokens_out": _as_int(raw.get("tokens_out")),
            "requests": _as_int(raw.get("requests")),
            "models": {},
        }
        models = raw.get("models")
        if isinstance(models, dict):
            for model, stats in models.items():
                if isinstance(model, str) and isinstance(stats, dict):
                    bucket["models"][model] = cls._clean_total(stats)
        return bucket

    def _restore(self, data: dict[str, Any]) -> None:
        """Copy a validated storage payload into the running state."""
        self._reset_state()
        total = data.get("total")
        if isinstance(total, dict):
            clean = self._clean_total(total)
            self._total_cost = clean["cost"]
            self._total_tokens_in = clean["tokens_in"]
            self._total_tokens_out = clean["tokens_out"]
            self._total_requests = clean["requests"]
        elif "cost" in data or "requests" in data:
            # Tolerate a payload written before the nested layout existed.
            clean = self._clean_total(data)
            self._total_cost = clean["cost"]
            self._total_tokens_in = clean["tokens_in"]
            self._total_tokens_out = clean["tokens_out"]
            self._total_requests = clean["requests"]

        days = data.get("days")
        if isinstance(days, dict):
            for key, bucket in days.items():
                if isinstance(key, str) and isinstance(bucket, dict):
                    self._days[key] = self._clean_bucket(bucket)

        last_request = data.get("last_request")
        if isinstance(last_request, dict):
            self._last_request = dict(last_request)

    def _reset_state(self) -> None:
        """Drop every in memory counter."""
        self._total_cost = 0.0
        self._total_tokens_in = 0
        self._total_tokens_out = 0
        self._total_requests = 0
        self._days = {}
        self._last_request = None
        self._unknown_models = set()

    def _as_dict(self) -> dict[str, Any]:
        """Return the payload written to the store."""
        return {
            "total": {
                "cost": round(self._total_cost, 10),
                "tokens_in": self._total_tokens_in,
                "tokens_out": self._total_tokens_out,
                "requests": self._total_requests,
            },
            "days": self._days,
            "last_request": self._last_request,
        }

    def _schedule_save(self) -> None:
        """Persist the current state after the configured delay."""
        self._store.async_delay_save(self._as_dict, SAVE_DELAY)

    def _dispatch_update(self) -> None:
        """Tell listening entities that the figures changed."""
        async_dispatcher_send(self.hass, COST_UPDATE_SIGNAL.format(self.entry_id))

    # ------------------------------------------------------------------
    # Pricing
    # ------------------------------------------------------------------
    def set_pricing(self, models: list[dict[str, Any]]) -> None:
        """Replace the pricing table from the OpenRouter style catalog."""
        pricing: dict[str, ModelPricing] = {}
        for model in models or []:
            if not isinstance(model, dict):
                continue
            model_id = model.get("id")
            if not isinstance(model_id, str) or not model_id:
                continue
            parsed = ModelPricing.from_api(model)
            if parsed is None:
                _LOGGER.debug("No pricing information for model %s", model_id)
                continue
            pricing[model_id] = parsed
        self._pricing = pricing
        self._pricing_lower = {
            model_id.lower(): price for model_id, price in pricing.items()
        }
        _LOGGER.debug("Pricing table updated with %s models", len(pricing))

    def pricing_for(self, model: str) -> ModelPricing | None:
        """Return the pricing for ``model`` when it is known."""
        if not isinstance(model, str) or not model:
            return None
        pricing = self._pricing.get(model)
        if pricing is not None:
            return pricing
        return self._pricing_lower.get(model.lower())

    def cost_for_usage(self, model: str, usage: NousUsage) -> tuple[float, bool]:
        """Return ``(usd, known)`` for one request."""
        pricing = self.pricing_for(model)
        if pricing is None:
            return 0.0, False
        prompt_tokens = max(int(usage.prompt_tokens), 0)
        cached_tokens = min(max(int(usage.cached_tokens), 0), prompt_tokens)
        completion_tokens = max(int(usage.completion_tokens), 0)
        uncached_tokens = prompt_tokens - cached_tokens
        cache_price = pricing.cache_read if pricing.cache_read > 0 else pricing.prompt
        cost = (
            uncached_tokens * pricing.prompt
            + cached_tokens * cache_price
            + completion_tokens * pricing.completion
        )
        return round(cost, 10), True

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    async def async_record(self, model: str, usage: NousUsage | None) -> float:
        """Record one request and return its cost in USD."""
        if isinstance(usage, dict):  # tolerate raw payloads from callers
            usage = NousUsage.from_api(usage)
        if usage is None:
            usd, known = 0.0, self.pricing_for(model) is not None
        else:
            usd, known = self.cost_for_usage(model, usage)
        if not known:
            self._unknown_models.add(model)

        tokens_in = int(usage.prompt_tokens) if usage is not None else 0
        tokens_out = int(usage.completion_tokens) if usage is not None else 0

        self._total_cost = round(self._total_cost + usd, 10)
        self._total_tokens_in += tokens_in
        self._total_tokens_out += tokens_out
        self._total_requests += 1

        today = day_key()
        if self._pruned_day != today:
            self._prune(today)
            self._pruned_day = today
        bucket = self._bucket(today)
        bucket["cost"] = round(bucket["cost"] + usd, 10)
        bucket["tokens_in"] += tokens_in
        bucket["tokens_out"] += tokens_out
        bucket["requests"] += 1
        model_bucket = bucket["models"].setdefault(model, dict.fromkeys(BUCKET_KEYS, 0))
        model_bucket["cost"] = round(model_bucket["cost"] + usd, 10)
        model_bucket["tokens_in"] += tokens_in
        model_bucket["tokens_out"] += tokens_out
        model_bucket["requests"] += 1

        self._last_request = {
            "model": model,
            "cost": usd,
            "known": known,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "when": dt_util.utcnow().isoformat(),
            "usage": usage.as_dict() if usage is not None else None,
        }

        self._schedule_save()
        self._dispatch_update()
        await async_push_statistics(
            self.hass,
            self.entry_id,
            total_cost=self._total_cost,
            total_tokens=self._total_tokens_in + self._total_tokens_out,
        )
        return usd

    def _bucket(self, key: str) -> dict[str, Any]:
        """Return (and create) the bucket for one day."""
        bucket = self._days.get(key)
        if bucket is None:
            bucket = {
                "cost": 0.0,
                "tokens_in": 0,
                "tokens_out": 0,
                "requests": 0,
                "models": {},
            }
            self._days[key] = bucket
        return bucket

    def _prune(self, today: str) -> None:
        """Drop day buckets older than ``HISTORY_DAYS``."""
        try:
            cutoff = (
                date.fromisoformat(today) - timedelta(days=HISTORY_DAYS)
            ).isoformat()
        except ValueError:
            cutoff = day_key(dt_util.now() - timedelta(days=HISTORY_DAYS))
        stale = [key for key in self._days if key < cutoff]
        for key in stale:
            del self._days[key]
        if stale:
            _LOGGER.debug("Pruned %s old cost buckets", len(stale))

    async def async_reset(self) -> None:
        """Clear all counters."""
        self._reset_state()
        self._schedule_save()
        self._dispatch_update()
        await async_push_statistics(
            self.hass, self.entry_id, total_cost=0.0, total_tokens=0
        )

    async def async_reset_usage(self) -> None:
        """Alias of :meth:`async_reset` used by the service layer."""
        await self.async_reset()

    # ------------------------------------------------------------------
    # Totals
    # ------------------------------------------------------------------
    @property
    def total_cost(self) -> float:
        """Return the lifetime cost in USD."""
        return self._total_cost

    @property
    def total_tokens_in(self) -> int:
        """Return the lifetime prompt token count."""
        return self._total_tokens_in

    @property
    def total_tokens_out(self) -> int:
        """Return the lifetime completion token count."""
        return self._total_tokens_out

    @property
    def total_requests(self) -> int:
        """Return the lifetime request count."""
        return self._total_requests

    @property
    def cost_today(self) -> float:
        """Return the cost recorded today (local time)."""
        bucket = self._days.get(day_key())
        return float(bucket["cost"]) if bucket else 0.0

    @property
    def last_request(self) -> dict[str, Any] | None:
        """Return information about the most recent request."""
        return dict(self._last_request) if self._last_request else None

    @property
    def unknown_models(self) -> list[str]:
        """Return the models seen without pricing information."""
        return sorted(self._unknown_models)

    # ------------------------------------------------------------------
    # Reporting helpers
    # ------------------------------------------------------------------
    def _window_keys(self, days: int) -> list[str]:
        """Return the day keys of the last ``days`` local days."""
        if days <= 0:
            return []
        today = dt_util.now().date()
        return [(today - timedelta(days=offset)).isoformat() for offset in range(days)]

    def _window_buckets(self, days: int) -> list[dict[str, Any]]:
        """Return every bucket inside the reporting window."""
        return [
            bucket
            for key in self._window_keys(days)
            if (bucket := self._days.get(key)) is not None
        ]

    def _model_totals(self, days: int) -> dict[str, dict[str, float]]:
        """Aggregate per model figures inside the window."""
        totals: dict[str, dict[str, float]] = {}
        for bucket in self._window_buckets(days):
            models = bucket.get("models") or {}
            for model, stats in models.items():
                slot = totals.setdefault(
                    model,
                    {"cost": 0.0, "requests": 0.0, "tokens_in": 0.0, "tokens_out": 0.0},
                )
                slot["cost"] += _as_float(stats.get("cost"))
                slot["requests"] += float(_as_int(stats.get("requests")))
                slot["tokens_in"] += float(_as_int(stats.get("tokens_in")))
                slot["tokens_out"] += float(_as_int(stats.get("tokens_out")))
        return totals

    def cost_last_days(self, days: int) -> float:
        """Return the cost of the last ``days`` local days, today included."""
        return round(
            sum(_as_float(bucket.get("cost")) for bucket in self._window_buckets(days)),
            10,
        )

    def top_models(self, days: int, limit: int = 5) -> list[dict[str, Any]]:
        """Return the most expensive models of the window, dearest first."""
        if limit <= 0:
            return []
        totals = self._model_totals(days)
        ranked = sorted(
            totals.items(),
            key=lambda item: (
                -item[1]["cost"],
                -item[1]["requests"],
                item[0],
            ),
        )
        return [
            {
                "model": model,
                "cost": round(stats["cost"], 10),
                "requests": int(stats["requests"]),
                "tokens_in": int(stats["tokens_in"]),
                "tokens_out": int(stats["tokens_out"]),
            }
            for model, stats in ranked[:limit]
        ]

    def top_model(self, days: int) -> str | None:
        """Return the model that cost the most in the window."""
        ranked = self.top_models(days, 1)
        return ranked[0]["model"] if ranked else None

    def usage_breakdown(self, days: int) -> dict[str, dict[str, float]]:
        """Return per model cost and token totals for the window."""
        return {
            model: {
                "cost": round(stats["cost"], 10),
                "requests": stats["requests"],
                "tokens_in": stats["tokens_in"],
                "tokens_out": stats["tokens_out"],
            }
            for model, stats in self._model_totals(days).items()
        }
