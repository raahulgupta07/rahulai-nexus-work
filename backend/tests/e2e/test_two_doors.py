"""Two doors: the local password form and the directory form are separate.

★★★The bug this file is the specification for: turning LDAP on used to change
what the LOCAL door does. ``UserManager.authenticate`` ran the directory first
for every sign-in, and a member who simply is not in Active Directory came back
``find_user -> None -> "failed"``, which then refused local fallback to anyone
who is not a superuser. So enabling a directory locked out every local member —
contractors, service accounts, the analyst who was created before LDAP existed.
They had a valid password and no way in, and the error told them nothing.

The fix is to stop making one endpoint mean two things:

    POST /api/auth/jwt/login    LOCAL ONLY  — never calls LDAP, routes on ldap_dn
    POST /api/auth/ldap/login   LDAP ONLY   — never falls back to a local password

★★★And the naive fix — "if they're not in the directory, try their local
password" — is worse than the bug. It is exactly how an ex-employee walks back
in: offboarding removes the directory entry, the stale ``users`` row and its old
password hash are still there, and a fallback hands them a session. Every
assertion about the ex-employee below exists for that. Both doors must refuse
them, and neither may say why.

★The "why" is the other half. The customer's directory is a staff roster; an
endpoint that answers "no such user" differently from "wrong password" lets an
unauthenticated stranger enumerate it one address at a time. So both doors
return the SAME 400 ``LOGIN_BAD_CREDENTIALS``, and one test here compares the
two failure bodies for byte equality rather than merely checking each is a 400.

Run (needs a real schema — this CANNOT live in tests/unit/fork, whose conftest
no-ops ``run_migrations``):

    cd backend && python -m pytest tests/e2e/test_two_doors.py -v
"""
import os
import uuid

import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine, text


LOCAL_DOOR = "/api/auth/jwt/login"
LDAP_DOOR = "/api/auth/ldap/login"

PASSWORD = "test-password-123"
DIRECTORY_PASSWORD = "directory-password-456"


# ──────────────────────────────────────────────────────────────────────────
# The mock directory
# ──────────────────────────────────────────────────────────────────────────
# Patched at the DEFINITION site (``app.ee.ldap.connection.LDAPConnectionManager``)
# rather than at an import site, because ``_ldap_authenticate`` imports it inside
# the function body and the new LDAP door may import it somewhere else again.

def _directory(entries):
    """A mocked ``LDAPConnectionManager`` over ``entries``.

    ``entries`` maps lowercased email -> {"dn", "name", "password"}. An address
    that is absent is *not in the directory* — the ex-employee case.
    """
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


ALICE_DN = "cn=alice,ou=Users,dc=test,dc=com"
EX_DN = "cn=exemployee,ou=Users,dc=test,dc=com"


@pytest.fixture
def enable_ldap():
    """Turn the file-config directory on for the duration of one test.

    ``resolve_login_ldap_config`` reads the database first and falls back to the
    file when no organization has an enabled ``ldap`` block — which is the case
    for a freshly migrated test database, so this fixture is what the login path
    actually sees.
    """
    from app.settings.config import settings

    ldap = settings.dash_config.ldap
    saved = (ldap.enabled, ldap.url, ldap.base_dn, ldap.bind_dn,
             ldap.bind_password, ldap.auto_provision_users)
    ldap.enabled = True
    ldap.url = "ldaps://mock-ldap.test:636"
    ldap.base_dn = "dc=test,dc=com"
    ldap.bind_dn = "cn=admin,dc=test,dc=com"
    ldap.bind_password = "admin_pass"
    # ★Deliberately on. Every directory persona below ALSO has a local row, so
    # this changes none of their outcomes — it only removes a refusal that would
    # come from provisioning policy rather than from the contract under test.
    ldap.auto_provision_users = True
    yield ldap
    (ldap.enabled, ldap.url, ldap.base_dn, ldap.bind_dn,
     ldap.bind_password, ldap.auto_provision_users) = saved


