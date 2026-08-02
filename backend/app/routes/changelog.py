import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.settings.config import settings

router = APIRouter()

# The CHANGELOG.md lives at the repository root. In production the backend runs
# from /app/backend (see start.sh), so the repo root maps to /app and the file
# is copied there by the Dockerfile. We resolve a list of candidate locations so
# the endpoint works identically in dev (repo checkout) and in the container.
_HEADER_RE = re.compile(
    r"^##\s+Version\s+(?P<version>\S+)\s*(?:\((?P<date>[^)]+)\))?\s*$"
)

# Anchored deliberately: an INDENTED bullet is a sub-point of the entry above
# it, not an entry, so it stays a continuation line the way it always has.
_BULLET_RE = re.compile(r"^[-*]\s+(.*)$")


def _candidate_paths() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.getenv("CHANGELOG_PATH")
    if env_path:
        candidates.append(Path(env_path))
    # backend/app/routes/changelog.py -> parents[3] == repo root (/app in prod)
    candidates.append(Path(__file__).resolve().parents[3] / "CHANGELOG.md")
    # cwd-relative (uvicorn runs from backend/, repo root is one level up)
    candidates.append(Path.cwd().parent / "CHANGELOG.md")
    candidates.append(Path("/app/CHANGELOG.md"))
    return candidates


def _resolve_changelog_path() -> Optional[Path]:
    for path in _candidate_paths():
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _parse_changelog(text: str) -> list[dict]:
    """Parse the release-notes markdown into a list of version entries.

    Each ``## Version X.Y.Z (Date)`` heading starts a new section. Beneath it,
    an entry is either a bullet (``- ...``) or a standalone paragraph, and a
    blank line ends whichever is open. Wrapped lines are joined into the entry
    they continue. Entries keep their raw inline markdown so the frontend can
    render **bold** / `code` / links.

    ★A paragraph is an entry in its own right. The first version of this parser
    recognised bullets only, and kept a paragraph solely as the continuation of
    an already-open bullet. Release notes written as prose therefore produced a
    version with zero entries — 0.0.510.5, .6 and .7 each rendered as a heading
    with nothing under it in "What's New" — and every version's opening
    paragraph, the one that says what the release is about, was dropped before
    its first bullet was reached. Nothing failed and nothing logged; the notes
    were simply absent.
    """
    versions: list[dict] = []
    current: Optional[dict] = None
    current_entry: Optional[list[str]] = None

    def flush_entry() -> None:
        nonlocal current_entry
        if current is not None and current_entry is not None:
            joined = " ".join(part.strip() for part in current_entry).strip()
            if joined:
                current["entries"].append(joined)
        current_entry = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        header = _HEADER_RE.match(line)
        if header:
            flush_entry()
            current = {
                "version": header.group("version"),
                "date": (header.group("date") or "").strip() or None,
                "entries": [],
            }
            versions.append(current)
            continue

        if current is None:
            continue

        if line.strip() == "":
            flush_entry()
            continue

        # A sub-heading structures a release, it is not a note about it.
        if line.lstrip().startswith("#"):
            flush_entry()
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            flush_entry()
            current_entry = [bullet.group(1)]
        elif current_entry is not None:
            # Continuation of the entry above (a wrapped line).
            current_entry.append(line.strip())
        else:
            # A paragraph with no bullet open — an entry of its own.
            current_entry = [line.strip()]

    flush_entry()
    return versions


@lru_cache(maxsize=1)
def _load_changelog() -> dict:
    path = _resolve_changelog_path()
    if path is None:
        return {"versions": [], "available": False}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"versions": [], "available": False}
    return {"versions": _parse_changelog(text), "available": True}


@router.get("/changelog", tags=["settings"])
async def get_changelog():
    """Public release notes, parsed from the repo-root CHANGELOG.md.

    Returns a structured list of versions so the frontend can render a clean
    "What's New" view without shipping the (large) markdown file in the JS
    bundle. Unauthenticated: release notes are not sensitive.
    """
    data = _load_changelog()
    return JSONResponse({
        "current_version": settings.PROJECT_VERSION,
        "available": data["available"],
        "versions": data["versions"],
    })
