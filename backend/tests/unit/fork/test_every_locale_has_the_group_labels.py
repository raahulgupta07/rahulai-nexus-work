"""The sidebar's six group headings must be translated in every locale.

A missing key renders as the raw path (`nav.groupPrev7`) for that language, and
a key copied across untranslated renders as English inside an otherwise
translated sidebar. Neither raises, neither shows up in any backend test, and
both reach a user directly — so both are asserted here.

★The English-string check is the point. Adding a key to ten files by copying the
English value is the usual way a locale ships untranslated: every file has the
key, every automated presence check passes, and the product is half English.

★`locales/en.json` ends WITHOUT a trailing newline and must keep doing so.
Adding one is a whole-file diff on a file upstream also edits, so every future
port then conflicts on it (CLAUDE.md records this).

The language list is enumerated from the directory, never hardcoded — a locale
added later is covered the day it lands.
"""

import json
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[4]
LOCALES = REPO / "locales"

GROUP_KEYS = (
    "nav.groupPinned",
    "nav.groupToday",
    "nav.groupYesterday",
    "nav.groupPrev7",
    "nav.groupPrev30",
    "nav.groupOlder",
)


def locale_files(directory: Path) -> list:
    """Every shipped locale. `.bak-*` snapshots are not `*.json` and so are out."""
    files = sorted(p for p in directory.glob("*.json"))
    assert files, f"no locale files under {directory}"
    assert (directory / "en.json") in files, "en.json is missing"
    return files


def lookup(data: dict, dotted: str):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def check_every_locale_has_the_keys(directory: Path) -> None:
    missing = []
    for path in locale_files(directory):
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in GROUP_KEYS:
            value = lookup(data, key)
            if not isinstance(value, str) or not value.strip():
                missing.append(f"{path.name}:{key}")
    assert not missing, (
        "group heading keys missing or empty — these render as the raw key "
        f"path in the sidebar: {missing}"
    )


def check_no_locale_ships_the_english_string(directory: Path) -> None:
    english = json.loads((directory / "en.json").read_text(encoding="utf-8"))
    untranslated = []
    for path in locale_files(directory):
        if path.name == "en.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in GROUP_KEYS:
            value = lookup(data, key)
            if isinstance(value, str) and value == lookup(english, key):
                untranslated.append(f"{path.name}:{key}={value!r}")
    assert not untranslated, (
        "these carry the English string verbatim, so that language shows "
        f"English headings: {untranslated}"
    )


def check_english_has_no_trailing_newline(directory: Path) -> None:
    raw = (directory / "en.json").read_bytes()
    assert raw and not raw.endswith(b"\n"), (
        "locales/en.json gained a trailing newline — that is a whole-file diff "
        "against upstream and every future port of this file will conflict"
    )


CHECKS = (
    check_every_locale_has_the_keys,
    check_no_locale_ships_the_english_string,
    check_english_has_no_trailing_newline,
)


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_locale_group_labels(check):
    assert LOCALES.is_dir(), f"{LOCALES} is missing"
    check(LOCALES)
