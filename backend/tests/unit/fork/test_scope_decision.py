"""What a turn reads: the file you attached, else the folder, else the agents.

The folder rung did not exist. A report inside a project had its folder's files
rendered into the model's catalog and into no readable pool, so a question about
the folder was answered from whatever databases happened to be bound.

`test_the_stock_report_reads_its_folder_not_its_databases` is that exact report,
read out of a stock bagofwords database rather than imagined: zero files on the
report, seven in the folder, two Postgres sources, and an answer about sales.
"""

import pytest

from app.services.file_scope import (
    SCOPE_AGENTS,
    SCOPE_ALL,
    SCOPE_ATTACHED,
    SCOPE_FOLDER,
    SCOPE_UPLOADS,
    ScopeDecision,
    decide_scope,
    scope_notice,
)


class FakeFile:
    def __init__(self, fid, filename):
        self.id = fid
        self.filename = filename
        self.path = f"/files/{filename}"
        self.is_agent_readable = True


class FakeDS:
    def __init__(self, name, files=()):
        self.name = name
        self.files = list(files)


class FakeReport:
    def __init__(self, files, data_sources=()):
        self.files = list(files)
        self.data_sources = list(data_sources)


# The real project contents of stock report 888a049f.
FOLDER_FILES = [
    FakeFile("p-doc", "2026-06-29 CRM Agent Q&A , Logic.docx"),
    FakeFile("p-jan", "MM Conso Data Report (Jan'25).csv"),
    FakeFile("p-feb", "MM Conso Data Report (Feb'25).csv"),
    FakeFile("p-mar", "MM Conso Data Report (Mar'25).csv"),
    FakeFile("p-apr", "MM Conso Data Report (Apr'25).csv"),
    FakeFile("p-may", "MM Conso Data Report (May'25).csv"),
    FakeFile("p-jun", "MM Conso Data Report (Jun'25).csv"),
]
DATABASES = [FakeDS("Sales DB"), FakeDS("HR DB")]
ATTACHED = FakeFile("a-1", "this_months_numbers.csv")


def _ids(scope):
    return [f.id for f in scope.files]


# ── the precedence table ─────────────────────────────────────────────────────


def test_the_stock_report_reads_its_folder_not_its_databases():
    """Report 888a049f, project "test": 0 attached, 7 in the folder, 2 sources."""
    scope = decide_scope(
        report=FakeReport([], data_sources=DATABASES),
        project_files=FOLDER_FILES,
        data_sources=DATABASES,
        project_name="test",
    )

    assert scope.kind == SCOPE_FOLDER
    assert len(scope.files) == 7
    assert scope.label == 'Reading: folder "test" · 7 files'


def test_an_attached_file_beats_the_folder():
    """You attached something to this message; that is what you meant."""
    scope = decide_scope(
        report=FakeReport([ATTACHED], data_sources=DATABASES),
        project_files=FOLDER_FILES,
        attached_file_ids=["a-1"],
        data_sources=DATABASES,
        project_name="test",
    )

    assert scope.kind == SCOPE_ATTACHED
    assert _ids(scope) == ["a-1"]
    # ★And the label admits the folder is still open to it. This read
    # "Reading: 1 attached file" while the tools could open all eight.
    assert scope.label == (
        'Reading: this_months_numbers.csv, plus 7 files in folder "test"'
    )


def test_no_files_anywhere_reads_the_agents():
    """Not a fallback — with no files the bound agents ARE the material."""
    scope = decide_scope(report=FakeReport([], data_sources=DATABASES), data_sources=DATABASES)

    assert scope.kind == SCOPE_AGENTS
    assert scope.files == []
    assert scope.label == "Reading: connected data"


def test_a_report_outside_any_folder_is_unchanged():
    """The no-folder, no-attachment path must behave exactly as it does today."""
    scope = decide_scope(
        report=FakeReport([], data_sources=DATABASES),
        project_files=[],
        data_sources=DATABASES,
    )

    assert scope.kind == SCOPE_AGENTS


# ── the user's own choice ────────────────────────────────────────────────────


