"""DEF-005 — one question, three contradictory answers, all presented as current.

Found by: a single user turn in which the agent called ``create_data`` three times
for the same thing. Each call minted its own Widget+Query+Step+Visualization set —
``CreateDataInput`` has no field naming an existing widget, so a retry cannot
revise, it can only add. The result was three sets with an IDENTICAL title, all
``status=success``, holding CONTRADICTORY numbers (52,372,000 / 518,000 /
78,558,000). Nothing marked the first two stale, so both the UI rail and the
agent's own LLM context presented three mutually exclusive answers as three equal
truths.

The fix is bookkeeping, not deletion: after a successful create, the earlier
same-title visualizations from THAT TURN are stamped in the existing
``Visualization.view`` JSON column with ``superseded_by`` / ``superseded_at`` /
``superseded_reason`` (no migration). The context layer then reads that stamp and
renders the superseded result with an explicit out-of-date warning, sorted after
the current ones.

Contract these tests pin:
  * the title match is strict — whitespace/case only, so two genuinely different
    results can never collide (a FALSE supersede hides real data)
  * a superseded entry renders the CONSEQUENCE, not a status: out-of-date, which
    id replaced it, and an explicit ban on totalling/averaging/reconciling it
  * superseded entries render AFTER current ones
  * an observation with no supersede keys renders byte-identically to the
    pre-fix renderer (no cosmetic drift on the normal path)
  * reading the stamp NEVER raises — ``view`` arrives as None, dict, JSON string
    or garbage, and this runs while assembling context for every agent turn, so a
    throw here takes down the whole turn

NOT covered here: ``_mark_superseded_visualizations`` end-to-end. It needs a live
DB session, real Query/Visualization rows and correct turn ordering; a mocked
version would pass while the real feature was dead. It is verified by a live run.
"""
import pytest

from app.ai.context.builders import query_context_builder as qcb
from app.ai.context.sections.queries_section import (
    QueriesSection,
    QueryObservation,
    QueryVisualizationSummary,
)
from app.ai.tools.implementations.create_data import CreateDataTool


def _normalize(title):
    return CreateDataTool._normalize_viz_title(title)


# --- unit 1: the title contract (the false-supersede guard) -------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Total revenue", "total revenue"),
        ("  Total revenue  ", "total revenue"),
        ("TOTAL REVENUE", "total revenue"),
        ("Total   revenue", "total revenue"),
        ("Total\trevenue", "total revenue"),
        ("Total\nrevenue", "total revenue"),
        ("\n Total  \t revenue \n", "total revenue"),
        ("", ""),
        ("   ", ""),
        (None, ""),
    ],
)
def test_def005_normalize_strips_casefolds_and_collapses(raw, expected):
    assert _normalize(raw) == expected


@pytest.mark.parametrize(
    "a,b",
    [
        ("Total revenue", "total revenue"),
        ("Total revenue", "  TOTAL   REVENUE  "),
        ("Revenue by segment", "Revenue\tby\nsegment"),
    ],
)
def test_def005_same_title_written_differently_still_matches(a, b):
    """These are the retry case — the same widget title, re-typed by the model."""
    assert _normalize(a) == _normalize(b)


@pytest.mark.parametrize(
    "a,b",
    [
        # A near miss must stay distinct: linking these would mark a REAL,
        # current result stale and hide it from the model and the rail.
        ("Total revenue", "Total revenue by segment"),
        ("Total revenue", "Total revenues"),
        ("Total revenue", "Total-revenue"),
        ("Total revenue", "Total_revenue"),
        ("Total revenue", "Total revenue."),
        ("Revenue (net)", "Revenue net"),
        ("Revenue 2024", "Revenue 2025"),
        ("Revenue", "Revenues"),
        ("Units sold", "Units returned"),
    ],
)
def test_def005_near_miss_titles_must_not_collide(a, b):
    assert _normalize(a) != _normalize(b)


def test_def005_normalize_never_raises_on_a_non_string():
    """Titles come off an ORM column; nothing guarantees a str upstream."""
    assert _normalize(42) == "42"
    assert _normalize(object()) != ""


# --- unit 2: what the model is actually told ----------------------------------

REPLACEMENT_ID = "viz-current-0002"
STALE_ID = "viz-stale-0001"


def _obs(query_id, title, superseded_by=None, superseded_at=None, superseded_reason=None):
    return QueryObservation(
        query_id=query_id,
        query_title=title,
        row_count=1,
        superseded_by=superseded_by,
        superseded_at=superseded_at,
        superseded_reason=superseded_reason,
    )


def _render(*items):
    return QueriesSection(items=list(items)).render()


