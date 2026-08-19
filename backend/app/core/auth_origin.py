"""Where an account's password actually lives.

Only a *local* account holds a password this application owns, so only a local
account can have one set by an admin or changed by its owner. Every other origin
authenticates somewhere else and the password here is a placeholder.

★★★The obvious test — "does the row have a hashed_password?" — is WRONG and was
the first thing tried. Every account in this system is created with one:

    core/auth.py  LDAP auto-provision   hashed_password=ph.hash(ph.generate())
    core/auth.py  SSO first login       hashed_password=...generate()
    core/auth.py  invite provisioning   hashed_password=ph.hash(ph.generate())
    ee/scim/service.py                  (same shape)

so `bool(user.hashed_password)` is True for literally everyone and proves
nothing. The origin has to come from the provisioning markers instead.

★`ldap_dn` was a declared-but-never-written column until the password work —
`models/user.py` declared it and no code in the backend assigned it, so an LDAP
account was indistinguishable from a local one. `core/auth.py` now records the
DN both when auto-provisioning and, as a backfill, whenever an existing account
authenticates through the directory.
"""

from typing import List, Optional

ORIGIN_LOCAL = "local"
ORIGIN_SSO = "sso"
ORIGIN_LDAP = "ldap"
ORIGIN_SCIM = "scim"

# Origins whose password this application owns. Everything else is refused by
# the set-password and change-password routes.
MANAGED_HERE = frozenset({ORIGIN_LOCAL})

# Human wording for the refusal, keyed by origin. The route sends this straight
# to the client so the message names the actual directory rather than saying
# "not allowed".
ORIGIN_OWNER = {
    ORIGIN_SSO: "your identity provider",
    ORIGIN_LDAP: "your directory",
    ORIGIN_SCIM: "your identity provider",
}


def resolve_auth_origin(user, *, oauth_accounts: Optional[list] = None) -> str:
    """Classify where ``user`` signs in from.

    ``oauth_accounts`` may be passed explicitly by callers that already loaded
    the collection in a batch. Reading ``user.oauth_accounts`` inside an async
    request lazy-loads, which raises under asyncpg — so callers that have not
    eager-loaded it MUST pass it, and a caller that passes ``None`` gets a
    best-effort read of whatever is already on the instance.
    """
    if getattr(user, "scim_external_id", None):
        return ORIGIN_SCIM
    if getattr(user, "ldap_dn", None):
        return ORIGIN_LDAP

    accounts = oauth_accounts
    if accounts is None:
        # Only trust an already-populated relationship; never trigger a lazy
        # load here. `__dict__` is how SQLAlchemy records a loaded collection.
        accounts = user.__dict__.get("oauth_accounts")
    if accounts:
        return ORIGIN_SSO

    return ORIGIN_LOCAL


def resolve_auth_origins(user, *, oauth_accounts: Optional[list] = None) -> List[str]:
    """Every way ``user`` can sign in, most authoritative first.

    ★★★`resolve_auth_origin` above answers a DIFFERENT question — *whose
    password is this* — and answers it by precedence, because a password can
    only be owned by one system. That is right for the Set-password gate and
    wrong for a column describing ACCESS: a person can hold a directory account
    and a linked Keycloak identity at once, and both genuinely let them in.

    Measured on the dev install 2026-08-19: the Members roster borrowed the
    password answer, so staff who sign in through Keycloak showed as **LDAP**
    and nothing else, and the administrator reading that column concluded single
    sign-on was not configured for them.

    ★`local` is never listed beside another origin. A provisioned account's hash
    is a random string nobody holds, so calling it a way in would be fiction —
    the same fiction the People screen was showing.

    ★The first entry is always what `resolve_auth_origin` returns, so anything
    reading only the leading badge stays correct.
    """
    accounts = oauth_accounts
    if accounts is None:
        # Never trigger a lazy load here — see the note in resolve_auth_origin.
        accounts = user.__dict__.get("oauth_accounts")

    origins: List[str] = []
    if getattr(user, "scim_external_id", None):
        origins.append(ORIGIN_SCIM)
    if getattr(user, "ldap_dn", None):
        origins.append(ORIGIN_LDAP)
    if accounts:
        origins.append(ORIGIN_SSO)

    return origins or [ORIGIN_LOCAL]


def password_is_managed_here(origin: str) -> bool:
    return origin in MANAGED_HERE


def origin_owner_label(origin: str) -> str:
    return ORIGIN_OWNER.get(origin, "another system")
