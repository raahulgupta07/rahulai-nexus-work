# OIDC Group Sync Service
# Licensed under the Business Source License 1.1
#
# Syncs group memberships from OIDC token claims (e.g., Entra ID groups)
# into BOW Groups + GroupMemberships on each user login.

import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.group import Group
from app.models.group_membership import GroupMembership
from app.models.membership import Membership
from app.ee.ldap.schemas import SyncResult

logger = logging.getLogger(__name__)

PROVIDER_NAME = "oidc"

_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def unresolved_group_label(external_id: str) -> str:
    """Display name for a claim value that never resolved to a group name.

    Only opaque object ids get the placeholder. Plenty of providers (Okta,
    Keycloak, Auth0, and Entra when the groups claim is configured to emit
    sAMAccountName) put the readable group name straight in the claim, with no
    Graph lookup involved — labelling "Engineering" as unresolved would be a
    downgrade, so a non-GUID claim value is taken as the name it already is.
    """
    if not _GUID_RE.match(external_id):
        return external_id
    return f"Unresolved directory group ({external_id[:8]}…)"


def _resolved_name(group_names: Dict[str, Optional[str]], external_id: str) -> Optional[str]:
    """The real display name for a claim id, or None if it never resolved.

    A name equal to the external id is treated as unresolved: older callers
    backfilled unresolved ids with the id itself, and rows created that way are
    still in the database.
    """
    name = (group_names.get(external_id) or "").strip()
    if not name or name == external_id:
        return None
    return name


