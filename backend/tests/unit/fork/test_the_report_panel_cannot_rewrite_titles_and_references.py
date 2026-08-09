"""The report-session Agent panel must not rewrite an instruction it edits.

Three defects, all the same shape: a surface that *displays* an instruction was
quietly *rewriting* the stored row, so the Knowledge Explorer and the report
panel disagreed about the same instruction.

  a) The title input wrote `value.toUpperCase()` back into the model on every
     keystroke, and the `uppercaseTitle` prop defaulted to `true`, so every
     mount site inherited it. Touching the title field inside a report session
     UPPERCASED the stored title for every other surface. Uppercase is a
     presentation choice and belongs in CSS (`:class="{ uppercase: … }"`, which
     was already there beside it).

  b) `buildInstructionPayload` omitted each reference's `display_text` and
     hardcoded `relation_type: 'scope'`. The service falls back to the target
     object's name when `display_text` is null, so saving from the panel
     relabelled every mention chip — and flattened every 'mention' reference to
     'scope'. `handleReferencesChange` compounded it by rebuilding the selection
     from the option list, which carries neither field, on every checkbox toggle.

  c) The panel's two instruction fetches omitted `include_archived`, which the
     Explorer's tree query passes. An archived instruction therefore existed in
     one surface and was invisible in the other — "my instruction disappeared".

★`include_global` differs between the two surfaces ON PURPOSE and is pinned
here so nobody "fixes" it: a report session has no synthetic Global entry in its
agent list, so dropping globals there would make them unreachable.

MEASURED, guard logic run against `git show HEAD:<path>` and the working tree:

    test_the_title_field_never_writes_uppercase_back   HEAD 1 hit  -> now 0
    test_uppercase_title_defaults_off                  HEAD fail   -> now pass
    test_the_payload_carries_display_text              HEAD fail   -> now pass
    test_the_payload_does_not_hardcode_scope           HEAD 1 hit  -> now 0
    test_a_checkbox_toggle_keeps_the_stored_fields     HEAD fail   -> now pass
    test_both_panel_fetches_ask_for_archived           HEAD 0 of 2 -> now 2 of 2

Upstream: 55cb33f0.
"""

from __future__ import annotations

import re

from vue_source import read_source

GLOBAL_CREATE = "components/InstructionGlobalCreateComponent.vue"
REPORT_PANEL = "components/report/ReportAgentPanel.vue"


def test_the_title_field_never_writes_uppercase_back():
    """No `.toUpperCase()` anywhere in the component that hosts the title field.

    Scoping this to the `@input` handler alone would miss `resetForm`, which
    seeded the field with `seedTitle.toUpperCase()` — a second write of the same
    defect, on the create path rather than the edit path. There is no legitimate
    reason for this component to upper-case anything; CSS does the display.
    """
    hits = re.findall(r"\.toUpperCase\s*\(", read_source(GLOBAL_CREATE))
    assert hits == [], (
        f"{len(hits)} `.toUpperCase()` call(s) in {GLOBAL_CREATE}. Uppercase is "
        "display-only here — use the `uppercase` CSS class, never a write back "
        "into instructionForm."
    )


def test_uppercase_title_defaults_off():
    src = read_source(GLOBAL_CREATE)
    assert re.search(r"uppercaseTitle:\s*false", src), (
        "`uppercaseTitle` must default to false, matching the Knowledge "
        "Explorer. Defaulting it true made every mount site uppercase titles."
    )
    assert not re.search(r"uppercaseTitle:\s*true", src)


def _payload_block(src: str) -> str:
    start = src.index("const buildInstructionPayload")
    return src[start : src.index("\n}", start)]


def test_the_payload_carries_display_text():
    block = _payload_block(read_source(GLOBAL_CREATE))
    assert "display_text" in block, (
        "buildInstructionPayload drops `display_text`. The service falls back to "
        "the target object's name when it is null, so saving relabels every "
        "mention chip."
    )


def test_the_payload_does_not_hardcode_scope():
    """`relation_type` must come from the reference, not from a literal."""
    block = _payload_block(read_source(GLOBAL_CREATE))
    hardcoded = re.findall(r"relation_type:\s*'scope'", block)
    assert hardcoded == [], (
        "buildInstructionPayload hardcodes relation_type: 'scope', flattening "
        "every 'mention' reference on save. Read it off the reference "
        "(`r.relation_type || 'scope'`)."
    )
    assert re.search(r"relation_type:\s*r\.relation_type", block), (
        "buildInstructionPayload must carry the stored relation_type through."
    )


def test_a_checkbox_toggle_keeps_the_stored_fields():
    """Rebuilding the selection from the option list loses what only the stored
    reference has. `display_text` / `relation_type` are absent from the mention
    options, so a blind rebuild reset both on every toggle — undoing the payload
    fix one checkbox at a time."""
    src = read_source(GLOBAL_CREATE)
    start = src.index("const handleReferencesChange")
    block = src[start : src.index("\n}", start)]
    assert "selectedReferences.value" in block
    assert re.search(r"kept\.get\(", block), (
        "handleReferencesChange rebuilds selectedReferences from the option "
        "list alone, discarding the stored display_text / relation_type. Keep "
        "the already-selected entry for any id that survives."
    )


def test_both_panel_fetches_ask_for_archived():
    """Both instruction fetches in the report panel, not just one.

    The panel has two branches — the synthetic global agent and a real agent —
    and each fetches instructions separately. Fixing one leaves archived rows
    invisible in the other.
    """
    src = read_source(REPORT_PANEL)
    calls = re.findall(r"fetchAllInstructions<any>\((.*?)\n\s*\)", src, re.DOTALL)
    assert len(calls) == 2, f"expected 2 fetchAllInstructions calls, found {len(calls)}"
    for i, call in enumerate(calls):
        assert "include_archived: true" in call, (
            f"fetchAllInstructions call #{i + 1} in {REPORT_PANEL} omits "
            "include_archived, which the Knowledge Explorer's tree query "
            "passes. Archived instructions then exist in one surface and are "
            "invisible in the other."
        )


def test_the_agent_branch_still_asks_for_globals():
    """Pinned deliberately: `include_global` is NOT a divergence to fix.

    A report session has no synthetic "Global" entry in its agent list, so
    dropping globals here makes them unreachable rather than merely re-homed.
    """
    src = read_source(REPORT_PANEL)
    calls = re.findall(r"fetchAllInstructions<any>\((.*?)\n\s*\)", src, re.DOTALL)
    agent_call = [c for c in calls if "data_source_ids" in c]
    assert len(agent_call) == 1
    assert "include_global: true" in agent_call[0], (
        "The agent-scoped fetch must keep include_global: true — see this "
        "test's docstring before changing it."
    )
