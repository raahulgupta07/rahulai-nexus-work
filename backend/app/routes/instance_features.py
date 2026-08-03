"""Super-admin control over instance-wide feature switches.

★Gated on ``is_superuser``, not ``manage_settings`` — and that is the point
------------------------------------------------------------------------------
Every other Settings screen gates on ``manage_settings``, which an organization
admin holds. These switches change the product for EVERY organization on the
deployment, so an org admin holding one would be an org admin administering
other people's organizations. ``is_superuser`` is the codebase's existing
instance-wide flag (described as such at routes/user_password.py:92) and is
granted to exactly one account: the bootstrap user (core/auth.py:1094).

The read half of this is already public — ``GET /api/settings`` serves the
resolved values because the login page needs them before anyone is signed in.
This module is the write half plus the richer read (value, source, default) that
the admin UI needs and the public feed has no business carrying.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import current_user
from app.dependencies import get_async_db
from app.models.user import User
from app.services import instance_features

router = APIRouter(tags=["instance"])


def _require_super_admin(user: User) -> None:
    """403 with a sentence, not a bare status.

    An org admin hitting this is not doing anything wrong — they hold every
    permission their own screens ask for. Telling them which power is missing,
    and that it is instance-wide, is the difference between a wall and an answer.
    """
    if not bool(getattr(user, "is_superuser", False)):
        raise HTTPException(
            status_code=403,
            detail=(
                "These switches apply to every organization on this deployment, "
                "so only a super admin can change them. Organization settings are "
                "unaffected."
            ),
        )


class FeatureUpdate(BaseModel):
    """``value=None`` clears the override back to the environment default.

    ★Not the same as ``false``. Storing false would pin the switch off and make
    the deployment's own default unreachable — the tri-state exists precisely so
    "turned off" and "never chosen" stay distinguishable.
    """
    value: Optional[bool] = None


@router.get("/instance/features")
async def get_instance_features(
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
):
    """Every toggleable switch: ``{name: {value, source, default}}``.

    ``source`` is ``"db"`` when a super admin chose it and ``"default"`` when it
    is inherited from the environment, so the UI can say which without guessing.
    """
    _require_super_admin(user)
    return await instance_features.read_all(db)


@router.put("/instance/features/{name}")
async def set_instance_feature(
    name: str,
    payload: FeatureUpdate,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
):
    """Turn one switch on, off, or back to the default.

    Unknown names are refused rather than stored: an accepted typo would sit in
    the database looking exactly like a real switch while nothing read it.
    """
    _require_super_admin(user)
    try:
        return await instance_features.set_feature(db, name, payload.value)
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail=(
                f"'{name}' is not a switch this deployment exposes. Known: "
                + ", ".join(sorted(instance_features.TOGGLEABLE))
            ),
        )