SUPERSEDED = _obs(
    "q-stale",
    "Total revenue",
    superseded_by=REPLACEMENT_ID,
    superseded_at="2026-07-26T09:15:00+00:00",
    superseded_reason="Recomputed within the same turn",
)
CURRENT = _obs("q-current", "Total revenue")


def test_def005_superseded_entry_states_it_is_out_of_date():
    out = _render(SUPERSEDED)
    assert "<superseded" in out
    assert "OUT OF DATE" in out


def test_def005_superseded_entry_names_the_replacement():
    """Without the replacement id the model cannot tell WHICH result now holds."""
    out = _render(SUPERSEDED)
    assert REPLACEMENT_ID in out


def test_def005_superseded_entry_bans_blending_the_numbers():
    """The defect's damage was arithmetic across three contradictory results."""
    out = _render(SUPERSEDED)
    for banned in ("report", "total", "average", "reconcile"):
        assert banned in out, f"the instruction must forbid {banned!r}-ing stale numbers"
    assert "not a second valid answer" in out


def test_def005_superseded_entry_carries_when_and_why():
    out = _render(SUPERSEDED)
    assert "2026-07-26T09:15:00+00:00" in out
    assert "Recomputed within the same turn" in out


def test_def005_superseded_marker_survives_missing_at_and_reason():
    out = _render(_obs("q-stale", "Total revenue", superseded_by=REPLACEMENT_ID))
    assert "OUT OF DATE" in out
    assert REPLACEMENT_ID in out


def test_def005_superseded_entries_render_after_current_ones():
    out = _render(SUPERSEDED, CURRENT)
    assert out.index('id="q-current"') < out.index('id="q-stale"')


def test_def005_partition_is_stable_within_each_group():
    a, b = _obs("q-a", "A"), _obs("q-b", "B")
    stale = _obs("q-s", "S", superseded_by=REPLACEMENT_ID)
    out = _render(a, stale, b)
    assert out.index('id="q-a"') < out.index('id="q-b"') < out.index('id="q-s"')


def test_def005_the_three_way_defect_shape_renders_two_stale_one_current():
    """The reported shape: three same-title results, two of them dead."""
    out = _render(
        _obs("q-1", "Total revenue", superseded_by=REPLACEMENT_ID),
        _obs("q-2", "Total revenue", superseded_by=REPLACEMENT_ID),
        _obs("q-3", "Total revenue"),
    )
    assert out.count("OUT OF DATE") == 2
    assert out.index('id="q-3"') < out.index('id="q-1"')


def test_def005_supersede_marker_is_xml_escaped():
    out = _render(_obs("q-s", "T", superseded_by="a<b>&c", superseded_reason="x & y"))
    assert "a<b>&c" not in out
    assert "a&lt;b&gt;&amp;c" in out
    assert "x &amp; y" in out


# --- unit 2b: the normal path must not drift ---------------------------------
#
# Constructed explicitly rather than snapshotted: this is the byte-for-byte
# pre-fix output for an observation with no supersede keys, so any cosmetic
# change to the untouched path fails here loudly.

NORMAL_OBS = QueryObservation(
    query_id="q-1",
    query_title="Sales by region",
    default_step_id="s-1",
    default_step_title="Compute sales",
    row_count=3,
    column_names=["region", "amount"],
    data_model={"type": "table"},
    stats={"total_rows": 3},
    data_preview="region | amount",
    visualizations=[
        QueryVisualizationSummary(
            id="v-1", title="Sales by region", status="success", view={"type": "count"}
        )
    ],
)

EXPECTED_NORMAL = (
    "<queries>\n"
    '<query id="q-1" title="Sales by region">\n'
    '<step id="s-1">\n'
    "Compute sales\n"
    "</step>\n"
    "<rows>\n"
    "3\n"
    "</rows>\n"
    "<columns>\n"
    "region, amount\n"
    "</columns>\n"
    "<data_model>\n"
    "{'type': 'table'}\n"
    "</data_model>\n"
    "<stats>\n"
    "{'total_rows': 3}\n"
    "</stats>\n"
    "<data_preview>\n"
    "region | amount\n"
    "</data_preview>\n"
    "<visualizations>\n"
    '<visualization id="v-1" title="Sales by region">\n'
    "<status>\n"
    "success\n"
    "</status>\n"
    "<view>\n"
    "{'type': 'count'}\n"
    "</view>\n"
    "</visualization>\n"
    "</visualizations>\n"
    "</query>\n"
    "</queries>"
)


def test_def005_normal_path_is_byte_identical_to_the_pre_fix_renderer():
    """Passes on pre-fix code BY DESIGN — it pins that the fix changed nothing here."""
    assert _render(NORMAL_OBS) == EXPECTED_NORMAL


def test_def005_no_supersede_keys_means_no_marker_at_all():
    out = _render(NORMAL_OBS)
    assert "superseded" not in out
    assert "OUT OF DATE" not in out


