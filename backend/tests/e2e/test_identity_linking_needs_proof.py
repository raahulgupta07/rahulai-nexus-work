"""Linking an external identity to a local account needs proof, not a string.

★★★The bug this file is the specification for. ``UserManager.oauth_callback``
could not find an ``OAuthAccount`` for the incoming ``(provider, sub)``, fell
back to ``get_by_email(account_email)``, and attached the external identity to
whatever row came back — unconditionally. A matching email STRING was treated as
proof of identity.

That is CVE-2026-53516 (Better Auth, CVSS 8.3 High), and the same shape as the
GitLab / "nOAuth" account-takeover class. The attack:

    1. attacker registers victim@corp.com locally; the row exists, UNVERIFIED
    2. victim signs in through SSO for the first time
    3. the victim's SSO identity attaches to the ATTACKER's row
    4. the attacker still holds the password on that row and now shares the
       victim's account, workspaces and data

``_ldap_authenticate`` has the identical shape at its merge branch: bind
succeeds, ``get_by_email`` finds a pre-existing row, the directory claims it.

★★★It was NOT exploitable on this deployment, and every reason for that is a
CONFIG DEFAULT rather than anything the linking code asserts:
``features.verify_emails = False`` makes ``on_after_register`` stamp
``is_verified = True`` on every account at creation, and
``allow_uninvited_signups = False`` stops a stranger registering. Turn
``verify_emails`` ON — which is the flag an admin flips to be MORE careful — and
step 1 opens up. ``TestWithEmailVerificationOn`` below is the test that says so;
without it nothing in this repository tells you that hardening one setting opens
a takeover path.

The contract now:

    SSO   link to an existing row iff  local row is_verified
                                 AND   IdP asserted email_verified
    LDAP  merge into an existing row iff  local row is_verified
          (no second half — see the asymmetry note in TestLdapMerge)

Run (needs a real schema — this CANNOT live in tests/unit/fork, whose conftest
no-ops ``run_migrations``):

    cd backend && python -m pytest tests/e2e/test_identity_linking_needs_proof.py -v
"""
import os
import uuid

import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine, text


PASSWORD = "test-password-123"
DIRECTORY_PASSWORD = "directory-password-456"

PROVIDER = "keycloak"
LDAP_DOOR = "/api/auth/ldap/login"


# ──────────────────────────────────────────────────────────────────────────
# Touching the users table directly
# ──────────────────────────────────────────────────────────────────────────
# Same synchronous-engine pattern as tests/e2e/test_two_doors.py: ``is_verified``
# has no API that sets it on demand, and an ``asyncio.run`` here would drive the
# app's async engine from a second event loop.

def _sync_db_url():
    url = os.environ.get("TEST_DATABASE_URL")
    assert url, "TEST_DATABASE_URL is set by tests/conftest.py before app import"
    return (url.replace("sqlite+aiosqlite:", "sqlite:")
               .replace("postgresql+asyncpg:", "postgresql:"))


def _stamp(email, **fields):
    assignments = ", ".join(f"{name} = :{name}" for name in fields)
    engine = create_engine(_sync_db_url())
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(f"UPDATE users SET {assignments} WHERE email = :_email"),
                {**fields, "_email": email},
            )
            assert result.rowcount == 1, f"expected one row for {email}, got {result.rowcount}"
    finally:
        engine.dispose()


def _read_user(email):
    engine = create_engine(_sync_db_url())
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT id, is_verified, ldap_dn, name FROM users WHERE email = :e"),
                {"e": email},
            ).mappings().first()
            return dict(row) if row else None
    finally:
        engine.dispose()


def _oauth_accounts(user_id):
    engine = create_engine(_sync_db_url())
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text("SELECT oauth_name, account_id, account_email "
                     "FROM oauth_accounts WHERE user_id = :u"),
                {"u": user_id},
            ).mappings().all()
            return [dict(r) for r in rows]
    finally:
        engine.dispose()


def _email(tag):
    return f"{tag}-{uuid.uuid4().hex[:8]}@test.com"


# ──────────────────────────────────────────────────────────────────────────
# Calling the door under test
# ──────────────────────────────────────────────────────────────────────────