# ──────────────────────────────────────────────────────────────────────────
# Personas
# ──────────────────────────────────────────────────────────────────────────
# Users are REGISTERED through the real endpoint (so the password hash, the org
# and the membership are all genuine), then their directory-managed flags are
# stamped straight onto the row. ``ldap_dn`` has no API that sets it on demand,
# and a synchronous engine is the pattern already used by tests/fixtures/user.py
# to touch the test database alongside ``test_client`` — an ``asyncio.run`` here
# would drive the app's async engine from a second event loop.

def _sync_db_url():
    url = os.environ.get("TEST_DATABASE_URL")
    assert url, "TEST_DATABASE_URL is set by tests/conftest.py before app import"
    return (url.replace("sqlite+aiosqlite:", "sqlite:")
               .replace("postgresql+asyncpg:", "postgresql:"))


def _stamp(email, **fields):
    """Set columns on an existing ``users`` row. Field names are literals here."""
    assignments = ", ".join(f"{name} = :{name}" for name in fields)
    engine = create_engine(_sync_db_url())
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(f"UPDATE users SET {assignments} WHERE email = :_email"),
                {**fields, "_email": email},
            )
            assert result.rowcount == 1, f"expected exactly one row for {email}, got {result.rowcount}"
    finally:
        engine.dispose()


def _email(tag):
    return f"{tag}-{uuid.uuid4().hex[:8]}@test.com"


@pytest.fixture
def org_admin(test_client, create_user, login_user, whoami):
    """The bootstrap account, left untouched so it can still issue invites.

    ★★★It is NOT a persona. First signup bootstraps the organization AND lands
    ``is_superuser = 1`` on the row — so a test that registers one user and
    calls them "a local member" is quietly testing the break-glass branch
    instead. Every ``ldap_dn`` assertion in an earlier draft of this file passed
    for exactly that reason and proved nothing. Personas are invited members,
    with their flags stamped explicitly.
    """
    email = _email("bootstrap-admin")
    create_user(email=email, password=PASSWORD)
    token = login_user(email, PASSWORD)
    org_id = whoami(token)["organizations"][0]["id"]
    return {"email": email, "token": token, "org_id": org_id}


@pytest.fixture
def make_person(test_client, create_user, org_admin):
    """Invite + register a member, then stamp the flags the doors route on.

    Sign-up is invite-only after the first account, so a persona cannot simply
    register. ``ldap_dn`` and ``is_superuser`` are always passed explicitly —
    never left to whatever the bootstrap happens to do.
    """
    def _make(tag, *, ldap_dn=None, is_superuser=False):
        email = _email(tag)
        invite = test_client.post(
            f"/api/organizations/{org_admin['org_id']}/members",
            json={"organization_id": org_admin["org_id"], "email": email, "role": "member"},
            headers={
                "Authorization": f"Bearer {org_admin['token']}",
                "X-Organization-Id": org_admin["org_id"],
            },
        )
        assert invite.status_code == 200, invite.text
        create_user(email=email, password=PASSWORD)
        _stamp(email, ldap_dn=ldap_dn, is_superuser=is_superuser)
        return email
    return _make


def _local_login(test_client, email, password):
    return test_client.post(LOCAL_DOOR, data={"username": email, "password": password})


def _ldap_login(test_client, email, password):
    """POST the directory door.

    ★Same OAuth2 form shape and same response shape as the local door, on
    purpose: a client can post the identical body to either. That is part of the
    contract, so this helper does NOT quietly retry in some other encoding — a
    422 here should fail the test that asked for it.
    """
    return test_client.post(LDAP_DOOR, data={"username": email, "password": password})


