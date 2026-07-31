"""Reclaim the bytes of removed uploads, once they are past recovery.

Removing a file soft-deletes its row and leaves the bytes in ``uploads/files/``.
That is deliberate and has already earned itself: it is the only reason a set of
files destroyed by mis-clicks could be put back, twice, with nothing lost.

What was missing is the other half. Nothing ever reclaimed those bytes, so the
directory grew with every removal and every deleted agent, forever. Measured on
a small development instance after one afternoon: 20 files, 18 MB, of which
**6 belonged to anything the product could still show** — the rest was the
residue of ordinary use.

So: keep the recovery window, then take the space back. Two rules, both narrow:

  * a file is only ever purged if its row is soft-deleted AND has been for
    longer than the retention window, and
  * a file on disk that NO row points at is left alone, not tidied away.

The second rule is the important one. An unreferenced file looks like garbage
and is the obvious thing to sweep, but the same shape appears when a write
lands before its row is committed, or when a code path stores a path this
function does not know how to read. Deleting on absence-of-evidence would turn
a bug elsewhere into destroyed user data. Absence of a row is not proof the
bytes are unwanted — the same reasoning that stops a failed schema refresh from
pruning a catalog.
"""
import logging
import os
from datetime import datetime, timedelta

from sqlalchemy import select

from app.models.file import File

logger = logging.getLogger(__name__)

# How long a removed file stays recoverable. Long enough that "I deleted the
# wrong thing" is survivable across a weekend, short enough that the directory
# does not grow without bound.
DEFAULT_RETENTION_DAYS = 30


def uploads_root() -> str:
    """The directory uploads are stored in, resolved the same way as at write."""
    return os.path.join(os.getcwd(), "uploads", "files")


async def purge_expired_uploads(db, retention_days: int = DEFAULT_RETENTION_DAYS,
                                dry_run: bool = False) -> dict:
    """Delete the bytes of files removed longer ago than the retention window.

    Only the bytes go. The row is kept, so the product can still tell that a
    file existed and was removed — a listing that silently loses its history is
    harder to reason about than one showing a file whose contents have expired.

    Returns a summary rather than logging and forgetting: a sweep that reports
    nothing is indistinguishable from one that never ran.
    """
    cutoff = datetime.utcnow() - timedelta(days=max(0, retention_days))
    root = uploads_root()
    freed_bytes = 0
    purged: list[str] = []

    try:
        rows = (await db.execute(
            select(File).filter(
                File.deleted_at.is_not(None),
                File.deleted_at < cutoff,
            )
        )).scalars().all()
    except Exception as err:
        logger.warning(f"upload retention sweep could not read files: {err}")
        return {"purged": 0, "freed_bytes": 0, "error": str(err)}

    for row in rows:
        if not row.path:
            continue
        # Rebuild the path from the trusted root plus a sanitized basename, the
        # same way the download endpoint does. A tampered or malformed stored
        # path must not be able to point this at anything outside uploads/.
        target = os.path.join(root, os.path.basename(row.path))
        if not os.path.isfile(target):
            continue  # already gone; nothing to reclaim
        try:
            size = os.path.getsize(target)
            if not dry_run:
                os.remove(target)
            freed_bytes += size
            purged.append(os.path.basename(target))
        except Exception as err:
            # One unreadable file must not stop the sweep reclaiming the rest.
            logger.warning(f"could not purge {target}: {err}")

    if purged:
        logger.info(
            f"upload retention: {'would purge' if dry_run else 'purged'} "
            f"{len(purged)} file(s), {freed_bytes / 1e6:.1f} MB, "
            f"removed before {cutoff.isoformat()}"
        )
    return {"purged": len(purged), "freed_bytes": freed_bytes,
            "files": purged, "cutoff": cutoff.isoformat(), "dry_run": dry_run}


async def sweep_expired_uploads() -> None:
    """Scheduler entry point. Opens its own session and never raises.

    Runs on the scheduler leader only — see the registration in main.py. A
    failure here costs disk space, never correctness, so it must not be able to
    take down the tick that carries it.
    """
    try:
        from app.dependencies import async_session_maker

        async with async_session_maker() as db:
            await purge_expired_uploads(db)
    except Exception as err:
        logger.warning(f"upload retention sweep failed: {err}")
