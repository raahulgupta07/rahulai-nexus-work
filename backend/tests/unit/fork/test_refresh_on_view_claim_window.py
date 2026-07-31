"""Refresh-on-view's single-flight claim is keyed on the state, not the clock.

`refresh_on_view_rerun` is reachable by anonymous visitors on a public report,
so its two guards decide how much work arbitrary traffic can cause:

  1. the staleness gate — `age(last_run_at) >= REFRESH_ON_VIEW_MIN_INTERVAL_SECONDS`
  2. `claim_scheduled_run(f"report_view_{id}", ..., refresh_claim_epoch(last_run_at))`

Guard 1 is a sliding window and is the rate limit. Guard 2 exists for the case
guard 1 structurally cannot cover: N viewers arriving together all read the same
stale `last_run_at` *before* any rerun has committed a new one, so all N pass
guard 1 and the claim is what collapses the herd.

`claim_scheduled_run`'s default key is a wall-clock bucket:

    bucket = int(time.time() // window_seconds * window_seconds)

That is correct for cron — every worker fires at the same instant, and distinct
fires are >=60s apart — but wrong here, because these contenders arrive at
arbitrary times. Buckets are fixed intervals anchored to the epoch, not a
sliding window, so two viewers a second apart on opposite sides of a boundary
computed different keys and BOTH reran. The elapsed time between them was not
an input. The exposed interval was as wide as the rerun itself: a 60s rerun
means every arrival in the 60s before a boundary is still in flight when the
next bucket opens, so a second viewer then also claims — 20% of a 300s clock,
and the staleness gate cannot save them because the first rerun has not
committed `last_run_at` yet.

The fix keys the claim on that shared `last_run_at` instead. Agreeing on the
state is then the same as agreeing on the key. These tests pin both halves:
that the refresh path keys on the epoch, and that the default bucketing it no
longer uses is still the wall-clock formula the scheduler needs.

The bucket arithmetic is modelled here rather than exercised through a database;
`test_the_default_bucket_formula_is_still_wall_clock` is what keeps the model in
step with the shipped one. The claim SEMANTICS are exercised for real, against a
fake engine that reproduces the unique (job_id, bucket) constraint.
"""
import inspect
import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.core import scheduler as scheduler_module
from app.core.scheduler import claim_scheduled_run
from app.services.report_service import (
    REFRESH_ON_VIEW_MIN_INTERVAL_SECONDS,
    ReportService,
    refresh_claim_epoch,
)

W = REFRESH_ON_VIEW_MIN_INTERVAL_SECONDS


