"""Which ways in a person actually has, for the screens that show them.

WHY THIS IS ITS OWN MODULE
--------------------------
`routes/people.py` built the identity list inline, from
`bool(getattr(user, "hashed_password", None))`. `app/core/auth_origin.py` opens
by naming that exact expression as the first thing anyone tries and the wrong
thing: every account in this system is created with a hash — LDAP
auto-provision, SSO first login, invite provisioning and SCIM all call
`ph.hash(ph.generate())` — so it is True for literally everyone and proves
nothing.

Measured on the dev install 2026-08-19, Settings ▸ People & Identities showed a
directory-provisioned account as:

    kaungminhtet    local (primary)    ·    keycloak

The `local` row is a password nobody holds, and the directory the person really
signs in from is not on the screen at all, because the merge only ever looked at
`oauth_accounts`.

★The rule now comes from `resolve_auth_origin`, which the password routes have
used since `0.0.521.5`. One rule, so the screen and the Set-password button can
never disagree about the same account — the drift between them is what produced
a button offered on an account the route refuses.

★It is a pure function taking the rows, not the session: the route does the
querying (batched, no N+1), this does the deciding, and the deciding is testable
without a schema.
"""
from typing import Any, Iterable, List, Optional

from app.core.auth_origin import (
    ORIGIN_LDAP,
    ORIGIN_LOCAL,
    ORIGIN_SCIM,
    ORIGIN_SSO,
    password_is_managed_here,
    resolve_auth_origin,
)
from app.schemas.people_schema import IdentityView


def _sorted_accounts(oauth_accounts: Optional[Iterable[Any]]) -> List[Any]:
    """Stable order, so the screen does not reshuffle between reads."""
    return sorted(
        list(oauth_accounts or []),
        key=lambda oa: (getattr(oa, "account_email", "") or "", getattr(oa, "account_id", "") or ""),
    )


def has_local_password(user: Any, oauth_accounts: Optional[Iterable[Any]] = None) -> bool:
    """Does this product own a password for ``user``?

    ★Not "is there a hash in the column" — see the module docstring. This is the
    same question `PUT /users/{id}/password` answers before it refuses, and it
    is answered by the same function, so the button and the route agree by
    construction rather than by review.
    """
    origin = resolve_auth_origin(user, oauth_accounts=_sorted_accounts(oauth_accounts))
    return password_is_managed_here(origin)


def merge_identities(
    user: Any, oauth_accounts: Optional[Iterable[Any]] = None
) -> List[IdentityView]:
    """Every way ``user`` can sign in, most authoritative first.

    Emits, in order: the SCIM provisioning record, the directory entry, each
    linked OAuth/SSO account, and — only when nothing else provisioned the
    account — the local password.

    ★A person may hold more than one at once. That is the whole point: access is
    not exclusive, and the reported screen showed one of two. Password OWNERSHIP
    is exclusive, and that is what `is_primary` marks.
    """
    accounts = _sorted_accounts(oauth_accounts)
    origin = resolve_auth_origin(user, oauth_accounts=accounts)
    email = getattr(user, "email", None)

    identities: List[IdentityView] = []

    scim_id = getattr(user, "scim_external_id", None)
    if scim_id:
        identities.append(
            IdentityView(
                kind="directory",
                provider=ORIGIN_SCIM,
                account_email=email,
                account_id=str(scim_id),
            )
        )

    ldap_dn = getattr(user, "ldap_dn", None)
    if ldap_dn:
        identities.append(
            IdentityView(
                kind="directory",
                provider=ORIGIN_LDAP,
                account_email=email,
                account_id=str(ldap_dn),
            )
        )

    for oa in accounts:
        identities.append(
            IdentityView(
                kind="oauth",
                provider=oa.oauth_name,
                account_email=getattr(oa, "account_email", None),
                account_id=getattr(oa, "account_id", None),
            )
        )

    if origin == ORIGIN_LOCAL:
        # Nothing provisioned this account, so the password is genuinely ours
        # and it is the only way in. Listed first because it is the primary.
        identities.insert(
            0,
            IdentityView(
                kind="local", provider=ORIGIN_LOCAL, account_email=email, account_id=None
            ),
        )

    # ★Mark the primary by the ORIGIN rule rather than by taking index 0. The two
    # coincide today, and writing it as "index 0" would silently mark the wrong
    # row the first time this list gains an entry that precedes the owner.
    primary_provider = {
        ORIGIN_SCIM: ORIGIN_SCIM,
        ORIGIN_LDAP: ORIGIN_LDAP,
        ORIGIN_LOCAL: ORIGIN_LOCAL,
    }.get(origin)
    for identity in identities:
        if primary_provider is None:
            # SSO: the earliest linked account, in the stable order above.
            if identity.kind == "oauth":
                identity.is_primary = True
                break
        elif identity.provider == primary_provider:
            identity.is_primary = True
            break

    return identities
