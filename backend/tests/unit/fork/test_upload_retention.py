"""Removed uploads must stay recoverable for a while, then stop costing disk.

Removing a file soft-deletes the row and leaves the bytes in uploads/files/.
That is deliberate — it is the only reason files destroyed by mis-clicks could
be restored, twice, with nothing lost. But nothing ever reclaimed those bytes,
so the directory grew with every removal and every deleted agent, for the life
of the install.

Measured on a development instance after a single afternoon of ordinary use:
20 files, 18 MB, of which 6 belonged to anything the product could still show.

These tests pin both halves of the answer — that expired files ARE reclaimed,
and, more importantly, that everything else is left strictly alone.
"""
import os
from datetime import datetime, timedelta

import pytest

from app.services import upload_retention
from app.services.upload_retention import (
    DEFAULT_RETENTION_DAYS,
    purge_expired_uploads,
)


class _Row:
    def __init__(self, path, deleted_at=None):
        self.path = path
        self.deleted_at = deleted_at


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    """Stands in for the session: the filter is exercised by what we hand back."""

    def __init__(self, rows):
        self._rows = rows
        self.queried = False

    async def execute(self, _stmt):
        self.queried = True
        return _FakeResult(self._rows)


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    root = tmp_path / "uploads" / "files"
    root.mkdir(parents=True)
    monkeypatch.setattr(upload_retention, "uploads_root", lambda: str(root))
    return root


def _make(root, name, size=1024):
    p = root / name
    p.write_bytes(b"x" * size)
    return str(p)


# ── what gets reclaimed ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_long_removed_file_is_purged(uploads):
    path = _make(uploads, "old.csv", 2048)
    db = _FakeDB([_Row(path, datetime.utcnow() - timedelta(days=90))])

    result = await purge_expired_uploads(db, retention_days=30)

    assert result["purged"] == 1
    assert result["freed_bytes"] == 2048
    assert not os.path.exists(path)


@pytest.mark.asyncio
async def test_a_dry_run_reports_without_deleting(uploads):
    """So the size of a first sweep on a long-running install can be known
    before it runs, rather than discovered afterwards."""
    path = _make(uploads, "old.csv")
    db = _FakeDB([_Row(path, datetime.utcnow() - timedelta(days=90))])

    result = await purge_expired_uploads(db, retention_days=30, dry_run=True)

    assert result["purged"] == 1
    assert result["dry_run"] is True
    assert os.path.exists(path), "dry run deleted a file"


@pytest.mark.asyncio
async def test_the_summary_names_the_files(uploads):
    """A sweep that reports only a count cannot be checked against what a user
    says went missing."""
    _make(uploads, "old.csv")
    db = _FakeDB([_Row(str(uploads / "old.csv"), datetime.utcnow() - timedelta(days=90))])

    result = await purge_expired_uploads(db, retention_days=30)

    assert result["files"] == ["old.csv"]


# ── what must survive ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_file_inside_the_window_is_kept(uploads):
    """The window is the whole point. Purging on removal would have destroyed
    the files recovered twice over."""
    path = _make(uploads, "recent.csv")
    db = _FakeDB([])  # the query itself excludes it; nothing reaches the loop

    result = await purge_expired_uploads(db, retention_days=30)

    assert result["purged"] == 0
    assert os.path.exists(path)


@pytest.mark.asyncio
async def test_a_file_with_no_row_is_never_touched(uploads):
    """The tempting sweep, and the wrong one. An unreferenced file looks like
    garbage, but the same shape appears when a write lands before its row
    commits, or when a path is stored in a form this code cannot read. Deleting
    on absence of evidence turns a bug elsewhere into destroyed user data."""
    stray = _make(uploads, "no-row-points-here.csv")
    db = _FakeDB([])

    await purge_expired_uploads(db, retention_days=0)

    assert os.path.exists(stray), (
        "the sweep deleted a file that no database row referenced — absence of a "
        "row is not proof the bytes are unwanted"
    )


@pytest.mark.asyncio
async def test_only_the_basename_is_trusted(uploads, tmp_path):
    """`path` is a database value. A tampered or malformed one must not let the
    sweep reach outside the uploads directory."""
    outside = tmp_path / "important.txt"
    outside.write_bytes(b"do not delete")
    db = _FakeDB([_Row("../../../important.txt", datetime.utcnow() - timedelta(days=90))])

    await purge_expired_uploads(db, retention_days=30)

    assert outside.exists(), "the sweep escaped the uploads directory"


@pytest.mark.asyncio
async def test_a_missing_file_is_not_an_error(uploads):
    """Rows outlive their bytes — a previous sweep, a restored backup, a manual
    tidy-up. That is a normal state, not a failure."""
    db = _FakeDB([_Row(str(uploads / "already-gone.csv"),
                       datetime.utcnow() - timedelta(days=90))])

    result = await purge_expired_uploads(db, retention_days=30)

    assert result["purged"] == 0


@pytest.mark.asyncio
async def test_one_bad_file_does_not_stop_the_sweep(uploads, monkeypatch):
    """Otherwise a single locked or unreadable file pins every later one on
    disk, and the space is never reclaimed."""
    a = _make(uploads, "a.csv")
    b = _make(uploads, "b.csv")
    old = datetime.utcnow() - timedelta(days=90)

    real_remove = os.remove

    def flaky(path):
        if path.endswith("a.csv"):
            raise PermissionError("locked")
        return real_remove(path)

    monkeypatch.setattr(os, "remove", flaky)
    result = await purge_expired_uploads(_FakeDB([_Row(a, old), _Row(b, old)]),
                                         retention_days=30)

    assert result["purged"] == 1
    assert os.path.exists(a) and not os.path.exists(b)


# ── the window itself ───────────────────────────────────────────────────────

def test_the_default_window_survives_a_weekend():
    """"I deleted the wrong thing on Friday" has to be recoverable on Monday."""
    assert DEFAULT_RETENTION_DAYS >= 7


@pytest.mark.asyncio
async def test_a_database_failure_reports_rather_than_raises(monkeypatch):
    """It runs on a scheduler tick shared with other jobs."""
    class _Broken:
        async def execute(self, _):
            raise RuntimeError("db down")

    result = await purge_expired_uploads(_Broken(), retention_days=30)
    assert result["purged"] == 0
    assert "error" in result


def test_the_sweep_is_registered_on_the_scheduler():
    """An unscheduled sweep reclaims nothing, and looks identical to a
    scheduled one from inside this module."""
    from pathlib import Path

    main_py = Path(__file__).resolve().parents[3] / "main.py"
    src = main_py.read_text()
    assert "sweep_expired_uploads" in src
    assert "upload_retention_sweep" in src
    assert "is_scheduler_leader" in src[:src.index("upload_retention_sweep")]
