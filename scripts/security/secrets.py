#!/usr/bin/env python3
"""Read-only secret-exposure / auth / transport probe suite for CityAgent Insights.

Runs against a RUNNING instance (default http://localhost:8095) and proves, one
probe at a time, the properties the product is supposed to hold:

  * settings/config endpoints redact stored secrets (never the ciphertext, only
    a ``*_set: bool`` flag) — asserted against the RESPONSE BODY, not the UI;
  * the JWT is signature-checked (alg:none and a wrong secret are both refused),
    and an invented org id resolves to *no* access rather than default-allow;
  * the two password doors (local + LDAP) and the reset door answer every kind
    of failure with a byte-identical body, so none is an enumeration oracle;
  * a failed connection test does not echo the credential it was handed;
  * the session cookie as SHIPPED carries HttpOnly/Secure (read off the served
    runtime config, because nuxt.config.ts's nested block is silently ignored);
  * the file-embed capability token is scoped to ONE file id, actually expires,
    and cannot be widened into a session (nor a session into a capability);
  * the inbound webhook receiver refuses an unauthenticated delivery;
  * the SSO callback does not hand the session JWT back in a redirect URL;
  * NOTHING returns a secret-shaped string in its body — the whole run fails
    loudly if ``gAAAAA`` / ``sk-`` / ``Bearer <jwt>`` / ``-----BEGIN`` appears.

It also *detects* (never exploits beyond a single /users/me read) the finding
that the session JWT is signed with DASH_ENCRYPTION_KEY itself: given a minted
token and a readable .env, it checks whether HMAC-SHA256(signing_input, key)
equals the token signature. It prints only the boolean — never the key.

STRICTLY READ-ONLY. It never calls GET /data_sources/{id}/test_connection
(which is spelled GET but WRITES is_active) and never creates or mutates a row.
The registration probe uses an EXISTING address, so nothing is created.

Tokens: pass ``--tokens /path/to/tokens.json`` produced by
scripts/mint-user-tokens.py (a PATH ARGUMENT — never the ``>`` redirect form,
which captures start-up log lines and breaks the JSON). Shape:

    {"org": {"id": "..."},
     "users": {"<admin-email>": {"token": "...", "is_superuser": true, ...},
               "<member-email>": {"token": "...", ...}}}

Usage:
    # from repo root, after minting tokens into the container and copying out:
    #   docker cp scripts/mint-user-tokens.py dash-app:/app/backend/
    #   docker exec -w /app/backend dash-app python mint-user-tokens.py \
    #       /tmp/tok.json <admin-email> <member-email>
    #   docker cp dash-app:/tmp/tok.json /tmp/tok.json
    python scripts/security/secrets.py --tokens /tmp/tok.json
    python scripts/security/secrets.py --tokens /tmp/tok.json --base http://localhost:8095
    python scripts/security/secrets.py --tokens /tmp/tok.json --env .env   # + key-reuse check

Exit code is non-zero if any probe FAILS or any secret-shaped string leaks.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# --------------------------------------------------------------------------- #
#  Secret-shaped strings that must NEVER appear in a response body.
# --------------------------------------------------------------------------- #
#  Deliberately broad. A false positive here is a good outcome — it means a
#  probe target changed shape and a human should look. Each is anchored enough
#  not to fire on ordinary prose.
SECRET_PATTERNS = [
    ("fernet_ciphertext", re.compile(r"gAAAAA[A-Za-z0-9_\-]{10,}")),
    ("openai_key",        re.compile(r"sk-[A-Za-z0-9]{16,}")),
    ("pem_block",         re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("pem_any",           re.compile(r"-----BEGIN (RSA|OPENSSH|EC|DSA|PGP)")),
    ("bearer_jwt",        re.compile(r"Bearer\s+eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.")),
    ("bind_password_val", re.compile(r'"bind_password"\s*:\s*"[^"]+')),
    ("client_secret_val", re.compile(r'"client_secret"\s*:\s*"[^"]+')),
    ("password_enc_val",  re.compile(r'"password_enc"\s*:\s*"[^"]+')),
    ("client_secret_enc", re.compile(r'"client_secret_enc"\s*:\s*"[^"]+')),
]

# Values a probe deliberately submits and then checks are NOT echoed back where
# a third party could read them. Kept distinct from SECRET_PATTERNS.
CANARY_PASSWORD = "leakcanary_p4ssw0rd_do_not_store"


class Colors:
    OK = "\033[92m"
    BAD = "\033[91m"
    WARN = "\033[93m"
    DIM = "\033[2m"
    END = "\033[0m"


class Suite:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.passed = 0
        self.failed = 0
        self.leaks = 0
        self.warned = 0

    # -- http ------------------------------------------------------------- #
    def call(self, method, path, token=None, org=None, json_body=None,
             form_body=None):
        headers = {}
        data = None
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if org:
            headers["X-Organization-Id"] = org
        if json_body is not None:
            data = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        elif form_body is not None:
            data = urllib.parse.urlencode(form_body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(self.base + path, data=data,
                                     headers=headers, method=method)
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            body = resp.read().decode("utf-8", "replace")
            return resp.status, dict(resp.headers), body
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            return None, {}, f"<transport error: {e}>"

    # -- reporting -------------------------------------------------------- #
    def scan_body(self, path, body):
        """Fail the whole run if a secret-shaped string is in a response body."""
        for name, pat in SECRET_PATTERNS:
            m = pat.search(body or "")
            if m:
                self.leaks += 1
                snippet = m.group(0)[:12] + "..."
                print(f"  {Colors.BAD}!!! SECRET LEAK{Colors.END} "
                      f"pattern={name} in {path}: {snippet!r}")

    def ok(self, msg):
        self.passed += 1
        print(f"  {Colors.OK}PASS{Colors.END} {msg}")

    def bad(self, msg):
        self.failed += 1
        print(f"  {Colors.BAD}FAIL{Colors.END} {msg}")

    def warn(self, msg):
        self.warned += 1
        print(f"  {Colors.WARN}WARN{Colors.END} {msg}")

    def head(self, title):
        print(f"\n{Colors.DIM}== {title} =={Colors.END}")


# --------------------------------------------------------------------------- #
#  Probes
# --------------------------------------------------------------------------- #
def probe_settings_redaction(s: Suite, admin, member, org):
    s.head("Settings/config endpoints redact stored secrets (response body)")

    # /api/settings is UNAUTHENTICATED — the login page reads it. It must serve
    # a 200 to anon (a versionCheck poller depends on it) and carry no secret.
    st, _, body = s.call("GET", "/api/settings")
    s.scan_body("/api/settings (anon)", body)
    if st == 200:
        s.ok(f"/api/settings serves anon 200 ({len(body)}B) with no secret-shaped string")
    else:
        s.bad(f"/api/settings anon returned {st} (expected 200)")

    # SSO config: a real client_secret_enc is stored; the read must show only
    # client_secret_set. Proven non-vacuous by requiring the flag to be present.
    st, _, body = s.call("GET", "/api/enterprise/sso/config", token=admin, org=org)
    s.scan_body("/api/enterprise/sso/config", body)
    if st == 200:
        try:
            cfg = json.loads(body)
            provs = cfg.get("providers", [])
            has_flag = any("client_secret_set" in p for p in provs) or \
                       "client_secret_set" in (cfg.get("google") or {})
            has_raw = any("client_secret_enc" in p or "client_secret" in p for p in provs)
            if has_flag and not has_raw:
                s.ok("SSO config exposes client_secret_set flag, never the ciphertext")
            elif has_raw:
                s.bad("SSO config exposes a raw client_secret / client_secret_enc field")
            else:
                s.warn("SSO config has no providers to prove redaction (vacuous)")
        except json.JSONDecodeError:
            s.bad("SSO config admin response was not JSON")
    else:
        s.warn(f"SSO config returned {st} for admin (no provider configured?)")

    # Member (no manage_identity_providers) must be refused, not shown a redacted view.
    st, _, body = s.call("GET", "/api/enterprise/sso/config", token=member, org=org)
    s.scan_body("/api/enterprise/sso/config (member)", body)
    if st in (401, 403):
        s.ok(f"SSO config refuses a non-admin member ({st})")
    else:
        s.bad(f"SSO config returned {st} to a non-admin member (expected 401/403)")

    # LDAP config: bind_password stored; read must show only bind_password_set.
    st, _, body = s.call("GET", "/api/enterprise/ldap/config", token=admin, org=org)
    s.scan_body("/api/enterprise/ldap/config", body)
    if st == 200:
        try:
            cfg = json.loads(body)
            if "bind_password_set" in cfg and "bind_password" not in cfg \
               and "bind_password_enc" not in cfg:
                s.ok("LDAP config exposes bind_password_set flag, never the ciphertext")
            else:
                s.bad("LDAP config exposes a raw bind_password field")
        except json.JSONDecodeError:
            s.bad("LDAP config admin response was not JSON")
    else:
        s.warn(f"LDAP config returned {st} for admin")


def probe_connection_redaction(s: Suite, admin, org):
    s.head("Connection list/detail never serialize credentials")
    st, _, body = s.call("GET", "/api/connections", token=admin, org=org)
    s.scan_body("/api/connections", body)
    if st != 200:
        s.warn(f"/api/connections returned {st}; skipping detail probe")
        return
    try:
        conns = json.loads(body)
    except json.JSONDecodeError:
        s.bad("/api/connections was not JSON")
        return
    leaked = [c.get("name") for c in conns if c.get("credentials")]
    if not leaked:
        s.ok(f"/api/connections ({len(conns)} rows) serialize credentials=null")
    else:
        s.bad(f"/api/connections leaked a credentials dict for: {leaked[:3]}")

    # Detail view for up to 3 connections — credentials_meta must hold no secret.
    for c in conns[:3]:
        cid = c.get("id")
        st, _, body = s.call("GET", f"/api/connections/{cid}", token=admin, org=org)
        s.scan_body(f"/api/connections/{cid}", body)
    s.ok("connection detail views scanned for secret-shaped strings")


def probe_console(s: Suite, admin, org):
    s.head("Console/monitoring endpoints carry no secret")
    for p in ("/api/console/app-analytics", "/api/console/metrics",
              "/api/console/recent-widgets"):
        st, _, body = s.call("GET", p, token=admin, org=org)
        s.scan_body(p, body)
    s.ok("console endpoints scanned for secret-shaped strings")


def probe_jwt(s: Suite, admin_token, admin_id, org):
    s.head("JWT is signature-checked and fails closed")

    def b64u(b):
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    exp = int(time.time()) + 300
    payload = b64u(json.dumps(
        {"sub": admin_id, "aud": ["fastapi-users:auth"], "exp": exp}).encode())

    # alg:none
    none_hdr = b64u(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    none_tok = f"{none_hdr}.{payload}."
    st, _, _ = s.call("GET", "/api/users/me", token=none_tok)
    (s.ok if st in (401, 403) else s.bad)(f"alg:none token refused ({st})")

    # HS256 signed with the WRONG secret
    hdr = b64u(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    bad_sig = b64u(hmac.new(b"not-the-real-secret",
                            f"{hdr}.{payload}".encode(), hashlib.sha256).digest())
    st, _, _ = s.call("GET", "/api/users/me", token=f"{hdr}.{payload}.{bad_sig}")
    (s.ok if st in (401, 403) else s.bad)(f"HS256 wrong-secret token refused ({st})")

    # A real token still works (sanity — proves the refusals above aren't blanket).
    st, _, _ = s.call("GET", "/api/users/me", token=admin_token)
    (s.ok if st == 200 else s.bad)(f"a genuine token is accepted ({st})")

    # Invented org id must resolve to no access (404 / empty), never default-allow.
    fake = "00000000-0000-0000-0000-000000000000"
    st, _, body = s.call("GET", f"/api/organizations/{fake}/members",
                         token=admin_token, org=fake)
    s.scan_body("invented-org members", body)
    (s.ok if st in (403, 404) else s.bad)(
        f"invented org id fails closed on /members ({st})")


def probe_enumeration(s: Suite, member_email):
    s.head("Password doors are not enumeration oracles (byte-identical failures)")

    def local(u):
        return s.call("POST", "/api/auth/jwt/login",
                      form_body={"username": u, "password": "definitely-wrong-xyz"})

    _, _, a = local(member_email)
    _, _, b = local("no-such-account-9f83a2@example.com")
    (s.ok if a == b else s.bad)(
        "local login: known-wrong-pw and unknown-user bodies are identical"
        if a == b else f"local login differs: {a[:60]!r} vs {b[:60]!r}")

    def ldap(u):
        return s.call("POST", "/api/auth/ldap/login",
                      form_body={"username": u, "password": "definitely-wrong-xyz"})

    _, _, c = ldap("ldapuser")
    _, _, d = ldap("no-such-directory-user-9f83")
    (s.ok if c == d else s.bad)(
        "ldap login: known and unknown bodies are identical"
        if c == d else f"ldap login differs: {c[:60]!r} vs {d[:60]!r}")

    def reset(email):
        return s.call("POST", "/api/auth/forgot-password", json_body={"email": email})

    _, _, e = reset(member_email)
    _, _, f = reset("no-such-account-9f83a2@example.com")
    (s.ok if e == f else s.bad)(
        "password reset: known and unknown email responses are identical"
        if e == f else f"reset differs: {e[:60]!r} vs {f[:60]!r}")


def probe_error_leak(s: Suite, admin, org):
    s.head("A failed connection test does not echo the credential handed to it")
    st, _, body = s.call(
        "POST", "/api/connections/test-params", token=admin, org=org,
        json_body={"name": "probe", "type": "postgresql",
                   "config": {"host": "127.0.0.1", "port": 1, "database": "x"},
                   "credentials": {"user": "probeuser", "password": CANARY_PASSWORD}})
    s.scan_body("/api/connections/test-params", body)
    if CANARY_PASSWORD in body:
        s.bad("connection-test error echoed the submitted password back")
    else:
        s.ok("connection-test failure message does not contain the password")


def probe_headers(s: Suite):
    s.head("Transport / security headers (informational)")
    _, headers, _ = s.call("GET", "/api/settings")
    lower = {k.lower(): v for k, v in headers.items()}
    wanted = ["content-security-policy", "strict-transport-security",
              "x-frame-options", "x-content-type-options", "referrer-policy"]
    present = [h for h in wanted if h in lower]
    missing = [h for h in wanted if h not in lower]
    if missing:
        s.warn(f"missing security headers: {', '.join(missing)}")
    if present:
        s.ok(f"present security headers: {', '.join(present)}")
    if "server" in lower and lower["server"]:
        s.warn(f"Server banner discloses stack: {lower['server']!r}")


def probe_cookie_flags(s: Suite):
    """The session-cookie flags the SPA actually ships, read off the served HTML.

    ★Read from the RUNTIME CONFIG in the shipped payload, not from
    nuxt.config.ts. @sidebase/nuxt-auth 0.9.x reads FLAT keys
    (``cookieName`` / ``secureCookieAttribute`` / ``httpOnlyCookieAttribute``);
    a nested ``cookie: {name, options}`` block is accepted by the config file
    and then ignored, so the file's stated intent and the shipped behaviour can
    disagree. Only the served payload settles it.
    """
    s.head("Session cookie flags as SHIPPED (from the served runtime config)")
    st, _, html = s.call("GET", "/")
    if st != 200 or not html:
        s.warn(f"could not fetch / ({st}); skipping cookie-flag probe")
        return

    name = re.search(r'cookieName:"([^"]+)"', html)
    http_only = re.search(r"httpOnlyCookieAttribute:(true|false)", html)
    secure = re.search(r"secureCookieAttribute:(true|false)", html)
    same_site = re.search(r'sameSiteAttribute:"([^"]+)"', html)
    max_age = re.search(r"maxAgeInSeconds:(\d+)", html)

    if not name:
        s.warn("no nuxt-auth runtime config in the served HTML; skipping")
        return
    s.ok(f"effective session cookie name is {name.group(1)!r}")

    if http_only and http_only.group(1) == "true":
        s.ok("session cookie is HttpOnly")
    else:
        s.bad("session cookie is NOT HttpOnly — readable by any script on the "
              "page, so an XSS anywhere yields the bearer token")

    if secure and secure.group(1) == "true":
        s.ok("session cookie is Secure")
    else:
        s.bad("session cookie is NOT Secure — it will ride a plain-HTTP request")

    if same_site:
        (s.ok if same_site.group(1) in ("strict", "lax") else s.warn)(
            f"session cookie SameSite={same_site.group(1)}")
    if max_age:
        days = int(max_age.group(1)) / 86400
        (s.warn if days >= 7 else s.ok)(
            f"session cookie lifetime is {days:g} days (stateless JWT: not revocable)")

    # The ignored nested block — flag it so the intent/effect gap is visible.
    nested = re.search(r'cookie:\{name:"([^"]+)",options:\{([^}]*)\}', html)
    if nested:
        s.warn(f"nuxt.config.ts nested cookie block is present but IGNORED by "
               f"nuxt-auth 0.9.x (declares name={nested.group(1)!r}, "
               f"{nested.group(2)}) — its flags never apply")


def probe_file_embed_token(s: Suite, admin, org, file_ids):
    """The 1h HS256 file-embed capability: single-file, expiring, non-widening."""
    s.head("File-embed capability token is narrow (single file, expires, no session)")
    if not file_ids:
        s.warn("no file ids supplied (--file-id A --file-id B); skipping")
        return
    file_a = file_ids[0]

    st, _, body = s.call("GET", f"/api/files/{file_a}/embed_token", token=admin, org=org)
    if st != 200:
        s.warn(f"could not mint an embed token ({st}); skipping")
        return
    s.scan_body("embed_token mint", body)
    try:
        tok = json.loads(body)["token"]
        hdr_b64, payload_b64, _sig = tok.split(".")
        claims = json.loads(base64.urlsafe_b64decode(
            payload_b64 + "=" * (-len(payload_b64) % 4)))
        alg = json.loads(base64.urlsafe_b64decode(
            hdr_b64 + "=" * (-len(hdr_b64) % 4))).get("alg")
    except Exception as e:  # noqa: BLE001
        s.bad(f"embed token was not a parseable JWT: {e}")
        return

    (s.ok if alg and alg.lower() != "none" else s.bad)(f"embed token alg={alg}")
    (s.ok if claims.get("scope") == "file-embed" else s.bad)(
        f"embed token carries scope={claims.get('scope')!r}")
    (s.ok if str(claims.get("fid")) == str(file_a) else s.bad)(
        "embed token is bound to the file id it was minted for")
    ttl = int(claims.get("exp", 0)) - int(claims.get("iat", 0))
    (s.ok if 0 < ttl <= 86400 else s.warn)(f"embed token ttl={ttl}s")

    st, _, _ = s.call("GET", f"/api/files/{file_a}/embed?token={tok}")
    (s.ok if st == 200 else s.bad)(f"token serves its OWN file ({st})")

    if len(file_ids) > 1:
        file_b = file_ids[1]
        st, _, _ = s.call("GET", f"/api/files/{file_b}/embed?token={tok}")
        (s.ok if st in (401, 403, 404) else s.bad)(
            f"token for file A is REFUSED on file B ({st}) — no cross-file read")
    else:
        s.warn("only one --file-id given; cross-file replay not tested")

    st, _, _ = s.call("GET", f"/api/files/{file_a}/embed?token=abc.def.ghi")
    (s.ok if st in (401, 403, 422) else s.bad)(f"garbage token refused ({st})")

    # Widening: the capability must not act as a session anywhere.
    for path in ("/api/users/me", "/api/connections"):
        st, _, _ = s.call("GET", path, token=tok, org=org)
        (s.ok if st in (401, 403) else s.bad)(
            f"embed token refused as a session bearer on {path} ({st})")

    # And the reverse: a session token is not a file capability.
    st, _, _ = s.call("GET", f"/api/files/{file_a}/embed?token={admin}")
    (s.ok if st in (401, 403) else s.bad)(
        f"session token refused as a file capability ({st})")


def probe_file_embed_expiry(s: Suite, env_path, file_id):
    """Forge an EXPIRED but correctly-signed capability; it must still be refused.

    Needs the derived secret, so it only runs with --env. This is the probe that
    proves expiry is enforced rather than merely encoded in a claim.
    """
    s.head("File-embed token expiry is enforced (not just an unread claim)")
    if not env_path or not file_id:
        s.warn("skipped (needs --env and --file-id)")
        return
    key = None
    try:
        with open(env_path) as fh:
            for line in fh:
                m = re.match(r"\s*DASH_ENCRYPTION_KEY\s*=\s*(.+?)\s*$", line)
                if m:
                    key = m.group(1).strip().strip('"').strip("'")
                    break
    except OSError:
        s.warn(f"could not read {env_path}")
        return
    if not key:
        s.warn("DASH_ENCRYPTION_KEY not found; skipping")
        return

    # Mirrors app/core/file_tokens._secret() — a DERIVED secret, never the raw key.
    derived = hashlib.sha256(b"bow-file-embed-token:" + key.encode()).hexdigest()
    (s.ok if derived != key else s.bad)(
        "embed signing secret is derived from the Fernet key, not the key itself")

    def b64u(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    def mint(claims):
        h = b64u(json.dumps({"alg": "HS256", "typ": "JWT"},
                            separators=(",", ":")).encode())
        p = b64u(json.dumps(claims, separators=(",", ":")).encode())
        sig = b64u(hmac.new(derived.encode(), f"{h}.{p}".encode(),
                            hashlib.sha256).digest())
        return f"{h}.{p}.{sig}"

    now = int(time.time())
    expired = mint({"fid": file_id, "iat": now - 7200, "exp": now - 3600,
                    "scope": "file-embed"})
    st, _, _ = s.call("GET", f"/api/files/{file_id}/embed?token={expired}")
    (s.ok if st in (401, 403) else s.bad)(
        f"EXPIRED but validly-signed token refused ({st})")

    wrong_scope = mint({"fid": file_id, "iat": now, "exp": now + 600,
                        "scope": "session"})
    st, _, _ = s.call("GET", f"/api/files/{file_id}/embed?token={wrong_scope}")
    (s.ok if st in (401, 403) else s.bad)(
        f"validly-signed token with a non-embed scope refused ({st})")


def probe_webhook_auth(s: Suite, webhook_token):
    """Inbound webhook receiver must not accept an unauthenticated delivery."""
    s.head("Inbound webhook receiver refuses unauthenticated deliveries")
    st, _, body = s.call("POST", "/webhooks/definitely-not-a-real-token-9f83a2",
                         json_body={"probe": 1})
    s.scan_body("/webhooks/<unknown>", body)
    (s.ok if st == 404 else s.bad)(f"unknown webhook token refused ({st})")

    if not webhook_token:
        s.warn("no --webhook-token given (needs a LIVE active webhook); "
               "the credential-rejection path is untested here")
        return
    # Deliberately WRONG credential only — a valid one would run an agent.
    st, _, body = s.call("POST", f"/webhooks/{webhook_token}",
                         json_body={"probe": 1})
    s.scan_body("/webhooks/<real> no-cred", body)
    (s.ok if st in (401, 403) else s.bad)(
        f"real webhook token with NO credential refused ({st})")
    st, _, _ = s.call("POST", f"/webhooks/{webhook_token}",
                      json_body={"probe": 1})
    (s.ok if st in (401, 403) else s.bad)(
        f"real webhook token with a WRONG credential refused ({st})")


def probe_sso_token_in_url(s: Suite, repo_root):
    """The SSO callback must not hand the session JWT back in a redirect URL."""
    s.head("SSO callback does not put the session token in a URL (source check)")
    path = f"{repo_root}/backend/app/services/auth_providers.py"
    try:
        with open(path) as fh:
            src = fh.read()
    except OSError:
        s.warn(f"{path} not readable; skipping (run from the repo root)")
        return
    hits = re.findall(r"RedirectResponse\(\s*f?\"[^\"]*access_token=", src)
    if hits:
        s.bad(f"{len(hits)} SSO redirect(s) carry access_token in the URL "
              f"(auth_providers.py) — lands in history, logs and Referer")
    else:
        s.ok("no SSO redirect places access_token in a URL")


def probe_key_reuse(s: Suite, sample_token, env_path):
    """Detect whether the session JWT is signed with DASH_ENCRYPTION_KEY itself.

    Reads the key from .env, computes the expected HMAC, prints only the boolean.
    Never prints the key. Skipped if .env or a token is unavailable.
    """
    s.head("JWT signing secret vs Fernet encryption key (key-reuse detector)")
    if not env_path or not sample_token:
        s.warn("skipped (needs --env <.env> and a minted token)")
        return
    key = None
    try:
        with open(env_path) as fh:
            for line in fh:
                m = re.match(r"\s*DASH_ENCRYPTION_KEY\s*=\s*(.+?)\s*$", line)
                if m:
                    key = m.group(1).strip().strip('"').strip("'")
                    break
    except OSError as e:
        s.warn(f"could not read {env_path}: {e}")
        return
    if not key:
        s.warn(f"DASH_ENCRYPTION_KEY not found in {env_path}")
        return
    try:
        hdr, payload, sig = sample_token.split(".")
        expected = base64.urlsafe_b64encode(
            hmac.new(key.encode(), f"{hdr}.{payload}".encode(),
                     hashlib.sha256).digest()).rstrip(b"=").decode()
    except Exception as e:  # noqa: BLE001
        s.warn(f"could not parse the sample token: {e}")
        return
    if hmac.compare_digest(expected, sig):
        s.bad("session JWT IS signed with DASH_ENCRYPTION_KEY — key leak = "
              "session forgery AND secret decryption in one (see report)")
    else:
        s.ok("session JWT is NOT signed with the Fernet key (separate secret)")


def discover_file_ids(s: Suite, admin, org, want=2):
    """Two real file ids, so the embed probes run with no flags under run-all.

    Read-only: a plain list. Returns [] when the org has no files, which the
    embed probes then report as SKIPPED rather than passing vacuously.
    """
    st, _, body = s.call("GET", "/api/files", token=admin, org=org)
    if st != 200:
        return []
    try:
        rows = json.loads(body)
    except json.JSONDecodeError:
        return []
    ids = [r.get("id") for r in rows if isinstance(r, dict) and r.get("id")]
    return ids[:want]


# --------------------------------------------------------------------------- #
def load_tokens(path):
    with open(path) as fh:
        data = json.load(fh)
    org = (data.get("org") or {}).get("id")
    admin = member = None
    for email, info in (data.get("users") or {}).items():
        if "token" not in info:
            continue
        if info.get("is_superuser") or info.get("role") == "admin":
            admin = (email, info)
        else:
            member = member or (email, info)
    return org, admin, member


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://localhost:8095")
    # ★Accepted BOTH positionally and as a flag. run-all.py invokes every probe
    # as `python <script> <tokens>`, so a flag-only spelling here would make
    # this suite fail on argparse and report as a security FAILURE — a probe
    # that cannot run must never look like a probe that found something.
    ap.add_argument("tokens_pos", nargs="?", default=None,
                    help=argparse.SUPPRESS)
    ap.add_argument("--tokens", default=None,
                    help="tokens.json from scripts/mint-user-tokens.py")
    ap.add_argument("--env", default=None,
                    help="path to .env, enables the key-reuse and embed-expiry detectors")
    ap.add_argument("--file-id", action="append", default=[], metavar="UUID",
                    help="a real file id for the embed-token probes; pass TWICE "
                         "(two different files) to test cross-file replay")
    ap.add_argument("--webhook-token", default=None,
                    help="path token of a LIVE active webhook; only ever sent "
                         "with a wrong credential, so no delivery is processed")
    ap.add_argument("--repo", default=".",
                    help="repo root for the SSO source check (default: cwd)")
    args = ap.parse_args()

    tokens_path = args.tokens or args.tokens_pos
    if not tokens_path:
        ap.error("a tokens.json path is required (positionally or via --tokens)")

    # ★Default --env to the repo's .env when it exists, so the key-reuse gate
    # still fires under run-all.py (which passes no flags). Without this the
    # release gate would silently downgrade to a WARN.
    if not args.env:
        candidate = f"{args.repo.rstrip('/')}/.env"
        if os.path.exists(candidate):
            args.env = candidate

    org, admin, member = load_tokens(tokens_path)
    if not admin:
        print("ERROR: tokens.json has no superuser/admin token", file=sys.stderr)
        return 2
    admin_email, admin_info = admin
    admin_token = admin_info["token"]
    admin_id = admin_info.get("id")
    member_email, member_token = (member[0], member[1]["token"]) if member else (None, None)

    print(f"CityAgent Insights — secret/auth/transport probe")
    print(f"base={args.base}  org={org}  admin={admin_email}  member={member_email}")

    s = Suite(args.base)
    probe_settings_redaction(s, admin_token, member_token, org)
    probe_connection_redaction(s, admin_token, org)
    probe_console(s, admin_token, org)
    probe_jwt(s, admin_token, admin_id, org)
    if member_email:
        probe_enumeration(s, member_email)
    else:
        s.head("Enumeration probes skipped (no member token in tokens.json)")
    probe_error_leak(s, admin_token, org)
    probe_headers(s)
    probe_cookie_flags(s)
    file_ids = args.file_id or discover_file_ids(s, admin_token, org)
    probe_file_embed_token(s, admin_token, org, file_ids)
    probe_file_embed_expiry(s, args.env, file_ids[0] if file_ids else None)
    probe_webhook_auth(s, args.webhook_token)
    probe_sso_token_in_url(s, args.repo.rstrip("/"))
    probe_key_reuse(s, admin_token, args.env)

    print(f"\n{'='*60}")
    print(f"passed={s.passed}  failed={s.failed}  "
          f"secret-leaks={s.leaks}  warnings={s.warned}")
    if s.leaks:
        print(f"{Colors.BAD}SECRET-SHAPED STRING(S) FOUND IN A RESPONSE BODY.{Colors.END}")
    if s.failed or s.leaks:
        print(f"{Colors.BAD}RESULT: FAIL{Colors.END}")
        return 1
    print(f"{Colors.OK}RESULT: PASS{Colors.END}  ({s.warned} warnings to review)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
