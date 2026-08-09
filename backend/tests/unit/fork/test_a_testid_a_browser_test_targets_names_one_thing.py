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
# ★A component may BUILD a testid from data:
#     :data-testid="`onboarding-demo-${demo.id}`"
# Only the literal prefix is knowable without running the app, so record that
# and match a targeted id by prefix. Without this the scan reports every
# dynamic id as "no component declares it" — which is a FALSE ALARM that reads
# exactly like a broken spec, and the natural way to silence it is to add a
# hardcoded duplicate testid to the component. Measured 2026-08-09: upstream's
# `onboarding-demo-chinook` tripped precisely this, and the page declares it
# correctly one interpolation away.
# The prefix must be non-empty — `` :data-testid="`${x}`" `` would otherwise
# match every id in the tree and switch this guard off silently.
DECLARED_DYNAMIC = re.compile(r':data-testid="`([^`$]+)\$\{')
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


def _dynamic_prefixes():
    """literal prefix -> set of source files that build a testid from it."""
    out = defaultdict(set)
    for f in _source_files():
        for prefix in DECLARED_DYNAMIC.findall(f.read_text(encoding="utf-8", errors="replace")):
            out[prefix].add(f)
    return out


def _declaring_files(tid, declared, dynamic):
    """Which source files can produce `tid` — literally, or by interpolation.

    A dynamic prefix is a WEAKER claim than a literal declaration: it proves a
    component can mint ids in that family, not that this exact one is reachable.
    So it is consulted only when nothing declares the id outright, and it can
    still report a collision between two families.
    """
    files = declared.get(tid, set())
    if files:
        return files, False
    owners = {f for prefix, fs in dynamic.items() if tid.startswith(prefix) for f in fs}
    return owners, bool(owners)


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
    dynamic = _dynamic_prefixes()
    targeted = _targets()
    if not targeted:
        pytest.skip("no browser specs target a data-testid yet")

    ambiguous = []
    missing = []
    for tid, specs in sorted(targeted.items()):
        files, _by_prefix = _declaring_files(tid, declared, dynamic)
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


def test_an_interpolated_testid_counts_as_declared(tmp_path, monkeypatch):
    """★The real up531 false alarm, reconstructed.

    `onboarding-demo-chinook` is minted by
    `` :data-testid="`onboarding-demo-${demo.id}`" ``, so a literal scan finds
    nothing and reports a broken spec. The tempting silencer — adding a
    hardcoded `data-testid="onboarding-demo-chinook"` beside the dynamic one —
    would put two declarations on the same element and make the id genuinely
    ambiguous. So the scanner learns the prefix instead.
    """
    fe = tmp_path / "frontend"
    (fe / "pages").mkdir(parents=True)
    (fe / "tests").mkdir(parents=True)
    (fe / "pages" / "onboarding.vue").write_text(
        '<button :data-testid="`onboarding-demo-${demo.id}`">{{ demo.name }}</button>'
    )
    (fe / "tests" / "onboarding.spec.ts").write_text(
        "page.getByTestId('onboarding-demo-chinook')"
    )
    monkeypatch.setattr(f"{__name__}.FRONTEND", fe)
    monkeypatch.setattr(f"{__name__}.SPECS", fe / "tests")

    test_every_testid_a_spec_targets_resolves_to_one_component()   # must not raise


def test_a_prefix_that_does_not_match_is_still_a_finding(tmp_path, monkeypatch):
    """★The positive control for the change above — WITHOUT this, teaching the
    scanner about interpolation could have switched the whole check off and
    every run would still be green."""
    fe = tmp_path / "frontend"
    (fe / "pages").mkdir(parents=True)
    (fe / "tests").mkdir(parents=True)
    (fe / "pages" / "onboarding.vue").write_text(
        '<button :data-testid="`onboarding-demo-${demo.id}`"></button>'
    )
    (fe / "tests" / "onboarding.spec.ts").write_text(
        "page.getByTestId('settings-danger-zone')"
    )
    monkeypatch.setattr(f"{__name__}.FRONTEND", fe)
    monkeypatch.setattr(f"{__name__}.SPECS", fe / "tests")

    with pytest.raises(AssertionError) as e:
        test_every_testid_a_spec_targets_resolves_to_one_component()
    assert "settings-danger-zone" in str(e.value)
