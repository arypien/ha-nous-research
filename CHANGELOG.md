# Changelog

## 0.1.5

**Free models can be added again, and a refused model no longer hides its reason.**

- **The one-token test no longer blocks the add.** Gated free tiers
  (`upstage/solar-mini4:free`, `stepfun/step-3.7-flash:free`, …) answer the
  1-token probe with a 4xx while neighbouring models answer fine, so the add used
  to end in a generic "cannot connect". Such a model now opens a confirmation step
  that quotes the API's own message and, if you accept, stores exactly the id you
  picked. A bad key (`invalid_auth`), empty balance (`no_credits`) or rate limit
  (`rate_limited`) still fails hard — those are account problems, not model problems.
- **The dropdown starts on a placeholder row**, `— choose a model —`. Version 0.1.4
  left the field without a default, but Home Assistant preselects the first option
  of a dropdown, so the picker still showed the first catalog entry. Now the first
  row is an explicit "nothing chosen yet" and submitting it returns `model_required`.
- **Every option is labelled with its price**: `… · FREE` or `… · $in/$out per 1M`,
  so a free variant is never mistaken for its paid sibling again.
- **The form shows the running version** (`Integration version: {version}`), which
  ends the "did my install take?" question.
- The API message of a refused probe is logged at warning level.

## 0.1.4

**The model you pick is the model that answers.**

- **No preselected model.** *Add model* opens with an empty picker. It used to
  preselect the first chat model of `/v1/models` — which is whatever the endpoint
  happens to list first (`upstage/solar-mini-4-5` at the time of writing) — so an
  untouched form installed a model nobody chose. Reconfiguring still shows the
  current model.
- **Model ids are validated against the catalog.** An id the endpoint does not list
  (a typo, the dropdown label pasted instead of the id, a stale model) is refused on
  the form with `invalid_model` instead of being stored.
- **No silent model substitution at request time.** `catalog.resolve_model()` used to
  fall back to the catalog default for an unknown id: a free model could be answered
  and billed as a different, paid one while the entity looked fine. The configured id
  is now used verbatim, the device shows it verbatim, and a request for an id outside
  the catalog raises "Model '<id>' is not in the API catalog. Pick a model for this
  subentry again — the request was not sent." alongside the existing repair issue.
- **Counters follow the configured model.** Cost/token/request sensors are booked
  under the subentry model, not under whatever `model` the API echoes in the stream,
  so a provider-side alias can no longer rename `sensor.*_top_model_today` or price a
  free model with the rates of another one. A mismatch is logged at debug level.
- **Empty catalog aborts the model step** (`cannot_connect`) instead of offering a
  guess when `/models` cannot be read.
- Removed `pick_default_model()` and `resolve_model()`; `known_model()` is the single
  id check. Docs: dropped a duplicated *Model* row from the README table.

## 0.1.3

**A config entry is an API account; models are subentries of it.**

- **Setup asks for the key only.** The entry is created right away with the key,
  the endpoint and the account defaults; the model is picked afterwards. A fresh
  entry starts clean: aggregate sensors at zero and no Assist agent or AI Task
  entity until a model is added.
- **`model` subentries.** Add one per model from the entry menu (*Add model*).
  Each subentry gets its own device, its own Assist agent (a `conversation.*`
  entity, selectable in Assist and in pipelines by entity id), its own AI Task
  entity and its own cost / token / request / price sensors, all starting at zero.
  The API key, prompt, exposed tools and aggregate sensors stay on the account.
- **One key, one entry.** The unique id is derived from the key alone, so the same
  key cannot be added twice; two fully independent setups need two keys. Duplicate
  models inside an entry are still rejected with `model_in_use`.
- **Per-model prices.** Input and output prices come from the live catalog, per
  model, in USD per 1M tokens with no padding zeros (`0.03`, not `0.0300`).
- **Migration from 0.1.2.** The stored `chat_model` is dropped from data and
  options and the entry moves to version 2; cost and token history is kept. The
  model is added again as a subentry — existing agents disappear until then.
- **Specific credential errors.** The catalog call doubles as the key check and
  reports `invalid_auth`, `no_credits`, `rate_limited` or `cannot_connect` on the
  form instead of a generic connection error.
- **Repair issue per unknown model.** A subentry whose model left the catalog
  raises its own issue (id keyed by the account and a hash of the model id) and it
  clears itself once the subentry points at a catalog model.
- **Device-based entity names**, so a model agent is called
  "Nous Research <model>" instead of repeating the title twice.
- **Diagnostics** list the subentries (type, title, model) with the API key
  redacted.
- Local cost tracking (today / 7d / 30d / total, tokens, requests, optional monthly
  budget), long-term `total` statistics throttled to once per minute, the
  `nous_research.reset_usage` service, and an English / Polish UI.

PL: Wpis konfiguracji to konto API, a modele są jego podwpisami.

- **Kreator pyta tylko o klucz.** Wpis powstaje od razu z kluczem, adresem
  i domyślnymi opcjami konta; model wybiera się później. Świeży wpis jest czysty:
  sensory zbiorcze na zero, brak agenta Assist i encji AI Task do dodania modelu.
- **Podwpisy `model`.** Dodajesz po jednym na model (*Dodaj model*). Każdy dostaje
  własne urządzenie, własnego agenta Assist (`conversation.*`, wybieralny w Assist
  i w pipeline'ach po entity_id), własną encję AI Task oraz swoje sensory kosztu,
  tokenów, zapytań i cen — wszystkie startują od zera. Klucz API, prompt,
  wystawione narzędzia i sensory zbiorcze zostają na koncie.
- **Jeden klucz, jeden wpis.** Unique_id liczy się z samego klucza, więc ten sam
  klucz nie wejdzie dwa razy; dwa w pełni niezależne zestawy wymagają dwóch
  kluczy. Duplikat modelu w wpisie nadal kończy się błędem `model_in_use`.
- **Ceny per model.** Ceny wejścia i wyjścia pochodzą z żywego katalogu, osobno dla
  każdego modelu, w USD za 1M tokenów bez dopełniania zer (`0.03`, nie `0.0300`).
- **Migracja z 0.1.2.** Zapisany `chat_model` znika z data i options, wpis
  przechodzi na wersję 2, historia kosztów i tokenów zostaje. Model dodajesz
  ponownie jako podwpis — do tego czasu dotychczasowe agenty znikają.
- **Konkretne błędy poświadczeń.** Wywołanie katalogu jest jednocześnie sprawdzeniem
  klucza i zwraca na formularzu `invalid_auth`, `no_credits`, `rate_limited` lub
  `cannot_connect` zamiast ogólnego błędu połączenia.
- **Repair issue per nieznany model.** Podwpis, którego model zniknął z katalogu,
  zgłasza własny issue (id z konta i hasha modelu) i sam znika po wskazaniu modelu
  z katalogu.
- **Nazwy encji z urządzenia**, więc agent nazywa się „Nous Research <model>” bez
  powtarzania tytułu.
- **Diagnostyka** pokazuje podwpisy (typ, tytuł, model) z zamaskowanym kluczem API.
- Lokalne śledzenie kosztów (dziś / 7d / 30d / suma, tokeny, zapytania, opcjonalny
  budżet miesięczny), statystyki długoterminowe `total` ograniczone do raz na
  minutę, usługa `nous_research.reset_usage` oraz interfejs EN / PL.

## 0.1.2

- New brand assets: the integration icon (light and dark variants) now uses the
  illustrated character mark.

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
