"""PHASE 3 — a dashboard that does not parse was stored as a success.

Found by: a user, in the browser, after the run reported `success` and the
artifact was stored `completed`:

    Dashboard failed to render
    /Inline Babel script: Missing semicolon. (3:8)

The generated artifact is React/JSX inside `<script type="text/babel">`.
Nothing on the write path ever parsed it. The model's mistake therefore became
a stored record, and the only component that noticed was the one the user was
looking at.

DEF-008 added a structural guard (`_looks_like_component_code`) that catches
the unmistakable case: a reply that is one English sentence and no code. It
cannot catch malformed JSX, because it is a marker search — anything holding
`function`, `return` or a JSX tag passes it. `compile()` is no help either;
JSX is not Python.

The fix runs the SAME Babel the browser runs (`babel-standalone`, already
shipped in the image for the artifact sandbox) inside the Chromium already
present for PDF export. For parse errors this is not an approximation of the
render check — it is the identical operation, moved to before the write.

★ These tests LAUNCH A REAL CHROMIUM (~0.5s each). Exactly FOUR do, and they
are marked below. Do not parametrize them — a six-case parametrize is thirty
browser launches. Everything cheap (`_strip_script_tags`, empty input, the
fail-open path) is pure Python and touches no browser.

★ If Babel or Chromium is missing from the environment, `check_artifact_code`
fails OPEN by design, so the four rejection tests will FAIL rather than skip.
That is deliberate: a silent skip would let a preflight that cannot actually
parse anything ship looking green.

Contract these tests pin:
  * the real prose stub that prompted this is rejected, naming the semicolon
  * a reply that OPENS with a prose sentence and then contains real code is
    rejected — DEF-008's structural guard passes it, and only a real parse can
    tell the difference. This is the reason the phase exists on top of DEF-008
  * valid JSX with a hook and a JSX return is accepted
  * an unclosed JSX tag is rejected
  * empty or whitespace-only code is rejected without starting a browser
  * `_strip_script_tags` yields the inner JSX and leaves un-wrapped code alone
    (Babel transforms JavaScript, not HTML — handing it the wrapper would fail
    EVERY artifact)
  * ★ it FAILS OPEN. A missing Babel returns `(True, None)` even for code that
    provably does not parse. This matters more than any rejection case: a
    broken preflight must never block a dashboard that would have rendered
    perfectly well
"""
import pytest

from app.services.artifact_preflight import (
    _find_babel,
    _strip_script_tags,
    check_artifact_code,
)

# Imported defensively so this suite FAILS on pre-fix code rather than erroring
# out at collection — a collection error proves nothing about behavior.
try:
    from app.ai.tools.implementations.create_artifact import _looks_like_component_code

    DEF008_GUARD_PRESENT = True
except ImportError:  # pragma: no cover - pre-DEF-008 code path
    DEF008_GUARD_PRESENT = False
    _looks_like_component_code = None


# --- the artifacts under test ------------------------------------------------

# Verbatim, as the model emitted it. `Creating the` is two identifiers in a row,
# which is where Babel reports the missing semicolon the user saw.
PROSE_STUB = """<script type="text/babel">
function App() {
Creating the full-totals CFC Sales Dashboard from the compact multi-section data.
}
ReactDOM.createRoot(document.getElementById('root')).render(<App />);
</script>"""

# Observed live: the reply OPENS with a sentence of monologue and then contains
# a genuine, complete component. DEF-008's guard sees `function` and `return`
# and lets it through; the browser then dies on the first line.
PROSE_PREFIX_THEN_CODE = """<script type="text/babel">
Building the interactive CFC sales dashboard from the pre-aggregated dataset,
starting with the branch totals and then the monthly trend.
function App() {
  const [tab, setTab] = React.useState('branches');
  return (
    <div className="dashboard">
      <h1>Sales</h1>
      <button onClick={() => setTab('months')}>Months</button>
    </div>
  );
}
ReactDOM.createRoot(document.getElementById('root')).render(<App />);
</script>"""

VALID_DASHBOARD = """<script type="text/babel">
const { useState } = React;

function App() {
  const [tab, setTab] = useState('branches');
  const rows = [
    { name: 'North', total: 41200 },
    { name: 'South', total: 38940 },
  ];
  return (
    <div className="dashboard">
      <h1>Sales by branch</h1>
      <button onClick={() => setTab('months')}>Months</button>
      {tab === 'branches' ? (
        <ul>
          {rows.map((r) => (
            <li key={r.name}>{r.name}: {r.total}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
</script>"""

UNCLOSED_JSX_TAG = """<script type="text/babel">
function App() {
  return (
    <div className="dashboard">
      <h1>Sales by branch</h1>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
</script>"""


# =============================================================================
# 1. cheap cases — pure Python, NO browser
# =============================================================================


def test_phase3_strip_script_tags_returns_the_inner_jsx():
    inner = _strip_script_tags(VALID_DASHBOARD)
    assert "<script" not in inner
    assert "</script>" not in inner
    assert inner.startswith("const { useState } = React;")
    assert inner.rstrip().endswith("render(<App />);")


