"""The dashboard iframe must not run inside the app's own origin.

Artifact code is written by the model from data the user supplied, and it is
executed verbatim in the browser. The frame carried:

    sandbox="allow-scripts allow-same-origin"

Those two together are the documented way to have no sandbox at all: the frame
runs scripts AND shares the app's origin, so its code reads `document.cookie`,
`localStorage` and `sessionStorage` of the app, and can call the API as the
signed-in user. The `auth` cookie here is not `httpOnly`, so the token is
directly readable.

Removing `allow-same-origin` gives the frame an opaque origin. That is the
whole fix — but it also changes how the two sides talk, so these tests cover
the coupling as well as the attribute:

  * child → parent already posts with `'*'`; unchanged.
  * parent → child posted with `window.location.origin`, which an opaque frame
    can NEVER match, so every message would be silently dropped and the
    dashboard would render empty. Those calls must target `'*'`.

Sending to `'*'` is safe here and only here: the recipient is our own srcdoc
document, the payload is the artifact's own data, and the frame can no longer
be anything else.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
FE = REPO / "frontend"

FRAMES = [
    FE / "components" / "dashboard" / "ArtifactFrame.vue",
    FE / "pages" / "r" / "[id]" / "index.vue",
]

# Where a new artifact frame could appear without anyone updating FRAMES.
SCAN_DIRS = [FE / "components", FE / "pages", FE / "utils", FE / "public"]
SCAN_SUFFIXES = {".vue", ".ts", ".js", ".html"}
SKIP_DIR_PARTS = {"node_modules", "dist", ".output", ".nuxt"}

_SANDBOX_ATTR = re.compile(r'sandbox\s*=\s*"([^"]*)"')


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """★Required, not hygiene. The fixes below are DOCUMENTED in comments that
    quote the very attribute values being asserted on — `sandbox="allow-scripts"`
    appears verbatim inside explanatory comments in artifactIframe.ts and
    artifact-sandbox.html. Without this, a correct file fails on its own
    explanation. (This trap has now bitten this repo six times.)

    Line comments are only dropped when `//` is not preceded by `:`, so URLs
    like `https://…` survive.
    """
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)      # HTML / .vue template
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)       # JS block
    src = re.sub(r"(?<!:)//[^\n]*", "", src)              # JS line
    return src


def _artifact_frame_sources():
    """Every frontend source that renders the artifact iframe.

    ★Deliberately NOT a hand-kept list. The list is exactly what failed: a fix
    was applied to ArtifactFrame.vue and looked complete while the public share
    page — a second file rendering the SAME artifact HTML — stayed broken. So
    the set is derived instead: any file that both binds the artifact srcdoc
    (`iframeSrcdoc`) and carries a `sandbox=` attribute is an artifact frame,
    whoever adds it and wherever they put it.
    """
    found = []
    for d in SCAN_DIRS:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.suffix not in SCAN_SUFFIXES or not p.is_file():
                continue
            if SKIP_DIR_PARTS & set(p.parts):
                continue
            if ".bak-" in p.name:          # backups of the broken versions
                continue
            body = _strip_comments(_read(p))
            if "iframeSrcdoc" in body and _SANDBOX_ATTR.search(body):
                found.append((p, _SANDBOX_ATTR.findall(body)))
    return found


@pytest.mark.parametrize("path", FRAMES, ids=lambda p: p.name)
def test_no_artifact_iframe_shares_the_app_origin(path):
    """★The defect itself. `allow-scripts` + `allow-same-origin` on a frame
    holding model-written code is not a sandbox."""
    src = _read(path)
    for m in re.finditer(r'sandbox="([^"]*)"', src):
        value = m.group(1)
        if "allow-scripts" in value:
            assert "allow-same-origin" not in value, (
                f"{path.name}: a scriptable artifact frame still shares the app origin"
            )


@pytest.mark.parametrize("path", FRAMES, ids=lambda p: p.name)
def test_the_frames_are_still_sandboxed_at_all(path):
    """Guard the guard: deleting the whole attribute would also pass the test
    above, and would be far worse."""
    src = _read(path)
    if ":srcdoc=" in src or "srcdoc=" in src:
        assert 'sandbox="' in src, f"{path.name}: srcdoc frame with no sandbox attribute"


def test_the_parent_no_longer_addresses_the_frame_by_the_apps_origin():
    """★The coupling. An opaque frame's origin can never equal
    `window.location.origin`, so every one of these messages would be dropped —
    a blank dashboard, with nothing in the console to explain it."""
    src = _read(FRAMES[0])
    for m in re.finditer(r"postMessage\(([^;]*?)\)\s*;", src, re.S):
        call = m.group(1)
        assert "window.location.origin" not in call, (
            "a parent→frame message still targets the app origin"
        )


def test_the_parent_still_sends_the_data_and_the_polish_signals():
    """The messages themselves must survive the retarget — dropping one leaves
    a dashboard with no data or a Polish button that does nothing."""
    src = _read(FRAMES[0])
    for kind in ("ARTIFACT_DATA", "POLISH_ENTER", "POLISH_EXIT"):
        assert kind in src, f"{kind} message disappeared"


def test_the_child_to_parent_direction_is_unchanged():
    """It already used `'*'`, correctly — an opaque frame has no origin to
    name. Locked in so a later 'tightening' does not break the ready signal."""
    builder = _read(FE / "utils" / "artifactIframe.ts")
    ready = [l for l in builder.splitlines() if "ARTIFACT_READY" in l and "postMessage" in l]
    assert ready, "the frame no longer announces itself"
    assert all("'*'" in l for l in ready)


# --- the capabilities the opaque origin took away ------------------------------
#
# Dropping `allow-same-origin` was correct and must stand. But an opaque origin
# does not only lose DOM access — it loses several ordinary browser capabilities,
# and each one that the product actually used broke silently. React loading was
# the first (fixed separately). Downloads were the second, and the failure mode
# is the worst kind: Chromium refuses a download from a frame without
# `allow-downloads` and logs NOTHING AT ALL, so `window.exportCSV`'s `<a download>`
# click simply vanished and the CSV button on every dashboard did nothing.
#
# ★`allow-downloads` is not a partial undo of the security fix. Verified in
# Chromium against this exact markup: adding it leaves `window.origin === "null"`
# and leaves `localStorage`, `sessionStorage`, `document.cookie` and
# `parent.document` all throwing SecurityError — byte-identical to
# `allow-scripts` alone. The only behaviour that changes is that a download the
# frame starts is no longer refused.

def test_every_artifact_frame_can_download():
    """★The defect. One missing token, no console output, a dead button."""
    offenders = []
    for path, values in _artifact_frame_sources():
        for v in values:
            if "allow-scripts" in v and "allow-downloads" not in v:
                offenders.append(f"{path.relative_to(REPO)}: sandbox=\"{v}\"")
    assert not offenders, (
        "these artifact frames cannot download. Chromium refuses `<a download>` "
        "in a sandboxed frame that lacks `allow-downloads` and reports NOTHING, "
        "so the dashboard CSV button will silently do nothing:\n  "
        + "\n  ".join(offenders)
    )


def test_no_artifact_frame_regained_the_app_origin():
    """The other half of the pair, over the DERIVED set rather than the list —
    so a newly added frame cannot quietly ship with `allow-same-origin`."""
    offenders = []
    for path, values in _artifact_frame_sources():
        for v in values:
            if "allow-scripts" in v and "allow-same-origin" in v:
                offenders.append(f"{path.relative_to(REPO)}: sandbox=\"{v}\"")
    assert not offenders, (
        "allow-scripts + allow-same-origin is the documented way to have no "
        "sandbox at all — this re-opens the escape fixed in 0.0.490.14:\n  "
        + "\n  ".join(offenders)
    )


def test_the_sweep_actually_finds_the_frames():
    """★Guard the guard. Both assertions above iterate a derived set; if the
    derivation ever finds nothing (a renamed variable, a moved directory, a
    comment-stripper bug) they pass vacuously and forever. Three frames are
    known to exist today — the panel, its fullscreen modal, and the public
    share page — across two files."""
    found = _artifact_frame_sources()
    files = {p for p, _ in found}
    attrs = [v for _, values in found for v in values]
    assert len(attrs) >= 3, f"expected at least 3 artifact sandbox attributes, found {attrs}"
    for expected in FRAMES:
        assert expected in files, (
            f"the sweep no longer sees {expected.name} as an artifact frame — "
            "fix the derivation, do not weaken the assertions"
        )


def test_the_comment_stripper_does_not_eat_urls():
    """The stripper runs over every scanned file; if it mangled `https://` it
    could silently change what the assertions see."""
    assert "https://example.test/x" in _strip_comments("const u = 'https://example.test/x'; // note")
    assert "note" not in _strip_comments("const u = 'https://example.test/x'; // note")
    assert 'sandbox="allow-scripts"' not in _strip_comments('<!-- sandbox="allow-scripts" -->')


def test_the_sandbox_html_shell_agrees():
    """`artifact-sandbox.html` is the MCP-apps shell for the same content and
    posts to its parent the same way."""
    shell = _read(FE / "public" / "artifact-sandbox.html")
    for l in shell.splitlines():
        if "postMessage" in l and "parent" in l:
            assert "'*'" in l or '"*"' in l
