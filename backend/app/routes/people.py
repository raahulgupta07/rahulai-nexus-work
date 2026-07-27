from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_user
from app.core.permissions_decorator import requires_permission
from app.dependencies import get_async_db, get_current_organization
from app.models.group import Group
from app.models.group_membership import GroupMembership
from app.models.membership import Membership
from app.models.oauth_account import OAuthAccount
from app.models.organization import Organization
from app.models.user import User
from app.schemas.people_schema import IdentityView, PersonGroupView, PersonView

router = APIRouter(tags=["people"])


@router.get(
    "/organizations/{organization_id}/people",
    response_model=List[PersonView],
)
# Administration data, not a picker: this returns every person in the org with
# their email, role, linked identity providers and join date. `view_members` is
# baseline for every member (it backs sharing pickers), so gating on it exposed
# the whole staff directory to any signed-in member. Hiding the tab in the UI is
# not access control — the gate has to move here too.
@requires_permission('manage_settings')
async def get_people(
    organization_id: str,
    db: AsyncSession = Depends(get_async_db),
    organization: Organization = Depends(get_current_organization),
    current_user: User = Depends(current_user),
) -> List[PersonView]:
    """Every registered person in the org with their merged identities.

    Accounts are unified by email, so a single person surfaces one ``local``
    identity (their password) plus one ``oauth`` identity per linked SSO/OAuth
    account, alongside their group memberships. Read-only. ``organization`` is
    the header-resolved, membership-authorized org (mirrors the members
    endpoint); we scope every query to ``organization.id``.
    """
    from app.core.permission_resolver import resolve_permissions, FULL_ADMIN

    org_id = organization.id

    # (a) Registered memberships (user_id present) for this org.
    membership_rows = (
        await db.execute(
            select(Membership).where(
                Membership.organization_id == org_id,
                Membership.user_id.isnot(None),
            )
        )
    ).scalars().all()

    if not membership_rows:
        return []

    role_by_user: dict = {m.user_id: m for m in membership_rows}
    user_ids = list(role_by_user.keys())

    # (b) The backing User rows.
    user_rows = (
        await db.execute(select(User).where(User.id.in_(user_ids)))
    ).scalars().all()
    users_by_id = {u.id: u for u in user_rows}

    # (c) OAuth accounts, grouped by user_id (batched, no N+1).
    oauth_rows = (
        await db.execute(
            select(OAuthAccount).where(OAuthAccount.user_id.in_(user_ids))
        )
    ).scalars().all()
    oauth_by_user: dict = {}
    for oa in oauth_rows:
        oauth_by_user.setdefault(oa.user_id, []).append(oa)

    # (d) Group memberships joined to Group, scoped to this org, by user_id.
    group_rows = (
        await db.execute(
            select(GroupMembership.user_id, Group.name, Group.external_provider)
            .join(Group, Group.id == GroupMembership.group_id)
            .where(
                Group.organization_id == org_id,
                GroupMembership.user_id.in_(user_ids),
            )
        )
    ).all()
    groups_by_user: dict = {}
    for uid, gname, gprovider in group_rows:
        groups_by_user.setdefault(uid, []).append(
            PersonGroupView(name=gname, source=gprovider or "manual")
        )

    people: List[PersonView] = []
    for user_id in user_ids:
        user = users_by_id.get(user_id)
        if user is None:
            # Membership references a user row that no longer exists; skip.
            continue
        membership = role_by_user[user_id]

        # Role label derived from RBAC so it never disagrees with effective
        # permissions (mirrors the members endpoint; the Membership.role column
        # can drift on role changes). 'admin' is the top/owner role here.
        resolved = await resolve_permissions(db, str(user_id), str(org_id))
        role = "admin" if FULL_ADMIN in resolved.org_permissions else "member"
        is_owner = role in ("owner", "admin")

        has_password = bool(getattr(user, "hashed_password", None))

        # (e) Merge identities: local (if a password exists) + one per oauth.
        identities: List[IdentityView] = []
        if has_password:
            identities.append(
                IdentityView(
                    kind="local",
                    provider="local",
                    account_email=user.email,
                    account_id=None,
                    is_primary=True,
                )
            )

        user_oauth = oauth_by_user.get(user_id, [])
        # Stable order; when there's no password the earliest oauth is primary.
        user_oauth = sorted(
            user_oauth, key=lambda oa: (oa.account_email or "", oa.account_id or "")
        )
        for idx, oa in enumerate(user_oauth):
            identities.append(
                IdentityView(
                    kind="oauth",
                    provider=oa.oauth_name,
                    account_email=oa.account_email,
                    account_id=oa.account_id,
                    is_primary=(not has_password and idx == 0),
                )
            )

        people.append(
            PersonView(
                user_id=str(user_id),
                email=user.email,
                name=user.name,
                role=role,
                is_owner=is_owner,
                created_at=membership.created_at,
                has_password=has_password,
                identities=identities,
                groups=groups_by_user.get(user_id, []),
            )
        )

    people.sort(key=lambda p: ((p.name or "").lower(), (p.email or "").lower()))
    return people
