"""A member could not create an instruction, and the reason was two aliases.

THE BUG. On a fresh cloud install, an ordinary member opened Knowledge and saw
no "+ New" button at all — no Instruction, and no Data Agent either. Both the
permission and the endpoint that make a member instruction work already
existed and were already correct:

  * ``POST /instructions`` (routes/instruction.py) accepts a member when
    PER_USER_INSTRUCTIONS is on: it forces ``is_private=True`` and verifies the
    caller can *access* each target agent. Only shared / org-wide instructions
    need ``manage_instructions``.
  * ``create_file_data_source`` lets a member build a Data Agent from uploads.

The front end never reached either. Two separate aliases did it:

  1. ``const canCreateInstruction = canApprove`` — creating was pointed at the
     tier that REVIEWS and DELETES. And because the popover wrapper renders only
     when at least one of its three rows is allowed, that one alias also took
     away the Data Agent row the member was entitled to.
  2. The create form always posted to ``/instructions/global``, the admin
     endpoint, so even reaching the form ended in 403.

WHY NOT JUST GRANT THE PERMISSION. Adding ``manage_instructions`` to the member
role would fix the symptom and hand over ~30 other routes with it: bulk delete,
deleting other people's instructions, build approval, git push. The requirement
was that a member creates instructions and Data Agents FOR THEMSELVES, and
never an Agent (a database connector). That is three different gates, so this
test pins all three apart — the failure mode is someone later "simplifying"
them back into one.
"""
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
EXPLORER = REPO / "frontend" / "components" / "KnowledgeExplorer.vue"
CREATE = REPO / "frontend" / "components" / "InstructionGlobalCreateComponent.vue"
ROUTE = REPO / "backend" / "app" / "routes" / "instruction.py"
REGISTRY = REPO / "backend" / "app" / "core" / "permissions_registry.py"
EN = REPO / "locales" / "en.json"


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _decl(src: str, name: str) -> str:
    """The text of a `const <name> = …` declaration, however it is wrapped.

    Deliberately not a regex over `computed\\((.*?)\\n\\)`: that only matches a
    multi-line computed, so reformatting a declaration onto one line would make
    these tests report "missing" for something that is right there. Read to the
    next top-level `const`/`function` instead.
    """
    i = src.index(f"const {name} =")
    rest = src[i:]
    nxt = re.search(r"\n(?:const |function |async function |// ──)", rest[1:])
    return rest[: nxt.start() + 1] if nxt else rest


# ── the front end ────────────────────────────────────────────────────────────

def test_create_instruction_is_not_an_alias_of_approve():
    """The exact line that caused it: `const canCreateInstruction = canApprove`."""
    src = _src(EXPLORER)
    assert not re.search(
        r"const\s+canCreateInstruction\s*=\s*canApprove\s*$",
        src,
        re.M,
    ), (
        "canCreateInstruction is aliased to canApprove again. Approving is a "
        "higher tier than creating; this hides the whole + New menu from members."
    )


def test_create_instruction_admits_a_member():
    """It must fall back to the per-user path, not only the manage tier."""
    body = _decl(_src(EXPLORER), "canCreateInstruction")
    assert "canApprove" in body, "an admin/approver must still be able to create"
    assert "perUserInstructionsOn" in body, (
        "the member path is gated on the PER_USER_INSTRUCTIONS feature flag; "
        "without it the UI would offer a button the backend refuses"
    )
    assert "agents" in body, (
        "the member branch of POST /instructions requires a non-empty "
        "data_source_ids, so with no agents the button only leads to a 403"
    )


def test_the_new_menu_wrapper_still_requires_only_one_row():
    """The wrapper is why one wrong gate removed two features, not one."""
    src = _src(EXPLORER)
    assert re.search(
        r"<UPopover\s+v-if=\"canCreateInstruction\s*\|\|\s*canCreateAgent\s*\|\|\s*canCreateDataAgent\"",
        src,
    ), "the + New popover must render when ANY of the three rows is permitted"