def test_choosing_connected_data_gives_back_the_databases():
    """The chip has to be able to undo the new default, or it is a trap."""
    scope = decide_scope(
        report=FakeReport([], data_sources=DATABASES),
        project_files=FOLDER_FILES,
        data_sources=DATABASES,
        project_name="test",
        override=SCOPE_AGENTS,
    )

    assert scope.kind == SCOPE_AGENTS


def test_choosing_everything_keeps_both():
    scope = decide_scope(
        report=FakeReport([ATTACHED], data_sources=DATABASES),
        project_files=FOLDER_FILES,
        attached_file_ids=["a-1"],
        data_sources=DATABASES,
        override=SCOPE_ALL,
    )

    assert scope.kind == SCOPE_ALL
    assert len(scope.files) == 8


def test_choosing_the_folder_over_an_attachment():
    scope = decide_scope(
        report=FakeReport([ATTACHED], data_sources=DATABASES),
        project_files=FOLDER_FILES,
        attached_file_ids=["a-1"],
        data_sources=DATABASES,
        project_name="test",
        override=SCOPE_FOLDER,
    )

    assert scope.kind == SCOPE_FOLDER
    assert len(scope.files) == 7


def test_choosing_an_empty_scope_falls_through_rather_than_reading_nothing():
    """Asking for attachments when nothing is attached must not produce a turn
    with no material at all — it drops to the next rung and says so."""
    scope = decide_scope(
        report=FakeReport([], data_sources=DATABASES),
        project_files=FOLDER_FILES,
        data_sources=DATABASES,
        project_name="test",
        override=SCOPE_ATTACHED,
    )

    assert scope.kind == SCOPE_FOLDER


# ── labels and reporting ─────────────────────────────────────────────────────


def test_a_single_attachment_is_named_not_counted():
    """"1 attached file" makes the reader go and check which one."""
    scope = decide_scope(
        report=FakeReport([ATTACHED]), attached_file_ids=["a-1"]
    )

    assert scope.label == "Reading: this_months_numbers.csv"


def test_several_attachments_are_counted_not_listed():
    """A footer is one line; six filenames is not."""
    more = [FakeFile(f"a-{i}", f"file{i}.csv") for i in range(2, 5)]
    scope = decide_scope(
        report=FakeReport([ATTACHED] + more),
        attached_file_ids=["a-1", "a-2", "a-3", "a-4"],
    )

    assert scope.label == "Reading: 4 attached files"


def test_an_unnamed_folder_still_reads_naturally():
    scope = decide_scope(report=FakeReport([]), project_files=FOLDER_FILES)

    assert scope.label == "Reading: this folder · 7 files"


def test_the_decision_serialises_for_the_completion_record():
    scope = decide_scope(
        report=FakeReport([]), project_files=FOLDER_FILES, project_name="test"
    )

    assert scope.as_dict() == {
        "kind": "folder",
        "label": 'Reading: folder "test" · 7 files',
        "file_count": 7,
    }


# ── guard rails ──────────────────────────────────────────────────────────────


def test_an_attached_id_that_is_not_in_the_pool_is_ignored():
    """A stale or cross-report id must not conjure a file into the scope."""
    scope = decide_scope(
        report=FakeReport([], data_sources=DATABASES),
        project_files=FOLDER_FILES,
        attached_file_ids=["a-nope"],
        data_sources=DATABASES,
        project_name="test",
    )

    assert scope.kind == SCOPE_FOLDER


def test_a_report_reads_its_own_uploads_on_the_turns_after_the_upload():
    """★The case the deleted `__init__` suppression used to cover.

    Turn one attaches a CSV and the attached rung fires. Turn two asks a
    follow-up with nothing attached and no folder — and before this rung
    existed, that turn fell straight through to the databases while the user
    was plainly still talking about their file."""
    scope = decide_scope(
        report=FakeReport([ATTACHED], data_sources=DATABASES),
        data_sources=DATABASES,
    )

    assert scope.kind == SCOPE_UPLOADS
    assert _ids(scope) == ["a-1"]
    assert scope.label == "Reading: 1 uploaded file"


def test_a_file_belonging_to_a_bound_agent_is_not_an_upload():
    """A CSV agent's own source file is part of its tables. Counting it as an
    upload would put every connected agent permanently in file scope."""
    owned = FakeFile("ds-1", "orders.csv")
    scope = decide_scope(
        report=FakeReport([owned], data_sources=[FakeDS("CSV Agent", [owned])]),
        data_sources=[FakeDS("CSV Agent", [owned])],
    )

    assert scope.kind == SCOPE_AGENTS


