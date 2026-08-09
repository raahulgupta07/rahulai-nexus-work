"""A viewer who only has the dashboard is sent to /r/{id} without a flash.

The conversation transcript is owner-only. Someone who was shared the dashboard
can open `/reports/{id}` — the route is not gated — but its completions fetch
comes back 403. Before this, that 403 fell into the generic transient-failure
branch, which retries three times with a backoff and then leaves an empty
workspace: the person who followed a share link sat looking at a blank chat.

Two hunks, and the second is not cosmetic. `navigateTo` alone clears
`completionsLoaded`'s hold on the spinner, so the workspace paints for a frame
behind the redirect — the reader sees the page they are not allowed to read
flicker past. `redirectingToViewer` holds the loading state across the hand-off.

★The page does not re-derive who may read a transcript. The backend is the
authority (owner, full admin, project collaborator); the page only honours the
refusal — which is why this guard checks for a *status check*, not for a
permission computation.

MEASURED, guard logic run against `git show HEAD:<path>` and the working tree:

    test_a_403_redirects_to_the_shared_surface   HEAD fail -> now pass
    test_the_redirect_precedes_the_retry_branch  HEAD fail -> now pass
    test_the_spinner_holds_through_the_handoff   HEAD fail -> now pass
    test_the_flag_is_declared                    HEAD fail -> now pass

Upstream: a72fd0d8 (the gate) and 396c0cc0 (the held spinner).
"""

from __future__ import annotations

import re

from vue_source import read_source

PAGE = "pages/reports/[id]/index.vue"


def _load_completions_block(src: str) -> str:
    start = src.index("async function loadCompletions")
    return src[start : start + 4000]


def test_the_flag_is_declared():
    src = read_source(PAGE)
    assert re.search(r"const redirectingToViewer = ref\(false\)", src), (
        "`redirectingToViewer` must be declared; without it the template "
        "reference below is a silent undefined."
    )


def test_a_403_redirects_to_the_shared_surface():
    block = _load_completions_block(read_source(PAGE))
    assert re.search(r"===\s*403", block), (
        "loadCompletions does not distinguish a 403 from a transient failure, "
        "so a dashboard viewer is retried three times into an empty workspace."
    )
    assert re.search(r"navigateTo\(`/r/\$\{report_id\}`", block), (
        "the 403 branch must hand off to /r/{id}, the surface the viewer does "
        "have."
    )


def test_the_redirect_precedes_the_retry_branch():
    """Order matters: the retry branch is `if (messages.length === 0 && …)`,
    which a fresh 403 satisfies. Placed after it, the redirect never runs."""
    block = _load_completions_block(read_source(PAGE))
    assert block.index("=== 403") < block.index("initialLoadRetries < 3"), (
        "the 403 hand-off sits after the generic retry branch, which swallows "
        "it — a fresh 403 has messages.length === 0 and retries first."
    )


def test_the_spinner_holds_through_the_handoff():
    """A template that reads the flag proves nothing unless something writes it,
    and a write proves nothing unless the template reads it. Assert both ends."""
    src = read_source(PAGE)

    loading_div = re.search(r'<div v-if="\(!reportLoaded[^"]*"', src)
    assert loading_div, "could not find the loading-state div"
    assert "redirectingToViewer" in loading_div.group(0), (
        "the loading gate does not consider redirectingToViewer, so the "
        "workspace paints for a frame behind the redirect."
    )

    block = _load_completions_block(src)
    assign = block.index("redirectingToViewer.value = true")
    assert assign < block.index("navigateTo(`/r/${report_id}`"), (
        "redirectingToViewer must be set BEFORE navigateTo, or the state it "
        "exists to hold is set after the thing it was holding for."
    )