async def sync_user_oidc_groups(
    db: AsyncSession,
    user_id: str,
    organization_id: str,
    group_ids: List[str],
    group_names: Optional[Dict[str, Optional[str]]] = None,
) -> SyncResult:
    """Sync a single user's group memberships from OIDC token claims.

    Args:
        db: Database session.
        user_id: The BOW user ID.
        organization_id: The org to sync groups into.
        group_ids: List of external group IDs from the id_token groups claim.
        group_names: Optional mapping of group_id → display name (from Graph API).
                     An id that is absent, or maps to None, counts as unresolved
                     and gets a placeholder name via ``unresolved_group_label``.

    Returns:
        SyncResult with counts of groups created/updated and memberships added/removed.
    """
    result = SyncResult(timestamp=datetime.now(timezone.utc))
    if not group_ids:
        return result

    group_names = group_names or {}

    # Get existing OIDC-synced groups for this org
    existing_groups = await _get_oidc_groups(db, organization_id)
    existing_by_ext_id: Dict[str, Group] = {
        g.external_id: g for g in existing_groups if g.external_id
    }

    # ★★★And every group in the org by NAME — any provider, tombstones included.
    # `groups` is unique on (organization_id, name), with no provider column and
    # no `deleted_at` predicate in the constraint, so a name is equally taken by
    # a hand-made group, an LDAP-synced one, or a soft-deleted row. Keying only
    # on `external_id` made this INSERT a name that was already held, and the
    # IntegrityError aborted the sync. This path runs during LOGIN, so the cost
    # is a user signing in without the groups that decide what they can see.
    # An instance running LDAP and OIDC together — which is the common case for
    # Entra or Keycloak beside AD — will eventually produce the same display
    # name from both directories.
    existing_by_name: Dict[str, Group] = await _get_groups_by_name(db, organization_id)

    # Upsert groups from token claims
    # ★Seeded from the WHOLE claim, not filled in as the loop goes: the
    # adopt-by-name branch below asks whether another id is live in this same
    # claim, and an id appearing later in the list is just as live as one already
    # seen. Building it incrementally makes that answer depend on claim order.
    token_group_ids: Set[str] = set(group_ids)
    for ext_id in group_ids:
        # Graph can't always resolve a claim id to a display name (directory
        # roles, or groups the app has no permission to read). Falling back to
        # the bare GUID put rows like "85f43b45-99ae-43a0-a780-a05c119e8b9c" in
        # the admin's group list with no hint of what they are or why. Label
        # them so they read as unresolved rather than as a group name.
        resolved = _resolved_name(group_names, ext_id)
        name = resolved or unresolved_group_label(ext_id)

        if ext_id in existing_by_ext_id:
            group = existing_by_ext_id[ext_id]
            if resolved:
                # A real name always wins — including over a placeholder written
                # on an earlier login when Graph was unavailable or unconsented.
                want = resolved if group.name != resolved else None
            elif group.name == ext_id and name != group.name:
                # Legacy row named by raw GUID, created before unresolved ids
                # were labelled. Relabel it; a resolved name is never overwritten
                # with the placeholder, so a transient Graph failure can't erase
                # names. A readable claim value labels to itself, hence the !=
                # guard: that row is already correct and is not an update.
                want = name
            else:
                want = None
            if want is not None:
                # ★Only rename onto a free name; the constraint does not care
                # that we mean well. The external id is the identity anyway.
                clash = existing_by_name.get(want)
                if clash is None or str(clash.id) == str(group.id):
                    existing_by_name.pop(group.name, None)
                    group.name = want
                    existing_by_name[want] = group
                    result.groups_updated += 1
            continue

        claimed = existing_by_name.get(name)

        if claimed is not None and claimed.external_provider == PROVIDER_NAME:
            if claimed.external_id in token_group_ids:
                # ★★★Both ids are LIVE in this very claim, so they are two
                # different directory objects that merely want the same name —
                # not one object reissued under a new id. Re-pointing would merge
                # them onto one row, and this token then names that row twice, so
                # the membership insert violates UNIQUE (group_id, user_id) and
                # the whole login-time sync raises. Reachable because the
                # unresolved-id placeholder keeps only 8 hex digits: two ids
                # sharing that prefix produce one name. Skipped, exactly like the
                # branch below — the user does not gain the group, which is the
                # safe direction for an access decision.
                logger.warning(
                    "OIDC group sync: group '%s' (external_id=%s) skipped in org "
                    "%s — external_id %s in the same claim already holds that name",
                    name, ext_id, organization_id, claimed.external_id,
                )
                continue
            # ★Ours under a different external id — the directory reissued it,
            # or an earlier sync stored the raw GUID before Graph could resolve
            # the display name. Re-point rather than duplicate the name.
            claimed.deleted_at = None
            claimed.external_id = ext_id
            existing_by_ext_id[ext_id] = claimed
            result.groups_updated += 1
            continue

        if claimed is not None:
            # ★★★Someone else owns this name — LDAP, SCIM, or a person. NOT
            # adopted: this runs on every login, so silently taking over a
            # hand-made group would let a login rewrite an admin's membership
            # list. Skipped and logged; the user simply does not gain that
            # group, which is the safe direction for an access decision.
            logger.warning(
                "OIDC group sync: group '%s' (external_id=%s) skipped in org %s — "
                "a group of that name already exists from '%s'",
                name, ext_id, organization_id, claimed.external_provider or "manual",
            )
            continue

        group = Group(
            organization_id=organization_id,
            name=name,
            external_id=ext_id,
            external_provider=PROVIDER_NAME,
        )
        db.add(group)
        # ★SAVEPOINT: two concurrent logins can race to create the same group.
        # Without it the loser poisons the session and the whole login-time sync
        # raises PendingRollbackError instead of losing one group.
        try:
            async with db.begin_nested():
                await db.flush()
        except IntegrityError as e:
            logger.warning(
                "OIDC group sync: could not create group '%s' (external_id=%s) "
                "in org %s: %s", name, ext_id, organization_id, e.orig,
            )
            continue
        existing_by_ext_id[ext_id] = group
        existing_by_name[name] = group
        result.groups_created += 1
        logger.info(
            f"OIDC group sync: created group '{name}' (external_id={ext_id}) "
            f"in org {organization_id}"
        )
        if not resolved and _GUID_RE.match(ext_id):
            logger.warning(
                "OIDC group sync: claim id %s did not resolve to a group name. "
                "It is likely a directory role or a group the app registration "
                "cannot read — grant Group.Read.All (application) to name it.",
                ext_id,
            )

    # Sync this user's memberships
    # Current: OIDC groups user is currently a member of
    current_group_ext_ids = await _get_user_oidc_group_ext_ids(
        db, user_id, organization_id
    )
    # Target: OIDC groups from the token
    target_group_ext_ids = token_group_ids

    to_add = target_group_ext_ids - current_group_ext_ids
    to_remove = current_group_ext_ids - target_group_ext_ids

    # ★★★Deduplicate by GROUP, not by external id. `group_memberships` is UNIQUE
    # on (group_id, user_id), and two claim ids can legitimately resolve to one
    # Group row — the adopt-by-name branch re-points a reissued id onto the row
    # the old id still occupies. Inserting twice raises IntegrityError on the
    # session, and this runs during LOGIN, so the cost is a user who cannot sign
    # in at all. The branch above prevents the case we know how to detect; this
    # closes the class regardless of how two ids came to share a row.
    added_group_ids: Set[str] = set()
    for ext_id in to_add:
        group = existing_by_ext_id.get(ext_id)
        if group is None or str(group.id) in added_group_ids:
            continue
        added_group_ids.add(str(group.id))
        db.add(GroupMembership(group_id=group.id, user_id=user_id))
        result.memberships_added += 1

    if to_remove:
        # Find and delete memberships for groups the user is no longer in
        for ext_id in to_remove:
            group = existing_by_ext_id.get(ext_id)
            if not group:
                continue
            stmt = select(GroupMembership).where(
                GroupMembership.group_id == group.id,
                GroupMembership.user_id == user_id,
            )
            row = (await db.execute(stmt)).scalar_one_or_none()
            if row:
                await db.delete(row)
                result.memberships_removed += 1

    # Ensure user has org Membership
    await _ensure_org_membership(db, organization_id, user_id)

    await db.commit()

    logger.info(
        f"OIDC group sync for user {user_id} in org {organization_id}: "
        f"created={result.groups_created} updated={result.groups_updated} "
        f"added={result.memberships_added} removed={result.memberships_removed}"
    )
    return result


