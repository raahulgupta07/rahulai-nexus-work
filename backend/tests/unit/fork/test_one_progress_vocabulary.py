"""Four progress trackers, three words for "it worked", two for "it didn't".

    finished, successfully   ->  "completed" | "done" | "ok"
    finished, unsuccessfully ->  "failed"    | "error"
    in flight                ->  "running"   | "syncing" | "learning"
    nothing here             ->  "idle"      | "pending"

★Two of them appeared in the SAME payload: a per-user sync said
``status: "done"`` at the top while each workspace inside it said
``status: "ok"``. Nothing tells a consumer which spelling a given endpoint
uses, so the natural move — copy the check that worked on the last screen —
produces a comparison that is never true. A status test that never fires does
not look broken; it looks like the job never finishes.

It was not only cosmetic. Both "is a sync already running" guards compared
against ``"syncing"`` alone, so once a sync reached its LEARNING stage the 409
stopped guarding — at the longest part of the run, which is exactly when a
second crawl is most likely to be started.

Stored values are deliberately untouched. Normalisation happens on the way out,
so every row written by every previous version keeps working and no migration
is needed.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
STATUS = REPO / "backend" / "app" / "core" / "progress_status.py"
LEARN = REPO / "backend" / "app" / "services" / "learn_progress.py"
SYNC = REPO / "backend" / "app" / "services" / "connection_sync_progress.py"
PBI = REPO / "backend" / "app" / "routes" / "powerbi_user_signin.py"
FAB = REPO / "backend" / "app" / "routes" / "fabric_user_signin.py"
INDEXING = REPO / "backend" / "app" / "models" / "connection_indexing.py"
JOB = REPO / "backend" / "app" / "models" / "metadata_indexing_job.py"
FE = (
    REPO / "frontend" / "composables" / "useConnectionSync.ts",
    REPO / "frontend" / "components" / "datasources" / "LearnProgressBar.vue",
    REPO / "frontend" / "components" / "UserDataSourceCredentialsModal.vue",
)

from app.core.progress_status import (  # noqa: E402
    CANONICAL, TERMINAL, SUCCESSFUL,
    normalize, is_terminal, is_running, is_successful,
)


# ---------------------------------------------------------------------------
# The vocabulary itself
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "legacy,canonical",
    [
        ("done", "completed"), ("ok", "completed"), ("success", "completed"),
        ("error", "failed"), ("failure", "failed"),
        ("syncing", "running"), ("learning", "running"), ("indexing", "running"),
        ("canceled", "cancelled"),
        ("", "idle"), (None, "idle"),
        # already canonical → itself
        ("idle", "idle"), ("pending", "pending"), ("running", "running"),
        ("completed", "completed"), ("partial", "partial"),
        ("failed", "failed"), ("cancelled", "cancelled"),
    ],
)
def test_every_spelling_maps_to_one_word(legacy, canonical):
    assert normalize(legacy) == canonical


def test_the_alias_table_records_every_spelling_we_have_written():
    """★The catch-all HIDES a deleted alias.

    Unknown values fall through to `running`, which is right — but it means
    removing `"syncing": RUNNING` from the table changes no behaviour at all,
    so a behavioural test cannot see it. The table is also the record of which
    spellings this product has actually written; losing an entry loses the only
    place that says so. Assert the table, not just what it computes.
    """
    from app.core import progress_status

    for legacy in ("done", "ok", "error", "syncing", "learning", "canceled"):
        assert legacy in progress_status._ALIASES, legacy


def test_normalize_is_total():
    """★It must return a canonical value for ANYTHING. A serializer calls this
    on a stored string it did not write; returning the input unchanged would
    reintroduce exactly the drift being removed."""
    for probe in ("", " ", "DONE", "Ok", "wat", "🙂", 0, 1, True, [], {}):
        assert normalize(probe) in CANONICAL, probe


def test_an_unknown_status_is_treated_as_running():
    """★Not idle, and not failed. An unrecognised status came from somewhere, so
    something is happening. `idle` would tell a poller to stop watching a live
    job; `failed` would report a failure that may not have happened."""
    assert normalize("some_new_stage") == "running"


def test_case_and_padding_do_not_matter():
    assert normalize("  DONE  ") == "completed"
    assert normalize("Error") == "failed"


def test_partial_is_a_success():
    """★Some units failed, the rest are usable, the work is over. Calling it
    failed tells a member their agent is broken when it is merely incomplete."""
    assert is_successful("partial")
    assert is_terminal("partial")
    assert not is_running("partial")


def test_idle_and_pending_stay_different():
    """★Not synonyms. "nothing is queued" and "queued, will start" are different
    answers to "should I keep polling"."""
    assert normalize("idle") != normalize("pending")
    assert not is_terminal("idle") and not is_terminal("pending")
    assert not is_running("pending")


def test_terminal_means_stop_polling():
    for v in ("completed", "partial", "failed", "cancelled", "done", "error", "ok"):
        assert is_terminal(v), v
    for v in ("idle", "pending", "running", "syncing", "learning"):
        assert not is_terminal(v), v


def test_the_helpers_agree_with_the_tables():
    assert set(TERMINAL) <= set(CANONICAL)
    assert set(SUCCESSFUL) <= set(TERMINAL)
    for v in CANONICAL:
        assert is_terminal(v) == (v in TERMINAL), v
        assert is_successful(v) == (v in SUCCESSFUL), v


def test_the_indexing_trackers_already_spoke_it():
    """★The canonical set was chosen to match what already existed, not invented
    on top of it. These two enums are unchanged by this work."""
    for f in (INDEXING, JOB):
        src = f.read_text(encoding="utf-8")
        for value in ("pending", "running", "completed", "failed", "cancelled"):
            assert f'= "{value}"' in src, (f.name, value)


# ---------------------------------------------------------------------------
# Normalised on the way OUT, stored values untouched
# ---------------------------------------------------------------------------
def test_both_serializers_normalize():
    assert "normalize_status(row.status)" in LEARN.read_text(encoding="utf-8")
    assert "normalize_status(row.status)" in SYNC.read_text(encoding="utf-8")


def test_the_endpoints_inside_a_payload_normalize_too():
    """★The one that made this concrete: "done" at the top, "ok" per workspace,
    in a single response."""
    src = SYNC.read_text(encoding="utf-8")
    assert "_normalize_detail" in src
    assert "[_normalize_detail(d) for d in (row.detail or [])]" in src


def test_the_writers_are_left_alone():
    """★No migration, and every row written by every previous version keeps
    working. Rewriting stored values would be a swap; normalising on read is an
    upgrade."""
    src = SYNC.read_text(encoding="utf-8")
    assert 'row.status = "syncing"' in src
    assert 'row.status = "learning"' in src
    assert 'else "done"' in src
    assert 'row.status = "error"' in src
    learn = LEARN.read_text(encoding="utf-8")
    assert 'row.status = "done"' in learn
    assert 'row.status = "error"' in learn


def test_the_ttl_check_reads_the_canonical_value():
    """It used to compare a literal list against the stored spelling; after
    normalisation that list would silently never match, and a finished sync
    would report its result forever."""
    src = SYNC.read_text(encoding="utf-8")
    assert 'is_terminal(payload["status"])' in src
    assert 'payload["status"] in ("done", "partial", "error")' not in src


def test_the_endpoint_list_is_written_as_a_new_value():
    """★★★Found live, and older than this work: the per-workspace list never
    persisted at all.

    `list(row.detail or [])` copies the LIST but keeps the same dict objects
    SQLAlchemy loaded. Mutating one also mutates the value the ORM compares
    against, so it sees no change and never writes the column. `endpoints_done`
    is a scalar and did persist — so the counters advanced while every
    workspace row stayed "pending" forever, which reads as a sync that is
    somehow both progressing and not.

    The module already warned that an in-place JSON mutation is invisible and
    prescribed reassignment; reassigning an object you have already mutated is
    not enough, and that gap is what shipped.
    """
    src = SYNC.read_text(encoding="utf-8")
    body = src[src.index("async def endpoint_done("):]
    body = body[: body.index("async def learning(")]
    assert "list(row.detail or [])" not in body, "shallow copy is back"
    assert "[dict(d) if isinstance(d, dict) else d for d in (row.detail or [])]" in body
    assert 'flag_modified(row, "detail")' in body


# ---------------------------------------------------------------------------
# The guard that stopped guarding
# ---------------------------------------------------------------------------
def test_the_concurrency_guards_cover_the_whole_run():
    """★★A real bug, not tidying. `== "syncing"` does not match a sync in its
    LEARNING stage, so the 409 stopped guarding during the longest part of the
    run — the moment a second crawl is most likely to be started."""
    for f in (PBI, FAB):
        src = f.read_text(encoding="utf-8")
        assert 'current.get("status") == "syncing"' not in src, f.name
        assert 'is_running(current.get("status"))' in src, f.name
        assert "from app.core.progress_status import is_running" in src, f.name


# ---------------------------------------------------------------------------
# The consumers
# ---------------------------------------------------------------------------
_LEGACY = re.compile(r"""status\s*(?:===|!==|==)\s*['"](done|error|syncing|learning|ok)['"]""")


