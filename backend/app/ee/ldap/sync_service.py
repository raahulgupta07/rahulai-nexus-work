# LDAP Group Sync Service
# Licensed under the Business Source License 1.1
# See ENTERPRISE_LICENSE for details

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.group import Group
from app.models.group_membership import GroupMembership
from app.models.user import User
from app.models.membership import Membership
from app.ee.ldap.connection import LDAPConnectionManager
from app.ee.ldap.schemas import SyncResult, LDAPSyncPreview, LDAPGroupPreview
from app.settings.dash_config import LDAPConfig

logger = logging.getLogger(__name__)

PROVIDER_NAME = "ldap"


class LDAPGroupSyncService:

    def __init__(self, config: LDAPConfig):
        self.config = config
        self.connection = LDAPConnectionManager(config)

    async def sync_groups(self, db: AsyncSession, organization_id: str) -> SyncResult:
        """
        Full sync of LDAP groups into the application.

        Algorithm:
        1. Fetch all LDAP groups and users
        2. Build DN→email lookup
        3. Match LDAP groups to existing Group records by external_id
        4. Create/update groups, diff memberships
        5. Mark removed LDAP groups as deleted
        """
        result = SyncResult(timestamp=datetime.now(timezone.utc))

        try:
            ldap_groups = self.connection.search_groups()
            ldap_users = self.connection.search_users()
        except Exception as e:
            result.errors.append(f"LDAP search failed: {e}")
            logger.error(f"LDAP sync failed for org {organization_id}: {e}")
            return result

        # Build DN→email map for member resolution
        dn_to_email: Dict[str, str] = {u["dn"]: u["email"] for u in ldap_users}

        # Build email→user_id map for ALL app users (not just org members)
        # so we can auto-create Membership when a user appears in an LDAP group
        email_to_user_id = await self._get_all_user_map(db)

        # Track which users are in at least one LDAP group (for removal logic)
        users_in_any_ldap_group: Set[str] = set()

        # Get existing LDAP-synced groups for this org
        existing_groups = await self._get_ldap_groups(db, organization_id)
        existing_by_dn: Dict[str, Group] = {g.external_id: g for g in existing_groups if g.external_id}

        # ★★★Every group in the org keyed by NAME — any provider, and INCLUDING
        # soft-deleted rows. `groups` is unique on (organization_id, name) and
        # that constraint is not partial, so a tombstone still occupies the name.
        # Matching only on DN meant a name already taken produced an INSERT and
        # an IntegrityError; because the commit is at the END of this method,
        # that aborted the ENTIRE sync — every group, every membership — and it
        # repeated on the next tick forever. Measured in production 2026-08-04:
        # `Administrators` failed hourly for five hours and LDAP sync was dead.
        existing_by_name: Dict[str, Group] = await self._get_groups_by_name(db, organization_id)

        seen_dns: Set[str] = set()

        for ldap_group in ldap_groups:
            group_dn = ldap_group["dn"]
            group_name = ldap_group["name"]
            seen_dns.add(group_dn)

            # Resolve LDAP members to app user IDs
            target_user_ids = self._resolve_members(
                ldap_group["members"], dn_to_email, email_to_user_id, result
            )
            users_in_any_ldap_group.update(target_user_ids)

            # Ensure all target users have an org Membership
            await self._ensure_org_memberships(db, organization_id, target_user_ids)

            if group_dn in existing_by_dn:
                # Update existing group
                group = existing_by_dn[group_dn]
                if group.name != group_name:
                    # ★Renaming onto a name another group already holds would
                    # hit the same constraint. Leave the old name and say so;
                    # the DN is the identity, the name is only a label.
                    clash = existing_by_name.get(group_name)
                    if clash is not None and str(clash.id) != str(group.id):
                        result.errors.append(
                            f"group '{group_name}' ({group_dn}): another group in this "
                            f"organization already uses that name; keeping '{group.name}'"
                        )
                    else:
                        existing_by_name.pop(group.name, None)
                        group.name = group_name
                        existing_by_name[group_name] = group
                        result.groups_updated += 1

                await self._sync_memberships(db, group, target_user_ids, result)
                continue

            claimed = existing_by_name.get(group_name)

            if claimed is not None and claimed.external_provider == PROVIDER_NAME:
                # ★Ours already, under a different DN or sitting as a tombstone
                # this same sync wrote when the group briefly left the directory.
                # Revive and re-point it rather than inserting a duplicate name.
                claimed.deleted_at = None
                claimed.external_id = group_dn
                existing_by_dn[group_dn] = claimed
                result.groups_updated += 1
                await self._sync_memberships(db, claimed, target_user_ids, result)
                continue

            if claimed is not None:
                # ★★★A group of this name exists that LDAP does NOT own — created
                # by hand, by SCIM, or by OIDC. Deliberately NOT adopted: taking
                # it over would hand its membership to the directory and drop
                # everyone an admin added by hand, silently. Skipping keeps that
                # decision with a person. Recorded as an error so it surfaces in
                # the sync result instead of vanishing.
                result.errors.append(
                    f"group '{group_name}' ({group_dn}) skipped: a group with that "
                    f"name already exists in this organization"
                    + (f" from '{claimed.external_provider}'" if claimed.external_provider
                       else " (created manually)")
                    + " — rename one of them to let LDAP manage it"
                )
                continue

            # Create new group
            group = Group(
                organization_id=organization_id,
                name=group_name,
                external_id=group_dn,
                external_provider=PROVIDER_NAME,
            )
            db.add(group)
            # ★A SAVEPOINT, so a name collision this lookup did not predict
            # (another worker syncing the same org concurrently) costs one group
            # instead of the whole run. Without it the session is poisoned and
            # every later flush raises PendingRollbackError — five of those in
            # the production log came from exactly this.
            try:
                async with db.begin_nested():
                    await db.flush()
            except IntegrityError as e:
                result.errors.append(
                    f"group '{group_name}' ({group_dn}) could not be created: {e.orig}"
                )
                continue
            existing_by_dn[group_dn] = group
            existing_by_name[group_name] = group
            result.groups_created += 1

            await self._sync_memberships(db, group, target_user_ids, result)

        # Remove groups no longer in LDAP
        for dn, group in existing_by_dn.items():
            if dn not in seen_dns:
                group.deleted_at = datetime.utcnow()
                result.groups_removed += 1

        # Deactivate org membership for users removed from ALL LDAP groups
        await self._cleanup_org_memberships(
            db, organization_id, users_in_any_ldap_group
        )

        await db.commit()
        logger.info(
            f"LDAP sync completed for org {organization_id}: "
            f"created={result.groups_created} updated={result.groups_updated} "
            f"removed={result.groups_removed} memberships_added={result.memberships_added} "
            f"memberships_removed={result.memberships_removed}"
        )
        return result

    async def preview_sync(self, db: AsyncSession, organization_id: str) -> LDAPSyncPreview:
        """Dry-run: compute what a sync would change without writing."""
        ldap_groups = self.connection.search_groups()
        ldap_users = self.connection.search_users()

        dn_to_email: Dict[str, str] = {u["dn"]: u["email"] for u in ldap_users}
        email_to_user_id = await self._get_org_user_map(db, organization_id)

        existing_groups = await self._get_ldap_groups(db, organization_id)
        existing_by_dn: Dict[str, Group] = {g.external_id: g for g in existing_groups if g.external_id}

        preview = LDAPSyncPreview()
        seen_dns: Set[str] = set()

        for ldap_group in ldap_groups:
            group_dn = ldap_group["dn"]
            seen_dns.add(group_dn)

            target_user_ids = set()
            for member_ref in ldap_group["members"]:
                email = dn_to_email.get(member_ref) if self.config.group_member_format == "dn" else None
                if email and email in email_to_user_id:
                    target_user_ids.add(email_to_user_id[email])

            exists = group_dn in existing_by_dn
            to_add = 0
            to_remove = 0

            if exists:
                group = existing_by_dn[group_dn]
                current_ids = await self._get_current_member_ids(db, str(group.id))
                to_add = len(target_user_ids - current_ids)
                to_remove = len(current_ids - target_user_ids)
                if to_add or to_remove or group.name != ldap_group["name"]:
                    preview.groups_to_update += 1
            else:
                preview.groups_to_create += 1
                to_add = len(target_user_ids)

            preview.total_membership_changes += to_add + to_remove
            preview.groups.append(LDAPGroupPreview(
                dn=group_dn,
                name=ldap_group["name"],
                member_count=len(ldap_group["members"]),
                exists_in_app=exists,
                members_to_add=to_add,
                members_to_remove=to_remove,
            ))

        # Groups to remove
        for dn in existing_by_dn:
            if dn not in seen_dns:
                preview.groups_to_remove += 1

        return preview

    def _resolve_members(
        self,
        member_refs: List[str],
        dn_to_email: Dict[str, str],
        email_to_user_id: Dict[str, str],
        result: SyncResult,
    ) -> Set[str]:
        """Resolve LDAP member references to app user IDs."""
        user_ids: Set[str] = set()
        for ref in member_refs:
            if self.config.group_member_format == "dn":
                email = dn_to_email.get(ref)
            else:
                # memberUid format — ref is the uid, try as email
                email = ref

            if not email:
                result.users_not_found += 1
                continue

            user_id = email_to_user_id.get(email.lower())
            if user_id:
                user_ids.add(user_id)
            else:
                result.users_not_found += 1
        return user_ids

    async def _sync_memberships(
        self,
        db: AsyncSession,
        group: Group,
        target_user_ids: Set[str],
        result: SyncResult,
    ) -> None:
        """Add/remove GroupMembership rows to match target set."""
        current_ids = await self._get_current_member_ids(db, str(group.id))

        to_add = target_user_ids - current_ids
        to_remove = current_ids - target_user_ids

        for user_id in to_add:
            db.add(GroupMembership(group_id=group.id, user_id=user_id))
            result.memberships_added += 1

        if to_remove:
            stmt = select(GroupMembership).where(
                GroupMembership.group_id == group.id,
                GroupMembership.user_id.in_(to_remove),
            )
            rows = (await db.execute(stmt)).scalars().all()
            for row in rows:
                await db.delete(row)
                result.memberships_removed += 1

    async def _get_current_member_ids(self, db: AsyncSession, group_id: str) -> Set[str]:
        stmt = select(GroupMembership.user_id).where(GroupMembership.group_id == group_id)
        rows = (await db.execute(stmt)).scalars().all()
        return set(rows)

    async def _get_all_user_map(self, db: AsyncSession) -> Dict[str, str]:
        """Build email→user_id map for ALL app users (regardless of org)."""
        stmt = select(User.email, User.id)
        rows = (await db.execute(stmt)).all()
        return {email.lower(): uid for email, uid in rows}

    async def _ensure_org_memberships(
        self,
        db: AsyncSession,
        organization_id: str,
        user_ids: Set[str],
    ) -> None:
        """Create Membership rows for users who are in LDAP groups but not yet in the org."""
        if not user_ids:
            return

        # Find which users already have a membership
        stmt = (
            select(Membership.user_id)
            .where(Membership.organization_id == organization_id)
            .where(Membership.user_id.in_(user_ids))
            .where(Membership.deleted_at.is_(None))
        )
        existing_ids = set((await db.execute(stmt)).scalars().all())

        # New members to create (users in LDAP groups but not yet in the org).
        # Enforce the license seat cap: fill up to the remaining seats and skip the
        # rest (sorted for a deterministic selection), logging the overflow — never
        # abort the whole sync. Existing members are untouched (already excluded).
        to_create = sorted(user_ids - existing_ids)
        if to_create:
            from app.core.seats import seats_remaining
            remaining = await seats_remaining(db, organization_id)
            if remaining is not None and len(to_create) > remaining:
                logger.warning(
                    "LDAP sync: %d new users would exceed the license seat cap for "
                    "org %s; adding %d, skipping %d (raise seats to add the rest)",
                    len(to_create), organization_id, remaining, len(to_create) - remaining,
                )
                to_create = to_create[:remaining]

        from app.core.permission_resolver import ensure_system_role_assignment
        for user_id in to_create:
            db.add(Membership(
                user_id=user_id,
                organization_id=organization_id,
                role="member",
            ))
            # Give the user a real RBAC assignment (not just the legacy string).
            await ensure_system_role_assignment(db, organization_id, str(user_id), "member")
            logger.info(f"LDAP sync: auto-created org membership for user {user_id} in org {organization_id}")

    async def _cleanup_org_memberships(
        self,
        db: AsyncSession,
        organization_id: str,
        users_still_in_ldap: Set[str],
    ) -> None:
        """
        Soft-delete org Membership for users who were LDAP-provisioned
        but are no longer in any LDAP group.

        Only removes memberships for users who are STILL in at least one
        LDAP-synced GroupMembership — i.e., users who were added by LDAP
        but have since been removed from all LDAP groups.
        """
        # Find all users who are in LDAP-synced groups for this org
        stmt = (
            select(GroupMembership.user_id)
            .join(Group, Group.id == GroupMembership.group_id)
            .where(Group.organization_id == organization_id)
            .where(Group.external_provider == PROVIDER_NAME)
            .where(Group.deleted_at.is_(None))
        )
        users_in_app_ldap_groups = set((await db.execute(stmt)).scalars().all())

        # ★★★AN EMPTY DIRECTORY RESULT IS NOT "EVERYBODY LEFT".
        #
        # If the search base is edited, a group is renamed, the bind account
        # loses its rights, or the directory is simply unreachable in a way the
        # caller swallowed, this function is handed an empty set — and the plain
        # reading of an empty set is that every person has been removed from
        # every group. It would then deactivate the whole organization, which
        # is indistinguishable from a successful sync of an empty directory.
        #
        # A real directory that has genuinely lost all its members is
        # vanishingly rare; a misconfigured one is common. Refusing to act on
        # nothing costs a stale membership until someone fixes the
        # configuration. Acting on it costs everyone their access at once.
        if not users_still_in_ldap:
            logger.warning(
                "LDAP sync: the directory returned no users for org %s, so no "
                "memberships will be deactivated. An empty result is treated as "
                "a configuration problem, not as everybody having left. Check "
                "the search base, the group filter and the bind account.",
                organization_id,
            )
            return

        # Users who are in app LDAP groups but NOT in any LDAP group anymore
        # (they were just removed from all groups by _sync_memberships above,
        # but we need to check what's left after the sync)
        # Actually, after _sync_memberships ran, the GroupMembership rows are
        # already updated. So we find users with Membership who are NOT in
        # users_still_in_ldap and whose Membership was likely LDAP-created.
        # To be safe, only remove memberships where the user has NO remaining
        # GroupMemberships in any LDAP group for this org.

        # Re-query after sync to see who still has LDAP group memberships
        stmt = (
            select(GroupMembership.user_id.distinct())
            .join(Group, Group.id == GroupMembership.group_id)
            .where(Group.organization_id == organization_id)
            .where(Group.external_provider == PROVIDER_NAME)
            .where(Group.deleted_at.is_(None))
        )
        users_with_ldap_groups = set((await db.execute(stmt)).scalars().all())

        # Find memberships that should be removed:
        # users NOT in any LDAP group who also don't have a manually-created membership
        # We only remove memberships for users who WERE in LDAP groups before
        # (i.e., they exist in app LDAP groups table but no longer have any)
        # For safety, we check: user has org membership AND zero LDAP group memberships
        # ★★★A DIRECTORY MAY ONLY REMOVE WHAT IT PROVISIONED.
        #
        # The line below used to be a COMMENT saying "only delete if user was
        # originally LDAP-provisioned" with nothing implementing it. The query
        # selected every live membership in the organization whose user was not
        # in an LDAP group — which is every SSO user, every local user, and
        # every invited member, none of whom the directory has any say over.
        #
        # Measured on a live production database after this had been running
        # hourly: 29 memberships, ONE still live, and that one only because it
        # holds full_admin_access. Of the 28 removed, 16 belonged to users with
        # NO LDAP link whatsoever — they signed in through SSO. The entire
        # organization had been emptied by a directory sync, and it presented
        # as people mysteriously losing access.
        #
        # `User.ldap_dn` is the recorded origin — the same field the sign-in
        # doors route on. A row without it was not created by the directory
        # and is not the directory's to remove.
        stmt = (
            select(Membership)
            .join(User, User.id == Membership.user_id)
            .where(Membership.organization_id == organization_id)
            .where(Membership.deleted_at.is_(None))
            .where(User.ldap_dn.isnot(None))
            .where(Membership.user_id.notin_(users_with_ldap_groups))
            .where(Membership.user_id.notin_(users_still_in_ldap))
        )
        orphan_memberships = (await db.execute(stmt)).scalars().all()

        # Never remove users who hold full_admin_access (RBAC-aware admin check)
        from app.core.permission_resolver import resolve_permissions, FULL_ADMIN
        _admin_safe: list = []
        for m in orphan_memberships:
            if not m.user_id:
                _admin_safe.append(m)
                continue
            resolved = await resolve_permissions(db, str(m.user_id), str(organization_id))
            if FULL_ADMIN not in resolved.org_permissions:
                _admin_safe.append(m)
        orphan_memberships = _admin_safe

        for membership in orphan_memberships:
            # Extra safety: only remove if user has no non-LDAP groups in this org
            non_ldap_stmt = (
                select(GroupMembership.id)
                .join(Group, Group.id == GroupMembership.group_id)
                .where(Group.organization_id == organization_id)
                .where(Group.external_provider != PROVIDER_NAME)
                .where(GroupMembership.user_id == membership.user_id)
            )
            # ★`.first()`, not `scalar_one_or_none()`. This asks "is this person
            # in any non-directory group?" — an existence question — and a
            # person in TWO manual groups returns two rows, which
            # scalar_one_or_none raises on. The raise would abort the sweep
            # mid-way, having already deactivated some memberships and not
            # others. Same defect class as the membership check.
            has_manual_groups = (await db.execute(non_ldap_stmt)).scalars().first()
            if not has_manual_groups:
                membership.deleted_at = datetime.utcnow()
                logger.info(
                    f"LDAP sync: deactivated org membership for user {membership.user_id} "
                    f"in org {organization_id} (removed from all LDAP groups)"
                )

    async def _get_ldap_groups(self, db: AsyncSession, organization_id: str) -> List[Group]:
        stmt = (
            select(Group)
            .where(Group.organization_id == organization_id)
            .where(Group.external_provider == PROVIDER_NAME)
            .where(Group.deleted_at.is_(None))
        )
        return list((await db.execute(stmt)).scalars().all())

    async def _get_groups_by_name(
        self, db: AsyncSession, organization_id: str
    ) -> Dict[str, Group]:
        """Every group in the org by name — any provider, tombstones included.

        ★★★Deliberately WIDER than `_get_ldap_groups`, and the width is the
        whole point. `uq_groups_org_name` is on (organization_id, name) with no
        provider column and no `deleted_at` predicate, so a name is taken by a
        manually-created group, an OIDC-synced one, and a soft-deleted one
        alike. A lookup narrowed to live LDAP rows — which is what this used —
        cannot see any of those, so it reports the name free and the INSERT
        fails.

        ★A live row wins over a tombstone when both somehow hold the name, so
        an adoption never re-points at a deleted row while a real one exists.
        """
        stmt = select(Group).where(Group.organization_id == organization_id)
        out: Dict[str, Group] = {}
        for g in (await db.execute(stmt)).scalars().all():
            prev = out.get(g.name)
            if prev is None or (prev.deleted_at is not None and g.deleted_at is None):
                out[g.name] = g
        return out
