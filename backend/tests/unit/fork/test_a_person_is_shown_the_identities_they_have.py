"""People & Identities invented a password and hid the directory.

WHAT THIS COST
--------------
Measured live on the dev install, 2026-08-19. Every person on
Settings ▸ People & Identities carried a **local** identity marked primary,
including accounts that have never had a password anyone holds — the LDAP-
provisioned staff. And the directory those accounts actually sign in from was
not on the screen at all, because the merge only ever looked at `oauth_accounts`.

So the one screen whose entire job is "where does this person sign in from"
answered:

    kaungminhtet   local (primary)   ·   keycloak

for an account provisioned out of the directory. The `local` row is fiction, and
the directory row is missing.

★★★The cause is one line, and `app/core/auth_origin.py` documents it as the
first thing anyone tries and the wrong thing:

    has_password = bool(getattr(user, "hashed_password", None))

Every account in this system is created with a hash — LDAP auto-provision, SSO
first login, invite provisioning and SCIM all call `ph.hash(ph.generate())` — so
that expression is True for literally everyone and proves nothing. The origin
has to be READ from the provisioning markers (`scim_external_id`, `ldap_dn`,
`oauth_accounts`), which is exactly what `resolve_auth_origin` does and what the
password routes have used since `0.0.521.5`.

This is the same class as the two auth outages this fork already has a section
about: something DECIDED where an account came from instead of READING it.

WHAT IS PINNED HERE
-------------------
The merge is a pure function so it can be tested without a schema — the route
does the querying, this does the deciding, and the two failure modes above are
decided here.
"""
import types

import pytest

from app.core import auth_origin
from app.services.identity_view import merge_identities


