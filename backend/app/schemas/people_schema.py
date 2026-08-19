from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class IdentityView(BaseModel):
    """One authentication identity that resolves to a person.

    ``kind`` is "local" for a password this product owns, "directory" for the
    LDAP/SCIM record that provisioned the account, or "oauth" for a linked
    SSO/OAuth account. Accounts are unified by email, so a single person may
    carry several at once — a directory entry and a linked identity provider is
    the ordinary case for staff.

    ★"local" appears only when nothing else provisioned the account. A
    provisioned row carries a random hash nobody holds, so listing it as a way
    in is fiction — see ``app/core/auth_origin.py``.
    """

    kind: str  # "local" | "directory" | "oauth"
    provider: str  # "local"/"ldap"/"scim", or OAuthAccount.oauth_name
    account_email: Optional[str] = None
    account_id: Optional[str] = None
    is_primary: bool = False


class PersonGroupView(BaseModel):
    name: str
    source: Optional[str] = None  # Group.external_provider, or "manual" when null


class PersonView(BaseModel):
    user_id: str
    email: str
    name: Optional[str] = None
    role: str
    is_owner: bool = False
    created_at: Optional[datetime] = None
    has_password: bool = False
    identities: List[IdentityView] = []
    groups: List[PersonGroupView] = []
