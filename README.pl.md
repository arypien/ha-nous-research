<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="images/banner-readme-dark.png">
    <img alt="Nous Research dla Home Assistant" src="images/banner-readme-light.png" width="760">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img alt="Repozytorium własne HACS" src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=flat-square"></a>
  <img alt="Home Assistant 2026.9.0+" src="https://img.shields.io/badge/Home%20Assistant-2026.9.0%2B-18BCF2.svg?style=flat-square">
  <a href="LICENSE"><img alt="Licencja MIT" src="https://img.shields.io/badge/license-MIT-3DA639.svg?style=flat-square"></a>
  <img alt="Testy" src="https://img.shields.io/badge/testy-68%20passing-3DA639.svg?style=flat-square">
  <img alt="Tłumaczenia" src="https://img.shields.io/badge/i18n-EN%20%7C%20PL-8A63D2.svg?style=flat-square">
</p>

<p align="center"><a href="README.md">English</a> · <b>Polski</b></p>

Agent konwersacyjny, encja AI Task i lokalne śledzenie kosztów dla API inferencji
[Nous Research](https://nousresearch.com). Modele Hermes 4 i DeepHermes dostają
realną kontrolę nad Home Assistant przez Assist, a rozliczanie tokenów i wydatków
liczy się na Twojej instancji.

> Nieoficjalna integracja społecznościowa. Niepowiązana z Nous Research — zużycie
> API rozlicza Nous Research na podstawie własnego cennika.

## Funkcje

- **Agent konwersacyjny** — streamowanie odpowiedzi, wywoływanie narzędzi Assist,
  do sześciu rund narzędziowych na odpowiedź.
- **Encja AI Task** — generowanie strukturalne przez `ai_task.generate_data`;
  wynik trafia do `data`, gotowy do użycia w automatyzacjach.
- **Tryby rozumowania Hermes 4** — oficjalny prompt myślenia albo prefill
  otwierającego tagu `think`.
- **Śledzenie kosztów** — dziś / 7 dni / 30 dni / suma, liczniki tokenów i zapytań,
  opcjonalny budżet miesięczny, wszystko przechowywane lokalnie.
- **Żywy katalog modeli** — ceny, długość kontekstu i modalności prosto z
  `/v1/models`; wybrany model czatu jest weryfikowany względem katalogu.
- **Statystyki długoterminowe** — sensory kosztów wysyłają statystyki `total`,
  więc historia przeżywa czyszczenie recorder-a i działa w kartach Statistics Graph.
- **Interfejs dwujęzyczny** — angielski i polski, plus
  [instrukcja dodawania tłumaczeń](docs/TRANSLATING.md).

## Instalacja

### HACS (zalecane)

1. HACS → **Integracje** → ⋮ → **Repozytoria własne**.
2. Dodaj `https://github.com/arypien/ha-nous-research`, kategoria **Integration**.
3. Wyszukaj **Nous Research** w HACS i zainstaluj.
4. Zrestartuj Home Assistant.

### Ręcznie

Skopiuj `custom_components/nous_research` z archiwum wydania do
`config/custom_components/` i zrestartuj Home Assistant.

Wymaga **Home Assistant 2026.9.0 lub nowszego** oraz klucza API z
<https://portal.nousresearch.com>.

## Konfiguracja

1. Utwórz klucz API w portalu Nous.
2. **Ustawienia → Urządzenia i usługi → Dodaj integrację → Nous Research**.
3. Wklej klucz — katalog jest pobierany i weryfikowany zapytaniem na jeden token,
   więc unieważniony klucz zawodzi od razu, a nie przy pierwszym użyciu.
4. Wybierz model czatu z listy zwracanej przez API (wyszukiwana lista, dowolny
   tekst też jest akceptowany).

| Pole | Znaczenie |
| --- | --- |
| Klucz API | Token Bearer z portalu, trzymany we wpisie konfiguracji |
| Adres bazowy | Domyślnie `https://inference-api.nousresearch.com/v1` |
| Nazwa | Nazwa wpisu, jednocześnie prefiks tworzonych encji |
| Model | Wybierany w drugim kroku z żywego katalogu `/models` |

Żaden identyfikator modelu nie jest zaszyty na stałe: katalog jest agregowany i
namespace'owany (`vendor/model`, ~300 modeli czatu), więc lista i jej domyślna
wartość pochodzą z API. Endpointy batch (`:batch`), embeddings, TTS i modele
tylko-obrazkowe są odfiltrowane. Jeśli zapisany model zniknie z katalogu,
integracja wraca do domyślnego z katalogu i zgłasza repair issue z prośbą o wybór
modelu — zamiast wysypywać każde zapytanie.

### Opcje

**Konfiguruj** na wpisie obejmuje prompt systemowy, wystawione API HA, model,
`max_tokens`, `temperature`, tryb rozumowania, sensory kosztów, budżet oraz
interwał odświeżania katalogu.

## Tryby rozumowania

Nous dokumentuje konkretny prompt systemowy odblokowujący długie łańcuchy myśli.
Tryby różnią się tylko sposobem jego wywołania:

- **Wyłączone** — brak myślenia, najniższe opóźnienie i koszt.
- **Prompt systemowy** (domyślnie, zalecane dla Hermes 4) — wysyłany jako
  wiadomość systemowa obok Twojego promptu:

  > You are a deep thinking AI, you may use extremely long chains of thought to
  > deeply consider the problem and deliberate with yourself via systematic
  > reasoning processes to help you come to a correct solution prior to answering.
  > You should enclose your thoughts and internal monologue inside `think` tags,
  > and then provide your solution or response to the problem.

- **Prefill tagu myślenia** — tura asystenta jest poprzedzona otwierającym tagiem
  myślenia, więc model kontynuuje w nim bez dodatkowego promptu. Taniej, nieco
  mniej niezawodnie na małych modelach.

Tokeny myślenia liczą się do `max_tokens`; podnieś limit, jeśli odpowiedzi się
urywają.

## Wywoływanie narzędzi / Assist

**Kontrola Home Assistant** = `assist` wystawia modelowi narzędzia Assist
(`GetLiveContext`, `HassTurnOn`, `HassTurnOff`, `HassLightSet`,
`HassClimateSetTemperature`, …). `conversation` dodatkowo rejestruje agenta w
oknie Assist. Agent wykonuje do sześciu rund narzędziowych przed odpowiedzią.

## AI Task

```yaml
action: ai_task.generate_data
data:
  task_name: Podsumowanie wieczoru
  entity_id: ai_task.nous_research
  instructions: >-
    Podsumuj stan domu w trzech krótkich punktach. Wspomnij o otwartych oknach
    i światłach, które wciąż się świecą.
  structure:
    summary:
      description: Jednolinijkowe podsumowanie
      required: true
    anomalies:
      description: Nietypowe odczyty, pusty string gdy brak
```

Strukturalny wynik wraca w `data`, gotowy do automatyzacji.

## Sensory

| Encja | Stan |
| --- | --- |
| `sensor.nous_research_cost_total` | Skumulowane wydatki w USD |
| `sensor.nous_research_cost_today` | Wydatki z dzisiaj |
| `sensor.nous_research_cost_last_7_days` / `_cost_last_30_days` | Wydatki w oknach czasowych |
| `sensor.nous_research_input_tokens_total` / `_output_tokens_total` | Tokeny promptu / odpowiedzi |
| `sensor.nous_research_requests_total` | Udane zapytania |
| `sensor.nous_research_top_model_today` | Najczęściej używany model dziś |
| `sensor.nous_research_last_request` | Model, tokeny i koszt ostatniego zapytania |
| `sensor.nous_research_available_models` | Liczba modeli w katalogu |
| `sensor.nous_research_selected_model_input_price` | Cena promptu za 1M tokenów |
| `sensor.nous_research_selected_model_output_price` | Cena odpowiedzi za 1M tokenów |

Identyfikatory encji biorą się z nazwy wpisu, więc inna **Nazwa** w konfiguracji daje
inny prefiks. Sensor `sensor.nous_research_budget_remaining` powstaje dopiero po
ustawieniu budżetu miesięcznego w opcjach.

Ceny są w USD za 1M tokenów i wyświetlają się bez dopełniania zer: wartość z
portalu `$0.03 / 1M` pokazuje się jako `0.03`, a nie `0.0300`.

### Brak sensora salda

API inferencji nie ma endpointu salda — `/v1/balance`, `/v1/credits`, `/v1/usage`,
`/v1/billing/*` oraz odpowiedniki w portalu zwracają `404`, a klucz API odblokowuje
tylko `/v1/models` i endpointy uzupełnień. Sensor salda mógłby więc pokazywać
jedynie lokalne zgadywanie na podstawie własnych wydatków, dlatego go nie ma:
prawdziwą kwotę sprawdzisz w portalu Nous, a sensory kosztów tutaj pokazują to, co
Home Assistant faktycznie wie (wydatki dzienne, per model, tokeny, budżet).

### Statystyki długoterminowe

Sensory kosztów wysyłają statystyki `total`, więc historia przeżywa czyszczenie
recorder-a i działa w kartach **Statistics graph**. Wysyłka jest ograniczona do
raz na minutę.

## Usługi

| Usługa | Pola | Opis |
| --- | --- | --- |
| `nous_research.reset_usage` | `entry_id` | Zeruje lokalne liczniki kosztów i tokenów |

`entry_id` jest opcjonalny, gdy skonfigurowany jest jeden wpis.

## Przykładowa automatyzacja

```yaml
automation:
  - alias: Dzienne podsumowanie wydatków Nous
    triggers:
      - trigger: time
        at: "21:00:00"
    actions:
      - action: notify.persistent_notification
        data:
          title: Nous Research
          message: >-
            Dziś: ${{ states('sensor.nous_research_cost_today') }} ·
            {{ states('sensor.nous_research_requests_total') }} zapytań ·
            top model {{ states('sensor.nous_research_top_model_today') }}
```

## Rozwiązywanie problemów

| Objaw | Rozwiązanie |
| --- | --- |
| `401` / `403` | Klucz unieważniony lub wklejony ze spacjami — HA pokaże reautoryzację |
| `402` / brak środków | Doładuj konto w portalu, do tego czasu zapytania są odrzucane |
| `429` | Limit zapytań — zwolnij automatyzacje korzystające z agenta |
| Własny model odrzucony | Identyfikatora nie ma w katalogu; wybierz model z listy |
| Puste lub ucięte odpowiedzi | Myślenie zjadło całe `max_tokens`; podnieś limit lub wyłącz rozumowanie |
| Ogólne nazwy encji | Stary cache tłumaczeń — zrestartuj HA po aktualizacji integracji |

Do zgłaszania problemów włącz logi debug:

```yaml
logger:
  logs:
    custom_components.nous_research: debug
```

## Prywatność

Klucz API leży we wpisie konfiguracji Home Assistant i jest wysyłany wyłącznie na
skonfigurowany adres bazowy. Liczniki kosztów i tokenów są liczone i przechowywane
lokalnie — nic nie jest wysyłane gdziekolwiek indziej. Diagnostyka redaguje klucz.

## Rozwój

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
pytest -q                                      # 68 testów, offline
python tools/check_translations.py --strict    # zgodność kluczy en/pl
```

Zestaw testów uruchamia prawdziwą instancję `hass` przez
`pytest-homeassistant-custom-component` i mockuje HTTP przez `aioresponses`, więc nie
potrzebuje sieci ani prawdziwego klucza API.

## Licencja

MIT — zobacz [LICENSE](LICENSE). Copyright 2026 arypien.