def _assert_generic_refusal(response, who):
    """The one refusal both doors are allowed to give."""
    assert response.status_code == 400, (
        f"{who}: expected a generic 400 refusal, got {response.status_code}: {response.text}"
    )
    body = response.json()
    assert body.get("detail") == "LOGIN_BAD_CREDENTIALS", f"{who}: {body}"
    # ★Nothing in the body may hint at which half of the credential was wrong,
    # nor at the directory's existence.
    blob = response.text.lower()
    for leak in ("ldap", "directory", "not found", "no such", "unknown user", "password"):
        assert leak not in blob, f"{who}: refusal leaks '{leak}': {response.text}"


def _assert_logged_in(response, who):
    assert response.status_code == 200, f"{who}: expected a session, got {response.status_code}: {response.text}"
    assert response.json().get("access_token"), f"{who}: no access_token in {response.text}"


# ══════════════════════════════════════════════════════════════════════════
# 1. The local member — the lockout this whole change exists to fix
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
class TestLocalMember:
    """No ``ldap_dn``, not in the directory, has a password. Must keep working."""

    def test_local_member_signs_in_at_the_local_door_while_ldap_is_on(
        self, test_client, make_person, enable_ldap,
    ):
        email = make_person("local-member")

        with patch("app.ee.ldap.connection.LDAPConnectionManager") as CM:
            # The directory does not know this person, and must never be asked.
            CM.return_value = _directory({})
            response = _local_login(test_client, email, PASSWORD)

            _assert_logged_in(response, "local member at the local door")
            # ★The stronger half of the assertion: not "it worked anyway" but
            # "the directory was not consulted at all". A local door that
            # consults LDAP and then recovers is one refactor away from the
            # lockout coming back.
            assert not CM.called, (
                "the local door called LDAP; it must route on User.ldap_dn alone"
            )

    def test_local_member_with_a_wrong_password_is_refused_generically(
        self, test_client, make_person, enable_ldap,
    ):
        email = make_person("local-member-bad")

        with patch("app.ee.ldap.connection.LDAPConnectionManager") as CM:
            CM.return_value = _directory({})
            _assert_generic_refusal(
                _local_login(test_client, email, "not-the-password"),
                "local member, wrong password",
            )

    def test_local_member_is_refused_at_the_ldap_door(
        self, test_client, make_person, enable_ldap,
    ):
        """★The door has no local-password fallback — not even for a local member
        whose password is correct. If this passes with a session, the LDAP door
        is a second local login form and the ex-employee guard below is void."""
        email = make_person("local-member-ldap-door")

        with patch("app.ee.ldap.connection.LDAPConnectionManager") as CM:
            CM.return_value = _directory({})
            _assert_generic_refusal(
                _ldap_login(test_client, email, PASSWORD),
                "local member at the LDAP door",
            )


# ══════════════════════════════════════════════════════════════════════════
# 2. The directory user — signs in at the directory door, and only there
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
class TestDirectoryUser:
    """In the directory, carries ``ldap_dn``. Their password lives in AD."""

    def _seed(self, make_person):
        email = make_person("directory-user", ldap_dn=ALICE_DN)
        return email, {email.lower(): {"dn": ALICE_DN, "name": "Alice Smith",
                                       "password": DIRECTORY_PASSWORD}}

    def test_directory_user_signs_in_at_the_ldap_door(
        self, test_client, make_person, enable_ldap,
    ):
        email, entries = self._seed(make_person)
        with patch("app.ee.ldap.connection.LDAPConnectionManager") as CM:
            CM.return_value = _directory(entries)
            _assert_logged_in(
                _ldap_login(test_client, email, DIRECTORY_PASSWORD),
                "directory user at the LDAP door",
            )

    def test_directory_user_is_refused_at_the_local_door(
        self, test_client, make_person, enable_ldap,
    ):
        """Even with the local password they were registered with. The account is
        directory-managed; the local hash is not a second key to it."""
        email, entries = self._seed(make_person)
        with patch("app.ee.ldap.connection.LDAPConnectionManager") as CM:
            CM.return_value = _directory(entries)
            _assert_generic_refusal(
                _local_login(test_client, email, PASSWORD),
                "directory user at the local door",
            )

    def test_directory_user_with_a_wrong_directory_password_is_refused(
        self, test_client, make_person, enable_ldap,
    ):
        email, entries = self._seed(make_person)
        with patch("app.ee.ldap.connection.LDAPConnectionManager") as CM:
            CM.return_value = _directory(entries)
            _assert_generic_refusal(
                _ldap_login(test_client, email, "wrong-directory-password"),
                "directory user, wrong directory password",
            )