def bucket_at(t: float, window: int = W) -> int:
    """The DEFAULT claim key's time component, as `claim_scheduled_run`
    computes it when no explicit bucket is passed."""
    return int(t // window * window)


# ── a real arbiter, without a database ──────────────────────────────────────

class _FakeUniqueIndex:
    """Stands in for the unique (job_id, run_bucket) constraint.

    `claim_scheduled_run` fails OPEN on any exception other than IntegrityError,
    so a fake that raised the wrong type would make every claim look like a win
    and every assertion below pass vacuously.
    """

    def __init__(self):
        self.rows = set()

    # -- engine surface --
    def begin(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt, params=None):
        params = params or {}
        if "DELETE" in str(stmt):
            return None
        key = (params["jid"], params["bucket"])
        if key in self.rows:
            raise IntegrityError("INSERT", params, Exception("duplicate key"))
        self.rows.add(key)
        return None


@pytest.fixture
def arbiter(monkeypatch):
    fake = _FakeUniqueIndex()
    monkeypatch.setattr(scheduler_module, "_engine", fake)
    return fake


def test_the_fake_arbiter_reproduces_the_constraint(arbiter):
    """Guard the guard: if this fake ever stopped rejecting a repeat, every
    'only one wins' assertion below would pass without proving anything."""
    assert claim_scheduled_run("job", W, 111) is True
    assert claim_scheduled_run("job", W, 111) is False
    assert claim_scheduled_run("job", W, 112) is True
    assert claim_scheduled_run("other", W, 111) is True


# ── the fix: viewers who read the same state share one key ──────────────────

def test_two_viewers_reading_the_same_last_run_at_collapse_to_one_rerun(arbiter):
    stale = datetime(2026, 3, 1, 12, 0, 0)
    first = claim_scheduled_run("report_view_r1", W, refresh_claim_epoch(stale))
    second = claim_scheduled_run("report_view_r1", W, refresh_claim_epoch(stale))
    assert first is True
    assert second is False


def test_a_whole_crowd_reading_one_stale_row_yields_one_winner(arbiter):
    stale = datetime(2026, 3, 1, 12, 0, 0)
    wins = [
        claim_scheduled_run("report_view_r1", W, refresh_claim_epoch(stale))
        for _ in range(200)
    ]
    assert wins.count(True) == 1


def test_the_key_does_not_depend_on_when_the_viewer_arrived():
    """The property the wall-clock version could not provide. Arrival time is
    not an input at all, so no pair of arrivals can straddle anything."""
    stale = datetime(2026, 3, 1, 12, 0, 0)
    assert "time" not in inspect.signature(refresh_claim_epoch).parameters
    assert refresh_claim_epoch(stale) == refresh_claim_epoch(stale)


def test_viewers_straddling_a_wall_clock_boundary_still_collapse(arbiter):
    """The regression this fixes, stated in the terms it used to fail in: two
    arrivals seconds apart with a bucket boundary between them. Under the old
    keying these produced different buckets and both reran."""
    stale = datetime(2026, 3, 1, 12, 0, 0)
    boundary = float(W * 5)
    assert bucket_at(boundary - 1.0) != bucket_at(boundary + 1.0), (
        "arrivals chosen so the OLD wall-clock keying would have split them"
    )
    key = refresh_claim_epoch(stale)
    assert claim_scheduled_run("report_view_r1", W, key) is True
    assert claim_scheduled_run("report_view_r1", W, key) is False


def test_a_long_rerun_no_longer_widens_anything(arbiter):
    """A rerun in flight has not written `last_run_at`, so every viewer during
    it reads the same value and computes the same key — for any duration."""
    stale = datetime(2026, 3, 1, 12, 0, 0)
    key = refresh_claim_epoch(stale)
    assert claim_scheduled_run("report_view_r1", W, key) is True
    for _seconds_into_the_rerun in (5, 30, 60, 299):
        assert claim_scheduled_run("report_view_r1", W, key) is False


# ── it must still allow the NEXT genuine rerun ──────────────────────────────

def test_a_new_last_run_at_opens_a_new_claim(arbiter):
    """The claim must not become permanent. Once a rerun commits, the state has
    changed, so the next herd reads a different value and one of them wins."""
    first_epoch = refresh_claim_epoch(datetime(2026, 3, 1, 12, 0, 0))
    later_epoch = refresh_claim_epoch(datetime(2026, 3, 1, 12, 10, 0))
    assert claim_scheduled_run("report_view_r1", W, first_epoch) is True
    assert claim_scheduled_run("report_view_r1", W, first_epoch) is False
    assert claim_scheduled_run("report_view_r1", W, later_epoch) is True


def test_two_reports_never_share_a_claim(arbiter):
    """Reports last run at the same instant share an epoch; the job_id is what
    keeps them apart, so a busy instance cannot starve one report with another."""
    stale = datetime(2026, 3, 1, 12, 0, 0)
    key = refresh_claim_epoch(stale)
    assert claim_scheduled_run("report_view_r1", W, key) is True
    assert claim_scheduled_run("report_view_r2", W, key) is True


def test_a_report_that_has_never_run_gives_every_viewer_the_same_key(arbiter):
    assert refresh_claim_epoch(None) == 0
    assert claim_scheduled_run("report_view_new", W, refresh_claim_epoch(None)) is True
    assert claim_scheduled_run("report_view_new", W, refresh_claim_epoch(None)) is False


# ── the epoch itself ────────────────────────────────────────────────────────

def test_last_run_at_is_read_as_utc_not_local_time():
    """`last_run_at` is written by `datetime.utcnow()`, i.e. naive UTC. Calling
    `.timestamp()` on it bare would interpret it as LOCAL time, shifting the key
    by the host's offset — two workers in different zones would then disagree
    about the same row and both rerun."""
    naive_utc = datetime(2026, 3, 1, 12, 0, 0)
    expected = int(naive_utc.replace(tzinfo=timezone.utc).timestamp())
    assert refresh_claim_epoch(naive_utc) == expected


def test_the_epoch_moves_with_the_row_second_by_second():
    base = datetime(2026, 3, 1, 12, 0, 0)
    assert refresh_claim_epoch(base + timedelta(seconds=1)) - refresh_claim_epoch(base) == 1


def test_sub_second_jitter_cannot_split_readers_of_one_row():
    """Truncation is deliberate: a bucket must be an integer for the claim
    table, and every reader of the row sees the same microseconds anyway."""
    base = datetime(2026, 3, 1, 12, 0, 0)
    assert refresh_claim_epoch(base.replace(microsecond=1)) == refresh_claim_epoch(base)


# ── the wiring, and the default the scheduler still relies on ───────────────

def test_the_refresh_path_passes_the_epoch_and_not_the_wall_clock():
    """A behavioural test cannot reach `refresh_on_view_rerun` without a
    database, and the defect was entirely in WHICH key it handed over — so
    check the call itself."""
    # Parse, don't grep: the method's own docstring names `claim_scheduled_run`
    # while explaining the guard, so a text search finds the prose before the
    # call and reports on the wrong thing.
    import ast
    import textwrap

    src = textwrap.dedent(inspect.getsource(ReportService.refresh_on_view_rerun))
    calls = [
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
        and any(
            isinstance(a, ast.Name) and a.id == "claim_scheduled_run"
            for a in node.args
        )
    ]
    assert len(calls) == 1, f"expected one dispatch of claim_scheduled_run, found {len(calls)}"
    passed = [ast.unparse(a) for a in calls[0].args]
    assert "claim_epoch" in passed, (
        "the refresh-on-view claim is no longer keyed on the staleness epoch; "
        "wall-clock bucketing lets viewers either side of a boundary both rerun. "
        f"passed: {passed}"
    )
    assert "refresh_claim_epoch(report.last_run_at)" in src, (
        "claim_epoch no longer comes from the report's own last_run_at"
    )


def test_the_default_bucket_formula_is_still_wall_clock():
    """Fixed buckets remain correct for cron, which is why the fix is a
    per-caller override rather than a change to the shared helper. If this
    formula ever changes, `bucket_at` above is wrong."""
    src = inspect.getsource(claim_scheduled_run)
    assert re.search(
        r"bucket\s*=\s*int\(\s*time\.time\(\)\s*//\s*window_seconds\s*\*\s*window_seconds\s*\)",
        src,
    ), "claim_scheduled_run no longer buckets on int(time.time() // w * w)"


def test_scheduler_callers_are_unaffected(arbiter, monkeypatch):
    """The override is opt-in. With no bucket passed, two claims inside one
    wall-clock window must still collapse exactly as they did before."""
    monkeypatch.setattr(scheduler_module.time, "time", lambda: float(W * 4) + 1.0)
    assert claim_scheduled_run("report_42", W) is True
    assert claim_scheduled_run("report_42", W) is False


def test_the_refresh_interval_is_a_module_constant_not_a_column():
    """The rate limit holds only while the interval is a constant — if it ever
    became an owner-settable field, a report could ask to be rerun on every
    page view."""
    assert isinstance(REFRESH_ON_VIEW_MIN_INTERVAL_SECONDS, int)
    assert REFRESH_ON_VIEW_MIN_INTERVAL_SECONDS > 0

    from app.models.report import Report

    columns = {c.name for c in Report.__table__.columns}
    assert "refresh_on_view" in columns, "the opt-in flag should still be a column"
    assert not any("interval" in c or "refresh_seconds" in c for c in columns), (
        "a per-report refresh interval column would defeat the rate limit"
    )
