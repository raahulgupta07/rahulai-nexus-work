"""Which files can `source_file_ids` actually reach?

`inspect_data`, `create_data` and `write_csv` all bind their inputs through
`resolve_source_files`. The model picks those ids out of the `<files>` context
section — so every kind of file that section renders has to be resolvable here,
or the model asks for a file it was just shown and is told it does not exist.

This test enumerates the provenances a report can hold and asserts each one
resolves. It exists because one of them did not: a file inherited from the
report's PROJECT is rendered into `<files>` with its real id
(files_context_builder.build), is reachable from read_file / grep_files
(_file_tool_common.resolve_session_file, which reads runtime_ctx["project_files"]),
and was reachable from none of the three data tools — `_candidates()` looked at
`excel_files` and `report.files` only.

The visible failure was three retries in a row on a single analysis:
"the CSV file IDs weren't resolved", "create_data couldn't resolve those file
ids", "create_data failed without a file source". Each retry is a fresh LLM
codegen round, so the cost of the missing lookup was minutes, not milliseconds.
"""

from app.ai.tools.implementations._source_files import resolve_source_files


class FakeFile:
    """The attribute surface `_source_files` reads off a File row."""

    def __init__(self, fid, filename, path=None):
        self.id = fid
        self.filename = filename
        self.path = path or f"/files/{filename}"


class FakeReport:
    def __init__(self, files):
        self.files = files


def _ctx(report_files=(), excel_files=(), project_files=()):
    return {
        "report": FakeReport(list(report_files)),
        "excel_files": list(excel_files),
        "project_files": list(project_files),
    }


def _resolve(ctx, fid):
    scoped, directive, missing = resolve_source_files(ctx, [fid])
    return scoped, directive, missing


# ── the provenances the <files> section renders ──────────────────────────────


def test_a_plain_upload_resolves():
    """origin="upload" — the file the user attached to this report.

    The ordinary single-file flow. It reaches the tools through both
    `excel_files` (agent_v2 builds analysis_files from report.files) and the
    report.files fallback, so it has never been affected.
    """
    f = FakeFile("f-upload", "sales.csv")
    ctx = _ctx(report_files=[f], excel_files=[f])

    scoped, directive, missing = _resolve(ctx, "f-upload")

    assert missing == []
    assert [x.id for x in scoped] == ["f-upload"]
    assert "pd.read_csv(excel_files[0].path)" in directive


def test_an_upload_missing_from_excel_files_still_resolves():
    """The report.files fallback: a caller that never populated excel_files.

    This is what makes `is_agent_readable` / upload-focus filtering safe — those
    narrow `analysis_files`, and the fallback restores the file here.
    """
    f = FakeFile("f-upload", "sales.csv")
    ctx = _ctx(report_files=[f], excel_files=[])

    scoped, directive, missing = _resolve(ctx, "f-upload")

    assert missing == []
    assert [x.id for x in scoped] == ["f-upload"]


def test_a_file_materialized_mid_turn_resolves():
    """execute_mcp appends its artifact to excel_files and hands back the id.

    It is never in report.files, which is exactly why `_candidates` reads the
    live list first.
    """
    f = FakeFile("f-mcp", "work_orders.json")
    ctx = _ctx(report_files=[], excel_files=[f])

    scoped, directive, missing = _resolve(ctx, "f-mcp")

    assert missing == []
    assert [x.id for x in scoped] == ["f-mcp"]


def test_a_project_file_resolves():
    """origin="project" — inherited live from the report's project folder.

    Rendered into <files> with its real id by files_context_builder (it appends
    project_service.get_project_files_for_report to the catalog), and staged in
    runtime_ctx as "project_files" by the agent loop. It is NOT in report.files:
    project membership lives in `project_file_association`, a different table
    from `report_file_association`.

    This is the reported bug. Before the fix `_candidates()` never looked at
    `project_files`, so this id came back as missing and the tool hard-failed
    with "None of the requested source files exist".
    """
    f = FakeFile("f-project", "MM_Conso_H1_2025.csv")
    ctx = _ctx(report_files=[], excel_files=[], project_files=[f])

    scoped, directive, missing = _resolve(ctx, "f-project")

    assert missing == [], (
        "a project-inherited file is shown to the model with this id but "
        "cannot be resolved by inspect_data / create_data / write_csv"
    )
    assert [x.id for x in scoped] == ["f-project"]
    assert "pd.read_csv(excel_files[0].path)" in directive


def test_a_project_file_and_an_upload_together_keep_caller_order():
    """The real shape of the failing run: uploads plus project CSVs.

    Order matters — the directive numbers `excel_files[i]` by the order the
    caller listed the ids, and the generated code indexes into that.
    """
    up = FakeFile("f-upload", "H1_2025_Overview.csv")
    proj = FakeFile("f-project", "MM_Conso_H1_2025.csv")
    ctx = _ctx(report_files=[up], excel_files=[up], project_files=[proj])

    scoped, directive, missing = resolve_source_files(
        ctx, ["f-project", "f-upload"]
    )

    assert missing == []
    assert [x.id for x in scoped] == ["f-project", "f-upload"]
    assert "excel_files[0]: MM_Conso_H1_2025.csv" in directive
    assert "excel_files[1]: H1_2025_Overview.csv" in directive


def test_a_project_docx_is_named_unreadable_rather_than_missing():
    """The docx half of the same run.

    `_NOT_LOADABLE` covers docx — the directive is supposed to say "use
    read_file". But that line is only produced for a file that RESOLVES, so
    while project files were invisible the model got "does not exist" instead
    and went hunting for another path.
    """
    f = FakeFile("f-doc", "CRM Agent Q&A Logic.docx")
    ctx = _ctx(project_files=[f])

    scoped, directive, missing = _resolve(ctx, "f-doc")

    assert missing == []
    assert "NOT readable from generated code" in directive
    assert "read_file(file_id='f-doc')" in directive


# ── the guard rails the fix must not break ───────────────────────────────────


def test_an_unknown_id_is_still_reported_missing():
    """A genuinely absent file must keep failing loudly.

    Widening the candidate pool must not turn a wrong id into a silent
    substitution — that is the failure mode `_source_files` was written to stop.
    """
    ctx = _ctx(report_files=[FakeFile("f-upload", "sales.csv")])

    scoped, directive, missing = _resolve(ctx, "f-nope")

    assert missing == ["f-nope"]
    assert scoped == []


def test_a_file_present_in_two_pools_is_listed_once():
    """A project file can also be attached to the report.

    Deduping is by id and the first pool wins, so the index the directive
    hands the coder stays stable.
    """
    f = FakeFile("f-both", "shared.csv")
    ctx = _ctx(report_files=[f], excel_files=[f], project_files=[f])

    scoped, directive, missing = _resolve(ctx, "f-both")

    assert missing == []
    assert len(scoped) == 1
    assert directive.count("excel_files[0]:") == 1
