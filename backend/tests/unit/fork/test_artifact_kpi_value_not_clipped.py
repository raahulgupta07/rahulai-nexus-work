"""A metric tile must never cut a value in half.

THE DEFECT
----------
A dashboard KPI card drew `105,150,299,75` where the stored value was
`105,150,299,753`. Nothing threw, nothing was logged, no request failed, the
card looked immaculate — the user simply read a number that was off by a factor
of ten and had no way to tell. Measured in a real browser inside the artifact
frame:

    <div class="mt-1 text-2xl md:text-3xl font-semibold tabular-nums">
    text="105,150,299,753"  needs 285px, has 222px  (font-size 30px)

★WHY IT KEEPS COMING BACK, AND WHY THESE TESTS GUARD A RULE INSTEAD OF A FILE.
Dashboard cards are not a Vue component. They are LLM-generated JSX executed in
the artifact sandbox iframe, and the model writes the card markup fresh every
time. The first attempt at this fix hardened
`frontend/components/dashboard/MetricCard.vue` — a real component, correctly
fixed, and completely uninvolved in drawing a dashboard KPI. Nothing changed on
screen. The only durable fix is a shared helper in the sandbox RUNTIME that the
codegen prompt tells the model to use, so the tests here assert exactly that
pairing:

  1. the runtime ships a metric tile that fits its value by MEASUREMENT;
  2. the codegen prompt advertises it and forbids the hand-rolled version;
  3. the browser gate can still SEE a clipped value.

★Measurement, not character counts. A previous helper scaled the type off
`String(value).length`. Twenty digits and twenty capital letters are not the
same width, and the same value fits a wide card and not a narrow one — so the
thresholds are right for one card and wrong for the next. The helper asks the
browser how wide the text actually came out and scales by that ratio.

The browser-side proof lives in `backend/scripts/browser_smoke.py`
(`--self-test` stage 3): hand-rolled fixed-size tiles MUST be flagged, the same
values through the helper MUST come back clean. It needs chromium and a live
server, so it cannot run in this suite — which is why the last test below
guards that the assertion is still in that file at all.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
RUNTIME = REPO / "frontend" / "public" / "libs" / "artifact-globals.js"
SMOKE = REPO / "backend" / "scripts" / "browser_smoke.py"
AI_DIR = REPO / "backend" / "app" / "ai"

# The helper the runtime must expose. `BowKpi` is the tile; `BowFitText` is the
# value-fitting primitive, needed on its own for designs that build custom card
# chrome but must still not clip the number.
TILE = "BowKpi"
FITTER = "BowFitText"


def _strip_js_comments(src: str) -> str:
    """The rule is DOCUMENTED in comments that name the very identifiers being
    searched for, so an unstripped search matches the explanation and passes on
    a broken file. This trap has bitten repeatedly in this repo."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(?<!:)//[^\n]*", "", src)
    return src


def _runtime() -> str:
    return _strip_js_comments(RUNTIME.read_text())


def _global_body(src: str, name: str) -> str:
    """The source of `window.<name> = function(...) { … }`, brace-matched."""
    m = re.search(r"window\.%s\s*=\s*function\s*\([^)]*\)\s*\{" % re.escape(name), src)
    if not m:
        return ""
    depth, i = 0, m.end() - 1
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.end():i]
        i += 1
    return src[m.end():]


# --- guard the guard ---------------------------------------------------------

def test_the_files_this_guards_actually_exist():
    """A wrong path makes every assertion below pass against nothing."""
    for p in (RUNTIME, SMOKE):
        assert p.is_file(), f"not found: {p}"
    assert AI_DIR.is_dir(), f"not found: {AI_DIR}"


# --- 1. the runtime ships a measuring metric tile -----------------------------

def test_the_runtime_exposes_a_shared_metric_tile_and_value_fitter():
    src = _runtime()
    for name in (TILE, FITTER):
        assert f"window.{name} =" in src, (
            f"{name} is gone from artifact-globals.js. Generated dashboards then "
            "have no KPI helper at all and go back to hand-rolling a fixed-size "
            "<div>, which clips long values silently."
        )


