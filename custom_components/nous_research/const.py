"""Constants for the Nous Research integration."""

from __future__ import annotations

import logging
from typing import Final

DOMAIN: Final = "nous_research"
LOGGER = logging.getLogger(__package__)

# Configuration keys
CONF_BASE_URL: Final = "base_url"
CONF_CHAT_MODEL: Final = "chat_model"
CONF_MAX_TOKENS: Final = "max_tokens"
CONF_TEMPERATURE: Final = "temperature"
CONF_REASONING_MODE: Final = "reasoning_mode"
CONF_BUDGET: Final = "budget_usd"
CONF_COST_SENSORS: Final = "cost_sensors"
CONF_CATALOG_INTERVAL: Final = "catalog_interval"

# Config subentry types
#: Extra model served by an entry as an additional AI Task entity.
SUBENTRY_TYPE_MODEL: Final = "model"

# Reasoning modes
REASONING_NONE: Final = "none"
REASONING_PROMPT: Final = "prompt"
REASONING_PREFILL: Final = "prefill"
REASONING_MODES: Final = [REASONING_NONE, REASONING_PROMPT, REASONING_PREFILL]

# Defaults
DEFAULT_BASE_URL: Final = "https://inference-api.nousresearch.com/v1"

#: The inference gateway wants a ``user=`` tag on every /chat/completions call.
#: Without it the request is refused with ``400 … Additional info: missing tags``,
#: which is what free (synthesized) variants hit on the very first probe.
API_TAGS: Final = ["user=home-assistant"]
#: No model id is hardcoded: the catalog is aggregated and namespaced
#: (``vendor/model``), so the value is resolved from the live catalog.
DEFAULT_CHAT_MODEL: Final = ""

#: Order of the model dropdown offered when a model is added or changed.
CONF_MODEL_SORT: Final = "model_sort"
SORT_PRICE_ASC: Final = "price_asc"
SORT_PRICE_DESC: Final = "price_desc"
SORT_NAME: Final = "name"
SORT_NEWEST: Final = "newest"
MODEL_SORTS: Final = [SORT_PRICE_ASC, SORT_PRICE_DESC, SORT_NAME, SORT_NEWEST]
#: Cheapest first: every free model lands on top of the list.
DEFAULT_MODEL_SORT: Final = SORT_PRICE_ASC
DEFAULT_MAX_TOKENS: Final = 1500
DEFAULT_TEMPERATURE: Final = 0.4
DEFAULT_REASONING_MODE: Final = REASONING_PROMPT
DEFAULT_CATALOG_INTERVAL: Final = 6  # hours
DEFAULT_NAME: Final = "Nous Research"

# System prompts
DEFAULT_SYSTEM_PROMPT: Final = (
    "You are a helpful and precise voice assistant for Home Assistant. "
    "Answer in the language of the user, keep replies short unless asked "
    "for details, and use the available tools to control the house. "
    "Never invent entity names, states or values."
)

# Documented by Nous Research: required to trigger Hermes 4 / DeepHermes reasoning
REASONING_SYSTEM_PROMPT: Final = (
    "You are a deep thinking AI, you may use extremely long chains of thought to "
    "deeply consider the problem and deliberate with yourself via systematic "
    "reasoning processes to help come to a correct solution prior to answering. "
    "You should enclose your thoughts and internal monologue inside  thinking </think> "
    "tags, and then provide your solution or response to the problem."
)

# Conversation / agent limits
MAX_TOOL_ITERATIONS: Final = 6

# Cost tracking
STORAGE_KEY: Final = f"{DOMAIN}.cost"
STORAGE_VERSION: Final = 1
STORAGE_VERSION_MINOR: Final = 1
SAVE_DELAY: Final = 15
HISTORY_DAYS: Final = 400  # daily buckets kept locally
STATISTICS_MIN_INTERVAL: Final = 60  # seconds between statistics pushes

# Catalog
CATALOG_TIMEOUT: Final = 30

DISCOVERY_SIGNAL: Final = f"{DOMAIN}_discovery_{{}}"
COST_UPDATE_SIGNAL: Final = f"{DOMAIN}_cost_{{}}"

SERVICE_RESET_USAGE: Final = "reset_usage"
