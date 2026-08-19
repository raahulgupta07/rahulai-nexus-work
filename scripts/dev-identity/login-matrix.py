"""Every way into this product, against real servers, in one run.

WHY THIS EXISTS
---------------
Three doors — local password, directory, single sign-on — and the interesting
behaviour is at their seams: one person arriving through two of them must end up
as ONE account, and an identity that cannot prove its address must not attach to
an account that already exists.

Unit tests cover the rules with fabricated inputs. This drives real OpenLDAP and
real Keycloak, so a claim the product mis-reads, or a provider that phrases
something unexpectedly, shows up here and not on a customer's install.

★It is DESTRUCTIVE on the database it runs against: it creates accounts and
links identities. Run it against a development install, never production.

    docker compose -f scripts/dev-identity/docker-compose.identity.yaml up -d
    ./scripts/dev-identity/setup-keycloak.sh
    docker cp scripts/dev-identity/login-matrix.py dash-app:/app/backend/
    docker exec -w /app/backend dash-app python login-matrix.py
"""
import asyncio, base64, json, urllib.error, urllib.parse, urllib.request

# ★`import main` first: it registers the ORM class registry, without which the
# first relationship resolution raises InvalidRequestError deep in SQLAlchemy.
import main  # noqa: F401

KC = "http://test-keycloak:8080/realms/citytest/protocol/openid-connect"
APP = "http://localhost:3000"
KC_PW = "KcPass123"

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  — " + detail) if detail else ""))


# --------------------------------------------------------------- helpers

def id_claims(username):
    """A REAL id_token from Keycloak, decoded."""
    data = urllib.parse.urlencode({
        "grant_type": "password", "client_id": "dash-insights",
        "client_secret": "dash-test-secret", "username": username,
        "password": KC_PW, "scope": "openid email profile"}).encode()
    r = json.load(urllib.request.urlopen(urllib.request.Request(KC + "/token", data=data)))
    p = r["id_token"].split(".")[1]; p += "=" * (-len(p) % 4)
    return json.loads(base64.urlsafe_b64decode(p)), r["access_token"]


