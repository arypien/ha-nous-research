# Translating the integration

`custom_components/nous_research/strings.json` is the source of truth. Home
Assistant reads `strings.json` for English and every
`custom_components/nous_research/translations/<lang>.json` for the rest, so a
language file must contain **exactly the same keys** as `strings.json`.

Current languages: `en` (English), `pl` (Polish).

## Add a language

1. Scaffold the file — it mirrors `strings.json` with empty values:

   ```bash
   python tools/check_translations.py --scaffold de
   ```

   Use the Home Assistant language code (`de`, `fr`, `es`, `nl`, …); Home
   Assistant picks the file automatically from the user's profile language.

2. Translate the values only. Rules:

   - **Never rename, add or remove keys** — the file structure is the schema.
   - **Keep placeholders** (`{name}`, `{model}`, `{error}`) byte for byte; the
     checker fails if one is missing or invented.
   - Brand names stay untouched: `Nous Research`, `Hermes`, `Assist`,
     `AI Task`, `max_tokens`, model ids and service names.
   - Sentence case for titles, full sentences for descriptions — match the tone
     of `translations/en.json`.
   - Do not translate code fences, YAML keys or entity ids.

3. Validate:

   ```bash
   python tools/check_translations.py --strict
   ```

   The report lists `missing`, `extra`, `empty`, `placeholders` and
   `untranslated` entries per language. `--strict` also fails when a value is
   identical to English (except brand names listed in `ALLOWED_SAME`).

4. Run the tests — they enforce the same rules in CI:

   ```bash
   python -m pytest tests/test_translations.py -q
   ```

5. Open a pull request with just the one `translations/<lang>.json` file.

## Keeping keys current

When a feature adds, renames or drops a key, update `strings.json` and every
language file in the same change; the tests fail otherwise. If a language falls
behind, run the checker to see exactly which keys are missing.

## Markdown docs

`README.md` is English, `README.pl.md` is Polish; both start with a language
switch line and link to each other. A new translation of the docs should follow
the same pattern (`README.<lang>.md`) and be linked from both files.
