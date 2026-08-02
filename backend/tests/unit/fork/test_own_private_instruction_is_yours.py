"""A member could create a private instruction and then not be allowed to edit it.

`Group promotions by PROMOTION_CODE only` — is_private = true, author
member@cityagent.io — returned:

    [PUT] "/api/instructions/d34b420a-…": 403 Forbidden

and its pending suggestion showed "Approving this change needs manage
instructions on Microsoft Fabric".

★ THE PRODUCT ALREADY AGREED WITH THE USER. Three layers had it right:

  * the route docstring: "Update an instruction (only if private and user owns it)"
  * permissions_decorator.py:208 — an explicit Instruction-owner allowance that
    lets a non-admin author through when the instruction is not approved
  * instruction_service._determine_update_type → `owner_edit`, applying
    _handle_owner_edit's whitelist: text, title, description, category, kind,
    is_seen, can_user_toggle

Only the per-agent `check_resource_permissions` in each route BODY disagreed,
and it ran before any of that could take effect. `manage_instructions` protects
what the organization SHARES; a private note is loaded into nobody else's
context and shared with nobody, so it was the wrong permission to ask for.

★ WHY THIS IS NOT AN ESCALATION. The bypass is private-and-author only. The
owner cannot make the note shared: `is_private`, `status` and the global fields
are absent from the owner whitelist, so the write layer drops them. Attaching to
a NEW agent is still checked — on `view`, since nothing shared is written. A
shared instruction still needs `manage_instructions` regardless of who wrote it.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
ROUTE = REPO / "backend" / "app" / "routes" / "instruction.py"
SERVICE = REPO / "backend" / "app" / "services" / "instruction_service.py"
DECORATOR = REPO / "backend" / "app" / "core" / "permissions_decorator.py"
SCHEMA = REPO / "backend" / "app" / "schemas" / "instruction_schema.py"
EXPLORER = REPO / "frontend" / "components" / "KnowledgeExplorer.vue"


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _route_body(src: str, decl: str) -> str:
    i = src.index(decl)
    rest = src[i:]
    nxt = re.search(r"\n@router\.", rest[1:])
    return rest[: nxt.start() + 1] if nxt else rest


# ── the helper ───────────────────────────────────────────────────────────────

def test_the_helper_requires_both_private_and_author():
    src = _src(ROUTE)
    body = src[src.index("def owns_private_instruction"):src.index("async def _assert_manage_scope")]
    assert 'getattr(instruction, "is_private", False)' in body, (
        "without the private check this would hand the author of a SHARED "
        "instruction the agent permission they were denied"
    )
    assert 'str(user.id)' in body and 'user_id' in body, "author check missing"


# ── every write path on one's own instruction ────────────────────────────────

WRITE_ROUTES = [
    ('@router.put("/instructions/{instruction_id}"', "edit — the 403 that was reported"),
    ('@router.delete("/instructions/{instruction_id}")', "delete your own note"),
    ('@router.post("/instructions/{instruction_id}/resolve"', "accept/reject a suggested change"),
    ('@router.post("/instructions/{instruction_id}/hunks/accept"', "accept one hunk"),
    ('@router.post("/instructions/{instruction_id}/hunks/reject"', "reject one hunk"),
    ('@router.post("/instructions/{instruction_id}/hunks/accept-all"', "accept all"),
    ('@router.post("/instructions/{instruction_id}/hunks/reject-all"', "reject all"),
    ('@router.post("/instructions/{instruction_id}/accept-staged"', "accept a staged build"),
    ('@router.post("/instructions/{instruction_id}/versions/{version_id}/revert"', "revert a version"),
    ('@router.post("/instructions/{instruction_id}/improve"', "AI rewrite of your own text"),
]


def test_every_write_path_lets_the_author_through():
    src = _src(ROUTE)
    missing = []
    for decl, what in WRITE_ROUTES:
        body = _route_body(src, decl)
        if "existing_ds_ids" not in body:
            continue  # no per-DS gate to bypass
        if "owns_private_instruction(existing, current_user)" not in body:
            missing.append(f"{what} ({decl.split(chr(34))[1]})")
    assert not missing, "still 403s on the author's own private instruction:\n  " + "\n  ".join(missing)


def test_no_per_ds_gate_was_left_unguarded():
    """Catches a route added later that copies the old pattern."""
    src = _src(ROUTE)
    for m in re.finditer(r'"data_source", existing_ds_ids, "manage_instructions"', src):
        window = src[max(0, m.start() - 400):m.start()]
        assert "owns_private_instruction(existing, current_user)" in window, (
            "a per-agent gate on an existing instruction with no author bypass — "
            f"near offset {m.start()}"
        )


# ── the escalation that must stay closed ─────────────────────────────────────

def test_an_owner_cannot_publish_their_private_note():
    """The whole safety argument rests on this whitelist."""
    src = _src(SERVICE)
    i = src.index("async def _handle_owner_edit")
    body = src[i:i + 1200]
    m = re.search(r"allowed_fields = \[(.*?)\]", body, re.S)
    assert m, "the owner whitelist is gone — re-read this test before trusting the bypass"
    allowed = m.group(1)
    for forbidden in ("is_private", "status", "global_status", "is_global"):
        assert f"'{forbidden}'" not in allowed, (
            f"'{forbidden}' is writable by an owner — a member could flip their "
            f"private note to shared and bypass manage_instructions entirely"
        )


def test_attaching_to_a_new_agent_is_still_checked():
    body = _route_body(_src(ROUTE), '@router.put("/instructions/{instruction_id}"')
    assert '"data_source", added, "view"' in body, (
        "editing your own note must not carry the right to attach it to an "
        "agent you cannot see"
    )
    assert "added = [i for i in instruction.data_source_ids if str(i) not in set(existing_ds_ids)]" in body


def test_a_shared_instruction_still_needs_the_agent_permission():
    """The bypass must be private-only; this is the line between the two."""
    body = _route_body(_src(ROUTE), '@router.put("/instructions/{instruction_id}"')
    assert 'own_private = owns_private_instruction(existing, current_user)' in body
    assert 'if existing_ds_ids and not own_private:' in body


# ── the layers that already agreed ───────────────────────────────────────────

def test_the_decorator_owner_allowance_is_still_there():
    """If this goes, the author is stopped before the route body runs and the
    bypass below it becomes dead code."""
    src = _src(DECORATOR)
    assert "Special owner allowance: Instruction owner may modify/delete when not published" in src
    assert "is_owner = obj and getattr(obj, 'user_id', None) == user.id" in src


def test_the_service_still_routes_an_owner_to_owner_edit():
    src = _src(SERVICE)
    i = src.index("def _determine_update_type")
    body = src[i:i + 900]
    assert "is_owner = instruction.user_id == current_user.id" in body
    assert 'return "owner_edit"' in body


# ── the frontend must show what the backend now allows ───────────────────────

def test_the_ui_gate_knows_about_ownership():
    src = _src(EXPLORER)
    assert "const ownsPrivate" in src, "the UI would still hide the buttons it is now allowed to show"
    body = src[src.index("const ownsPrivate"):src.index("const canApproveFor")]
    assert "is_private" in body and "user_id" in body
    gate = src[src.index("const canApproveFor"):]
    assert "if (ownsPrivate(ins)) return true" in gate[:400]


def test_the_payload_actually_carries_the_two_fields():
    """The UI check is worthless if the API does not send them."""
    src = _src(SCHEMA)
    i = src.index("class InstructionSchema")
    block = src[i:i + 2500]
    assert "user_id: Optional[str] = None" in block, "detail payload has no author"
    # is_private is inherited from InstructionBase
    assert "is_private: bool = False" in src[:i], "is_private is not on the base schema"
