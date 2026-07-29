"""The address we hand the provider must be the one the browser can come back to.

`connection_oauth.py` built the OAuth `redirect_uri` from the configured
`base_url`, whose shipped default is the placeholder `http://0.0.0.0:3000`:

    redirect_uri = f"{settings.dash_config.base_url}/api/connections/oauth/callback"

`0.0.0.0` is a bind address, not a destination — no browser can reach it, and
Entra will not accept it as a registered redirect. So every Microsoft connector
sign-in (SharePoint, OneDrive, Outlook Mail) sends the user to the provider,
the user authenticates, and then has nowhere to return to.

★This is UPSTREAM's bug, not the fork's — verified byte-identical in
v0.0.490 AND in v0.0.495, five releases later:

    bow-config.yaml            base_url: http://0.0.0.0:3000
    bow_config.py:245          default="http://0.0.0.0:3000"
    connection_oauth.py:220    redirect_uri = f"{base_url}/api/..."
    connection_oauth.py:332    redirect_uri = f"{base_url}/api/..."

★★And upstream already wrote the fix and did not use it here.
`app/core/base_url.py` derives the externally-visible address from the request
(honouring X-Forwarded-*) and explicitly names `http://0.0.0.0:3000` as
"unconfigured". SSO login uses it (`auth_providers.py`). The connector OAuth
path never adopted it.

Two more consequences of the same placeholder, fixed here as well:
  - `_cookie_secure()` decided the PKCE cookie's Secure flag by asking whether
    base_url starts with https. With the placeholder it always said NO, so on a
    real https deployment the verifier cookie shipped without Secure.
  - the error redirect sent the user to the placeholder host.

★★★THE TRAP this file mainly guards: the redirect_uri sent at authorize and
the one sent at token exchange must be BYTE-IDENTICAL or the provider rejects
the exchange — with an error that does not say which part differs. Deriving it
twice invites drift (a proxy, a www. prefix, a different Host on the callback).
So it is derived ONCE at authorize and carried in the signed state, and the
callback reuses that exact string.

FORK PATCH: a future upstream port will reintroduce the config-only version in
both places. These tests must fail loudly if it does.
"""
import inspect
import re

import pytest

from app.core.base_url import derive_base_url
from app.routes import connection_oauth as co
from app.settings.config import settings

PLACEHOLDERS = ("http://0.0.0.0:3000", "http://0.0.0.0:8000")


class FakeURL:
    def __init__(self, scheme="http", netloc="localhost:8095"):
        self.scheme = scheme
        self.netloc = netloc


class FakeRequest:
    """Only what the derivation actually reads."""

    def __init__(self, host="localhost:8095", scheme="http", headers=None):
        h = {"host": host}
        h.update({k.lower(): v for k, v in (headers or {}).items()})
        self.headers = h
        self.url = FakeURL(scheme=scheme, netloc=host)
        self.cookies = {}


@pytest.fixture(autouse=True)
def _placeholder_config(monkeypatch):
    """Default every test to the SHIPPED config, which is the broken one."""
    monkeypatch.setattr(settings.dash_config, "base_url", "http://0.0.0.0:3000")


# --- the base the connector path resolves ----------------------------------

def test_the_shipped_default_really_is_unusable():
    """★Guard the guard. If upstream ever ships a real default, this whole file
    is arguing about nothing and should say so instead of passing quietly."""
    assert settings.dash_config.base_url in PLACEHOLDERS


def test_a_plain_local_browser_gets_its_own_address():
    base = co._public_base(FakeRequest(host="localhost:8095"))
    assert base == "http://localhost:8095"
    assert "0.0.0.0" not in base


def test_a_real_domain_behind_a_proxy_is_honoured():
    req = FakeRequest(
        host="app-internal:3000",
        headers={"x-forwarded-proto": "https", "x-forwarded-host": "cowork.citygpt.xyz"},
    )
    assert co._public_base(req) == "https://cowork.citygpt.xyz"


def test_a_proxy_chain_uses_the_address_the_client_saw():
    """Left-most entry is the external client, not the last hop."""
    req = FakeRequest(
        host="internal",
        headers={
            "x-forwarded-proto": "https,http",
            "x-forwarded-host": "cowork.citygpt.xyz, internal-lb",
        },
    )
    assert co._public_base(req) == "https://cowork.citygpt.xyz"


