import os
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Any, List, Callable

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update

import httpx

from fastapi import Depends, Request, HTTPException, status
from fastapi.security import APIKeyHeader
from fastapi_users import BaseUserManager, FastAPIUsers
from fastapi_users import exceptions
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy import select, and_, func
from httpx_oauth.oauth2 import BaseOAuth2

from fastapi_mail import FastMail, MessageSchema, ConnectionConfig

from app.schemas.user_schema import UserCreate
from app.models.organization import Organization
from app.models.membership import Membership

from app.models.user import User
from app.dependencies import get_user_db, get_async_db, resolve_organization
from app.models.oauth_account import OAuthAccount
from fastapi.responses import RedirectResponse

from app.settings.config import settings
from app.services.organization_service import OrganizationService
from app.schemas.organization_schema import OrganizationCreate
from app.core.telemetry import telemetry

SECRET = settings.dash_config.encryption_key


DEFAULT_ORG_NAME = "Main Org"
DEFAULT_ORG_DESCRIPTION = ""


def _brand_name() -> str:
    """Runtime product name for the transactional emails below.

    Imported lazily: this module is loaded very early in application startup
    and must not pull the service layer in with it.
    """
    from app.services.branding_service import product_name

    return product_name()

# ★Off by default, which is exactly today's behaviour: an unverified account
# currently signs in with no complaint (measured — `requires_verification`
# appears nowhere in this codebase). Turning it on is a policy decision that
# locks out every already-unverified account at the next login, so it is opt-in
# rather than something a deploy switches on for you.
REQUIRE_EMAIL_VERIFICATION = (
    os.environ.get("DASH_REQUIRE_EMAIL_VERIFICATION", "").strip().lower()
    in ("1", "true", "yes", "on")
)


