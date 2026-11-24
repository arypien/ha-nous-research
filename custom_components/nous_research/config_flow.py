"""Config flow for the Nous Research integration."""

from __future__ import annotations

from collections.abc import Iterable

import hashlib
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API, CONF_PROMPT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    NousApiError,
    NousAuthError,
    NousClient,
    NousConnectionError,
    NousPaymentRequiredError,
    NousRateLimitError,
)
from .catalog import is_chat_model, model_id, model_ids, pick_default_model
from .const import (
    CONF_BASE_URL,
    CONF_BUDGET,
    CONF_CATALOG_INTERVAL,
    CONF_CHAT_MODEL,
    CONF_COST_SENSORS,
    CONF_MAX_TOKENS,
    CONF_REASONING_MODE,
    CONF_TEMPERATURE,
    DEFAULT_BASE_URL,
    DEFAULT_CATALOG_INTERVAL,
    DEFAULT_CHAT_MODEL,
    DEFAULT_MAX_TOKENS,
    DEFAULT_NAME,
    DEFAULT_REASONING_MODE,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TEMPERATURE,
    DOMAIN,
    REASONING_MODES,
)

_LOGGER = logging.getLogger(__package__)

# Entry fields that are not exposed by const.py
CONF_NAME = "name"

LLM_API_NONE = "none"
LLM_API_ASSIST = "assist"
LLM_API_CONVERSATION = "conversation"
LLM_HASS_API_OPTIONS = [LLM_API_NONE, LLM_API_ASSIST, LLM_API_CONVERSATION]