def _user(**kw):
    """A user row, with every marker absent unless named."""
    base = dict(
        id="u1",
        email="person@example.com",
        name="A Person",
        # ★Present on EVERY account in this system. That is the point.
        hashed_password="$argon2id$v=19$m=65536,t=3,p=4$...",
        ldap_dn=None,
        scim_external_id=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _oauth(name="keycloak", email="person@example.com", account_id="kc-1"):
    return types.SimpleNamespace(
        oauth_name=name, account_email=email, account_id=account_id
    )


def _providers(identities):
    return [i.provider for i in identities]


# --- the defect --------------------------------------------------------------


def test_a_directory_account_is_not_shown_a_password_it_never_had():
    user = _user(ldap_dn="uid=kaungminhtet,ou=people,dc=chl,dc=local")

    identities = merge_identities(user, [])

    assert "local" not in _providers(identities), (
        "the screen offered a local password identity for a directory account — "
        "every account carries a hash, so `bool(hashed_password)` said yes"
    )


def test_the_directory_the_person_actually_signs_in_from_is_on_the_screen():
    user = _user(ldap_dn="uid=kaungminhtet,ou=people,dc=chl,dc=local")

    identities = merge_identities(user, [])

    assert "ldap" in _providers(identities), (
        "the one place this person signs in from is missing from the identity list"
    )
    assert [i.is_primary for i in identities].count(True) == 1


def test_both_ways_in_are_shown_when_the_person_has_both():
    """★The reported case: linked to the directory AND to Keycloak. A screen
    that shows one of two is worse than one that shows neither, because it reads
    as complete."""
    user = _user(ldap_dn="uid=kaungminhtet,ou=people,dc=chl,dc=local")

    identities = merge_identities(user, [_oauth()])

    assert set(_providers(identities)) >= {"ldap", "keycloak"}
    assert [i.is_primary for i in identities].count(True) == 1, (
        "exactly one identity is primary, whatever else is listed"
    )


def test_a_scim_provisioned_account_says_so():
    user = _user(scim_external_id="ext-9911")
    identities = merge_identities(user, [])

    assert "scim" in _providers(identities)
    assert "local" not in _providers(identities)


# --- what must NOT change ----------------------------------------------------


def test_a_genuinely_local_account_still_shows_its_password():
    user = _user()
    identities = merge_identities(user, [])

    assert _providers(identities) == ["local"]
    assert identities[0].kind == "local"
    assert identities[0].is_primary is True
    assert identities[0].account_email == user.email


def test_an_sso_only_account_is_unchanged():
    """This case was already right, and is the positive control: a fix that
    simply stopped emitting `local` everywhere would still pass the tests above
    and break nothing here — so it is asserted rather than assumed."""
    user = _user()
    identities = merge_identities(user, [_oauth(name="google", account_id="g-1")])

    assert _providers(identities) == ["google"]
    assert identities[0].kind == "oauth"
    assert identities[0].is_primary is True
    assert identities[0].account_id == "g-1"


def test_several_linked_accounts_keep_a_stable_order():
    user = _user()
    identities = merge_identities(
        user,
        [_oauth(name="keycloak", account_id="k-2"), _oauth(name="google", account_id="g-1")],
    )

    assert len(identities) == 2
    assert [i.is_primary for i in identities].count(True) == 1
    # Same list, same order, every call — the screen must not reshuffle.
    again = merge_identities(
        user,
        [_oauth(name="keycloak", account_id="k-2"), _oauth(name="google", account_id="g-1")],
    )
    assert _providers(identities) == _providers(again)


# --- the screen's "has a password" flag --------------------------------------


def test_has_password_means_a_password_this_product_owns():
    """★The flag drives whether Set password is offered. It must mean the same
    thing the password ROUTE means, or the button is offered on an account the
    route will refuse."""
    from app.services.identity_view import has_local_password

    assert has_local_password(_user(), []) is True
    assert has_local_password(_user(ldap_dn="uid=x,dc=y"), []) is False
    assert has_local_password(_user(scim_external_id="ext-1"), []) is False
    assert has_local_password(_user(), [_oauth()]) is False


def test_the_route_no_longer_decides_this_for_itself():
    """★A second copy of this rule is how the two screens drifted apart in the
    first place. `routes/people.py` must ask, not derive."""
    import inspect

    from app.routes import people

    source = inspect.getsource(people)
    assert "bool(getattr(user, \"hashed_password\"" not in source, (
        "the route still classifies accounts by whether a hash exists — "
        "see app/core/auth_origin.py for why that is True for everyone"
    )
    assert "merge_identities" in source


def test_the_shared_rule_is_the_password_routes_rule():
    """Not "behaves the same" — IS the same function. `user_password.py` has
    refused non-local accounts since 0.0.521.5 and this screen must agree with
    it by construction."""
    import inspect

    from app.services import identity_view

    source = inspect.getsource(identity_view)
    assert "resolve_auth_origin" in source
    assert identity_view.resolve_auth_origin is auth_origin.resolve_auth_origin


def test_the_rule_that_was_removed_is_still_detected():
    """★Carry the red proof IN the test.

    These assertions were written against a tree where `merge_identities` did
    not exist, so they failed at IMPORT — which proves the module is missing and
    nothing about whether they detect the defect. A red proof done once at a
    shell prompt rots into a comment; one that runs every time cannot.

    So the pre-fix rule is reconstructed here and required to produce exactly
    the wrong screen that was reported: a local password for an account that has
    none, and no sign of the directory.
    """
    def _pre_fix(user, oauth_accounts):
        # routes/people.py, verbatim in effect, before this change.
        has_password = bool(getattr(user, "hashed_password", None))
        out = ["local"] if has_password else []
        return out + [oa.oauth_name for oa in oauth_accounts]

    directory_user = _user(ldap_dn="uid=kaungminhtet,ou=people,dc=chl,dc=local")

    assert _pre_fix(directory_user, []) == ["local"], (
        "the reconstruction no longer reproduces the defect, so these tests "
        "have stopped proving anything"
    )
    assert _pre_fix(directory_user, [_oauth()]) == ["local", "keycloak"]
    assert "ldap" not in _pre_fix(directory_user, [_oauth()])

    # And the same inputs through the shipped rule.
    assert "local" not in _providers(merge_identities(directory_user, []))
    assert "ldap" in _providers(merge_identities(directory_user, []))