def test_no_consumer_still_checks_a_legacy_spelling():
    """★The failure mode is silent: the comparison is simply never true, and a
    status test that never fires reads as "the job never finished"."""
    for f in FE:
        src = f.read_text(encoding="utf-8")
        bad = [l.strip() for l in src.splitlines() if _LEGACY.search(l)]
        assert not bad, f"{f.name}: {bad}"


def test_the_sweep_is_actually_looking_at_something():
    """Guard the guard: if the pattern stopped matching this shape entirely, the
    test above would pass by checking nothing."""
    probe = "  const isDone = computed(() => s.status === 'done')"
    assert _LEGACY.search(probe)
    for f in FE:
        src = f.read_text(encoding="utf-8")
        assert re.search(r"""status\s*(?:===|!==)\s*['"]\w+['"]""", src), f.name


def test_learning_moved_from_status_to_phase():
    """★"is it running" and "running what" are different questions. Status
    collapses to `running`; the stage a member watches lives in `phase`, where
    every other tracker keeps its own."""
    src = (REPO / "frontend" / "composables" / "useConnectionSync.ts").read_text(encoding="utf-8")
    assert "state.value.phase === 'learning'" in src
    assert "s.phase === 'learning'" in src
    assert "'learning'" not in src.split("export type SyncStatus")[1].split("\n\n")[0]


def test_the_declared_types_are_the_canonical_set():
    for f in (FE[0], FE[1]):
        src = f.read_text(encoding="utf-8")
        for value in ("idle", "pending", "running", "completed", "partial", "failed", "cancelled"):
            assert f"'{value}'" in src, (f.name, value)
