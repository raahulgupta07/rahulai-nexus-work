"""The narrative section's absent and malformed cases.

★An empty shell under a dashboard is worse than no section: a heading that
says "What this means" over nothing reads as a failed analysis rather than an
artifact that never had one. Every artifact generated before this feature
existed carries no payload at all, and they are the majority — so the absent
case is the COMMON case, not the edge one.

This file exists because `insights_section_html` had no unit coverage of any
kind. The contract lived in its docstring and in a live render of the handful
of artifacts that happen to be in the dev database, none of which carry a
malformed payload.
"""
from __future__ import annotations

import pytest

from app.services.artifact_insights_html import insights_section_html


# ═══════════════════════════════════════════════════════════════════════════
# Nothing to say → nothing rendered
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("payload", [
    None,
    {},
    {"headline": ""},
    {"headline": "   "},
    {"findings": []},
    {"findings": None},
    # ★Findings present but every one of them blank. The filter is on the
    # TEXT, not on the list, so a list of empty dicts must still come out
    # empty rather than rendering three bullet-less <li>s.
    {"findings": [{"text": ""}, {"text": "   "}, {}]},
    {"headline": "", "findings": [{"text": ""}]},
    # ★Wrong types must not raise. This payload is model-adjacent: it arrives
    # as JSON from a column, and a shape change upstream must degrade to "no
    # section", never to a 500 on the dashboard render.
    "a string",
    ["a", "list"],
    42,
    {"findings": "not a list"},
    {"findings": ["not a dict"]},
])
def test_renders_nothing_when_there_is_nothing_to_say(payload):
    assert insights_section_html(payload) == ""


def test_renders_nothing_rather_than_an_empty_shell():
    """The specific failure this guards: markup with a label and no content."""
    out = insights_section_html({"headline": "", "findings": [{"text": ""}]})
    assert "What this means" not in out
    assert "artifact-insights" not in out


# ═══════════════════════════════════════════════════════════════════════════
# Something to say → exactly that much
# ═══════════════════════════════════════════════════════════════════════════

def test_a_headline_alone_renders_without_an_empty_list():
    out = insights_section_html({"headline": "Sales fell 12%."})
    assert "Sales fell 12%." in out
    assert "bow-insight-list" not in out, "empty <ul> rendered under a lone headline"


def test_findings_alone_render_without_an_empty_headline():
    out = insights_section_html({"findings": [{"text": "Q3 was flat."}]})
    assert "Q3 was flat." in out
    assert "bow-insight-headline" not in out, "empty headline <p> rendered"


def test_blank_findings_are_dropped_but_the_rest_survive():
    out = insights_section_html({"findings": [
        {"text": "kept one"}, {"text": "  "}, {"text": "kept two"},
    ]})
    assert out.count("<li>") == 2
    assert "kept one" in out and "kept two" in out


# ═══════════════════════════════════════════════════════════════════════════
# Source chips
# ═══════════════════════════════════════════════════════════════════════════

def test_a_finding_cites_the_chart_it_came_from():
    out = insights_section_html(
        {"findings": [{"text": "Revenue rose.", "viz_id": "v1"}]},
        [{"id": "v1", "title": "Revenue by month"}],
    )
    assert "Revenue by month" in out


def test_an_unresolvable_viz_id_renders_no_chip_rather_than_a_blank_one():
    out = insights_section_html(
        {"findings": [{"text": "Revenue rose.", "viz_id": "gone"}]},
        [{"id": "v1", "title": "Revenue by month"}],
    )
    assert "Revenue rose." in out
    assert "bow-insight-src" not in out


# ═══════════════════════════════════════════════════════════════════════════
# Dropped findings are stated, not hidden
# ═══════════════════════════════════════════════════════════════════════════

def test_rejected_findings_are_reported():
    """★A narrative that lost four of five points is a warning, not a short one.

    A reader who cannot see the count has no way to tell a thin section from a
    verified one.
    """
    out = insights_section_html({"findings": [{"text": "one"}], "rejected_count": 4})
    assert "4 finding(s) were dropped" in out


@pytest.mark.parametrize("count", [0, None, False, "3"])
def test_no_rejection_notice_when_nothing_was_rejected(count):
    out = insights_section_html({"findings": [{"text": "one"}], "rejected_count": count})
    assert "dropped for citing" not in out


# ═══════════════════════════════════════════════════════════════════════════
# Escaping
# ═══════════════════════════════════════════════════════════════════════════

def test_model_written_text_cannot_close_the_section_or_open_a_script():
    """★This text is model-written and lands in a document that also RUNS
    model-written code. An unescaped finding could close the section and open
    a script tag."""
    nasty = '</section><script>window.__pwned=1</script>'
    out = insights_section_html({"headline": nasty, "findings": [{"text": nasty}]})
    assert "<script>" not in out
    assert "</section><script>" not in out
    assert "&lt;/section&gt;" in out


def test_a_source_title_is_escaped_too():
    out = insights_section_html(
        {"findings": [{"text": "ok", "viz_id": "v1"}]},
        [{"id": "v1", "title": '<img src=x onerror=alert(1)>'}],
    )
    assert "<img" not in out
    assert "&lt;img" in out
