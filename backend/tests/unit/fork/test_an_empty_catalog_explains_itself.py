"""An agent with no tables must not invent a reason it has none.

The incident: our own database refused connections mid-crawl, the Fabric sync
died, the member's catalog stayed empty — and the agent told them to "attach or
refresh the lakehouse". The lakehouse had been attached for weeks. The agent was
not wrong to say something; it had nothing at all in context, and a blank slate
reads as "the user never set this up".

Two things are checked here:

1. A source whose sync failed is **kept in the schema context**, carrying the
   failure. Previously it rendered empty and was dropped by a `continue`.
2. The wording is decided by the classified cause, and an outage of ours
   explicitly forbids telling the member to go fix their connection.

★No schema. The section renders from a plain dataclass, so this file stays in
`tests/unit/fork` — see CLAUDE.md, split by cost.
"""
import inspect

import pytest

from app.ai.context.sections.tables_schema_section import TablesSchemaContext


DataSourceSection = TablesSchemaContext.DataSource


def _section(**kwargs):
    """A section with nothing in it but whatever the test puts there."""
    from app.schemas.data_source_schema import DataSourceSummarySchema

    info = DataSourceSummarySchema(
        id="ds-1", name="Fabric", type="fabric_user", is_active=True,
    )
    return DataSourceSection(info=info, tables=[], **kwargs)


# ────────────────── the source survives an empty catalog ──────────────────


def test_a_source_with_no_tables_and_no_failure_is_still_dropped():
    """The existing behaviour, asserted so the fix does not become "never drop
    anything" — an agent that genuinely has nothing to offer is noise in every
    prompt.

    ★`render_combined`, not `render`. Only the combined path drops an empty
    source; the 'full' path has always kept it. Written against `render` first,
    which made this test claim a behaviour that never existed.
    """
    ctx = TablesSchemaContext(data_sources=[_section()])
    assert "Fabric" not in ctx.render_combined()


def test_a_source_whose_sync_failed_is_kept():
    ctx = TablesSchemaContext(data_sources=[
        _section(sync_failure={"kind": "infrastructure", "message": "boom"}),
    ])
    rendered = ctx.render_combined()
    assert "Fabric" in rendered
    assert "sync_failure" in rendered


@pytest.mark.parametrize("how", ["render", "render_combined"])
def test_both_render_paths_carry_the_explanation(how):
    """★An explanation that reaches one prompt shape and not the other means
    the same question gets the honest answer or the invented one depending on
    which tool built the context."""
    ctx = TablesSchemaContext(data_sources=[
        _section(sync_failure={"kind": "infrastructure", "message": "boom"}),
    ])
    assert "sync_failure" in getattr(ctx, how)()


# ────────────────── the wording follows the cause ──────────────────


def test_our_own_outage_forbids_blaming_the_member():
    """★The whole point of the phase. If the model is told the catalog is empty
    and nothing else, "reconnect your lakehouse" is a reasonable guess. It has
    to be told that guess is wrong."""
    ctx = TablesSchemaContext(data_sources=[
        _section(sync_failure={"kind": "infrastructure", "message": "InvalidPasswordError"}),
    ])
    rendered = ctx.render_combined()
    assert "OUR service" in rendered
    assert "Do NOT tell the user to attach, refresh, reconnect" in rendered
    assert "retried automatically" in rendered


def test_a_source_side_failure_does_point_at_the_connection():
    """The opposite case must still work, or the fix trades one wrong answer
    for another: when the source really did refuse us, checking credentials is
    the correct advice."""
    ctx = TablesSchemaContext(data_sources=[
        _section(sync_failure={"kind": "source", "message": "Login failed"}),
    ])
    rendered = ctx.render_combined()
    assert "refused by the source" in rendered
    assert "credentials" in rendered
    assert "Do NOT tell the user" not in rendered


def test_an_unclassified_failure_tells_the_model_not_to_guess():
    ctx = TablesSchemaContext(data_sources=[_section(sync_failure={"message": "?"})])
    rendered = ctx.render_combined()
    assert "cause is not known" in rendered
    assert "do not guess" in rendered.lower()


@pytest.mark.parametrize("kind", ["infrastructure", "source", None])
def test_the_reported_error_is_always_carried(kind):
    ctx = TablesSchemaContext(data_sources=[
        _section(sync_failure={"kind": kind, "message": "RAW-MARKER"}),
    ])
    assert "RAW-MARKER" in ctx.render_combined()


def test_a_failure_with_no_message_still_renders():
    ctx = TablesSchemaContext(data_sources=[_section(sync_failure={"kind": "source"})])
    assert "refused by the source" in ctx.render_combined()


# ────────────────── F.2 — say where you looked ──────────────────


def test_the_endpoints_that_answered_are_named():
    """"Not found" is a guess unless the model can say what it searched. Naming
    them lets the member check the list against the access they know they have
    — which is how a wrong sync gets caught instead of believed."""
    ctx = TablesSchemaContext(data_sources=[
        _section(sync_failure={
            "kind": "infrastructure",
            "message": "boom",
            "searched": ["DL_POC", "Sales_LH"],
        }),
    ])
    rendered = ctx.render_combined()
    assert "DL_POC" in rendered
    assert "Sales_LH" in rendered
    assert 'count="2"' in rendered


def test_no_searched_block_when_nothing_answered():
    """A crawl that died before reaching anything has no list to give, and an
    empty "searched: " line implies we looked and found nothing — the opposite
    of what happened."""
    ctx = TablesSchemaContext(data_sources=[
        _section(sync_failure={"kind": "infrastructure", "searched": []}),
    ])
    assert "successfully_searched" not in ctx.render_combined()


def test_blank_endpoint_names_are_dropped_not_rendered():
    ctx = TablesSchemaContext(data_sources=[
        _section(sync_failure={"kind": "source", "searched": ["DL_POC", "", None]}),
    ])
    rendered = ctx.render_combined()
    assert 'count="1"' in rendered


# ────────────────── the builder only speaks when it knows ──────────────────


def _builder_source(fn_name: str) -> str:
    from app.ai.context.builders.schema_context_builder import SchemaContextBuilder

    return inspect.getsource(getattr(SchemaContextBuilder, fn_name))


def test_the_note_is_suppressed_when_the_catalog_is_healthy():
    """A source with all its tables does not need a paragraph about a sync that
    failed and recovered — it would ride in every prompt for the rest of the
    day and crowd out the schema it is annotating."""
    from app.ai.context.builders import schema_context_builder as mod

    source = inspect.getsource(mod)
    assert "None if tables" in source


def test_the_builder_never_fails_a_turn_over_this():
    """An empty catalog with no explanation is the behaviour we already shipped.
    Degrading to it is acceptable; raising is not."""
    source = _builder_source("_last_sync_failure")
    assert "except Exception:" in source
    assert source.rstrip().endswith("return None")


def test_the_builder_reads_the_same_row_the_member_is_looking_at():
    """★If the agent's account came from a different store than the sync strip,
    the two could disagree on screen — which is worse than either being wrong
    alone, because the member cannot tell which to believe."""
    source = _builder_source("_last_sync_failure")
    assert "ConnectionSyncProgress" in source
    assert "self.user.id" in source


def test_only_endpoints_that_actually_succeeded_are_listed_as_searched():
    """★The trap: `detail` rows use 'ok'/'completed' for success and 'failed'
    for a workspace that did not answer. Listing all of them would claim we
    searched places we never reached."""
    source = _builder_source("_last_sync_failure")
    assert '"ok"' in source and '"completed"' in source
    assert '"failed"' not in source
