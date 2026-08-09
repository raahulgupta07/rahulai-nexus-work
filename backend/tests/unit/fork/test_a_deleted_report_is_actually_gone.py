"""A deleted report stayed readable, and a refused id announced that it existed.

Two LOW-severity findings from the 2026-08-09 tenancy sweep, both closed here.

★★★The soft-delete one turned on a word. `DELETE /reports/{id}` is called
`delete_report`, logs `report.deleted`, and calls `archive_report`, which sets
`status = 'archived'` and never touches `deleted_at`. Every LIST query in
`report_service` already carries `Report.status != 'archived'`, so the report
disappeared from the UI — while `GET /reports/{id}` returned it in full, and a
`PUT` carrying `status: "draft"` brought it back into everyone's list. There is
no un-archive route and no UI for one.

So the guard has to check BOTH columns. A first pass added only
`deleted_at.is_(None)` and the probe still failed 5/5 — filtering the column the
product does not use looks exactly like filtering the one it does.

★The enumeration one: `owner_only` answered 403 for a real id belonging to
someone else, and 404 for an id that does not exist. Walk a range and the pair
of status codes maps the install. Both now answer 404 with the same body.

★Red-proof against a `git worktree` at HEAD, 2026-08-09: 5 failed, 0 passed.
Live probe before → after: 218 probes, 10 failed → **0 failed**.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
BACKEND = REPO / "backend"


def _src(rel: str) -> str:
    return (BACKEND / rel).read_text()


def _slice(text: str, start_marker: str, length: int = 5000) -> str:
    # ★5000, not 3000: `get_report`'s eager-loading `options(...)` block plus the
    # comments explaining these two filters run past 3000 characters, so the
    # shorter window cut the `status != 'archived'` assertion off and the guard
    # failed against the CORRECT file. A window that ends mid-function measures
    # the window, not the code.
    i = text.index(start_marker)
    return text[i:i + length]


def test_reading_a_report_excludes_both_deleted_columns():
    body = _slice(_src("app/services/report_service.py"),
                  "async def get_report(self")
    assert "Report.deleted_at.is_(None)" in body
    assert "Report.status != 'archived'" in body


def test_writing_a_report_excludes_both_deleted_columns():
    """★The write is the one that mattered most: without it a caller holding a
    deleted id could set `status` back to `draft` and resurrect the report for
    the whole organization."""
    body = _slice(_src("app/services/report_service.py"),
                  "async def update_report(self")
    assert "Report.deleted_at.is_(None)" in body
    assert "Report.status != 'archived'" in body


def test_the_write_is_also_scoped_to_the_organization():
    """`update_report` resolved its id against every org in the install and
    relied entirely on the route decorator. A service reachable from anywhere
    has to be safe on its own terms."""
    body = _slice(_src("app/services/report_service.py"),
                  "async def update_report(self")
    assert "Report.organization_id == organization.id" in body


def test_the_shared_visibility_helper_hides_deleted_reports_in_both_modes():
    """`report_access` has three queries — permissive mode, strict mode, and the
    id list. All three, or a report is gone from one surface and not another."""
    src = _src("app/core/report_access.py")
    assert src.count("Report.deleted_at.is_(None)") == 3
    assert src.count("Report.status != 'archived'") == 3


def test_a_forbidden_id_answers_like_an_unknown_one():
    """★The check that makes this non-vacuous is the ABSENCE assertion: the old
    string must be gone. Asserting only that a 404 exists would pass on the
    buggy file, which raised 404 for unknown ids all along."""
    src = _src("app/core/permissions_decorator.py")
    assert "Only the owner can perform this action" not in src
    assert src.count('status_code=404, detail="Object not found or access denied"') >= 4
