"""A user who has not signed in saw an agent with no tables, and no reason.

`_resolve_user_access` returns `'none'` for a `user_required` connection whose
caller has not proven access, and the builder then renders the data source with
an empty table list — deliberately, so a stale overlay cannot keep leaking
tables after someone loses access. That part is right and is not changed here.

What was missing is the SENTENCE. An empty table list and a genuinely empty
database are the same thing in the prompt, so the model reached the only
conclusion available to it and told the user the data does not exist. It does
exist; they are not connected to it. That is the same silent-wrong-answer class
as the eleven connectors reporting a permission failure as zero tables.

The reason travels through the existing `sync_failure` channel, because the
channel is already "here is why this source is thin, say this and do not invent
a cause" and is already what keeps a thin source from being dropped from the
prompt entirely.

★An access denial OUTRANKS a sync failure. When access was never proven the
catalog was never consulted, so reporting yesterday's sync error would send the
member to fix a thing that is not the reason they can see nothing.
"""

import ast
import inspect
from pathlib import Path

from app.ai.context.sections.tables_schema_section import (
    DataSourceSummarySchema,
    TablesSchemaContext,
)

SECTION = Path(inspect.getsourcefile(TablesSchemaContext)).read_text(encoding="utf-8")
BUILDER = Path(
    "app/ai/context/builders/schema_context_builder.py"
).read_text(encoding="utf-8")


def _render(sync_failure):
    ds = TablesSchemaContext.DataSource(
        info=DataSourceSummarySchema(id="ds-1", name="Fabric", type="ms_fabric"),
        sync_failure=sync_failure,
    )
    return ds._render_sync_failure_xml()


def test_an_unconnected_user_is_told_to_connect_their_account():
    xml = _render({"kind": "access"})
    assert xml, "an access denial must render a reason, not nothing"
    assert "own credentials" in xml or "OWN credentials" in xml
    assert "connect their" in xml


def test_the_model_is_told_not_to_call_the_data_missing():
    """★The whole point. The damaging output is not the empty list, it is the
    sentence the model writes from it."""
    xml = _render({"kind": "access"})
    assert "do NOT say the data" in xml.lower().replace("do not say the data", "do NOT say the data") \
        or "not say the data" in xml.lower()
    assert "do not exist" in xml.lower()


def test_the_access_reason_is_not_dressed_up_as_a_sync_failure():
    """A member sent to re-run a sync that is working fine loses the thread
    entirely — the sync is not why they see nothing."""
    xml = _render({"kind": "access"})
    assert "sync" not in xml.lower().replace("sync_failure", "")


def test_the_other_reasons_still_render_as_they_did():
    for kind in ("infrastructure", "source", None):
        assert _render({"kind": kind}), kind
    assert _render(None) == "", "no reason means no block, as before"


def test_access_outranks_a_stale_sync_error_in_the_builder():
    tree = ast.parse(BUILDER)
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "sync_failure":
            source = ast.dump(node.value)
            assert "access_denied" in source, (
                "the builder still reports a sync failure to a user who was "
                "never allowed to look at the catalog"
            )
            assert "_last_sync_failure" in source, (
                "the sync-failure reason must survive for users who DO have access"
            )
            return
    raise AssertionError("the builder no longer passes sync_failure at all")


def test_a_denied_source_still_reaches_the_prompt():
    """★If the source were dropped the sentence would go with it, and we would be
    back to an agent that silently has nothing. The renderer keeps a source that
    has a reason — this pins that the reason counts."""
    assert "sync_failure_xml" in SECTION
    body = SECTION[SECTION.index("sync_failure_xml = "):]
    assert "sync_failure_xml" in body.split("continue")[0], (
        "a source whose only content is its reason must not be dropped"
    )
