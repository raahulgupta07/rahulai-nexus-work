import uuid
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from fastapi_users import schemas
from datetime import datetime
from app.schemas.external_user_mapping_schema import ExternalUserMappingMinimalSchema


# class UserBase(BaseModel):
    # email: EmailStr
    # username: str = Field(..., min_length=3, max_length=50)

class UserCreate(schemas.BaseUserCreate):
    # pass
    name: str = Field(..., min_length=3, max_length=50)
    # Optional invite token from the sign-up link. Used only to validate the
    # invite at registration; never persisted on the User row.
    invite_token: Optional[str] = None
    #password: str = Field(..., min_length=6)

    def create_update_dict(self):
        d = super().create_update_dict()
        d.pop("invite_token", None)
        return d

class UserUpdate(schemas.BaseUserUpdate):
    # Allow users to edit their display name from the profile modal. fastapi-users
    # applies this through the standard PATCH /users/me self-update flow.
    name: Optional[str] = Field(None, min_length=1, max_length=50)

    # ★★★`password` is inherited from BaseUserUpdate and is deliberately DROPPED
    # here rather than declared. The commented-out line that used to sit at this
    # spot read as "password updates are off" — they were not. The base class
    # declares the field and `create_update_dict` passes it straight through, so
    # `PATCH /api/users/me {"password": "..."}` silently reset the caller's
    # password **without asking for the current one**. Anyone holding a token —
    # or sitting at an unlocked session — could take the account over permanently.
    #
    # Password changes now go through POST /api/users/me/change-password, which
    # verifies the current password first and refuses accounts whose password
    # lives in a directory. Both dict builders are overridden because
    # fastapi-users picks between them by caller privilege, and the superuser
    # variant is the one an admin's own PATCH would use.
    def create_update_dict(self):
        d = super().create_update_dict()
        d.pop("password", None)
        return d

    def create_update_dict_superuser(self):
        d = super().create_update_dict_superuser()
        d.pop("password", None)
        return d

class UserRead(schemas.BaseUser[uuid.UUID]):
    name: str
    image_url: Optional[str] = None
    # Set when a super admin set this account's password. The app shell reads it
    # from /users/whoami and routes to the change-password screen; the backend
    # refuses every other path until it clears, so the flag is a hint to the UI
    # rather than the enforcement.
    must_change_password: bool = False

    # class Config:
      #  orm_mode = True  # Allows the output model to be compatible with ORM objects

class UserSchema(BaseModel):
    id: str
    name: str
    email: str
    image_url: Optional[str] = None
    external_user_mappings: List[ExternalUserMappingMinimalSchema] = []
    last_login: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    # "local" | "sso" | "ldap" | "scim" — where this account's password lives.
    # Populated by the caller (see OrganizationService.get_members), NOT derived
    # by pydantic: it needs `oauth_accounts`, and reading that attribute during
    # serialization would lazy-load inside an async request.
    auth_origin: Optional[str] = None
    must_change_password: bool = False
    is_superuser: bool = False

    class Config:
        from_attributes = True