"""A brand-new employee's whole first week, both orders, nothing pre-seeded.

WHY SEPARATE FROM login-matrix.py
---------------------------------
The matrix proves each door and the MOMENT of the merge. It does not prove what
happens to the OTHER door afterwards, and that is the question a person actually
asks: "I signed in with my directory password, then with single sign-on — can I
still use my password?"

★★★That is not obvious from the code. Linking writes an `oauth_accounts` row and
leaves `ldap_dn` alone, but the sign-in ROUTER reads the recorded origin
(`core/auth.py _do_authenticate`), and a person who now has two identities is
exactly the case where a precedence rule could send them to the wrong door. A
merge that silently costs somebody their directory password would look like a
success in every test that stops at the link.

So each journey below signs in AGAIN through the first door after the second one
has attached, and again through the second. Four sign-ins, one account.

★Both fresh users are created in Keycloak with `emailVerified = false`, which is
what the real installation's provider sends. If that ever starts passing for the
wrong reason — because somebody set the flag rather than because the rule
works — these stop testing what they claim to.

    docker cp scripts/dev-identity/new-user-journey.py dash-app:/app/backend/
    docker exec -w /app/backend dash-app python new-user-journey.py

★DESTRUCTIVE on the database it runs against: it deletes and recreates its two
accounts so a re-run measures this run. Development installs only.
"""
import asyncio, base64, json, urllib.error, urllib.parse, urllib.request

import main  # noqa: F401  — registers the ORM registry

KC = "http://test-keycloak:8080/realms/citytest/protocol/openid-connect"
APP = "http://localhost:3000"

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"  — {detail}" if detail else ""))


