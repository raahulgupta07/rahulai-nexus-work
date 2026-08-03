"""A file the model is shown must have a tool that can open it.

`app/services/file_scope.py` exists because five call sites used to answer
"which files can this run read?" independently, the catalog was the most
permissive of them, and every disagreement became a file the model was told
about and no tool could reach — three wasted code-generation rounds each time.

There was a **sixth** answerer that module never covered: the capability gate in
`Agent.__init__` that decides whether `read_file` and `grep_files` appear in the
tool catalog at all. It read `report.files`. Project-inherited files live in
`project_file_association`, a different table, and `readable_files` documents
that they are *never* in `report.files` — so a report whose files all came from
its folder looked file-less to the gate, lost both reader tools, and went on
advertising those files in `<files>`.

★Read-only, no schema — `tests/unit/fork`. See CLAUDE.md.
"""
from app.ai.agent_v2 import capabilities_for_report_files, report_may_have_files

READER_TOOLS = {"read_file", "grep_files"}


class _Report:
    """Only the two attributes the gate is allowed to consult. Anything else it
    starts reading would be a query in a synchronous constructor."""

    def __init__(self, files=None, project_id=None):
        self.files = files or []
        self.project_id = project_id


def test_a_report_with_its_own_attached_files_gets_the_readers():
    assert report_may_have_files(_Report(files=[object()]))
    assert capabilities_for_report_files(True) == READER_TOOLS


def test_a_report_in_a_folder_gets_the_readers_even_with_no_files_of_its_own():
    """★The defect. `report.files` is empty and the folder's files are still
    rendered in `<files>` — dropping the readers here leaves the model looking
    at a file id it has no tool to open."""
    assert report_may_have_files(_Report(files=[], project_id="proj-1")), (
        "an inherited-files-only report is being treated as file-less — "
        "read_file will not be in its catalog"
    )


def test_a_sql_only_agent_still_gets_no_file_tools():
    """The gate's original purpose, unchanged: an agent with no files and no
    folder should not carry readers that can never resolve."""
    assert not report_may_have_files(_Report(files=[], project_id=None))
    assert capabilities_for_report_files(False) == set()


def test_the_gate_never_reads_report_files_alone():
    """★A guard on the shape, not just the outcome. `bool(report.files)` is the
    exact expression that caused this, and it is an easy thing to restore while
    "simplifying"."""
    import inspect

    src = inspect.getsource(report_may_have_files)
    assert "project_id" in src, (
        "the gate stopped consulting the folder — inherited files are invisible "
        "to it again"
    )


def test_project_files_are_documented_as_living_outside_report_files():
    """The premise this whole file rests on, asserted rather than trusted: if
    `readable_files` ever folds the project pool into `report.files`, the gate
    could go back to the simple expression and this test should be what says so.
    """
    from app.services import file_scope

    doc = file_scope.readable_files.__doc__ or ""
    assert "NEVER in ``report.files``" in doc