# ── one owner: the record must match what is reachable ───────────────────────


def test_the_decision_never_claims_a_source_is_unreachable():
    """★The invariant. A scope used to carry `suppress_schemas`, meaning "the
    bound sources are gone for this turn" — and they were not, because 503's
    focus-follow-use rebuilt them inside the planner loop after this ran. A
    record that disagrees with reality is worse than no record.

    Scope decides the SUBJECT. Reachability has exactly one owner, and it is
    not this."""
    for kind in (SCOPE_ATTACHED, SCOPE_FOLDER, SCOPE_UPLOADS, SCOPE_AGENTS, SCOPE_ALL):
        scope = ScopeDecision(kind, [], "x")
        assert "suppress" not in scope.as_dict(), (
            f"{kind}: scope is describing what a run cannot reach again"
        )
    assert not hasattr(ScopeDecision("x", [], "y"), "suppress_schemas")


def test_the_label_accounts_for_every_file_the_turn_can_open():
    """★The invariant's twin. Whatever the rung, the numbers printed in the
    label must add up to the pool — so a file can never be readable and
    unmentioned.

    The mixed case is the one that was wrong in production: an attachment on a
    report inside a folder said "1 attached file" and read eight."""
    import re

    cases = [
        ("attachment inside a folder", dict(
            report=FakeReport([ATTACHED], data_sources=DATABASES),
            project_files=FOLDER_FILES,
            attached_file_ids=["a-1"],
            data_sources=DATABASES,
            project_name="test",
        ), 8),
        ("folder alone", dict(
            report=FakeReport([], data_sources=DATABASES),
            project_files=FOLDER_FILES,
            data_sources=DATABASES,
            project_name="test",
        ), 7),
        ("uploads alone", dict(
            report=FakeReport([ATTACHED], data_sources=DATABASES),
            data_sources=DATABASES,
        ), 1),
    ]

    for name, kwargs, reachable in cases:
        scope = decide_scope(**kwargs)
        # The label either names a file or counts it. Both forms count here.
        named = sum(1 for f in scope.files if f.filename in scope.label)
        stated = named + sum(int(n) for n in re.findall(r"\b(\d+)\b", scope.label))
        assert stated == reachable, (
            f"{name}: label {scope.label!r} accounts for {stated} of "
            f"{reachable} readable files"
        )


def test_the_planner_is_told_the_subject_not_that_the_databases_are_gone():
    """Naming the files must not read as "you have nothing else"."""
    scope = decide_scope(
        report=FakeReport([], data_sources=DATABASES),
        project_files=FOLDER_FILES,
        data_sources=DATABASES,
        project_name="test",
    )
    notice = scope_notice(scope)

    assert "SUBJECT OF THIS QUESTION" in notice
    assert "this folder" in notice
    assert "MM Conso Data Report (Jan'25).csv" in notice
    assert "Connected data sources remain available" in notice


def test_the_notice_names_the_files_and_counts_the_rest():
    """Eight names is enough to recognise the set; a silent cut is not."""
    scope = decide_scope(
        report=FakeReport([], data_sources=DATABASES),
        project_files=FOLDER_FILES + [FakeFile(f"x{i}", f"x{i}.csv") for i in range(4)],
        data_sources=DATABASES,
    )
    notice = scope_notice(scope)

    assert "and 3 more" in notice


def test_reading_the_agents_says_nothing_extra():
    """No file subject, no notice. A sentence on every turn is noise."""
    assert scope_notice(decide_scope(report=FakeReport([]))) == ""
    assert scope_notice(None) == ""


def test_a_table_backed_folder_file_does_not_count_as_folder_content():
    """The pool filters it out, so it must not hold the folder rung open."""
    backed = FakeFile("p-backed", "orders.csv")
    backed.is_agent_readable = False

    scope = decide_scope(
        report=FakeReport([], data_sources=DATABASES),
        project_files=[backed],
        data_sources=DATABASES,
    )

    assert scope.kind == SCOPE_AGENTS
