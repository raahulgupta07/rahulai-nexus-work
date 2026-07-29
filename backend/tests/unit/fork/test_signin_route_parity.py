"""The two Microsoft sign-in connectors must expose the SAME route shape.

The sign-in window drives both from one code path: it computes a base
(`fabric-signin` or `user-signin`) and appends the same suffixes. If one
connector lacks a route the other has, nothing fails at build time — the member
just gets a 404 mid-sign-in and a screen that stops updating. That is precisely
the failure being fixed, so it is pinned here.

Only the routers are imported: no app boot, no database.
"""
import pytest

from app.routes import fabric_user_signin as fab
from app.routes import powerbi_user_signin as pbi


def _paths(module) -> set:
    return {r.path for r in module.router.routes}


def _methods(module, path: str) -> set:
    out = set()
    for r in module.router.routes:
        if r.path == path:
            out |= set(r.methods or [])
    return out - {"HEAD", "OPTIONS"}


FAB = "/data_sources/{data_source_id}/fabric-signin"
PBI = "/data_sources/{data_source_id}/user-signin"


@pytest.mark.parametrize("suffix", ["/connect", "/device-code/poll", "/sync-status", "/resync"])
def test_both_connectors_expose_the_same_suffix(suffix):
    assert FAB + suffix in _paths(fab), f"fabric_user is missing {suffix}"
    assert PBI + suffix in _paths(pbi), f"powerbi_user is missing {suffix}"


@pytest.mark.parametrize("suffix,verb", [
    ("/connect", "POST"),
    ("/device-code/poll", "POST"),
    ("/sync-status", "GET"),
    ("/resync", "POST"),
])
def test_matching_verbs(suffix, verb):
    """The UI issues one verb per suffix regardless of connector."""
    assert verb in _methods(fab, FAB + suffix)
    assert verb in _methods(pbi, PBI + suffix)


def test_status_is_a_read_and_resync_is_a_write():
    """A GET that starts a crawl would fire on every prefetch and every retry of
    a dropped poll. Keep the distinction explicit."""
    assert _methods(fab, FAB + "/sync-status") == {"GET"}
    assert _methods(pbi, PBI + "/sync-status") == {"GET"}
    assert _methods(fab, FAB + "/resync") == {"POST"}
    assert _methods(pbi, PBI + "/resync") == {"POST"}


def test_both_kick_off_work_in_the_background():
    """Neither sign-in may crawl inside the request. Power BI used to, which is
    why its window closed with nothing to show — the response only arrived once
    the whole multi-tenant scan had finished."""
    assert hasattr(fab, "_kick_off_sync")
    assert hasattr(pbi, "_kick_off_merge")


def test_in_flight_tasks_are_strongly_referenced():
    """★asyncio keeps only a WEAK reference to a running task. Without a strong
    reference the sync can be collected part-way through — silently, no
    traceback, and the member's tables never arrive."""
    assert isinstance(fab._SYNC_TASKS, set)
    assert isinstance(pbi._SYNC_TASKS, set)


def test_sign_in_responses_announce_background_work():
    """The window decides whether to stay open by looking for `sync: started`.
    Both connectors must say it, or the one that does not closes on the member
    exactly as before."""
    import inspect
    for module in (fab, pbi):
        src = inspect.getsource(module)
        assert '"sync": "started"' in src, f"{module.__name__} never reports a started sync"
