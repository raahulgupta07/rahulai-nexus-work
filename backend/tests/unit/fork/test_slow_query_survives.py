"""A slow query has to be allowed to finish.

The per-query value used to BE the kill. A warehouse that needed four minutes
could never answer: at three the wrapper stopped waiting, asked the source to
cancel (best effort, routinely declined), and threw away whatever the thread
was computing. The model's retry then started the identical scan again,
alongside the first one still running. Six minutes, two live scans, nothing
kept — and an answer built on whichever subset happened to come back.

Now that value is a progress mark and a separate hard limit is the only thing
that ends a query.
"""

import threading
import time

import pytest

from app.ai.code_execution.code_execution import (
    DEFAULT_HARD_TIMEOUT_SECONDS,
    QueryCapturingClientWrapper,
    QueryTimeoutError,
    resolve_hard_timeout,
)


class SlowClient:
    """A client whose query takes `seconds`, and counts how often it is asked."""

    def __init__(self, seconds, rows=("row",)):
        self.seconds = seconds
        self.rows = list(rows)
        self.calls = 0
        self._bow_connection_id = "conn-1"

    def execute_query(self, query, *a, **k):
        self.calls += 1
        time.sleep(self.seconds)
        return list(self.rows)


def _wrap(client, soft, hard):
    queries, timings = [], []
    w = QueryCapturingClientWrapper(
        client, queries, timings,
        query_timeout_seconds=soft,
        hard_timeout_seconds=hard,
    )
    # Tick fast so a test can cross the progress mark in well under a second.
    import app.ai.code_execution.code_execution as mod
    return w, timings, mod


# ── the hard limit resolver ──────────────────────────────────────────────────


def test_the_hard_limit_defaults_to_fifteen_minutes():
    assert resolve_hard_timeout(SlowClient(0), None, 180) == DEFAULT_HARD_TIMEOUT_SECONDS


def test_a_connection_may_tighten_the_hard_limit():
    client = SlowClient(0)
    client._bow_connection_hard_timeout = 300

    assert resolve_hard_timeout(client, None, 180) == 300


def test_the_hard_limit_is_never_below_the_progress_mark():
    """A hard limit inside the soft mark would kill every query before it was
    ever reported as slow — the old behaviour, reintroduced by a bad setting."""
    client = SlowClient(0)
    client._bow_connection_hard_timeout = 30

    assert resolve_hard_timeout(client, None, 180) == 180


def test_a_nonsense_setting_is_ignored_rather_than_obeyed():
    client = SlowClient(0)
    client._bow_connection_hard_timeout = 0

    assert resolve_hard_timeout(client, None, 180) == DEFAULT_HARD_TIMEOUT_SECONDS


# ── the wait loop ────────────────────────────────────────────────────────────


def test_a_query_slower_than_the_progress_mark_still_returns(monkeypatch):
    """★The whole point. This case used to raise."""
    import app.ai.code_execution.code_execution as mod
    monkeypatch.setattr(mod, "_PROGRESS_TICK_SECONDS", 0.05)

    client = SlowClient(0.3)
    w, timings, _ = _wrap(client, soft=0.1, hard=10)

    assert w.execute_query("SELECT 1") == ["row"]
    assert client.calls == 1


def test_passing_the_progress_mark_is_recorded(monkeypatch):
    """So the planner and the operator can tell "alive" from "hung"."""
    import app.ai.code_execution.code_execution as mod
    monkeypatch.setattr(mod, "_PROGRESS_TICK_SECONDS", 0.05)

    w, timings, _ = _wrap(SlowClient(0.3), soft=0.1, hard=10)
    w.execute_query("SELECT 1")

    assert timings[0]["ran_long_seconds"] >= 0
    assert timings[0]["soft_timeout_seconds"] == 0.1


def test_a_fast_query_is_not_marked_as_slow(monkeypatch):
    import app.ai.code_execution.code_execution as mod
    monkeypatch.setattr(mod, "_PROGRESS_TICK_SECONDS", 0.05)

    w, timings, _ = _wrap(SlowClient(0), soft=5, hard=10)
    w.execute_query("SELECT 1")

    assert "ran_long_seconds" not in timings[0]


