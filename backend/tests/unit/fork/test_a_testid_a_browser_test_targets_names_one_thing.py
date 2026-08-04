"""A `data-testid` that a browser test targets must name exactly one component.

★Written from a near-miss, not from theory. `data-testid="agent-picker"` was
added to `DataSourceSelector.vue` as the anchor for the 0.0.518.1 regression
tests — and upstream already had that exact string on an unrelated dropdown
panel in `pages/projects/[id]/index.vue`. Two different components, one name,
three specs locating it with `.first()`.

It passed. The home page does not mount the projects page, so `.first()` found
the right element every time. That is the whole danger: a locator that is
correct by luck reads identically to one that is correct by construction, and
the luck runs out the first time both components share a route.

This is the third time in one session that a locator matched something other
than what it named. The first matched the text "Auto", which the *model* picker
beside it also renders, and passed against a build where the component under
test was entirely absent. Same defect class, different mechanism: the assertion
was not anchored to one thing.

★NOT a blanket uniqueness rule. `steering-badge` is deliberately on three
elements — one component rendered in three places is a legitimate shared id, and
failing it would teach people to weaken this test. The rule is narrower and
sharper: if a browser spec *targets* an id, that id must resolve to one source
file, because that is the only case where ambiguity silently changes what is
being asserted.
"""
import re
from collections import defaultdict
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
FRONTEND = REPO / "frontend"
SPECS = FRONTEND / "tests"

# Where a testid may be DECLARED. `tests/` is excluded on purpose — a spec
# referencing an id is not a second declaration of it.
SOURCE_DIRS = ("components", "pages", "layouts", "ee", "app")

DECLARED = re.compile(r'data-testid="([^"]+)"')
# How a spec names one: `[data-testid="x"]` in a locator, or getByTestId('x').
TARGETED = re.compile(r"""\[data-testid=["']([^"']+)["']\]|getByTestId\(\s*["']([^"']+)["']""")


def _source_files():
    for d in SOURCE_DIRS:
        root = FRONTEND / d
        if root.is_dir():
            yield from (p for p in root.rglob("*.vue") if p.is_file())


def _rel(p: Path) -> str:
    """Repo-relative where we can — the self-test points FRONTEND at a tmpdir."""
    try:
        return str(p.relative_to(REPO))
    except ValueError:
        return str(p)


def _declarations():
    """testid -> set of source files declaring it."""
    out = defaultdict(set)
    for f in _source_files():
        for tid in DECLARED.findall(f.read_text(encoding="utf-8", errors="replace")):
            out[tid].add(f)
    return out


def _targets():
    """testid -> set of spec files targeting it."""
    out = defaultdict(set)
    if not SPECS.is_dir():
        return out
    for f in SPECS.rglob("*.spec.ts"):
        for a, b in TARGETED.findall(f.read_text(encoding="utf-8", errors="replace")):
            out[a or b].add(f)
    return out


def test_the_frontend_tree_is_actually_there():
    # ★Without this the scan below silently passes on an empty tree, which is
    # exactly how a guard stops guarding. See CLAUDE.md on the `/src` runner:
    # inside `dash-app` there is no frontend source at all.
    assert FRONTEND.is_dir(), f"no frontend tree at {FRONTEND} — run this on the /src runner"
    assert any(_source_files()), "found no .vue files to scan"


def test_every_testid_a_spec_targets_resolves_to_one_component():
    declared = _declarations()
    targeted = _targets()
    if not targeted:
        pytest.skip("no browser specs target a data-testid yet")

    ambiguous = []
    missing = []
    for tid, specs in sorted(targeted.items()):
        files = declared.get(tid, set())
        where = ", ".join(sorted(_rel(s) for s in specs))
        if not files:
            missing.append(f"  {tid!r} targeted by {where} — no component declares it")
        elif len(files) > 1:
            owners = ", ".join(sorted(_rel(f) for f in files))
            ambiguous.append(f"  {tid!r} targeted by {where}\n      declared by: {owners}")

    assert not ambiguous, (
        "a browser test targets a data-testid that more than one component declares.\n"
        "`.first()` will pick whichever mounts first, so the assertion silently\n"
        "changes meaning depending on the route. Rename one of them:\n\n"
        + "\n".join(ambiguous)
    )
    assert not missing, (
        "a browser test targets a data-testid that no component declares — the\n"
        "locator can only ever find nothing, and the test fails for a reason that\n"
        "has nothing to do with the product:\n\n" + "\n".join(missing)
    )


def test_the_scanner_still_recognises_the_collision_it_was_written_for(tmp_path, monkeypatch):
    """The real 0.0.518.2 near-miss, reconstructed."""
    fe = tmp_path / "frontend"
    (fe / "components").mkdir(parents=True)
    (fe / "pages").mkdir(parents=True)
    (fe / "tests").mkdir(parents=True)
    (fe / "components" / "Picker.vue").write_text('<div data-testid="agent-picker"></div>')
    (fe / "pages" / "project.vue").write_text('<div data-testid="agent-picker"></div>')
    (fe / "tests" / "home.spec.ts").write_text(
        'page.locator(\'[data-testid="agent-picker"]\').first()'
    )
    monkeypatch.setattr(f"{__name__}.FRONTEND", fe)
    monkeypatch.setattr(f"{__name__}.SPECS", fe / "tests")

    with pytest.raises(AssertionError) as e:
        test_every_testid_a_spec_targets_resolves_to_one_component()
    assert "agent-picker" in str(e.value)
    assert "Picker.vue" in str(e.value) and "project.vue" in str(e.value)


def test_a_shared_id_no_spec_targets_is_not_a_finding(tmp_path, monkeypatch):
    """★`steering-badge` sits on three elements and always has. One component
    rendered three times is not ambiguity — only a spec targeting it makes the
    duplication matter. A guard that fails here would be rewritten to pass."""
    fe = tmp_path / "frontend"
    (fe / "components").mkdir(parents=True)
    (fe / "tests").mkdir(parents=True)
    for n in ("A", "B", "C"):
        (fe / "components" / f"{n}.vue").write_text('<span data-testid="steering-badge"></span>')
    (fe / "tests" / "home.spec.ts").write_text('page.locator(\'[data-testid="something-else"]\')')
    (fe / "components" / "Else.vue").write_text('<div data-testid="something-else"></div>')
    monkeypatch.setattr(f"{__name__}.FRONTEND", fe)
    monkeypatch.setattr(f"{__name__}.SPECS", fe / "tests")

    test_every_testid_a_spec_targets_resolves_to_one_component()   # must not raise