class UserManager(BaseUserManager[User, str]):
    reset_password_token_secret = SECRET
    verification_token_secret = SECRET

    def parse_id(self, value: Any) -> str:
        return str(value)

    async def authenticate(self, credentials) -> Optional[User]:
        """The LOCAL door: an address and a password this product itself stores.

        ★★★This door makes NO LDAP call at all, and that is a change.

        It used to attempt an LDAP bind FIRST and fall back to the local
        password only for superusers. The cost was measured against the running
        instance: with LDAP enabled and reachable, a member an admin had created
        *here* — an ordinary local account, correct password, never in the
        directory — was refused 400, while the same credentials signed in fine
        the moment LDAP was switched off or the server went down. The reason is
        in `_ldap_authenticate`: "this address is not in the directory" returns
        the same ``"failed"`` as "the directory rejected this password", and the
        caller granted local fallback only to superusers. So every ordinary
        local member was locked out by a *working* directory. A product whose
        local logins work only while LDAP is BROKEN is the wrong way round.

        The two doors are now separate and never call each other — the Open
        WebUI / Grafana model. This one checks local passwords;
        `authenticate_directory` (POST /api/auth/ldap/login) binds against the
        directory. Which door an account may use is decided by what is already
        RECORDED on its row, ``User.ldap_dn``, never by a live query. See
        `_do_authenticate`.

        In ``auth.mode = sso_only``, this form remains a break-glass: SSO is
        the source of truth, so we only let admins (or superusers) sign in
        with password to avoid handing regular users a parallel login surface.
        """
        user = await self._do_authenticate(credentials)
        return await self._finish_authentication(user, credentials, local_form=True)

    async def authenticate_directory(self, credentials) -> Optional[User]:
        """The DIRECTORY door: bind against LDAP, and nothing else.

        Never consults a local password, in either direction. A purely local
        account cannot be reached through this door even by someone typing its
        real password — the mirror image of `authenticate`'s refusal to touch
        the directory. Two doors, one question each.

        Returns a user, or None for every way of failing; the route turns all of
        them into one LOGIN_BAD_CREDENTIALS.
        """
        user = await self._do_authenticate_ldap(credentials)
        return await self._finish_authentication(user, credentials, local_form=False)

    async def _finish_authentication(
        self, user: Optional[User], credentials, local_form: bool
    ) -> Optional[User]:
        """The checks both doors share, once a door has produced a user or not.

        Extracted when the directory door was split out. Two copies of the
        enumeration rule below is how one of them eventually grows a helpful
        error message.
        """
        # ★★★Everything reaching this line is indistinguishable ON PURPOSE.
        # A wrong address and a wrong password both return None, and the router
        # turns that into one LOGIN_BAD_CREDENTIALS. Splitting them would make
        # this form an account-existence oracle: submit a list of addresses,
        # keep the ones that answer "wrong password", and you have a verified
        # roster of who works here — harvested without a single valid
        # credential. That is username enumeration, and no amount of "but the
        # error would be more helpful" is worth it. The true reason is written
        # to the audit log instead, where the reader is already authenticated.
        if user is None:
            await self._record_login_failure(credentials, "invalid_credentials")
            return None

        # ★Below this line the password has ALREADY been verified. Naming the
        # account's state now tells the caller nothing they could not confirm
        # themselves — they hold the credential. That asymmetry is the whole
        # design: silent before the password checks out, specific after.
        if not user.is_active:
            await self._record_login_failure(credentials, "inactive", user=user)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="ACCOUNT_DEACTIVATED",
            )

        if REQUIRE_EMAIL_VERIFICATION and not user.is_verified:
            await self._record_login_failure(credentials, "unverified", user=user)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="EMAIL_NOT_VERIFIED",
            )

        # ★Only the LOCAL form is a break-glass in sso_only mode. Applying this
        # to the directory door would lock out every directory user on an
        # sso_only instance — a directory IS the single sign-on there, not a
        # parallel password surface, which is the thing this rule guards against.
        if local_form and settings.dash_config.auth.mode == "sso_only" and not await self._user_can_use_local_login(user):
            # Deliberately NOT specific: in sso_only the local form is a
            # break-glass for admins, and telling a regular user "you exist but
            # may not use this door" confirms the address.
            await self._record_login_failure(credentials, "local_login_not_permitted", user=user)
            return None
        return user

    async def _record_login_failure(self, credentials, reason: str, user=None) -> None:
        """Log why a sign-in really failed, for an audience that is allowed to know.

        The login response is deliberately vague; this is where the precision
        lives. Best-effort — a logging failure must never turn a bad password
        into a 500, and must never change what the caller is told.
        """
        try:
            email = getattr(credentials, "username", None) or ""
            logging.getLogger("app.auth.login").info(
                "login failed",
                extra={
                    "login_email": email,
                    "login_failure_reason": reason,
                    "user_id": str(getattr(user, "id", "")) or None,
                },
            )
        except Exception:  # noqa: BLE001
            pass

    async def _login_ldap_config(self):
        """The LDAP config this login should be checked against, and whose org.

        Returns ``(config, organization_id | None)``. ★The org id is not
        decoration: a directory that vouched for somebody is also the workspace
        they belong in, and it is the only door that can answer that question
        for itself. Single sign-on is instance-global and cannot.

        ★Reads the DATABASE first, then the file. It used to read only
        ``settings.dash_config.ldap``, so an LDAP directory configured through
        the settings UI drove the hourly group sync and was invisible to the
        login path — `enabled` was false, this whole branch was skipped, and no
        directory user could sign in with their own password.

        Swallowed: if the lookup fails, fall back to the file config so a
        database blip degrades to the previous behaviour rather than locking
        everybody out.
        """
        try:
            from app.dependencies import async_session_maker
            from app.services.organization_settings_service import (
                OrganizationSettingsService,
            )

            async with async_session_maker() as _db:
                return await OrganizationSettingsService().resolve_login_ldap_config(_db)
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "Could not resolve the LDAP login config, falling back to the file: %s", e
            )
            return settings.dash_config.ldap, None

    async def _do_authenticate(self, credentials) -> Optional[User]:
        """Local password only, routed on the account's RECORDED origin.

            no ldap_dn                  → ordinary local account → local password
            has ldap_dn                 → directory-managed      → refused here
            has ldap_dn + is_superuser  → break-glass            → local password

        ★★★No network call, deliberately. Asking the directory "is this person
        still in it?" would drag the dependency straight back in and undo the
        split: the local door would once again be unusable whenever LDAP is
        down or misconfigured, which is the exact failure this replaced.

        The recorded mark is enough, and it covers offboarding from BOTH sides
        with no query at all. Someone the directory has dropped still carries
        their ``ldap_dn``, so this door refuses them; the directory door also
        refuses them, because the bind no longer finds them there. Neither door
        opens, and nothing had to be asked over the network to know it.

        ★``ldap_dn`` is the only thing that records the fact. "Has a password"
        cannot classify an account — a directory-provisioned row is created with
        a random hash nobody holds (see `_ldap_authenticate`), so it *has* one.

        ★The superuser break-glass stays, and matters more now than before. An
        instance owner must never be locked out of their own product because a
        directory is unreachable, misconfigured, or has just disowned them.
        """
        try:
            local_user = await self.get_by_email(credentials.username)
        except exceptions.UserNotExists:
            local_user = None

        if (
            local_user is not None
            and (local_user.ldap_dn or "").strip()
            and not local_user.is_superuser
        ):
            # The precise reason goes to the audit log; the caller is told the
            # same nothing as a wrong password. See _finish_authentication.
            await self._record_login_failure(
                credentials, "directory_managed_account", user=local_user
            )
            return None

        # ★Delegated, not hand-rolled. The base implementation runs a dummy hash
        # when the address does not exist, so a missing account costs the same
        # time as a wrong password. Verifying inline here would quietly lose
        # that and turn this door into the timing oracle the comments above
        # spend so much effort refusing to be.
        return await super().authenticate(credentials)

    async def _do_authenticate_ldap(self, credentials) -> Optional[User]:
        """Bind these credentials against the directory; merge/provision on success.

        ★A directory that is not enabled refuses exactly like a wrong password.
        Answering "LDAP is not configured" would tell an unauthenticated caller
        how this instance is set up, and — once they know it IS on — turns the
        route into a probe against the customer's directory.

        ★"Unreachable" does NOT fall back to the local password. That fallback
        is precisely what welded the two doors together. A directory outage now
        means the directory door is down and the local door is untouched, which
        is a state an admin can actually reason about.
        """
        ldap_config, ldap_org_id = await self._login_ldap_config()
        if not ldap_config.enabled:
            await self._record_login_failure(credentials, "ldap_not_enabled")
            return None

        # ★Pass the SAME config that opened this branch. Without it the bind
        # would be attempted against the file config — typically a blank url —
        # so a DB-configured directory would gate correctly and then fail to
        # connect, which reads as "the server is down".
        ldap_result, directory_email = await self._ldap_authenticate(
            credentials.username, credentials.password, ldap_config, ldap_org_id
        )
        if ldap_result == "success":
            # _ldap_authenticate has already created or merged the local row.
            # ★★★Looked up by the DIRECTORY's address, not `credentials.username`.
            # That was the second half of the same bug the login attribute
            # closed: the bind succeeded, the row was created or merged under
            # the entry's `mail`, and then this line asked for an account named
            # `ldapuser`. UserNotExists → None → one generic
            # LOGIN_BAD_CREDENTIALS, so a correct username and a correct
            # password produced a refusal indistinguishable from a typo, with
            # the audit log showing only `invalid_credentials` and no LDAP
            # reason at all. Measured live 2026-08-08.
            try:
                return await self.get_by_email(directory_email)
            except exceptions.UserNotExists:
                return None

        # ★★★"not_in_directory", "failed" and "unreachable" are three different
        # facts and exactly ONE answer to the caller. Open WebUI replies "User
        # not found in the LDAP server" at this point; against a customer's
        # Active Directory that is a free membership oracle — feed it addresses,
        # keep the ones it recognises, and you have their staff list without a
        # single valid credential. The distinction is written to the audit log,
        # whose reader is already authenticated.
        await self._record_login_failure(credentials, f"ldap_{ldap_result}")
        return None

    async def _user_can_use_local_login(self, user: User) -> bool:
        """Allow local login in sso_only mode only for admins / superusers.

        "Admin" is decided by RBAC — the source of truth — not the legacy
        ``Membership.role`` string: a user gets break-glass password login iff
        they resolve to ``full_admin_access`` in any organization they belong to.
        (The resolver's transitional net still maps a legacy ``role='admin'``
        membership with no assignment to admin, so pre-backfill admins are not
        locked out; a user demoted in RBAC is correctly denied.)
        """
        if user.is_superuser:
            return True
        from app.dependencies import async_session_maker
        from app.core.permission_resolver import resolve_permissions, FULL_ADMIN
        async with async_session_maker() as session:
            org_rows = (await session.execute(
                select(Membership.organization_id).where(
                    Membership.user_id == str(user.id),
                    Membership.organization_id.isnot(None),
                    Membership.deleted_at.is_(None),
                )
            )).all()
            for (org_id,) in org_rows:
                resolved = await resolve_permissions(session, str(user.id), str(org_id))
                if FULL_ADMIN in resolved.org_permissions:
                    return True
            return False

    async def _ldap_authenticate(
        self, identifier: str, password: str, ldap_config=None, ldap_org_id=None
    ) -> str:
        """
        Try LDAP bind auth.

        ★★★``identifier`` is what the person TYPED — a username or an address,
        whichever their directory uses (see `LDAPConfig.user_login_attribute`).
        It is NOT an email and must never be used as one. The address this
        account is keyed on comes from the matched entry, below.

        ``ldap_config`` is the already-resolved config from the caller. It is
        optional only so existing callers and tests keep working; when omitted
        it resolves the same way `_do_authenticate` does — never straight from
        the file, which is the bug this argument exists to close.

        Returns ``(result, directory_email)``:
            "success" — LDAP bind succeeded and user is ready
            "not_in_directory" — reachable, but no entry for this identifier
            "failed"  — LDAP reachable but credentials rejected
            "unreachable" — could not connect to LDAP server
            "no_directory_email" — bound, but the entry carries no address

        ★★★The second element is the address read off the matched entry, and
        it is the ONLY thing the caller may key an account on. It is returned
        rather than left for the caller to re-derive because the caller used
        to re-derive it — from `credentials.username` — and that silently
        broke every username sign-in the moment usernames were accepted.

        ★★★These are for the AUDIT LOG only. Every non-success must reach the
        caller as one indistinguishable refusal — see the ★★★ note in
        `_do_authenticate_ldap`. "not_in_directory" was split out from "failed"
        because a log that cannot tell "nobody by that name" from "wrong
        password" is why the local door was wired to the directory in the first
        place: the single "failed" made an absent user look like a rejected one.
        """
        from app.ee.ldap.connection import LDAPConnectionManager
        import logging
        _logger = logging.getLogger(__name__)

        if ldap_config is None:
            ldap_config, ldap_org_id = await self._login_ldap_config()
        manager = LDAPConnectionManager(ldap_config)

        try:
            # ★Fetches the display name in the SAME search as the DN. The old
            # call asked only for the email attribute, so provisioning had
            # nothing to name the account with — see the `name` below.
            found = manager.find_user(identifier)
        except Exception as e:
            _logger.warning(f"LDAP server unreachable during user search: {e}")
            return "unreachable", None

        if not found:
            return "not_in_directory", None
        user_dn = found["dn"]
        directory_name = found.get("name")

        try:
            if not manager.bind_user(user_dn, password):
                return "failed", None
        except Exception as e:
            _logger.warning(f"LDAP server unreachable during bind: {e}")
            return "unreachable", None

        # ★★★FROM HERE DOWN, `email` IS THE DIRECTORY'S VALUE — never
        # `identifier`. Once this door accepts a username the typed string is
        # not an address at all, and every line below keys an account on it:
        # the merge lookup, the create, the invite match. Reading the address
        # off the matched entry is also what keeps the merge gate's argument
        # true — see the ★THE ASYMMETRY note below, which rests entirely on
        # the address being admin-maintained rather than caller-supplied.
        email = (found.get("email") or "").strip()
        if not email:
            # Bound successfully, so this person is who they say they are — but
            # a local account has to be keyed on an address, and this entry has
            # none. Inventing one from the typed identifier is how two people
            # end up sharing a row. Its own reason so an admin sees the real
            # cause (a directory missing `mail`), one generic refusal to the
            # caller like every other branch here.
            _logger.warning(
                "LDAP entry %s bound successfully but carries no %s attribute",
                user_dn, ldap_config.user_email_attribute,
            )
            return "no_directory_email", None

        # Bind succeeded — find or create local user
        try:
            existing_user = await self.get_by_email(email)
        except exceptions.UserNotExists:
            existing_user = None

        if existing_user is not None:
            # ★★★The MERGE case: this address already has a local account, so
            # the directory is claiming it rather than creating it. That much
            # always worked — one row, no duplicate, their name and workspace
            # left alone.
            #
            # What did not work is that this branch used to `return "success"`
            # right here. Placement lives in the create branch below, so anybody
            # who already existed but belonged to NO organization signed in
            # perfectly and landed in an empty product — no workspace, no role,
            # nothing to ask about. Measured: a local account with zero
            # memberships authenticated through the directory and stayed at
            # zero. Exactly the outcome _place_auto_provisioned_user was written
            # to prevent, reached by walking around it.
            #
            # Both calls are no-ops for the 200 people who are already placed:
            # _place_auto_provisioned_user returns early when a live membership
            # exists, and _attach_open_memberships only claims invites addressed
            # to this email. So this costs the common path one query and fixes
            # the uncommon one.
            #
            # ★The name is deliberately NOT overwritten from the directory here.
            # A human may have set it, and a sign-in is not the moment to
            # second-guess that. It is only FILLED IN when empty — see below.
            # ★The DN backfill sits OUTSIDE the auto_provision branch on
            # purpose. Whether or not this deployment provisions from the
            # directory, an account that just bound against it is directory-
            # managed — and `ldap_dn` is the only thing that says so. Without
            # it the account reads as `local`, and the password routes would
            # offer to set a password the directory owns. (This column was
            # declared and written nowhere at all until now.)
            #
            # ★★★But first: the same email-is-not-identity gate the SSO door
            # carries (CVE-2026-53516 / nOAuth class — the long comment lives in
            # `oauth_callback`). An attacker who registers victim@corp.com
            # locally before the victim's first directory sign-in otherwise ends
            # up sharing the victim's account.
            #
            # ★THE ASYMMETRY, and it cuts both ways.
            #
            # The directory side of the proof is STRONGER here than anything an
            # OAuth token offers, so there is no `email_verified` half to this
            # gate: the bind above actually succeeded, which means this caller
            # holds the credential the directory stores for `user_dn`, and the
            # address we matched on came from the directory's OWN entry
            # (`found["email"]`, read off the matched entry — NOT the string
            # posted to the form) — an admin-maintained attribute, not
            # something the person types. That is proof about the DIRECTORY side
            # and it is why the LDAP branch asks for one condition where the SSO
            # branch asks for two.
            #
            # It is proof about NOTHING on the local side. It says the person at
            # the keyboard owns the directory entry; it says nothing whatsoever
            # about who created the `users` row already sitting at that address.
            # That row is the attacker's asset in this attack, and a bind cannot
            # see it. Hence: the local row must be verified, exactly as on the
            # SSO door.
            #
            # ★There IS a partial mitigation already, and it is not enough. The
            # DN backfill below stamps `ldap_dn`, and `authenticate` refuses the
            # local password door to any row carrying one — so post-merge the
            # attacker's password stops working there. It does not help if the
            # attacker's row is `is_superuser` (break-glass keeps the local
            # password), and it does not undo the merge: the attacker's existing
            # OAuth identities, API keys and memberships all remain on the row
            # the victim now signs into. A mitigation that depends on the
            # attacker not being an admin is not a gate.
            if not existing_user.is_verified:
                # ★Returned as its own reason so the AUDIT LOG names it, while
                # the caller still gets the one generic refusal both doors give
                # (`_do_authenticate_ldap` → LOGIN_BAD_CREDENTIALS). Saying
                # "an unverified local account is in the way" to an
                # unauthenticated caller would confirm the address exists —
                # the enumeration oracle test_two_doors.py exists to prevent.
                #
                # ★A legitimate user hitting this cannot sign in at either door
                # until an admin verifies or removes the local row. On this
                # deployment nobody can hit it: `verify_emails = False` makes
                # every registered account verified at creation, and LDAP
                # auto-provisioning creates rows with is_verified = True.
                _logger.warning(
                    "Refused to merge LDAP identity %s into existing local "
                    "account %s: the local account is not verified",
                    user_dn, email,
                )
                return "unverified_local_account", None

            if not (existing_user.ldap_dn or "").strip():
                async with self.user_db.session as session:
                    existing_user.ldap_dn = user_dn
                    session.add(existing_user)
                    await session.commit()

            if ldap_config.auto_provision_users:
                async with self.user_db.session as session:
                    if directory_name and not (existing_user.name or "").strip():
                        existing_user.name = directory_name
                        session.add(existing_user)
                    await self._attach_open_memberships(existing_user, session)
                    await self._place_auto_provisioned_user(
                        session, existing_user, ldap_org_id, source="ldap"
                    )
                    await session.commit()
            return "success", email

        if not ldap_config.auto_provision_users:
            # ★The bind SUCCEEDED — this person is who they say they are, and
            # this deployment simply does not hand out accounts on that basis.
            # Logged as its own reason because "correct password, no account" is
            # the one refusal an admin can fix in ten seconds, and it looked
            # identical to a typo until now. Still one generic answer to the
            # caller: naming it would confirm the address exists in the
            # directory, which is the oracle above.
            return "not_provisioned", email

        # Auto-provision: create local user from LDAP
        from fastapi_users.password import PasswordHelper
        ph = PasswordHelper()
        async with self.user_db.session as session:
            await self.user_db.create({
                "email": email,
                # ★The directory's own name for this person, falling back to
                # the address only when the entry genuinely has none. This
                # used to be unconditionally `email.split("@")[0]`, which
                # turned a 200-person directory into 200 accounts called
                # `staff001`…`staff200` while their real names sat unread in
                # the entry. Open WebUI reads `cn` here for the same reason.
                "name": directory_name or email.split("@")[0],
                "hashed_password": ph.hash(ph.generate()),
                # ★Records that the directory owns this account. The password
                # hashed just above is random and is never a way in — which is
                # exactly why `has a password` cannot classify an account and
                # this column has to carry the fact. See core/auth_origin.py.
                "ldap_dn": user_dn,
                "is_active": True,
                "is_verified": True,
                "is_superuser": False,
            })
            new_user = await self.get_by_email(email)
            await self._attach_open_memberships(new_user, session)
            # ★An invite is the exception here, not the rule — the whole
            # point of directory auto-provision is that nobody wrote this
            # person's name down first. Without this they get an account
            # and no workspace. The org comes from the directory itself:
            # whoever configured it decided which workspace it speaks for.
            await self._place_auto_provisioned_user(
                session, new_user, ldap_org_id, source="ldap"
            )
            await session.commit()
        return "success", email

    async def on_after_login(
        self,
        user: User,
        request: Optional[Request] = None,
        json_body: Optional[dict] = None
    ) -> None:
        try:
            from app.dependencies import async_session_maker
            from app.core.timestamps import utcnow_naive
            async with async_session_maker() as session:
                # ★NAIVE UTC. `last_login` is a plain DateTime column and
                # asyncpg refuses an aware datetime for one — this write raised
                # DataError on every single sign-in, silently, because of the
                # swallow below. The column read NULL for accounts that had
                # signed in dozens of times.
                await session.execute(
                    update(User).where(User.id == str(user.id)).values(last_login=utcnow_naive())
                )
                await session.commit()
        except Exception as e:  # noqa: BLE001
            # Still swallowed — a failed bookkeeping write must not fail a
            # login — but no longer silent. A swallow with no log is how the
            # bug above survived from the day it shipped.
            logging.getLogger(__name__).warning(
                "Could not record last_login for %s: %s", user.id, e
            )

        # A successful sign-in clears this account's failed-attempt count, so a
        # person who mistyped their password a few times is not still carrying
        # them, and REFUNDS the one attempt it charged to its address.
        #
        # ★★The refund is what lets a whole office sign in. The address charge
        # happens in a dependency, before the password is checked, so without it
        # 200 people behind one NAT address spend the cap on arriving correctly
        # — measured, 20 in and 180 refused. The bucket is refunded by one, not
        # cleared: clearing is how an attacker holding one valid account would
        # wipe their own guesses at will.
        try:
            from app.dependencies import async_session_maker
            from app.core.login_throttle import clear_login_throttle, refund_login_ip
            async with async_session_maker() as session:
                await clear_login_throttle(session, user.email)
                await refund_login_ip(session, request)
        except Exception:  # noqa: BLE001
            pass

        # Handle redirect for any OAuth/OIDC callback
        if request and request.url.path.endswith("/callback"):
            import json
            try:
                response_data = json.loads(json_body.body.decode()) if json_body else {}
            except Exception:
                response_data = {}
            token = response_data.get('access_token')
            if token:
                redirect_url = f"{settings.dash_config.base_url}/users/sign-in?access_token={token}&email={user.email}"
                raise HTTPException(status_code=303, headers={"Location": redirect_url})

    async def _attach_open_memberships(self, user: User, session: AsyncSession):
        stmt = select(Membership).where(
            and_(
                func.lower(Membership.email) == (user.email or "").strip().lower(),
                Membership.user_id.is_(None)
            )
        )
        open_memberships = (await session.execute(stmt)).scalars().all()
        
        if open_memberships:
            user.is_verified = True

        # Update each open membership with the new user
        for membership in open_memberships:
            membership.user_id = user.id
            membership_role = membership.role
            membership.email = None  # Clear the email since we now have a user

            # Materialize any pre-assigned (pending) RBAC roles and group
            # memberships from the invite onto the freshly-registered user.
            await self._materialize_pending_rbac(session, membership, user.id)

            # Create the matching RBAC role_assignment so the invited user
            # actually has permissions in the org. Mirrors
            # OrganizationService._assign_system_role.
            if membership_role:
                await self._assign_system_role(
                    session, membership.organization_id, user.id, membership_role
                )
            # Telemetry: invited user accepted invite and signed up
            try:
                await telemetry.capture(
                    "organization_member_joined_via_invite",
                    {
                        "organization_id": str(membership.organization_id),
                        "membership_id": str(membership.id),
                        "user_id": str(user.id),
                    },
                    user_id=str(user.id),
                    org_id=str(membership.organization_id),
                )
            except Exception:
                pass

    async def _assign_system_role(
        self,
        session: AsyncSession,
        organization_id,
        user_id,
        role_name: str,
    ) -> bool:
        """Give a user the RBAC assignment behind a membership role.

        ★A ``Membership`` row is not permission. The resolver reads
        ``role_assignments``; the ``Membership.role`` string is a label beside
        it. Create one without the other and the person is a member of a
        workspace who can see nothing in it — which reads as a broken product,
        not as a permission problem.

        ★Argument order deliberately mirrors
        ``OrganizationService._assign_system_role(db, org_id, user_id, role_name)``.
        Two same-named helpers whose last two arguments are swapped is a bug
        waiting for the first person who moves a line between them.

        Extracted so the invite path and the auto-provision paths cannot drift:
        one of them getting this right and the others not is exactly the shape
        of bug this phase is fixing.

        Swallowed and returns False on failure: never break a sign-in over
        RBAC bookkeeping. Idempotent — an existing live assignment is left
        alone rather than duplicated.
        """
        from app.models.role import Role
        from app.models.role_assignment import RoleAssignment

        if not role_name:
            return False
        try:
            system_role = (await session.execute(
                select(Role).where(
                    Role.name == role_name,
                    Role.is_system == True,
                    Role.organization_id.is_(None),
                    Role.deleted_at.is_(None),
                )
            )).scalar_one_or_none()
            if not system_role:
                return False
            existing = (await session.execute(
                select(RoleAssignment).where(
                    RoleAssignment.organization_id == organization_id,
                    RoleAssignment.role_id == system_role.id,
                    RoleAssignment.principal_type == "user",
                    RoleAssignment.principal_id == user_id,
                    RoleAssignment.deleted_at.is_(None),
                )
            )).scalar_one_or_none()
            if existing:
                return True
            # ★★★A SAVEPOINT, because `except` does not contain a database
            # error. Postgres aborts the WHOLE transaction on a failed
            # statement, so swallowing an IntegrityError here leaves the
            # caller's session poisoned and its own commit() raises
            # PendingRollbackError — the sign-in 500s and the account it just
            # created is rolled back with it. Exactly the failure this method
            # claims to be immune to. The savepoint is what makes the claim
            # true: only the failed write is discarded.
            async with session.begin_nested():
                session.add(RoleAssignment(
                    organization_id=organization_id,
                    role_id=system_role.id,
                    principal_type="user",
                    principal_id=user_id,
                ))
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _place_auto_provisioned_user(
        self,
        session: AsyncSession,
        user: User,
        organization_id=None,
        source: str = "sso",
    ) -> bool:
        """Put a self-admitted account into a workspace, with a role.

        ★This is the other half of admission. Phase S1 let a trusted provider
        create the account; without this the person signs in successfully and
        lands in an empty product — no organization, nothing to ask about.
        Authenticating, being admitted, and being placed are three different
        things, and only the first two were ever built.

        Runs in the CALLER's session on purpose, before its commit, so the
        account and the membership are one transaction. A crash between them
        would leave exactly the orphaned account this method exists to prevent.

        Returns True if a membership was created.
        """
        import logging as _logging
        log = _logging.getLogger(__name__)

        try:
            # An invite already placed them — that path owns the role, and it
            # was chosen by a human. Never second-guess it.
            existing = (await session.execute(
                select(Membership).where(
                    Membership.user_id == user.id,
                    Membership.deleted_at.is_(None),
                )
            )).scalars().first()
            if existing:
                return False

            org_id = str(organization_id) if organization_id else None
            if org_id:
                # ★Check the hint before trusting it. A directory configured
                # against an organization that has since been deleted would
                # otherwise reach the INSERT and fail on the foreign key, and a
                # failed statement aborts the caller's whole transaction — see
                # the savepoint note in _assign_system_role. Cheaper and far
                # clearer in the log than relying on the savepoint to catch it.
                exists = (await session.execute(
                    select(Organization.id).where(
                        Organization.id == org_id,
                        Organization.deleted_at.is_(None),
                    )
                )).first()
                if not exists:
                    log.warning(
                        "Auto-provision (%s) cannot place %s: organization %s does "
                        "not exist. Check the directory's organization.",
                        source, user.email, org_id,
                    )
                    return False
            if not org_id:
                # ★No hint means SSO, which is instance-global and does not know
                # which workspace it speaks for. With exactly one organization
                # there is no question. With several, guessing puts somebody in
                # the wrong company's data — so refuse, loudly, and let the
                # invite path handle it.
                org_rows = (await session.execute(
                    select(Organization.id).where(Organization.deleted_at.is_(None))
                )).all()
                if len(org_rows) == 1:
                    org_id = str(org_rows[0][0])
                elif len(org_rows) > 1:
                    log.warning(
                        "Auto-provision (%s) cannot place %s: %d organizations exist "
                        "and single sign-on does not say which one. Invite them, or "
                        "use the directory, which does.",
                        source, user.email, len(org_rows),
                    )
                    return False
                else:
                    # No org yet — this is the bootstrap first user, and
                    # _ensure_org_for_first_uninvited_user owns that case.
                    return False

            # ★★★The seat cap, which this path did not check.
            #
            # seats.py states that "every path that creates a Membership
            # enforces the same rule" and names this module among them. It did
            # not: nothing in auth.py imported seats, and can_add_members was
            # called only by the LDAP GROUP SYNC. So a customer licensed for 50
            # got 50 by sync and unlimited by sign-in — measured, 200 directory
            # users provisioned themselves straight past the cap.
            #
            # Raises 402 rather than returning False. The graceful-degrade helper
            # (has_seat_for) exists for exactly this kind of path, but degrading
            # here means an account that authenticates and belongs nowhere — the
            # empty product this whole method was written to prevent. Refusing
            # loudly is the honest answer, and because this runs inside the
            # caller's transaction BEFORE its commit, the half-created account
            # is discarded with it.
            from app.core.seats import enforce_seat_limit
            await enforce_seat_limit(session, org_id, 1)

            from app.services.organization_settings_service import (
                OrganizationSettingsService,
            )
            role_name = await OrganizationSettingsService().resolve_auto_provision_role(
                session, org_id
            )

            # ★Savepoint, for the same reason as in _assign_system_role: a
            # failed INSERT here would abort the caller's transaction and turn
            # a swallowed placement failure into a failed sign-in.
            async with session.begin_nested():
                session.add(Membership(
                    user_id=user.id,
                    organization_id=org_id,
                    role=role_name,
                ))
                await session.flush()
            # ★The membership is the seat; the assignment is the permission.
            # See _assign_system_role — one without the other is a member who
            # can see nothing.
            self_assigned = await self._assign_system_role(
                session, org_id, user.id, role_name
            )
            if not self_assigned:
                log.warning(
                    "Auto-provision (%s): placed %s in org %s as '%s' but could not "
                    "create the role assignment — they will have a seat and no "
                    "permissions until an admin sets their role.",
                    source, user.email, org_id, role_name,
                )
            user.is_verified = True
            log.info(
                "Auto-provision (%s): %s joined org %s as '%s'.",
                source, user.email, org_id, role_name,
            )
            return True
        except HTTPException:
            # ★★★The seat refusal above must NOT be swallowed by the catch-all
            # below — that would turn "your licence is full" back into a silent
            # placement failure and leave exactly the account-with-no-workspace
            # this method exists to prevent. This clause is the only reason the
            # 402 reaches the caller at all.
            raise
        except Exception as e:  # noqa: BLE001
            # ★Swallowed: a placement failure must not undo an authentication
            # that already succeeded. The account still exists and an admin can
            # invite them; a raised exception here would instead surface as a
            # broken sign-in with an account left behind.
            log.warning(
                "Auto-provision (%s) could not place %s: %s", source, user.email, e
            )
            return False

    async def _materialize_pending_rbac(self, session: AsyncSession, membership: Membership, user_id: str) -> None:
        """Rewrite pending (membership-keyed) RBAC rows onto the registered user.

        An org admin can pre-assign roles and groups to an invite before the
        user registers. Those are stored as ``RoleAssignment`` rows with
        ``principal_type='membership'`` and ``GroupMembership`` rows with
        ``membership_id`` set. When the invitee registers we convert them to
        the user-keyed equivalents, de-duplicating against anything the user
        may already have, so the resolver (which only knows 'user'/'group')
        sees the intended permissions immediately.
        """
        from app.models.role_assignment import RoleAssignment
        from app.models.group_membership import GroupMembership
        from app.models.usage_policy import UsagePolicyAssignment

        org_id = membership.organization_id
        try:
            # Role assignments: membership principal → user principal
            ra_result = await session.execute(
                select(RoleAssignment).where(
                    RoleAssignment.organization_id == org_id,
                    RoleAssignment.principal_type == "membership",
                    RoleAssignment.principal_id == membership.id,
                    RoleAssignment.deleted_at.is_(None),
                )
            )
            for assignment in ra_result.scalars().all():
                existing = await session.execute(
                    select(RoleAssignment).where(
                        RoleAssignment.organization_id == org_id,
                        RoleAssignment.role_id == assignment.role_id,
                        RoleAssignment.principal_type == "user",
                        RoleAssignment.principal_id == user_id,
                        RoleAssignment.deleted_at.is_(None),
                    )
                )
                if existing.scalar_one_or_none():
                    await session.delete(assignment)
                else:
                    assignment.principal_type = "user"
                    assignment.principal_id = user_id

            # Group memberships: membership-keyed → user-keyed
            gm_result = await session.execute(
                select(GroupMembership).where(
                    GroupMembership.membership_id == membership.id,
                    GroupMembership.deleted_at.is_(None),
                )
            )
            for gm in gm_result.scalars().all():
                existing = await session.execute(
                    select(GroupMembership).where(
                        GroupMembership.group_id == gm.group_id,
                        GroupMembership.user_id == user_id,
                    )
                )
                if existing.scalar_one_or_none():
                    await session.delete(gm)
                else:
                    gm.user_id = user_id
                    gm.membership_id = None

            # Usage-policy (quota) assignments: membership principal → user principal
            upa_result = await session.execute(
                select(UsagePolicyAssignment).where(
                    UsagePolicyAssignment.organization_id == org_id,
                    UsagePolicyAssignment.principal_type == "membership",
                    UsagePolicyAssignment.principal_id == membership.id,
                    UsagePolicyAssignment.deleted_at.is_(None),
                )
            )
            for upa in upa_result.scalars().all():
                existing = await session.execute(
                    select(UsagePolicyAssignment).where(
                        UsagePolicyAssignment.organization_id == org_id,
                        UsagePolicyAssignment.policy_id == upa.policy_id,
                        UsagePolicyAssignment.principal_type == "user",
                        UsagePolicyAssignment.principal_id == user_id,
                        UsagePolicyAssignment.deleted_at.is_(None),
                    )
                )
                if existing.scalar_one_or_none():
                    await session.delete(upa)
                else:
                    upa.principal_type = "user"
                    upa.principal_id = user_id
            await session.flush()
        except Exception:
            # Never block registration if pending-RBAC tables aren't present
            # (pre-migration) or a conversion hits a transient conflict.
            pass

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        print(f"User {user.id} has registered.")

        # Get open memberships and attach user
        async with self.user_db.session as session:
            # Materialize any domain-based auto-invites before attaching
            await self._create_domain_invites(user.email, session)
            await self._attach_open_memberships(user, session)

            if not settings.dash_config.features.verify_emails:
                user.is_verified = True

            await session.commit()
            # Auto-create organization for the first uninvited user
            await self._ensure_org_for_first_uninvited_user(session, user)

            # Audit the signup against each org the user now belongs to.
            try:
                from app.ee.audit.service import audit_service
                memberships = (
                    await session.execute(
                        select(Membership).where(Membership.user_id == user.id)
                    )
                ).scalars().all()
                for m in memberships:
                    await audit_service.log(
                        db=session,
                        organization_id=str(m.organization_id),
                        action="user.registered",
                        user_id=str(user.id),
                        resource_type="user",
                        resource_id=str(user.id),
                        details={"email": user.email},
                        request=request,
                    )
            except Exception:
                pass

        # Welcome email (best-effort, non-blocking): summarizes the agents the
        # user can access + a CTA. Fired after commit so memberships are visible.
        self._fire_welcome_email(user.id)

    @staticmethod
    def _fire_welcome_email(user_id) -> None:
        try:
            import asyncio
            from app.services.welcome_email import send_welcome_email
            asyncio.create_task(send_welcome_email(str(user_id)))
        except Exception:
            pass

    async def _provider_admits_new_users(self, oauth_name: str) -> bool:
        """Does the identity provider that just authenticated somebody also
        get to admit them?

        Opens its OWN session deliberately: the caller's session is mid-flight
        inside the OAuth callback, and this is a read of instance-global config
        that must not join that transaction.

        ★Swallowed and fails CLOSED — an unreadable SSO config leaves the
        existing invite and domain checks to decide, exactly as before.
        """
        try:
            from app.dependencies import async_session_maker
            from app.services.sso_config_service import SsoConfigService

            async with async_session_maker() as _db:
                return await SsoConfigService().provider_admits_new_users(_db, oauth_name)
        except Exception:  # noqa: BLE001
            return False

    async def _has_domain_invite(self, email: str, session: AsyncSession) -> bool:
        """Return True if some org's signup_policy would admit this email's domain."""
        from app.models.organization_settings import OrganizationSettings
        from app.ee.license import has_feature
        if not has_feature("domain_signup"):
            return False
        if not email or "@" not in email:
            return False
        domain = email.split("@", 1)[-1].lower()
        result = await session.execute(select(OrganizationSettings))
        for s in result.scalars().all():
            policy = (s.config or {}).get("signup_policy") or {}
            if not policy.get("enabled"):
                continue
            domains = [str(d).lower() for d in (policy.get("allowed_domains") or []) if isinstance(d, str)]
            if domain in domains:
                return True
        return False

    async def _create_domain_invites(self, email: str, session: AsyncSession) -> None:
        """For every org whose signup_policy admits this email's domain,
        create an open Membership invite if one doesn't already exist."""
        from app.models.organization_settings import OrganizationSettings
        from app.ee.license import has_feature
        from sqlalchemy import or_
        if not has_feature("domain_signup"):
            return
        if not email or "@" not in email:
            return
        domain = email.split("@", 1)[-1].lower()
        result = await session.execute(select(OrganizationSettings))
        for s in result.scalars().all():
            policy = (s.config or {}).get("signup_policy") or {}
            if not policy.get("enabled"):
                continue
            domains = [str(d).lower() for d in (policy.get("allowed_domains") or []) if isinstance(d, str)]
            if domain not in domains:
                continue
            # Skip if this email is already linked to this org (invite or attached user)
            email_norm = (email or "").strip().lower()
            existing_user = (await session.execute(
                select(User).where(func.lower(User.email) == email_norm)
            )).scalar_one_or_none()
            conditions = [func.lower(Membership.email) == email_norm]
            if existing_user is not None:
                conditions.append(Membership.user_id == existing_user.id)
            dupe = (await session.execute(
                select(Membership).where(
                    Membership.organization_id == s.organization_id,
                    or_(*conditions),
                )
            )).scalar_one_or_none()
            if dupe:
                continue
            # Respect the license seat cap: auto-provisioning must not push an org
            # past its paid seat count. Full org → skip this invite (and log), don't
            # abort the whole signup — other admitting orgs may still have room.
            from app.core.seats import has_seat_for
            if not await has_seat_for(session, s.organization_id):
                logging.getLogger(__name__).warning(
                    "Domain signup: org %s is at its license seat limit; "
                    "skipping auto-invite for %s", s.organization_id, email
                )
                continue
            role = str(policy.get("auto_invite_role") or "member")
            session.add(Membership(
                email=email_norm,
                organization_id=s.organization_id,
                role=role,
            ))
        await session.flush()

    async def oauth_callback(
        self: "UserManager[User, str]",
        oauth_name: str,
        access_token: str,
        account_id: str,
        account_email: str,
        expires_at: Optional[int] = None,
        refresh_token: Optional[str] = None,
        request: Optional[Request] = None,
        *args,
        # ★The name the identity provider sent, when it sent one.
        #
        # Keyword-only with a default because the base library's own OAuth
        # router calls this method and knows nothing about it. Absent → the old
        # behaviour exactly.
        account_name: Optional[str] = None,
        # ★★★Did the identity provider ASSERT that it verified this address?
        #
        # Three states, and the difference between two of them is the whole
        # point of this parameter:
        #
        #   True   the IdP said `email_verified: true`
        #   False  the IdP said `email_verified: false`, or the value we are
        #          about to match on did not come from the `email` claim at all
        #          (see the `preferred_username`/`upn` note in auth_providers)
        #   None   nobody told us — the base fastapi-users OAuth router calls
        #          this method and knows nothing about the argument
        #
        # ★The default is None and NOT True, deliberately. Keeping the default
        # means the signature stays compatible with a caller that predates it
        # (that is why `account_name` above is shaped the same way); it does not
        # mean the link is allowed. Absence of evidence is not evidence: the
        # gate below tests `is True`, so None and False both refuse. A caller
        # that wants an account linked has to say so.
        account_email_verified: Optional[bool] = None,
        **kwargs
    ) -> User:
        try:
            # First try to get user by OAuth account
            user = await self.get_by_oauth_account(oauth_name, account_id)
            # Keep the stored tokens current. They used to be written only on
            # the account's first login, so anything reading them later (Entra
            # profile sync/preview) worked against a long-expired token.
            async with self.user_db.session as session:
                stmt = select(OAuthAccount).where(
                    and_(
                        OAuthAccount.oauth_name == oauth_name,
                        OAuthAccount.account_id == account_id,
                    )
                )
                acc = (await session.execute(stmt)).scalar_one_or_none()
                if acc:
                    acc.access_token = access_token
                    acc.expires_at = expires_at
                    if refresh_token:
                        acc.refresh_token = refresh_token
                    session.add(acc)
                    await session.commit()
            return user
        except exceptions.UserNotExists:
            # If OAuth account doesn't exist, check if user exists by email
            try:
                user = await self.get_by_email(account_email)

                # ★★★A MATCHING EMAIL STRING IS NOT PROOF OF IDENTITY.
                #
                # CVE-2026-53516 (Better Auth, CVSS 8.3) and the GitLab /
                # "nOAuth" class of account-takeover, all the same shape: an
                # external identity is attached to whatever local row happens to
                # carry the same address, and the address alone is treated as
                # the person.
                #
                # The attack, concretely:
                #   1. attacker registers victim@corp.com locally — the row
                #      exists and is UNVERIFIED, because nobody clicked the
                #      link that only the victim can receive
                #   2. victim later signs in through SSO for the first time
                #   3. the victim's external identity attaches to the
                #      ATTACKER's row
                #   4. the attacker still holds the password on that row, and
                #      now shares the victim's account, workspaces and data
                #
                # So a link needs proof from BOTH sides, and neither half is
                # sufficient on its own:
                #
                #   user.is_verified        somebody proved they can receive
                #                           mail at this address before this
                #                           row was trusted — i.e. the local
                #                           row is not a squatter
                #   account_email_verified  the IdP asserts it verified the
                #                           address it is handing us — i.e. the
                #                           external identity is not a squatter
                #                           either (an IdP where a user can type
                #                           their own unverified email is step 1
                #                           of the attack run from the far side)
                #
                # ★It is NOT currently exploitable on this deployment, and that
                # is an accident of two config defaults, not a property of this
                # code: `features.verify_emails = False` makes
                # `on_after_register` stamp is_verified = True on every new
                # account, and `allow_uninvited_signups = False` stops a
                # stranger registering at all. Flip either one — and
                # `verify_emails` is exactly the flag an admin turns ON to be
                # more careful — and step 1 becomes available. The linking code
                # is what has to assert this, not the sign-up policy.
                if not (user.is_verified and account_email_verified is True):
                    # ★What "refuse" means here: we do NOT attach the identity
                    # and we do NOT issue a session. There is no safe middle.
                    #   - linking anyway is the takeover
                    #   - logging in without linking would hand the session to
                    #     whoever owns that local row on the strength of the
                    #     same unproven string
                    #   - creating a SECOND user at this address is worse than
                    #     both: `get_by_email` and every membership/invite path
                    #     in this codebase treat the address as unique, so the
                    #     duplicate either collides on the unique index or
                    #     silently splits one person's data in two
                    #
                    # ★What a LEGITIMATE user hitting this sees: the sign-in
                    # page with the message below (handle_callback turns an
                    # HTTPException into `?error=` on /users/sign-in). They
                    # cannot fix it themselves, which is intended — the whole
                    # question is who owns the pre-existing row, and only an
                    # admin can answer it. The admin's fix is one of: verify the
                    # local account, delete it if it is a squatter, or configure
                    # the provider so it sends `email_verified` (see
                    # `trust_email_claim` in auth_providers.py for the Entra
                    # case, which never sends the claim at all).
                    # ★★★ALIASED, and it has to be. `HTTPException` is imported
                    # at module level, but the invite branch further down this
                    # same function re-imports it locally — which makes the name
                    # LOCAL for the whole of `oauth_callback`, so a bare
                    # `raise HTTPException(...)` here would hit UnboundLocalError
                    # before it ever refused anything. That would turn this
                    # refusal into a 500, and a 500 on the linking path reads as
                    # "SSO is down", not "we blocked a takeover".
                    # Guard: tests/unit/fork/test_no_shadowed_module_imports.py,
                    # which is what caught it.
                    from fastapi import HTTPException as _HTTPException
                    import logging as _logging

                    # ★A refused link is the one thing an admin has to be able
                    # to find. It is indistinguishable from "SSO is broken" from
                    # the user's side, and the reason lives nowhere else.
                    _logging.getLogger(__name__).warning(
                        "Refused to link %s identity to existing account %s: "
                        "local_is_verified=%s idp_email_verified=%s",
                        oauth_name, account_email,
                        user.is_verified, account_email_verified,
                    )
                    raise _HTTPException(
                        status_code=403,
                        detail={
                            "code": "identity_link_unverified",
                            "message": (
                                "An account already exists for this email address, "
                                "and we could not prove it belongs to you. Ask your "
                                "admin to verify the existing account before signing "
                                "in with this provider."
                            ),
                        },
                    )

                # User exists, let's link the OAuth account
                async with self.user_db.session as session:
                    oauth_account = OAuthAccount(
                        oauth_name=oauth_name,
                        access_token=access_token,
                        account_id=account_id,
                        account_email=account_email,
                        expires_at=expires_at,
                        refresh_token=refresh_token,
                        user_id=user.id
                    )
                    session.add(oauth_account)
                    # ★The MERGE case: an account that already existed locally.
                    # Fill in a name it never had; never overwrite one a person
                    # chose. The directory claims the account — it does not get
                    # to rename its owner.
                    if account_name and not (user.name or "").strip():
                        user.name = account_name
                        session.add(user)
                    # ★★★And PLACE them. This branch used to link the identity
                    # and return, which is correct for anyone who already
                    # belongs somewhere and silently wrong for anyone who does
                    # not: measured live, an existing local account with zero
                    # memberships signed in through Keycloak perfectly and
                    # stayed at zero — a valid session and an empty product.
                    #
                    # Both calls are no-ops for an existing member, so the
                    # common path is unchanged. Placement is gated on the same
                    # trust the create branch uses: a provider that may not
                    # admit strangers may not hand out workspaces either.
                    await self._attach_open_memberships(user, session)
                    if await self._provider_admits_new_users(oauth_name):
                        await self._place_auto_provisioned_user(
                            session, user, None, source="sso"
                        )
                    await session.commit()
                return user
            except exceptions.UserNotExists:
                # User doesn't exist at all, create new user with OAuth
                # Enforce invite policy similar to regular registration
                async with self.user_db.session as session:
                    # If uninvited signups are disabled and not first user, require invite
                    user_count = (await session.execute(select(User))).scalars().all().__len__()
                    if user_count > 0 and not settings.dash_config.features.allow_uninvited_signups:
                        stmt = select(Membership).where(
                            and_(
                                func.lower(Membership.email) == (account_email or "").strip().lower(),
                                Membership.user_id.is_(None)
                            )
                        )
                        open_membership = (await session.execute(stmt)).scalar_one_or_none()
                        # ★The provider itself may be trusted to admit people.
                        #
                        # Everything else in this block asks "did an admin write
                        # this person's name down first?" — an invite, or a
                        # domain list an admin maintains. When a directory is
                        # wired up, that question is redundant: the directory IS
                        # the list, and keeping a second copy here only creates
                        # two places to disagree. This install refused ten real
                        # sign-ins that Keycloak had already authenticated.
                        #
                        # Checked FIRST because it is the cheapest to reason
                        # about and the one an admin actually configured; the
                        # invite and domain paths remain untouched beneath it,
                        # so nothing that worked before stops working.
                        if not open_membership and await self._provider_admits_new_users(oauth_name):
                            open_membership = True
                        if not open_membership and await self._has_domain_invite(account_email, session):
                            open_membership = True
                        if not open_membership:
                            from fastapi import HTTPException
                            raise HTTPException(
                                status_code=403,
                                detail={
                                    "code": "invitation_required",
                                    "message": "Sign-up is disabled. Ask your admin for an invite.",
                                },
                            )
                # Fetch user info if needed (e.g., from Google)
                fetched_name = None
                if oauth_name == "google":
                    async with httpx.AsyncClient() as client:
                        response = await client.get(
                            "https://www.googleapis.com/oauth2/v1/userinfo",
                            headers={"Authorization": f"Bearer {access_token}"},
                        )
                        response.raise_for_status()
                        user_info = response.json()
                        fetched_name = user_info.get("name")
                # ★The directory's own name comes FIRST. The email local part is
                # a last resort, not a default — it produced `emp001`…`emp200`
                # for a 200-person Keycloak realm that was sending real names.
                if not fetched_name:
                    fetched_name = account_name
                if not fetched_name:
                    fetched_name = account_email.split("@")[0]

                # Create the new user
                async with self.user_db.session as session:
                    user = await self.user_db.create(
                        {
                            "email": account_email,
                            "name": fetched_name,
                            "hashed_password": self.password_helper.hash(self.password_helper.generate()),
                            "is_active": True,
                            "is_verified": True,
                            "is_superuser": False,
                        }
                    )

                    # Materialize domain-based auto-invites, then attach memberships
                    await self._create_domain_invites(account_email, session)
                    await self._attach_open_memberships(user, session)
                    # ★If no invite claimed them, the provider that admitted
                    # them in the gate above is why they are here — so place
                    # them. Same session, before the commit below, so the
                    # account and the membership stand or fall together.
                    # No-ops when an invite already placed them, and when this
                    # is the very first user (no org exists yet;
                    # _ensure_org_for_first_uninvited_user owns that).
                    await self._place_auto_provisioned_user(
                        session, user, None, source="sso"
                    )


                    oauth_account = OAuthAccount(
                        oauth_name=oauth_name,
                        access_token=access_token,
                        account_id=account_id,
                        account_email=account_email,
                        expires_at=expires_at,
                        refresh_token=refresh_token,
                        user_id=user.id
                    )
                    session.add(oauth_account)
                    await session.commit()
                    await session.refresh(user)
                    # Auto-create organization for the first uninvited user
                    await self._ensure_org_for_first_uninvited_user(session, user)
                # New OAuth/OIDC sign-up → welcome email (best-effort).
                self._fire_welcome_email(user.id)
                return user

    async def _ensure_org_for_first_uninvited_user(self, session: AsyncSession, user: User) -> None:
        """Create an organization automatically if this is the first user without an invite.

        Conditions:
        - User has no memberships (not invited/attached)
        - Total users == 1
        - Total organizations == 0
        """
        # If user already has a membership, skip
        user_membership = (
            await session.execute(
                select(Membership).where(Membership.user_id == user.id)
            )
        ).scalars().first()
        if user_membership:
            return

        total_users = (await session.execute(select(User))).scalars().all().__len__()
        total_orgs = (await session.execute(select(Organization))).scalars().all().__len__()

        if total_users != 1 or total_orgs != 0:
            return

        # First bootstrap user is the super admin: only a superuser may create
        # accounts (self-signup is disabled), so the very first user must be one.
        user.is_superuser = True
        session.add(user)
        await session.commit()

        org_name = DEFAULT_ORG_NAME
        description = DEFAULT_ORG_DESCRIPTION

        organization_service = OrganizationService()
        created_org = await organization_service.create_organization(
            session,
            OrganizationCreate(name=org_name, description=description),
            user,
        )

        # Fresh install → seed the default ready-to-use agents (Microsoft Fabric,
        # Power BI, City Mart Retail). This is the single first-org bootstrap that
        # both the local-register and OAuth signup paths funnel through, and it
        # only runs when total_users == 1 / total_orgs == 0 (checked above), so an
        # existing install is never retroactively seeded. Fully swallowed: a
        # seeding failure must NEVER break signup.
        try:
            from app.models.organization import Organization as _Org
            from app.services.default_agents_seeder import seed_default_agents
            org_model = (await session.execute(
                select(_Org).where(_Org.id == created_org.id)
            )).scalars().first()
            if org_model is not None:
                await seed_default_agents(session, org_model, user)
        except Exception as _seed_err:  # noqa: BLE001
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "seed_default_agents failed for org %s: %s",
                getattr(created_org, "id", None), _seed_err, exc_info=True,
            )

    async def _update(self, user: User, update_dict: dict):
        """The chokepoint for the super-admin password lock.

        ★★★Every fastapi-users path that changes a password funnels through
        here — the reset-token flow, PATCH /users/me, the superuser variant. In
        particular `POST /api/auth/reset-password` is mounted whenever local
        auth is on and changes a password on the strength of an emailed token
        alone: no current password, no super-admin check. It is unreachable in
        practice today only because the sign-in page hides Forgot password while
        SMTP is unconfigured, and unconfigured is the default. The day someone
        fills in the SMTP settings, mailbox access would silently become a way
        to take over the one account nothing can restore.

        Guarding this single method means a future route — or an upstream port
        that adds one — cannot reopen the hole by accident.
        """
        if "password" in update_dict and getattr(user, "is_superuser", False):
            raise exceptions.InvalidPasswordException(
                reason="A super admin's password cannot be changed from inside the app."
            )
        return await super()._update(user, update_dict)

    async def on_after_forgot_password(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        if getattr(user, "is_superuser", False):
            # No mail, and no hint that the address exists either.
            return
        await self._send_reset_password_email(user, token, request)

    async def _send_reset_password_email(self, user: User, token: str, request: Optional[Request] = None):
        import asyncio
        
        base_url = settings.dash_config.base_url
            
        reset_url = f"{base_url}/users/reset-password?token={token}"

        # Password-reset mail goes to a real person, so it names the product
        # the admin configured rather than the string this build shipped with.
        brand = _brand_name()

        message = MessageSchema(
            subject="Reset your password",
            recipients=[user.email],
            body=f"Hello {user.name},<br /><br />You have requested to reset your password for {brand}. Click the link below to reset your password:<br /><br /> <a href='{reset_url}'>{reset_url}</a><br /><br />If you didn't request this, please ignore this email.<br /><br />Best regards,<br />{brand} team",
            subtype="html"
        )
        fm = settings.email_client
        
        async def send_email():
            try:
                await fm.send_message(message)
            except Exception as e:
                print(f"Error sending reset password email: {e}")
        
        # Create task without awaiting it
        asyncio.create_task(send_email())

    async def on_after_request_verify(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        await self._send_verification_email(user, token, request)

    async def _send_verification_email(self, user: User, token: str, request: Optional[Request] = None):
        import asyncio
        
        base_url = settings.dash_config.base_url
            
        verification_url = f"{base_url}/users/verify?token={token}"
        
        message = MessageSchema(
            subject="Verify your email",
            recipients=[user.email],
            body=f"Welcome to {_brand_name()}! You are almost ready to start using our platform. Click to verify your email: <br /> {verification_url}",
            subtype="html"
        )
        fm = settings.email_client
        
        async def send_email():
            try:
                await fm.send_message(message)
            except Exception as e:
                print(f"Error sending verification email: {e}")
        
        # Create task without awaiting it
        asyncio.create_task(send_email())

    async def create(
        self,
        user_create: UserCreate,
        safe: bool = False,
        request: Optional[Request] = None,
    ) -> User:
        # Your pre-registration logic here
        # For example:
        await self._validate_user_creation(user_create)
        
        # Call parent create method
        user = await super().create(user_create, safe, request)
        
        return user

    async def _validate_user_creation(self, user_create: UserCreate) -> None:
        """Pre-registration gate (runs before the User row is created).

        Invite-token rules for the local/password path (SSO has its own check in
        ``oauth_callback`` and is intentionally not affected):

          - token supplied  -> must match a pending invite for this email and not
            be expired; otherwise reject (no user is created).
          - no token, pending invite exists, signups closed -> reject and point
            the user at their invite link (closes the "claim by typing an
            invited email" gap). Under open signups, email-match onboarding is
            still allowed.
          - no token, no invite -> existing behaviour (first user / domain
            policy / open signups allowed; otherwise rejected).
        """
        from datetime import datetime

        token = getattr(user_create, "invite_token", None)
        email = user_create.email

        async with self.user_db.session as session:
            user_count = (await session.execute(select(User))).scalars().all().__len__()

            # 1) A token was presented — validate it strictly.
            if token:
                membership = (await session.execute(
                    select(Membership).where(
                        Membership.invite_token == token,
                        Membership.user_id.is_(None),
                    )
                )).scalar_one_or_none()
                if not membership:
                    raise HTTPException(status_code=400, detail="This invite link is invalid. Ask your admin to resend it.")
                expires = membership.invite_expires_at
                if expires is not None and expires < datetime.utcnow():
                    raise HTTPException(status_code=400, detail="This invite link has expired. Ask your admin to resend it.")
                if membership.email and membership.email.lower() != (email or "").lower():
                    raise HTTPException(status_code=400, detail="This invite was sent to a different email address.")
                return  # valid invite — allow creation

            # 2) No token. First user always allowed (bootstrap).
            if user_count == 0:
                return

            # A pending invite exists for this email but no token was supplied.
            pending = (await session.execute(
                select(Membership).where(
                    and_(Membership.email == email, Membership.user_id.is_(None))
                )
            )).scalar_one_or_none()

            if pending:
                if settings.dash_config.features.allow_uninvited_signups:
                    return  # open signups: email-match onboarding still allowed
                raise HTTPException(
                    status_code=400,
                    detail="Please sign up using your invite link, or ask your admin to resend it.",
                )

            # 3) No pending invite.
            if not settings.dash_config.features.allow_uninvited_signups:
                if await self._has_domain_invite(email, session):
                    return
                raise HTTPException(
                    status_code=400,
                    detail="Sign-up is disabled. Ask your admin for an invite.",
                )

        return

async def _org_signup_policy(db: AsyncSession, organization_id: str) -> dict:
    from app.models.organization_settings import OrganizationSettings
    from app.ee.license import has_feature
    if not has_feature("domain_signup"):
        return {}
    result = await db.execute(
        select(OrganizationSettings).where(OrganizationSettings.organization_id == organization_id)
    )
    s = result.scalar_one_or_none()
    if not s:
        return {}
    policy = (s.config or {}).get("signup_policy") or {}
    if not policy.get("enabled"):
        return {}
    return policy


async def auto_provision_user_for_org(
    db: AsyncSession,
    organization_id: str,
    email: str,
    name: Optional[str] = None,
) -> Optional[User]:
    """Provision (or attach) a user for chat-onboarding into a specific org.

    Returns the User if admitted via (a) an existing open invite scoped to this
    org or (b) this org's domain_signup policy. Returns None if neither applies
    (caller should block the chat user with an "ask your admin" message).
    """
    from sqlalchemy import func
    from fastapi_users.password import PasswordHelper

    if not email or "@" not in email:
        return None
    email_norm = email.strip().lower()

    # Step 1 (find existing user) is the caller's responsibility. Here we
    # handle: existing user with no membership in this org, or no user at all.
    existing_user = (await db.execute(
        select(User).where(func.lower(User.email) == email_norm)
    )).scalar_one_or_none()

    open_invite = (await db.execute(
        select(Membership).where(
            Membership.organization_id == organization_id,
            func.lower(Membership.email) == email_norm,
            Membership.user_id.is_(None),
        )
    )).scalar_one_or_none()

    policy = await _org_signup_policy(db, organization_id)
    domain = email_norm.split("@", 1)[-1]
    allowed_domains = [str(d).lower() for d in (policy.get("allowed_domains") or []) if isinstance(d, str)]
    domain_admitted = domain in allowed_domains

    if not open_invite and not domain_admitted:
        return None

    # A brand-new membership is minted only when there's no open invite to attach
    # to (an existing invite already occupies a seat). Enforce the license seat cap
    # before creating anything, so a full org can't be grown via chat onboarding —
    # and so we never leave an orphan User with no membership.
    if not open_invite:
        from app.core.seats import has_seat_for
        if not await has_seat_for(db, organization_id):
            logging.getLogger(__name__).warning(
                "Chat auto-provision: org %s is at its license seat limit; "
                "refusing to provision %s", organization_id, email_norm
            )
            return None

    if existing_user:
        if open_invite:
            open_invite.user_id = existing_user.id
        else:
            role = str(policy.get("auto_invite_role") or "member")
            db.add(Membership(
                user_id=existing_user.id,
                organization_id=organization_id,
                role=role,
            ))
        await db.commit()
        return existing_user

    ph = PasswordHelper()
    user = User(
        email=email_norm,
        name=name or email_norm.split("@")[0],
        hashed_password=ph.hash(ph.generate()),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    db.add(user)
    await db.flush()

    if open_invite:
        open_invite.user_id = user.id
    else:
        role = str(policy.get("auto_invite_role") or "member")
        db.add(Membership(
            user_id=user.id,
            organization_id=organization_id,
            role=role,
        ))

    await db.commit()
    await db.refresh(user)

    try:
        await telemetry.capture(
            "user_auto_provisioned_from_chat",
            {
                "organization_id": str(organization_id),
                "user_id": str(user.id),
                "via": "domain_signup" if not open_invite else "open_invite",
            },
            user_id=str(user.id),
            org_id=str(organization_id),
        )
    except Exception:
        pass

    return user


async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)):
    yield UserManager(user_db)


bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")


def get_jwt_strategy() -> JWTStrategy:
    # Align JWT lifetime with frontend cookie (7 days) to avoid desync logout
    return JWTStrategy(secret=SECRET, lifetime_seconds=60 * 60 * 24 * 7)


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)


