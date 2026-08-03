"""A retry must not start a second scan of the query already running.

`test_slow_query_survives.py` proves parking works *within one wrapper*: time a
query out, ask for it again on the same wrapper, and the second call collects
the first thread instead of starting another. `_park_orphan`'s docstring says
this covers "an immediate identical retry".

It did not. Every retry builds a new wrapper.

    generate_and_execute_stream          # the retry loop
      └── execute_code_async
            └── execute_code
                  └── wrap_clients_for_capture   <- fresh wrappers, empty _parked

`wrap_clients_for_capture` is called *inside* `execute_code`, and `execute_code`
runs once per attempt. So the parked thread from attempt 1 was never visible to
attempt 2, and the abandoned scan — still running on the warehouse, because
cancellation is best effort and sources routinely decline it — was joined by an
identical second one. Exactly the failure parking exists to prevent, in the one
case it was written for.

★The fix widens the scope to the run, and no further. It must never become a
cross-run cache: on a per-user-credentialed connection the same SQL run by two
people can legitimately return different rows, so a result keyed on the SQL
alone would serve one person's data to another. `StreamingCodeExecutor` is
constructed once per tool invocation, so its instance is exactly one run by one
user — the widest scope that is still safe.
"""
import inspect
import itertools
import time

import pytest

from app.ai.code_execution.code_execution import (
    QueryCapturingClientWrapper,
    QueryTimeoutError,
    StreamingCodeExecutor,
    wrap_clients_for_capture,
)


_CONN_SEQ = itertools.count()


class SlowClient:
    """Sleeps, and counts how many scans it was actually asked for.

    ★Unique connection id per instance. `query_concurrency` keeps its in-flight
    counts in a process-global registry keyed by connection, and an abandoned
    thread holds its slot until it finishes — so a shared id like "conn-1" lets
    a parked thread from another test file block this one's queries. It passed
    alone and failed in the full suite, which reads as flakiness rather than as
    the shared-state bug it is.
    """

    def __init__(self, seconds):
        self.seconds = seconds
        self.calls = 0
        self._bow_connection_id = f"conn-rescan-{next(_CONN_SEQ)}"

    def execute_query(self, query, *a, **k):
        self.calls += 1
        time.sleep(self.seconds)
        return ["row"]


@pytest.fixture(autouse=True)
def _fast_ticks(monkeypatch):
    """Cross the progress mark in well under a second."""
    import app.ai.code_execution.code_execution as mod
    monkeypatch.setattr(mod, "_PROGRESS_TICK_SECONDS", 0.02)


def _attempt(client, parked):
    """One pass of the retry loop: a fresh wrapper, the run's parking map.

    Built directly rather than through `wrap_clients_for_capture` so the budget
    can be sub-second and the suite stays fast — the resolvers return `int`, so
    a fractional connection value truncates to 0 and falls back to the 60s
    default. `test_wrap_clients_for_capture_forwards_the_run_map` covers the
    forwarding those resolvers sit in front of.

    ★The hard limit is also how long a retry will wait on a parked thread
    (`_collect_parked` joins for one more full budget). 0.3 against 0.4s of work
    leaves the scan ~0.1s to finish after being abandoned — the same proportions
    `test_slow_query_survives.py` uses. Too small a budget and the retry gives
    up and rescans, which looks exactly like the bug.
    """
    return QueryCapturingClientWrapper(
        client, [], [],
        query_timeout_seconds=0.05,
        hard_timeout_seconds=0.3,
        parked_queries=parked,
    )


# --- the defect -------------------------------------------------------------

def test_a_second_attempt_collects_the_first_scan_instead_of_starting_one():
    """★The whole point. Two attempts, one scan."""
    client = SlowClient(0.4)
    parked = {}

    with pytest.raises(QueryTimeoutError):
        _attempt(client, parked).execute_query("SELECT big")
    assert client.calls == 1

    # The retry loop builds new wrappers. The parked thread must survive that.
    assert _attempt(client, parked).execute_query("SELECT big") == ["row"]
    assert client.calls == 1, "the retry started a second scan of the same query"


def test_without_shared_parking_the_second_attempt_rescans():
    """★Guard the guard. This is the old behaviour — if it ever stops
    reproducing, the test above is passing for the wrong reason."""
    client = SlowClient(0.4)

    with pytest.raises(QueryTimeoutError):
        _attempt(client, {}).execute_query("SELECT big")
    with pytest.raises(QueryTimeoutError):
        _attempt(client, {}).execute_query("SELECT big")

    assert client.calls == 2


# --- what must NOT be shared ------------------------------------------------

def test_a_different_query_is_never_served_a_parked_result():
    client = SlowClient(0.4)
    parked = {}

    with pytest.raises(QueryTimeoutError):
        _attempt(client, parked).execute_query("SELECT big")
    with pytest.raises(QueryTimeoutError):
        _attempt(client, parked).execute_query("SELECT other")

    assert client.calls == 2


def test_a_different_connection_is_never_served_a_parked_result():
    """The park key carries the connection id, so two sources running byte-
    identical SQL stay separate."""
    a, b = SlowClient(0.4), SlowClient(0.4)
    b._bow_connection_id = a._bow_connection_id + "-other"
    parked = {}

    with pytest.raises(QueryTimeoutError):
        _attempt(a, parked).execute_query("SELECT big")
    with pytest.raises(QueryTimeoutError):
        _attempt(b, parked).execute_query("SELECT big")

    assert a.calls == 1 and b.calls == 1


def test_separate_runs_never_share_parked_results():
    """★The security boundary. Two runs are two dicts — on a per-user
    credentialed connection the same SQL by two people can legitimately return
    different rows, and a result keyed on SQL alone would cross that line."""
    client = SlowClient(0.4)

    with pytest.raises(QueryTimeoutError):
        _attempt(client, {}).execute_query("SELECT big")
    with pytest.raises(QueryTimeoutError):
        _attempt(client, {}).execute_query("SELECT big")

    assert client.calls == 2


# --- it is actually wired in ------------------------------------------------

def test_the_executor_owns_one_parking_map_for_the_whole_run():
    executor = StreamingCodeExecutor()
    assert isinstance(getattr(executor, "_parked_queries", None), dict)


def test_two_executors_do_not_share_one():
    assert (
        StreamingCodeExecutor()._parked_queries
        is not StreamingCodeExecutor()._parked_queries
    )


def test_wrap_clients_for_capture_forwards_the_run_map():
    """The wrappers must hold the caller's map itself, not a copy — a copy per
    attempt is indistinguishable from the empty dict this fixes."""
    run_map = {}
    wrapped = wrap_clients_for_capture(
        {"main": SlowClient(0)}, [], [], parked_queries=run_map
    )
    assert wrapped["main"]._parked is run_map


def test_omitting_the_map_still_gives_each_wrapper_its_own():
    """Callers outside the retry loop keep working, isolated as before."""
    a = wrap_clients_for_capture({"main": SlowClient(0)}, [], [])["main"]
    b = wrap_clients_for_capture({"main": SlowClient(0)}, [], [])["main"]
    assert a._parked == {} and a._parked is not b._parked


def test_execute_code_hands_its_run_scoped_map_to_the_wrappers():
    """★A run-scoped map nothing passes down fixes nothing — the recurring
    shape in this codebase."""
    body = inspect.getsource(StreamingCodeExecutor.execute_code)
    assert "wrap_clients_for_capture(" in body
    tail = body[body.index("wrap_clients_for_capture("):]
    assert "parked_queries=self._parked_queries" in tail