async def _sso_sign_in(**kwargs):
    """Drive ``UserManager.oauth_callback`` the way the real callback does.

    ★A real UserManager over a real session, not a mock: the whole defect lives
    in what this method does with the row ``get_by_email`` returns, so anything
    that stubs the lookup would test the stub.
    """
    from fastapi_users.db import SQLAlchemyUserDatabase

    from app.core.auth import UserManager
    from app.dependencies import async_session_maker
    from app.models.user import User
    from app.models.oauth_account import OAuthAccount

    async with async_session_maker() as session:
        manager = UserManager(SQLAlchemyUserDatabase(session, User, OAuthAccount))
        return await manager.oauth_callback(**kwargs)


def _sso_kwargs(email, *, verified, sub=None, provider=PROVIDER):
    return {
        "oauth_name": provider,
        "access_token": "access-token-xyz",
        "account_id": sub or f"sub-{uuid.uuid4().hex[:12]}",
        "account_email": email,
        "expires_at": None,
        "refresh_token": None,
        "request": None,
        "account_email_verified": verified,
    }


def _assert_link_refused(excinfo, who):
    """The one refusal the SSO door gives for an unproven link."""
    from fastapi import HTTPException

    exc = excinfo.value
    assert isinstance(exc, HTTPException), f"{who}: {exc!r}"
    assert exc.status_code == 403, f"{who}: got {exc.status_code}"
    detail = exc.detail
    assert isinstance(detail, dict), f"{who}: {detail!r}"
    assert detail.get("code") == "identity_link_unverified", f"{who}: {detail}"
    # ★A person, not a stack trace: this reaches the sign-in page verbatim via
    # handle_callback -> _friendly_error_message -> ?error=.
    assert detail.get("message"), f"{who}: no user-facing message in {detail}"


# ──────────────────────────────────────────────────────────────────────────
# The mock directory (same shape as tests/e2e/test_two_doors.py)
# ──────────────────────────────────────────────────────────────────────────

def _directory(entries):
    manager = MagicMock()

    def find_user(email):
        entry = entries.get((email or "").lower())
        if entry is None:
            return None
        return {"dn": entry["dn"], "name": entry.get("name"), "email": email}

    def bind_user(dn, password):
        for entry in entries.values():
            if entry["dn"] == dn:
                return password == entry["password"]
        return False

    manager.find_user.side_effect = find_user
    manager.bind_user.side_effect = bind_user
    manager.test_connection.return_value = {"connected": True, "server": "ldaps://mock-ldap.test:636"}
    manager.search_users.return_value = [
        {"dn": e["dn"], "email": k, "name": e.get("name")} for k, e in entries.items()
    ]
    manager.search_groups.return_value = []
    return manager


# ──────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────

@pytest.fixture
def enable_ldap():
    from app.settings.config import settings

    ldap = settings.dash_config.ldap
    saved = (ldap.enabled, ldap.url, ldap.base_dn, ldap.bind_dn,
             ldap.bind_password, ldap.auto_provision_users)
    ldap.enabled = True
    ldap.url = "ldaps://mock-ldap.test:636"
    ldap.base_dn = "dc=test,dc=com"
    ldap.bind_dn = "cn=admin,dc=test,dc=com"
    ldap.bind_password = "admin_pass"
    ldap.auto_provision_users = True
    yield ldap
    (ldap.enabled, ldap.url, ldap.base_dn, ldap.bind_dn,
     ldap.bind_password, ldap.auto_provision_users) = saved


@pytest.fixture
def exploitable_config():
    """★★★The config under which a stranger can plant an unverified row.

    It takes BOTH flags, and finding that out is half the value of this fixture:

      ``verify_emails = True``          otherwise ``on_after_register`` stamps
                                        ``is_verified = True`` on everything
      ``allow_uninvited_signups = True`` otherwise no stranger can register at
                                        all — and, less obviously, an INVITED
                                        registration is verified regardless of
                                        ``verify_emails``, because
                                        ``_attach_open_memberships`` sets
                                        ``is_verified = True`` when an open
                                        invite exists (an admin already mailed
                                        that address, which is fair)

    ★So this deployment has THREE accidental protections stacked, not the two
    the security review named, and all three are config defaults that assert
    nothing. Flip both flags — a perfectly ordinary open-signup install that
    also wants email verification — and step 1 of the takeover is a sign-up
    form.
    """
    from app.settings.config import settings

    features = settings.dash_config.features
    saved = (features.verify_emails, features.allow_uninvited_signups)
    features.verify_emails = True
    features.allow_uninvited_signups = True
    yield features
    (features.verify_emails, features.allow_uninvited_signups) = saved