def create_fastapi_users(
    get_user_manager: Callable,
    auth_backend: AuthenticationBackend,
    oauth_providers: List[BaseOAuth2] = None
) -> FastAPIUsers:
    if oauth_providers is None:
        oauth_providers = []
    return FastAPIUsers(get_user_manager, [auth_backend])
# verified user only!
fapi = create_fastapi_users(get_user_manager, auth_backend)
_jwt_current_user = fapi.current_user(active=True, optional=True)

# API Key authentication
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


_LAST_SEEN_DEBOUNCE = timedelta(hours=1)

async def _update_last_seen(user: User, db: AsyncSession) -> None:
    # ★Same naive-column rule as last_login, and the same consequence: the
    # aware write failed on EVERY authenticated request, so `last_seen` stayed
    # NULL, so the debounce below never once short-circuited — the failing
    # UPDATE was attempted on every request rather than at most hourly.
    from app.core.timestamps import utcnow_naive, as_utc

    now = datetime.now(timezone.utc)
    if user.last_seen and now - as_utc(user.last_seen) < _LAST_SEEN_DEBOUNCE:
        return
    try:
        await db.execute(update(User).where(User.id == str(user.id)).values(last_seen=utcnow_naive()))
        await db.commit()
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning("Could not record last_seen for %s: %s", user.id, e)


