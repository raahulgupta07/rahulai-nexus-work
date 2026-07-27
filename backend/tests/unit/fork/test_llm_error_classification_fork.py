"""Fork-specific guards on LLM error classification.

Upstream's ``test_llm_error_classification.py`` covers the quota-vs-rate-limit
axis added in 0.0.489. This file covers the OTHER axis, added here in
0.0.486.3: threading the original exception's CLASS NAME through ``exc_type``
so a dead endpoint is classified as ``network`` rather than ``unknown``.

Why it needs its own suite: the two axes meet at exactly one place — the
ORDER of the branches inside ``classify()``. Upstream's quota check runs
*before* the network branch, and upstream does not carry our fix (v0.0.489
still ships the original inline network branch). A future upstream port that
reorders or rewrites those branches would silently un-fix a dead endpoint —
it would still classify as *something*, just not as ``network``, and the model
fallback chain would stop firing for the single most likely real outage.
"""

import pytest

from app.ai.llm.errors import ERROR_CODES, classify
from app.ai.llm.fallback import FALLBACK_ELIGIBLE_CODES


class APIConnectionError(Exception):
    """Stands in for the openai/anthropic SDK class of the same name."""


class RateLimitError(Exception):
    pass


class Boom(Exception):
    """A bare exception — what agent_v2 rebuilds from a stored PlannerError."""


def code_of(exc, **kw):
    return classify(exc, provider="openrouter", **kw).code


# --- our axis: transport failures ------------------------------------------
#
# Every major SDK stringifies a transport failure to exactly "Connection
# error." — closed port, bad DNS and dead TLS are indistinguishable by text,
# so the class name is the only reliable signal.

def test_dead_endpoint_with_real_sdk_class():
    assert code_of(APIConnectionError("Connection error.")) == "network"


def test_dead_endpoint_when_class_was_lost_but_exc_type_passed():
    # planner_v3 stores only str(exc); agent_v2 rebuilds a bare Exception and
    # forwards the original class name via exc_type.
    assert code_of(Boom("Connection error."), exc_type="APIConnectionError") == "network"


def test_dead_endpoint_falls_back_to_text_when_class_is_gone():
    assert code_of(Boom("Connection error.")) == "network"


@pytest.mark.parametrize(
    "message",
    [
        "name or service not known",
        "temporary failure in name resolution",
        "tls handshake failure",
        "connection refused",
        "connection reset by peer",
        "server disconnected",
        "timed out",
    ],
)
def test_transport_text_tokens(message):
    assert code_of(Boom(message)) == "network"


# --- the collision point: ordering vs upstream's quota branch --------------

def test_quota_branch_does_not_swallow_a_dead_endpoint():
    # The quota check runs BEFORE the network branch. A transport failure
    # carries none of the quota markers, so it must fall through untouched.
    assert code_of(APIConnectionError("Connection error.")) == "network"


def test_google_throttling_stays_retryable():
    # Google reuses RESOURCE_EXHAUSTED for plain per-minute throttling, which
    # heals in seconds. The transient markers must win over the quota markers.
    msg = "429 RESOURCE_EXHAUSTED. Quota exceeded for quota metric, limit per minute"
    assert code_of(Boom(msg)) == "rate_limit"


def test_google_real_exhaustion_is_quota():
    msg = "429 RESOURCE_EXHAUSTED. The resource has been exhausted"
    assert code_of(Boom(msg)) == "quota"


@pytest.mark.parametrize(
    "message",
    ["monthly llm token quota exceeded", "monthly llm spend quota exceeded"],
)
def test_our_own_spend_caps_are_not_a_provider_fault(message):
    # UsageLimitExceeded says "quota exceeded" too. Reading it as a provider
    # quota would swap to another model and spend someone else's budget.
    assert code_of(Boom(message)) == "unknown"


# --- neighbouring branches must keep working -------------------------------

def test_provider_quota():
    assert code_of(Boom("Error code: 429 - insufficient_quota")) == "quota"


def test_credit_balance():
    assert code_of(Boom("Error code: 400 - your credit balance is too low")) == "quota"


def test_plain_rate_limit():
    assert code_of(Boom("Error code: 429 - rate limit reached")) == "rate_limit"


def test_rate_limit_by_class_name():
    assert code_of(RateLimitError("slow down")) == "rate_limit"


def test_context_length_is_checked_before_quota():
    msg = "Error code: 400 - maximum context length is 8192 tokens"
    assert code_of(Boom(msg)) == "context_length"


def test_auth():
    assert code_of(Boom("Error code: 401 - invalid api key")) == "auth"


def test_unknown_stays_unknown():
    assert code_of(Boom("something weird happened")) == "unknown"


# --- the wiring that makes the fix actually do something -------------------

def test_network_is_a_registered_code():
    assert "network" in ERROR_CODES


def test_network_is_fallback_eligible():
    # Without this, classifying as 'network' would change nothing: the model
    # fallback chain only engages for codes in this tuple.
    assert "network" in FALLBACK_ELIGIBLE_CODES
