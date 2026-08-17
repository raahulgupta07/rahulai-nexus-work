# SCIM Services
# Licensed under the Business Source License 1.1
# See ENTERPRISE_LICENSE for details

import re
import secrets
import hashlib
import logging
from typing import Optional, List
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from fastapi import HTTPException
from fastapi_users.password import PasswordHelper

from app.models.user import User
from app.models.membership import Membership
from app.models.organization import Organization
from app.ee.scim.models import ScimToken
from app.ee.scim.schemas import (
    ScimUser, ScimUserCreate, ScimPatchOp, ScimMeta, ScimName, ScimEmail,
    ScimListResponse, ScimTokenCreate, ScimTokenResponse, ScimTokenCreated,
)

logger = logging.getLogger(__name__)
password_helper = PasswordHelper()


# --- SCIM Token Service ---

class ScimTokenService:

    @staticmethod
    def _generate_token() -> tuple[str, str, str]:
        """Generate a SCIM token. Returns (full_token, token_hash, token_prefix)."""
        random_bytes = secrets.token_urlsafe(32)
        full_token = f"bow_scim_{random_bytes}"
        token_hash = hashlib.sha256(full_token.encode()).hexdigest()
        token_prefix = full_token[:16]
        return full_token, token_hash, token_prefix

    async def create_token(
        self,
        db: AsyncSession,
        organization: Organization,
        user_id: str,
        data: ScimTokenCreate,
    ) -> ScimTokenCreated:
        full_token, token_hash, token_prefix = self._generate_token()

        scim_token = ScimToken(
            organization_id=organization.id,
            name=data.name,
            token_hash=token_hash,
            token_prefix=token_prefix,
            created_by_user_id=user_id,
            expires_at=data.expires_at,
        )
        db.add(scim_token)
        await db.commit()
        await db.refresh(scim_token)

        return ScimTokenCreated(
            id=scim_token.id,
            name=scim_token.name,
            token_prefix=scim_token.token_prefix,
            created_at=scim_token.created_at,
            expires_at=scim_token.expires_at,
            last_used_at=scim_token.last_used_at,
            token=full_token,
        )

    async def list_tokens(
        self,
        db: AsyncSession,
        organization_id: str,
    ) -> List[ScimTokenResponse]:
        result = await db.execute(
            select(ScimToken)
            .where(ScimToken.organization_id == organization_id)
            .where(ScimToken.deleted_at.is_(None))
            .order_by(ScimToken.created_at.desc())
        )
        tokens = result.scalars().all()
        return [ScimTokenResponse.model_validate(t) for t in tokens]

    async def revoke_token(
        self,
        db: AsyncSession,
        organization_id: str,
        token_id: str,
    ) -> bool:
        result = await db.execute(
            select(ScimToken)
            .where(ScimToken.id == token_id)
            .where(ScimToken.organization_id == organization_id)
            .where(ScimToken.deleted_at.is_(None))
        )
        token = result.scalar_one_or_none()
        if not token:
            raise HTTPException(status_code=404, detail="SCIM token not found")

        token.deleted_at = datetime.utcnow()
        await db.commit()
        return True


# --- SCIM User Service ---

def _parse_scim_filter(filter_str: Optional[str]) -> dict:
    """
    Parse minimal SCIM filter expressions.
    Supports: 'userName eq "value"' and 'emails.value eq "value"'
    """
    if not filter_str:
        return {}

    match = re.match(r'(\S+)\s+eq\s+"([^"]*)"', filter_str.strip())
    if not match:
        return {}

    attr, value = match.group(1), match.group(2)
    return {attr: value}


def _user_to_scim(user: User, membership: Optional[Membership] = None, base_url: str = "") -> ScimUser:
    """Convert internal User to SCIM User representation."""
    name_parts = (user.name or "").split(" ", 1)
    given_name = name_parts[0] if name_parts else ""
    family_name = name_parts[1] if len(name_parts) > 1 else ""

    created_at = membership.created_at if membership else None
    updated_at = membership.updated_at if membership else None

    return ScimUser(
        id=user.id,
        externalId=user.scim_external_id,
        userName=user.email,
        name=ScimName(
            formatted=user.name,
            givenName=given_name,
            familyName=family_name,
        ),
        displayName=user.name,
        emails=[ScimEmail(value=user.email, type="work", primary=True)],
        active=user.is_active,
        meta=ScimMeta(
            resourceType="User",
            created=created_at,
            lastModified=updated_at,
            location=f"{base_url}/scim/v2/Users/{user.id}",
        ),
    )