def post_form(path, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(APP + path, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        return urllib.request.urlopen(req).status, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


async def sso_login(username, provider="keycloak", as_email=None):
    """The real callback path: real claims -> the real decision -> the real gate.

    Returns (user_or_None, refusal_detail_or_None).
    """
    from fastapi import HTTPException
    from app.core.auth import get_user_manager, get_user_db
    from app.dependencies import async_session_maker
    from app.services.auth_providers import _email_and_verification_from_claims

    claims, access_token = id_claims(username)
    email, verified, reason = _email_and_verification_from_claims(claims, provider, object())
    if as_email:
        # ★Reuse a real unverified token but aim it at a different local row, so
        # the DIRECTORY reason can be tested without a second Keycloak user.
        email = as_email

    async with async_session_maker() as session:
        from app.models.user import User
        from app.models.oauth_account import OAuthAccount
        from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
        user_db = SQLAlchemyUserDatabase(session, User, OAuthAccount)
        # ★One iteration only. An earlier version put `break` in a `finally`,
        # which DISCARDS the return value of the `try` — every SSO case came
        # back None and read as the callback failing.
        async for um in get_user_manager(user_db):
            try:
                user = await um.oauth_callback(
                    oauth_name=provider, access_token=access_token,
                    account_id=claims["sub"], account_email=str(email),
                    expires_at=None, refresh_token=None, request=None,
                    account_email_verified=verified,
                    account_email_verified_reason=reason,
                )
                return user, None
            except HTTPException as e:
                d = e.detail
                return None, (d.get("message") if isinstance(d, dict) else str(d))
    return None, "user manager never yielded"


async def db_state(email):
    """What the database holds for an address, case-insensitively."""
    from sqlalchemy import func, select
    from app.dependencies import async_session_maker
    from app.models.user import User
    from app.models.oauth_account import OAuthAccount
    async with async_session_maker() as s:
        rows = (await s.execute(select(User).where(func.lower(User.email) == email.lower()))).scalars().all()
        if not rows:
            return {"rows": 0}
        u = rows[0]
        n = len((await s.execute(select(OAuthAccount).where(OAuthAccount.user_id == u.id))).scalars().all())
        return {"rows": len(rows), "email": u.email, "ldap": bool(u.ldap_dn),
                "oauth": n, "verified": bool(u.is_verified), "id": u.id}


async def plant_local(email, verified=True, ldap_dn=None):
    """Create a local account for `email` if there isn't one.

    ★★★The linking gate only fires when an account ALREADY EXISTS — a brand new
    address is simply auto-provisioned, verified or not, because there is
    nothing to take over. The first version of these tests attacked an address
    nobody held and read the auto-provision as the gate failing. The refusal
    cases below therefore plant the account first, which is exactly how the real
    ones arise: the person was already here, out of the directory.

    ★★★It also REMOVES any identity already linked to that account. Once an
    identity is linked, a later sign-in is looked up by `(provider, account_id)`
    and returns the user directly — the gate is never consulted, correctly,
    because there is nothing left to decide. A re-run of this file therefore
    reported every refusal case as LINKED, which reads exactly like the gate
    having been removed. A fixture that does not reset its own state measures
    the previous run.
    """
    from fastapi_users.password import PasswordHelper
    from sqlalchemy import delete, func, select
    from app.dependencies import async_session_maker
    from app.models.user import User
    from app.models.oauth_account import OAuthAccount
    async with async_session_maker() as s:
        row = (await s.execute(select(User).where(func.lower(User.email) == email.lower()))).scalars().first()
        if row is None:
            row = User(email=email, name=email.split("@")[0],
                       hashed_password=PasswordHelper().hash("Planted123!"),
                       is_active=True, is_verified=verified, is_superuser=False,
                       ldap_dn=ldap_dn)
            s.add(row)
            await s.flush()
        else:
            row.is_verified = verified
            row.ldap_dn = ldap_dn
        await s.execute(delete(OAuthAccount).where(OAuthAccount.user_id == row.id))
        await s.commit()


async def unlink_identity(username):
    """Remove any link carrying THIS Keycloak identity, whoever owns it.

    ★★★`plant_local` clears links by USER, and that is not enough. A refusal
    case must reach the gate, and the very first thing `oauth_callback` does is
    look the identity up by `(provider, account_id)` — if any row anywhere
    carries that sub, it returns that user and the gate is never consulted.

    Measured: `S7` signs in with the `unverified` user's sub while aiming it at
    a different local row, so the next run found that row and reported the
    refusal case as LINKED — which reads exactly like the security check having
    been removed. Second time this suite has measured its own previous run; the
    first was through the user key, this one through the identity key.
    """
    from sqlalchemy import delete
    from app.dependencies import async_session_maker
    from app.models.oauth_account import OAuthAccount
    claims, _ = id_claims(username)
    async with async_session_maker() as s:
        await s.execute(delete(OAuthAccount).where(OAuthAccount.account_id == claims["sub"]))
        await s.commit()


async def identities(email):
    from app.core.auth_origin import resolve_auth_origin, resolve_auth_origins
    from app.services.identity_view import merge_identities, has_local_password
    from sqlalchemy import func, select
    from sqlalchemy.orm import selectinload
    from app.dependencies import async_session_maker
    from app.models.user import User
    async with async_session_maker() as s:
        u = (await s.execute(select(User).options(selectinload(User.oauth_accounts))
             .where(func.lower(User.email) == email.lower()))).scalars().first()
        if not u:
            return None
        accts = list(u.oauth_accounts or [])
        return {
            "providers": [i.provider for i in merge_identities(u, accts)],
            "has_password": has_local_password(u, accts),
            "origin": resolve_auth_origin(u, oauth_accounts=accts),
            "origins": resolve_auth_origins(u, oauth_accounts=accts),
        }


# --------------------------------------------------------------- the matrix

async def main():
    print("\nDIRECTORY DOOR")
    code, _ = post_form("/api/auth/ldap/login", {"username": "ldaptest", "password": "LdapPass123"})
    check("D1 sign in by username", code == 200, f"HTTP {code}")
    code, _ = post_form("/api/auth/ldap/login", {"username": "ldaptest@cityagent.io", "password": "LdapPass123"})
    check("D2 sign in by email", code == 200, f"HTTP {code}")

    c1, b1 = post_form("/api/auth/ldap/login", {"username": "ldaptest", "password": "WrongPassword1"})
    c2, b2 = post_form("/api/auth/ldap/login", {"username": "nobody-here", "password": "WrongPassword1"})
    check("D3 wrong password and unknown user are indistinguishable",
          (c1, b1) == (c2, b2), f"{c1}/{c2}")

    st = await db_state("ldaptest@cityagent.io")
    check("D4 the directory account exists and is marked as directory",
          st.get("rows") == 1 and st.get("ldap"), str(st))

    code, _ = post_form("/api/auth/jwt/login", {"username": "ldaptest@cityagent.io", "password": "LdapPass123"})
    check("D5 a directory account is refused at the LOCAL door", code != 200, f"HTTP {code}")

    print("\nLOCAL DOOR (must keep working while the directory is enabled)")
    code, _ = post_form("/api/auth/jwt/login", {"username": "localmatrix@cityagent.io", "password": "LocalPass123!"})
    check("L1 a local member can still sign in", code == 200, f"HTTP {code}")
    code, _ = post_form("/api/auth/jwt/login", {"username": "localmatrix@cityagent.io", "password": "definitely-wrong"})
    check("L2 a wrong local password is refused", code != 200, f"HTTP {code}")

    print("\nSINGLE SIGN-ON")
    user, refusal = await sso_login("verified")
    check("S1 a new verified identity provisions an account", user is not None, refusal or "")

    user, refusal = await sso_login("verified")
    st = await db_state("verified@cityagent.io")
    check("S6 signing in again reuses the same account, no duplicate",
          user is not None and st["rows"] == 1 and st["oauth"] == 1, str(st))

    print("\n  a closed installation — nobody can sign themselves up")
    print("  (allow_uninvited_signups = False, as both live installs are)")

    from app.settings.config import settings as _settings
    _features = _settings.dash_config.features
    _restore = _features.allow_uninvited_signups
    _features.allow_uninvited_signups = False

    # ★The account exists FIRST, which is what makes this a link rather than a
    # new sign-up — the production shape: provisioned by the directory, then
    # arriving through single sign-on.
    await plant_local("unverified@cityagent.io", verified=True)
    await unlink_identity("unverified")
    user, refusal = await sso_login("unverified")
    check("S3 provider says unverified -> LINKED anyway", user is not None,
          refusal or "linked")

    await plant_local("upnonly@cityagent.io", verified=True)
    await unlink_identity("upnonly@cityagent.io")
    user, refusal = await sso_login("upnonly@cityagent.io")
    check("S4 address from a username -> LINKED anyway", user is not None,
          refusal or "linked")

    # ★The half that did NOT move: our own row is unverified, so the gate still
    # refuses. This change is about the PROVIDER's half of the proof.
    await plant_local("localunver@cityagent.io", verified=False)
    await unlink_identity("localunver")
    user, refusal = await sso_login("localunver")
    check("S5 our own row unverified -> still refused", user is None, refusal or "LINKED")
    check("S5 message names the LOCAL account",
          bool(refusal) and "verify the existing account" in refusal, refusal or "")

    print("\n  an OPEN installation — anyone may register an address")
    print("  (the squatter is real again, and the refusal must return)")
    _features.allow_uninvited_signups = True

    await plant_local("unverified@cityagent.io", verified=True)
    await unlink_identity("unverified")
    user, refusal = await sso_login("unverified")
    check("S3o provider says unverified -> refused", user is None, refusal or "LINKED")
    check("S3o message names the identity provider",
          bool(refusal) and "identity provider reports" in refusal, refusal or "")

    await plant_local("upnonly@cityagent.io", verified=True)
    await unlink_identity("upnonly@cityagent.io")
    user, refusal = await sso_login("upnonly@cityagent.io")
    check("S4o address from a username -> refused", user is None, refusal or "LINKED")
    check("S4o message names the email attribute",
          bool(refusal) and "username rather than" in refusal, refusal or "")

    # ★A directory account is safe under EITHER policy — it cannot be
    # self-registered, so the two reasons are independent.
    await plant_local("dirtest@cityagent.io", verified=True, ldap_dn="uid=dirtest,ou=people,dc=cityagent,dc=io")
    await unlink_identity("unverified")
    user, refusal = await sso_login("unverified", as_email="dirtest@cityagent.io")
    check("S7 a directory account links even on an open install", user is not None,
          refusal or "linked")

    _features.allow_uninvited_signups = _restore
    print(f"  (policy restored to {_restore})")

    print("\nMERGE — one person, one row")
    code, _ = post_form("/api/auth/ldap/login", {"username": "bothdoors", "password": "LdapPass123"})
    before = await db_state("bothdoors@cityagent.io")
    user, refusal = await sso_login("bothdoors")
    after = await db_state("bothdoors@cityagent.io")
    check("M1 directory first, then SSO -> ONE account with both",
          code == 200 and after["rows"] == 1 and after["ldap"] and after["oauth"] == 1,
          f"before={before} after={after}" + (f" refusal={refusal}" if refusal else ""))

    user, refusal = await sso_login("ssofirst")
    mid = await db_state("ssofirst@cityagent.io")
    code, _ = post_form("/api/auth/ldap/login", {"username": "ssofirst", "password": "LdapPass123"})
    after = await db_state("ssofirst@cityagent.io")
    check("M2 SSO first, then directory -> ONE account, directory recorded",
          after["rows"] == 1 and after["oauth"] == 1 and after["ldap"],
          f"after_sso={mid} after_ldap={after}")

    print("\nWHAT THE SCREENS SHOW")
    for email, expect_origins, expect_pw in [
        ("bothdoors@cityagent.io", ["ldap", "sso"], False),
        ("verified@cityagent.io", ["sso"], False),
        ("ldaptest@cityagent.io", ["ldap"], False),
        ("localmatrix@cityagent.io", ["local"], True),
    ]:
        got = await identities(email)
        if got is None:
            check(f"V {email}", False, "no such account")
            continue
        check(f"V {email:26s} sign-in column = {expect_origins}",
              got["origins"] == expect_origins, str(got))
        check(f"V {email:26s} password owned here = {expect_pw}",
              got["has_password"] is expect_pw, str(got))

    print("\n" + "=" * 62)
    bad = [r for r in results if not r[1]]
    print(f"{len(results) - len(bad)} passed, {len(bad)} failed, of {len(results)}")
    for n, _, d in bad:
        print("  FAILED:", n, "—", d)

asyncio.run(main())