@pytest.fixture
def org_admin(test_client, create_user, login_user, whoami):
    """The bootstrap account — used only to issue invites.

    ★Not a subject. First signup also lands ``is_superuser = 1``, so a test that
    used it as "an ordinary local account" would be testing a different row than
    it thinks.
    """
    email = _email("bootstrap-admin")
    create_user(email=email, password=PASSWORD)
    token = login_user(email, PASSWORD)
    org_id = whoami(token)["organizations"][0]["id"]
    return {"email": email, "token": token, "org_id": org_id}


@pytest.fixture
def invite(test_client, org_admin):
    """Open an invite for an address. Sign-up is invite-only after the first
    account, so this is what lets a subject register at all — and what lets the
    create-a-new-user path below reach its create branch."""
    def _invite(email):
        response = test_client.post(
            f"/api/organizations/{org_admin['org_id']}/members",
            json={"organization_id": org_admin["org_id"], "email": email, "role": "member"},
            headers={
                "Authorization": f"Bearer {org_admin['token']}",
                "X-Organization-Id": org_admin["org_id"],
            },
        )
        assert response.status_code == 200, response.text
        return email
    return _invite


@pytest.fixture
def local_account(test_client, create_user, invite):
    """Invite + register a member, then set ``is_verified`` EXPLICITLY.

    ★Always explicit. ``verify_emails`` decides the default and this file
    changes that flag in one class, so a subject that inherited the default
    would silently mean different things in different tests.
    """
    def _make(tag, *, is_verified):
        email = _email(tag)
        invite(email)
        create_user(email=email, password=PASSWORD)
        _stamp(email, is_verified=1 if is_verified else 0)
        return email
    return _make


# ══════════════════════════════════════════════════════════════════════════
# 1. The takeover
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
class TestTheTakeover:

    @pytest.mark.asyncio
    async def test_an_unverified_local_row_does_not_capture_an_sso_identity(
        self, local_account,
    ):
        """★★★The CVE, exactly. Attacker's squatter row must not receive the
        victim's identity, even though the IdP verified the address."""
        squatted = local_account("squatter", is_verified=False)
        before = _read_user(squatted)

        with pytest.raises(Exception) as excinfo:
            await _sso_sign_in(**_sso_kwargs(squatted, verified=True))

        _assert_link_refused(excinfo, "verified SSO identity onto an unverified local row")
        assert _oauth_accounts(before["id"]) == [], (
            "the external identity was attached to the unverified row — this is "
            "the takeover"
        )

    @pytest.mark.asyncio
    async def test_the_refusal_does_not_quietly_create_a_second_account(
        self, local_account,
    ):
        """★The other way to get this wrong. Every membership, invite and lookup
        path in this codebase treats the address as unique; a duplicate row
        either collides on the index or splits one person's data in two."""
        squatted = local_account("squatter-dup", is_verified=False)

        with pytest.raises(Exception):
            await _sso_sign_in(**_sso_kwargs(squatted, verified=True))

        engine = create_engine(_sync_db_url())
        try:
            with engine.begin() as conn:
                count = conn.execute(
                    text("SELECT count(*) FROM users WHERE email = :e"), {"e": squatted}
                ).scalar()
        finally:
            engine.dispose()
        assert count == 1, f"refusal created a duplicate account: {count} rows"


# ══════════════════════════════════════════════════════════════════════════
# 2. The ordinary merge still has to work
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
class TestTheOrdinaryMerge:

    @pytest.mark.asyncio
    async def test_verified_local_row_plus_verified_claim_links(self, local_account):
        """Both halves of the proof present. This is the common path and the
        reason the gate cannot simply be "never link"."""
        email = local_account("ordinary", is_verified=True)
        before = _read_user(email)

        user = await _sso_sign_in(**_sso_kwargs(email, verified=True, sub="sub-ordinary-1"))

        assert str(user.id) == str(before["id"]), "linked to the wrong row"
        accounts = _oauth_accounts(before["id"])
        assert len(accounts) == 1, f"expected one linked identity, got {accounts}"
        assert accounts[0]["oauth_name"] == PROVIDER
        assert accounts[0]["account_id"] == "sub-ordinary-1"

    @pytest.mark.asyncio
    async def test_a_second_sign_in_finds_the_link_and_does_not_re_gate(
        self, local_account,
    ):
        """★Once linked, the (provider, sub) lookup wins before the gate is ever
        reached — so a later sign-in must work even if the IdP stops sending
        ``email_verified``. Getting this wrong locks out everyone who already
        linked, which is a worse outage than the bug."""
        email = local_account("returning", is_verified=True)
        await _sso_sign_in(**_sso_kwargs(email, verified=True, sub="sub-returning-1"))

        user = await _sso_sign_in(**_sso_kwargs(email, verified=None, sub="sub-returning-1"))

        assert user.email == email


