"""The budget an operator configures must survive the wiring, not just the resolver.

`test_slow_query_survives.py` covers the two ends of this — `resolve_hard_timeout`
picks the right number, and a wrapper handed that number enforces it. Nothing
covered the piece between them: `wrap_clients_for_capture`, which is what every
real query actually goes through.

That gap is not hypothetical. It is exactly the shape of the defect this file was
written after. When `query_timeout_seconds` stopped being the kill, nine upstream
tests kept constructing wrappers with the soft value alone, silently ran on the
900s default, and failed `DID NOT RAISE` — while the resolver tests stayed green
the whole time, because the resolver was never the broken part.

A setting an operator can type into AI Settings and have quietly ignored is worse
than one that does not exist, so the assertion here is deliberately end-to-end:
given the config, does the object that runs the query carry the number?
"""
import pytest

from app.ai.code_execution.code_execution import (
    DEFAULT_HARD_TIMEOUT_SECONDS,
    QueryCapturingClientWrapper,
    wrap_clients_for_capture,
)


class _Client:
    """Minimal client — `wrap_clients_for_capture` only needs execute_query."""

    _bow_connection_id = "conn-1"

    def execute_query(self, sql, *a, **k):  # pragma: no cover - never called
        return []


class _Settings:
    """Mimics the OrganizationSettings lookup used by both resolvers."""

    def __init__(self, **config):
        self._config = config

    def get_config(self, key, default=None):
        return self._config.get(key, default)


def _wrap(client, settings=None) -> QueryCapturingClientWrapper:
    wrapped = wrap_clients_for_capture({"main": client}, [], [], organization_settings=settings)
    return wrapped["main"]


# --- the default ------------------------------------------------------------

def test_with_nothing_configured_both_budgets_come_from_the_constants():
    w = _wrap(_Client())
    assert w._hard_timeout_seconds == float(DEFAULT_HARD_TIMEOUT_SECONDS)


# --- the org setting reaches the object that runs the query -----------------

def test_the_org_hard_limit_reaches_the_wrapper():
    """`query_hard_timeout_seconds` is editable in AI Settings. If it stops
    arriving here, the field still renders and still saves — and does nothing."""
    w = _wrap(_Client(), _Settings(query_hard_timeout_seconds=120))
    assert w._hard_timeout_seconds == 120.0


def test_the_org_progress_mark_reaches_the_wrapper():
    w = _wrap(_Client(), _Settings(query_timeout_seconds=30, query_hard_timeout_seconds=120))
    assert w._query_timeout_seconds == 30.0


# --- a connection may tighten, per its own documented contract --------------

def test_a_connection_tightens_the_hard_limit_below_the_org_default():
    client = _Client()
    client._bow_connection_hard_timeout = 45
    w = _wrap(client, _Settings(query_timeout_seconds=30, query_hard_timeout_seconds=600))
    assert w._hard_timeout_seconds == 45.0


def test_the_progress_mark_is_a_floor_on_how_far_a_connection_may_tighten():
    """★Worth knowing before you type a number into AI Settings: asking for a
    45s hard limit while the progress mark sits at its 60s default gives you
    60, not 45. Not a bug — the hard limit is never allowed inside the progress
    mark — but it is the kind of quiet clamp an operator reads as "my setting
    was ignored", so it is pinned here rather than left to be rediscovered.

    To genuinely tighten below the default, lower the progress mark too.
    """
    client = _Client()
    client._bow_connection_hard_timeout = 45
    w = _wrap(client, _Settings())            # progress mark defaults to 60
    assert w._hard_timeout_seconds == 60.0


def test_a_connection_cannot_drop_the_hard_limit_under_the_progress_mark():
    """Not a bug — a hard limit inside the progress mark would end every query
    before one was ever reported as slow, which is the behaviour the split was
    built to remove."""
    client = _Client()
    client._bow_connection_hard_timeout = 5
    w = _wrap(client, _Settings(query_timeout_seconds=60, query_hard_timeout_seconds=600))
    assert w._hard_timeout_seconds == 60.0


# --- the invariant that makes the pair coherent -----------------------------

@pytest.mark.parametrize(
    "soft,hard",
    [(30, 120), (180, 900), (60, 60), (1, 1)],
)
def test_the_hard_limit_is_never_below_the_progress_mark(soft, hard):
    w = _wrap(_Client(), _Settings(query_timeout_seconds=soft, query_hard_timeout_seconds=hard))
    assert w._hard_timeout_seconds >= w._query_timeout_seconds


def test_a_nonsense_setting_is_ignored_rather_than_obeyed():
    """0 would mean "kill immediately" — the harshest possible reading of a
    value someone almost certainly meant as "unset"."""
    w = _wrap(_Client(), _Settings(query_hard_timeout_seconds=0))
    assert w._hard_timeout_seconds == float(DEFAULT_HARD_TIMEOUT_SECONDS)


# --- per-client resolution --------------------------------------------------

def test_each_connection_gets_its_own_budget_in_one_wrap_call():
    """One tool invocation can hit several connections. A warehouse given ten
    minutes must not hand that budget to the transactional database beside it."""
    slow, fast = _Client(), _Client()
    slow._bow_connection_hard_timeout = 600
    # Both budgets, because the hard limit cannot go under the progress mark —
    # see test_the_progress_mark_is_a_floor_on_how_far_a_connection_may_tighten.
    fast._bow_connection_query_timeout = 10
    fast._bow_connection_hard_timeout = 30

    wrapped = wrap_clients_for_capture(
        {"warehouse": slow, "oltp": fast}, [], [], organization_settings=_Settings()
    )

    assert wrapped["warehouse"]._hard_timeout_seconds == 600.0
    assert wrapped["oltp"]._hard_timeout_seconds == 30.0


def test_a_client_without_execute_query_is_passed_through_untouched():
    sentinel = object()
    wrapped = wrap_clients_for_capture({"notes": sentinel}, [], [])
    assert wrapped["notes"] is sentinel
