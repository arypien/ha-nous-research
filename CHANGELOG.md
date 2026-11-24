# Changelog

## 0.1.1

- Declare the Marketplace/HACS category `ai_voice.ai_models` in the manifest.

## 0.1.0 — initial public release

- Conversation agent for the Nous Research inference API with streaming replies and
  Assist tool calling (up to six tool rounds).
- AI Task entity for structured generation (`ai_task.generate_data`).
- Hermes 4 reasoning modes: off, documented thinking system prompt, think-tag prefill.
- Live model catalog from `/v1/models` with namespaced ids, filtering of batch,
  embedding, TTS and image-only models, and a repair issue when a stored model
  disappears.
- Local cost tracking: today / 7d / 30d / total, token and request counters, optional
  monthly budget, long-term `total` statistics throttled to once per minute.
- Price sensors for the selected model, prompt and completion, in USD per 1M tokens
  with no padding zeros (`0.03`, not `0.0300`).
- Entity names, config flow and error messages translated to English and Polish.
- `nous_research.reset_usage` service, diagnostics with the API key redacted.