async def _get_groups_by_name(
    db: AsyncSession, organization_id: str
) -> Dict[str, Group]:
    """Every group in the org by name — any provider, tombstones included.

    ★Wider than `_get_oidc_groups` on purpose: `uq_groups_org_name` covers
    (organization_id, name) with no provider column and no `deleted_at`
    predicate, so a narrower lookup reports a taken name as free.
    """
    stmt = select(Group).where(Group.organization_id == organization_id)
    out: Dict[str, Group] = {}
    for g in (await db.execute(stmt)).scalars().all():
        prev = out.get(g.name)
        if prev is None or (prev.deleted_at is not None and g.deleted_at is None):
            out[g.name] = g
    return out


async def _get_oidc_groups(db: AsyncSession, organization_id: str) -> List[Group]:
    """Get all OIDC-synced groups for an org."""
    stmt = (
        select(Group)
        .where(Group.organization_id == organization_id)
        .where(Group.external_provider == PROVIDER_NAME)
        .where(Group.deleted_at.is_(None))
    )
    return list((await db.execute(stmt)).scalars().all())


async def _get_user_oidc_group_ext_ids(
    db: AsyncSession, user_id: str, organization_id: str
) -> Set[str]:
    """Get external_ids of OIDC groups the user is currently a member of."""
    stmt = (
        select(Group.external_id)
        .join(GroupMembership, GroupMembership.group_id == Group.id)
        .where(Group.organization_id == organization_id)
        .where(Group.external_provider == PROVIDER_NAME)
        .where(Group.deleted_at.is_(None))
        .where(GroupMembership.user_id == user_id)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return set(r for r in rows if r)


async def _ensure_org_membership(
    db: AsyncSession, organization_id: str, user_id: str
) -> None:
    """Ensure the user has an org Membership (create if missing)."""
    stmt = (
        select(Membership)
        .where(Membership.organization_id == organization_id)
        .where(Membership.user_id == user_id)
        .where(Membership.deleted_at.is_(None))
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if not existing:
        # Enforce the license seat cap: a full org can't gain new members via OIDC
        # group sync. Existing members hit the branch above and are unaffected — only
        # a brand-new membership beyond the cap is refused (skipped + logged).
        from app.core.seats import has_seat_for
        if not await has_seat_for(db, organization_id):
            logger.warning(
                "OIDC group sync: org %s is at its license seat limit; "
                "skipping new membership for user %s", organization_id, user_id
            )
            return
        db.add(Membership(
            user_id=user_id,
            organization_id=organization_id,
            role="member",
        ))
        # Give the user a real RBAC assignment (not just the legacy string).
        from app.core.permission_resolver import ensure_system_role_assignment
        await ensure_system_role_assignment(db, organization_id, str(user_id), "member")
        logger.info(
            f"OIDC group sync: auto-created org membership for user {user_id} "
            f"in org {organization_id}"
        )
