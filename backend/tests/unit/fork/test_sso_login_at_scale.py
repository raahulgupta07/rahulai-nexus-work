"""What a whole company signing in through Keycloak needs to survive.

Measured against a real Keycloak realm of 200 people federated the way the
office runs it, driving the actual authorize → login-form → callback flow:

  gate closed   200 sign-ins, 200 REFUSED  ("Sign-up is disabled")
  gate open     201 sign-ins, 201 admitted, 0 duplicates, every one placed
  and then      200 of 200 accounts named `emp001` … `emp200`

The refusal was the reported production failure and was already fixed. The
naming was not: the id_token carried `name`, `given_name` and `family_name`
for every one of them, and nothing read any of it.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
AUTH = REPO / "backend" / "app" / "core" / "auth.py"
PROVIDERS = REPO / "backend" / "app" / "services" / "auth_providers.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _fn(src: str, start: str) -> str:
    """The body of a def, up to the next def/class at the same indent."""
    i = src.index(start)
    indent = len(src[:i].split("\n")[-1])
    rest = src[i + len(start):]
    m = re.search(r"\n {0,%d}(async def |def |class )" % indent, rest)
    return rest[: m.start()] if m else rest


# --- the name the provider sent -------------------------------------------

def test_the_callback_reads_the_name_out_of_the_id_token():
    """★Keycloak sends `name: 'Kyaw Naing042'` in the very token the callback
    already decodes for the email. It was decoded, used for `sub` and `email`,
    and the name was stepped over."""
    body = _fn(_read(PROVIDERS), "async def _handle_callback(")
    assert "_display_name_from_claims(id_claims)" in body
    assert "account_name=account_name" in body, "the name is read and then dropped"


def test_the_name_helper_prefers_the_standard_claim_then_the_parts():
    body = _fn(_read(PROVIDERS), "def _display_name_from_claims(")
    assert 'claims.get("name")' in body
    assert 'claims.get("given_name")' in body
    assert 'claims.get("family_name")' in body


def test_the_name_helper_never_falls_back_to_the_login_id():
    """★`preferred_username` is the login id — for this realm it is the email
    again by another route, which is the exact defect being fixed."""
    body = _fn(_read(PROVIDERS), "def _display_name_from_claims(")
    assert "preferred_username" not in body


def test_a_provider_that_sends_no_name_yields_none_not_empty_string():
    """An empty string is falsy but would still be *chosen* by a naive `or`
    chain in some orderings; returning None keeps the email fallback reachable."""
    body = _fn(_read(PROVIDERS), "def _display_name_from_claims(")
    assert "return joined or None" in body


def test_the_email_local_part_is_the_last_resort_not_the_default():
    body = _fn(_read(AUTH), "async def oauth_callback(")
    i_name = body.index("fetched_name = account_name")
    i_email = body.index('fetched_name = account_email.split("@")[0]')
    assert i_name < i_email, "the email fallback still wins over the real name"


def test_the_base_librarys_own_caller_still_works():
    """★fastapi-users' OAuth router calls this method and knows nothing about
    the new argument, so it must be keyword-only WITH a default."""
    sig = _read(AUTH)[_read(AUTH).index("async def oauth_callback("):]
    sig = sig[: sig.index(") -> User:")]
    assert "account_name: Optional[str] = None" in sig
    assert sig.index("*args") < sig.index("account_name"), (
        "a positional parameter here would break every existing call"
    )


# --- merging onto an account that already exists ---------------------------

def test_merging_fills_a_blank_name_but_never_overwrites_a_chosen_one():
    """★The directory claims the account; it does not get to rename its owner.
    Proven live: a local account named 'Test' kept that name after signing in
    through a realm that called the same address 'Directory Claimed'."""
    body = _fn(_read(AUTH), "async def oauth_callback(")
    assert 'not (user.name or "").strip()' in body


def test_an_existing_local_account_is_placed_in_a_workspace():
    """★★★Measured live: an account that existed here with zero memberships
    signed in through Keycloak, got a perfectly valid session, and stayed at
    zero memberships — an empty product with nothing to ask about.

    Placement lived only in the create branch; the merge branch linked the
    identity and returned."""
    src = _read(AUTH)
    i = src.index("user = await self.get_by_email(account_email)")
    j = src.index("except exceptions.UserNotExists:", i)
    merge = src[i:j]
    assert "_attach_open_memberships(" in merge
    assert "_place_auto_provisioned_user(" in merge


def test_placement_on_merge_needs_the_same_trust_as_creation():
    """A provider that may not admit strangers may not hand out workspaces
    either — otherwise the invite-only setting would be half-enforced."""
    src = _read(AUTH)
    i = src.index("user = await self.get_by_email(account_email)")
    j = src.index("except exceptions.UserNotExists:", i)
    merge = src[i:j]
    assert merge.index("_provider_admits_new_users(") < merge.index(
        "_place_auto_provisioned_user("
    )


def test_the_merge_path_still_creates_no_second_account():
    """The thing that was already right: merge is by email. 201 sign-ins
    against 200 directory users plus one existing local account produced 201
    rows total and zero duplicate addresses."""
    src = _read(AUTH)
    i = src.index("user = await self.get_by_email(account_email)")
    j = src.index("except exceptions.UserNotExists:", i)
    assert "user_db.create(" not in src[i:j]


# --- the door (already fixed, kept honest) ---------------------------------

def test_a_trusted_provider_can_admit_people_and_is_checked_first():
    """★The reported production failure. Keycloak authenticated 200 real
    people and the product refused every one of them because no admin had
    written their names down first."""
    body = _fn(_read(AUTH), "async def oauth_callback(")
    i_prov = body.index("_provider_admits_new_users(")
    i_dom = body.index("_has_domain_invite(")
    i_raise = body.index('"code": "invitation_required"')
    assert i_prov < i_dom < i_raise


def test_the_provider_trust_lookup_fails_closed():
    """An unreadable SSO config must leave the invite and domain checks to
    decide, exactly as before — never open the door by accident."""
    body = _fn(_read(AUTH), "async def _provider_admits_new_users(")
    assert "except Exception" in body
    assert body.rstrip().endswith("return False")
