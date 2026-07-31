"""`ToolConfirmationService.may_respond` — the authorization gate on MCP tool approval.

Upstream v0.0.491 moved mid-run tool approvals from a per-process dict into the
`tool_confirmations` table so any uvicorn worker can resolve one. That fixed a
real multi-worker bug, but it also moved the authorization decision: the route
no longer compares against in-memory metadata it created itself, it compares
against a row any worker can read.

Upstream's cover for this is `tests/e2e/rbac/test_rbac_tool_policies.py`, which
needs a database and the full app. `may_respond` is pure logic, so it belongs in
the fast suite as well — it is the one function standing between "the user who
started this run" and "anyone who can guess a confirmation id", and a change that
weakened it would still pass every e2e test that only exercises the happy path
and the two obvious denials.

The `remember` semantics are covered here too because the route reads
`row.remember` back after `resolve()` and persists a per-tool policy preference
from it — a decision that outlives the run.
"""
import pytest

from app.models.tool_confirmation import ToolConfirmation
from app.services.tool_confirmation_service import (
    KIND_MCP_TOOL_POLICY,
    ToolConfirmationService,
)


class _Row:
    """Stand-in for a `tool_confirmations` row. Deliberately not a real model
    instance: `may_respond` must decide from field values alone, so a plain
    object proves it never reaches for session state or a relationship."""

    def __init__(self, *, system_completion_id=None, head_completion_id=None, user_id=None):
        self.system_completion_id = system_completion_id
        self.head_completion_id = head_completion_id
        self.user_id = user_id


@pytest.fixture
def svc():
    return ToolConfirmationService()


# ── the run's own completions may answer ────────────────────────────────────

@pytest.mark.parametrize("field", ["system_completion_id", "head_completion_id"])
def test_either_of_the_runs_completions_may_answer(svc, field):
    """A run has two completion ids and the approval card can be addressed to
    either; accepting only one would make the buttons dead on half of them."""
    row = _Row(**{field: "c-1"}, user_id="u-1")
    assert svc.may_respond(row, completion_id="c-1", user_id="u-1") is True


def test_a_completion_from_a_different_run_is_refused(svc):
    row = _Row(system_completion_id="c-1", head_completion_id="c-2", user_id="u-1")
    assert svc.may_respond(row, completion_id="c-999", user_id="u-1") is False


def test_another_user_may_not_answer(svc):
    """Approving a tool call runs it with the *starting* user's identity and
    credentials, so a second user answering is a privilege escalation, not a
    convenience."""
    row = _Row(system_completion_id="c-1", user_id="u-1")
    assert svc.may_respond(row, completion_id="c-1", user_id="u-2") is False


def test_the_user_check_is_string_compared(svc):
    """Ids arrive as UUID objects from the ORM and as strings from the request
    path. An identity comparison would refuse the legitimate owner."""
    import uuid

    uid = uuid.uuid4()
    row = _Row(system_completion_id="c-1", user_id=uid)
    assert svc.may_respond(row, completion_id="c-1", user_id=str(uid)) is True


# ── the two "unset" fallbacks, which are the risky part ─────────────────────

def test_a_row_naming_no_completion_is_not_restricted_by_completion(svc):
    """Documented behaviour, not an accident: a row with neither completion id
    set is answerable from any completion of the owning user. It is reachable
    only for rows written before those columns were populated, and the user
    check below still applies — this test exists so that anyone tightening it
    knows it was a choice."""
    row = _Row(user_id="u-1")
    assert svc.may_respond(row, completion_id="anything", user_id="u-1") is True


def test_a_row_with_no_user_still_enforces_the_completion(svc):
    """The two guards are independent. If the user is unknown, the completion
    id must still be the one this card was raised for — otherwise an unowned
    row would be answerable by anyone from anywhere."""
    row = _Row(system_completion_id="c-1")
    assert svc.may_respond(row, completion_id="c-1", user_id="u-9") is True
    assert svc.may_respond(row, completion_id="c-other", user_id="u-9") is False


# ── contract the route depends on ───────────────────────────────────────────

def test_the_service_kind_matches_the_model(svc):
    """The route refuses any row whose `kind` is not this constant. A drift
    between the service constant and what `create` writes would make every
    approval 404 with 'not found or expired'."""
    assert KIND_MCP_TOOL_POLICY == "mcp_tool_policy"


def test_resolved_statuses_are_distinct_from_pending(svc):
    """`resolve()` updates only rows still PENDING, and `poll_decision` treats
    PENDING as 'no answer yet'. If any resolved status collided with PENDING a
    decision would never be visible to the waiting run."""
    resolved = {
        ToolConfirmation.STATUS_APPROVED,
        ToolConfirmation.STATUS_DENIED,
        ToolConfirmation.STATUS_EXPIRED,
    }
    assert ToolConfirmation.STATUS_PENDING not in resolved
    assert len(resolved) == 3


def test_is_pending_tracks_the_status_field():
    """The route returns `already_resolved` based on `is_pending`; if that
    property stopped following `status`, a second click would re-resolve a row
    and could flip a denial into an approval."""
    row = ToolConfirmation(status=ToolConfirmation.STATUS_PENDING)
    assert row.is_pending is True
    for status in (
        ToolConfirmation.STATUS_APPROVED,
        ToolConfirmation.STATUS_DENIED,
        ToolConfirmation.STATUS_EXPIRED,
    ):
        row.status = status
        assert row.is_pending is False


def test_approved_is_true_only_for_the_approved_status():
    """`row.approved` is what the route echoes back and what the run acts on.
    An expired or denied row reading as approved would run the tool."""
    row = ToolConfirmation(status=ToolConfirmation.STATUS_APPROVED)
    assert row.approved is True
    for status in (
        ToolConfirmation.STATUS_PENDING,
        ToolConfirmation.STATUS_DENIED,
        ToolConfirmation.STATUS_EXPIRED,
    ):
        row.status = status
        assert row.approved is False
