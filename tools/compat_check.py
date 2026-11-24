"""Check the Home Assistant APIs used by the integration exist in this HA version."""

from __future__ import annotations

import inspect
import sys

from homeassistant.components import ai_task, conversation
from homeassistant.components.conversation import ConversationEntity
from homeassistant.components.conversation.chat_log import ChatLog
from homeassistant.const import __version__

failures: list[str] = []


def check(condition: bool, label: str) -> None:
    """Record a failure when an API is missing."""
    if not condition:
        failures.append(label)


def has_attr(obj: object, name: str) -> bool:
    """Return True when the attribute exists."""
    return hasattr(obj, name)


chat_log_members = [
    "async_add_delta_content_stream",
    "async_provide_llm_data",
    "unresponded_tool_results",
    "async_add_assistant_content_without_tools",
]
for member in chat_log_members:
    check(has_attr(ChatLog, member), f"ChatLog.{member}")

check(has_attr(conversation, "async_get_chat_log"), "conversation.async_get_chat_log")

for member in ("_attr_supports_streaming", "async_process"):
    check(has_attr(ConversationEntity, member), f"ConversationEntity.{member}")

for member in ("GenDataTask", "GenDataTaskResult", "AITaskEntity", "AITaskEntityFeature"):
    check(has_attr(ai_task, member), f"ai_task.{member}")

sig = inspect.signature(ChatLog.async_add_delta_content_stream)
params = list(sig.parameters)
check("chat_session" in " ".join(params) or len(params) >= 3, "delta stream signature")

delta_params = list(
    inspect.signature(ChatLog.async_add_delta_content_stream).parameters
)
print(f"HA {__version__}")
print(f"delta stream params: {delta_params}")
print("failures:", failures or "none")
sys.exit(1 if failures else 0)
