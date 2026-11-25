"""Base entity, message conversion and chat loop for the Nous Research integration."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
import json
from typing import TYPE_CHECKING, Any, Final

import probatio

from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, llm
from homeassistant.helpers.entity import Entity

from .api import (
    NousApiError,
    NousAuthError,
    NousConnectionError,
    NousPaymentRequiredError,
    NousRateLimitError,
)
from .catalog import resolve_model
from .const import (
    CONF_CHAT_MODEL,
    CONF_MAX_TOKENS,
    CONF_REASONING_MODE,
    CONF_TEMPERATURE,
    DEFAULT_MAX_TOKENS,
    DEFAULT_REASONING_MODE,
    DEFAULT_TEMPERATURE,
    DOMAIN,
    MAX_TOOL_ITERATIONS,
    REASONING_PREFILL,
    REASONING_PROMPT,
    REASONING_SYSTEM_PROMPT,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry

    from . import NousConfigEntry

MANUFACTURER = "Nous Research"

# Device names are free text; keep them short enough for the registry UI.
_DEVICE_NAME_MAX: Final = 120

# The API client pre-fills this to force models into a reasoning block.
PREFILL_THINKING = " thinking"

# Tags some models emit inline instead of using a dedicated field.
THINKING_OPEN_TAGS = (" thinking", "<thinking>")
THINKING_CLOSE_TAGS = ("</think>", "</thinking>")


def _json_default(value: Any) -> Any:
    """Serialize a value json does not understand as a string."""
    return str(value)


def format_tool(tool: llm.Tool, serializer: Callable[[Any], Any]) -> dict[str, Any]:
    """Format a Home Assistant tool as an OpenAI compatible tool definition."""
    default = serializer or _json_default
    try:
        parameters: Any = probatio.to_openapi(
            tool.parameters, custom_serializer=default, openapi_version="3.1.0"
        )
    except Exception:  # noqa: BLE001 - fall back to the raw voluptuous schema
        try:
            parameters = json.loads(
                json.dumps(
                    getattr(tool.parameters, "schema", {}),
                    default=default,
                    ensure_ascii=False,
                )
            )
        except (TypeError, ValueError):
            parameters = {}
    if not isinstance(parameters, dict):
        parameters = {}
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": parameters,
        },
    }


def convert_content(content: conversation.Content) -> dict[str, Any] | None:
    """Convert a chat log content item into an OpenAI compatible message."""
    if isinstance(content, conversation.SystemContent):
        return {"role": "system", "content": content.content}

    if isinstance(content, conversation.UserContent):
        if content.attachments:
            raise HomeAssistantError(
                "The Nous Research API does not support attachments",
                translation_domain=DOMAIN,
                translation_key="nous_research_error_image_unsupported",
            )
        return {"role": "user", "content": content.content}

    if isinstance(content, conversation.AssistantContent):
        message: dict[str, Any] = {
            "role": "assistant",
            "content": content.content or "",
        }
        if content.tool_calls:
            message["tool_calls"] = [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.tool_name,
                        "arguments": json.dumps(tool_call.tool_args, ensure_ascii=False),
                    },
                }
                for tool_call in content.tool_calls
            ]
        return message

    if isinstance(content, conversation.ToolResultContent):
        return {
            "role": "tool",
            "tool_call_id": content.tool_call_id,
            "content": json.dumps(
                {
                    "success": not content.result.error,
                    "result": content.result.data,
                },
                ensure_ascii=False,
                default=_json_default,
            ),
        }

    return None


def _split_reasoning(
    text: str, in_reasoning: bool, carry: str
) -> tuple[str, str, bool, str]:
    """Split one text chunk into answer text and reasoning text.

    Returns the answer text, the reasoning text, whether the model is still
    inside a reasoning block, and the trailing characters held back because
    they may be the beginning of a reasoning tag cut in half by streaming.
    """
    content: list[str] = []
    reasoning: list[str] = []

    if not in_reasoning:
        for tag in THINKING_OPEN_TAGS:
            if tag in text:
                head, _, text = text.partition(tag)
                content.append(head)
                in_reasoning = True
                break

    if not in_reasoning:
        content.append(text)
        return "".join(content), "", False, ""

    buffer = f"{carry}{text}"
    carry = ""
    position = -1
    closing = ""
    for tag in THINKING_CLOSE_TAGS:
        found = buffer.find(tag)
        if found != -1 and (position == -1 or found < position):
            position, closing = found, tag

    if position == -1:
        hold = 0
        for tag in THINKING_CLOSE_TAGS:
            for size in range(min(len(tag) - 1, len(buffer)), 0, -1):
                if size > hold and buffer.endswith(tag[:size]):
                    hold = size
        if hold:
            carry = buffer[-hold:]
            buffer = buffer[:-hold]
        reasoning.append(buffer)
        return "".join(content), "".join(reasoning), True, carry

    reasoning.append(buffer[:position])
    content.append(buffer[position + len(closing) :])
    return "".join(content), "".join(reasoning), False, carry


def _structure_prompt(
    structure: probatio.Schema, serializer: Callable[[Any], Any]
) -> str:
    """Build the system instruction that enforces a structured answer."""
    try:
        schema: Any = probatio.to_openapi(
            structure, custom_serializer=serializer, openapi_version="3.1.0"
        )
        payload = json.dumps(schema, ensure_ascii=False, default=_json_default)
    except Exception:  # noqa: BLE001 - any failure falls back to a plain rendering
        payload = str(structure)
    return (
        "IMPORTANT: Answer with a single JSON object that validates against this "
        f"OpenAPI schema and nothing else: {payload}"
    )


class NousBaseEntity(Entity):
    """Base entity holding the client and cost tracker of a config entry."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, entry: NousConfigEntry, subentry: ConfigSubentry | None = None
    ) -> None:
        """Initialize the entity from the entry runtime data.

        ``subentry`` identifies the extra model an entity serves; it overrides
        the entry settings for that entity only.
        """
        self.entry = entry
        self.subentry = subentry
        self.client = entry.runtime_data.client
        self.tracker = entry.runtime_data.tracker
        # One device per subentry: a device belongs to a single config subentry
        # and moving an existing device between subentries makes HA drop the
        # entities that were registered under the old subentry. Keep the entry
        # device stable and give every model subentry a device of its own.
        if subentry is None:
            identifiers = {(DOMAIN, entry.entry_id)}
            device_name = entry.title
        else:
            identifiers = {(DOMAIN, f"{entry.entry_id}_{subentry.subentry_id}")}
            # The model devices sit next to the account device, so their name
            # carries the subentry title to stay distinguishable in the UI.
            device_name = f"{entry.title} {subentry.title}"[:_DEVICE_NAME_MAX]
        self._attr_device_info = dr.DeviceInfo(
            identifiers=identifiers,
            name=device_name,
            manufacturer=MANUFACTURER,
            model=self._device_model,
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    @property
    def _settings(self) -> dict[str, Any]:
        """Return entry data, then options, then subentry data on top."""
        settings = {**self.entry.data, **self.entry.options}
        if self.subentry is not None:
            settings.update(self.subentry.data)
        return settings

    @property
    def _device_model(self) -> str:
        """Return the model this entity serves, as shown on its device.

        A config entry is an API account and carries no model of its own; only
        ``model`` subentries do, so the account device is labelled as the API.
        """
        configured = str(self._settings.get(CONF_CHAT_MODEL) or "").strip()
        if not configured:
            return "API account"
        return resolve_model(configured, self._catalog)

    @property
    def _catalog(self) -> list[dict[str, Any]]:
        """Return the latest model catalog entries."""
        runtime = getattr(self.entry, "runtime_data", None)
        coordinator = getattr(runtime, "catalog", None)
        return list(getattr(coordinator, "data", None) or [])

    @property
    def _model(self) -> str:
        """Return the model to use, resolved against the live catalog.

        The catalog is namespaced and evolves, so a stale or renamed id would
        make every request fail; the catalog default is the fallback. Entities
        without a configured model (the API account device) have no agent.
        """
        configured = str(self._settings.get(CONF_CHAT_MODEL) or "").strip()
        if not configured:
            raise HomeAssistantError(
                "No model is configured for this entity; add a model subentry."
            )
        return resolve_model(configured, self._catalog)

    def _build_messages(
        self,
        chat_log: conversation.ChatLog,
        structure: probatio.Schema | None,
        reasoning_mode: str,
    ) -> list[dict[str, Any]]:
        """Rebuild the request messages from the chat log."""
        messages = [
            message
            for content in chat_log.content
            if (message := convert_content(content)) is not None
        ]

        instructions: list[str] = []
        if structure is not None:
            serializer: Callable[[Any], Any] = (
                chat_log.llm_api.custom_serializer
                if chat_log.llm_api
                else llm.selector_serializer
            )
            instructions.append(_structure_prompt(structure, serializer))
        if reasoning_mode == REASONING_PROMPT:
            instructions.append(REASONING_SYSTEM_PROMPT)

        if instructions:
            addition = "\n\n".join(instructions)
            for message in messages:
                if message["role"] == "system":
                    message["content"] = f"{message['content']}\n\n{addition}"
                    break
            else:
                messages.insert(0, {"role": "system", "content": addition})

        return messages

    async def _transform(
        self,
        stream: AsyncIterator[dict[str, Any]],
        chat_log: conversation.ChatLog | None = None,
    ) -> AsyncIterator[conversation.AssistantContentDeltaDict]:
        """Translate client stream events into chat log deltas."""
        settings = self._settings
        model = self._model
        # A prefill starts the model inside the reasoning block, so everything
        # before the closing tag is thinking and never part of the answer.
        in_reasoning = (
            settings.get(CONF_REASONING_MODE, DEFAULT_REASONING_MODE)
            == REASONING_PREFILL
        )
        carry = ""
        # A delta carrying a role starts a new message in the chat log, so the
        # role is only sent along with the first delta of each message.
        new_message = True

        async for event in stream:
            event_type = event.get("type")

            if event_type == "delta":
                text, thinking, in_reasoning, carry = _split_reasoning(
                    event.get("text") or "", in_reasoning, carry
                )
                thinking = f"{event.get('thinking') or ''}{thinking}"
                delta: conversation.AssistantContentDeltaDict = {}
                if text:
                    delta["content"] = text
                if thinking:
                    delta["thinking_content"] = thinking
                if not delta:
                    continue
                if new_message:
                    delta["role"] = "assistant"
                    new_message = False
                yield delta

            elif event_type in ("tool_calls", "usage"):
                if carry:
                    flush: conversation.AssistantContentDeltaDict = (
                        {"thinking_content": carry}
                        if in_reasoning
                        else {"content": carry}
                    )
                    if new_message:
                        flush["role"] = "assistant"
                        new_message = False
                    yield flush
                    carry = ""

                if event_type == "tool_calls":
                    tool_inputs = [
                        llm.ToolInput(
                            tool_name=tool_call.name,
                            tool_args=tool_call.parsed_arguments(),
                            id=tool_call.id,
                        )
                        for tool_call in event.get("tool_calls") or []
                        if tool_call.name
                    ]
                    if not tool_inputs:
                        continue
                    # Tool calls always start a fresh assistant message.
                    yield {"role": "assistant", "tool_calls": tool_inputs}
                    # Tool results are followed by a new assistant message.
                    new_message = True
                    continue

                usage = event.get("usage")
                if usage is not None:
                    await self.tracker.async_record(event.get("model") or model, usage)

    async def _async_handle_chat_log(
        self,
        chat_log: conversation.ChatLog,
        structure: probatio.Schema | None = None,
        max_iterations: int = MAX_TOOL_ITERATIONS,
    ) -> None:
        """Run the model until it answers or the tool budget is spent."""
        settings = self._settings
        model: str = self._model
        max_tokens: int = settings.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)
        temperature: float = settings.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE)
        reasoning_mode: str = settings.get(CONF_REASONING_MODE, DEFAULT_REASONING_MODE)
        prefill = PREFILL_THINKING if reasoning_mode == REASONING_PREFILL else None

        tools: list[dict[str, Any]] | None = None
        if chat_log.llm_api:
            tools = [
                format_tool(tool, chat_log.llm_api.custom_serializer)
                for tool in chat_log.llm_api.tools
            ]

        for _iteration in range(max_iterations):
            messages = self._build_messages(chat_log, structure, reasoning_mode)
            try:
                stream = self.client.async_chat_stream(
                    model,
                    messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tools=tools,
                    prefill=prefill,
                )
                async for _content in chat_log.async_add_delta_content_stream(
                    self.entity_id, self._transform(stream, chat_log)
                ):
                    pass
            except NousAuthError as err:
                raise HomeAssistantError(
                    "Authentication with the Nous Research API failed",
                    translation_domain=DOMAIN,
                    translation_key="nous_research_error_auth",
                ) from err
            except NousPaymentRequiredError as err:
                raise HomeAssistantError(
                    "The Nous Research account has no credits left",
                    translation_domain=DOMAIN,
                    translation_key="nous_research_error_no_credits",
                ) from err
            except NousRateLimitError as err:
                raise HomeAssistantError(
                    "The Nous Research API rate limit was exceeded",
                    translation_domain=DOMAIN,
                    translation_key="nous_research_error_rate_limit",
                ) from err
            except NousConnectionError as err:
                raise HomeAssistantError(
                    str(err),
                    translation_domain=DOMAIN,
                    translation_key="nous_research_error_api",
                    translation_placeholders={"error": str(err)},
                ) from err
            except NousApiError as err:
                raise HomeAssistantError(
                    str(err),
                    translation_domain=DOMAIN,
                    translation_key="nous_research_error_api",
                    translation_placeholders={"error": str(err)},
                ) from err

            if not chat_log.unresponded_tool_results:
                break