class ScimUserService:

    async def list_users(
        self,
        db: AsyncSession,
        organization_id: str,
        filter_str: Optional[str] = None,
        start_index: int = 1,
        count: int = 100,
        base_url: str = "",
    ) -> ScimListResponse:
        filters = _parse_scim_filter(filter_str)

        query = (
            select(User, Membership)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.organization_id == organization_id)
            .where(Membership.deleted_at.is_(None))
        )

        # Apply SCIM filters
        email_filter = filters.get("userName") or filters.get("emails.value")
        if email_filter:
            query = query.where(User.email == email_filter)

        external_id_filter = filters.get("externalId")
        if external_id_filter:
            query = query.where(User.scim_external_id == external_id_filter)

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar()

        # Apply pagination (SCIM uses 1-based indexing)
        offset = max(0, start_index - 1)
        query = query.offset(offset).limit(count)

        result = await db.execute(query)
        rows = result.all()

        resources = [_user_to_scim(user, membership, base_url) for user, membership in rows]

        return ScimListResponse(
            totalResults=total,
            startIndex=start_index,
            itemsPerPage=count,
            Resources=resources,
        )

    async def get_user(
        self,
        db: AsyncSession,
        organization_id: str,
        user_id: str,
        base_url: str = "",
    ) -> ScimUser:
        result = await db.execute(
            select(User, Membership)
            .join(Membership, Membership.user_id == User.id)
            .where(User.id == user_id)
            .where(Membership.organization_id == organization_id)
            .where(Membership.deleted_at.is_(None))
        )
        row = result.one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        user, membership = row
        return _user_to_scim(user, membership, base_url)

    async def create_user(
        self,
        db: AsyncSession,
        organization_id: str,
        data: ScimUserCreate,
        base_url: str = "",
    ) -> ScimUser:
        email = data.userName
        if data.emails:
            email = data.emails[0].value

        # Check if user already exists
        existing_result = await db.execute(
            select(User).where(User.email == email)
        )
        existing_user = existing_result.scalar_one_or_none()

        if existing_user:
            # Check if already a member of this org
            membership_result = await db.execute(
                select(Membership)
                .where(Membership.user_id == existing_user.id)
                .where(Membership.organization_id == organization_id)
                .where(Membership.deleted_at.is_(None))
            )
            if membership_result.scalar_one_or_none():
                raise HTTPException(status_code=409, detail="User already exists in this organization")

            # Enforce the license seat cap before adding a new member. Checked after
            # the duplicate guard so re-provisioning an existing member still 409s
            # rather than being masked by a seat-limit 402.
            from app.core.seats import enforce_seat_limit
            await enforce_seat_limit(db, organization_id)

            # Add membership to existing user
            membership = Membership(
                user_id=existing_user.id,
                organization_id=organization_id,
                email=existing_user.email,
                role="member",
            )
            db.add(membership)
            # Give the user a real RBAC assignment (not just the legacy string).
            from app.core.permission_resolver import ensure_system_role_assignment
            await ensure_system_role_assignment(db, organization_id, str(existing_user.id), "member")

            # Update external ID if provided
            if data.externalId:
                existing_user.scim_external_id = data.externalId

            await db.commit()
            await db.refresh(membership)
            return _user_to_scim(existing_user, membership, base_url)

        # Enforce the license seat cap before creating a brand-new user + membership,
        # so a full org can't be grown via SCIM provisioning (and we don't leave an
        # orphan User with no membership).
        from app.core.seats import enforce_seat_limit
        await enforce_seat_limit(db, organization_id)

        # Create new user
        display_name = data.displayName
        if not display_name and data.name:
            if data.name.formatted:
                display_name = data.name.formatted
            elif data.name.givenName or data.name.familyName:
                display_name = f"{data.name.givenName or ''} {data.name.familyName or ''}".strip()
        if not display_name:
            display_name = email.split("@")[0]

        user = User(
            email=email,
            name=display_name,
            hashed_password=password_helper.hash(password_helper.generate()),
            is_active=data.active,
            is_verified=True,
            is_superuser=False,
            scim_external_id=data.externalId,
        )
        db.add(user)
        await db.flush()  # Get user.id before creating membership

        membership = Membership(
            user_id=user.id,
            organization_id=organization_id,
            email=user.email,
            role="member",
        )
        db.add(membership)
        # Give the user a real RBAC assignment (not just the legacy string).
        from app.core.permission_resolver import ensure_system_role_assignment
        await ensure_system_role_assignment(db, organization_id, str(user.id), "member")
        await db.commit()
        await db.refresh(user)
        await db.refresh(membership)

        return _user_to_scim(user, membership, base_url)

    async def update_user(
        self,
        db: AsyncSession,
        organization_id: str,
        user_id: str,
        data: ScimUserCreate,
        base_url: str = "",
    ) -> ScimUser:
        """PUT - full replace of user attributes."""
        result = await db.execute(
            select(User, Membership)
            .join(Membership, Membership.user_id == User.id)
            .where(User.id == user_id)
            .where(Membership.organization_id == organization_id)
            .where(Membership.deleted_at.is_(None))
        )
        row = result.one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        user, membership = row
        # Captured BEFORE the mutation below — see _fire_successor_if_deactivated.
        was_active = bool(user.is_active)

        # Update name
        if data.displayName:
            user.name = data.displayName
        elif data.name:
            if data.name.formatted:
                user.name = data.name.formatted
            elif data.name.givenName or data.name.familyName:
                user.name = f"{data.name.givenName or ''} {data.name.familyName or ''}".strip()

        # Update email
        if data.emails:
            user.email = data.emails[0].value
        elif data.userName:
            user.email = data.userName

        # Update active status
        user.is_active = data.active

        # Update external ID
        if data.externalId is not None:
            user.scim_external_id = data.externalId

        await db.commit()
        await db.refresh(user)
        await self._fire_successor_if_deactivated(
            db, organization_id, user, was_active
        )
        return _user_to_scim(user, membership, base_url)

    async def patch_user(
        self,
        db: AsyncSession,
        organization_id: str,
        user_id: str,
        patch: ScimPatchOp,
        base_url: str = "",
    ) -> ScimUser:
        """PATCH - partial update of user attributes."""
        result = await db.execute(
            select(User, Membership)
            .join(Membership, Membership.user_id == User.id)
            .where(User.id == user_id)
            .where(Membership.organization_id == organization_id)
            .where(Membership.deleted_at.is_(None))
        )
        row = result.one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        user, membership = row
        was_active = bool(user.is_active)

        for op in patch.Operations:
            if op.op.lower() == "replace":
                self._apply_replace(user, op.path, op.value)
            elif op.op.lower() == "add":
                self._apply_replace(user, op.path, op.value)  # add behaves like replace for single-valued attrs
            elif op.op.lower() == "remove":
                if op.path and op.path.lower() == "externalid":
                    user.scim_external_id = None

        await db.commit()
        await db.refresh(user)
        await self._fire_successor_if_deactivated(
            db, organization_id, user, was_active
        )
        return _user_to_scim(user, membership, base_url)

    async def _fire_successor_if_deactivated(
        self,
        db: AsyncSession,
        organization_id: str,
        user: User,
        was_active: bool,
    ) -> None:
        """The directory switched somebody off — hand their work over.

        ★★★**On the TRANSITION only.** Okta and Entra both re-send the full
        object on every reconcile, so a plain "is inactive now" test would fire
        on every sync for every departed person forever. The second run moves
        nothing (they own nothing by then), but it writes a fresh
        ``ownership_transfers`` batch each time — an audit trail that claims a
        handover happened today, and an Undo offer that reverses nothing.

        ★★★**Nothing in here may fail the request.** A directory deprovisioning
        is the security-critical act; the handover is a courtesy on top of it.
        If this raised, an Okta deactivation would return 500 and the IdP would
        retry it forever while the account stayed **enabled** — the exact
        opposite of what was asked for. Uncleared content is not lost, it is
        listed by the Needs-an-owner view.
        """
        if not was_active or user.is_active:
            return

        try:
            from app.services import ownership_service

            organization = (
                await db.execute(
                    select(Organization).where(Organization.id == str(organization_id))
                )
            ).scalar_one_or_none()
            if organization is None:
                return

            # actor is None: no human asked for this. `successor` is the one
            # reason in the ledger that legitimately has no actor.
            result = await ownership_service.on_member_deactivated(
                db, organization, str(user.id), actor_user_id=None
            )
            if result is not None:
                await db.commit()
                await self._tell_the_subscribers(db, organization, result)
        except Exception:
            logger.exception(
                "successor handover failed for user %s; content stays orphaned "
                "and is listed by the Needs-an-owner view",
                user.id,
            )
            await db.rollback()

    async def _tell_the_subscribers(self, db, organization, result) -> None:
        """People receiving a scheduled dashboard learn it changed hands.

        ★★★**Its own try/except, outside the transfer's.** This runs on the
        automatic path — a directory telling us somebody has left the company —
        where switching the account off is the security-critical act and every
        courtesy on top of it must be unable to fail it. The caller above
        already swallows, but it also rolls back on failure, and a notice that
        could roll back a handover that has already committed would be strictly
        worse than no notice at all.

        ★The recipient is read from the LEDGER, not from the membership row.
        The ledger is what actually happened; `successor_user_id` is what was
        intended, and re-reading the intention would let a notice name somebody
        the transfer never moved anything to.
        """
        try:
            from app.models.ownership_transfer import OwnershipTransfer
            from app.services.schedule_owner_notice import (
                notify_schedule_subscribers_of_owner_change,
            )

            row = (
                await db.execute(
                    select(OwnershipTransfer).where(
                        OwnershipTransfer.batch_id == result.batch_id
                    )
                )
            ).scalars().first()
            if row is None:
                return

            await notify_schedule_subscribers_of_owner_change(
                db,
                organization,
                batch_id=result.batch_id,
                to_user_id=str(row.to_user_id),
                # No human asked for this one. The notice must not name an
                # actor it would have to invent.
                actor_user_id=None,
            )
            await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception(
                "owner-change notice failed after an automatic successor "
                "handover; the handover itself stands and is unaffected"
            )

    def _apply_replace(self, user: User, path: Optional[str], value) -> None:
        """Apply a replace operation to user attributes."""
        if path is None and isinstance(value, dict):
            # Okta-style: {"op": "replace", "value": {"active": false}}
            for key, val in value.items():
                self._set_user_attr(user, key, val)
        elif path:
            self._set_user_attr(user, path, value)

    def _set_user_attr(self, user: User, attr: str, value) -> None:
        """Set a single user attribute from SCIM path/key."""
        attr_lower = attr.lower()
        if attr_lower == "active":
            user.is_active = bool(value)
        elif attr_lower == "username":
            user.email = str(value)
        elif attr_lower == "displayname":
            user.name = str(value)
        elif attr_lower == "externalid":
            user.scim_external_id = str(value) if value is not None else None
        elif attr_lower == "name":
            if isinstance(value, dict):
                formatted = value.get("formatted")
                given = value.get("givenName", "")
                family = value.get("familyName", "")
                user.name = formatted or f"{given} {family}".strip()
        elif attr_lower == "emails":
            if isinstance(value, list) and value:
                email_val = value[0].get("value") if isinstance(value[0], dict) else str(value[0])
                if email_val:
                    user.email = email_val

    async def delete_user(
        self,
        db: AsyncSession,
        organization_id: str,
        user_id: str,
    ) -> None:
        """SCIM DELETE - deactivate user (soft delete)."""
        result = await db.execute(
            select(User, Membership)
            .join(Membership, Membership.user_id == User.id)
            .where(User.id == user_id)
            .where(Membership.organization_id == organization_id)
            .where(Membership.deleted_at.is_(None))
        )
        row = result.one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        user, membership = row
        was_active = bool(user.is_active)
        user.is_active = False
        await db.commit()
        await self._fire_successor_if_deactivated(
            db, organization_id, user, was_active
        )
