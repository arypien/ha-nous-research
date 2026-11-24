"""Validate and scaffold integration translations.

``strings.json`` is the source of truth: every language file in
``custom_components/nous_research/translations`` must contain exactly the same
keys, keep the same ``{placeholders}`` and should not leave a key untranslated.

Usage::

    python tools/check_translations.py               # report, exit 0 unless broken
    python tools/check_translations.py --strict      # exit 1 on any finding
    python tools/check_translations.py --scaffold de # create translations/de.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "nous_research"
STRINGS = COMPONENT / "strings.json"
TRANSLATIONS = COMPONENT / "translations"

PLACEHOLDER = re.compile(r"\{[a-zA-Z0-9_]+\}")
# Values that legitimately stay identical to English: brand names and the like.
ALLOWED_SAME = {"nous research", "api", "tts", "url", "ai task", "model", "assist"}


def flatten(data: object, prefix: str = "") -> dict[str, str]:
    """Return ``{"a.b.c": "value"}`` for every leaf of a nested mapping."""
    if isinstance(data, dict):
        flat: dict[str, str] = {}
        for key, value in data.items():
            flat.update(flatten(value, f"{prefix}{key}."))
        return flat
    if isinstance(data, list):
        flat = {}
        for index, value in enumerate(data):
            flat.update(flatten(value, f"{prefix}{index}."))
        return flat
    return {prefix[:-1]: data if isinstance(data, str) else ""}


def load(path: Path) -> dict[str, str]:
    """Load a translation file as a flat key/value mapping."""
    with path.open(encoding="utf-8") as handle:
        return flatten(json.load(handle))


def scaffold(language: str) -> None:
    """Create an empty translation file that mirrors ``strings.json``."""
    target = TRANSLATIONS / f"{language}.json"
    if target.exists():
        raise SystemExit(f"{target} already exists, edit it instead")
    source = json.loads(STRINGS.read_text(encoding="utf-8"))

    def blank(node: object) -> object:
        if isinstance(node, dict):
            return {key: blank(value) for key, value in node.items()}
        if isinstance(node, list):
            return [blank(value) for value in node]
        return ""

    target.write_text(
        json.dumps(blank(source), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    total = len(load(target))
    print(f"created {target.relative_to(ROOT)} with {total} empty strings")
    print("fill the values, keep the keys, then run this script again")


def report(strict: bool) -> int:
    """Compare every language file with ``strings.json``."""
    reference = load(STRINGS)
    reference_placeholders = {
        key: set(PLACEHOLDER.findall(value)) for key, value in reference.items()
    }

    problems = 0
    languages = sorted(TRANSLATIONS.glob("*.json"))
    if not languages:
        print("no translation files found")
        return 1

    for path in languages:
        language = path.stem
        current = load(path)
        missing = sorted(set(reference) - set(current))
        extra = sorted(set(current) - set(reference))
        empty = sorted(key for key, value in current.items() if not value.strip())
        placeholder_gaps = sorted(
            key
            for key in current.keys() & reference.keys()
            if set(PLACEHOLDER.findall(current[key])) != reference_placeholders[key]
        )
        untranslated = sorted(
            key
            for key in current.keys() & reference.keys()
            if language != "en"
            and current[key].strip()
            and current[key] == reference[key]
            and current[key].strip().lower() not in ALLOWED_SAME
        )

        findings = {
            "missing": missing,
            "extra": extra,
            "empty": empty,
            "placeholders": placeholder_gaps,
            "untranslated": untranslated,
        }
        total = len(reference)
        print(f"{language}: {len(current)}/{total} keys")
        for label, keys in findings.items():
            if keys:
                print(f"  {label} ({len(keys)}): {', '.join(keys[:6])}"
                      + (" ..." if len(keys) > 6 else ""))
        problems += sum(len(keys) for label, keys in findings.items()
                        if label in {"missing", "extra", "empty", "placeholders"})
        if strict:
            problems += len(untranslated)

    if problems:
        print(f"\n{problems} problem(s) found")
        return 1
    print("\nall translations are complete")
    return 0


def main(argv: list[str]) -> int:
    """Run the checker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true",
                        help="also fail on strings identical to English")
    parser.add_argument("--scaffold", metavar="LANG",
                        help="create an empty translations/LANG.json")
    args = parser.parse_args(argv)

    if args.scaffold:
        scaffold(args.scaffold)
        return 0
    return report(args.strict)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
