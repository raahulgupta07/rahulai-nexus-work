"""A row count must come from a query, never from the prompt or the catalog.

Measured defect: asked "how many rows are in the largest table?" the agent
answered with NO tool call on all three connectors (DuckDB / Power BI /
Fabric). Naming a table made it query properly, so the failure is specific to
the un-named, magnitude-shaped question.

What the schema block actually carries was measured before fixing anything:
`render_combined` — the renderer `agent_v2` binds as `schemas_excerpt` — emits
`cols` (a COLUMN count), `<index count>` (a TABLE count) and `score`/`usage`
(how often a table has been queried). None of them is a row count, and no row
count is rendered anywhere in it. So the model had no volume figure and no rule
telling it that a size question is a data question; it answered from numbers
that look like counts but measure something else.

Two things are pinned here:

1. the planner is told, in the cached system prompt, that data volume is never
   in context and that a row count may only be stated from a tool result;
2. the schema block still renders no row count (a pin, so a future "helpful"
   `rows=` attribute has to argue with this file first);
3. `get_connection` — the one tool that surfaces a PERSISTED count — reports an
   unrecorded count as absent rather than as 0. `ConnectionTable.no_rows` is
   NOT NULL DEFAULT 0 and almost nothing writes it (every row in the live
   install is 0), so a bare 0 is a false fact the model cannot tell from a
   measurement.
"""
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.ai.agents.planner.prompt_builder_v3 import PromptBuilderV3  # noqa: E402
from app.ai.context.sections.tables_schema_section import TablesSchemaContext  # noqa: E402
from app.ai.prompt_formatters import Table as PromptTable, TableColumn  # noqa: E402
from app.ai.tools.implementations.get_connection import stored_row_count  # noqa: E402
from app.schemas.ai.planner import PlannerInput  # noqa: E402


def _system_prompt() -> str:
    return PromptBuilderV3._build_system(
        PlannerInput(user_message="how many rows are in the largest table?")
    )


def test_the_planner_is_told_data_volume_is_not_in_context():
    """The prompt must say the schema block carries no row counts."""
    text = _system_prompt().lower()
    assert "row count" in text, "the prompt never mentions row counts at all"
    # The claim, not merely the words: catalog != data volume.
    assert re.search(r"no (data )?volume|not? .{0,20}row count|catalog, not the data", text), (
        "the prompt does not tell the planner that the schema block describes "
        "the catalog rather than the data in it"
    )


def test_a_size_question_must_go_through_a_query():
    """A magnitude question with no table named must still reach a data tool."""
    text = _system_prompt().lower()
    assert "largest table" in text, (
        "the un-named size question — the exact measured failure — is not named "
        "in the prompt, so nothing routes it to a tool"
    )
    assert "how many rows" in text
    # And the prohibition on answering one from context.
    assert "never state a row count" in text


def test_a_stored_catalog_figure_is_marked_stale_in_the_prompt():
    text = _system_prompt().lower()
    assert "stale" in text and "row_count" in text, (
        "the prompt must mark a stored catalog row_count as a possibly-stale "
        "estimate, not as an answer"
    )


def _render_schema_block() -> str:
    """Render the schema block exactly as `schemas_excerpt` is built."""
    from app.schemas.data_source_schema import DataSourceSummarySchema

    tbl = PromptTable(
        name="orders",
        columns=[TableColumn(name="id", dtype="int"), TableColumn(name="amount", dtype="float")],
        pks=[],
        fks=[],
        is_active=True,
        usage_count=12,
        success_count=11,
        failure_count=1,
        score=0.42,
    )
    ds = TablesSchemaContext.DataSource(
        info=DataSourceSummarySchema(id="ds1", name="warehouse", type="duckdb"),
        tables=[tbl],
    )
    return TablesSchemaContext(data_sources=[ds]).render_combined(top_k_per_ds=10)


def test_the_schema_block_renders_no_row_count():
    """PIN (not a pre-fix failure): no row-count attribute in the schema block.

    This one passes on the pre-fix tree too — nothing rendered a row count
    then either. It exists so that adding one later is a deliberate argument
    with this file rather than a quiet regression, and it is written to FAIL if
    a `rows=`/`row_count=` attribute ever appears.
    """
    xml = _render_schema_block()
    assert "<table" in xml, "the fixture rendered nothing — the pin would be vacuous"
    offenders = re.findall(r'\b(rows|row_count|no_rows|num_rows|total_rows)\s*=', xml)
    assert not offenders, (
        f"the schema block now renders {sorted(set(offenders))} — a bare row "
        "count in the prompt is what lets the model answer a size question "
        "without querying"
    )


def test_the_pin_can_actually_fail():
    """The offender scan must match a row count when one is present.

    A scan that can never match is a comment with a test's salary.
    """
    fake = '<table name="orders" cols="2" rows="1200"/>'
    assert re.findall(r'\b(rows|row_count|no_rows|num_rows|total_rows)\s*=', fake)


def test_an_unrecorded_stored_row_count_is_reported_as_absent():
    """`no_rows` is NOT NULL DEFAULT 0 — 0 means "never measured", not "empty"."""
    assert stored_row_count(0) is None
    assert stored_row_count(None) is None
    assert stored_row_count("") is None


def test_a_real_stored_row_count_survives():
    assert stored_row_count(1200) == 1200
    assert stored_row_count("1200") == 1200


def test_the_tool_schema_marks_row_count_as_an_estimate():
    from app.ai.tools.schemas.get_connection import ConnectionTableItem

    desc = (ConnectionTableItem.model_fields["row_count"].description or "").lower()
    assert "estimate" in desc and "never report it" in desc, (
        "get_connection still presents a persisted row count as an "
        "authoritative number"
    )
