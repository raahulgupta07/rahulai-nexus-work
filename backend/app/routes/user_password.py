"""Password routes for accounts this application owns.

Two operations, deliberately separate because they answer to different proofs:

* ``POST /users/{user_id}/set-password`` — a **super admin** sets someone else's
  password. Proof of authority is the super-admin flag; no old password is
  involved because the admin does not know it.
* ``POST /users/me/change-password`` — the account holder changes their own.
  Proof is the **current password**, not the session, so a stolen or unattended
  session cannot take the account over permanently.

★★★A super admin cannot use the first route on themselves — the route refuses
``user_id == current_user.id`` outright. Allowing it would hand any unlocked
admin session a permanent takeover with no old password required, which is the
precise hole the second route exists to close. An admin changes their own
password like everyone else.

Both routes refuse any account whose password lives elsewhere (SSO, LDAP, SCIM);
see ``app/core/auth_origin.py`` for why "has a hashed_password" cannot decide that.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import current_user, forbid_service_account_principal
from app.core.auth_origin import (
    ORIGIN_LOCAL,
    origin_owner_label,
    password_is_managed_here,
    resolve_auth_origin,
)
from app.dependencies import get_async_db
from app.ee.audit.service import audit_service
from app.models.membership import Membership
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(tags=["users"])

# Matches the client-side rule. Kept as a constant so the message and the check
# cannot drift apart.
MIN_PASSWORD_LENGTH = 8


class SetPasswordRequest(BaseModel):
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=256)
    # Default ON: a password the admin knows should survive exactly one sign-in.
    require_change: bool = True


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=256)


class PasswordOpResult(BaseModel):
    ok: bool = True
    user_id: str
    must_change_password: bool


def _password_helper():
    from fastapi_users.password import PasswordHelper

    return PasswordHelper()


async def _load_user_with_identities(db: AsyncSession, user_id: str) -> Optional[User]:
    """Load a user with `oauth_accounts` populated.

    The eager load is not an optimisation — ``resolve_auth_origin`` reads that
    collection, and touching it unloaded inside an async request raises.
    """
    result = await db.execute(
        select(User)
        .options(selectinload(User.oauth_accounts))
        .where(User.id == str(user_id))
    )
    return result.scalars().first()


async def _shares_organization(db: AsyncSession, user_a_id: str, user_b_id: str) -> bool:
    """True when both users hold a membership in at least one common org.

    ★Without this a super admin of one workspace could set the password of a
    user they have no relationship with, since `is_superuser` is an instance-wide
    flag rather than a per-org role.
    """
    a = await db.execute(
        select(Membership.organization_id).where(Membership.user_id == str(user_a_id))
    )
    b = await db.execute(
        select(Membership.organization_id).where(Membership.user_id == str(user_b_id))
    )
    return bool(set(a.scalars().all()) & set(b.scalars().all()))


@router.post(
    "/users/{user_id}/set-password",
    response_model=PasswordOpResult,
    dependencies=[Depends(forbid_service_account_principal)],
)
async def set_user_password(
    user_id: str,
    data: SetPasswordRequest,
    request: Request,
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Super admin sets another local account's password."""
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=403,
            detail="Only a super admin can set another person's password.",
        )

    if str(user_id) == str(current_user.id):
        # See the module docstring — this is a security boundary, not a UX choice.
        raise HTTPException(
            status_code=400,
            detail=(
                "You cannot set your own password here. "
                "Change it from your profile, where the current password is required."
            ),
        )

    target = await _load_user_with_identities(db, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found.")

    if target.is_service_account:
        raise HTTPException(
            status_code=400,
            detail="A service account has no interactive password.",
        )

    if target.is_superuser:
        # ★Not just the caller's own row — ANY super admin. Without this, one
        # super admin could set another's password with require_change on, and
        # the target would then be permanently stuck: the gate refuses every
        # path but change-password, and change-password refuses super admins.
        # A locked-out super admin has nobody above them to undo it.
        raise HTTPException(status_code=400, detail=SUPER_ADMIN_LOCK_REASON)

    if not await _shares_organization(db, current_user.id, target.id):
        # 404 rather than 403: a super admin of another workspace should not be
        # able to probe which addresses exist here.
        raise HTTPException(status_code=404, detail="User not found.")

    origin = resolve_auth_origin(target, oauth_accounts=list(target.oauth_accounts or []))
    if not password_is_managed_here(origin):
        raise HTTPException(
            status_code=400,
            detail=(
                f"This account signs in through {origin_owner_label(origin)}. "
                "Its password is not stored here, so there is nothing to set."
            ),
        )

    target.hashed_password = _password_helper().hash(data.password)
    target.must_change_password = bool(data.require_change)
    db.add(target)
    await db.commit()

    # ★The password itself never reaches the audit log — only that it happened,
    # to whom, and whether a change was forced.
    try:
        org_id = (
            await db.execute(
                select(Membership.organization_id).where(
                    Membership.user_id == str(target.id)
                )
            )
        ).scalars().first()
        if org_id:
            await audit_service.log(
                db=db,
                organization_id=org_id,
                action="user.password_set",
                user_id=current_user.id,
                resource_type="user",
                resource_id=str(target.id),
                details={
                    "target_email": target.email,
                    "require_change": bool(data.require_change),
                },
                request=request,
            )
    except Exception as exc:  # noqa: BLE001
        # An audit failure must not undo a completed password change — the user
        # would be told it failed while their old password no longer works.
        logger.warning("Could not audit password set for %s: %s", target.id, exc)

    return PasswordOpResult(
        user_id=str(target.id),
        must_change_password=bool(target.must_change_password),
    )


class MyPasswordStatus(BaseModel):
    auth_origin: str
    # False for SSO/LDAP/SCIM (the password lives elsewhere) and for the super
    # admin (see SUPER_ADMIN_LOCK_REASON). The profile panel renders an
    # explanation instead of a form the change route would refuse anyway.
    can_change: bool
    must_change_password: bool
    owner_label: Optional[str] = None
    min_length: int = MIN_PASSWORD_LENGTH
    # "managed_elsewhere" | "super_admin" | None — lets the UI pick its wording
    # without parsing a sentence.
    blocked_reason: Optional[str] = None
    is_superuser: bool = False


# ★★★The super admin's own password cannot be changed from inside the product.
# There is no account above them to put it right, and a deployment with no mail
# server has no reset path either — so a password changed by mistake, or by
# somebody sitting at an unlocked session, locks the instance's only privileged
# account out permanently. The admin route already refused self-service
# (`user_id == current_user.id`); this closes the self-change route as well, so
# the two together mean the super admin password is fixed for the life of the
# install and can only be altered by someone with database access.
SUPER_ADMIN_LOCK_REASON = (
    "A super admin's password cannot be changed from inside the app. "
    "No other account can restore your access if it goes wrong, so this has to "
    "be done by someone with direct database access."
)


@router.get("/users/me/password-status", response_model=MyPasswordStatus)
async def my_password_status(
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """What the profile modal needs to render its Password section.

    ★A separate endpoint rather than a field on ``/users/whoami``: that route
    builds its response by splatting ``vars(current_user)`` into the schema, so a
    field the ORM row does not carry has to be threaded through by hand, and
    ``auth_origin`` is derived (it needs ``oauth_accounts``), not stored.
    """
    me = await _load_user_with_identities(db, current_user.id)
    if me is None:
        raise HTTPException(status_code=404, detail="User not found.")
    origin = resolve_auth_origin(me, oauth_accounts=list(me.oauth_accounts or []))
    managed = password_is_managed_here(origin)
    is_super = bool(getattr(me, "is_superuser", False))

    # Origin is reported first: for a super admin who signs in through a
    # directory, "your password lives in the directory" is the more useful
    # answer, and the lock below is then beside the point.
    blocked = None if managed else "managed_elsewhere"
    if managed and is_super:
        blocked = "super_admin"

    return MyPasswordStatus(
        auth_origin=origin,
        can_change=managed and not is_super,
        must_change_password=bool(getattr(me, "must_change_password", False)),
        owner_label=None if managed else origin_owner_label(origin),
        blocked_reason=blocked,
        is_superuser=is_super,
    )


@router.post("/users/me/change-password", response_model=PasswordOpResult)
async def change_my_password(
    data: ChangePasswordRequest,
    current_user: User = Depends(current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """The account holder changes their own password, proving the current one."""
    me = await _load_user_with_identities(db, current_user.id)
    if me is None:
        raise HTTPException(status_code=404, detail="User not found.")

    if getattr(me, "is_superuser", False):
        # See SUPER_ADMIN_LOCK_REASON. Refused here, not merely hidden in the
        # profile modal — the panel is a courtesy, this is the rule.
        raise HTTPException(status_code=403, detail=SUPER_ADMIN_LOCK_REASON)

    origin = resolve_auth_origin(me, oauth_accounts=list(me.oauth_accounts or []))
    if not password_is_managed_here(origin):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Your password is managed by {origin_owner_label(origin)}. "
                "Change it there."
            ),
        )

    helper = _password_helper()
    verified, updated_hash = helper.verify_and_update(
        data.current_password, me.hashed_password or ""
    )
    if not verified:
        raise HTTPException(status_code=400, detail="That is not your current password.")

    if data.current_password == data.new_password:
        raise HTTPException(
            status_code=400,
            detail="Your new password must be different from the current one.",
        )

    me.hashed_password = helper.hash(data.new_password)
    # Whatever forced this change is now satisfied.
    me.must_change_password = False
    db.add(me)
    await db.commit()

    return PasswordOpResult(user_id=str(me.id), must_change_password=False)
