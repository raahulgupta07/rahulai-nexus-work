"""The Accept button was shown to people the backend would refuse.

A member who owned one agent of their own held `manage_instructions` on that
agent. The tree asked `useCanAny('manage_instructions', 'data_source')` — "on
ANY data source" — so every Accept all / Reject all / Delete control rendered,
including on instructions attached to Microsoft Fabric and Power BI, agents the
member had never had access to.

The backend asks a different question. `POST /instructions/{id}/resolve` (and
revert, and delete) calls `check_resource_permissions` over THAT instruction's
own data sources. So the click returned:

    Access denied to data_source 235e2e85-12e3-47f6-8445-f09a555c234a
    for 'manage_instructions'

— a raw uuid, no name, no indication of who could approve it instead, and no
way to have known before clicking.

★ The check is CONJUNCTIVE. The backend hands the whole `data_source_ids` list
to one call, so an instruction on two agents needs the grant on both. A gate
written with `.some()` would reopen the same hole for multi-agent instructions.

These are text assertions over a .vue file — they pin the wiring, not the
render. The browser check is: sign in as a member, open a pending change on an
agent you do not manage, and confirm there is no Accept button.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
EXPLORER = REPO / "frontend" / "components" / "KnowledgeExplorer.vue"
ROUTE = REPO / "backend" / "app" / "routes" / "instruction.py"
EN = REPO / "locales" / "en.json"


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _decl(src: str, name: str) -> str:
    """A declaration's text, from `const <name> =` to the next top-level one."""
    i = src.index(f"const {name} =")
    rest = src[i:]
    nxt = re.search(r"\n(?:const |function |async function |// ──)", rest[1:])
    return rest[: nxt.start() + 1] if nxt else rest


def test_a_per_instruction_gate_exists():
    body = _decl(_src(EXPLORER), "canApproveFor")
    assert "useCan('manage_instructions', { type: 'data_source', id: d.id })" in body, (
        "the gate must ask about THIS instruction's data sources, by id"
    )


def test_the_gate_requires_every_agent_not_any():
    body = _decl(_src(EXPLORER), "canApproveFor")
    assert ".every(" in body, "conjunctive — the backend checks the whole list at once"
    assert ".some(" not in body, (
        "`.some()` would let an instruction attached to one manageable agent and "
        "one unmanageable agent render an Accept button that 403s"
    )


def test_a_global_instruction_falls_back_to_the_org_permission():
    body = _decl(_src(EXPLORER), "canApproveFor")
    assert "if (!dss.length) return useCan('manage_instructions')" in body, (
        "an instruction with no data sources is gated by the route decorator "
        "alone, which is the org-level permission"
    )


def test_every_write_control_uses_the_per_instruction_gate():
    """Accept/reject, per-hunk accept, and delete all hit per-DS backend gates."""
    src = _src(EXPLORER)
    for needle, what in [
        (':can-approve="canApproveDetail"', "the tracked-changes reviewer (per-hunk Accept)"),
        ('v-if="diff.buildId && canApproveDetail"', "Accept all / Reject all"),
        ('v-if="!creating && canApproveDetail && !isBuiltinDetail"', "Delete"),
        ('<span v-if="canApproveDetail" class="invisible opacity-0', "the hover accept/reject card"),
    ]:
        assert needle in src, f"{what} is still gated on the any-resource flag"


def test_the_old_global_flag_no_longer_gates_a_write():
    """`canApprove` may still exist — it legitimately gates the pending-changes
    entry point, which is a read. What it must not gate is a write control."""
    src = _src(EXPLORER)
    for line in src.splitlines():
        if "canApprove" not in line or "canApproveDetail" in line or "canApproveFor" in line:
            continue
        for write in ("acceptAll", "rejectAll", "deleteInstruction", "can-approve"):
            assert write not in line, f"a write control still reads the global flag: {line.strip()[:120]}"


def test_the_user_is_told_which_agent_blocks_them():
    src = _src(EXPLORER)
    assert "const approvalBlockers" in src, "nothing computes the blocking agents"
    body = _decl(src, "approvalBlockers")
    assert ".map((d: any) => d.name)" in body, "the message must name agents, not print uuids"
    assert "agentsPage.approvalNeedsManage" in src, "no message is rendered"


def test_unapprovable_rows_are_marked_in_the_pending_list():
    src = _src(EXPLORER)
    assert 'v-if="!canApproveFor(ins)"' in src, (
        "the pending list still looks uniformly actionable — the dead end has "
        "to be visible before the click, not after it"
    )


def test_the_copy_exists():
    import json
    en = json.loads(_src(EN))
    ap = en["agentsPage"]
    assert ap["reviewOnly"]
    assert "{agents}" in ap["approvalNeedsManage"], "the message must interpolate the agent names"
    assert ap["approvalNeedsManageGeneric"]


def test_the_backend_gate_this_mirrors_is_still_there():
    """If the route stops checking per-DS, this whole fix is the wrong shape."""
    src = _src(ROUTE)
    i = src.index('@router.post("/instructions/{instruction_id}/resolve"')
    block = src[i:i + 2500]
    # ★0.0.528 routes this through `_require_instruction_authority`, which does
    # the same per-agent `check_resource_permissions` on every attached agent
    # (and requires org-level authority when there are none). Accept either the
    # inline call or the helper — asserting only the inline form would pass
    # vacuously the moment the route was refactored, which is what happened.
    gated = ('check_resource_permissions' in block and '"manage_instructions"' in block) \
        or '_require_instruction_authority' in block
    assert gated, (
        "resolve no longer gates per data source — re-read this test before "
        "assuming the frontend is what changed"
    )
    if '_require_instruction_authority' in block:
        src_all = _src(ROUTE)
        h = src_all[src_all.index("async def _require_instruction_authority"):][:2200]
        assert '"manage_instructions"' in h and 'check_resource_permissions' in h, (
            "the helper resolve now delegates to does not gate per data source"
        )
