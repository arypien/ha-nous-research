<p align="center"><img src="https://raw.githubusercontent.com/arypien/ha-nous-research/main/images/banner-dark.png" alt="Nous Research for Home Assistant" width="620"></p>

Talk to Hermes 4 and DeepHermes models from Assist, generate structured content with
AI Task, and keep an honest local tally of what you spend.

### What you get

- **Assist conversation agent** — streaming answers and real Home Assistant control
  (`HassTurnOn`, `HassLightSet`, `GetLiveContext`, …), up to six tool rounds.
- **AI Task entity** — `ai_task.generate_data` with your own structure schema.
- **One entry per key, one subentry per model** — add models to an account entry;
  each one gets its own Assist agent (`conversation.*`), its own AI Task entity
  and its own cost, token and price sensors.
- **Reasoning modes** — the documented Hermes thinking prompt, a cheap think-tag
  prefill, or off.
- **Cost and token sensors** — today / 7d / 30d / total, requests and top model on
  the account, per-model figures on each model device, plus prompt and completion
  prices (`0.03` USD per 1M, no padding zeros), with long-term statistics.
- **Live model catalog** — every price, context length and modality comes from
  `/v1/models`; no model id is hardcoded. A model that leaves the catalog raises a
  repair issue instead of failing every call.
- **Cheapest first** — the model dropdown is sorted by price by default (free models
  on top), and the order is yours to pick: price up, price down, name or newest.

You need an API key from [portal.nousresearch.com](https://portal.nousresearch.com) —
everything else is configured in the UI.