# ══════════════════════════════════════════════════════════════════════════
# 3. A verified local row is not enough on its own
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
class TestTheIdpHalfOfTheProof:

    @pytest.mark.asyncio
    async def test_an_explicitly_unverified_claim_does_not_link(self, local_account):
        """The attack run from the far side: an IdP where the user typed their
        own address and never proved it."""
        email = local_account("idp-false", is_verified=True)
        before = _read_user(email)

        with pytest.raises(Exception) as excinfo:
            await _sso_sign_in(**_sso_kwargs(email, verified=False))

        _assert_link_refused(excinfo, "email_verified=false")
        assert _oauth_accounts(before["id"]) == []

    @pytest.mark.asyncio
    async def test_an_absent_claim_does_not_link(self, local_account):
        """★★★Absence of evidence is not evidence. ``None`` is what the base
        fastapi-users OAuth router — which knows nothing about the argument —
        would produce, and it must refuse exactly as ``False`` does. A default
        of ``True`` would keep the signature compatible and the vulnerability
        intact."""
        email = local_account("idp-absent", is_verified=True)
        before = _read_user(email)

        with pytest.raises(Exception) as excinfo:
            await _sso_sign_in(**_sso_kwargs(email, verified=None))

        _assert_link_refused(excinfo, "email_verified absent")
        assert _oauth_accounts(before["id"]) == []

    @pytest.mark.asyncio
    async def test_the_default_argument_is_not_verified(self, local_account):
        """The same thing asserted against the SIGNATURE rather than a passed
        None — a caller that omits the argument entirely."""
        email = local_account("idp-omitted", is_verified=True)
        kwargs = _sso_kwargs(email, verified=None)
        kwargs.pop("account_email_verified")

        with pytest.raises(Exception) as excinfo:
            await _sso_sign_in(**kwargs)

        _assert_link_refused(excinfo, "argument omitted")


# ══════════════════════════════════════════════════════════════════════════
# 4. Nobody there yet — creation is untouched
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
class TestNoPreExistingAccount:

    @pytest.mark.asyncio
    async def test_a_new_address_still_creates_an_account(self, invite):
        """★The gate guards the MATCH, never the create. There is no row to
        take over when no row exists, and refusing here would break first-time
        SSO sign-in for everyone."""
        email = invite(_email("brand-new"))

        user = await _sso_sign_in(**_sso_kwargs(email, verified=True, sub="sub-new-1"))

        assert user.email == email
        row = _read_user(email)
        assert row is not None
        accounts = _oauth_accounts(row["id"])
        assert len(accounts) == 1, f"identity not attached to the new account: {accounts}"

    @pytest.mark.asyncio
    async def test_a_new_address_is_created_even_without_a_verified_claim(self, invite):
        """★Deliberate, and worth stating: an unverified claim is not a reason
        to refuse a brand-new account. It is only a reason to refuse to treat
        the address as a key into somebody else's row. Refusing creation would
        break every Entra tenant (Entra never sends ``email_verified``) for no
        security gain."""
        email = invite(_email("brand-new-unverified"))

        user = await _sso_sign_in(**_sso_kwargs(email, verified=None, sub="sub-new-2"))

        assert user.email == email