def _unique_id(api_key: str) -> str:
    """Return a stable unique id for an API key."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def _chat_model_options(
    catalog: Iterable[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    """Build searchable dropdown entries from the catalog."""
    options: list[dict[str, str]] = []
    for model in catalog or []:
        if not is_chat_model(model):
            continue
        identifier = model_id(model)
        name = str(model.get("name") or identifier)
        options.append({"value": identifier, "label": f"{name} ({identifier})"})
    return options


async def _async_validate(
    hass: HomeAssistant, api_key: str, base_url: str, model: str
) -> dict[str, str]:
    """Validate credentials with a minimal request, returning form errors."""
    client = NousClient(hass, api_key, base_url)
    try:
        await client.async_validate(model)
    except NousAuthError:
        return {"base": "invalid_auth"}
    except NousPaymentRequiredError:
        return {"base": "no_credits"}
    except NousRateLimitError:
        return {"base": "rate_limited"}
    except (NousConnectionError, NousApiError):
        return {"base": "cannot_connect"}
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected error while validating the Nous API key")
        return {"base": "unknown"}
    return {}


class NousConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Nous Research."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        super().__init__()
        self._catalog: list[dict[str, Any]] = []
        self._api_key = ""
        self._base_url = DEFAULT_BASE_URL
        self._name = DEFAULT_NAME

    async def _async_fetch_models(
        self, api_key: str, base_url: str
    ) -> list[dict[str, Any]]:
        """Return the model catalog from the API, best effort."""
        client = NousClient(self.hass, api_key, base_url)
        try:
            return await client.async_list_models()
        except (NousApiError, NousConnectionError, NousAuthError) as err:
            _LOGGER.debug("Could not fetch the model catalog: %s", err)
            return []
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error while fetching the model catalog")
            return []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the credentials, then pick a model from the live catalog."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = str(user_input.get(CONF_API_KEY) or "").strip()
            base_url = str(user_input.get(CONF_BASE_URL) or DEFAULT_BASE_URL).strip()
            name = str(user_input.get(CONF_NAME) or "").strip() or DEFAULT_NAME

            if not api_key:
                errors[CONF_API_KEY] = "invalid_auth"
            if not base_url.startswith(("http://", "https://")):
                errors[CONF_BASE_URL] = "invalid_url"

            if not errors:
                models = await self._async_fetch_models(api_key, base_url)
                if models:
                    self._catalog = models
                    self._api_key = api_key
                    self._base_url = base_url
                    self._name = name
                    return await self.async_step_model()
                errors["base"] = "cannot_connect"

        return self._async_show_user_form(user_input, errors)

    async def async_step_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the chat model out of the catalog reported by the API."""
        errors: dict[str, str] = {}
        default_model = pick_default_model(self._catalog)

        if user_input is not None:
            model = str(user_input.get(CONF_CHAT_MODEL) or "").strip() or default_model
            if not model:
                errors[CONF_CHAT_MODEL] = "model_required"
            else:
                errors = await _async_validate(
                    self.hass, self._api_key, self._base_url, model
                )

            if not errors:
                await self.async_set_unique_id(_unique_id(self._api_key))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=self._name,
                    data={
                        CONF_API_KEY: self._api_key,
                        CONF_BASE_URL: self._base_url,
                        CONF_CHAT_MODEL: model,
                    },
                    options={
                        CONF_PROMPT: DEFAULT_SYSTEM_PROMPT,
                        CONF_LLM_HASS_API: LLM_API_ASSIST,
                        CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
                        CONF_TEMPERATURE: DEFAULT_TEMPERATURE,
                        CONF_REASONING_MODE: DEFAULT_REASONING_MODE,
                        CONF_COST_SENSORS: True,
                        CONF_CATALOG_INTERVAL: DEFAULT_CATALOG_INTERVAL,
                    },
                )

        options = _chat_model_options(self._catalog)
        if default_model and default_model not in {
            option["value"] for option in options
        }:
            options.insert(0, {"value": default_model, "label": default_model})
        schema = vol.Schema(
            {
                vol.Required(CONF_CHAT_MODEL, default=default_model): SelectSelector(
                    SelectSelectorConfig(options=options, custom_value=True)
                ),
            }
        )
        return self.async_show_form(step_id="model", data_schema=schema, errors=errors)

    def _async_show_user_form(
        self, user_input: dict[str, Any] | None, errors: dict[str, str]
    ) -> ConfigFlowResult:
        """Show the credentials form, keeping the values already typed."""
        values = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_API_KEY): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Optional(
                    CONF_BASE_URL, default=values.get(CONF_BASE_URL, DEFAULT_BASE_URL)
                ): TextSelector(),
                vol.Optional(
                    CONF_NAME, default=values.get(CONF_NAME, DEFAULT_NAME)
                ): TextSelector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle a reauthentication request."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a fresh API key."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = str(user_input.get(CONF_API_KEY) or "").strip()
            base_url = entry.data.get(CONF_BASE_URL) or DEFAULT_BASE_URL
            model = str(entry.data.get(CONF_CHAT_MODEL) or DEFAULT_CHAT_MODEL)

            if not api_key:
                errors[CONF_API_KEY] = "invalid_auth"
            else:
                digest = _unique_id(api_key)
                for other in self._async_current_entries():
                    if other.entry_id != entry.entry_id and other.unique_id == digest:
                        return self.async_abort(reason="already_configured")
                catalog = await self._async_fetch_models(api_key, base_url)
                if catalog and model not in model_ids(catalog):
                    # The catalog is the source of truth: a stale id would make
                    # every request fail.
                    model = pick_default_model(catalog)
                errors = await _async_validate(self.hass, api_key, base_url, model)

            if not errors:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_API_KEY: api_key, CONF_CHAT_MODEL: model},
                )
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> NousOptionsFlow:
        """Return the options flow."""
        return NousOptionsFlow()


