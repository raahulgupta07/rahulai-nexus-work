"""`decide_scope` is synchronous and must never trigger a lazy load.

It runs inside an async request. Reading an ORM relationship that is not
currently loaded makes SQLAlchemy fetch it, and doing that outside a greenlet
raises MissingGreenlet — which aborted the whole of `_resolve_scope` on dev and
left the turn with no file scoping at all.

`DataSource.files` is `lazy="selectin"`, so it is normally loaded with its
parent — which is precisely why this survived. A commit EXPIRES loaded
attributes, and the next read of an expired attribute is a fresh load. Any
caller that commits between fetching its data sources and resolving scope hits
it.

★`getattr(ds, "files", None)` does not help. The default applies when the
attribute is MISSING; an unloaded attribute is not missing, and the two are
indistinguishable at the call site.
"""
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

import main  # noqa: F401  — boots the ORM registry
from app.models.base import Base
from app.models.data_source import DataSource
from app.models.organization import Organization
from app.services.file_scope import _files_without_io


@pytest.fixture
def db():
    tmp = Path(tempfile.mkdtemp(prefix="file-scope-"))
    path = tmp / "t.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        shutil.rmtree(tmp, ignore_errors=True)


def _data_source(db) -> DataSource:
    org = Organization(id=str(uuid.uuid4()), name="Org")
    # ★`type` is NOT a column here — it moved to Connection in this fork, and
    # passing it raises TypeError. Only the NOT NULL columns are set.
    ds = DataSource(
        id=str(uuid.uuid4()),
        name="Agent",
        organization_id=org.id,
        is_active=True,
        is_public=False,
        publish_status="published",
        reliability_status="unknown",
        use_llm_sync=False,
    )
    db.add_all([org, ds])
    db.commit()
    return ds


@pytest_asyncio.fixture
async def adb():
    """★An ASYNC session, deliberately.

    A sync Session loads an expired attribute perfectly happily, so a
    sync-session version of the test below passes against the broken code —
    measured. MissingGreenlet only exists on the async driver, which is what
    production runs, so only an async session can demonstrate the defect.
    """
    tmp = Path(tempfile.mkdtemp(prefix="file-scope-async-"))
    path = tmp / "t.db"
    sync = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(sync)
    sync.dispose()
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_an_expired_relationship_does_not_raise(adb):
    """★The production shape: a commit expired it, the next read went to IO.

    Reading `ds.files` here — an async function, outside any greenlet — is
    exactly what `decide_scope` does, and on the async driver it raises
    MissingGreenlet. `_files_without_io` must answer without touching the
    database at all.
    """
    org = Organization(id=str(uuid.uuid4()), name="Org")
    ds = DataSource(
        id=str(uuid.uuid4()), name="Agent", organization_id=org.id,
        is_active=True, is_public=False, publish_status="published",
        reliability_status="unknown", use_llm_sync=False,
    )
    adb.add_all([org, ds])
    await adb.commit()
    adb.expire(ds)                       # exactly what a commit does
    assert "files" in inspect(ds).unloaded, "precondition: the attribute is unloaded"

    # ★Guarded read FIRST. A failed lazy load leaves the attribute no longer
    # marked unloaded, so probing the raw read beforehand destroys the very
    # state under test — measured: the guard then fell through and raised.
    assert _files_without_io(ds) == []

    # Now prove the unguarded read really does bite on this fixture, so the
    # assertion above is known to be testing something real rather than
    # passing because nothing was ever wrong.
    with pytest.raises(Exception) as raised:
        _ = list(ds.files)
    assert "MissingGreenlet" in type(raised.value).__name__ or \
        "greenlet" in str(raised.value).lower(), raised.value


def test_a_loaded_relationship_is_still_read(db):
    """★Positive control. Returning [] unconditionally would pass the test
    above and silently disable the agent-files exclusion entirely."""
    ds = _data_source(db)
    _ = ds.files                        # force it loaded
    assert "files" not in inspect(ds).unloaded

    assert _files_without_io(ds) == []   # loaded, and genuinely empty

    # ...and the read path really does return contents when there are any.
    class _F:
        id = "file-1"

    class _Stub:
        files = [_F()]

    assert [f.id for f in _files_without_io(_Stub())] == ["file-1"]


def test_a_non_orm_object_still_works(db):
    """Tests and callers pass plain stubs; inspect() raises on those."""
    class _Stub:
        files = None
    assert _files_without_io(_Stub()) == []


def test_decide_scope_does_not_read_files_directly():
    """★The guard that keeps the fix in place.

    A future edit reintroducing `getattr(ds, "files", ...)` in the comprehension
    restores the crash, and nothing else would notice until a turn silently
    lost its scoping again.
    """
    src = (Path(__file__).resolve().parents[3] / "app" / "services"
           / "file_scope.py").read_text(encoding="utf-8")
    start = src.index("    of_sources = {")
    block = src[start:start + 400]
    assert "_files_without_io(ds)" in block, (
        "decide_scope reads ds.files directly again; an expired relationship "
        "will raise MissingGreenlet and abort scope resolution"
    )