# ══════════════════════════════════════════════════════════════════════════
# 5. The directory door has the same shape
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
class TestLdapMerge:
    """★THE ASYMMETRY. There is no ``email_verified`` half here, and that is not
    an oversight: the bind actually succeeded, so the caller holds the
    credential the directory stores for that DN, and the address was read from
    the directory's OWN entry rather than typed by anyone. That is stronger
    evidence than any OAuth claim — about the DIRECTORY side. It says nothing
    about who created the ``users`` row already sitting at that address, which
    is the attacker's asset. So the local half of the gate is identical."""

    def test_an_unverified_local_row_does_not_capture_a_directory_identity(
        self, test_client, local_account, enable_ldap,
    ):
        squatted = local_account("ldap-squatter", is_verified=False)
        before = _read_user(squatted)
        dn = "cn=victim,ou=Users,dc=test,dc=com"

        with patch("app.ee.ldap.connection.LDAPConnectionManager") as CM:
            CM.return_value = _directory({
                squatted.lower(): {"dn": dn, "name": "Victim", "password": DIRECTORY_PASSWORD},
            })
            response = test_client.post(
                LDAP_DOOR, data={"username": squatted, "password": DIRECTORY_PASSWORD}
            )

        assert response.status_code == 400, (
            f"the directory merged into an unverified local row: {response.text}"
        )
        after = _read_user(squatted)
        assert not (after["ldap_dn"] or ""), (
            "the row was stamped with the directory DN — the merge happened"
        )
        # ★The refusal may not say WHY. `_do_authenticate_ldap` funnels every
        # reason into one 400 because a distinguishable answer is a free
        # membership oracle against the customer's Active Directory — see
        # test_two_doors.py, which this must not undo.
        blob = response.text.lower()
        for leak in ("ldap", "directory", "unverified", "verify"):
            assert leak not in blob, f"refusal leaks '{leak}': {response.text}"

    def test_a_verified_local_row_still_merges(
        self, test_client, local_account, enable_ldap,
    ):
        """The ordinary directory merge — one row, DN stamped, no duplicate."""
        email = local_account("ldap-ordinary", is_verified=True)
        dn = "cn=ordinary,ou=Users,dc=test,dc=com"

        with patch("app.ee.ldap.connection.LDAPConnectionManager") as CM:
            CM.return_value = _directory({
                email.lower(): {"dn": dn, "name": "Ordinary", "password": DIRECTORY_PASSWORD},
            })
            response = test_client.post(
                LDAP_DOOR, data={"username": email, "password": DIRECTORY_PASSWORD}
            )

        assert response.status_code == 200, response.text
        assert response.json().get("access_token")
        assert _read_user(email)["ldap_dn"] == dn


# ══════════════════════════════════════════════════════════════════════════
# 6. The config that makes it exploitable
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
class TestWithEmailVerificationOn:
    """★★★The test whose absence was the real problem.

    Everything above stamps ``is_verified`` by hand, which proves the gate but
    not that anyone can REACH the unverified state. This class registers a
    stranger through the real endpoint, with no invite, and shows the row lands
    unverified on its own — i.e. that hardening one setting is what opens the
    takeover, and that the gate is what closes it.
    """

    def test_an_uninvited_registration_leaves_the_account_unverified(
        self, test_client, create_user, exploitable_config,
    ):
        email = _email("verify-on")
        # ★No invite on purpose. `create_user` auto-supplies an invite token
        # whenever one is pending, and an invited registration is verified even
        # under verify_emails=True — see the fixture.
        create_user(email=email, password=PASSWORD)

        row = _read_user(email)
        assert row is not None
        assert not row["is_verified"], (
            "an uninvited registration under verify_emails=True must land "
            "unverified — if this ever passes trivially, the premise of this "
            "whole file is gone"
        )

    @pytest.mark.asyncio
    async def test_the_takeover_is_blocked_under_the_exploitable_config(
        self, test_client, create_user, exploitable_config,
    ):
        """★★★End to end on the exploitable config: a stranger registers the
        victim's address, the victim signs in via SSO, and the identity does NOT
        land on the stranger's row."""
        victim_address = _email("victim")
        create_user(email=victim_address, password="attacker-password-789")
        attacker_row = _read_user(victim_address)
        assert not attacker_row["is_verified"], "premise: the squatter row is unverified"

        with pytest.raises(Exception) as excinfo:
            await _sso_sign_in(**_sso_kwargs(victim_address, verified=True))

        _assert_link_refused(excinfo, "takeover under verify_emails=True")
        assert _oauth_accounts(attacker_row["id"]) == []


