"""A description of a dashboard must not be stored as one.

`_looks_like_component_code` is the gate that catches a page-mode reply which
described the work instead of doing it (DEF-008). It decides three things: it
triggers the one strict retry, it decides whether that retry is accepted, and
it stops `_ensure_app_wrapper` from putting `function App() { ... }` around an
English sentence — a syntactically perfect shell around a syntactically
impossible body, which stored `completed` and reached the user as "Dashboard
failed to render: Missing semicolon (3:8)".

It was a substring scan over a marker list:

    ("return", "<", "const ", "let ", "var ", "function ", "=>",
     "useArtifactData")

★Three of those match ordinary English. "I'll **return** the top 5 banners"
has `return`. "revenue **<** 1M" has `<`. "create a **function that**
aggregates" has `function `. So the gate the whole DEF-008 recovery hangs on
passed the exact replies it exists to catch, and the retry never ran for them.

The five samples in `fixtures/artifact_prose_replies.txt` are in the register
the model actually writes in. Three of them passed.

★The gate must stay generous in the other direction. It runs on genuine output
too, and a false *negative* costs a wasted LLM round-trip and can refuse a real
component — so every code sample below has to keep passing. Where the two
pressures meet, this errs toward accepting code: a lone JSX tag is enough on
its own, because a real component that opens a tag is far more likely than
prose that does.
"""
import pathlib

import pytest

from app.ai.tools.implementations.create_artifact import _looks_like_component_code


FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "artifact_prose_replies.txt"


def _prose_samples():
    text = FIXTURE.read_text(encoding="utf-8")
    # Everything before the first separator is the file's own header.
    return [block.strip() for block in text.split("=====")[1:] if block.strip()]


PROSE = _prose_samples()

# Real page-mode output, in the shapes the generator emits.
CODE = {
    "full component": (
        "function App() {\n"
        "  const rows = useArtifactData();\n"
        "  return <div className=\"p-4\">{rows.length}</div>;\n"
        "}"
    ),
    "body only, as _ensure_app_wrapper receives it": (
        "const rows = useArtifactData();\n"
        "return <BarChart data={rows} />;"
    ),
    "arrow component": "const App = () => <div>hi</div>;",
    "bare jsx return": "return <div/>",
    "hook call only": "useArtifactData()",
    "no jsx at all": (
        "function App() {\n"
        "  const total = rows.reduce((a, b) => a + b.net, 0);\n"
        "  return React.createElement('div', null, total);\n"
        "}"
    ),
}


def test_the_fixture_has_the_five_samples():
    assert len(PROSE) == 5


# --- the defect -------------------------------------------------------------

@pytest.mark.parametrize("sample", PROSE, ids=range(len(PROSE)))
def test_prose_is_rejected(sample):
    """★The whole point. Every one of these is a description, not a component."""
    assert _looks_like_component_code(sample) is False, (
        f"prose accepted as component code: {sample[:60]!r}"
    )


def test_the_specific_english_words_that_used_to_pass():
    """★Named so a future edit that reinstates a bare substring scan fails here
    with the reason rather than somewhere downstream with a parser error."""
    assert not _looks_like_component_code("I'll return the top 5 banners.")
    assert not _looks_like_component_code("Revenue < 1M shows in red.")
    assert not _looks_like_component_code("I will create a function that aggregates sales.")
    assert not _looks_like_component_code("Use const values for the thresholds.")
    assert not _looks_like_component_code("Let me export this as a chart.")


# --- what must NOT change ---------------------------------------------------

@pytest.mark.parametrize("name", list(CODE))
def test_real_component_code_is_still_accepted(name):
    """A false negative burns a round-trip and can refuse a valid dashboard."""
    assert _looks_like_component_code(CODE[name]) is True, (
        f"genuine code rejected: {name}"
    )


def test_empty_and_whitespace_are_not_code():
    assert not _looks_like_component_code("")
    assert not _looks_like_component_code("   \n\t ")
    assert not _looks_like_component_code(None)


def test_prose_wrapped_around_real_code_is_accepted():
    """The generator often prefixes a sentence. The code is what matters — the
    extractor handles the prose, this gate only asks whether code is present."""
    assert _looks_like_component_code(
        "Here is the dashboard you asked for:\n\n"
        "function App() { return <div>ok</div>; }"
    )


def test_a_sentence_that_merely_mentions_a_tag_is_still_prose_shaped():
    """★Documenting a known limit rather than claiming it is solved: a lone JSX
    tag is accepted on its own, so a sentence naming a component passes. That
    is the deliberate direction of the error — accepting prose costs one render
    retry, refusing real code costs the user their dashboard."""
    assert _looks_like_component_code("I'll use the <Chart> component here.")


def test_the_gate_is_not_a_substring_scan_any_more():
    """★Guard the guard. The defect was the *method*, not the marker list —
    a scan for bare words over free text cannot separate the two, so no edit
    should be able to quietly restore one."""
    import inspect
    source = inspect.getsource(_looks_like_component_code)
    assert "any(marker in inner" not in source, (
        "the substring scan is back; it cannot tell 'return the top 5' from code"
    )
