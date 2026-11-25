"""The conversation platform for the Nous Research integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, override

from homeassistant.components import conversation
from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, SUBENTRY_TYPE_MODEL
from .entity import NousBaseEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry

    from . import NousConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NousConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one conversation agent per ``model`` subentry.

    A config entry is an API account: it carries the key and the aggregate
    sensors but no model of its own. Every model is added as a subentry and
    gets its own agent, so agents are selectable in Assist and in pipelines by
    entity id.
    """
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_MODEL:
            continue
        async_add_entities(
            [NousConversationEntity(entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class NousConversationEntity(
    conversation.ConversationEntity,
    conversation.AbstractConversationAgent,
    NousBaseEntity,
):
    """Nous Research conversation agent."""

    _attr_name = None
    _attr_supports_streaming = True

    def __init__(self, entry: NousConfigEntry, subentry: ConfigSubentry) -> None:
        """Initialize the agent."""
        super().__init__(entry, subentry)
        self._attr_unique_id = f"{subentry.subentry_id}_conversation"
        # _attr_name stays None: the device is named after the subentry, so the
        # agent shows up as "Nous Research <model>" in the Assist picker.
        # A subentry carries only its model: the LLM API, prompt, token limits
        # and the rest still come from the entry options via self._settings.
        if self._settings.get(CONF_LLM_HASS_API):
            self._attr_supported_features = (
                conversation.ConversationEntityFeature.CONTROL
            )

    @property
    @override
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    @override
    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Call the Nous Research API."""
        settings = self._settings

        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN),
                settings.get(CONF_LLM_HASS_API),
                settings.get(CONF_PROMPT),
                user_input.extra_system_prompt,
            )
        except conversation.ConverseError as err:
            return err.as_conversation_result()

        await self._async_handle_chat_log(chat_log)

        return conversation.async_get_result_from_chat_log(user_input, chat_log)
