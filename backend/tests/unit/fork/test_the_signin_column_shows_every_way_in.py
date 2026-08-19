"""The roster's Sign-in column showed one way in when there were two.

WHAT THIS COST
--------------
Measured live on the dev install, 2026-08-19. Staff who sign in through
Keycloak, and whose accounts were also provisioned out of the directory, appear
on Settings ▸ Members as **LDAP** and nothing else. The administrator reading
that column concludes single sign-on is not configured for them — the reported
question was exactly "here they also login with sso why we only have ldap?"

`resolve_auth_origin` is not wrong. It answers ONE question — *whose password is
this* — and it answers it by precedence (SCIM, then LDAP, then SSO, then local)
because a password can only be owned by one system. That is the right rule for
the Set-password gate, and it must not change.

★★★It is the wrong rule for a COLUMN THAT DESCRIBES ACCESS. Access is not
exclusive: a person can hold a directory account and a linked Keycloak identity
at once, and both genuinely let them in. The column borrowed the password
answer, so a second way in was invisible.

WHAT IS PINNED HERE
-------------------
  * a list function for the column, alongside the unchanged single-value
    function for the gate
  * the gate's precedence, re-pinned from this side, so a change to the list
    cannot quietly relax who may be handed a password
  * the value reaching the wire (`UserSchema.auth_origins`) and the caller that
    fills it, because a field nothing populates is the failure this fork has
    already paid for five times over
  * the column rendering every entry — a template that reads a list while the
    markup draws one badge is the logo-picker defect again
"""
import re
import types
from pathlib import Path

from app.core import auth_origin
from app.core.auth_origin import resolve_auth_origin, resolve_auth_origins

REPO = Path(__file__).resolve().parents[4]
MEMBERS_VUE = REPO / "frontend" / "components" / "MembersComponent.vue"