def test_the_value_fitter_measures_the_browser_instead_of_counting_characters():
    """★The rule. A helper that guesses from string length is right for one card
    width and wrong for the next; only the browser knows how wide the text came
    out in the box it was actually given."""
    src = _runtime()
    fit_region = src[src.index("function _fitMeasure"):] if "_fitMeasure" in src else ""
    assert fit_region, "the measuring routine (_fitMeasure) is gone from the runtime"
    body = _global_body(src, FITTER) + fit_region[:2000]
    assert "scrollWidth" in body and "clientWidth" in body, (
        f"{FITTER} no longer compares the text's rendered width (scrollWidth) "
        "against the box it has (clientWidth) — it is guessing again, and a "
        "guess loses digits on some card width"
    )
    assert "ResizeObserver" in body, (
        f"{FITTER} does not re-fit on resize. A card is a grid child: its width "
        "changes with the viewport, and a value fitted to the old width is "
        "clipped at the new one"
    )


def test_the_fitted_value_shrinks_but_then_wraps_rather_than_vanishing():
    """Shrinking without a floor produces an unreadable value; shrinking with a
    floor and no wrap produces a clipped one. Both are required."""
    src = _runtime()
    body = _global_body(src, FITTER)
    assert "minRem" in body, f"{FITTER} lost its lower bound on type size"
    assert "wrap" in body, (
        f"{FITTER} no longer falls back to wrapping. Past the smallest legible "
        "size the value must go onto a second line — an extra line is "
        "acceptable, losing characters never is"
    )


def test_the_metric_tile_can_shrink_inside_a_grid():
    """`min-width:auto` is the flex/grid default: a child refuses to shrink below
    its content, so the tile reports the OVERFLOWING width as available and no
    amount of fitting helps. `min-w-0` is what makes the measurement true."""
    body = _global_body(_runtime(), TILE) + _global_body(_runtime(), FITTER)
    assert "min-w-0" in body, (
        f"{TILE}/{FITTER} lost min-w-0 — inside a grid the tile will not shrink, "
        "the measured 'available' width is the overflowing one, and the value "
        "is clipped anyway"
    )


def test_no_metric_tile_in_the_runtime_renders_its_value_at_a_fixed_size():
    """★The general rule, swept rather than listed. ANY metric-tile component in
    the sandbox runtime — today BowKpi and the KPICard alias, tomorrow whatever
    a redesign adds — must put its value through the fitter. A fixed
    `text-3xl` on a value is the defect itself.
    """
    src = _runtime()
    offenders = []
    for m in re.finditer(r"window\.(\w*(?:Kpi|KPI|Metric|Stat)\w*)\s*=\s*function", src):
        name = m.group(1)
        body = _global_body(src, name)
        if "props.value" not in body:
            continue  # not a value-rendering tile
        if FITTER not in body:
            offenders.append(f"{name}: renders props.value without {FITTER}")
        bad = re.findall(r"text-(?:xl|\d+xl)", body)
        if bad:
            offenders.append(f"{name}: hard-coded type size(s) {sorted(set(bad))}")
    assert not offenders, (
        "a metric tile renders its value at a size it chose in advance. A card "
        "is a fixed box and a value is arbitrarily long, so this clips — "
        f"silently — for some value on some screen:\n  " + "\n  ".join(offenders)
    )


def test_the_legacy_kpicard_name_gets_the_fix_too():
    """Every dashboard generated before this change calls `KPICard`. If the name
    is left pointing at the old fixed-size implementation, all of them stay
    broken and only newly generated ones are fixed."""
    src = _runtime()
    alias = re.search(r"window\.KPICard\s*=\s*window\.(\w+)\s*;", src)
    body = _global_body(src, "KPICard")
    assert (alias and alias.group(1) == TILE) or (body and FITTER in body), (
        "KPICard neither aliases " + TILE + " nor uses " + FITTER + " — already "
        "stored artifacts (which all use the KPICard name) keep clipping"
    )


