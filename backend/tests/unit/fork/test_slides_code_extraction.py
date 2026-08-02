"""A deck that was cut off and a deck that was written wrong are not the same.

Both used to arrive as ``invalid syntax (<string>, line 1)``.

The real one: a 13-slide deck, 37,720 characters of valid python-pptx, stopping
dead at ``for row in`` on line 881 because the model ran out of output tokens.
The extractor's fence patterns both required a CLOSING fence, which a truncated
response does not have, so the code came through cut mid-statement; and the
retry was then told to "fix that specific error and keep the structure the
same", which is an instruction to rebuild the deck at the length that had just
failed.
"""

from app.ai.tools.implementations.create_artifact import CreateArtifactTool

DECK = (
    "def generate_slides(visualizations, report):\n"
    "    prs = Presentation()\n"
    "    prs.save(_pptx_output_path)\n"
)
CUT_OFF = (
    "def generate_slides(visualizations, report):\n"
    "    prs = Presentation()\n"
    "    if rows:\n"
    "        for row in"
)


def _extract(response):
    return CreateArtifactTool._extract_slides_python(CreateArtifactTool, response)


def _problem(code):
    return CreateArtifactTool._slides_code_problem(code)


# ── pulling the code out of the reply ────────────────────────────────────────


def test_a_plain_python_fence_still_works():
    assert _extract(f"here you go\n```python\n{DECK}```\n") == DECK.strip()


def test_a_language_tag_is_not_left_on_line_one():
    """★The generic pattern was ```` ```\\s*([\\s\\S]*?)``` ````. `\\s*` does not
    match a word, so "py" became the first line of the "code" — and every
    such deck died as `invalid syntax (<string>, line 1)`."""
    for tag in ("py", "Python", "PYTHON", "python3"):
        code = _extract(f"```{tag}\n{DECK}```")
        assert code.startswith("def generate_slides"), f"{tag!r} leaked into the code"


def test_an_unclosed_fence_still_yields_its_code():
    """★A response cut off by the token limit has no closing fence. Both old
    patterns therefore missed it entirely."""
    code = _extract(f"```python\n{CUT_OFF}")

    assert code.startswith("def generate_slides")
    assert code.rstrip().endswith("for row in")


def test_no_fence_at_all_anchors_on_the_function():
    assert _extract(f"Sure!\n\n{DECK}").startswith("def generate_slides")


def test_prose_after_a_closed_fence_is_not_included():
    code = _extract(f"```python\n{DECK}```\nLet me know if you want changes!")

    assert "Let me know" not in code


# ── naming what is wrong, before running it ──────────────────────────────────


def test_good_code_has_no_problem():
    assert _problem(DECK) is None


def test_a_cut_off_deck_is_reported_as_a_length_problem():
    """★The distinction the whole file exists for. "invalid syntax at line 881"
    points at a line the model wrote correctly; the mistake is that there is no
    line 882."""
    problem = _problem(CUT_OFF)

    assert "CUT OFF" in problem
    assert "SHORTER" in problem
    assert "line 4" not in problem, "do not point at the cut as though it were the error"


def test_a_genuine_syntax_error_is_reported_as_one():
    broken = (
        "def generate_slides(visualizations, report):\n"
        "    prs = Presentation(\n"        # unclosed call
        "    prs.save(_pptx_output_path)\n"
    )
    problem = _problem(broken)

    assert "does not parse" in problem
    assert "CUT OFF" not in problem


def test_a_missing_function_says_so_rather_than_failing_at_call_time():
    problem = _problem("print('hello')\n")

    assert "generate_slides" in problem


def test_empty_code_is_named_not_executed():
    assert "empty" in _problem("   ").lower()


# ── the real artifact ────────────────────────────────────────────────────────


def test_the_deck_that_failed_in_production_is_diagnosed_correctly():
    """Artifact 397067b1, 2026-08-02 — 13 slides, 37,720 characters, ending at
    `for row in` on line 881. Reconstructed here at its essential shape."""
    real = (
        "def generate_slides(visualizations, report):\n"
        "    prs = Presentation()\n"
        "    prs.slide_width = Inches(13.333)\n"
        + "    # layout helper\n" * 800
        + "    lead_total = 1544\n"
        "    if r11:\n"
        "        for row in"
    )
    problem = _problem(real)

    assert "CUT OFF" in problem
    assert "prs.save" in problem
