"""The invariant: nothing is shown to the model that a tool cannot reach.

Five surfaces used to answer "which files can this run read?" independently —
the `<files>` catalog, `agent_v2.analysis_files`, `read_file`'s session
resolver, `grep_files`, and `_source_files._candidates`. The catalog was the
most permissive of the five, and the catalog is what the MODEL reads. So every
disagreement was a file the model was told about and a tool could not open.

It fired twice before anyone noticed the shape:

* a project-inherited file resolved in `read_file` and in nothing else, so
  `inspect_data` / `create_data` / `write_csv` returned "None of the requested
  source files exist" for an id the model had just read out of its own context,
* `grep_files` had the same blindness and produced no error at all, only fewer
  matches, so it went unreported.

These tests fail if a sixth pool is ever added to one call site and not the
others. That is the whole point of them — the one-line fix works until the next
pool arrives, and this is what makes it last.
"""

from app.ai.tools.implementations._file_tool_common import resolve_session_file
from app.ai.tools.implementations._source_files import _candidates
from app.services.file_scope import PURPOSE_CATALOG, readable_files


class FakeFile:
    def __init__(self, fid, filename, is_agent_readable=True):
        self.id = fid
        self.filename = filename
        self.path = f"/files/{filename}"
        self.content_type = "text/csv"
        self.organization_id = "org-1"
        self.is_agent_readable = is_agent_readable

    def prompt_schema(self):
        return f"{self.filename}: a,b,c"


class FakeOrg:
    id = "org-1"


class FakeReport:
    def __init__(self, files, data_sources=()):
        self.id = "rep-1"
        self.files = list(files)
        self.data_sources = list(data_sources)
        self.project_id = "proj-1"


# One file of every provenance a report can hold.
ATTACHED = FakeFile("f-attached", "sales.csv")
PROJECT = FakeFile("f-project", "MM_Conso_H1_2025.csv")
MID_TURN = FakeFile("f-midturn", "work_orders.json")


def _ctx():
    return {
        "report": FakeReport([ATTACHED]),
        "organization": FakeOrg(),
        "project_files": [PROJECT],
        "excel_files": [MID_TURN],
    }


def _catalog_ids(ctx):
    """What the model is shown, via the same call the context builder makes."""
    return {
        str(f.id)
        for f in readable_files(
            report=ctx["report"],
            project_files=ctx["project_files"],
            purpose=PURPOSE_CATALOG,
        )
    }


def _codegen_ids(ctx):
    """What inspect_data / create_data / write_csv can bind to."""
    return {str(f.id) for f in _candidates(ctx)}


def _grep_ids(ctx):
    """What grep_files sweeps. Imported through its own module so a future
    private pool inside grep_files is caught rather than assumed away."""
    from app.services.file_scope import PURPOSE_READ, readable_files_from_ctx

    return {str(f.id) for f in readable_files_from_ctx(ctx, purpose=PURPOSE_READ)}


def test_no_file_is_shown_that_cannot_be_read():
    """The assertion that would have caught the reported bug on day one.

    Every id rendered into the model's catalog must resolve in every resolver a
    tool goes through. A file the model can name and no tool can open is the
    single failure this module exists to prevent.
    """
    ctx = _ctx()
    shown = _catalog_ids(ctx)

    assert shown, "the fixture must show the model something"

    unreachable_by_codegen = shown - _codegen_ids(ctx)
    assert unreachable_by_codegen == set(), (
        "shown to the model but unreachable by inspect_data / create_data / "
        f"write_csv: {sorted(unreachable_by_codegen)}"
    )

    unreachable_by_grep = shown - _grep_ids(ctx)
    assert unreachable_by_grep == set(), (
        f"shown to the model but not swept by grep_files: {sorted(unreachable_by_grep)}"
    )

    for fid in sorted(shown):
        assert resolve_session_file(ctx, fid) is not None, (
            f"shown to the model but read_file cannot open it: {fid}"
        )


def test_every_file_surface_sees_the_same_files():
    """Membership is one set. Purpose may reorder it; it may not change it."""
    ctx = _ctx()
    expected = {"f-attached", "f-project", "f-midturn"}

    # The catalog is the one surface that does not carry the mid-turn pool:
    # a file materialized this turn is reported by the tool that made it, not
    # re-advertised in the static context. Everything else must agree exactly.
    assert _catalog_ids(ctx) == expected - {"f-midturn"}
    assert _codegen_ids(ctx) == expected
    assert _grep_ids(ctx) == expected


def test_the_project_pool_is_not_silently_dropped():
    """The specific regression. Project membership lives in
    `project_file_association`, so a project file is NEVER in `report.files` —
    a resolver that reads only that table cannot see it, and will not say so."""
    ctx = _ctx()

    assert "f-project" in _catalog_ids(ctx)
    assert "f-project" in _codegen_ids(ctx)
    assert "f-project" in _grep_ids(ctx)
    assert resolve_session_file(ctx, "f-project") is not None


def test_codegen_order_puts_the_mid_turn_file_first():
    """Generated code indexes `excel_files` positionally. Widening the pool
    must not renumber the list the code generator was shown."""
    assert str(_candidates(_ctx())[0].id) == "f-midturn"


def test_a_file_from_another_org_is_still_refused():
    """Widening membership must not widen the tenant boundary."""
    ctx = _ctx()
    ctx["organization"] = type("Other", (), {"id": "org-2"})()

    assert resolve_session_file(ctx, "f-attached") is None


def test_an_unknown_id_still_resolves_to_nothing():
    assert resolve_session_file(_ctx(), "f-nope") is None
    assert "f-nope" not in _codegen_ids(_ctx())