class NousOptionsFlow(OptionsFlow):
    """Handle options for Nous Research."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the integration options."""
        entry = self.config_entry
        options = entry.options

        if user_input is not None:
            llm_api = user_input.get(CONF_LLM_HASS_API, LLM_API_NONE)
            budget = user_input.get(CONF_BUDGET)
            return self.async_create_entry(
                title="",
                data={
                    CONF_PROMPT: user_input.get(CONF_PROMPT)
                    or DEFAULT_SYSTEM_PROMPT,
                    CONF_LLM_HASS_API: None
                    if llm_api == LLM_API_NONE
                    else llm_api,
                    CONF_CHAT_MODEL: user_input.get(CONF_CHAT_MODEL)
                    or entry.data.get(CONF_CHAT_MODEL)
                    or DEFAULT_CHAT_MODEL,
                    CONF_MAX_TOKENS: int(
                        user_input.get(CONF_MAX_TOKENS) or DEFAULT_MAX_TOKENS
                    ),
                    CONF_TEMPERATURE: float(
                        user_input.get(CONF_TEMPERATURE)
                        if user_input.get(CONF_TEMPERATURE) is not None
                        else DEFAULT_TEMPERATURE
                    ),
                    CONF_REASONING_MODE: user_input.get(CONF_REASONING_MODE)
                    or DEFAULT_REASONING_MODE,
                    CONF_COST_SENSORS: bool(user_input.get(CONF_COST_SENSORS, True)),
                    CONF_BUDGET: None
                    if budget in (None, "")
                    else float(str(budget)),
                    CONF_CATALOG_INTERVAL: int(
                        user_input.get(CONF_CATALOG_INTERVAL)
                        or DEFAULT_CATALOG_INTERVAL
                    ),
                },
            )

        current_llm_api = options.get(CONF_LLM_HASS_API) or LLM_API_NONE
        if current_llm_api not in LLM_HASS_API_OPTIONS:
            current_llm_api = LLM_API_ASSIST
        runtime = getattr(entry, "runtime_data", None)
        coordinator = getattr(runtime, "catalog", None)
        model_options = _chat_model_options(getattr(coordinator, "data", None))
        current_model = (
            options.get(CONF_CHAT_MODEL)
            or entry.data.get(CONF_CHAT_MODEL)
            or DEFAULT_CHAT_MODEL
        )
        if current_model and current_model not in {
            option["value"] for option in model_options
        }:
            model_options.insert(0, {"value": current_model, "label": current_model})
        current_reasoning = options.get(CONF_REASONING_MODE) or DEFAULT_REASONING_MODE
        if current_reasoning not in REASONING_MODES:
            current_reasoning = DEFAULT_REASONING_MODE
        current_budget = options.get(CONF_BUDGET)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PROMPT,
                    default=options.get(CONF_PROMPT) or DEFAULT_SYSTEM_PROMPT,
                ): TextSelector(TextSelectorConfig(multiline=True)),
                vol.Required(
                    CONF_LLM_HASS_API, default=current_llm_api
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=LLM_HASS_API_OPTIONS, custom_value=False
                    )
                ),
                vol.Required(
                    CONF_CHAT_MODEL, default=current_model
                ): SelectSelector(
                    SelectSelectorConfig(options=model_options, custom_value=True)
                ),
                vol.Required(
                    CONF_MAX_TOKENS,
                    default=int(options.get(CONF_MAX_TOKENS) or DEFAULT_MAX_TOKENS),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=32768, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_TEMPERATURE,
                    default=float(
                        options.get(CONF_TEMPERATURE)
                        if options.get(CONF_TEMPERATURE) is not None
                        else DEFAULT_TEMPERATURE
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=2, step=0.1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(
                    CONF_REASONING_MODE, default=current_reasoning
                ): SelectSelector(
                    SelectSelectorConfig(options=REASONING_MODES, custom_value=False)
                ),
                vol.Required(
                    CONF_COST_SENSORS, default=bool(options.get(CONF_COST_SENSORS, True))
                ): BooleanSelector(),
                vol.Optional(
                    CONF_BUDGET,
                    default=None if current_budget is None else float(current_budget),
                ): vol.Any(
                    None,
                    NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=10000,
                            step=0.01,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                ),
                vol.Required(
                    CONF_CATALOG_INTERVAL,
                    default=int(
                        options.get(CONF_CATALOG_INTERVAL) or DEFAULT_CATALOG_INTERVAL
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=1, max=168, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
