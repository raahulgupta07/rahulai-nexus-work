"""A request that failed must never be drawn as a report with nothing in it.

WHAT THIS COST
--------------
A user built a seven-slide deck, refreshed the page, and it was gone. It had
not gone anywhere — every slide was still in the database, and the previews had
been generated twice. What had happened is that `/api/artifacts/report/<id>`
answered 500 (a duplicate membership row, fixed separately), and the page
turned that failure into `hasArtifacts = false` and rendered an empty
dashboard. No error, no retry, nothing to suggest the request had failed at
all. The only honest reading available to the user was that we had deleted
their work.

★The trap is that the code LOOKED defensive. It was wrapped in try/catch:

    try {
        const { data } = await useMyFetch(`/artifacts/report/${report_id}`)
        ...
    } catch (e) {
        hasArtifacts.value = false
    }

But `useMyFetch` does not throw. It catches internally and returns
`{ data: ref(null), error: ref(error) }` — so that `catch` block could never
run, and the failure fell through the HAPPY path instead: `data.value` null,
`Array.isArray(null)` false, `artifacts = []`, `hasArtifacts = false`. A
try/catch around a function that does not throw is not error handling; it reads
exactly like error handling to anyone skimming, which is worse than no handler
at all.

WHAT IS PINNED HERE
-------------------
1. The loader reads `error` from `useMyFetch` — the only way to see a failure
   from a function that swallows its own exceptions.
2. A failure sets a distinct flag rather than falling into the empty state.
3. The template renders that flag, ahead of the branches that draw "no
   dashboard". A flag nothing renders is the same defect one layer up, and this
   codebase has shipped exactly that before (the SSO logo picker: saved
   correctly, read by nothing, four releases).

★These are source assertions because Python cannot mount a Vue page. They are
the weaker kind of test and they are what is available here; the strong version
is a browser spec driving a forced 500, which belongs in `frontend/tests/`.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
PAGE = REPO / "frontend" / "pages" / "reports" / "[id]" / "index.vue"
FETCH = REPO / "frontend" / "composables" / "useMyFetch.ts"


def _fn(src: str, header: str) -> str:
    """The body of a function, from its header to the next top-level one."""
    start = src.index(header)
    rest = src[start + len(header):]
    nxt = re.search(r"\n(?:async )?function ", rest)
    return rest[: nxt.start()] if nxt else rest


def test_use_my_fetch_still_swallows_its_errors():
    """★The premise every other test here rests on.

    If `useMyFetch` is ever changed to throw, a plain try/catch becomes correct
    and these guards should be revisited rather than obeyed. Pinning the
    premise means this file explains itself instead of failing obscurely.
    """
    src = FETCH.read_text(encoding="utf-8")
    assert "error: ref(error)" in src, (
        "useMyFetch no longer returns the error on its result object — "
        "re-read test_a_failed_request_is_not_an_empty_report.py"
    )


def test_the_artifacts_loader_reads_the_error():
    body = _fn(PAGE.read_text(encoding="utf-8"), "async function checkHasArtifacts(")
    assert "error" in body, "checkHasArtifacts ignores the error entirely"
    assert re.search(r"const\s*\{\s*data\s*,\s*error\s*\}", body), (
        "checkHasArtifacts destructures only `data`, so a failed request is "
        "indistinguishable from an empty one"
    )


def test_a_failure_does_not_become_an_empty_report():
    body = _fn(PAGE.read_text(encoding="utf-8"), "async function checkHasArtifacts(")
    assert "artifactsUnavailable" in body, (
        "a failed artifacts request must set its own state, not fall through "
        "to hasArtifacts = false"
    )
    # the error branch must come BEFORE the success bookkeeping
    err = body.index("if (error.value)")
    ok = body.index("Array.isArray(data.value)")
    assert err < ok, "the error is checked after the data has already been used"


def test_the_latest_artifact_loader_distinguishes_404_from_failure():
    """★A 404 genuinely means "no dashboard yet" and must stay an empty state.

    Treating every non-200 as an error would put a warning on every report that
    simply has no dashboard — noise that trains people to ignore the warning
    that matters.
    """
    body = _fn(PAGE.read_text(encoding="utf-8"), "async function loadLatestArtifact(")
    assert "404" in body, (
        "loadLatestArtifact treats a 404 the same as a real failure"
    )


def test_the_template_actually_renders_the_failure():
    """★A flag nothing draws is the same bug one layer up.

    The SSO logo picker in this codebase saved its value correctly and was read
    by nothing, through four releases. State that no template consults is
    indistinguishable from state that was never set.
    """
    src = PAGE.read_text(encoding="utf-8")
    assert 'v-else-if="rightPanelView === \'artifact\' && reportLoaded && artifactsUnavailable"' in src, (
        "artifactsUnavailable is never rendered"
    )
    assert "artifactFrame.loadFailed" in src, "no message is shown to the user"
    assert 'data-testid="artifacts-retry"' in src, "no way to retry"


def test_the_failure_branch_precedes_the_empty_branches():
    """Order is the whole fix: `v-else-if` chains resolve top-down."""
    src = PAGE.read_text(encoding="utf-8")
    fail = src.index("artifactsUnavailable\"")
    legacy = src.index("hasLegacyLayout && !hasArtifacts")
    assert fail < legacy, (
        "the empty-dashboard branch is evaluated before the failure branch, so "
        "a failed request still renders as an empty report"
    )