def ldap_login(username, password="LdapPass123"):
    data = urllib.parse.urlencode({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        APP + "/api/auth/ldap/login", data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        return urllib.request.urlopen(req).status
    except urllib.error.HTTPError as e:
        return e.code


def id_claims(username):
    data = urllib.parse.urlencode({
        "grant_type": "password", "client_id": "dash-insights",
        "client_secret": "dash-test-secret", "username": username,
        "password": "KcPass123", "scope": "openid email profile"}).encode()
    r = json.load(urllib.request.urlopen(urllib.request.Request(KC + "/token", data=data)))
    p = r["id_token"].split(".")[1]
    p += "=" * (-len(p) % 4)
    return json.loads(base64.urlsafe_b64decode(p)), r["access_token"]


async def sso_login(username):
    """The real callback: real claims -> the real decision -> the real gate."""
    from fastapi import HTTPException
    from app.core.auth import get_user_manager
    from app.dependencies import async_session_maker
    from app.models.oauth_account import OAuthAccount
    from app.models.user import User
    from app.services.auth_providers import _email_and_verification_from_claims
    from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

    claims, token = id_claims(username)
    email, verified, reason = _email_and_verification_from_claims(claims, "keycloak", object())

    async with async_session_maker() as session:
        user_db = SQLAlchemyUserDatabase(session, User, OAuthAccount)
        async for um in get_user_manager(user_db):
            try:
                user = await um.oauth_callback(
                    oauth_name="keycloak", access_token=token,
                    account_id=claims["sub"], account_email=str(email),
                    expires_at=None, refresh_token=None, request=None,
                    account_email_verified=verified,
                    account_email_verified_reason=reason,
                )
                return user, None, verified
            except HTTPException as e:
                d = e.detail
                return None, (d.get("message") if isinstance(d, dict) else str(d)), verified
    return None, "no user manager", verified


async def state(email):
    from sqlalchemy import func, select
    from app.dependencies import async_session_maker
    from app.models.oauth_account import OAuthAccount
    from app.models.user import User
    async with async_session_maker() as s:
        rows = (await s.execute(
            select(User).where(func.lower(User.email) == email.lower()))).scalars().all()
        if not rows:
            return {"rows": 0}
        u = rows[0]
        n = len((await s.execute(
            select(OAuthAccount).where(OAuthAccount.user_id == u.id))).scalars().all())
        return {"rows": len(rows), "id": str(u.id), "ldap": bool(u.ldap_dn), "oauth": n}


async def wipe(email):
    """Remove the account entirely, so the journey starts from nothing.

    ★A fixture that does not reset its own state measures the previous run —
    twice already in this suite, through the user key and the identity key.
    """
    from sqlalchemy import delete, func, select
    from app.dependencies import async_session_maker
    from app.models.membership import Membership
    from app.models.oauth_account import OAuthAccount
    from app.models.user import User
    async with async_session_maker() as s:
        u = (await s.execute(
            select(User).where(func.lower(User.email) == email.lower()))).scalars().first()
        if u:
            await s.execute(delete(OAuthAccount).where(OAuthAccount.user_id == u.id))
            await s.execute(delete(Membership).where(Membership.user_id == u.id))
            await s.execute(delete(User).where(User.id == u.id))
            await s.commit()


async def journey(username, email, first):
    """One person's first week. `first` is the door they arrive through."""
    print(f"\n{'=' * 66}\n{username} — {first.upper()} first, then the other\n{'=' * 66}")
    await wipe(email)
    check(f"{username}: starts with no account", (await state(email))["rows"] == 0)

    if first == "ldap":
        code = ldap_login(username)
        st = await state(email)
        check(f"{username}: 1. directory sign-in creates the account",
              code == 200 and st["rows"] == 1 and st["ldap"] and st["oauth"] == 0,
              f"HTTP {code} {st}")

        code = ldap_login(username)
        check(f"{username}: 2. directory sign-in again still works", code == 200, f"HTTP {code}")

        user, refusal, verified = await sso_login(username)
        st = await state(email)
        check(f"{username}: 3. single sign-on JOINS the same account "
              f"(provider said verified={verified})",
              user is not None and st["rows"] == 1 and st["oauth"] == 1 and st["ldap"],
              refusal or str(st))
    else:
        user, refusal, verified = await sso_login(username)
        st = await state(email)
        check(f"{username}: 1. single sign-on creates the account "
              f"(provider said verified={verified})",
              user is not None and st["rows"] == 1 and st["oauth"] == 1, refusal or str(st))

        user, refusal, _ = await sso_login(username)
        check(f"{username}: 2. single sign-on again still works", user is not None, refusal or "")

        code = ldap_login(username)
        st = await state(email)
        check(f"{username}: 3. directory sign-in JOINS the same account",
              code == 200 and st["rows"] == 1 and st["oauth"] == 1 and st["ldap"],
              f"HTTP {code} {st}")

    before = await state(email)

    # ★★★THE POINT OF THIS FILE. Both doors, after the merge, for the same person.
    code = ldap_login(username)
    check(f"{username}: 4. directory password STILL works after the merge",
          code == 200, f"HTTP {code}")

    user, refusal, _ = await sso_login(username)
    check(f"{username}: 5. single sign-on STILL works after the merge",
          user is not None, refusal or "")

    after = await state(email)
    check(f"{username}: 6. still ONE account, one identity, same id",
          after["rows"] == 1 and after["oauth"] == 1 and after["id"] == before["id"],
          f"{before} -> {after}")

    from app.core.auth_origin import resolve_auth_origins
    from app.services.identity_view import merge_identities, has_local_password
    from sqlalchemy import func, select
    from sqlalchemy.orm import selectinload
    from app.dependencies import async_session_maker
    from app.models.user import User
    async with async_session_maker() as s:
        u = (await s.execute(select(User).options(selectinload(User.oauth_accounts))
             .where(func.lower(User.email) == email.lower()))).scalars().first()
        accts = list(u.oauth_accounts or [])
        origins = resolve_auth_origins(u, oauth_accounts=accts)
        providers = [i.provider for i in merge_identities(u, accts)]
        pw = has_local_password(u, accts)
    check(f"{username}: 7. the roster shows both ways in",
          origins == ["ldap", "sso"], str(origins))
    check(f"{username}: 8. identities are the directory and the provider",
          set(providers) == {"ldap", "keycloak"} and pw is False,
          f"{providers} has_password={pw}")


async def main_():
    await journey("freshone", "freshone@cityagent.io", first="ldap")
    await journey("freshtwo", "freshtwo@cityagent.io", first="sso")

    print("\n" + "=" * 66)
    bad = [n for n, ok in results if not ok]
    print(f"{len(results) - len(bad)} passed, {len(bad)} failed, of {len(results)}")
    for n in bad:
        print("  FAILED:", n)

asyncio.run(main_())
