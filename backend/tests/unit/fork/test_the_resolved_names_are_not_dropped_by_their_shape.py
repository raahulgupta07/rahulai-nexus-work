"""The planner was right every time, and the names never reached the coder.

Measured on the live instance 2026-08-17, against report
`747df8a9-6106-491f-a088-0acc5239fe69`. Every tool call carried the correct,
fully-qualified name:

    inspect_data  tables_by_source: [{"name": "LK_CFC_Sales.dbo.cfc_champion"}, ...]
    create_data   tables_by_source: ["LK_CFC_Sales.dbo.cfc_accuracy_by_outlet", ...]

The catalog holds exactly ONE `cfc_champion`, in `LK_CFC_Sales`, and the org's
Fabric instruction names it explicitly. Nothing was ambiguous and nothing was
missing — and the generated SQL still said `CFC_Lakehouse.dbo.cfc_champion`.

★★★`InspectDataInput.tables_by_source` was `Optional[List[Dict[str, Any]]]` — a
free-form dict list with no shape, so `{"name": ...}` validated happily. Every
consumer downstream reads `group["tables"]`, found nothing, and handed the code
generator an EMPTY list. The "Resolved Target Tables (authoritative)" block from
`0.0.542.1` therefore never rendered for inspect_data at all, and the coder was
back to picking one lakehouse out of four — one of which, `CFC_Lakehouse`,
genuinely holds tables named `cfc_*`.

★An untyped argument is not permissive, it is SILENT: it accepts the wrong shape
and discards the meaning. `create_data`, which uses the typed lenient field,
answered the same question twice in 12.8s and 13.1s. `inspect_data` failed three
times over 79s in the same turn.

Two layers are fixed, deliberately:
  1. the tool schema, so the names are kept — the real fix;
  2. the coder's extractor, as a floor, so a resolved name can never be lost
     again just because some future caller nests it differently.
"""

import pytest

from app.ai.agents.coder.coder import (
    _resolved_table_names,
    _resolved_tables_section,
    _table_correction_section,
)
from app.ai.tools.schemas.inspect_data import InspectDataInput

CHAMPION = "LK_CFC_Sales.dbo.cfc_champion"
OUTLET = "LK_CFC_Sales.dbo.cfc_accuracy_by_outlet"

# Verbatim from `tool_executions.arguments_json` on the live instance.
LIVE_INSPECT = [{"name": CHAMPION}, {"name": OUTLET}]
LIVE_CREATE = [OUTLET, CHAMPION]
GROUPED = [{"data_source_id": None, "tables": [CHAMPION]}]


# --------------------------------------------------------------------------
# 1. the tool schema keeps the names
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "label, sent, expected",
    [
        ("the live inspect_data shape", LIVE_INSPECT, [CHAMPION, OUTLET]),
        ("bare strings", LIVE_CREATE, [OUTLET, CHAMPION]),
        ("table_name instead of tables", [{"table_name": CHAMPION}], [CHAMPION]),
        ("the grouped shape it always accepted", GROUPED, [CHAMPION]),
    ],
)
def test_inspect_data_keeps_the_names_the_planner_sent(label, sent, expected):
    parsed = InspectDataInput(user_prompt="check versions", tables_by_source=sent)
    kept = [t for group in (parsed.tables_by_source or []) for t in group.tables]
    assert kept == expected, label


def test_the_field_is_no_longer_a_shapeless_dict_list():
    """★The whole defect in one assertion: `Dict[str, Any]` validates anything
    and therefore checks nothing."""
    annotation = str(InspectDataInput.model_fields["tables_by_source"].annotation)
    assert "TablesBySource" in annotation, annotation
    assert "Dict[str, Any]" not in annotation


def test_a_file_only_inspection_still_needs_no_tables():
    parsed = InspectDataInput(user_prompt="preview the uploaded excel")
    assert parsed.tables_by_source is None


# --------------------------------------------------------------------------
# 2. the coder sees them as authoritative
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "label, sent",
    [("live inspect_data", LIVE_INSPECT), ("bare strings", LIVE_CREATE), ("grouped", GROUPED)],
)
def test_the_authoritative_block_renders_for_every_shape(label, sent):
    parsed = InspectDataInput(user_prompt="x", tables_by_source=sent)
    section = _resolved_tables_section(parsed.tables_by_source, "")
    assert section, f"{label}: the authoritative block is ABSENT"
    assert CHAMPION in section


@pytest.mark.parametrize(
    "label, sent, expected",
    [
        ("flat {'name': ...} dicts", LIVE_INSPECT, [CHAMPION, OUTLET]),
        ("a bare string per element", LIVE_CREATE, [OUTLET, CHAMPION]),
        ("a group whose tables is one string", [{"tables": CHAMPION}], [CHAMPION]),
        ("the grouped shape", GROUPED, [CHAMPION]),
    ],
)
def test_the_extractor_is_the_floor_under_the_schema(label, sent, expected):
    """★Raw shapes, bypassing the tool schema entirely — a resolved name must not
    be lost again because a future caller nests it differently."""
    assert _resolved_table_names(sent) == expected, label


def test_an_unreadable_shape_contributes_nothing_rather_than_a_guess():
    """Fail-open in the one direction that matters: it may miss a name, it must
    never invent one."""
    assert _resolved_table_names([{"unrelated": "x"}]) == []
    assert _resolved_table_names([None, 7]) == []
    assert _resolved_tables_section([{"unrelated": "x"}], "") == ""


def test_the_model_is_told_not_to_re_prove_a_resolved_table():
    """The other half of the 79 seconds: an inspection that spends a round trip
    confirming a table the catalog already confirmed, then tries prefixes in a
    loop when it disagrees."""
    section = _resolved_tables_section(GROUPED, "")
    assert "Do NOT spend a query" in section
    assert "alternative prefixes" in section


# --------------------------------------------------------------------------
# 3. the correction is repeated where the model actually reads
# --------------------------------------------------------------------------

GUARD_MESSAGE = (
    "Table reference check failed before execution. `CFC_Lakehouse.dbo.cfc_champion` "
    'does not exist on client "Microsoft Fabric:fabric_user-1" — that table is '
    f"`{CHAMPION}`."
)


def test_a_table_correction_is_hoisted_next_to_the_target_list():
    """It was already reaching the retry — buried in `<failed_attempt><error>`,
    hundreds of lines of rejected code away from where the model reads its
    targets. The same wrong lakehouse came back anyway, in the same turn."""
    out = _table_correction_section([("some rejected code", GUARD_MESSAGE)], "")
    assert CHAMPION in out
    assert "CORRECTION" in out


def test_only_table_corrections_are_hoisted():
    """A general prompt shouting every past error would drown the one that
    carries a name."""
    assert _table_correction_section([("code", "KeyError: 'revenue'")], "") == ""
    assert _table_correction_section([], "") == ""
    assert _table_correction_section(None, "") == ""


def test_the_same_correction_is_not_repeated_twice():
    out = _table_correction_section(
        [("a", GUARD_MESSAGE), ("b", GUARD_MESSAGE)], ""
    )
    assert out.count("CFC_Lakehouse") == 1
