"""The JS that runs inside every artifact sandbox must be committed and pinned.

These libraries — Tailwind's JIT compiler, React, Babel's JSX transpiler,
ECharts, PDF.js — are loaded into the artifact iframe and into headless
Chromium during PDF export. They are, in the most literal sense, code we
execute on behalf of users.

They used to be `.gitignore`d and fetched at Docker build time by
`scripts/download-vendor-libs.sh` with `curl -sL` and **no checksum**, from
URLs that were partly unpinned:

    unpkg.com/@babel/standalone/babel.min.js   ← no version at all
    cdn.jsdelivr.net/npm/echarts@5/...         ← major range
    unpkg.com/react@18/...                     ← major range

Three consequences, all real:

  1. ★Builds were not reproducible, and it had already bitten: the committed
     bytes resolve to `@babel/standalone` **8.0.4** — a MAJOR version. Any
     image built a few months earlier carries Babel 7. The JSX transpiler
     every dashboard is rendered with changed silently, between builds, with
     nothing recording it.
  2. Whatever a CDN returned was baked in and executed, with no integrity
     check of any kind.
  3. `test_pdf_export.py::test_dashboard_renders_to_pdf` skipped itself
     against a bare checkout, because the files it needs did not exist there.
     A skip reads like a pass in a summary line.

Committing them fixes all three and removes nine network fetches from the
build. This file is what keeps them committed.

★These tests FAIL rather than skip. That is the whole point — the defect being
guarded against is a missing file, and a guard that skips when the thing is
missing guards nothing.
"""
import hashlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
LIBS = REPO / "frontend" / "public" / "libs"
MANIFEST = LIBS / "libs.sha256"
SCRIPT = REPO / "scripts" / "download-vendor-libs.sh"
DOCKERFILE = REPO / "Dockerfile"

# Every file the artifact sandbox and the PDF renderer load.
EXPECTED = {
    "tailwindcss-3.4.16.js",
    "react-18.production.min.js",
    "react-18.development.js",
    "react-dom-18.production.min.js",
    "react-dom-18.development.js",
    "babel-standalone.min.js",
    "echarts-5.min.js",
    "pdf.min.js",
    "pdf.worker.min.js",
}


def _manifest() -> dict[str, str]:
    entries = {}
    for line in MANIFEST.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        digest, name = line.split(None, 1)
        entries[name.strip()] = digest
    return entries


# --- the files exist ---------------------------------------------------------

def test_the_repo_layout_is_what_this_file_assumes():
    """★Guard the guard. If the path walk is wrong, every assertion below
    passes vacuously against an empty directory."""
    assert (REPO / "Dockerfile").is_file(), f"REPO resolved wrong: {REPO}"
    assert LIBS.is_dir(), f"libs dir not found at {LIBS}"


def test_every_sandbox_library_is_committed():
    missing = sorted(n for n in EXPECTED if not (LIBS / n).is_file())
    assert not missing, (
        f"missing from the checkout: {missing}. These are executed inside the "
        "artifact sandbox and must be committed, not downloaded at build time."
    )


def test_no_library_is_a_zero_byte_or_error_page():
    """A failed `curl` can leave a short HTML error page behind, which is far
    worse than an empty file: it looks present and breaks at render time."""
    for name in sorted(EXPECTED):
        data = (LIBS / name).read_bytes()
        assert len(data) > 5_000, f"{name} is only {len(data)} bytes"
        head = data[:200].lstrip().lower()
        assert not head.startswith(b"<!doctype"), f"{name} is an HTML page"
        assert not head.startswith(b"<html"), f"{name} is an HTML page"


# --- and they are the files we intended --------------------------------------

def test_the_checksum_manifest_covers_every_library():
    entries = _manifest()
    assert set(entries) == EXPECTED, (
        f"manifest covers {sorted(entries)}, expected {sorted(EXPECTED)}"
    )


def test_every_library_matches_its_committed_checksum():
    """★The real assertion. Catches a corrupted checkout, a partial LFS fetch,
    or a file swapped without going through the update tool."""
    for name, want in sorted(_manifest().items()):
        got = hashlib.sha256((LIBS / name).read_bytes()).hexdigest()
        assert got == want, (
            f"{name} does not match the committed checksum.\n"
            f"  committed: {want}\n  on disk:   {got}"
        )


# --- the update tool cannot go back to floating versions ---------------------

def _script_body() -> str:
    """Source with comments stripped — otherwise these assertions match the
    explanation written above them (this has bitten four times)."""
    return "\n".join(
        l.split("#", 1)[0] for l in SCRIPT.read_text().splitlines()
    )


def test_no_download_url_floats():
    """★The bug that let Babel cross a major version. Every URL must carry a
    full x.y.z, interpolated from a pinned variable or written literally."""
    body = _script_body()
    floating = []
    for url in re.findall(r'https://[^\s"\']+', body):
        # Resolve the ${VAR} pins this script uses.
        resolved = re.sub(r"\$\{[A-Z_]+\}", "0.0.0", url)
        if not re.search(r"\d+\.\d+\.\d+", resolved):
            floating.append(url)
    assert not floating, f"unpinned or major-range URLs: {floating}"


