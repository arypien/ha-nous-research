"""Config flow for the Nous Research integration."""

from __future__ import annotations

from collections.abc import Iterable

import hashlib
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
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
from .catalog import is_chat_model, model_id, pick_default_model
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
    DEFAULT_MAX_TOKENS,
    DEFAULT_NAME,
    DEFAULT_REASONING_MODE,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TEMPERATURE,
    DOMAIN,
    REASONING_MODES,
    SUBENTRY_TYPE_MODEL,
)

_LOGGER = logging.getLogger(__package__)

# Entry fields that are not exposed by const.py
CONF_NAME = "name"

LLM_API_NONE = "none"
LLM_API_ASSIST = "assist"
LLM_API_CONVERSATION = "conversation"
LLM_HASS_API_OPTIONS = [LLM_API_NONE, LLM_API_ASSIST, LLM_API_CONVERSATION]


def _unique_id(api_key: str) -> str:
    """Return the stable unique id of an API account.

    The id is derived from the key alone: an entry is one account, and the
    models it serves are subentries, so the same key can never be added twice.
    """
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def _uses_key(entry: ConfigEntry, api_key: str) -> bool:
    """Return True when the entry already stores this API key."""
    return str(entry.data.get(CONF_API_KEY) or "") == api_key


def _model_in_use(
    entry: ConfigEntry, model: str, subentry_id: str | None = None
) -> bool:
    """Return True when a ``model`` subentry already serves this model.

    A config entry carries no model of its own, so only the subentries are
    compared; the subentry being reconfigured is ignored so saving it
    unchanged stays possible.
    """
    return any(
        subentry.subentry_id != subentry_id
        and str(subentry.data.get(CONF_CHAT_MODEL) or "") == model
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_MODEL
    )


def _async_model_conflict(
    hass: HomeAssistant, entry: ConfigEntry, model: str
) -> str | None:
    """Return the error key when ``model`` is already served by this entry."""
    if _model_in_use(entry, model):
        return "model_in_use"
    return None


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


def _chat_model_options_with(
    catalog: Iterable[dict[str, Any]] | None, current: str
) -> list[dict[str, str]]:
    """Build dropdown entries, keeping ``current`` selectable when missing."""
    options = _chat_model_options(catalog)
    if current and current not in {option["value"] for option in options}:
        options.insert(0, {"value": current, "label": current})
    return options


async def _async_fetch_catalog(
    hass: HomeAssistant, api_key: str, base_url: str
) -> tuple[list[dict[str, Any]], str | None]:
    """Return the model catalog and, on failure, the error key to show.

    The catalog call doubles as credential check: it is the cheapest request
    that proves the key works, so its failure reason is what the form reports.
    """
    client = NousClient(hass, api_key, base_url)
    try:
        return await client.async_list_models(), None
    except NousAuthError:
        return [], "invalid_auth"
    except NousPaymentRequiredError:
        return [], "no_credits"
    except NousRateLimitError:
        return [], "rate_limited"
    except (NousApiError, NousConnectionError) as err:
        _LOGGER.debug("Could not fetch the model catalog: %s", err)
        return [], "cannot_connect"
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected error while fetching the model catalog")
        return [], "unknown"


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
    """Handle a config flow for Nous Research.

    Version 2 entries are API accounts: they store the key only. Models live in
    ``model`` subentries, each one carrying its own agent, task entity and
    per model sensors.
    """

    VERSION = 2

    def __init__(self) -> None:
        """Initialize the flow."""
        super().__init__()
        self._api_key = ""
        self._base_url = DEFAULT_BASE_URL

    async def _async_fetch_models(
        self, api_key: str, base_url: str
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Return the model catalog and the error key when the call fails."""
        return await _async_fetch_catalog(self.hass, api_key, base_url)

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
                models, error = await self._async_fetch_models(api_key, base_url)
                if models:
                    await self.async_set_unique_id(_unique_id(api_key))
                    self._abort_if_unique_id_configured()
                    if any(
                        _uses_key(other, api_key)
                        for other in self._async_current_entries()
                    ):
                        return self.async_abort(reason="already_configured")
                    return self.async_create_entry(
                        title=name,
                        data={
                            CONF_API_KEY: api_key,
                            CONF_BASE_URL: base_url,
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
                errors["base"] = error or "cannot_connect"

        return self._async_show_user_form(user_input, errors)

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentry types this integration supports."""
        return {SUBENTRY_TYPE_MODEL: ModelSubentryFlow}

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

            if not api_key:
                errors[CONF_API_KEY] = "invalid_auth"
            elif any(
                other.entry_id != entry.entry_id and _uses_key(other, api_key)
                for other in self._async_current_entries()
            ):
                return self.async_abort(reason="already_configured")
            else:
                models, error = await self._async_fetch_models(api_key, base_url)
                if not models:
                    # A rejected key must not be stored: the catalog call is the
                    # cheapest proof the credentials still work.
                    errors["base"] = error or "cannot_connect"

            if not errors:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_API_KEY: api_key},
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
        errors: dict[str, str] = {}

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
                    CONF_COST_SENSORS: bool(
                        user_input.get(CONF_COST_SENSORS, True)
                    ),
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
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)


class ModelSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure an extra model served by one config entry.

    Every subentry becomes its own conversation agent (a ``conversation.*``
    entity, selectable in Assist and in pipelines by entity id) and its own AI
    Task entity. Key, prompt, tools and cost sensors are shared with the entry.
    """

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add another model to an existing entry."""
        return await self._async_model_step(user_input, subentry=None)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change the model or the title of an existing subentry."""
        return await self._async_model_step(
            user_input, subentry=self._get_reconfigure_subentry()
        )

    async def _async_model_step(
        self, user_input: dict[str, Any] | None, subentry: ConfigSubentry | None
    ) -> SubentryFlowResult:
        """Show the model form and store the selection."""
        entry = self._get_entry()
        catalog = await self._async_catalog(entry)
        current = str(subentry.data.get(CONF_CHAT_MODEL) or "") if subentry else ""
        default_model = current or pick_default_model(catalog)
        default_title = subentry.title if subentry else ""
        errors: dict[str, str] = {}

        if user_input is not None:
            model = str(user_input.get(CONF_CHAT_MODEL) or "").strip() or default_model
            title = (
                str(user_input.get(CONF_NAME) or "").strip()
                or default_title
                or model
            )

            if not model:
                errors[CONF_CHAT_MODEL] = "model_required"
            elif _model_in_use(
                entry,
                model,
                subentry.subentry_id if subentry is not None else None,
            ):
                errors[CONF_CHAT_MODEL] = "model_in_use"
            else:
                # Reuse the entry credentials: a subentry never stores its own key.
                errors = await _async_validate(
                    self.hass,
                    str(entry.data.get(CONF_API_KEY) or ""),
                    entry.data.get(CONF_BASE_URL) or DEFAULT_BASE_URL,
                    model,
                )

            if not errors:
                data = {CONF_CHAT_MODEL: model}
                if subentry is None:
                    return self.async_create_entry(
                        title=title, data=data, unique_id=model
                    )
                # async_update_reload_and_abort refuses entries with update
                # listeners; the entry update listener triggers the reload that
                # recreates the AI Task entities.
                return self.async_update_and_abort(
                    entry, subentry, title=title, data=data, unique_id=model
                )

        return self.async_show_form(
            step_id="reconfigure" if subentry is not None else "user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CHAT_MODEL, default=default_model
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=_chat_model_options_with(catalog, default_model),
                            custom_value=True,
                        )
                    ),
                    vol.Optional(CONF_NAME, default=default_title): TextSelector(),
                }
            ),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def _async_catalog(self, entry: ConfigEntry) -> list[dict[str, Any]]:
        """Return the model catalog, preferring the loaded entry coordinator."""
        runtime = getattr(entry, "runtime_data", None)
        coordinator = getattr(runtime, "catalog", None)
        models = list(getattr(coordinator, "data", None) or [])
        if models:
            return models
        catalog, _ = await _async_fetch_catalog(
            self.hass,
            str(entry.data.get(CONF_API_KEY) or ""),
            entry.data.get(CONF_BASE_URL) or DEFAULT_BASE_URL,
        )
        return catalog
