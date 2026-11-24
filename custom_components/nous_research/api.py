"""Async client for the Nous Research inference API (OpenAI compatible)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
import json
import logging
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DEFAULT_BASE_URL

_LOGGER = logging.getLogger(__package__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=300, sock_connect=15, sock_read=240)
VALIDATE_TIMEOUT = aiohttp.ClientTimeout(total=60, sock_connect=15, sock_read=45)


class NousError(Exception):
    """Base error for the Nous Research API."""


class NousAuthError(NousError):
    """Invalid or missing API key."""


class NousPaymentRequiredError(NousError):
    """Account has no credits left."""


class NousRateLimitError(NousError):
    """Rate limit hit."""


class NousConnectionError(NousError):
    """Network level failure."""


class NousApiError(NousError):
    """The API returned an unexpected answer."""

    def __init__(self, status: int, message: str) -> None:
        """Initialize the error."""
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


@dataclass(slots=True)
class NousUsage:
    """Token usage reported by the API for one request."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    @classmethod
    def from_api(cls, raw: Any) -> NousUsage | None:
        """Build usage from the API payload, returning None when absent."""
        if not isinstance(raw, dict):
            return None
        prompt = int(raw.get("prompt_tokens") or 0)
        completion = int(raw.get("completion_tokens") or 0)
        total = int(raw.get("total_tokens") or (prompt + completion))
        prompt_details = raw.get("prompt_tokens_details") or {}
        completion_details = raw.get("completion_tokens_details") or {}
        return cls(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            cached_tokens=int(prompt_details.get("cached_tokens") or 0),
            reasoning_tokens=int(completion_details.get("reasoning_tokens") or 0),
        )

    def as_dict(self) -> dict[str, int]:
        """Return the usage as a dictionary."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
            "reasoning_tokens": self.reasoning_tokens,
        }


@dataclass(slots=True)
class NousToolCall:
    """A tool call requested by the model."""

    id: str
    name: str
    arguments: str = "{}"

    def parsed_arguments(self) -> dict[str, Any]:
        """Return parsed arguments, tolerating invalid JSON."""
        try:
            parsed = json.loads(self.arguments or "{}")
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}


@dataclass(slots=True)
class NousChatResult:
    """Result of a chat completion."""

    model: str
    text: str = ""
    thinking: str = ""
    tool_calls: list[NousToolCall] = field(default_factory=list)
    usage: NousUsage | None = None
    finish_reason: str | None = None


class NousClient:
    """Small async client for the Nous Research inference API."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        """Initialize the client."""
        self.hass = hass
        self._api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._session = async_get_clientsession(hass)
        self.rate_limits: dict[str, str] = {}

    @property
    def headers(self) -> dict[str, str]:
        """Return request headers."""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _capture_limits(self, headers: aiohttp.typedefs.LooseHeaders) -> None:
        """Keep the latest rate limit headers for diagnostics."""
        captured = {
            key.lower(): value
            for key, value in (headers.items() if headers else ())
            if key.lower().startswith(("x-ratelimit", "ratelimit"))
        }
        if captured:
            self.rate_limits = captured

    @staticmethod
    def _error_message(body: str) -> str:
        """Extract a readable error message from an error payload."""
        try:
            payload = json.loads(body)
        except ValueError:
            return body[:300]
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or error)
            if error:
                return str(error)
            detail = payload.get("detail")
            if detail:
                return str(detail)
        return body[:300]

    def _handle_error(self, status: int, body: str) -> None:
        """Translate an HTTP error into a typed exception."""
        message = self._error_message(body)
        if status in (401, 403):
            raise NousAuthError(message)
        if status == 402:
            raise NousPaymentRequiredError(message)
        if status == 429:
            raise NousRateLimitError(message)
        raise NousApiError(status, message)

    async def async_list_models(self) -> list[dict[str, Any]]:
        """Return the public model catalog."""
        url = f"{self.base_url}/models"
        try:
            async with self._session.get(
                url, headers=self.headers, timeout=VALIDATE_TIMEOUT
            ) as resp:
                self._capture_limits(resp.headers)
                body = await resp.text()
                if resp.status != 200:
                    self._handle_error(resp.status, body)
        except aiohttp.ClientError as err:
            raise NousConnectionError(str(err)) from err
        except asyncio.TimeoutError as err:
            raise NousConnectionError("Timeout while reading the model catalog") from err
        try:
            payload = json.loads(body)
        except ValueError as err:
            raise NousApiError(resp.status, "Invalid JSON in model catalog") from err
        data = payload.get("data") if isinstance(payload, dict) else None
        return [model for model in data or [] if isinstance(model, dict)]

    def _build_payload(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None,
        temperature: float | None,
        tools: list[dict[str, Any]] | None,
        prefill: str | None,
        stream: bool,
    ) -> dict[str, Any]:
        """Build the request payload for /chat/completions."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "stream": stream,
        }
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)
        if temperature is not None:
            payload["temperature"] = float(temperature)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if prefill:
            payload["messages"].append({"role": "assistant", "content": prefill})
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    async def async_chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        prefill: str | None = None,
    ) -> NousChatResult:
        """Run a non streaming chat completion."""
        url = f"{self.base_url}/chat/completions"
        payload = self._build_payload(
            model,
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            prefill=prefill,
            stream=False,
        )
        try:
            async with self._session.post(
                url, json=payload, headers=self.headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                self._capture_limits(resp.headers)
                body = await resp.text()
                status = resp.status
        except aiohttp.ClientError as err:
            raise NousConnectionError(str(err)) from err
        except asyncio.TimeoutError as err:
            raise NousConnectionError("Timeout while calling the Nous API") from err
        if status != 200:
            self._handle_error(status, body)
        try:
            data = json.loads(body)
        except ValueError as err:
            raise NousApiError(status, "Invalid JSON in response") from err

        choices = data.get("choices") or []
        if not choices:
            raise NousApiError(status, "Response contains no choices")
        choice = choices[0]
        message = choice.get("message") or {}
        result = NousChatResult(
            model=data.get("model") or model,
            text=message.get("content") or "",
            thinking=message.get("reasoning_content") or message.get("reasoning") or "",
            usage=NousUsage.from_api(data.get("usage")),
            finish_reason=choice.get("finish_reason"),
        )
        for raw_call in message.get("tool_calls") or []:
            function = raw_call.get("function") or {}
            result.tool_calls.append(
                NousToolCall(
                    id=str(raw_call.get("id") or ""),
                    name=str(function.get("name") or ""),
                    arguments=function.get("arguments") or "{}",
                )
            )
        return result

    @staticmethod
    def _events_from_completion(
        document: dict[str, Any], model: str
    ) -> list[dict[str, Any]]:
        """Convert a non-streamed completion into stream-shaped events."""
        events: list[dict[str, Any]] = []
        choice = (document.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        if text := message.get("content"):
            events.append({"type": "delta", "text": text, "thinking": ""})
        calls = [
            NousToolCall(
                id=call.get("id") or f"call_{position}",
                name=(call.get("function") or {}).get("name") or "",
                arguments=(call.get("function") or {}).get("arguments") or "{}",
            )
            for position, call in enumerate(message.get("tool_calls") or [])
            if (call.get("function") or {}).get("name")
        ]
        if calls:
            events.append({"type": "tool_calls", "tool_calls": calls})
        events.append(
            {
                "type": "usage",
                "usage": NousUsage.from_api(document.get("usage")),
                "model": document.get("model") or model,
                "finish_reason": choice.get("finish_reason"),
            }
        )
        return events

    async def async_chat_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        prefill: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Run a streaming chat completion, yielding parsed events."""
        url = f"{self.base_url}/chat/completions"
        payload = self._build_payload(
            model,
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            prefill=prefill,
            stream=True,
        )
        tool_buffer: dict[int, dict[str, str]] = {}
        usage: NousUsage | None = None
        model_name = model
        finish_reason: str | None = None
        try:
            async with self._session.post(
                url, json=payload, headers=self.headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                self._capture_limits(resp.headers)
                status = resp.status
                if status != 200:
                    body = await resp.text()
                    self._handle_error(status, body)
                content_type = resp.headers.get("Content-Type", "")
                if "text/event-stream" not in content_type:
                    # Some gateways answer stream requests with a full JSON body.
                    document = json.loads(await resp.text())
                    for event in self._events_from_completion(document, model_name):
                        yield event
                    return
                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8", "replace").strip()
                    if "data:" not in line:
                        continue
                    raw = line.split("data:", 1)[1].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    usage = NousUsage.from_api(chunk.get("usage")) or usage
                    model_name = chunk.get("model") or model_name
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta") or {}
                    text = delta.get("content")
                    thinking = delta.get("reasoning_content") or delta.get("reasoning")
                    if text or thinking:
                        yield {
                            "type": "delta",
                            "text": text or "",
                            "thinking": thinking or "",
                        }
                    for position, raw_call in enumerate(delta.get("tool_calls") or []):
                        index = raw_call.get("index", position)
                        slot = tool_buffer.setdefault(
                            index, {"id": "", "name": "", "arguments": ""}
                        )
                        if raw_call.get("id"):
                            slot["id"] = raw_call["id"]
                        function = raw_call.get("function") or {}
                        if function.get("name"):
                            slot["name"] = function["name"]
                        if function.get("arguments"):
                            slot["arguments"] += function["arguments"]
        except aiohttp.ClientError as err:
            raise NousConnectionError(str(err)) from err
        except asyncio.TimeoutError as err:
            raise NousConnectionError("Timeout while calling the Nous API") from err
        if tool_buffer:
            tool_calls = [
                NousToolCall(
                    id=slot["id"] or f"call_{index}",
                    name=slot["name"],
                    arguments=slot["arguments"] or "{}",
                )
                for index, slot in sorted(tool_buffer.items())
                if slot["name"]
            ]
            if tool_calls:
                yield {"type": "tool_calls", "tool_calls": tool_calls}
        yield {
            "type": "usage",
            "usage": usage,
            "model": model_name,
            "finish_reason": finish_reason,
        }

    async def async_validate(self, model: str) -> None:
        """Validate the API key with the smallest possible request."""
        await self.async_chat(
            model, [{"role": "user", "content": "ping"}], max_tokens=1
        )
