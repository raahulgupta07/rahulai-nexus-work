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

# Libraries needed for dashboard (page) mode artifacts
_PAGE_LIBS = [
    "tailwindcss-3.4.16.js",
    "react-18.development.js",
    "react-dom-18.development.js",
    "babel-standalone.min.js",
    "echarts-5.min.js",
]

# Libraries needed for slides mode artifacts
_SLIDES_LIBS = [
    "tailwindcss-3.4.16.js",
]


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


def _required_lib_names() -> set[str]:
    """Every vendored file a renderer can ask for."""
    return set(_PAGE_LIBS) | set(_SLIDES_LIBS) | {_GLOBALS_FILENAME}


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

    lib_files = _PAGE_LIBS if mode == "page" else _SLIDES_LIBS
    parts = []

    for filename in lib_files:
        content = _read_lib(libs_dir, filename)  # raises FileNotFoundError if missing
        parts.append(f"<script>{content}</script>")

    # Add global setup for page mode (hooks, EChart wrapper, filters, etc.)
    if mode == "page":
        parts.append(f"<script>{_read_globals()}</script>")

    return "\n".join(parts)
