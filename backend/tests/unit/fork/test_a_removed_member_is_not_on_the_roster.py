"""Removal is a soft delete. Every roster must honour it.

Measured on a live installation: the Members screen listed **29 rows of which
ONE was live**. Removed people appeared as Active, with a working Remove button
for a membership that was already gone. The administrator's answer to "who has
access to this workspace" was wrong by a factor of 29.

They genuinely could not get in — `principal_belongs_to_org` filters
`deleted_at` — so the list and the gate behind it disagreed, and only the list
was visible. That is the same defect as the workspace switcher, and this file
exists because finding it twice means it will be written a third time.

★The rule: any query that answers "who belongs here" must filter `deleted_at`,
because the permission check does. A roster that disagrees with its gate is
worse than no roster — it is confidently wrong.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SVC = REPO / "backend" / "app" / "services" / "organization_service.py"


def _fn(src: str, header: str) -> str:
    start = src.index(header)
    rest = src[start + len(header):]
    nxt = re.search(r"\n    (?:async )?def ", rest)
    return rest[: nxt.start()] if nxt else rest


def test_the_members_screen_excludes_removed_people():
    """★The one that shipped: the Members page with its Sign-in column."""
    src = SVC.read_text(encoding="utf-8")
    body = _fn(src, "    async def get_members(")
    assert "Membership.deleted_at.is_(None)" in body, (
        "the Members screen lists people who have been removed, and shows "
        "them as Active"
    )


def test_the_plain_member_list_excludes_removed_people():
    src = SVC.read_text(encoding="utf-8")
    body = _fn(src, "    async def get_organization_members(")
    assert "Membership.deleted_at.is_(None)" in body, (
        "the member list used by pickers offers people who were removed"
    )


def test_the_workspace_switcher_still_excludes_them():
    """★The first instance of this defect. Pinned so a refactor cannot undo it."""
    src = SVC.read_text(encoding="utf-8")
    body = _fn(src, "    async def get_user_organizations(")
    assert "Membership.deleted_at.is_(None)" in body, (
        "the workspace switcher offers workspaces the person was removed from"
    )


def test_the_permission_check_is_the_thing_they_must_agree_with():
    """★The premise. If the CHECK ever stops filtering deleted_at, these
    rosters are no longer wrong to include them — and this file should be
    re-read rather than obeyed."""
    src = (REPO / "backend" / "app" / "core" / "permission_resolver.py").read_text(encoding="utf-8")
    body = _fn(src, "async def principal_belongs_to_org(")
    assert "Membership.deleted_at.is_(None)" in body, (
        "the membership CHECK no longer filters deleted_at — re-read "
        "test_a_removed_member_is_not_on_the_roster.py"
    )