def test_an_operator_who_configured_a_real_address_still_wins(monkeypatch):
    """Deriving from the request must not override a deliberate setting — some
    deployments terminate on a host that is not the public name."""
    monkeypatch.setattr(settings.dash_config, "base_url", "https://insight.citygpt.xyz")
    req = FakeRequest(host="something-else:3000")
    assert co._public_base(req) == "https://insight.citygpt.xyz"


def test_a_configured_address_keeps_no_trailing_slash(monkeypatch):
    """A trailing slash would produce '…//api/connections/oauth/callback', which
    is a DIFFERENT string to Entra and fails the exact-match rule."""
    monkeypatch.setattr(settings.dash_config, "base_url", "https://cowork.citygpt.xyz/")
    assert co._public_base(FakeRequest()) == "https://cowork.citygpt.xyz"


def test_the_placeholder_is_never_returned():
    for host in ("localhost:8095", "cowork.citygpt.xyz", "10.0.0.4:3000"):
        assert co._public_base(FakeRequest(host=host)) not in PLACEHOLDERS


def test_it_agrees_with_the_shared_helper_when_a_proxy_sends_both_headers():
    """The fork must not grow a gratuitously different derivation. On the fully
    specified case — both forwarded headers present — it matches what /mcp and
    the Office add-in resolve to."""
    req = FakeRequest(
        host="internal:3000",
        headers={"x-forwarded-proto": "https", "x-forwarded-host": "cowork.citygpt.xyz"},
    )
    assert co._public_base(req) == derive_base_url(req)


def test_a_proxy_that_sends_only_the_scheme_still_yields_https():
    """★★A deliberate divergence, and the reason this helper exists.

    `derive_base_url` requires BOTH `X-Forwarded-Proto` and `X-Forwarded-Host`
    before it trusts either. Cloudflare and most reverse proxies send only the
    proto header — the Host they forward is already the public name. Under the
    shared helper an https deployment therefore resolves to `http://`, which
    Entra refuses as a redirect and which silently drops Secure from the PKCE
    cookie. Scheme and host are resolved independently here.

    If the shared helper is ever fixed the same way, this test still passes and
    the one above still passes; nothing here depends on the divergence lasting.
    """
    req = FakeRequest(host="cowork.citygpt.xyz", headers={"x-forwarded-proto": "https"})
    assert co._public_base(req) == "https://cowork.citygpt.xyz"
    assert derive_base_url(req).startswith("http://"), (
        "the shared helper's behaviour changed — re-read this test's reasoning"
    )


# --- the redirect uri itself -----------------------------------------------

def test_the_redirect_uri_is_the_documented_callback_path():
    req = FakeRequest(host="cowork.citygpt.xyz", headers={"x-forwarded-proto": "https"})
    assert co._callback_redirect_uri(req) == (
        "https://cowork.citygpt.xyz/api/connections/oauth/callback"
    )


def test_the_redirect_uri_is_never_the_placeholder_host():
    assert "0.0.0.0" not in co._callback_redirect_uri(FakeRequest())


# --- authorize and callback must send the SAME string ----------------------

def test_the_signed_state_carries_the_redirect_uri():
    sent = "https://cowork.citygpt.xyz/api/connections/oauth/callback"
    state = co._encode_state(connection_id="c1", user_id="u1", redirect_uri=sent)
    assert co._decode_state(state).get("ru") == sent


def test_the_callback_reuses_the_exact_string_even_from_another_host():
    """★★★The whole point. Authorize happens on the public domain; suppose the
    callback arrives with a different Host (proxy rewrite, www., internal LB).
    Re-deriving would send a different redirect_uri to the token endpoint and
    the exchange would fail with an error that names nothing useful."""
    authorize_req = FakeRequest(
        host="internal:3000",
        headers={"x-forwarded-proto": "https", "x-forwarded-host": "cowork.citygpt.xyz"},
    )
    sent = co._callback_redirect_uri(authorize_req)
    state = co._encode_state(connection_id="c1", user_id="u1", redirect_uri=sent)

    callback_req = FakeRequest(host="www.cowork.citygpt.xyz")  # deliberately different
    used = co._redirect_uri_for_exchange(co._decode_state(state), callback_req)

    assert used == sent == "https://cowork.citygpt.xyz/api/connections/oauth/callback"


