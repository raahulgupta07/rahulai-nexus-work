import base64
import hashlib
import os
import time
import uuid
import urllib.parse

from typing import Optional, Dict, Any, Tuple

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from httpx_oauth.clients.openid import OpenID
from httpx_oauth.clients.google import GoogleOAuth2

from app.settings.config import settings
from app.core.auth import get_jwt_strategy

import logging as _logging

_auth_logger = _logging.getLogger(__name__)


async def _audit_auth_event(
    action: str,
    request: Request,
    user_id: str | None = None,
    details: dict | None = None,
) -> None:
    """Fire-and-forget audit log for auth events (login/failure).

    Uses its own session since auth handlers have no injected db session.
    """
    try:
        from app.dependencies import async_session_maker
        from app.ee.audit.service import audit_service

        ip_address = None
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip_address = forwarded.split(",")[0].strip()
        else:
            ip_address = request.client.host if request.client else None

        async with async_session_maker() as session:
            await audit_service.log(
                db=session,
                organization_id=None,
                action=action,
                user_id=user_id,
                resource_type="auth",
                details=details,
                request=request,
            )
    except Exception:
        _auth_logger.debug("_audit_auth_event failed", exc_info=True)


def _cookie_secure() -> bool:
    base_url = (settings.dash_config.base_url or "").lower()
    return base_url.startswith("https://")


def _get_scopes(scopes: Optional[list]) -> list:
    return scopes or ["openid", "profile", "email"]


def _display_name_from_claims(claims: dict) -> Optional[str]:
    """The human name an OIDC provider sends, or None if it sent none.

    ``name`` is the standard profile claim and every provider we have met fills
    it. Entra and some Keycloak realms send only the parts, hence the join.
    Returns None rather than a guess — the caller falls back to the email, and
    a blank string must not win over that.
    """
    full = (claims.get("name") or "").strip()
    if full:
        return full
    parts = [
        (claims.get("given_name") or "").strip(),
        (claims.get("family_name") or "").strip(),
    ]
    joined = " ".join(p for p in parts if p)
    return joined or None


