"""Helper for loading vendored JS libraries for artifact rendering in headless browser.

In airgapped deployments, CDN URLs are not available. This module reads the
vendored JS files from disk and returns them as inline <script> tags for use
with Playwright's page.set_content() (which renders at about:blank and cannot
resolve relative paths).
"""

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# Paths where vendored libs may be found (checked in order):
# 1. Nuxt build output (production Docker image)
# 2. Frontend public dir (local development / Docker with public copied)
_CANDIDATE_DIRS = [
    Path(__file__).parent.parent.parent.parent / "frontend" / ".output" / "public" / "libs",
    Path(__file__).parent.parent.parent.parent / "frontend" / "public" / "libs",
]

# ★Which libraries an artifact document loads is decided in ONE place:
# frontend/public/libs/manifest.json. This list used to live here as well, and
# the two lists drifted — pdf.min.js was loaded by the in-app renderer and by
# nothing on the server, so a dashboard embedding a PDF rendered in the app and
# showed BowPdfViewer's "nolib" state in the thumbnail, the export and the
# planner's preview. Nothing errored; the page simply came out different.
#
# React is named by FAMILY in the manifest ("react-18"). Which build a renderer
# picks is a real choice — the browser can afford the development build's
# clearer errors, headless rendering wants the smaller one — so it is resolved
# here rather than pinned in the shared list.
_MANIFEST_FILENAME = "manifest.json"

_REACT_BUILD = {
    "react-18": "react-18.development.js",
    "react-dom-18": "react-dom-18.development.js",
}


@lru_cache(maxsize=1)
def _manifest() -> dict:
    libs_dir = _find_libs_dir()
    if libs_dir is None:
        raise FileNotFoundError(
            "Vendored JS libs directory not found. "
            "Run scripts/download-vendor-libs.sh during Docker build."
        )
    import json as _json

    return _json.loads((libs_dir / _MANIFEST_FILENAME).read_text(encoding="utf-8"))


def _libs_for(mode: str) -> list[str]:
    """Resolve the manifest's family names to the files on disk."""
    names = _manifest()["page" if mode == "page" else "slides"]
    return [_REACT_BUILD.get(n, n) for n in names]


_GLOBALS_FILENAME = "artifact-globals.js"


@lru_cache(maxsize=1)
def _read_globals() -> str:
    """Read the shared artifact-globals.js from the vendored libs directory."""
    libs_dir = _find_libs_dir()
    if libs_dir is None:
        raise FileNotFoundError(
            "Vendored JS libs directory not found. "
            "Run scripts/download-vendor-libs.sh during Docker build."
        )
    return (libs_dir / _GLOBALS_FILENAME).read_text(encoding="utf-8")


_CHECKSUM_FILENAME = "libs.sha256"


def _required_lib_names(candidate: Path | None = None) -> set[str]:
    """Every vendored file a complete libs directory must hold.

    ★A directory describes its OWN completeness. The list used to be a constant
    here, which meant the check could only ever be as current as whoever last
    remembered to edit it; and a first attempt at reading it from manifest.json
    was worse still — a candidate with no manifest produced a required set of
    one file, so a stale directory holding nothing but artifact-globals.js
    passed as complete and won. That is precisely the failure _find_libs_dir
    was written to stop.

    So the source is libs.sha256, which is generated beside the libraries and
    already names every one of them. A partial directory does not have a
    complete checksum file, and cannot fake one.
    """
    dirs = [candidate] if candidate is not None else list(_CANDIDATE_DIRS)
    for d in dirs:
        if d is None:
            continue
        f = d / _CHECKSUM_FILENAME
        if not f.is_file():
            continue
        try:
            names = {
                line.split(None, 1)[1].strip()
                for line in f.read_text(encoding="utf-8").splitlines()
                if line.strip() and len(line.split(None, 1)) == 2
            }
        except OSError:
            continue
        if names:
            return names | {_GLOBALS_FILENAME, _MANIFEST_FILENAME, _CHECKSUM_FILENAME}
    # No candidate describes itself. Fall back to the two files a renderer
    # cannot start without, rather than to nothing.
    return {_GLOBALS_FILENAME, _MANIFEST_FILENAME}


def _find_libs_dir() -> Path | None:
    """Find the directory containing vendored JS libraries.

    ★Prefers a candidate that actually HOLDS the libraries. The original test
    was `is_dir() and any(d.iterdir())` — merely non-empty — which let a stale
    or partial Nuxt output directory shadow the real one and win, because it
    is checked first.

    Observed: `frontend/.output/public/libs` left over from an earlier build
    contained only `.gitkeep`, `.gitignore` and `artifact-globals.js`. It was
    selected, and the failure then surfaced later in `_read_lib` as a
    FileNotFoundError whose message blames a missing download — pointing at
    entirely the wrong cause. Dashboard PDF export fails and the error says to
    re-run a script that has nothing to do with it.

    Falls back to the old "first non-empty" behaviour when no candidate is
    complete, so a deployment that deliberately ships a trimmed set is never
    made worse than before.
    """
    required = _required_lib_names()
    fallback: Path | None = None
    for d in _CANDIDATE_DIRS:
        if not d.is_dir():
            continue
        names = {p.name for p in d.iterdir()}
        if required <= names:
            return d
        if fallback is None and names:
            fallback = d
    return fallback


@lru_cache(maxsize=1)
def _read_lib(libs_dir: Path, filename: str) -> str:
    """Read a vendored JS file and return its contents."""
    path = libs_dir / filename
    return path.read_text(encoding="utf-8")


def get_inline_scripts(mode: str = "page") -> str:
    """Return inline <script> tags with vendored JS library contents.

    Args:
        mode: 'page' for React/Babel/ECharts dashboard, 'slides' for Tailwind-only.

    Returns:
        HTML string with <script>...</script> tags containing the library code.

    Raises:
        FileNotFoundError: If vendored libs directory or individual files are missing.
            In airgapped deployments there is no CDN to fall back to, so missing
            vendored files must fail loudly.
    """
    libs_dir = _find_libs_dir()

    if libs_dir is None:
        raise FileNotFoundError(
            "Vendored JS libs directory not found. "
            "Run scripts/download-vendor-libs.sh during Docker build."
        )

    lib_files = _libs_for(mode)
    parts = []

    for filename in lib_files:
        content = _read_lib(libs_dir, filename)  # raises FileNotFoundError if missing
        parts.append(f"<script>{content}</script>")

    # Add global setup for page mode (hooks, EChart wrapper, filters, etc.)
    if mode == "page":
        parts.append(f"<script>{_read_globals()}</script>")

    return "\n".join(parts)