def test_every_version_pin_is_exact():
    body = _script_body()
    for var in ("TAILWIND_VERSION", "REACT_VERSION", "BABEL_VERSION",
                "ECHARTS_VERSION", "PDFJS_VERSION"):
        m = re.search(rf'^{var}="([^"]+)"', body, re.M)
        assert m, f"{var} is not declared"
        assert re.fullmatch(r"\d+\.\d+\.\d+", m.group(1)), (
            f"{var}={m.group(1)!r} is not an exact version"
        )


def test_the_downloads_are_verified_before_they_replace_anything():
    """Pinning alone is not integrity — a pinned URL can still serve different
    bytes. The tool must compare against the manifest and refuse on mismatch."""
    body = _script_body()
    assert "shasum -a 256" in body
    assert "MISMATCH" in body
    assert "exit 1" in body


def test_overwriting_the_manifest_requires_an_explicit_opt_in():
    """Rewriting the manifest is how integrity is lost by accident. It must be
    a deliberate act, never the default path."""
    body = _script_body()
    assert "UPDATE_MANIFEST" in body
    i_guard = body.index("UPDATE_MANIFEST")
    i_copy = body.index('cp "$STAGING"')
    assert i_guard < i_copy, "files are copied before the opt-in is checked"


# --- the build no longer downloads them --------------------------------------

def test_the_docker_build_does_not_fetch_them():
    """★A committed copy is pointless if the build overwrites it from a CDN."""
    body = "\n".join(
        l.split("#", 1)[0] for l in DOCKERFILE.read_text().splitlines()
    )
    assert "download-vendor-libs.sh" not in body, (
        "the Dockerfile still runs the download script — committed libs would "
        "be overwritten by whatever the CDN serves at build time"
    )


def test_the_build_fails_loudly_if_the_libs_are_absent():
    """Without this the image builds fine and every artifact renders blank at
    runtime — the failure would surface far from its cause."""
    body = DOCKERFILE.read_text()
    assert "libs.sha256" in body and "exit 1" in body


def test_the_libs_reach_the_image():
    """They are served from frontend/public and copied by the frontend COPY —
    a .dockerignore rule excluding them would silently empty the directory."""
    ignore = (REPO / ".dockerignore").read_text().splitlines()
    for pattern in ignore:
        p = pattern.strip()
        if not p or p.startswith("#"):
            continue
        assert "libs" not in p, f".dockerignore may exclude the libs: {p!r}"


# --- and the test that used to skip now runs ---------------------------------

def test_the_pdf_export_test_no_longer_skips_itself():
    """The original symptom. `test_pdf_export.py` guards on the libs being
    present; with them committed that guard must now be satisfied in a plain
    checkout."""
    from app.services import artifact_libs

    d = artifact_libs._find_libs_dir()
    assert d is not None, "artifact_libs cannot find the vendored libs"
    assert (d / "tailwindcss-3.4.16.js").is_file()


# --- a stale build directory must not shadow the real one --------------------

def test_a_partial_directory_does_not_win_over_a_complete_one(monkeypatch, tmp_path):
    """★The bug this change uncovered.

    `_find_libs_dir` used to accept the first candidate that was merely
    non-empty. A six-day-old `frontend/.output/public/libs` holding only
    `.gitkeep` and `artifact-globals.js` therefore won over the real
    directory — and the eventual error blamed a missing download, which is not
    what went wrong. Dashboard PDF export fails and the message sends you to
    the wrong script.
    """
    from app.services import artifact_libs

    stale = tmp_path / "stale"
    stale.mkdir()
    (stale / "artifact-globals.js").write_text("// leftover")

    good = tmp_path / "good"
    good.mkdir()
    for name in artifact_libs._required_lib_names():
        (good / name).write_text("// real")

    # Stale is checked FIRST, exactly as the real candidate order does.
    monkeypatch.setattr(artifact_libs, "_CANDIDATE_DIRS", [stale, good])
    assert artifact_libs._find_libs_dir() == good


def test_an_incomplete_set_is_still_returned_when_nothing_is_complete(monkeypatch, tmp_path):
    """Deliberate fallback: a deployment shipping a trimmed set must not be
    made worse than it was before this change."""
    from app.services import artifact_libs

    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "artifact-globals.js").write_text("// only this")
    monkeypatch.setattr(artifact_libs, "_CANDIDATE_DIRS", [partial])
    assert artifact_libs._find_libs_dir() == partial


def test_no_candidate_at_all_still_reports_nothing(monkeypatch, tmp_path):
    from app.services import artifact_libs

    monkeypatch.setattr(artifact_libs, "_CANDIDATE_DIRS", [tmp_path / "nope"])
    assert artifact_libs._find_libs_dir() is None


def test_the_required_set_covers_every_renderer(monkeypatch):
    """If a renderer gains a library and this set is not updated, the resolver
    would go back to accepting a directory missing it."""
    from app.services import artifact_libs

    required = artifact_libs._required_lib_names()
    assert set(artifact_libs._PAGE_LIBS) <= required
    assert set(artifact_libs._SLIDES_LIBS) <= required
    assert artifact_libs._GLOBALS_FILENAME in required