def test_def005_empty_section_still_renders():
    assert _render() == "<queries>\n\n</queries>"


# --- unit 3: reading the stamp must never take down a turn --------------------


class _Viz:
    """Stand-in for a Visualization row: ``_supersede_info`` only getattrs ``view``."""

    def __init__(self, view):
        self.view = view


class _ExplodingViz:
    """A row whose ``view`` access itself fails (stale session, bad decode)."""

    @property
    def view(self):
        raise RuntimeError("view is unreadable")


class _NoViewAttr:
    """A row with no ``view`` attribute at all."""


EMPTY = {"superseded_by": None, "superseded_at": None, "superseded_reason": None}

STAMP = {
    "type": "count",
    "superseded_by": REPLACEMENT_ID,
    "superseded_at": "2026-07-26T09:15:00+00:00",
    "superseded_reason": "Recomputed within the same turn",
}


def test_def005_supersede_info_reads_all_three_keys():
    got = qcb._supersede_info([_Viz(dict(STAMP))])
    assert got == {
        "superseded_by": REPLACEMENT_ID,
        "superseded_at": "2026-07-26T09:15:00+00:00",
        "superseded_reason": "Recomputed within the same turn",
    }


def test_def005_supersede_info_needs_only_superseded_by():
    got = qcb._supersede_info([_Viz({"superseded_by": REPLACEMENT_ID})])
    assert got["superseded_by"] == REPLACEMENT_ID
    assert got["superseded_at"] is None and got["superseded_reason"] is None


def test_def005_supersede_info_reads_a_json_string_view():
    """``view`` comes back as a JSON string on some rows — the stamp is still there."""
    import json

    got = qcb._supersede_info([_Viz(json.dumps(STAMP))])
    assert got["superseded_by"] == REPLACEMENT_ID
    assert got["superseded_reason"] == "Recomputed within the same turn"


def test_def005_supersede_info_scans_past_unstamped_visualizations():
    vizes = [_Viz(None), _Viz({"type": "count"}), _Viz(dict(STAMP))]
    assert qcb._supersede_info(vizes)["superseded_by"] == REPLACEMENT_ID


def test_def005_supersede_info_stringifies_a_non_string_id():
    assert qcb._supersede_info([_Viz({"superseded_by": 12345})])["superseded_by"] == "12345"


@pytest.mark.parametrize(
    "view",
    [
        None,
        {},
        {"type": "count"},
        {"superseded_by": None},
        {"superseded_by": ""},
        {"superseded_by": False},
        {"superseded_by": 0},
        "not json at all",
        "{broken json",
        '"a bare json string"',
        "[]",
        "[1, 2, 3]",
        [],
        ["superseded_by"],
        42,
        3.14,
        True,
        b"\x00\x01binary",
        object(),
    ],
)
def test_def005_supersede_info_degrades_to_not_superseded(view):
    """Context assembly runs on EVERY turn — a throw here kills the whole turn."""
    assert qcb._supersede_info([_Viz(view)]) == EMPTY


@pytest.mark.parametrize(
    "vizes",
    [
        [],
        None,
        [_ExplodingViz()],
        [_NoViewAttr()],
        [_NoViewAttr(), _ExplodingViz(), _Viz(None)],
    ],
)
def test_def005_supersede_info_survives_a_hostile_viz_list(vizes):
    assert qcb._supersede_info(vizes) == EMPTY


def test_def005_an_exploding_row_does_not_hide_a_later_stamp_from_raising():
    """The guard is a whole-loop try/except: it must return, never propagate."""
    got = qcb._supersede_info([_ExplodingViz(), _Viz(dict(STAMP))])
    assert got == EMPTY  # scan aborts safely rather than throwing


# --- unit 3b: the stamp is actually wired into the observation ----------------


def _observation(view):
    """The real summary type here — the observation validates its own viz list."""
    builder = qcb.QueryContextBuilder(db=None, organization=None, report=None)
    return builder._build_query_observation(
        {"id": "q-1", "title": "Total revenue"},
        None,
        [QueryVisualizationSummary(id="v-1", title="Total revenue", status="success", view=view)],
        include_data_preview=False,
    )


def test_def005_observation_carries_the_stamp_from_its_visualization():
    obs = _observation(dict(STAMP))
    assert obs.superseded_by == REPLACEMENT_ID
    assert obs.superseded_at == "2026-07-26T09:15:00+00:00"
    assert obs.superseded_reason == "Recomputed within the same turn"


def test_def005_observation_without_a_stamp_is_all_none():
    obs = _observation({"type": "count"})
    assert (obs.superseded_by, obs.superseded_at, obs.superseded_reason) == (None, None, None)