# The only paths a signed-in user may still reach while `must_change_password`
# is set. Anything else is refused, so a password the admin knows cannot be used
# to work in the product — it can only be spent on choosing a new one.
#
# ★`/api/settings` is on the list because it is what the login page and the app
# shell read before anything else; refusing it renders a blank page instead of
# the change-password screen. It is public anyway (no auth required), so listing
# it grants nothing.
_PASSWORD_CHANGE_ALLOWED_PATHS = (
    "/api/users/whoami",
    "/api/users/me/change-password",
    "/api/users/me/password-status",
    "/api/auth/jwt/logout",
    "/api/settings",
    "/api/changelog",
    "/health",
)

# Sent as the detail so the frontend can tell this apart from an ordinary 403
# and route to the change-password screen rather than showing "access denied".
PASSWORD_CHANGE_REQUIRED = "password_change_required"


def _enforce_password_change(request: Request, user: User) -> None:
    """Refuse everything but the change-password flow while a reset is pending.

    ★Gates the JWT path only. An API key is minted from an interactive session,
    and minting one is itself blocked here, so a pending user has no key to
    present; leaving key auth alone keeps existing automation working when an
    admin resets a human's password.
    """
    if not getattr(user, "must_change_password", False):
        return
    path = request.url.path
    if any(path.startswith(p) for p in _PASSWORD_CHANGE_ALLOWED_PATHS):
        return
    raise HTTPException(status_code=403, detail=PASSWORD_CHANGE_REQUIRED)