def test_a_state_from_before_this_change_still_completes():
    """States live 10 minutes, so a deploy can land mid-flow. No 'ru' claim must
    fall back to derivation rather than crash the user's sign-in."""
    legacy = co._encode_state(connection_id="c1", user_id="u1")
    req = FakeRequest(host="cowork.citygpt.xyz", headers={"x-forwarded-proto": "https"})
    assert co._redirect_uri_for_exchange(co._decode_state(legacy), req) == (
        "https://cowork.citygpt.xyz/api/connections/oauth/callback"
    )


def test_the_return_path_still_rides_along():
    """The existing feature must survive the new claim."""
    state = co._encode_state(
        connection_id="c1", user_id="u1", return_to="/agents/new",
        redirect_uri="https://x.test/api/connections/oauth/callback",
    )
    payload = co._decode_state(state)
    assert payload.get("rt") == "/agents/new"
    assert payload.get("ru") == "https://x.test/api/connections/oauth/callback"


def test_the_state_is_still_signed_and_still_binds_the_user():
    """Adding a claim must not weaken what state is FOR."""
    state = co._encode_state(
        connection_id="c1", user_id="u1",
        redirect_uri="https://x.test/api/connections/oauth/callback",
    )
    payload = co._decode_state(state)
    assert payload["cid"] == "c1" and payload["uid"] == "u1"
    assert payload["aud"] == co._STATE_AUDIENCE
    with pytest.raises(Exception):
        co._decode_state(state + "x")


# --- the PKCE cookie ---------------------------------------------------------

def test_the_verifier_cookie_is_secure_on_an_https_deployment():
    """★With the placeholder this always said False, so the PKCE verifier
    shipped without Secure on real https installs."""
    req = FakeRequest(host="internal", headers={"x-forwarded-proto": "https"})
    assert co._cookie_secure(req) is True


def test_the_verifier_cookie_is_not_secure_on_plain_local_http():
    """Marking it Secure over http would stop the browser sending it back and
    break local development with 'Missing PKCE code verifier'."""
    assert co._cookie_secure(FakeRequest(host="localhost:8095")) is False


def test_a_configured_https_address_still_marks_the_cookie_secure(monkeypatch):
    monkeypatch.setattr(settings.dash_config, "base_url", "https://insight.citygpt.xyz")
    assert co._cookie_secure(FakeRequest(host="internal")) is True


# --- what we DISPLAY must equal what we SEND --------------------------------

def test_the_value_shown_to_an_admin_is_the_value_we_send():
    """★★★The recurring defect in this codebase is a claim nothing enforces.
    A redirect URI printed on the connector form for the admin to paste into
    Entra is worthless if it is a hardcoded template that can drift from the
    string actually sent. Both must come from one function."""
    req = FakeRequest(host="cowork.citygpt.xyz", headers={"x-forwarded-proto": "https"})
    assert co.public_callback_url(req) == co._callback_redirect_uri(req)


# --- the shape --------------------------------------------------------------

def _code_of(*objs):
    """Source with comments and docstrings stripped — otherwise these assertions
    match the explanations written above them (this has bitten four times)."""
    out = []
    for o in objs:
        src = inspect.getsource(o)
        src = re.sub(r'"""(?:.|\n)*?"""', "", src)
        out.append("\n".join(l.split("#", 1)[0] for l in src.splitlines()))
    return "\n".join(out)


def test_the_config_only_redirect_is_gone_from_both_places():
    """FORK PATCH guard. An upstream port that restores either line fails here
    instead of silently reinstating a redirect no browser can reach."""
    code = _code_of(co.oauth_authorize, co.oauth_callback)
    assert "base_url}/api/connections/oauth/callback" not in code
    assert "dash_config.base_url" not in code


def test_both_handlers_go_through_the_one_helper():
    code = _code_of(co.oauth_authorize)
    assert "_callback_redirect_uri(request)" in code
    callback = _code_of(co.oauth_callback)
    assert "_redirect_uri_for_exchange(" in callback


def test_the_error_redirect_does_not_strand_the_user_on_the_placeholder():
    """A failed sign-in must return the person to the site they came from."""
    code = _code_of(co.oauth_callback)
    assert "_public_base(request)" in code