def _user(**kw):
    base = dict(ldap_dn=None, scim_external_id=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _oauth(name="keycloak"):
    return types.SimpleNamespace(oauth_name=name, account_email=None, account_id=None)


# --- the defect --------------------------------------------------------------


def test_a_person_with_two_ways_in_shows_two():
    user = _user(ldap_dn="uid=kaungminhtet,ou=people,dc=chl,dc=local")

    origins = resolve_auth_origins(user, oauth_accounts=[_oauth()])

    assert set(origins) == {"ldap", "sso"}, (
        "the column borrowed the password answer, so the second way in was "
        "invisible: %r" % (origins,)
    )


def test_the_first_entry_is_the_one_that_owns_the_password():
    """★The column is ordered, not a set: the primary answer stays first, so
    reading only the first badge can never be misleading."""
    user = _user(ldap_dn="uid=x,dc=y")
    origins = resolve_auth_origins(user, oauth_accounts=[_oauth()])

    assert origins[0] == resolve_auth_origin(user, oauth_accounts=[_oauth()])


def test_one_way_in_is_still_one_badge():
    assert resolve_auth_origins(_user(), oauth_accounts=[]) == ["local"]
    assert resolve_auth_origins(_user(), oauth_accounts=[_oauth()]) == ["sso"]
    assert resolve_auth_origins(_user(ldap_dn="uid=x,dc=y"), oauth_accounts=[]) == ["ldap"]
    assert resolve_auth_origins(_user(scim_external_id="e1"), oauth_accounts=[]) == ["scim"]


def test_local_is_never_listed_beside_another_origin():
    """★"local" here means a password THIS product owns. A provisioned account's
    hash is a random string nobody holds, so listing it beside the directory
    would be the People-screen fiction in a second place."""
    user = _user(ldap_dn="uid=x,dc=y")
    assert "local" not in resolve_auth_origins(user, oauth_accounts=[_oauth()])
    assert "local" not in resolve_auth_origins(_user(), oauth_accounts=[_oauth()])


def test_a_scim_account_linked_to_an_idp_shows_both():
    user = _user(scim_external_id="ext-9911")
    origins = resolve_auth_origins(user, oauth_accounts=[_oauth()])
    assert origins == ["scim", "sso"]


# --- the gate is unchanged, and that is asserted, not assumed ----------------


def test_the_password_gate_still_answers_with_exactly_one_origin():
    """★A password can only be owned by one system. If this ever returns a list,
    `password_is_managed_here` starts deciding on a container and every account
    becomes settable."""
    user = _user(ldap_dn="uid=x,dc=y")
    origin = resolve_auth_origin(user, oauth_accounts=[_oauth()])

    assert origin == "ldap"
    assert isinstance(origin, str)
    assert auth_origin.password_is_managed_here(origin) is False
    assert auth_origin.password_is_managed_here(
        resolve_auth_origin(_user(), oauth_accounts=[])
    ) is True


def test_neither_function_lazy_loads_the_relationship():
    """★Reading `user.oauth_accounts` inside an async request raises under
    asyncpg. Both take the collection from the caller."""
    class _Exploding:
        ldap_dn = None
        scim_external_id = None

        @property
        def oauth_accounts(self):  # pragma: no cover - must never be reached
            raise AssertionError("lazy load inside a request")

    assert resolve_auth_origins(_Exploding(), oauth_accounts=[]) == ["local"]
    assert resolve_auth_origin(_Exploding(), oauth_accounts=[]) == "local"


# --- it has to reach the screen ----------------------------------------------


def test_the_value_reaches_the_wire():
    from app.schemas.user_schema import UserSchema

    assert "auth_origins" in UserSchema.model_fields, (
        "the column has nothing to render — the list never leaves the server"
    )
    # The single value stays, because the Set-password gate reads it.
    assert "auth_origin" in UserSchema.model_fields


def test_the_roster_populates_it():
    """★A field nothing writes is this fork's most repeated defect: the SSO logo
    picker saved `icon` and four separate consumers dropped it."""
    import inspect

    from app.services import organization_service

    source = inspect.getsource(organization_service)
    assert "resolve_auth_origins" in source
    assert "auth_origins" in source


def _signin_cell(src: str) -> str:
    """The one table cell that draws the Sign-in column.

    ★Scoped deliberately. A whole-file scan for "auth_origins" passes on a file
    that merely declares the type, and this fork has shipped exactly that class
    of vacuous guard four separate times.
    """
    marker = src.index("signInBadgeColor(")
    return src[src.rindex("<td", 0, marker):src.index("</td>", marker)]


def _function_body(src: str, name: str) -> str:
    start = src.index("function %s(" % name)
    return src[start:src.index("\n}", start)]


def _loop_source(src: str) -> str:
    """What the Sign-in cell iterates, resolved through a helper if it calls one."""
    cell = _signin_cell(src)
    match = re.search(r'v-for="\s*\w+\s+in\s+([^"]+)"', cell)
    assert match, (
        "the Sign-in cell draws a single badge, so a person with two ways in is "
        "drawn as having one:\n" + cell
    )
    expression = match.group(1)
    called = re.match(r"\s*(\w+)\s*\(", expression)
    return _function_body(src, called.group(1)) if called else expression


def test_the_column_draws_one_badge_per_way_in():
    """★A template that READS a list proves nothing while the markup draws a
    single badge. The loop is what makes the second identity visible."""
    resolved = _loop_source(MEMBERS_VUE.read_text(encoding="utf-8"))

    assert "auth_origins" in resolved, (
        "the Sign-in cell loops over something that is not the list of ways in: "
        + resolved
    )


def test_the_column_still_renders_for_an_account_that_predates_the_field():
    """★`auth_origins` is populated by the caller, so anything that skips that
    path — a cached response, an older client — sends the single value alone.
    The cell must fall back rather than going blank."""
    resolved = _loop_source(MEMBERS_VUE.read_text(encoding="utf-8"))

    assert re.search(r"auth_origin\b(?!s)", resolved), (
        "nothing falls back to the single origin, so the column empties out for "
        "any payload without the list"
    )


def test_every_origin_the_list_can_hold_has_a_label_and_a_colour():
    """★An unlabelled origin renders as the raw slug — `scim` in a column of
    proper nouns."""
    src = MEMBERS_VUE.read_text(encoding="utf-8")
    for origin in ("local", "sso", "ldap", "scim"):
        assert re.search(r"^\s+%s:\s" % origin, src, re.M), (
            "%r has no entry in the label/colour maps" % origin
        )


def test_the_rule_that_was_removed_is_still_detected():
    """★Carry the red proof IN the test — see the matching note in
    `test_a_person_is_shown_the_identities_they_have.py`.

    The pre-fix column was the single-valued password answer wrapped in a list.
    Required here to still produce the reported screen: LDAP alone for someone
    who also signs in through Keycloak.
    """
    user = _user(ldap_dn="uid=kaungminhtet,ou=people,dc=chl,dc=local")
    accounts = [_oauth()]

    pre_fix = [resolve_auth_origin(user, oauth_accounts=accounts)]
    assert pre_fix == ["ldap"], (
        "the reconstruction no longer reproduces the defect, so this test has "
        "stopped proving anything"
    )
    assert "sso" not in pre_fix

    assert set(resolve_auth_origins(user, oauth_accounts=accounts)) == {"ldap", "sso"}