# ══════════════════════════════════════════════════════════════════════════
# 3. ★★★The ex-employee — the row that makes or breaks offboarding
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
class TestExEmployee:
    """Was in the directory (so ``ldap_dn`` is set), has been removed from it,
    and still has the local password row nobody ever deletes.

    BOTH doors refuse. A "not in the directory? try their local password"
    fallback would let them straight back in and quietly undo the offboarding —
    with no error anywhere, because from the app's side it looks like a
    perfectly ordinary successful login.
    """

    def _seed(self, make_person):
        return make_person("ex-employee", ldap_dn=EX_DN)

    def test_ex_employee_is_refused_at_the_local_door(
        self, test_client, make_person, enable_ldap,
    ):
        email = self._seed(make_person)
        with patch("app.ee.ldap.connection.LDAPConnectionManager") as CM:
            CM.return_value = _directory({})  # removed from the directory
            _assert_generic_refusal(
                _local_login(test_client, email, PASSWORD),
                "ex-employee at the local door",
            )

    def test_ex_employee_is_refused_at_the_ldap_door(
        self, test_client, make_person, enable_ldap,
    ):
        email = self._seed(make_person)
        with patch("app.ee.ldap.connection.LDAPConnectionManager") as CM:
            CM.return_value = _directory({})
            _assert_generic_refusal(
                _ldap_login(test_client, email, PASSWORD),
                "ex-employee at the LDAP door",
            )

    def test_ex_employee_is_refused_even_when_ldap_is_switched_off(
        self, test_client, make_person,
    ):
        """No ``enable_ldap`` fixture here. Turning the directory off must not
        reopen the local door for accounts the directory owns — otherwise
        offboarding is undone by a config change nobody connects to it."""
        email = self._seed(make_person)
        _assert_generic_refusal(
            _local_login(test_client, email, PASSWORD),
            "ex-employee at the local door with LDAP off",
        )


# ══════════════════════════════════════════════════════════════════════════
# 4. The superuser — break-glass, so a directory outage is not a lockout
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
class TestSuperuser:

    def test_superuser_admin_signs_in_at_the_local_door(
        self, test_client, make_person, enable_ldap,
    ):
        email = make_person("superuser", is_superuser=True)

        with patch("app.ee.ldap.connection.LDAPConnectionManager") as CM:
            CM.return_value = _directory({})
            _assert_logged_in(
                _local_login(test_client, email, PASSWORD),
                "superuser at the local door",
            )

    def test_superuser_with_an_ldap_dn_still_gets_in_locally(
        self, test_client, make_person, enable_ldap,
    ):
        """The actual break-glass branch: a directory-managed account that the
        ``ldap_dn`` rule would otherwise refuse. This is the one exception, and
        it is why the rule is ``ldap_dn AND NOT is_superuser``. Without it, a
        directory outage locks the last admin out of their own instance."""
        email = make_person("superuser-dn", ldap_dn=ALICE_DN, is_superuser=True)

        with patch("app.ee.ldap.connection.LDAPConnectionManager") as CM:
            CM.return_value = _directory({})  # directory down / entry gone
            _assert_logged_in(
                _local_login(test_client, email, PASSWORD),
                "superuser with ldap_dn at the local door",
            )

    def test_superuser_is_refused_at_the_ldap_door(
        self, test_client, make_person, enable_ldap,
    ):
        """Break-glass is a property of the LOCAL door only. The directory door
        has one question — did this bind succeed — and being a superuser is not
        an answer to it."""
        email = make_person("superuser-ldap-door", is_superuser=True)

        with patch("app.ee.ldap.connection.LDAPConnectionManager") as CM:
            CM.return_value = _directory({})
            _assert_generic_refusal(
                _ldap_login(test_client, email, PASSWORD),
                "superuser at the LDAP door",
            )


