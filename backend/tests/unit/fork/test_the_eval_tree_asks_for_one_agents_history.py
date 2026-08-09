"""The explorer's eval tree must scope its questions in SQL, not in the browser.

Both listings were org-wide, so the panel narrowed a PAGE of org-wide rows
client-side: the newest 1000 cases in the organization, kept if they sat in one
of this agent's suites. Once other agents produced more cases than the page,
your suites were simply not in what got filtered, and the empty state is
identical to "never ran".

The tree also asks two different questions and used to ask one: by FILING (what
sits in this agent's suites) and by TARGET (what this agent is tested by,
wherever it is filed — chiefly the org-wide Drafts bucket). One org-wide fetch
answered neither once the page was full.

★Also pinned here: TreeGroup's prop list. `renamable`/`deletable` had been
dropped from the declaration while the render still read them, so suite rename
and delete rendered nowhere — a prop a component reads but does not declare is
always undefined, and Vue says nothing. That is the "verify a merge on the lines
nobody changed" failure: our added lines all survived, a line both sides already
had did not.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
EXPLORER = REPO / "frontend" / "components" / "KnowledgeExplorer.vue"


def strip_comments(src: str) -> str:
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)
    return "\n".join(
        line for line in src.splitlines()
        if not line.strip().startswith(("//", "/*", "*"))
    )


@pytest.fixture(scope="module")
def explorer() -> str:
    return strip_comments(EXPLORER.read_text(encoding="utf-8"))


def _function_body(src: str, header: str) -> str:
    """Source of the function starting at `header`, by brace balance.

    ★Brace counting must not start until the parameter list is closed:
    `loadEvalTree(scope: string, opts: { force?: boolean } = {})` balances a
    brace inside its own signature, and a naive scan returns the signature as
    the body — which then contains none of the calls under test and passes on
    a broken file.
    """
    start = src.index(header)
    parens = 0
    for i in range(start, len(src)):
        c = src[i]
        if c == "(":
            parens += 1
        elif c == ")":
            parens -= 1
        elif c == "{" and parens == 0:
            start = i
            break
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces after {header!r}")


def test_no_case_listing_is_asked_for_org_wide(explorer):
    body = _function_body(explorer, "async function loadEvalTree(")
    bare = [m for m in re.findall(r"/api/tests/cases\?[^`'\"]*", body)
            if "scopeQ" not in m and "suiteQ" not in m]
    assert not bare, (
        "an unscoped case listing is still fetched, so the tree filters a page "
        f"of org-wide rows in the browser: {bare}"
    )


def test_the_tree_asks_both_questions(explorer):
    """Filing and target are different sets; one fetch cannot answer both."""
    body = _function_body(explorer, "async function loadEvalTree(")
    assert "suite_ids=" in body, (
        "cases are not asked for by suite, so 'what is filed here' is still a "
        "client-side filter over whatever page came back"
    )
    assert body.count("/api/tests/cases?") >= 2, (
        "only one case listing is fetched — the org-wide Drafts bucket, where "
        "auto-drafted cases land, is invisible in the tree"
    )


def test_the_suite_row_can_run_its_suite(explorer):
    """POST /tests/suites/{id}/runs was permission-gated with no caller."""
    assert "/api/tests/suites/${suite.id}/runs" in explorer, (
        "nothing calls the run-suite endpoint; running a suite still means "
        "opening it and ticking every case"
    )
    body = _function_body(explorer, "async function runSuite(")
    assert "pendingRunId" in body, "the new run is never handed to the Evals panel"


def test_an_empty_suite_offers_no_play_button(explorer):
    """A run with no cases can only produce a 400."""
    assert "runnable: props.canManage && cases.length > 0" in explorer, (
        "the play button is offered on suites with nothing in them"
    )


# ── The dropped-prop class ───────────────────────────────────────────────────

@pytest.mark.parametrize("prop", ["renamable", "deletable", "runnable", "running"])
def test_treegroup_declares_every_prop_its_render_reads(explorer, prop):
    """An undeclared prop is undefined forever and Vue reports nothing: the
    button simply never renders."""
    decl = next(l for l in explorer.splitlines()
                if l.strip().startswith("props: { label: String"))
    assert re.search(rf"\b{prop}: Boolean\b", decl), (
        f"TreeGroup's render reads props.{prop} but does not declare it, so "
        "that control is dead on every row"
    )


@pytest.mark.parametrize("event", ["rename", "delete", "run"])
def test_treegroup_declares_every_event_it_emits(explorer, event):
    decl = next(l for l in explorer.splitlines() if l.strip().startswith("emits: ['toggle'"))
    assert f"'{event}'" in decl, f"TreeGroup emits '{event}' without declaring it"