def test_phase3_strip_script_tags_leaves_unwrapped_code_alone():
    """Not every caller wraps. Un-wrapped code must pass through untouched."""
    bare = "function App() { return <div>hi</div>; }"
    assert _strip_script_tags(bare) == bare


def test_phase3_strip_script_tags_accepts_single_quotes_and_odd_case():
    code = "<SCRIPT TYPE='text/babel'>\nfunction App() { return null; }\n</SCRIPT>"
    assert _strip_script_tags(code).strip() == "function App() { return null; }"


def test_phase3_strip_script_tags_survives_empty_input():
    assert _strip_script_tags("") == ""


@pytest.mark.asyncio
async def test_phase3_empty_code_is_rejected(monkeypatch):
    """Nothing to parse is a failure, and it must not cost a browser launch."""

    def _explode():
        raise AssertionError("empty code must be rejected before touching Babel")

    monkeypatch.setattr("app.services.artifact_preflight._find_babel", _explode)

    ok, message = await check_artifact_code("")
    assert ok is False
    assert message


@pytest.mark.asyncio
async def test_phase3_whitespace_only_code_is_rejected(monkeypatch):
    def _explode():
        raise AssertionError("whitespace-only code must be rejected before Babel")

    monkeypatch.setattr("app.services.artifact_preflight._find_babel", _explode)

    ok, message = await check_artifact_code("   \n\t  \n")
    assert ok is False
    assert message


@pytest.mark.asyncio
async def test_phase3_empty_script_wrapper_is_rejected(monkeypatch):
    """The wrapper is present but holds nothing — still nothing to render."""

    def _explode():
        raise AssertionError("empty wrapper must be rejected before Babel")

    monkeypatch.setattr("app.services.artifact_preflight._find_babel", _explode)

    ok, _ = await check_artifact_code('<script type="text/babel">\n\n</script>')
    assert ok is False


def test_phase3_find_babel_returns_a_real_file_or_none():
    """Never a path that does not exist — the caller treats a path as usable."""
    import os

    found = _find_babel()
    assert found is None or os.path.isfile(found)


# =============================================================================
# 2. ★ FAIL OPEN — the most important contract here. No browser.
# =============================================================================


@pytest.mark.asyncio
async def test_phase3_missing_babel_allows_the_artifact(monkeypatch):
    monkeypatch.setattr("app.services.artifact_preflight._find_babel", lambda: None)

    assert await check_artifact_code(VALID_DASHBOARD) == (True, None)


@pytest.mark.asyncio
async def test_phase3_missing_babel_allows_even_unparseable_code(monkeypatch):
    """★ The one that matters.

    With no Babel the check cannot know anything, so it must claim nothing —
    even about the exact stub that broke a user's dashboard. A preflight that
    guessed here would block good dashboards on a broken deployment.
    """
    monkeypatch.setattr("app.services.artifact_preflight._find_babel", lambda: None)

    assert await check_artifact_code(PROSE_STUB) == (True, None)


@pytest.mark.asyncio
async def test_phase3_missing_babel_does_not_swallow_the_empty_case(monkeypatch):
    """Failing open is about the CHECK failing, not about accepting nothing."""
    monkeypatch.setattr("app.services.artifact_preflight._find_babel", lambda: None)

    ok, _ = await check_artifact_code("")
    assert ok is False


# =============================================================================
# 3. ★ THE FOUR BROWSER TESTS. Each launches Chromium. Do not parametrize.
# =============================================================================


@pytest.mark.asyncio
async def test_phase3_real_prose_stub_is_rejected():
    """★ browser. The artifact the user met as "Missing semicolon. (3:8)"."""
    ok, message = await check_artifact_code(PROSE_STUB)

    assert ok is False
    assert message
    assert "semicolon" in message.lower(), message


@pytest.mark.asyncio
async def test_phase3_prose_prefix_with_real_code_is_rejected():
    """★ browser. The case DEF-008's structural guard MISSES.

    This is why the phase exists on top of DEF-008: the reply contains real
    component code, so every marker search passes it, and only an actual parse
    sees that the monologue in front of it is a syntax error.
    """
    if DEF008_GUARD_PRESENT:
        inner = _strip_script_tags(PROSE_PREFIX_THEN_CODE)
        assert _looks_like_component_code(inner) is True, (
            "fixture no longer demonstrates the gap — DEF-008's guard now "
            "rejects it, so this test would pass for the wrong reason"
        )

    ok, message = await check_artifact_code(PROSE_PREFIX_THEN_CODE)

    assert ok is False
    assert message


@pytest.mark.asyncio
async def test_phase3_valid_dashboard_is_accepted():
    """★ browser. The control: a real dashboard must sail through."""
    assert await check_artifact_code(VALID_DASHBOARD) == (True, None)


@pytest.mark.asyncio
async def test_phase3_unclosed_jsx_tag_is_rejected():
    """★ browser. Malformed JSX — invisible to any marker-based guard."""
    ok, message = await check_artifact_code(UNCLOSED_JSX_TAG)

    assert ok is False
    assert message
