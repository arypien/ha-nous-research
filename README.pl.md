<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="images/banner-readme-dark.png">
    <img alt="Nous Research dla Home Assistant" src="images/banner-readme-light.png" width="760">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img alt="Repozytorium HACS" src="https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=flat-square"></a>
  <img alt="Home Assistant 2026.9.0+" src="https://img.shields.io/badge/Home%20Assistant-2026.9.0%2B-18BCF2.svg?style=flat-square">
  <a href="LICENSE"><img alt="Licencja MIT" src="https://img.shields.io/badge/license-MIT-3DA639.svg?style=flat-square"></a>
  <img alt="Testy" src="https://img.shields.io/badge/testy-104%20passing-3DA639.svg?style=flat-square">
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
- **Osobne sensory per model** — każdy podwpis modelu ma własne liczniki kosztu,
  tokenów, zapytań i ceny, startujące od zera.
- **Żywy katalog modeli** — ceny, długość kontekstu i modalności prosto z
  `/v1/models`; każdy model jest weryfikowany względem katalogu.
- **Posortowana lista modeli** — lista w oknie dodawania modelu otwiera się **od
  najtańszych**, więc darmowe modele są na górze, a nie w środku droższych sąsiadów.
  Kolejność wybierasz raz (cena rosnąco, cena malejąco, nazwa, najnowsze) — w
  formularzu klucza API albo później w **Konfiguruj** — a każda pozycja ma etykietę
  z ceną.
- **Statystyki długoterminowe** — sensory kosztów wysyłają statystyki `total`,
  więc historia przeżywa czyszczenie recorder-a i działa w kartach Statistics Graph.
- **Interfejs dwujęzyczny** — angielski i polski, plus
  [instrukcja dodawania tłumaczeń](docs/TRANSLATING.md).

## Instalacja

### HACS (zalecane)

Nous Research jest w domyślnym katalogu HACS, więc repozytorium własne nie jest
potrzebne:

1. HACS → **Integracje**.
2. Wyszukaj **Nous Research** i zainstaluj.
3. Zrestartuj Home Assistant.

### Ręcznie

Skopiuj `custom_components/nous_research` z archiwum wydania do
`config/custom_components/` i zrestartuj Home Assistant.

Wymaga **Home Assistant 2026.9.0 lub nowszego** oraz klucza API z
<https://portal.nousresearch.com>.

> **Aktualizacja z 0.1.2:** wpis staje się kontem API, więc zapisany model znika
> (historia kosztów zostaje), a jego agent Assist i encja AI Task przestają
> istnieć. Wejdź we wpis, naciśnij **Dodaj model** i wskaż model ponownie — nic
> więcej nie trzeba konfigurować.

## Konfiguracja

1. Utwórz klucz API w portalu Nous.
2. **Ustawienia → Urządzenia i usługi → Dodaj integrację → Nous Research**.
3. Wklej klucz — katalog jest pobierany od razu, więc unieważniony klucz zawodzi
   natychmiast, a nie przy pierwszym użyciu. Formularz kończy się polem **Sortowanie
   listy modeli**, więc lista od razu układa się po Twojemu (zmienisz też później
   w Konfiguruj).
4. Na nowym wpisie naciśnij **Dodaj model** i wybierz model czatu z listy
   zwracanej przez API (wyszukiwana lista, dowolny tekst też jest akceptowany).
   Każda pozycja ma cenę — `FREE` albo `$in/$out za 1M` — a pole startuje na
   pozycji zastępczej, więc nic nie zostanie dodane bez Twojego wyboru. Jeśli API
   odrzuci testowe zapytanie dla modelu (tak robią niektóre darmowe warianty),
   formularz pokazuje komunikat API i pozwala dodać model mimo to.
   Powtórz dla kolejnych modeli.

| Pole | Znaczenie |
| --- | --- |
| Klucz API | Token Bearer z portalu, trzymany we wpisie konfiguracji |
| Adres bazowy | Domyślnie `https://inference-api.nousresearch.com/v1` |
| Nazwa | Nazwa wpisu, jednocześnie prefiks tworzonych encji |
| Model | Wybierany per podwpis (po utworzeniu wpisu) z żywego katalogu `/models` |
| Sortowanie listy modeli | Kolejność tej listy: od najtańszych (domyślnie), od najdroższych, nazwa A-Z, najnowsze |

Żaden identyfikator modelu nie jest zaszyty na stałe: katalog jest agregowany i
namespace'owany (`vendor/model`, ~300 modeli czatu), więc lista pochodzi z API.
Endpointy batch (`:batch`), embeddings, TTS i modele tylko-obrazkowe są
odfiltrowane. Nic nie jest zaznaczone z góry i nic nie jest zgadywane: krok wyboru
modelu odrzuca identyfikator, którego endpoint nie oferuje (`invalid_model`), a gdy model
podwpisu zniknie później z katalogu, podwpis zgłasza repair
issue, a jego zapytania kończą się tym komunikatem — zamiast cicho trafiać do innego
modelu.

### Opcje

**Konfiguruj** na wpisie obejmuje prompt systemowy, wystawione API HA,
`max_tokens`, `temperature`, tryb rozumowania, sensory kosztów, budżet,
interwał odświeżania katalogu **oraz kolejność listy modeli**. Model nie jest opcją
wpisu — siedzi w podwpisach modeli.

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
  entity_id: ai_task.nous_research_hermes_4_405b  # po jednej na podwpis modelu
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

## Wiele modeli

Wpis integracji to jedno konto API: trzyma klucz, prompt, narzędzia i sensory
zbiorcze, a po dodaniu zaczyna się pusty (zero agentów, zero liczników). Modele
dodajesz do niego:

- Wejdź we wpis integracji i wybierz **Dodaj model** — lista otwiera się w kolejności
  ustawionej opcją *Sortowanie listy modeli*. Każdy podwpis typu `model`
  dostaje własnego agenta Assist (encja `conversation.*`, wybieralna w Assist
  i w pipeline'ach po entity_id), własną encję AI Task oraz własne urządzenie
  z sensorami kosztu, tokenów, zapytań i cen. Modeli możesz dodać dowolnie wiele
  — klucz można wykorzystać raz, więc dwa w pełni niezależne zestawy wymagają
  dwóch kluczy.
- Podwpisy dzielą tylko to, co należy do konta: klucz API, prompt, wystawione
  narzędzia i sensory zbiorcze. Wydatki każdego modelu trafiają dodatkowo na jego
  własne sensory, więc dwa modele można porównać na jednym wykresie.

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

Każdy podwpis modelu dodaje własne urządzenie z tymi sensorami (identyfikatory
wyglądają jak `sensor.nous_research_<model>_…`):

| Encja | Stan |
| --- | --- |
| `…_model_cost_total` / `…_model_cost_today` | Wydatki tego modelu w USD |
| `…_model_input_tokens` / `…_model_output_tokens` | Tokeny promptu / odpowiedzi tego modelu |
| `…_model_requests` | Zapytania wykonane tym modelem |
| `…_input_price_per_1m_tokens` / `…_output_price_per_1m_tokens` | Ceny z katalogu za 1M tokenów |

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
pytest -q                                      # 78 testów, offline
python tools/check_translations.py --strict    # zgodność kluczy en/pl
```

Zestaw testów uruchamia prawdziwą instancję `hass` przez
`pytest-homeassistant-custom-component` i mockuje HTTP przez `aioresponses`, więc nie
potrzebuje sieci ani prawdziwego klucza API.

## Licencja

MIT — zobacz [LICENSE](LICENSE). Copyright 2026 arypien.