def _claim_is_true(value: Any) -> bool:
    """Is an OIDC boolean claim asserting truth?

    ★Spec says `email_verified` is a JSON boolean, and providers disagree with
    the spec: Keycloak and Google send `true`, several SAML-fronted IdPs send
    the STRING `"true"`, and at least one sends `"1"`. A bare `bool(value)`
    would also make the string `"false"` true, which is the wrong way to be
    wrong. Anything not on the list is treated as NOT asserted.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return False


def _provider_email_claim_is_trusted(provider: str, cfg: Any) -> bool:
    """May this provider's `email` claim stand in for a missing `email_verified`?

    ★★★This escape hatch exists because Microsoft Entra ID does not emit
    `email_verified` AT ALL. A gate that requires the claim, with no way to say
    "this IdP does not speak it", refuses every Entra tenant that has any
    pre-existing local account — which is most of them, and it would look like
    the SSO integration simply broke.

    Default is off, on purpose: the safe failure is a refused link an admin can
    fix in one setting, not a silent takeover nobody can see. Turning it on is
    an admin ASSERTING that this IdP owns its address space (true for Entra
    against a tenant domain, false for any consumer-signup IdP).

    Two ways to set it, because the config schema for providers lives in
    dash-config/instance_settings and this file must not require a migration to
    be usable:

        per provider   `trust_email_claim: true` on the OIDC provider block
        instance-wide  DASH_TRUST_EMAIL_CLAIM_PROVIDERS=entra,okta

    ★`getattr` with a default, so the provider config object is free not to
    have the attribute at all — which it currently does not.
    """
    if _claim_is_true(getattr(cfg, "trust_email_claim", False)):
        return True
    raw = os.environ.get("DASH_TRUST_EMAIL_CLAIM_PROVIDERS", "")
    names = {n.strip().lower() for n in raw.split(",") if n.strip()}
    return (provider or "").strip().lower() in names


def _email_and_verification_from_claims(
    claims: dict, provider: str, cfg: Any
) -> Tuple[Optional[str], bool]:
    """The address to sign in as, and whether the IdP PROVED it owns it.

    Returns `(account_email, account_email_verified)`. The second value is the
    one that decides whether `UserManager.oauth_callback` may attach this
    identity to an account that already exists — see the CVE-2026-53516 comment
    there.

    ★★★`preferred_username` and `upn` are NOT verified email addresses, and
    using either as a LINKING key is the nOAuth bug.

    `preferred_username` is a display/login handle the OIDC spec explicitly
    calls mutable and non-unique, and on several IdPs the user edits it
    themselves. `upn` is an AD attribute that is usually admin-owned but not
    always, and is not required to be a deliverable address. Feed either into
    `get_by_email` as proof and an attacker who can set their own handle to
    "victim@corp.com" claims the victim's existing row — no email access needed,
    which is the whole point of the class.

    ★They are KEPT as a source for the address, deliberately. Removing them
    outright breaks real Entra / AD FS deployments where `upn` is genuinely the
    only address in the token; those tenants would simply stop signing in. The
    fix is not to refuse the value — it is to refuse to treat it as PROOF. So
    `verified` is computed from the `email` claim ALONE: an address sourced from
    a fallback can still create a new account, and can never match an existing
    one.

    ★And `trust_email_claim` cannot rescue a fallback either. It vouches for a
    provider's `email` claim; a handle the user controls is not that claim no
    matter who vouches for the provider.
    """
    email_claim = claims.get("email")
    account_email = (
        email_claim
        or claims.get("preferred_username")
        or claims.get("upn")
    )
    if not email_claim or account_email != email_claim:
        return account_email, False
    if "email_verified" in claims:
        return account_email, _claim_is_true(claims["email_verified"])
    # ★No claim at all — Entra's normal behaviour. Unverified unless an admin
    # has explicitly vouched for this provider.
    return account_email, _provider_email_claim_is_trusted(provider, cfg)


def _get_redirect_uri(provider: str, request: Request, redirect_path: Optional[str] = None) -> str:
    from app.core.base_url import derive_request_base_url
    path = redirect_path or f"/api/auth/{provider}/callback"
    return f"{derive_request_base_url(request)}{path}"


def _issue_state_cookie(provider: str, response: JSONResponse, state: str) -> None:
    response.set_cookie(
        key=f"oidc_{provider}_state",
        value=state,
        max_age=300,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path=f"/api/auth/{provider}",
    )


def _read_state_cookie(provider: str, request: Request) -> Optional[str]:
    return request.cookies.get(f"oidc_{provider}_state")


def _issue_pkce_cookies(provider: str, response: JSONResponse, code_verifier: str) -> None:
    response.set_cookie(
        key=f"oidc_{provider}_verifier",
        value=code_verifier,
        max_age=300,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path=f"/api/auth/{provider}",
    )


def _read_pkce_cookie(provider: str, request: Request) -> Optional[str]:
    return request.cookies.get(f"oidc_{provider}_verifier")


def _generate_pkce_pair() -> Tuple[str, str]:
    # verifier (43-128 chars) and S256 challenge
    verifier_bytes = os.urandom(64)
    code_verifier = base64.urlsafe_b64encode(verifier_bytes).decode().rstrip("=")
    challenge = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(challenge).decode().rstrip("=")
    return code_verifier, code_challenge


def _get_oidc_config(provider_name: str):
    providers = getattr(settings.dash_config, "oidc_providers", []) or []
    for p in providers:
        if p.name == provider_name:
            return p
    return None


async def _resolve_oidc_config(provider_name: str):
    """Resolve an OIDC provider's config from the DB (falling back to the
    bow-config file). Login happens with no org context, so open a fresh
    session. Returns None if the provider is not found."""
    from app.dependencies import async_session_maker
    from app.services.sso_config_service import SsoConfigService

    async with async_session_maker() as db:
        providers = await SsoConfigService().resolve_oidc_providers(db)
    for p in providers or []:
        if p.name == provider_name:
            return p
    return None


async def _resolve_google_config():
    """Resolve the Google OAuth config from the DB (falling back to the
    bow-config file). Uses a fresh session (no org context at login)."""
    from app.dependencies import async_session_maker
    from app.services.sso_config_service import SsoConfigService

    async with async_session_maker() as db:
        return await SsoConfigService().resolve_google(db)


def _is_entra_provider(provider_name: str) -> bool:
    """Check if an OIDC provider is Microsoft Entra ID based on its issuer URL."""
    cfg = _get_oidc_config(provider_name)
    if not cfg:
        return False
    issuer = (cfg.issuer or "").lower()
    return "login.microsoftonline.com" in issuer or "sts.windows.net" in issuer


def _is_google_provider(provider_name: str) -> bool:
    """Check if a login provider is Google — either the built-in google_oauth
    flow or a generic OIDC provider whose issuer is accounts.google.com."""
    if provider_name == "google":
        return True
    cfg = _get_oidc_config(provider_name)
    if not cfg:
        return False
    return "accounts.google.com" in (cfg.issuer or "").lower()


async def build_authorize_url(provider: str, request: Request) -> JSONResponse:
    # Google
    if provider == "google":
        g = await _resolve_google_config()
        if not g or not g.enabled:
            raise HTTPException(status_code=404, detail="Google OAuth not enabled")
        # Enabled-but-unconfigured: return a clean, user-facing 400 (defense
        # against direct URL hits — the login page also guards this client-side).
        if not (g.client_id and g.client_secret):
            raise HTTPException(
                status_code=400,
                detail="Google sign-in is not available yet — ask your admin to finish setup.",
            )

        client = GoogleOAuth2(g.client_id, g.client_secret)
        state = uuid.uuid4().hex
        redirect_uri = _get_redirect_uri(provider, request)
        authorization_url = await client.get_authorization_url(
            redirect_uri=redirect_uri,
            state=state,
            scope=["openid", "profile", "email"],
        )
        response = JSONResponse({"authorization_url": authorization_url})
        _issue_state_cookie(provider, response, state)
        return response

    # OIDC providers
    cfg = await _resolve_oidc_config(provider)
    if not cfg or not cfg.enabled:
        raise HTTPException(status_code=404, detail="OIDC provider not found")
    # Enabled-but-unconfigured: clean, user-facing 400 (defense against direct
    # URL hits — the login page also guards this client-side).
    if not (cfg.client_id and cfg.client_secret and cfg.issuer):
        _label = getattr(cfg, "label", None) or provider
        raise HTTPException(
            status_code=400,
            detail=f"{_label} sign-in is not available yet — ask your admin to finish setup.",
        )

    issuer = cfg.issuer.rstrip("/")
    openid_cfg_endpoint = issuer if "well-known" in issuer else f"{issuer}/.well-known/openid-configuration"
    client = OpenID(cfg.client_id, cfg.client_secret, openid_configuration_endpoint=openid_cfg_endpoint, name=provider)

    code_verifier, code_challenge = _generate_pkce_pair()
    state = uuid.uuid4().hex
    redirect_uri = _get_redirect_uri(provider, request, getattr(cfg, "redirect_path", None))

    authorization_url = await client.get_authorization_url(
        redirect_uri=redirect_uri,
        state=state,
        scope=_get_scopes(getattr(cfg, "scopes", None)),
        extras_params={
            **(getattr(cfg, "extra_authorize_params", {}) or {}),
            **({"code_challenge": code_challenge, "code_challenge_method": "S256"} if getattr(cfg, "pkce", True) else {}),
        },
    )

    response = JSONResponse({"authorization_url": authorization_url})
    _issue_state_cookie(provider, response, state)
    if getattr(cfg, "pkce", True):
        _issue_pkce_cookies(provider, response, code_verifier)
    return response


def _friendly_error_message(detail: Any) -> str:
    """Extract a user-facing message from an HTTPException detail.

    OAuth/OIDC errors (missing invite, bad state, token exchange failures, etc.)
    should be surfaced to the user on the sign-in page rather than rendered as a
    raw JSON error response.
    """
    if isinstance(detail, dict):
        return detail.get("message") or "Sign-in failed. Please try again or contact your admin."
    if isinstance(detail, str) and detail:
        return detail
    return "Sign-in failed. Please try again or contact your admin."


def _error_redirect(message: str) -> RedirectResponse:
    """Redirect back to the sign-in page with a visible error message."""
    msg = urllib.parse.quote(message)
    return RedirectResponse(f"/users/sign-in?error={msg}", status_code=303)


async def handle_callback(
    provider: str,
    request: Request,
    code: Optional[str],
    state: Optional[str],
    user_manager,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
) -> RedirectResponse:
    """Public entrypoint: convert any callback failure into a user-visible redirect.

    Without this, errors raised during the OAuth/OIDC flow (e.g. a user without an
    invite signing in via EntraID/Okta) would surface as a raw JSON error page
    instead of being shown on the sign-in/sign-up screen.
    """
    # The provider may redirect back with an OAuth error (e.g. Entra AADSTS500011)
    # instead of a code. Surface that message rather than the misleading
    # "Missing code/state" that a bare code/state check would produce.
    if error:
        message = error_description or error
        _auth_logger.warning(f"OAuth callback error for provider={provider}: {message}")
        await _audit_auth_event(
            action="auth.login_failed",
            request=request,
            details={"provider": provider, "reason": "provider_error", "error": error},
        )
        return _error_redirect(message)

    try:
        return await _handle_callback(provider, request, code, state, user_manager)
    except HTTPException as e:
        _auth_logger.warning(f"OAuth callback failed for provider={provider}: {e.detail}")
        return _error_redirect(_friendly_error_message(e.detail))
    except Exception as e:
        _auth_logger.error(f"Unexpected error in OAuth callback for provider={provider}: {e}", exc_info=True)
        return _error_redirect("Sign-in failed due to an unexpected error. Please try again or contact your admin.")


async def _handle_callback(provider: str, request: Request, code: Optional[str], state: Optional[str], user_manager) -> RedirectResponse:
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code/state")

    cookie_state = _read_state_cookie(provider, request)
    if not cookie_state or cookie_state != state:
        raise HTTPException(status_code=400, detail="Invalid state")

    # Google
    if provider == "google":
        g = await _resolve_google_config()
        if not g or not g.enabled:
            raise HTTPException(status_code=404, detail="Google OAuth not enabled")
        client = GoogleOAuth2(g.client_id, g.client_secret)
        redirect_uri = _get_redirect_uri(provider, request)
        try:
            token = await client.get_access_token(code, redirect_uri)
        except httpx.HTTPStatusError as e:
            try:
                body = e.response.json()
            except Exception:
                body = e.response.text
            await _audit_auth_event(
                action="auth.login_failed",
                request=request,
                details={"provider": provider, "reason": "token_exchange_failed"},
            )
            raise HTTPException(status_code=400, detail=f"Token exchange failed: {body}")

        access_token = token.get("access_token")
        refresh_token = token.get("refresh_token")
        expires_in = token.get("expires_in")
        expires_at = int(time.time()) + int(expires_in) if isinstance(expires_in, int) else None

        try:
            account_id, account_email = await client.get_id_email(access_token)
        except Exception as e:
            await _audit_auth_event(
                action="auth.login_failed",
                request=request,
                details={"provider": provider, "reason": "user_info_fetch_failed"},
            )
            raise HTTPException(status_code=400, detail=f"Failed to fetch user info: {e}")

        # ★Google's own proof that it verified this address.
        #
        # `get_id_email` above reads the People API, which returns no such
        # signal — but the authorize call requests the `openid` scope, so the
        # TOKEN response carries an id_token, and Google always puts
        # `email_verified` in it. Decoded without signature verification for the
        # same reason the OIDC path does: it came back over TLS from Google's
        # own token endpoint in response to our client_secret, so it was never
        # in the browser's hands.
        #
        # ★A Google Workspace address is verified by construction; a
        # @gmail.com one is verified because Google owns the mailbox. The claim
        # being FALSE is rare and means an unverified alias — exactly the case
        # this must not link on.
        google_email_verified = False
        google_id_token = token.get("id_token")
        if google_id_token:
            try:
                import jwt as _pyjwt

                google_claims = _pyjwt.decode(
                    google_id_token, options={"verify_signature": False}
                )
                if (google_claims.get("email") or "").lower() == str(account_email).lower():
                    google_email_verified = _claim_is_true(
                        google_claims.get("email_verified")
                    )
            except Exception as e:
                _auth_logger.warning(
                    f"Google id_token could not be read (email treated as "
                    f"unverified): {e}"
                )

        # Use user manager to link/create. Any failure (e.g. missing invite) is
        # handled by the outer wrapper and surfaced on the sign-in page.
        user = await user_manager.oauth_callback(
            oauth_name=provider,
            access_token=access_token,
            account_id=str(account_id),
            account_email=str(account_email),
            expires_at=expires_at,
            refresh_token=refresh_token,
            request=request,
            account_email_verified=google_email_verified,
        )

        await _audit_auth_event(
            action="auth.login",
            request=request,
            user_id=str(user.id),
            details={"provider": provider, "email": str(account_email)},
        )

        # Google profile / job-info sync — fetch the signed-in user's profile
        # (userinfo + People API) and store it on their Membership when the org
        # has opted in. Uses the fresh login token in hand.
        if access_token:
            try:
                await _sync_google_profile_on_login(user=user, access_token=access_token)
            except Exception as e:
                _auth_logger.warning(f"Google profile sync after login failed for user {user.id}: {e}")

        await _record_login(user)

        strategy = get_jwt_strategy()
        jwt_token = await strategy.write_token(user)
        return RedirectResponse(f"/users/sign-in?access_token={jwt_token}&email={user.email}", status_code=303)

    # OIDC providers
    cfg = await _resolve_oidc_config(provider)
    if not cfg or not cfg.enabled:
        raise HTTPException(status_code=404, detail="OIDC provider not found")

    issuer = cfg.issuer.rstrip("/")
    openid_cfg_endpoint = issuer if "well-known" in issuer else f"{issuer}/.well-known/openid-configuration"
    client = OpenID(cfg.client_id, cfg.client_secret, openid_configuration_endpoint=openid_cfg_endpoint, name=provider)
    redirect_uri = _get_redirect_uri(provider, request, getattr(cfg, "redirect_path", None))

    try:
        token_endpoint = (await _discover_endpoints(openid_cfg_endpoint))["token_endpoint"]
        async with httpx.AsyncClient(timeout=10) as http:
            data: Dict[str, Any] = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            }
            scopes = _get_scopes(getattr(cfg, "scopes", None))
            if scopes:
                data["scope"] = " ".join(scopes)
            if getattr(cfg, "pkce", True):
                code_verifier = _read_pkce_cookie(provider, request)
                if not code_verifier:
                    raise HTTPException(status_code=400, detail="Missing PKCE verifier")
                data["code_verifier"] = code_verifier
            data.update(getattr(cfg, "extra_token_params", {}) or {})

            auth = None
            if getattr(cfg, "client_auth_method", "basic") == "basic":
                auth = httpx.BasicAuth(cfg.client_id, cfg.client_secret)
            else:
                data["client_id"] = cfg.client_id
                data["client_secret"] = cfg.client_secret

            resp = await http.post(token_endpoint, data=data, auth=auth, headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
            if resp.status_code >= 400:
                try:
                    detail = resp.json()
                except Exception:
                    detail = resp.text
                raise HTTPException(status_code=400, detail=f"Token exchange failed: {detail}")
            token = resp.json()
    except Exception as e:
        await _audit_auth_event(
            action="auth.login_failed",
            request=request,
            details={"provider": provider, "reason": "token_exchange_failed"},
        )
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {e}")

    access_token = token.get("access_token")
    refresh_token = token.get("refresh_token")
    expires_in = token.get("expires_in")
    expires_at = int(time.time()) + int(expires_in) if isinstance(expires_in, int) else None

    # Extract user identity from id_token first (reliable for Entra/OIDC),
    # then fall back to userinfo endpoint.
    import jwt as pyjwt
    account_id = None
    account_email = None
    account_name = None
    # ★Starts False, not None: "we have not been shown proof" is the state this
    # variable must hold everywhere it is not explicitly set. See the
    # `account_email_verified` docstring on UserManager.oauth_callback.
    account_email_verified = False

    id_token_raw = token.get("id_token")
    if id_token_raw:
        id_claims = pyjwt.decode(id_token_raw, options={"verify_signature": False})
        uid_claim = getattr(cfg, "uid_claim", "sub") or "sub"
        account_id = id_claims.get(uid_claim) or id_claims.get("sub")
        # ★The address AND whether the provider proved it owns it, decided in
        # one place. The `preferred_username`/`upn` reasoning lives on that
        # function — it is the nOAuth half of CVE-2026-53516.
        account_email, account_email_verified = _email_and_verification_from_claims(
            id_claims, provider, cfg
        )
        # ★The person's own name, which the provider has been sending all along.
        #
        # Without this the account is named after the local part of its email:
        # a directory of 200 arrives as `emp001` … `emp200` while `name`,
        # `given_name` and `family_name` sit unread in the same token. Measured
        # against a real Keycloak realm: 200 of 200 accounts mis-named.
        #
        # `preferred_username` is deliberately NOT a fallback — it is usually
        # the login id, which is the email again by another route.
        account_name = _display_name_from_claims(id_claims)
        _auth_logger.info(f"OIDC id_token claims: sub={account_id}, email={account_email}")

    # Fall back to userinfo endpoint if id_token didn't provide what we need
    if not account_id or not account_email:
        try:
            uid, email = await client.get_id_email(access_token)
            account_id = account_id or uid
            account_email = account_email or email
            # ★`get_id_email` is a two-tuple and CANNOT carry `email_verified`
            # — that is a property of httpx-oauth's API, not of the endpoint.
            # The userinfo RESPONSE does carry it (OIDC core §5.1 lists
            # `email_verified` beside `email`), so read the raw profile rather
            # than declaring this path unverifiable. `get_profile` is a second
            # HTTP call, which is why it is made only on this fallback and not
            # on the id_token path that already has every claim in hand.
            if email and account_email == email:
                try:
                    profile = await client.get_profile(access_token)
                except Exception as pe:
                    _auth_logger.warning(
                        f"OIDC userinfo profile fetch failed (email treated as "
                        f"unverified): {pe}"
                    )
                    profile = None
                if isinstance(profile, dict) and "email_verified" in profile:
                    account_email_verified = _claim_is_true(profile["email_verified"])
                else:
                    account_email_verified = _provider_email_claim_is_trusted(provider, cfg)
        except Exception as e:
            _auth_logger.warning(f"OIDC userinfo fallback failed: {e}")
            if not account_id or not account_email:
                await _audit_auth_event(
                    action="auth.login_failed",
                    request=request,
                    details={"provider": provider, "reason": "user_info_fetch_failed"},
                )
                raise HTTPException(status_code=400, detail=f"Failed to fetch user info: {e}")

    if not account_email:
        await _audit_auth_event(
            action="auth.login_failed",
            request=request,
            details={"provider": provider, "reason": "no_email_in_token"},
        )
        raise HTTPException(status_code=400, detail="Could not determine email from OIDC provider. Ensure the 'email' scope is configured.")

    # Any failure here (e.g. missing invite) is handled by the outer wrapper
    # and surfaced on the sign-in page.
    user = await user_manager.oauth_callback(
        oauth_name=provider,
        access_token=access_token,
        account_id=str(account_id),
        account_email=str(account_email),
        expires_at=expires_at,
        refresh_token=refresh_token,
        request=request,
        account_name=account_name,
        account_email_verified=account_email_verified,
    )

    await _audit_auth_event(
        action="auth.login",
        request=request,
        user_id=str(user.id),
        details={"provider": provider, "email": str(account_email)},
    )

    # OIDC group sync — sync group claims from id_token into BOW Groups
    if getattr(cfg, 'sync_groups', False):
        try:
            await _sync_oidc_groups_on_login(
                cfg=cfg,
                token=token,
                access_token=access_token,
                user=user,
            )
        except Exception as e:
            _auth_logger.warning(f"OIDC group sync failed for user {user.id}: {e}", exc_info=True)

    # Phase 2: Auto-provision OAuth credentials for Entra-based data sources via OBO
    if access_token and _is_entra_provider(provider):
        try:
            from app.services.connection_oauth_service import auto_provision_connection_credentials
            from app.dependencies import async_session_maker
            async with async_session_maker() as db:
                await auto_provision_connection_credentials(db, user, access_token)
        except Exception as e:
            _auth_logger.warning(f"OBO auto-provision after login failed for user {user.id}: {e}")

    # Entra ID profile / job-info sync — fetch the signed-in user's Graph /me
    # profile and store it on their Membership when the org has opted in. Uses
    # the fresh delegated token in hand (default-granted User.Read).
    if access_token and _is_entra_provider(provider):
        try:
            await _sync_entra_profile_on_login(user=user, access_token=access_token)
        except Exception as e:
            _auth_logger.warning(f"Entra profile sync after login failed for user {user.id}: {e}")

    # Google profile / job-info sync — same gate for OIDC providers whose
    # issuer is accounts.google.com (the built-in google flow returns above).
    if access_token and _is_google_provider(provider):
        try:
            await _sync_google_profile_on_login(user=user, access_token=access_token)
        except Exception as e:
            _auth_logger.warning(f"Google profile sync after login failed for user {user.id}: {e}")

    await _record_login(user)

    strategy = get_jwt_strategy()
    jwt_token = await strategy.write_token(user)
    return RedirectResponse(f"/users/sign-in?access_token={jwt_token}&email={user.email}", status_code=303)


async def _record_login(user) -> None:
    from datetime import datetime, timezone
    from app.dependencies import async_session_maker
    from app.models.user import User as UserModel
    from sqlalchemy import update
    try:
        # ★NAIVE UTC — `last_login` is a plain DateTime column and asyncpg
        # refuses an aware datetime for one. This raised on every SSO sign-in;
        # the warning below was the only trace, and nothing read it.
        from app.core.timestamps import utcnow_naive
        now = utcnow_naive()
        async with async_session_maker() as db:
            await db.execute(
                update(UserModel).where(UserModel.id == str(user.id)).values(last_login=now)
            )
            await db.commit()
    except Exception as e:
        _auth_logger.warning(f"Failed to record last_login for user {user.id}: {e}")


async def _sync_oidc_groups_on_login(cfg, token: dict, access_token: str, user) -> None:
    """Extract group claims from id_token and sync into BOW Groups."""
    import jwt as pyjwt
    from app.dependencies import async_session_maker
    from app.ee.oidc.group_sync_service import sync_user_oidc_groups

    id_token_raw = token.get("id_token")
    if not id_token_raw:
        _auth_logger.debug("OIDC group sync: no id_token in token response, skipping")
        return

    # Decode without signature verification — token was already validated by the provider
    id_claims = pyjwt.decode(id_token_raw, options={"verify_signature": False})
    group_claim = getattr(cfg, 'group_claim', 'groups')
    group_ids = id_claims.get(group_claim, [])

    # Handle Entra group overage (>200 groups — groups omitted, _claim_names present)
    if not group_ids and "_claim_names" in id_claims:
        _auth_logger.info("OIDC group sync: group overage detected, falling back to Graph API")
        # For overage, we need to get all groups via Graph — use /me/memberOf with delegated token first,
        # fall back to client credentials
        try:
            from app.ee.oidc.graph_client import resolve_group_names
            group_names_map = await resolve_group_names(access_token)
        except Exception:
            group_names_map = {}
        group_ids = list(group_names_map.keys())
    else:
        group_names_map = None

    if not group_ids:
        _auth_logger.debug(f"OIDC group sync: no groups in claim '{group_claim}', skipping")
        return

    # Resolve group display names via Graph API using client credentials
    if group_names_map is None and getattr(cfg, 'resolve_group_names', False):
        try:
            from app.ee.oidc.graph_client import resolve_group_names_by_ids
            # Extract tenant_id from issuer URL
            issuer = getattr(cfg, 'issuer', '') or ''
            parts = issuer.rstrip('/').split('/')
            tenant_id = parts[-2] if len(parts) >= 2 and parts[-1] == 'v2.0' else parts[-1]
            group_names_map = await resolve_group_names_by_ids(
                group_ids=group_ids,
                tenant_id=tenant_id,
                client_id=cfg.client_id,
                client_secret=cfg.client_secret,
            )
        except Exception as e:
            _auth_logger.warning(f"OIDC group sync: Graph API name resolution failed: {e}")
            group_names_map = None

    # Get user's org — use the first membership's org
    async with async_session_maker() as db:
        from sqlalchemy import select
        from app.models.membership import Membership
        stmt = select(Membership.organization_id).where(
            Membership.user_id == str(user.id),
            Membership.deleted_at.is_(None),
        )
        org_id = (await db.execute(stmt)).scalar_one_or_none()

        if not org_id:
            _auth_logger.debug(f"OIDC group sync: user {user.id} has no org membership, skipping")
            return

        await sync_user_oidc_groups(
            db=db,
            user_id=str(user.id),
            organization_id=str(org_id),
            group_ids=group_ids,
            group_names=group_names_map,
        )


async def _sync_provider_profile_on_login(
    user,
    access_token: str,
    *,
    config_key: str,
    default_fields: list,
    sync_fn,
    label: str,
) -> None:
    """Fetch a provider profile and store it on the user's org Membership.

    Provider-agnostic on-login gate: no-op unless the user's org has enabled
    the given profile-sync config key. Uses the fresh login token so no
    stored-token refresh is needed.
    """
    from app.dependencies import async_session_maker

    async with async_session_maker() as db:
        from sqlalchemy import select
        from app.models.membership import Membership
        stmt = select(Membership.organization_id).where(
            Membership.user_id == str(user.id),
            Membership.deleted_at.is_(None),
        )
        org_id = (await db.execute(stmt)).scalar_one_or_none()
        if not org_id:
            _auth_logger.debug(f"{label}: user {user.id} has no org membership, skipping")
            return

        from app.models.organization import Organization
        org = (
            await db.execute(select(Organization).where(Organization.id == str(org_id)))
        ).scalar_one_or_none()
        if not org:
            return
        settings_obj = await org.get_settings(db)
        cfg = (settings_obj.config or {}).get(config_key) or {}
        if not cfg.get("enabled"):
            _auth_logger.debug(f"{label}: org {org_id} has sync disabled, skipping")
            return

        fields = list(cfg.get("fields") or default_fields)

        await sync_fn(
            db=db,
            user=user,
            organization_id=str(org_id),
            fields=fields,
            access_token=access_token,
        )


async def _sync_entra_profile_on_login(user, access_token: str) -> None:
    """Fetch the user's Entra profile and store it on their org Membership."""
    from app.ee.oidc.profile_service import sync_profile_on_login
    from app.schemas.organization_settings_schema import ENTRA_PROFILE_SYNC_DEFAULT_FIELDS

    await _sync_provider_profile_on_login(
        user,
        access_token,
        config_key="entra_profile_sync",
        default_fields=ENTRA_PROFILE_SYNC_DEFAULT_FIELDS,
        sync_fn=sync_profile_on_login,
        label="Entra profile sync",
    )


async def _sync_google_profile_on_login(user, access_token: str) -> None:
    """Fetch the user's Google profile and store it on their org Membership."""
    from app.ee.oidc.google_profile_service import sync_profile_on_login
    from app.schemas.organization_settings_schema import GOOGLE_PROFILE_SYNC_DEFAULT_FIELDS

    await _sync_provider_profile_on_login(
        user,
        access_token,
        config_key="google_profile_sync",
        default_fields=GOOGLE_PROFILE_SYNC_DEFAULT_FIELDS,
        sync_fn=sync_profile_on_login,
        label="Google profile sync",
    )


async def _discover_endpoints(openid_cfg_endpoint: str) -> Dict[str, str]:
    # openid_cfg_endpoint may already be the well-known URL
    url = openid_cfg_endpoint if "well-known" in openid_cfg_endpoint else f"{openid_cfg_endpoint}/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10) as http:
        resp = await http.get(url)
        resp.raise_for_status()
        return resp.json()