def test_agent_stays_admin_only_and_data_agent_does_not():
    """The one thing the user explicitly ruled out: members creating Agents.

    "Agent" connects a database or BI tool — shared infrastructure, server-side
    paths, admin. "Data Agent" is an upload the member owns. Collapsing these
    would either hide uploads from members or offer them a connector the
    backend then refuses.
    """
    src = _src(EXPLORER)
    assert re.search(r"const\s+canCreateAgent\s*=\s*computed\(\(\)\s*=>\s*useCan\('create_data_source'\)\)", src), \
        "Agent creation must stay on create_data_source (admin)"
    assert "create_file_data_source" in _decl(src, "canCreateDataAgent"), \
        "Data Agent must also accept the member-level file permission"
    # And the rows are gated separately in the template.
    assert 'v-if="canCreateAgent"' in src and 'v-if="canCreateDataAgent"' in src


def test_create_form_posts_to_the_member_endpoint_when_it_must():
    src = _src(CREATE)
    assert "isPrivateCreate" in src, "no member create path in the form at all"
    assert re.search(
        r"else if \(isPrivateCreate\.value\).*?useMyFetch\('/instructions'",
        src,
        re.S,
    ), "a member create must POST /instructions, not /instructions/global"
    # The admin path must survive.
    assert "useMyFetch('/instructions/global'" in src


def test_member_create_never_sends_an_empty_agent_list():
    """`[]` means org-wide, which is exactly what a member may NOT create.

    buildInstructionPayload sends `[]` for "All agents". Passing that through
    lands on routes/instruction.py's else branch and 403s, so it is expanded to
    the agents this user can actually see.
    """
    src = _src(CREATE)
    block = re.search(r"else if \(isPrivateCreate\.value\)(.*?)\n        \} else \{", src, re.S)
    assert block, "the member branch is gone"
    b = block.group(1)
    assert "editorTargetDsIds" in b, "no fallback from 'all agents' to an explicit list"
    assert "is_private: true" in b, "the client should state its intent even though the route forces it"
    assert "selectAgentRequired" in b, "no guard for the genuinely-no-agents case"


def test_a_member_creating_is_not_labelled_a_suggestion():
    """Creating your own private instruction is a create, not a proposal."""
    assert "isPrivateCreate" in _decl(_src(CREATE), "isSuggestMode"), (
        "isSuggestMode still swallows the member create path, so the button "
        "would read 'Submit suggestion' for something that is created outright"
    )


def test_the_guard_toast_has_a_translation():
    keys = json.loads(_src(EN))
    assert "selectAgentRequired" in keys["instructionGlobalCreate"]["toast"], \
        "missing i18n key — vue-i18n would render the raw key path to the user"


# ── the backend it relies on (unchanged, but load-bearing) ───────────────────

def test_the_member_route_still_forces_private():
    """These assertions are about a contract the UI now depends on."""
    src = _src(ROUTE)
    body = src[src.index('@router.post("/instructions", response_model'):src.index('@router.post("/instructions/global"')]
    assert "per_user_instructions" in body
    assert "instruction.is_private = True" in body, \
        "the member path must force private; the client cannot be trusted to"
    assert "user_can_access_data_source" in body, \
        "a member must be checked for ACCESS to each target agent"
    assert "force_global=False" in body


def test_global_instructions_are_still_admin_only():
    src = _src(ROUTE)
    i = src.index('@router.post("/instructions/global"')
    head = src[i:i + 400]
    assert "@requires_permission('manage_instructions'" in head
    assert "require_org_permission" in src[i:i + 2000], (
        "an org-wide instruction with no data source must need the ORG-level "
        "permission — a per-agent manage grant must not author instructions "
        "that apply to every agent"
    )


@pytest.mark.parametrize("perm", ["create_file_data_source", "create_data_source"])
def test_both_data_permissions_exist(perm):
    assert perm in _src(REGISTRY), f"{perm} vanished from the registry"
