"""DEF-003 — a tool's real failure message was replaced by "Output validation failed".

Found by: E2E-P2.6 (Fabric L6 channel YoY — one edit_note came back `status=error`,
`error_message="Output validation failed"`, and the agent had to burn a turn on
read_query to recover)

Every `_fail()` path in the doc/note authoring family emits
``{"success": False, "note_id": ..., "error": "<the real reason>"}`` — no
``diff_applied``/``version``/``title``, because those are success fields and this is
a failure. ToolRunner then validated that failure envelope against the tool's
SUCCESS output_model, and on mismatch RETURNED EARLY with a generic
"Output validation failed / Fix output to match declared output schema" —
discarding ``last_observation``, which held the actionable message. The model was
told to fix its output schema (author advice) instead of that its `find` string
did not match (something it could actually act on).

Four tools were affected: create_doc, create_note, edit_doc, edit_note.

Contract these tests pin:
  * a tool that declares ``success: False`` keeps its own observation and message
  * genuinely malformed SUCCESS output is still rejected (the check is not disabled)
  * every tool in the family survives its own failure path
"""
import pytest
from pydantic import BaseModel

from app.ai.runner.tool_runner import ToolRunner
from app.ai.tools.schemas.events import ToolEndEvent


class _Output(BaseModel):
    """Stand-in for the real success models: a required field a failure cannot supply."""

    note_id: str
    diff_applied: bool


class _Tool:
    """Minimal tool whose run_stream yields one tool.end with the given payload."""

    def __init__(self, payload, name="edit_note_stub"):
        self._payload = payload
        self.name = name
        self.output_model = _Output

    async def run_stream(self, tool_input, runtime_ctx):
        yield ToolEndEvent(type="tool.end", payload=self._payload)


REAL_REASON = "No match found for find text: '- [ ] step 3'"

FAILURE_PAYLOAD = {
    "output": {"success": False, "note_id": "n1", "error": REAL_REASON},
    "observation": {
        "summary": f"Failed to edit note: {REAL_REASON}",
        "note_id": "n1",
        "error": {"type": "edit_match_error", "message": REAL_REASON},
    },
}


async def _run(payload, name="edit_note_stub"):
    async def emit(_evt):
        return None

    return await ToolRunner().run(_Tool(payload, name), {}, {}, emit)


# --- the defect itself --------------------------------------------------------


@pytest.mark.asyncio
async def test_def003_declared_failure_keeps_its_real_message():
    res = await _run(FAILURE_PAYLOAD)
    blob = repr(res)
    assert REAL_REASON in blob, "the actionable reason must reach the model"
    assert "Output validation failed" not in blob


@pytest.mark.asyncio
async def test_def003_declared_failure_keeps_its_observation():
    """The generic path RETURNED EARLY and threw the observation away."""
    res = await _run(FAILURE_PAYLOAD)
    obs = res.get("observation") or {}
    assert obs.get("error", {}).get("type") == "edit_match_error"
    assert obs.get("summary", "").startswith("Failed to edit note")


@pytest.mark.asyncio
async def test_def003_no_schema_author_advice_on_a_tool_failure():
    """"Fix output to match declared output schema" is advice for the tool AUTHOR."""
    res = await _run(FAILURE_PAYLOAD)
    assert "declared output schema" not in repr(res)


@pytest.mark.asyncio
async def test_def003_failure_output_is_passed_through_verbatim():
    res = await _run(FAILURE_PAYLOAD)
    assert res.get("output") == FAILURE_PAYLOAD["output"]


@pytest.mark.parametrize("tool_name", ["create_doc", "create_note", "edit_doc", "edit_note"])
@pytest.mark.asyncio
async def test_def003_every_tool_in_the_family(tool_name):
    """All four tools' _fail() envelopes omit a required success field.

    Asserting on the observation, NOT on `REAL_REASON in repr(res)`: pydantic's
    error details echo the offending input dict, so the message text appears in the
    generic reply too and a text-presence check would pass even unfixed.
    """
    res = await _run(FAILURE_PAYLOAD, name=tool_name)
    assert res.get("error", {}).get("type") != "output_validation_error"
    assert (res.get("observation") or {}).get("error", {}).get("message") == REAL_REASON


# --- guard: the validation must still catch real breakage --------------------


@pytest.mark.asyncio
async def test_def003_malformed_success_output_is_still_rejected():
    """A success payload missing a required field is a genuine bug — keep failing it."""
    res = await _run(
        {
            "output": {"note_id": "n1"},  # no diff_applied, and no success:False
            "observation": {"summary": "Edited note"},
        }
    )
    assert res.get("error", {}).get("type") == "output_validation_error"


@pytest.mark.asyncio
async def test_def003_valid_success_output_still_passes():
    res = await _run(
        {
            "output": {"note_id": "n1", "diff_applied": True},
            "observation": {"summary": "Edited note (note_id: n1) via surgical edits."},
        }
    )
    assert "error" not in res
    assert res["output"]["diff_applied"] is True


@pytest.mark.asyncio
async def test_def003_success_true_is_not_treated_as_a_failure():
    """Only `success is False` opts out — a truthy/absent value must not skip the check."""
    res = await _run(
        {
            "output": {"success": True, "note_id": "n1"},  # missing diff_applied
            "observation": {"summary": "Edited note"},
        }
    )
    assert res.get("error", {}).get("type") == "output_validation_error"


@pytest.mark.asyncio
async def test_def003_string_off_is_not_mistaken_for_failure():
    """`success: "off"` is truthy — it must not silently opt out of validation."""
    res = await _run(
        {
            "output": {"success": "off", "note_id": "n1"},
            "observation": {"summary": "Edited note"},
        }
    )
    assert res.get("error", {}).get("type") == "output_validation_error"