# ══════════════════════════════════════════════════════════════════════════
# 5. ★The enumeration oracle — a security property, not a cosmetic one
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
class TestNoEnumerationOracle:

    def test_ldap_door_answers_identically_for_absent_and_for_wrong_password(
        self, test_client, make_person, enable_ldap,
    ):
        """Submit a list of addresses; keep the ones that answer differently, and
        you have read the customer's staff roster out of an unauthenticated
        endpoint without holding a single valid credential. So the two answers
        must be indistinguishable — same status, same body — not merely "both
        are failures".

        ★The two accounts are identical in every respect the app can see — both
        real, both members, both carrying an ``ldap_dn``. The ONLY difference is
        whether the directory still holds them, which is precisely the fact that
        must not be readable from outside."""
        known = make_person("known-to-directory", ldap_dn=ALICE_DN)
        absent = make_person("absent-from-directory", ldap_dn=EX_DN)

        entries = {known.lower(): {"dn": ALICE_DN, "name": "Alice Smith",
                                   "password": DIRECTORY_PASSWORD}}

        with patch("app.ee.ldap.connection.LDAPConnectionManager") as CM:
            manager = _directory(entries)
            CM.return_value = manager
            wrong_password = _ldap_login(test_client, known, "definitely-wrong")
            not_in_directory = _ldap_login(test_client, absent, "definitely-wrong")

        # ★★★Proof the two 400s came from the two DIFFERENT internal branches.
        # `_do_authenticate_ldap` resolves the login config first and refuses
        # before it ever builds a connection when LDAP is not enabled — so a
        # test whose directory never came on gets a matching pair of 400s and
        # asserts nothing at all. The equality below is only meaningful if the
        # directory was genuinely asked about both addresses, and answered
        # differently.
        looked_up = [c.args[0].lower() for c in manager.find_user.call_args_list]
        assert known.lower() in looked_up and absent.lower() in looked_up, (
            f"the directory was not consulted for both addresses: {looked_up}"
        )
        assert manager.bind_user.called, (
            "no bind was attempted, so the 'wrong password' branch was never reached"
        )

        _assert_generic_refusal(wrong_password, "known address, wrong password")
        _assert_generic_refusal(not_in_directory, "address not in the directory")
        assert wrong_password.status_code == not_in_directory.status_code
        assert wrong_password.json() == not_in_directory.json(), (
            "the LDAP door distinguishes 'not in the directory' from 'wrong "
            f"password' — that is an enumeration oracle against the customer's "
            f"Active Directory.\n  wrong password: {wrong_password.text}\n"
            f"  not in directory: {not_in_directory.text}"
        )

    def test_a_correct_directory_password_with_no_local_account_says_nothing_either(
        self, test_client, make_person, enable_ldap,
    ):
        """★The sharpest version of the oracle: the caller supplies a password
        the directory ACCEPTS, and must still learn nothing.

        With ``auto_provision_users`` off, a real directory member who has no
        local account yet is refused. That refusal has to look exactly like a
        wrong password — otherwise a successful bind against a directory the
        instance does not provision from becomes a way to confirm, one address
        at a time, who really is in the customer's AD. The bind succeeding is
        the whole point: this branch is reached only by someone holding a valid
        credential, and it still must not answer.
        """
        enable_ldap.auto_provision_users = False

        stranger_in_directory = _email("in-directory-no-account")
        entries = {
            stranger_in_directory.lower(): {
                "dn": ALICE_DN, "name": "Alice Smith", "password": DIRECTORY_PASSWORD,
            },
        }
        member = make_person("member-for-comparison", ldap_dn=EX_DN)

        with patch("app.ee.ldap.connection.LDAPConnectionManager") as CM:
            manager = _directory(entries)
            CM.return_value = manager
            not_provisioned = _ldap_login(
                test_client, stranger_in_directory, DIRECTORY_PASSWORD,
            )
            wrong_password = _ldap_login(test_client, member, "definitely-wrong")

        assert manager.bind_user.called, "the bind branch was never reached"
        _assert_generic_refusal(not_provisioned, "correct directory password, not provisioned")
        _assert_generic_refusal(wrong_password, "known member, wrong password")
        assert not_provisioned.status_code == wrong_password.status_code
        assert not_provisioned.json() == wrong_password.json(), (
            "a correct directory password for an unprovisioned account is "
            "answered differently from a wrong one — that confirms directory "
            f"membership to an unauthenticated caller.\n"
            f"  not provisioned: {not_provisioned.text}\n"
            f"  wrong password:  {wrong_password.text}"
        )

    def test_an_address_with_no_account_at_all_answers_the_same_way(
        self, test_client, make_person, enable_ldap,
    ):
        """The third variant of the same oracle: a complete stranger must be
        answered exactly as a real member with a bad password is."""
        member = make_person("real-member")

        with patch("app.ee.ldap.connection.LDAPConnectionManager") as CM:
            CM.return_value = _directory({})
            stranger = _local_login(test_client, _email("nobody"), "whatever")
            bad_password = _local_login(test_client, member, "whatever")

        _assert_generic_refusal(stranger, "no such account, local door")
        _assert_generic_refusal(bad_password, "real member, wrong password")
        assert stranger.status_code == bad_password.status_code
        assert stranger.json() == bad_password.json(), (
            "the local door distinguishes 'no such account' from 'wrong "
            f"password'.\n  stranger: {stranger.text}\n  member: {bad_password.text}"
        )


