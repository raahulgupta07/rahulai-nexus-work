"""The permission named on the checkbox is the permission that is checked.

`manage_instructions` is offered as its own grant on an agent
(`RESOURCE_SCOPED_GROUPS["data_source"]`). `_can_manage_shared_instruction`
asked for `manage` instead, and the implication in `RESOURCE_PERM_IMPLIES` runs
ONE way — `manage` grants `manage_instructions`, never the reverse. So a member
handed exactly that checkbox was refused with:

    403  You need manage-instructions permission on the selected agent(s)

naming the permission they were holding.

★And where the route did not 403 outright, `create_instruction` read the same
False and silently forced the instruction PRIVATE. One cause, two symptoms, and
the quiet one is worse: the member sees their instruction saved and never learns
that nobody else can see it.

★This was a REGRESSION, not an inherited defect. The decorator this in-handler
check replaced asked for the right thing
(`@requires_permission('manage_instructions', resource_scoped=True)`); the
rewrite that let members write private instructions swapped the permission name
in passing.

★Read-only, no schema — `tests/unit/fork`. See CLAUDE.md.
"""
import inspect
import re

from app.core.permission_resolver import RESOURCE_PERM_IMPLIES
from app.core.permissions_registry import RESOURCE_SCOPED_GROUPS
from app.services.instruction_service import InstructionService

SRC = inspect.getsource(InstructionService._can_manage_shared_instruction)


def test_the_check_asks_for_manage_instructions_not_manage():
    """The whole defect in one assertion."""
    # ★The argument list contains `str(ds_id)`, so a `[^)]*` scan stops at the
    # wrong bracket and finds nothing. Match the last quoted argument instead.
    calls = re.findall(
        r'has_resource_permission\(\s*"data_source",.*?"(\w+)"\s*\)', SRC, re.S
    )
    assert calls, "the resource-permission check is gone entirely"
    assert set(calls) == {"manage_instructions"}, (
        f"asks for {sorted(set(calls))} — a member granted the "
        f"`manage_instructions` checkbox on an agent will be refused on it"
    )


def test_manage_instructions_is_a_grant_a_member_can_actually_be_given():
    """If it were not offered, asking for it would be the bug instead."""
    assert "manage_instructions" in RESOURCE_SCOPED_GROUPS["data_source"]["Permissions"]


def test_the_implication_runs_one_way_only():
    """★The reason `manage` was the wrong thing to ask for. Holding `manage`
    still works — it implies `manage_instructions` — so narrowing the check
    takes nothing away from anyone."""
    implied_by_manage = RESOURCE_PERM_IMPLIES["data_source"]["manage"]
    assert "manage_instructions" in implied_by_manage

    # Nothing implies `manage`, so a manage_instructions holder never gains it.
    for held, grants in RESOURCE_PERM_IMPLIES["data_source"].items():
        if held != "manage":
            assert "manage" not in grants, (
                f"{held} now implies manage — re-check whether the narrow "
                f"permission is still the right thing to ask for"
            )


def test_a_global_instruction_still_needs_an_org_admin():
    """Narrowing the per-agent check must not open the no-agent path, where
    there is no resource to be granted anything on."""
    assert "if not data_source_ids:" in SRC
    assert "return False" in SRC


def test_the_check_still_fails_closed():
    """★It decides whether an instruction is SHARED. An exception that fell
    through as True would publish one member's rule to the whole org."""
    assert "except Exception:" in SRC
    tail = SRC[SRC.index("except Exception:"):]
    assert "return False" in tail, "the failure path no longer fails closed"


def test_full_admin_still_bypasses():
    assert "FULL_ADMIN in resolved.org_permissions" in SRC
