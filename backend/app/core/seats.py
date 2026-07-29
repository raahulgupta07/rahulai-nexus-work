# Seat-limit enforcement for organization memberships.
#
# The enterprise license can cap the number of members (active members + pending
# invites) per organization via ``max_users``. This module is the single source of
# truth for counting memberships and deciding whether new ones fit under that cap,
# so every path that creates a Membership enforces the same rule:
#   - admin invite / CSV import      (app.services.organization_service)
#   - domain signup                  (app.core.auth._create_domain_invites)
#   - chat provision                 (app.core.auth, provision-on-first-message)
#   - LDAP / SSO login auto-provision(app.core.auth._place_auto_provisioned_user)
#   - LDAP group sync                (app.ee.ldap.sync_service)
#   - SCIM provisioning              (app.ee.scim.service)
#   - OIDC group sync                (app.ee.oidc.group_sync_service)
#
# ★★★The line about login auto-provision is new, and until it was added the word
# "every" above was false. Domain signup and chat provision did check; the path
# that admits somebody the moment a directory or an identity provider vouches
# for them — by far the highest-volume way this product gains members — did not.
# A customer licensed for N got N by sync and unlimited by sign-in: measured on
# this product, 200 directory users provisioned themselves past the cap without
# one check firing. Anything added to this list has to be grep-able. If
# `from app.core.seats import` does not appear in the named module, the line is
# fiction, and a list of paths is worth nothing if it is aspirational.
#
# Semantics ("block only truly new members"): only the creation of a *new*
# membership beyond the cap is refused. Existing members already count toward the
# total and are never re-added, so a re-login or re-sync of someone already in the
# org always passes — nobody is ever removed or locked out by the cap.

from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException


async def count_org_memberships(db: AsyncSession, organization_id) -> int:
    """Count live memberships in an org — active members and pending invites alike.

    Pending invites (user_id is NULL) count too, so the seat cap can't be bypassed
    by leaving invites unaccepted. Soft-deleted rows are excluded (membership
    removal is a hard delete today, so this matches the live total either way).
    """
    from app.models.membership import Membership

    result = await db.execute(
        select(func.count(Membership.id)).where(
            Membership.organization_id == organization_id,
            Membership.deleted_at.is_(None),
        )
    )
    return result.scalar() or 0


async def seats_remaining(db: AsyncSession, organization_id) -> Optional[int]:
    """Seats still available under the license cap, or ``None`` if unlimited.

    Returns ``None`` when no active license cap applies (``max_users == -1``).
    Otherwise returns ``max(0, cap - current_members)``; ``0`` means the org is full.
    """
    from app.ee.license import get_max_users

    max_users = get_max_users()
    if max_users < 0:
        return None
    current = await count_org_memberships(db, organization_id)
    return max(0, max_users - current)


async def has_seat_for(db: AsyncSession, organization_id, adding: int = 1) -> bool:
    """True if ``adding`` new membership(s) fit under the license seat cap.

    Always True when unlicensed/unset (unlimited). Use this on auto-provisioning
    paths that should degrade gracefully (skip/deny) rather than surface an error.
    """
    remaining = await seats_remaining(db, organization_id)
    if remaining is None:
        return True
    return adding <= remaining


async def enforce_seat_limit(db: AsyncSession, organization_id, adding: int = 1) -> None:
    """Raise HTTP 402 if ``adding`` member(s) would exceed the license seat cap.

    No-op when unlicensed/unset (unlimited). Use this on paths that surface the cap
    to a caller as an error (admin invite, SCIM provisioning).
    """
    if await has_seat_for(db, organization_id, adding):
        return

    from app.ee.license import get_max_users

    max_users = get_max_users()
    raise HTTPException(
        status_code=402,
        detail=(
            f"User limit reached for your license ({max_users}). "
            "Contact sales to increase your seat count."
        ),
    )
