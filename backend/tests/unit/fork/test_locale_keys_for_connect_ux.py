"""Every locale key the connector screens reference must exist in en.json.

★A missing key does not fail, warn, or fall back — vue-i18n renders the KEY
ITSELF, so a member sees `data.syncKeepsRunning` on a button. That has already
happened twice in this codebase, which is why it is checked mechanically rather
than by reading.

Only `en` is asserted. The other nine locales fall back to English for anything
they do not define, and inventing translations for languages nobody here reads
would be worse than falling back.
"""
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
EN = REPO / "locales" / "en.json"
FRONTEND = REPO / "frontend"

# The files this work touched. Deliberately an explicit list: a glob over the
# whole frontend would sweep in pre-existing debt and turn a targeted guard into
# a wall of failures nobody acts on.
FILES = [
    FRONTEND / "components" / "datasources" / "ConnectionSyncStrip.vue",
    FRONTEND / "components" / "UserDataSourceCredentialsModal.vue",
    FRONTEND / "components" / "AddConnectionModal.vue",
]

# `$t('a.b')` and `t('a.b')`, single or double quoted.
KEY_RE = re.compile(r"(?<![\w.])\$?t\(\s*['\"]([a-zA-Z][\w.]*)['\"]")


def _locale() -> dict:
    return json.loads(EN.read_text(encoding="utf-8"))


def _has(locale: dict, dotted: str) -> bool:
    node = locale
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return isinstance(node, str)


def _keys_in(path: Path) -> set:
    return set(KEY_RE.findall(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_referenced_key_exists(path):
    assert path.exists(), path
    locale = _locale()
    missing = sorted(k for k in _keys_in(path) if not _has(locale, k))
    assert missing == [], f"{path.name} renders raw keys: {missing}"


def _strip_comments(text: str) -> str:
    """Blank out comments, preserving line numbering.

    ★Needed because the comment WARNING about this very trap quotes the broken
    pattern verbatim, and the first version of this guard flagged its own
    documentation. A guard that fires on prose gets muted, and a muted guard
    protects nothing.
    """
    text = re.sub(r"<!--.*?-->", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    return re.sub(r"^(\s*)//.*$", r"\1", text, flags=re.M)


def test_no_dead_translate_fallbacks():
    """`$t('x') || 'Fallback'` never falls back — $t returns the key on a miss,
    and a non-empty string is truthy. The fallback is dead code that hides a
    missing key behind what looks like a safety net.

    Note the direction: `value || $t('key')` is the OPPOSITE and perfectly fine
    — a real value with a translated default.
    """
    bad = []
    for path in FILES:
        code = _strip_comments(path.read_text(encoding="utf-8"))
        for i, line in enumerate(code.splitlines(), 1):
            if re.search(r"\$?t\([^)]*\)\s*\|\|", line):
                bad.append(f"{path.name}:{i}")
    assert bad == [], f"dead $t fallbacks: {bad}"


def test_interpolation_placeholders_match_their_use():
    """A message whose placeholder is never supplied renders `{n}` literally."""
    locale = _locale()["data"]
    expected = {
        "syncFinding": {"unit"},
        "syncReading": {"unit", "done", "total"},
        "syncTablesSoFar": {"n"},
        "syncRowTables": {"n"},
        "syncReady": {"name"},
        "syncCountUnits": {"unit"},
        "syncPartialTitle": {"done", "total", "unit"},
        "syncPartialBody": {"n", "unit"},
        "syncPartialSummary": {"done", "total", "unit", "n", "which"},
        "syncContinueWith": {"n"},
        "syncSummaryTables": {"n", "done", "unit"},
        "syncMinsAgo": {"n"},
        "syncHoursAgo": {"n"},
        "syncDaysAgo": {"n"},
        "syncExpiresInDays": {"n"},
    }
    for key, placeholders in expected.items():
        assert key in locale, key
        found = set(re.findall(r"\{(\w+)\}", locale[key]))
        assert found == placeholders, f"{key}: template has {found}, code supplies {placeholders}"


def test_the_singular_expiry_string_carries_no_placeholder():
    """"expires in 1 day" is written out rather than interpolated, so it must
    not contain a placeholder that would then render as `{n}`."""
    assert "{" not in _locale()["data"]["syncExpiresInDay"]


def test_en_json_still_has_no_trailing_newline():
    """★en.json ships without one while the other nine locales have one. Adding
    one is a whole-file diff on a file upstream also edits, and every future
    port then conflicts on the last line."""
    assert EN.read_bytes()[-1:] != b"\n"


def test_setup_copy_does_not_ask_for_things_the_connector_dropped():
    """These connectors stopped needing a tenant ID or an app registration. The
    copy must not send anyone to fetch one — that is the instruction that was
    wrong, and it cost people a support ticket each."""
    data = _locale()["data"]
    blob = " ".join([
        data["msPickTitle"], data["msPickBody"], data["msPickNothingNeeded"],
        data["msFabricWhat"], data["msPowerbiWhat"],
    ]).lower()
    assert "no tenant id" in blob
    assert "no app registration" in blob
    assert "no admin approval" in blob