# ══════════════════════════════════════════════════════════════════════════
# 7. preferred_username / upn may not be a linking key
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
class TestUnverifiableClaimsAreNotLinkingKeys:
    """★The nOAuth half, tested where the decision is made.

    ``preferred_username`` is a mutable, non-unique login handle the user can
    often set themselves; ``upn`` is an AD attribute that need not be a
    deliverable address. They are KEPT as a source for the address — removing
    them breaks real Entra / AD FS tenants where ``upn`` is the only address in
    the token — but an address that came from one of them can never carry
    ``email_verified``, so it can create a new account and never match an
    existing one.
    """

    def _claims(self, **extra):
        base = {"sub": "sub-nooauth-1"}
        base.update(extra)
        return base

    def _extract(self, claims, provider="entra"):
        """★The PRODUCTION function, not a copy of it.

        An earlier draft of this class re-implemented the claim logic here and
        asserted against its own copy — which passes forever regardless of what
        the callback does. `_email_and_verification_from_claims` exists as a
        named function precisely so this can call the real thing.
        """
        from app.services.auth_providers import _email_and_verification_from_claims

        # `cfg` is the provider config object; the only thing read off it is an
        # optional `trust_email_claim`, and a plain object has none — which is
        # the current shape of every real provider config too.
        # ★Returns `(email, verified, reason)` since 0.0.543.5 — the reason is
        # what the refusal log and the sign-in message are built from. These
        # tests are about the VERDICT, so the reason is dropped here rather than
        # threaded through thirty assertions; `_extract_with_reason` below is
        # for anything that cares which branch decided.
        email, verified, _reason = _email_and_verification_from_claims(
            claims, provider, object()
        )
        return email, verified

    def _extract_with_reason(self, claims: dict, provider: str = "oidc"):
        from app.services.auth_providers import _email_and_verification_from_claims

        return _email_and_verification_from_claims(claims, provider, object())

    def test_preferred_username_supplies_the_address_but_never_the_proof(self):
        email, verified = self._extract(
            self._claims(preferred_username="victim@corp.com", email_verified=True)
        )
        assert email == "victim@corp.com", "the address must still be usable for creation"
        assert verified is False, (
            "preferred_username was accepted as verified — this is nOAuth: an "
            "attacker who can set their own handle claims the victim's row"
        )

    def test_upn_supplies_the_address_but_never_the_proof(self):
        email, verified = self._extract(
            self._claims(upn="victim@corp.com", email_verified=True)
        )
        assert email == "victim@corp.com"
        assert verified is False

    def test_the_real_email_claim_still_carries_proof(self):
        email, verified = self._extract(
            self._claims(email="real@corp.com", email_verified=True)
        )
        assert email == "real@corp.com"
        assert verified is True

    def test_a_string_true_counts_and_a_string_false_does_not(self):
        """★Providers disagree with the spec about this claim's type. A bare
        ``bool(value)`` would make the STRING ``"false"`` true, which is the
        wrong way to be wrong."""
        from app.services import auth_providers as ap

        assert ap._claim_is_true(True) is True
        assert ap._claim_is_true("true") is True
        assert ap._claim_is_true("1") is True
        assert ap._claim_is_true(False) is False
        assert ap._claim_is_true("false") is False
        assert ap._claim_is_true(None) is False
        assert ap._claim_is_true("") is False

    def test_a_provider_that_never_sends_the_claim_is_unverified_by_default(self):
        """★Entra emits no ``email_verified`` at all. Unverified unless an admin
        explicitly vouches for the provider."""
        email, verified = self._extract(self._claims(email="entra@corp.com"))
        assert email == "entra@corp.com"
        assert verified is False

    def test_an_admin_can_vouch_for_a_provider_that_never_sends_the_claim(self):
        """★The escape hatch, and why it exists: without it every Entra tenant
        with any pre-existing local account loses SSO linking, which looks like
        the integration broke. Off by default — the safe failure is a refused
        link an admin can fix in one setting."""
        from app.services import auth_providers as ap

        saved = os.environ.get("DASH_TRUST_EMAIL_CLAIM_PROVIDERS")
        os.environ["DASH_TRUST_EMAIL_CLAIM_PROVIDERS"] = "entra, okta"
        try:
            email, verified = self._extract(self._claims(email="entra@corp.com"))
            assert verified is True
            # ...and only for the providers named.
            _, other = self._extract(self._claims(email="x@corp.com"), provider="keycloak")
            assert other is False
        finally:
            if saved is None:
                os.environ.pop("DASH_TRUST_EMAIL_CLAIM_PROVIDERS", None)
            else:
                os.environ["DASH_TRUST_EMAIL_CLAIM_PROVIDERS"] = saved

    def test_vouching_does_not_rescue_preferred_username(self):
        """★★★The one combination that would re-open nOAuth: an admin vouches
        for the provider, and the address came from a handle the user controls.
        Trust is about the ``email`` claim, never about the fallbacks."""
        from app.services import auth_providers as ap

        saved = os.environ.get("DASH_TRUST_EMAIL_CLAIM_PROVIDERS")
        os.environ["DASH_TRUST_EMAIL_CLAIM_PROVIDERS"] = "entra"
        try:
            email, verified = self._extract(
                self._claims(preferred_username="victim@corp.com")
            )
            assert email == "victim@corp.com"
            assert verified is False
        finally:
            if saved is None:
                os.environ.pop("DASH_TRUST_EMAIL_CLAIM_PROVIDERS", None)
            else:
                os.environ["DASH_TRUST_EMAIL_CLAIM_PROVIDERS"] = saved
