"""The LDAP sync emptied a production organization. All of it, hourly.

WHAT HAPPENED
-------------
`_cleanup_org_memberships` deactivates people who have left every LDAP group.
Its own comment said "only delete if user was originally LDAP-provisioned" —
and nothing implemented that. The query selected EVERY live membership in the
organization whose user was not currently in an LDAP group, which is every SSO
user, every local user and every invited member.

Measured on the live database after this had run hourly for weeks:

    29 memberships, 1 live.

The survivor held full_admin_access, which is the one case the code protects.
Of the 28 removed, 16 belonged to users with NO ldap_dn at all — they had
signed in through SSO and had nothing to do with the directory. The whole
organization had been emptied by a sync, and it presented to users as
mysteriously losing access.

★A directory may only remove what it provisioned. `User.ldap_dn` is the
recorded origin — the same field the sign-in doors route on — and a row
without it is not the directory's to touch.

★An empty directory result is not "everybody left". A renamed group, an edited
search base or a bind account that lost its rights all produce an empty set,
and the plain reading of an empty set deactivates everyone. A directory that
has genuinely lost all its members is vanishingly rare; a misconfigured one is
common.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SVC = REPO / "backend" / "app" / "ee" / "ldap" / "sync_service.py"


def _fn(src: str, header: str) -> str:
    start = src.index(header)
    rest = src[start + len(header):]
    nxt = re.search(r"\n    (?:async )?def ", rest)
    return rest[: nxt.start()] if nxt else rest


def _strip_comments(text: str) -> str:
    """Blank `#` comments, keeping line count.

    ★Required: the comment explaining this fix quotes the broken intent
    verbatim, and a scan over raw text would match its own documentation.
    """
    return "\n".join(l.split("#", 1)[0] for l in text.splitlines())


def test_only_directory_provisioned_members_can_be_removed():
    """★The 16 SSO users. Without this filter they are swept every hour."""
    body = _strip_comments(_fn(SVC.read_text(encoding="utf-8"), "    async def _cleanup_org_memberships("))
    assert "User.ldap_dn" in body, (
        "the cleanup does not restrict itself to directory-provisioned "
        "members, so it removes SSO, local and invited people too"
    )
    assert re.search(r"User\.ldap_dn\.isnot\(None\)", body), (
        "the ldap_dn restriction is not the 'was provisioned by LDAP' test"
    )


def test_an_empty_directory_result_removes_nobody():
    body = _strip_comments(_fn(SVC.read_text(encoding="utf-8"), "    async def _cleanup_org_memberships("))
    assert "if not users_still_in_ldap" in body, (
        "an empty directory result is treated as everybody having left, which "
        "deactivates the entire organization"
    )
    # and it must RETURN, not merely log
    seg = body[body.index("if not users_still_in_ldap"):]
    assert "return" in seg[:600], "the empty-result branch logs but still proceeds to delete"


def test_the_admin_protection_is_still_there():
    """★Positive control. The fixes above must not replace the existing guard —
    it is the only reason anyone at all survived in production."""
    body = _fn(SVC.read_text(encoding="utf-8"), "    async def _cleanup_org_memberships(")
    assert "FULL_ADMIN" in body, "the admin protection was removed"


def test_membership_in_two_manual_groups_does_not_abort_the_sweep():
    """★`scalar_one_or_none` raises on two rows. Aborting mid-sweep leaves some
    memberships deactivated and others not — a half-applied removal."""
    # ★Comments stripped: the comment explaining THIS fix names
    # scalar_one_or_none verbatim, and the first version of this test failed
    # against the corrected file, citing its own documentation. Third time
    # this trap has fired in this codebase.
    body = _strip_comments(_fn(SVC.read_text(encoding="utf-8"), "    async def _cleanup_org_memberships("))
    assert "scalar_one_or_none" not in body, (
        "an existence check still uses scalar_one_or_none, which raises when a "
        "user belongs to two non-directory groups"
    )
