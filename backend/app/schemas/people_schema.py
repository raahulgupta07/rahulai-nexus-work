from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class IdentityView(BaseModel):
    """One authentication identity that resolves to a person.

    ``kind`` is "local" for the password identity or "oauth" for a linked
    SSO/OAuth account. Accounts are unified by email, so a single person may
    carry a local identity plus any number of oauth identities.
    """

    kind: str  # "local" | "oauth"
    provider: str  # "local" for the password identity; OAuthAccount.oauth_name otherwise
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