async def current_user(
    request: Request,
    jwt_user: Optional[User] = Depends(_jwt_current_user),
    api_key: Optional[str] = Depends(api_key_header),
    db: AsyncSession = Depends(get_async_db),
) -> User:
    """
    Get the current user from either JWT token or API key.

    Tries JWT first, then falls back to API key authentication.
    API keys can be passed via X-API-Key header or Authorization: Bearer <key> (for bow_ prefixed keys).
    """
    # Try JWT first
    if jwt_user is not None:
        _enforce_password_change(request, jwt_user)
        await _update_last_seen(jwt_user, db)
        return jwt_user

    # Try API key from X-API-Key header
    if api_key:
        from app.services.api_key_service import ApiKeyService
        api_key_service = ApiKeyService()
        user = await api_key_service.get_user_by_api_key(db, api_key)
        if user is not None:
            return user
    
    # Try API key from Authorization header (for MCP clients that use Bearer format)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer bow_"):
        from app.services.api_key_service import ApiKeyService
        api_key_service = ApiKeyService()
        bearer_api_key = auth_header[7:]  # Remove "Bearer " prefix
        user = await api_key_service.get_user_by_api_key(db, bearer_api_key)
        if user is not None:
            return user
    
    # No valid authentication
    raise HTTPException(
        status_code=401,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def current_user_optional(
    request: Request,
    jwt_user: Optional[User] = Depends(_jwt_current_user),
    api_key: Optional[str] = Depends(api_key_header),
    db: AsyncSession = Depends(get_async_db),
) -> Optional[User]:
    """Same as current_user but returns None instead of raising 401."""
    try:
        return await current_user(request, jwt_user, api_key, db)
    except HTTPException:
        return None


async def get_current_organization(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    user: User = Depends(current_user),
) -> Organization:
    """Resolve the request's organization AND enforce that the caller still
    belongs to it.

    This is the single membership chokepoint for the HTTP surface: every route
    that depends on it inherits the check, including the ~138 org-scoped routes
    that carry no ``@requires_permission`` decorator. A user whose membership was
    removed (directly, or via LDAP/OIDC/SCIM sync) is rejected with 403 the
    moment they touch any org-scoped endpoint, instead of retaining access to
    everything gated only on "public within the org".

    Defined in core.auth (next to ``current_user``) rather than in dependencies
    to avoid a circular import; ``app.dependencies`` re-exports it lazily so
    ``from app.dependencies import get_current_organization`` keeps working.

    Membership-bootstrap flows (invite acceptance, org creation) do not depend on
    this — they establish the membership and so must run above the invariant.
    """
    from app.core.permission_resolver import assert_principal_belongs_to_org

    org = await resolve_organization(request, db)
    await assert_principal_belongs_to_org(db, user, org.id)
    return org


async def forbid_service_account_principal(user: User = Depends(current_user)) -> User:
    """Block service-account principals from privilege-management surfaces.

    A service account must never be able to mint keys, create/assign roles,
    invite members, or manage other service accounts — otherwise a leaked key
    could self-escalate. Applied to the RBAC, member-management, API-key, and
    service-account routers regardless of the account's RBAC role.
    """
    if getattr(user, "is_service_account", False):
        raise HTTPException(
            status_code=403,
            detail="Service accounts cannot manage identities, roles, or keys",
        )
    return user