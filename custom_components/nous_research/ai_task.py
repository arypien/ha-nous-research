"""AI Task platform for the Nous Research integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from homeassistant.components import ai_task, conversation
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.json import json_loads

from .const import DOMAIN
from .entity import NousBaseEntity

if TYPE_CHECKING:
    from . import NousConfigEntry

# Language hints that may follow the opening markdown fence.
_FENCE_LANGUAGES: Final = frozenset(
    {"json", "json5", "jsonc", "javascript", "js", "python", "yaml", "yml"}
)

# The AI Task platform runs long, tool heavy conversations; let the agent loop
# run until the model is done instead of the conversation default.
_MAX_TASK_ITERATIONS: Final = 1000


def _strip_code_fence(text: str) -> str:
    """Return a model answer without surrounding markdown code fences."""
    payload = text.strip()
    if not payload.startswith("```"):
        return payload

    payload = payload[3:]
    first_line, separator, remainder = payload.partition("\n")
    hint = first_line.strip().lower()
    if separator and (not hint or hint in _FENCE_LANGUAGES or " " not in hint):
        payload = remainder

    payload = payload.rstrip()
    if payload.endswith("```"):
        payload = payload[:-3]
    return payload.strip()


class NousAITaskEntity(ai_task.AITaskEntity, NousBaseEntity):
    """Generate structured data with a Nous Research model."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_supported_features = ai_task.AITaskEntityFeature.GENERATE_DATA

    def __init__(self, entry: NousConfigEntry) -> None:
        """Initialize the AI Task entity."""
        # MRO resolves to NousBaseEntity.__init__(entry): AITaskEntity and
        # RestoreEntity do not define their own constructor.
        super().__init__(entry)
        self._attr_unique_id = f"{entry.entry_id}_ai_task"

    async def _async_generate_data(
        self, task: ai_task.GenDataTask, chat_log: conversation.ChatLog
    ) -> ai_task.GenDataTaskResult:
        """Handle a generate data task."""
        await self._async_handle_chat_log(
            chat_log, structure=task.structure, max_iterations=_MAX_TASK_ITERATIONS
        )

        if not chat_log.content:
            raise HomeAssistantError("The model returned an empty conversation")

        last_content = chat_log.content[-1]
        if not isinstance(last_content, conversation.AssistantContent):
            raise HomeAssistantError(
                "The model did not return an assistant message"
            )

        text = last_content.content or ""

        if task.structure is None:
            return ai_task.GenDataTaskResult(
                conversation_id=chat_log.conversation_id, data=text
            )

        payload = _strip_code_fence(text)
        try:
            data: Any = json_loads(payload)
        except (TypeError, ValueError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="structured_error"
            ) from err

        return ai_task.GenDataTaskResult(
            conversation_id=chat_log.conversation_id, data=data
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NousConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Nous Research AI Task entity from a config entry."""
    async_add_entities([NousAITaskEntity(entry)])