# --- 2. codegen is pointed at it ----------------------------------------------

def _prompt_files_documenting_the_sandbox_globals():
    """Derived, not listed. Any prompt that teaches the model the sandbox
    surface is a place the model learns to hand-roll a tile — and a second copy
    of such a prompt is exactly the thing a file-by-file fix misses."""
    out = []
    for p in AI_DIR.rglob("*.py"):
        if ".bak-" in p.name or not p.is_file():
            continue
        body = p.read_text(errors="ignore")
        if "<SectionCard" in body or "KPICard" in body:
            out.append((p, body))
    return out


def test_every_sandbox_prompt_advertises_the_metric_tile():
    pages = _prompt_files_documenting_the_sandbox_globals()
    assert pages, (
        "no prompt file documents the sandbox globals any more — the search "
        "marker went stale and the assertion below would check nothing"
    )
    offenders = [
        str(p.relative_to(REPO)) for p, body in pages if TILE not in body
    ]
    assert not offenders, (
        f"these prompts describe the sandbox runtime without mentioning <{TILE}>. "
        "The model will keep writing its own fixed-size KPI div, and long values "
        "will keep being clipped:\n  " + "\n  ".join(offenders)
    )


def test_the_prompt_tells_the_model_not_to_hand_roll_a_tile():
    """Advertising the helper is not enough — the model reached for a raw
    `<div className="text-3xl">` even with `<KPICard>` documented. The
    prohibition has to be explicit."""
    body = (
        REPO / "backend" / "app" / "ai" / "tools" / "implementations"
        / "_sandbox_context.py"
    ).read_text()
    prohibition = re.search(
        r"NEVER hand-roll[^\n]*", body
    )
    assert prohibition, (
        "_sandbox_context.py no longer forbids hand-rolling a metric tile at a "
        "fixed type size — that instruction is what stops the model from "
        "re-creating the clipped card"
    )
    assert FITTER in body, (
        "the prompt offers no way to render a value safely inside CUSTOM tile "
        f"markup; {FITTER} must be documented or a custom design has no option "
        "but the clipping one"
    )


# --- 3. the browser gate can still see it -------------------------------------

def test_the_browser_gate_still_asserts_that_text_fits_its_box():
    """★Guard the guard. Everything above is source-level: the only thing that
    can observe a real clip is the browser gate, and a future edit could quietly
    drop the check while every test here stayed green."""
    body = SMOKE.read_text()
    assert "scrollWidth" in body and "clientWidth" in body, (
        "browser_smoke.py no longer measures whether rendered text fits its box. "
        "That comparison is the only thing in the codebase that can observe a "
        "clipped value — nothing throws and nothing is logged when it happens"
    )
    assert "_frame_overflows" in body and "res.fail(_describe_overflow" in body, (
        "the overflow probe is no longer wired into a gate FAILURE. A check "
        "whose result is not asserted on is not a check"
    )
    assert "frame.evaluate" in body, (
        "the overflow probe must run INSIDE the artifact frame. The frame is "
        "sandboxed without allow-same-origin, so it is an opaque origin and "
        "iframe.contentDocument from the host is a SecurityError — a host-side "
        "measurement cannot work at all"
    )


def test_the_browser_gate_proves_both_directions():
    """A guard that has never caught anything is not a guard. The self-test must
    keep a negative control (a hand-rolled tile that MUST be flagged) alongside
    the helper case (which must come back clean) — otherwise 'zero clipped
    elements' could mean 'the probe is broken' or 'the fixture rendered
    nothing'."""
    body = SMOKE.read_text()
    assert "self_test_clipping" in body, "the clipping self-test stage is gone"
    assert "_CLIPPING_BODY" in body and "_FIXED_BODY" in body, (
        "the clipping self-test lost one of its two directions — it can no "
        "longer distinguish a working probe from a blind one"
    )
    assert "_FIXTURE_VALUES" in body, (
        "the self-test no longer asserts the values are actually present in the "
        "rendered frame. A tile that drew nothing at all also reports zero "
        "clipped elements"
    )