# ══════════════════════════════════════════════════════════════════════════
# 6. What the unauthenticated login page is told
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.e2e
class TestPublicSettingsExposeOnlyTheFlag:

    def test_settings_reports_that_ldap_is_enabled(self, test_client, enable_ldap):
        """The sign-in page needs exactly one bit — whether to offer the second
        door. It gets that bit and nothing else."""
        response = test_client.get("/api/settings")
        assert response.status_code == 200, response.text
        body = response.json()
        assert "ldap" in body, f"/api/settings does not expose the ldap flag: {sorted(body)}"
        assert body["ldap"] == {"enabled": True}, (
            f"expected exactly {{'enabled': True}}, got {body['ldap']}"
        )

    def test_settings_reports_ldap_disabled_when_it_is_off(self, test_client):
        from app.settings.config import settings

        saved = settings.dash_config.ldap.enabled
        settings.dash_config.ldap.enabled = False
        try:
            response = test_client.get("/api/settings")
            assert response.status_code == 200, response.text
            body = response.json()
            assert "ldap" in body, f"/api/settings does not expose the ldap flag: {sorted(body)}"
            assert body["ldap"] == {"enabled": False}, body["ldap"]
        finally:
            settings.dash_config.ldap.enabled = saved

    def test_settings_leaks_no_directory_details(self, test_client, enable_ldap):
        """★This endpoint is UNAUTHENTICATED. The server URL, the base DN and the
        service-account DN are internal topology, and the bind password is a
        secret; none of them are needed to draw a second button."""
        response = test_client.get("/api/settings")
        assert response.status_code == 200, response.text
        blob = response.text
        for secret in ("ldaps://mock-ldap.test", "dc=test,dc=com",
                       "cn=admin,dc=test,dc=com", "admin_pass"):
            assert secret not in blob, f"/api/settings leaks {secret!r}: {blob}"
        for key in ("url", "base_dn", "bind_dn", "bind_password", "bind_password_enc",
                    "user_filter", "auto_provision_users"):
            assert key not in response.json().get("ldap", {}), (
                f"/api/settings exposes ldap.{key}; the login page needs only `enabled`"
            )
