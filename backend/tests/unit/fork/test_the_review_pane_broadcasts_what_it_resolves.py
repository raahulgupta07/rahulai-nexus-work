"""0.0.543.22 — Accept all tells the rest of the app what it did.

Measured live on production (2026-08-21, browser network log): a successful
POST /hunks/accept-all was followed by ZERO list/badge refresh requests. The
review pane resolved hunks with its own fetch and notified only its parent via
a Vue `changed` emit — and when the last hunk resolves, `load()` emits `empty`
first, so the host can unmount the pane before `changed` is processed. The
global `instruction:resolved` window event (which the Agents page subscribed
to in 0.0.543.21) was dispatched only by the composable path this button does
not use. Fix: both resolve paths dispatch the global event themselves.
"""
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
PANE = REPO / "frontend" / "components" / "instructions" / "InstructionTrackedChanges.vue"
EXPLORER = REPO / "frontend" / "components" / "KnowledgeExplorer.vue"


def _code(path: Path) -> str:
    # Strip // comments so a comment naming the event cannot satisfy a check —
    # the measured trap: a source-scanning test matching its own explanation.
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("//")
    )


class TestTheResolvePathsBroadcast:
    def test_both_resolve_functions_dispatch_the_global_event(self):
        code = _code(PANE)
        calls = code.count("dispatchInstructionResolved(")
        assert calls >= 2, (
            f"expected the single-hunk and resolve-all paths each to dispatch "
            f"instruction:resolved; found {calls} call(s) — the Agents page "
            "badge and pending list go stale until a manual refresh"
        )

    def test_the_dispatch_happens_before_the_pane_can_unmount(self):
        """The dispatch must come BEFORE `load({ silent: true })` in source
        order: load() emits `empty` when the last hunk resolves and the host
        may unmount the pane, losing anything queued after it."""
        code = _code(PANE)
        for fn_marker in ("async function _resolve(", "async function resolveAll("):
            start = code.find(fn_marker)
            assert start != -1, f"{fn_marker} is gone"
            body = code[start:start + 1600]
            d = body.find("dispatchInstructionResolved(")
            l = body.find("load({ silent: true })")
            assert d != -1 and l != -1 and d < l, (
                f"{fn_marker} dispatches after load() — the event can be lost "
                "with the unmounting pane"
            )


class TestTheAgentsPageStillListens:
    def test_the_explorer_subscribes_and_unsubscribes(self):
        code = _code(EXPLORER)
        assert "addEventListener(INSTRUCTION_RESOLVED_EVENT" in code
        assert "removeEventListener(INSTRUCTION_RESOLVED_EVENT" in code
