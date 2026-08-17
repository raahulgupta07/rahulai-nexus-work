"""up537's UI projection must not re-impose the MCP ceiling we just removed.

Release 0.0.537 added `project_tool_result_for_ui` to `serializers/completion_v2`
and routed **stored** tool output through it: `agent_v2` now persists
`project_tool_result_for_ui(tool_output)` into `ToolExecution.result_json`
instead of the full output, and the read path applies it again. It caps any
`rows` list to `PREVIEW_ROWS` (20).

Our newest work before the port was the opposite direction — commit 9e6b302fe,
"Stop cutting MCP results to three records". So the two changes push against
each other, and **git reports no conflict at all**: `completion_v2.py` is a file
this fork does not touch, so the collision is semantic and invisible to every
merge tool. That is exactly the class of thing that ships.

Measured at port time: `execute_mcp` emits `output`, `observation`, `preview`
and `columns` — none of the four keys the projection caps (`rows`, `data`,
`data_preview`, `results`). So the two coexist today. This test pins that fact
so the day someone reshapes an MCP result into a `rows` grid, it says so here
rather than in a user's truncated result.

★The positive control is the load-bearing half. "MCP output is unchanged" is
equally satisfied by a projection that does nothing at all, so the same test
proves the cap still fires on the shape it was written for.
"""
from __future__ import annotations

from app.serializers.completion_v2 import PREVIEW_ROWS, project_tool_result_for_ui


def _mcp_like_output(n: int = 500) -> dict:
    """The shape `execute_mcp` actually returns, at a size worth truncating."""
    records = [{"id": i, "name": f"row-{i}"} for i in range(n)]
    return {
        "output": {
            "success": True,
            "preview": records,
            "columns": ["id", "name"],
            "record_count": n,
        },
        "observation": {"summary": f"{n} records", "success": True},
    }


def test_an_mcp_result_is_not_truncated_by_the_ui_projection():
    raw = _mcp_like_output(500)
    projected = project_tool_result_for_ui(raw)

    assert len(projected["output"]["preview"]) == 500, (
        "the up537 UI projection truncated an MCP result — the ceiling removed "
        "in 'Stop cutting MCP results to three records' is back, and no merge "
        "conflict would have shown it"
    )
    assert projected["output"]["record_count"] == 500
    assert projected["observation"]["summary"] == "500 records"


def test_the_projection_still_caps_the_shape_it_was_written_for():
    """★Positive control. Without this, deleting the projection passes the test
    above."""
    raw = {"data": {"rows": [{"i": i} for i in range(200)], "columns": ["i"]}}
    projected = project_tool_result_for_ui(raw)

    assert len(projected["data"]["rows"]) == PREVIEW_ROWS, (
        "the row cap no longer fires, so the test above proves nothing"
    )
    assert projected["data"]["truncated"] is True
    assert projected["data"]["total_rows"] == 200


def test_mcp_output_carries_none_of_the_capped_keys():
    """The premise of the first test, asserted rather than assumed.

    If an MCP result ever grows a `rows`/`data`/`results` container, the cap
    starts applying to it and this fails while the behaviour is still correct —
    which is the moment to re-read the first test, not to delete this one.
    """
    import ast
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[3]
        / "app" / "ai" / "tools" / "implementations" / "execute_mcp.py"
    ).read_text(encoding="utf-8")

    emitted = {
        k.value
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Dict)
        for k in node.keys
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    }
    capped = {"rows", "data", "data_preview", "results"}
    assert not (emitted & capped), (
        f"execute_mcp now emits {sorted(emitted & capped)}, which the up537 UI "
        "projection caps — re-check that MCP results still reach the user whole"
    )
