"""A private instruction belongs to one person. Nothing may hand out its text.

`is_private` was added so a member could write rules only their own agent runs
sees. Two things then made the promise hollow:

  1. A private instruction is force-published into the MAIN build by
     construction — `_auto_finalize_build(..., force_publish=True)` — because a
     rule nobody else can see cannot wait for someone else's approval. So the
     shared main build physically contains every member's private text.

  2. `GET /builds/main` → `GET /builds/{id}/contents` returned every row in
     that build, `text` included, with no owner filter. Two ordinary GETs, no
     admin rights, and one member reads another member's private notes.

`user_can_view_instruction` — the gate written for exactly this class of
question — never looked at `is_private` or `user_id` either, so the detail
endpoints it protects had the same hole.

The scope filter here is deliberately NOT gated on `per_user_instructions`.
The flag controls whether new private rules can be CREATED; rows written while
it was on must stay private if it is ever switched off.
"""
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.instruction_service import (
    InstructionService,
    instruction_is_private_to_other,
)

REPO = Path(__file__).resolve().parents[4]
BUILD_ROUTES = REPO / "backend" / "app" / "routes" / "build.py"

OWNER = "user-who-wrote-it"
STRANGER = "some-other-member"


def _instruction(**kw):
    base = dict(id="i1", is_private=False, user_id=OWNER, data_sources=[])
    base.update(kw)
    return SimpleNamespace(**base)


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


# --- the predicate ---------------------------------------------------------

def test_a_private_row_is_hidden_from_everyone_but_its_owner():
    priv = _instruction(is_private=True)
    assert instruction_is_private_to_other(priv, STRANGER) is True
    assert instruction_is_private_to_other(priv, OWNER) is False


def test_a_shared_row_is_hidden_from_nobody():
    for shared in (_instruction(is_private=False), _instruction(is_private=None)):
        assert instruction_is_private_to_other(shared, STRANGER) is False


def test_the_owner_is_compared_as_text():
    """★Ids arrive as UUID objects from the ORM and as strings from a token.
    `UUID(...) == "..."` is False, which would hide a row from its own owner
    — or, read the other way round, a sloppy comparison is how such a check
    silently starts passing everyone."""
    priv = _instruction(is_private=True, user_id=OWNER)
    assert instruction_is_private_to_other(priv, str(OWNER)) is False


def test_an_anonymous_caller_never_sees_a_private_row():
    """No user at all must not read as "the owner"."""
    priv = _instruction(is_private=True)
    assert instruction_is_private_to_other(priv, None) is True


def test_a_row_with_no_owner_recorded_is_treated_as_private():
    """★Fails CLOSED. A private row whose `user_id` was lost is unattributable;
    showing it to everyone is the one outcome that cannot be undone."""
    orphan = _instruction(is_private=True, user_id=None)
    assert instruction_is_private_to_other(orphan, STRANGER) is True
    assert instruction_is_private_to_other(orphan, OWNER) is True


# --- the gate that already existed ----------------------------------------

def test_the_view_gate_refuses_another_persons_private_note():
    """★`user_can_view_instruction` short-circuits `return True` for any
    instruction attached to no data source — and a private note is usually
    exactly that. It has to check ownership BEFORE that shortcut."""
    verdict = _run(InstructionService().user_can_view_instruction(
        db=None,
        instruction=_instruction(is_private=True),
        current_user=SimpleNamespace(id=STRANGER),
        organization=SimpleNamespace(id="org"),
    ))
    assert verdict is False


def test_the_view_gate_still_admits_the_owner():
    verdict = _run(InstructionService().user_can_view_instruction(
        db=None,
        instruction=_instruction(is_private=True),
        current_user=SimpleNamespace(id=OWNER),
        organization=SimpleNamespace(id="org"),
    ))
    assert verdict is True


def test_the_view_gate_still_admits_a_shared_global_instruction():
    """The existing behaviour must survive: a shared rule attached to no agent
    is visible to the whole organization."""
    verdict = _run(InstructionService().user_can_view_instruction(
        db=None,
        instruction=_instruction(is_private=False),
        current_user=SimpleNamespace(id=STRANGER),
        organization=SimpleNamespace(id="org"),
    ))
    assert verdict is True


# --- the build contents endpoint ------------------------------------------

def test_build_contents_filters_by_viewer():
    """★The actual two-GET leak. The route must drop rows the caller may not
    read instead of serialising their text."""
    src = BUILD_ROUTES.read_text(encoding="utf-8")
    i = src.index("async def get_build_contents(")
    body = src[i: src.index("\n@router", i)]
    assert "instruction_is_private_to_other" in body or "user_can_view_instruction" in body, (
        "build contents serialises every instruction in the build, private ones included"
    )
    assert "current_user" in body


def test_build_contents_drops_the_row_rather_than_blanking_it():
    """A row present with `text=None` still leaks that the note exists, who
    owns it, and which agent it belongs to."""
    src = BUILD_ROUTES.read_text(encoding="utf-8")
    i = src.index("async def get_build_contents(")
    body = src[i: src.index("\n@router", i)]
    assert "continue" in body, "filtered rows must be skipped, not emptied"


def test_the_detailed_diff_is_scoped_to_the_caller_too():
    """★The same leak through a second door. `/builds/{id}/diff/details`
    returns FULL instruction text by design — filtering only `/contents`
    would have left this one wide open."""
    from app.services.build_service import BuildService
    params = inspect.signature(BuildService.diff_builds_detailed).parameters
    assert "viewer_user_id" in params
    src = BUILD_ROUTES.read_text(encoding="utf-8")
    i = src.index("async def diff_builds_detailed(")
    body = src[i: src.index("\n@router", i)]
    assert "viewer_user_id" in body, "the route knows the caller and does not pass them"


def test_the_filter_is_not_inside_get_build_contents():
    """★Publish, promote and rollback all read `get_build_contents`. Hiding a
    row there would silently drop somebody's private rule out of the build it
    belongs to — a data-loss bug wearing a security fix's clothes."""
    from app.services.build_service import BuildService
    body = inspect.getsource(BuildService.get_build_contents)
    assert "is_private" not in body
    assert "instruction_is_private_to_other" not in body


# --- the pending sweep -----------------------------------------------------

def test_the_pending_sweep_is_scoped_too():
    """★The list query got this filter; the pending-badge query is a SECOND,
    separately built condition list and was missed. A private title showing up
    in someone else's "pending changes" is the same leak in miniature."""
    body = inspect.getsource(InstructionService._visible_pending_instruction_ids)
    assert "is_private" in body
    assert "Instruction.user_id" in body


def test_the_pending_scope_is_not_behind_the_feature_flag():
    """Turning `per_user_instructions` off must not publish rows that were
    written while it was on."""
    body = inspect.getsource(InstructionService._visible_pending_instruction_ids)
    # Comments may name the flag while explaining why it is not consulted, so
    # judge the CODE: strip comment text before looking.
    code = "\n".join(l.split("#", 1)[0] for l in body.splitlines())
    assert "is_private" in code
    assert "per_user_instructions" not in code, (
        "the private filter is gated on the flag that only governs creation"
    )