def test_only_the_hard_limit_ends_a_query(monkeypatch):
    import app.ai.code_execution.code_execution as mod
    monkeypatch.setattr(mod, "_PROGRESS_TICK_SECONDS", 0.05)

    w, timings, _ = _wrap(SlowClient(5), soft=0.1, hard=0.3)

    with pytest.raises(QueryTimeoutError) as err:
        w.execute_query("SELECT 1")

    # The message must quote the limit that actually fired, not the soft mark —
    # "exceeded 0.1s" on a query given 0.3s would be a lie to the model.
    # QueryTimeoutError keeps its int contract for callers; the timing
    # entry below carries the exact figure.
    assert timings[0]["timeout_seconds"] == 0.3
    assert timings[0]["soft_timeout_seconds"] == 0.1


def test_the_timeout_message_forbids_answering_as_though_it_returned(monkeypatch):
    """★The correctness half. A query that never came back must not quietly
    become a smaller total."""
    import app.ai.code_execution.code_execution as mod
    monkeypatch.setattr(mod, "_PROGRESS_TICK_SECONDS", 0.05)

    w, _, _ = _wrap(SlowClient(5), soft=0.1, hard=0.3)

    with pytest.raises(QueryTimeoutError) as err:
        w.execute_query("SELECT 1")

    assert "Do NOT answer as though this query returned" in str(err.value)


# ── parking ──────────────────────────────────────────────────────────────────


def test_an_identical_retry_waits_on_the_running_scan(monkeypatch):
    """★The abandoned thread is still computing — cancellation is best effort
    and sources decline it. The retry used to launch a SECOND scan of the same
    table beside the first."""
    import app.ai.code_execution.code_execution as mod
    monkeypatch.setattr(mod, "_PROGRESS_TICK_SECONDS", 0.05)

    client = SlowClient(0.4)
    w, _, _ = _wrap(client, soft=0.05, hard=0.3)

    with pytest.raises(QueryTimeoutError):
        w.execute_query("SELECT big")
    assert client.calls == 1

    # The retry collects the first scan rather than starting another.
    assert w.execute_query("SELECT big") == ["row"]
    assert client.calls == 1, "a second scan was started for the same query"


def test_a_different_query_is_not_served_the_parked_result(monkeypatch):
    import app.ai.code_execution.code_execution as mod
    monkeypatch.setattr(mod, "_PROGRESS_TICK_SECONDS", 0.05)

    client = SlowClient(0.4)
    w, _, _ = _wrap(client, soft=0.05, hard=0.3)

    with pytest.raises(QueryTimeoutError):
        w.execute_query("SELECT big")

    with pytest.raises(QueryTimeoutError):
        w.execute_query("SELECT other")

    assert client.calls == 2


def test_parking_is_per_wrapper_and_never_shared(monkeypatch):
    """★Deliberately not a cross-run cache. On a per-user-credentialed
    connection the same SQL run by two people can legitimately return different
    rows, so a result keyed on the SQL alone would serve one person's data to
    another. A wrapper lives for one tool execution."""
    import app.ai.code_execution.code_execution as mod
    monkeypatch.setattr(mod, "_PROGRESS_TICK_SECONDS", 0.05)

    client = SlowClient(0.4)
    first, _, _ = _wrap(client, soft=0.05, hard=0.3)
    with pytest.raises(QueryTimeoutError):
        first.execute_query("SELECT big")

    second, _, _ = _wrap(client, soft=0.05, hard=0.3)
    assert second._parked == {}


def test_the_parked_entry_is_dropped_once_collected(monkeypatch):
    """It must not accumulate across the many queries one run makes."""
    import app.ai.code_execution.code_execution as mod
    monkeypatch.setattr(mod, "_PROGRESS_TICK_SECONDS", 0.05)

    w, _, _ = _wrap(SlowClient(0.4), soft=0.05, hard=0.3)
    with pytest.raises(QueryTimeoutError):
        w.execute_query("SELECT big")
    assert len(w._parked) == 1

    w.execute_query("SELECT big")
    assert w._parked == {}


def test_an_exception_from_a_parked_query_is_raised_not_swallowed(monkeypatch):
    """A collected failure is still a failure — returning None would look like
    an empty result set."""
    import app.ai.code_execution.code_execution as mod
    monkeypatch.setattr(mod, "_PROGRESS_TICK_SECONDS", 0.05)

    class Boom(SlowClient):
        def execute_query(self, query, *a, **k):
            self.calls += 1
            time.sleep(self.seconds)
            raise RuntimeError("relation does not exist")

    w, _, _ = _wrap(Boom(0.4), soft=0.05, hard=0.3)
    with pytest.raises(QueryTimeoutError):
        w.execute_query("SELECT big")

    with pytest.raises(RuntimeError, match="relation does not exist"):
        w.execute_query("SELECT big")
