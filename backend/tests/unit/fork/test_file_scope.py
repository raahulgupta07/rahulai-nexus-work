"""The one resolver: membership is the same for every purpose.

`file_scope.readable_files` replaced five independent answers to "which files
can this run read?". The tests below pin the two things that made those five
disagree — a pool one of them forgot, and a filter another one skipped.
"""

import pytest

from app.services.file_scope import (
    PURPOSE_CATALOG,
    PURPOSE_CODEGEN,
    PURPOSE_READ,
    PURPOSES,
    readable_files,
    readable_files_from_ctx,
)


class FakeFile:
    def __init__(self, fid, filename, is_agent_readable=True):
        self.id = fid
        self.filename = filename
        self.path = f"/files/{filename}"
        self.is_agent_readable = is_agent_readable


class FakeDS:
    def __init__(self, files):
        self.files = list(files)


class FakeReport:
    def __init__(self, files, data_sources=()):
        self.files = list(files)
        self.data_sources = list(data_sources)


UPLOAD = FakeFile("f-upload", "sales.csv")
PROJECT = FakeFile("f-project", "MM_Conso_H1_2025.csv")
LIVE = FakeFile("f-mcp", "work_orders.json")


def _ids(files):
    return [f.id for f in files]


# ── membership ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("purpose", PURPOSES)
def test_every_purpose_sees_the_same_files(purpose):
    """The whole point of the module: purpose changes order, never membership."""
    files = readable_files(
        report=FakeReport([UPLOAD]),
        project_files=[PROJECT],
        excel_files=[LIVE],
        purpose=purpose,
    )

    assert set(_ids(files)) == {"f-upload", "f-project", "f-mcp"}


def test_a_project_file_is_in_the_pool():
    """The reported bug. Project membership lives in `project_file_association`,
    so a project file is never in `report.files` and four of the five old
    resolvers could not see it."""
    files = readable_files(report=FakeReport([]), project_files=[PROJECT])

    assert _ids(files) == ["f-project"]


def test_a_mid_turn_file_is_in_the_pool():
    """execute_mcp appends its artifact to excel_files and hands back the id."""
    files = readable_files(report=FakeReport([]), excel_files=[LIVE])

    assert _ids(files) == ["f-mcp"]


def test_an_empty_run_is_empty_not_an_error():
    assert readable_files(report=None) == []


# ── ordering ─────────────────────────────────────────────────────────────────


def test_codegen_puts_the_live_list_first():
    """Generated code indexes `excel_files` positionally, and an index only
    means anything against the list the code generator saw."""
    files = readable_files(
        report=FakeReport([UPLOAD]),
        project_files=[PROJECT],
        excel_files=[LIVE],
        purpose=PURPOSE_CODEGEN,
    )

    assert _ids(files)[0] == "f-mcp"


def test_reading_surfaces_lead_with_the_report():
    for purpose in (PURPOSE_CATALOG, PURPOSE_READ):
        files = readable_files(
            report=FakeReport([UPLOAD]),
            project_files=[PROJECT],
            excel_files=[LIVE],
            purpose=purpose,
        )
        assert _ids(files)[0] == "f-upload", purpose


# ── dedupe ───────────────────────────────────────────────────────────────────


def test_a_file_in_two_pools_appears_once():
    files = readable_files(
        report=FakeReport([UPLOAD]), project_files=[UPLOAD], excel_files=[UPLOAD]
    )

    assert _ids(files) == ["f-upload"]


def test_a_file_listed_twice_in_one_pool_appears_once():
    """Report attachments are append-only with no dedup — a working report
    reaches nineteen rows for six files. The resolver this replaced built its
    `seen` set once and never added to it, so those duplicates came through."""
    files = readable_files(report=FakeReport([UPLOAD, UPLOAD]))

    assert _ids(files) == ["f-upload"]


# ── filters ──────────────────────────────────────────────────────────────────


def test_a_table_backed_file_is_dropped():
    """Its data is already queryable; reading both risks a stale second copy."""
    backed = FakeFile("f-table", "orders.csv", is_agent_readable=False)

    files = readable_files(report=FakeReport([UPLOAD, backed]))

    assert _ids(files) == ["f-upload"]


def test_upload_focus_drops_an_agents_snapshot_when_the_user_attached_something():
    snapshot = FakeFile("f-snap", "Definitions.xlsx")
    report = FakeReport([UPLOAD, snapshot], data_sources=[FakeDS([snapshot])])

    files = readable_files(report=report)

    assert _ids(files) == ["f-upload"]


def test_upload_focus_keeps_the_snapshot_when_there_is_no_upload():
    """Fail open: focus narrows, it never empties the pool."""
    snapshot = FakeFile("f-snap", "Definitions.xlsx")
    report = FakeReport([snapshot], data_sources=[FakeDS([snapshot])])

    files = readable_files(report=report)

    assert _ids(files) == ["f-snap"]


def test_upload_focus_can_be_turned_off():
    snapshot = FakeFile("f-snap", "Definitions.xlsx")
    report = FakeReport([UPLOAD, snapshot], data_sources=[FakeDS([snapshot])])

    files = readable_files(report=report, upload_focus=False)

    assert set(_ids(files)) == {"f-upload", "f-snap"}


# ── the runtime_ctx wrapper ──────────────────────────────────────────────────


def test_from_ctx_reads_the_project_pool_under_its_own_key():
    """The agent loop stages project files as `project_files`, NOT merged into
    `excel_files`. Every tool that forgot to look there is why this exists."""
    ctx = {
        "report": FakeReport([UPLOAD]),
        "project_files": [PROJECT],
        "excel_files": [LIVE],
    }

    assert set(_ids(readable_files_from_ctx(ctx))) == {
        "f-upload",
        "f-project",
        "f-mcp",
    }


def test_from_ctx_tolerates_a_bare_context():
    assert readable_files_from_ctx({}) == []


def test_an_unknown_purpose_is_rejected():
    """Silently treating a typo as `read` would hand generated code the wrong
    ordering, which is the positional-binding failure all over again."""
    with pytest.raises(ValueError):
        readable_files(report=FakeReport([UPLOAD]), purpose="codgen")
