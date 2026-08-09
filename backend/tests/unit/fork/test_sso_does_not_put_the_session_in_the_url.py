"""A completed SSO login must not carry its session token in the query string.

MEASURED DEFECT (2026-08-09, static — both ends of the path read).
`auth_providers.py` finished every Google and OIDC login with:

    RedirectResponse(f"/users/sign-in?access_token={jwt_token}&email={user.email}")

A query string is not a private channel:

  - it is written to the web server's access log and to every proxy in front of
    the app;
  - it stays in browser history;
  - it is sent in the `Referer` header of any external resource the landing page
    loads — and this app sets no Referrer-Policy, so that path is live.

The token is a 7-day, stateless, unrevocable session. One log line is therefore a
working credential for a week.

The fix moves it to the URL FRAGMENT, which is never sent to the server, never
reaches a log or proxy, and is never included in a `Referer` — the reasoning that
made OAuth's implicit flow use fragments. The sign-in page reads it and clears it
from the address bar immediately.

★Residual, deliberately not glossed: the fragment still touches browser history
on the user's own machine for an instant. Closing that needs a single-use
exchange code and shared state across workers (a table + migration). Deferred on
purpose; this change removes the exposure that leaves the user's device.
"""
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
REPO = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pytest  # noqa: E402

AUTH_PROVIDERS = BACKEND_ROOT / "app" / "services" / "auth_providers.py"
SIGN_IN = REPO / "frontend" / "pages" / "users" / "sign-in.vue"
NUXT_CONFIG = REPO / "frontend" / "nuxt.config.ts"


def _source(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path} not present in this checkout")
    return path.read_text(encoding="utf-8")


def _code_only(text: str) -> str:
    """Drop comment LINES, keeping code intact.

    ★Written the obvious way first — `line.split("#", 1)[0]` — and that broke
    this guard immediately: the fix it is checking for is a URL FRAGMENT,
    `sign-in#access_token=`, so cutting at the first `#` deleted the very string
    the test looks for. The guard then failed against the fixed code and would
    have "passed" against a tree where the token moved back into the query
    string, since that spelling has no `#` at all. Exactly the inversion CLAUDE.md
    records: a stripper that removes the token being searched for can never fail
    for the right reason.

    So: whole-line comments go, inline `#` inside a code line stays. The
    docstring is dropped separately below, because the fix's own explanation
    quotes the broken URL.
    """
    lines = []
    in_doc = False
    for line in text.splitlines():
        s = line.strip()
        # Module/function docstrings quote the bug; skip them wholesale.
        if s.startswith('"""') or s.startswith("'''"):
            ticks = s[:3]
            if not in_doc:
                in_doc = not (len(s) > 3 and s.endswith(ticks))
            else:
                in_doc = False
            continue
        if in_doc:
            continue
        if s.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def test_the_redirect_does_not_carry_the_token_in_the_query_string():
    code = _code_only(_source(AUTH_PROVIDERS))
    offenders = re.findall(r"sign-in\?[^\"']*access_token=", code)
    assert not offenders, (
        f"an SSO redirect still puts the session token in the query string: "
        f"{offenders} — it will be written to access logs, proxies and Referer"
    )


def test_the_redirect_uses_the_fragment():
    code = _code_only(_source(AUTH_PROVIDERS))
    assert "sign-in#access_token=" in code, (
        "the SSO success redirect no longer delivers the token in the fragment; "
        "if the mechanism changed, update this guard deliberately"
    )


def test_the_sign_in_page_reads_the_fragment():
    """A backend that sends a fragment and a page that reads only the query is a
    silently broken login, so both halves are pinned together."""
    page = _source(SIGN_IN)
    assert "location.hash" in page, (
        "sign-in.vue does not read the URL fragment — SSO login would complete "
        "on the backend and then drop the session"
    )


def test_the_sign_in_page_clears_the_credential_from_the_address_bar():
    page = _source(SIGN_IN)
    assert "replaceState" in page, (
        "the token is left in the address bar after login; it should be cleared "
        "so it does not sit in session history or on a shared screen"
    )


def test_the_cookie_flags_use_the_keys_the_framework_reads():
    """The nested `cookie:{...}` block is not valid config and is ignored.

    Measured in the shipped runtime payload: both spellings were present, and the
    FLAT keys won — `secureCookieAttribute:false` while the nested block claimed
    `secure:true`. The type checker agrees: "'cookie' does not exist in type".
    """
    raw = _source(NUXT_CONFIG)
    # ★Strip // and /* */ comments before looking for the dead block: the comment
    # explaining its removal necessarily contains the literal `cookie: {`, and a
    # scan over raw text therefore fails on the FIXED file. Same class of mistake
    # as the `#` stripper above, in a different language.
    code = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    code = "\n".join(
        ln for ln in code.splitlines() if not ln.strip().startswith("//")
    )

    assert "secureCookieAttribute" in code, (
        "the Secure flag is not set through the key nuxt-auth actually reads"
    )
    assert "sameSiteAttribute" in code, (
        "SameSite is not set through the key nuxt-auth actually reads"
    )
    # And the dead nested block must not come back.
    token_block = code.split("token: {", 1)[-1].split("sessionDataType", 1)[0]
    assert "cookie: {" not in token_block, (
        "the nested cookie:{name,options} block is back — it is not valid config "
        "(the type checker reports \"'cookie' does not exist in type\"), so it is "
        "silently ignored while reading as though the flags were set"
    )
