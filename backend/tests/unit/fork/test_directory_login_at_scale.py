"""What a whole company signing in through a directory needs to survive.

Every assertion here was written against a measured failure, not an imagined
one. A directory of 200 accounts was stood up and signed in twice — once with
everybody behind a single address, once with an address each:

  all 200 through one address    20 admitted, 180 refused with 429
  one address each              200 admitted, none refused
  merge onto an existing local   one row, no duplicate — correct already
  10 simultaneous FIRST logins   one row, one membership — correct already

and the audit afterwards found three things nobody had signed in enough people
to notice: the seat cap never fired, every account was named after its email,
and a local account with no workspace signed in and stayed in no workspace.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
AUTH = REPO / "backend" / "app" / "core" / "auth.py"
THROTTLE = REPO / "backend" / "app" / "core" / "login_throttle.py"
SEATS = REPO / "backend" / "app" / "core" / "seats.py"
LDAP_CONN = REPO / "backend" / "app" / "ee" / "ldap" / "connection.py"
CONFIG = REPO / "backend" / "app" / "settings" / "config.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _fn(src: str, start: str) -> str:
    """The body of a top-level-ish def, up to the next def at the same indent."""
    i = src.index(start)
    indent = len(src[:i].split("\n")[-1])
    rest = src[i + len(start):]
    m = re.search(r"\n {0,%d}(async def |def |class )" % indent, rest)
    return rest[: m.start()] if m else rest


def _merge_branch(src: str) -> str:
    """The merge half of _ldap_authenticate, up to where it returns.

    ★Sliced on a real STATEMENT, not on the first occurrence of the text: the
    comment in that branch quotes `return "success"` while explaining the bug,
    and slicing on the raw string cut the block off before any of the code.
    """
    body = _fn(src, "async def _ldap_authenticate(")
    block = body[body.index("if existing_user is not None:"):]
    out = []
    for line in block.split("\n"):
        out.append(line)
        if line.strip() == 'return "success"':
            break
    return "\n".join(out)


# --- 1. one office address must not lock the office out ---------------------

def test_a_successful_sign_in_gives_its_address_allowance_back():
    """★★★The measured failure: 200 people signing in CORRECTLY through one
    office address got 20 in and 180 refused for five minutes.

    The address is charged by a dependency, before anybody knows whether the
    password was right, so an honest arrival cost exactly what a guess did."""
    src = _read(THROTTLE)
    assert "async def refund_login_ip(" in src, "success still costs the address"
    assert "attempts = attempts - 1" in src


def test_the_refund_is_one_attempt_and_never_a_reset():
    """★An attacker holding one valid account must not be able to wipe their own
    guesses at will. One success returns exactly one attempt — break-even."""
    body = _fn(_read(THROTTLE), "_REFUND_SQL = text(")
    assert "attempts = attempts - 1" in body
    assert "DELETE" not in body.upper(), "a refund that deletes is a reset"
    assert "attempts > 0" in body, "a refund could otherwise drive the count negative"
    assert "window_start > :cutoff" in body, (
        "without the window check, a success arriving after the window rolled "
        "banks credit against a fresh bucket"
    )


def test_the_successful_login_path_actually_refunds():
    """A function nothing calls is not a fix."""
    body = _fn(_read(AUTH), "async def on_after_login(")
    assert "refund_login_ip(" in body


def test_the_address_bucket_is_still_never_cleared():
    """★The email bucket is CLEARED on success; the address bucket is only ever
    refunded by one. Clearing it is how the limit would become resettable."""
    body = _fn(_read(THROTTLE), "async def clear_login_throttle(")
    assert "login:email:" in body
    assert "login:ip:" not in body


# --- 2. the seat cap has to apply to the way most members actually arrive ----

def test_login_auto_provisioning_checks_the_seat_cap():
    """★★★seats.py claimed 'every path that creates a Membership enforces the
    same rule'. The highest-volume path — somebody a directory or an identity
    provider vouches for, arriving at sign-in — did not check at all. Measured:
    200 directory users provisioned themselves past the cap, no check fired."""
    body = _fn(_read(AUTH), "async def _place_auto_provisioned_user(")
    assert "seats import" in body, "the placement path still ignores the licence"
    assert "enforce_seat_limit(" in body


def test_the_seat_refusal_is_not_eaten_by_the_catch_all():
    """★★The method ends in a broad `except Exception` that deliberately
    swallows placement failures. Without an explicit re-raise the 402 would be
    swallowed with them and leave exactly the account-with-no-workspace this
    check exists to prevent."""
    body = _fn(_read(AUTH), "async def _place_auto_provisioned_user(")
    assert "except HTTPException:" in body
    assert body.index("except HTTPException:") < body.index("except Exception")


def test_the_list_of_enforcing_paths_is_true():
    """★★★A list of paths is worth nothing if it is aspirational. Every module
    named in seats.py must genuinely import from it."""
    header = _read(SEATS).split("from typing import")[0]
    named = re.findall(r"\(app\.([a-z0-9_.]+)", header)
    assert named, "the header no longer lists its callers"
    for dotted in named:
        mod = dotted.split(".")
        # entries name a module, sometimes with a function after it
        for depth in (len(mod), len(mod) - 1):
            path = REPO / "backend" / "app" / Path(*mod[:depth]).with_suffix(".py")
            if path.exists():
                assert "from app.core.seats import" in _read(path), (
                    f"seats.py claims app.{dotted} enforces the cap; it does not "
                    f"import this module"
                )
                break
        else:
            raise AssertionError(f"seats.py names app.{dotted}, which is not a module")


# --- 3. people have names -----------------------------------------------------

def test_provisioning_uses_the_directorys_own_name():
    """★A 200-person directory arrived as staff001…staff200 while the real
    names sat unread in the entry the whole time."""
    src = _read(AUTH)
    body = _fn(src, "async def _ldap_authenticate(")
    assert "directory_name or email.split" in body, (
        "the account is still named after its email address"
    )
    assert "manager.find_user(" in body, "the name is never fetched"


def test_the_name_lookup_costs_no_extra_round_trip():
    """The DN and the name come from one search — the old call already made it,
    it just asked for one attribute."""
    body = _fn(_read(LDAP_CONN), "def find_user(")
    assert body.count("conn.search(") == 1
    assert "name_attr" in body


def test_a_missing_name_attribute_is_not_an_error():
    """★★An attribute the schema does not define is simply ABSENT from the
    entry — reading it raises. Stock OpenLDAP inetOrgPerson has no
    `displayName`, which is what this product used to default to."""
    body = _fn(_read(LDAP_CONN), "def find_user(")
    assert "if name_attr in entry else None" in body


def test_the_default_name_attribute_exists_on_a_plain_openldap_directory():
    """★`cn` is mandatory on `person`; `displayName` is not defined by
    inetOrgPerson at all. The old default named an attribute that was never
    there, so every account came out unnamed on the commonest directory made."""
    for f in (
        REPO / "backend" / "app" / "settings" / "dash_config.py",
        REPO / "backend" / "app" / "ee" / "ldap" / "schemas.py",
        REPO / "backend" / "app" / "schemas" / "organization_settings_schema.py",
    ):
        src = _read(f)
        assert 'user_name_attribute: str = "displayName"' not in src, f.name
        assert 'user_name_attribute: str = "cn"' in src, f.name


def test_the_form_offers_the_same_default_as_the_backend():
    """A form that pre-fills a different attribute than the code defaults to
    puts the two out of step the moment anybody presses save."""
    vue = _read(REPO / "frontend" / "pages" / "settings" / "identity-provider.vue")
    assert "user_name_attribute: 'cn'," in vue


# --- 4. merging must place people, not just admit them -----------------------

def test_an_existing_local_account_is_still_placed_in_a_workspace():
    """★★★Measured: a local account with zero memberships signed in through the
    directory and stayed at zero — a token, and an empty product.

    Placement lived only in the create branch, and the merge branch returned
    before reaching it."""
    merge = _merge_branch(_read(AUTH))
    assert "_place_auto_provisioned_user(" in merge
    assert "_attach_open_memberships(" in merge


def test_merging_does_not_overwrite_a_name_a_person_chose():
    """★The directory claims the account; it does not get to rename its owner.
    The name is FILLED IN when empty and otherwise left alone."""
    merge = _merge_branch(_read(AUTH))
    assert 'not (existing_user.name or "").strip()' in merge


def test_the_merge_path_still_reuses_the_row_it_found():
    """The one thing that was already right: merge is by email and creates
    nothing. 204 accounts, zero duplicate addresses."""
    merge = _merge_branch(_read(AUTH))
    assert "user_db.create(" not in merge, "the merge path creates a second account"
