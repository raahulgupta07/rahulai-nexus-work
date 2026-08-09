import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_user_optional
from app.core.permission_resolver import FULL_ADMIN, resolve_permissions
from app.dependencies import get_async_db
from app.models.user import User
from app.settings.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# How many releases a non-admin may read. Admins get the whole history.
#
# ★Deliberately a constant, not a setting: one number in one place. The release
# notes are a product surface, not per-deployment configuration, and an env var
# here would be a knob nobody sets and everybody has to reason about.
PUBLIC_VERSION_LIMIT = 3

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


async def _caller_sees_full_history(
    request: Request, user: Optional[User], db: AsyncSession
) -> bool:
    """True only for an administrator. Fails closed on anything unexpected.

    Two different notions of "admin" exist here and they are not interchangeable:

    - ``Membership.role == 'admin'`` / ``full_admin_access`` — the ORG role, and
      the one shown on the account badge in the sidebar. This is what a customer
      promotes a colleague to, so it is the one that must work.
    - ``User.is_superuser`` — INSTANCE level. On this fork it is only ever set by
      the first-signup bootstrap (``core/auth.py``), so gating on it alone would
      lock out every admin created afterwards.

    So: the org permission decides, and ``is_superuser`` is a fallback for the
    case where no organization was named on the request at all.
    """
    if user is None:
        return False

    org_id = request.headers.get("X-Organization-Id")
    if not org_id:
        # No org context to resolve against — an instance superuser still gets
        # the full history, anyone else gets the public view.
        return bool(getattr(user, "is_superuser", False))

    try:
        resolved = await resolve_permissions(db, str(user.id), str(org_id))
    except Exception:
        # resolve_permissions already swallows and audits its own failures; this
        # is belt-and-braces so the changelog can never 500 on a permission read.
        logger.warning("Changelog permission resolution failed", exc_info=True)
        return False

    # ★``has_org_permission`` treats full_admin_access as a bypass for ANY
    # permission, so asking it for FULL_ADMIN is the same as asking whether the
    # caller holds FULL_ADMIN. Membership is enforced inside resolve_permissions:
    # a non-member of ``org_id`` matches no role assignment and no legacy
    # Membership row, so the resolved set is empty and this returns False. That
    # is why a caller cannot widen their own view by inventing a header value.
    if resolved.has_org_permission(FULL_ADMIN):
        return True

    return bool(getattr(user, "is_superuser", False))


@router.get("/changelog", tags=["settings"])
async def get_changelog(
    request: Request,
    user: Optional[User] = Depends(current_user_optional),
    db: AsyncSession = Depends(get_async_db),
):
    """Release notes, parsed from the repo-root CHANGELOG.md.

    Returns a structured list of versions so the frontend can render a clean
    "What's New" view without shipping the (large) markdown file in the JS
    bundle.

    ★**Non-admins see only the newest ``PUBLIC_VERSION_LIMIT`` releases.** The
    full history is an internal record — it names ported upstream versions,
    reversed decisions and fixes for bugs that shipped — and there is no reason
    an ordinary member needs it. The cut happens HERE rather than in the modal
    because a frontend slice still ships every release over the wire, where
    devtools reads it.

    ★**The route stays reachable unauthenticated, and that is load-bearing.**
    ``frontend/plugins/versionCheck.client.ts`` polls this endpoint every 60s
    with a bare ``$fetch`` that carries no auth header, purely to notice that a
    new build was deployed; its catch is ``return null`` with the comment "never
    nag on errors". Requiring auth here would not fail loudly — it would delete
    the new-version toast silently and forever. So ``current_version`` and
    ``available`` are always served, and only ``versions`` is gated.
    """
    data = _load_changelog()
    all_versions = data["versions"]

    if await _caller_sees_full_history(request, user, db):
        visible = all_versions
    else:
        # ★Slice AFTER the cached load, never inside it. ``_load_changelog`` is
        # @lru_cache(maxsize=1) — caching a per-caller slice there would serve
        # whoever called first to everyone, so one admin opening the modal would
        # hand the full history to every member until the process restarted.
        visible = all_versions[:PUBLIC_VERSION_LIMIT]

    return JSONResponse({
        "current_version": settings.PROJECT_VERSION,
        "available": data["available"],
        "versions": visible,
        # So the modal can say "showing the N most recent" instead of a
        # truncated list silently passing for the whole history.
        "truncated": len(visible) < len(all_versions),
        "total_versions": len(all_versions),
    })
