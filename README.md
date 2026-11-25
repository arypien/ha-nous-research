<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="images/banner-readme-dark.png">
    <img alt="Nous Research for Home Assistant" src="images/banner-readme-light.png" width="760">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img alt="HACS custom repository" src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square"></a>
  <img alt="Home Assistant 2026.9.0+" src="https://img.shields.io/badge/Home%20Assistant-2026.9.0%2B-18BCF2.svg?style=flat-square">
  <a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-3DA639.svg?style=flat-square"></a>
  <img alt="Tests" src="https://img.shields.io/badge/tests-97%20passing-3DA639.svg?style=flat-square">
  <img alt="Translations" src="https://img.shields.io/badge/i18n-EN%20%7C%20PL-8A63D2.svg?style=flat-square">
</p>

<p align="center"><b>English</b> · <a href="README.pl.md">Polski</a></p>

Conversation agent, AI Task entity and local cost tracking for the
[Nous Research](https://nousresearch.com) inference API. Hermes 4 and DeepHermes
models get real Home Assistant control through Assist, plus honest accounting of
tokens and spend computed on your own instance.

> Unofficial community integration. Not affiliated with Nous Research — API usage
> is billed by them under their own terms.

## Features

- **Conversation agent** — streaming replies, Assist tool calling, up to six tool
  rounds per answer.
- **AI Task entity** — structured generation through `ai_task.generate_data`, the
  payload comes back in `data`, ready for automations.
- **Hermes 4 reasoning modes** — the documented thinking prompt or an opening
  `think` tag prefill.
- **Cost tracking** — today / 7 days / 30 days / total, token and request counters,
  optional monthly budget, all stored locally.
- **Per-model figures** — every model subentry has its own cost, token, request
  and price sensors, all starting at zero.
- **Live model catalog** — prices, context lengths and modalities straight from
  `/v1/models`; each model is validated against it.
- **Sorted model list** — the add-model dropdown opens **cheapest first**, so the free
  models sit on top instead of being buried between expensive siblings. The order is
  chosen once (price ascending, price descending, name, newest) — on the API key form
  or later under **Configure** — and every model entry is labelled with its price.
- **Long-term statistics** — cost sensors push `total` statistics, so history
  survives recorder purges and works in Statistics Graph cards.
- **Bilingual UI** — English and Polish, with a
  [translation guide](docs/TRANSLATING.md).

## Installation

### HACS (recommended)

1. HACS → **Integrations** → ⋮ → **Custom repositories**.
2. Add `https://github.com/arypien/ha-nous-research`, category **Integration**.
3. Search for **Nous Research** in HACS and install it.
4. Restart Home Assistant.

### Manual

Copy `custom_components/nous_research` from the release ZIP into
`config/custom_components/` and restart Home Assistant.

Requires **Home Assistant 2026.9.0 or newer** and an API key from
<https://portal.nousresearch.com>.

> **Upgrading from 0.1.2:** an entry becomes an API account, so its stored model is
> dropped (cost history is kept) and its Assist agent and AI Task entity disappear.
> Open the entry, press **Add model** and pick the model again — nothing else needs
> to be reconfigured.

## Configuration

1. Create an API key in the Nous portal.
2. **Settings → Devices & services → Add integration → Nous Research**.
3. Paste the key — the catalog is fetched right away, so a revoked key fails
   immediately instead of at first use. The form ends with **Model list order**, so the
   dropdown is already sorted the way you want it (changeable later under Configure).
4. On the new entry press **Add model** and pick a chat model from the list the
   API reports (searchable dropdown, free text allowed). Every option carries its
   price — `FREE` or `$in/$out per 1M` — and the field starts on a placeholder, so
   nothing is installed until you choose. If the API refuses the one-token test for
   a model (gated free tiers do), the form quotes the API's message and lets you
   store the model anyway. Repeat for more models.

| Field | Meaning |
| --- | --- |
| API key | Bearer token from the portal, stored in the config entry |
| Base URL | Defaults to `https://inference-api.nousresearch.com/v1` |
| Name | Entry name, also the prefix of the created entities |
| Model | Picked per subentry (after the entry exists), from the live `/models` catalog |
| Model list order | Order of that dropdown: cheapest first (default), most expensive first, name A-Z, newest |

No model id is hardcoded: the catalog is aggregated and namespaced
(`vendor/model`, ~300 chat models), so the list comes from the API. Batch endpoints
(`:batch`), embeddings, TTS and image-only models are filtered out. Nothing is
preselected and nothing is guessed: the model step refuses an id the endpoint does
not list (`invalid_model`), and a subentry whose model later disappears from the
catalog raises a repair issue and fails its requests with that message — it is never
quietly answered by some other model.

### Options

**Configure** on the entry covers the system prompt, exposed HA API, model,
`max_tokens`, `temperature`, reasoning mode, cost sensors, budget and catalog
refresh interval, **and the order of the model list**. The model is not an option: it
lives in the model subentries.

## Reasoning modes

Nous documents a specific system prompt to unlock long chains of thought. The modes
differ only in how it is triggered:

- **Off** — no thinking, lowest latency and cost.
- **System prompt** (default, recommended for Hermes 4) — sent as a system message
  next to your own prompt:

  > You are a deep thinking AI, you may use extremely long chains of thought to
  > deeply consider the problem and deliberate with yourself via systematic
  > reasoning processes to help you come to a correct solution prior to answering.
  > You should enclose your thoughts and internal monologue inside `think` tags,
  > and then provide your solution or response to the problem.

- **Prefill think tag** — the assistant turn is prefilled with an opening think
  tag, so the model continues inside it without the extra prompt. Cheaper,
  slightly less reliable on small models.

Thinking tokens count towards `max_tokens`; raise it if replies are truncated.

## Tool calling / Assist

**Home Assistant control** = `assist` exposes the Assist tools (`GetLiveContext`,
`HassTurnOn`, `HassTurnOff`, `HassLightSet`, `HassClimateSetTemperature`, …) to the
model. `conversation` additionally registers the agent for the Assist dialog. The
agent loops up to six tool rounds before answering.

## AI Task

```yaml
action: ai_task.generate_data
data:
  task_name: Evening summary
  entity_id: ai_task.nous_research_hermes_4_405b  # one per model subentry
  instructions: >-
    Summarise the state of the house in three short bullet points.
    Mention open windows and lights that are still on.
  structure:
    summary:
      description: One-line summary
      required: true
    anomalies:
      description: Unusual readings, empty string if none
```

The structured payload comes back in `data`, ready to use in automations.

## Multiple models

An integration entry is one API account: it stores the key, the prompt, the tools
and the aggregate sensors, and it starts empty (zero agents, zero counters). Models
are added to it:

- Open the integration entry and pick **Add model**. Every `model` subentry gets its
  own Assist agent (a `conversation.*` entity, selectable in Assist and in pipelines
  by its entity id), its own AI Task entity and its own device with cost, token,
  request and price sensors. Add as many models as you like — a key can be used
  once, so two fully independent setups need two keys.
- Subentries share only what belongs to the account: the API key, the prompt, the
  exposed tools and the aggregate sensors. Everything a model spends is also
  recorded on its own sensors, so you can chart two models side by side.

## Sensors

| Entity | State |
| --- | --- |
| `sensor.nous_research_cost_total` | Cumulative spend in USD |
| `sensor.nous_research_cost_today` | Spend today |
| `sensor.nous_research_cost_last_7_days` / `_cost_last_30_days` | Spend per window |
| `sensor.nous_research_input_tokens_total` / `_output_tokens_total` | Prompt / completion tokens |
| `sensor.nous_research_requests_total` | Successful requests |
| `sensor.nous_research_top_model_today` | Most used model today |
| `sensor.nous_research_last_request` | Model, tokens and cost of the last call |
| `sensor.nous_research_available_models` | Number of models in the catalog |
| `sensor.nous_research_free_models` | Free models in the catalog |

Every model subentry adds a device of its own with these sensors (ids follow
`sensor.nous_research_<model>_…`):

| Entity | State |
| --- | --- |
| `…_model_cost_total` / `…_model_cost_today` | Spend of that model in USD |
| `…_model_input_tokens` / `…_model_output_tokens` | Prompt / completion tokens of that model |
| `…_model_requests` | Requests made with that model |
| `…_input_price_per_1m_tokens` / `…_output_price_per_1m_tokens` | Catalog prices per 1M tokens |

Entity ids follow the entry name, so a different **Name** in the setup produces a
different prefix. `sensor.nous_research_budget_remaining` only exists once a monthly
budget is set in the options.

Prices are USD per 1M tokens, rendered without padding zeros: the portal value
`$0.03 / 1M` shows up as `0.03`, not `0.0300`.

### No balance sensor

The inference API has no balance endpoint — `/v1/balance`, `/v1/credits`,
`/v1/usage`, `/v1/billing/*` and the portal equivalents all answer `404`, and the
API key unlocks only `/v1/models` and the completion endpoints. A balance sensor
could therefore only show a local guess derived from your own spending, which is why
this integration does not ship one: check the real figure in the Nous Portal, and use
the cost sensors here for what Home Assistant can actually know (spend per day, per
model, tokens, budget).

### Long-term statistics

Cost sensors push `total` statistics, so history survives recorder purges and works
in **Statistics graph** cards. Pushes are throttled to once per minute.

## Services

| Service | Fields | Description |
| --- | --- | --- |
| `nous_research.reset_usage` | `entry_id` | Clears local cost and token counters |

`entry_id` is optional when a single entry is configured.

## Example automation

```yaml
automation:
  - alias: Daily Nous spend report
    triggers:
      - trigger: time
        at: "21:00:00"
    actions:
      - action: notify.persistent_notification
        data:
          title: Nous Research
          message: >-
            Today: ${{ states('sensor.nous_research_cost_today') }} ·
            {{ states('sensor.nous_research_requests_total') }} requests ·
            top model {{ states('sensor.nous_research_top_model_today') }}
```

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `401` / `403` | Key revoked or copied with whitespace — HA offers a reauth flow |
| `402` / no credits | Top up at the portal, requests are rejected until then |
| `429` | Rate limit — back off or slow down automations calling the agent |
| Custom model rejected | The id is not in the catalog; pick one from the list |
| Empty or truncated replies | Thinking used the whole `max_tokens`; raise it or set reasoning to **Off** |
| Generic entity names | Stale translation cache — restart HA after updating the integration |

Enable debug logs when reporting an issue:

```yaml
logger:
  logs:
    custom_components.nous_research: debug
```

## Privacy

The API key lives in the Home Assistant config entry and is only sent to the
configured base URL. Cost and token counters are computed and stored locally —
nothing is uploaded anywhere else. Diagnostics redact the key.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
pytest -q                                      # 78 tests, offline
python tools/check_translations.py --strict    # en/pl key parity
```

The suite boots a real `hass` instance through
`pytest-homeassistant-custom-component` and mocks HTTP with `aioresponses`, so it
needs no network and no real API key.

## License

MIT — see [LICENSE](LICENSE). Copyright 2026 arypien.
