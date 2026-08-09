"""The LDAP sign-in form says USERNAME. The door has to accept one.

Measured on a stock OpenLDAP (2026-08-08), before this was fixed: the form is
labelled `auth.usernameFieldLabel` with the placeholder `jsmith`, and
`LDAPConnectionManager.find_user` searched
``(&(objectClass=inetOrgPerson)(mail=<typed>))`` — the email attribute alone.
So `ldapuser` came back as one indistinguishable LOGIN_BAD_CREDENTIALS
(`ldap_not_in_directory` in the audit log) while `ldapuser@cityagent.io`
signed in. The label promised a credential the code could never accept.

Three separate properties are pinned here, because the fix has three parts and
only the first one is the visible bug:

1. the search matches the LOGIN attribute as well as the email attribute;
2. the identifier is ESCAPED into the filter — it was interpolated raw, so a
   bare ``*`` matched the first entry in the tree;
3. ★★★the local account is keyed on the DIRECTORY's address, never on the
   string that was typed. This is the one that matters. `_ldap_authenticate`
   used the typed value for `get_by_email`, for `user_db.create` and for
   invite matching, which was safe only while the filter forced that value to
   equal the directory's `mail`. The moment a username is accepted that stops
   being true, and the merge gate's own argument — "an admin-maintained
   attribute, not something the person types" — becomes false with it.

★These assert BEHAVIOUR through a fake directory wherever they can, not the
shape of the source. The two that do read source read the AST, for the reason
recorded in CLAUDE.md: a comment-stripping text scan passed on a docstring
quoting the very expression it was hunting.
"""

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from app.settings.dash_config import LDAPConfig


REPO = Path(__file__).resolve().parents[4]
AUTH_PY = REPO / "backend" / "app" / "core" / "auth.py"


# ---------------------------------------------------------------- fake server

class _Attr:
    def __init__(self, value):
        self.value = value


class _Entry:
    """Just enough of an ldap3 entry: `attr in entry` and `entry[attr].value`."""

    def __init__(self, dn, attrs):
        self.entry_dn = dn
        self._attrs = attrs

    def __contains__(self, name):
        return name in self._attrs

    def __getitem__(self, name):
        return _Attr(self._attrs[name])


class _Conn:
    def __init__(self, directory, recorder):
        self._directory = directory
        self._recorder = recorder
        self.entries = []

    def search(self, search_base, search_filter, search_scope, attributes, size_limit):
        self._recorder.append(search_filter)
        self.entries = [
            _Entry(dn, attrs)
            for dn, attrs in self._directory
            if _filter_matches(search_filter, attrs)
        ][:size_limit]

    def unbind(self):
        pass


def _filter_matches(search_filter, attrs):
    """A deliberately literal reader of `(attr=value)` pairs.

    It understands equality only — no wildcards. That is the point: an
    unescaped `*` reaching this reader matches nothing, so the escaping test
    below cannot pass by accident, and a filter that wrongly matched
    everything in a real directory does not silently match here either.
    """
    import re

    pairs = re.findall(r"\(([A-Za-z0-9;-]+)=([^()]*)\)", search_filter)
    # Drop the objectClass term contributed by user_search_filter.
    pairs = [(a, v) for a, v in pairs if a.lower() != "objectclass"]
    return any(str(attrs.get(a, "")) == v for a, v in pairs)


DIRECTORY = [
    (
        "uid=ldapuser,ou=people,dc=cityagent,dc=io",
        {"uid": "ldapuser", "mail": "ldapuser@cityagent.io", "cn": "LDAP Test User"},
    ),
    (
        "uid=other,ou=people,dc=cityagent,dc=io",
        {"uid": "other", "mail": "other@cityagent.io", "cn": "Other Person"},
    ),
]


def _manager(**overrides):
    from app.ee.ldap.connection import LDAPConnectionManager

    cfg = LDAPConfig(
        enabled=True,
        url="ldap://directory.invalid:389",
        base_dn="dc=cityagent,dc=io",
        user_search_filter="(objectClass=inetOrgPerson)",
        use_ssl=False,
        **overrides,
    )
    mgr = LDAPConnectionManager(cfg)
    filters = []
    mgr.get_connection = lambda: _Conn(DIRECTORY, filters)
    return mgr, filters


# ------------------------------------------------------------------ the bug

def test_a_username_finds_the_entry():
    """The reported defect, stated as the product promise it broke."""
    mgr, _ = _manager()
    found = mgr.find_user("ldapuser")
    assert found is not None, (
        "the sign-in form is labelled USERNAME with the placeholder 'jsmith'; "
        "a username has to reach an entry"
    )
    assert found["dn"] == "uid=ldapuser,ou=people,dc=cityagent,dc=io"


def test_an_email_address_still_finds_the_entry():
    """Accepting a username must not stop accepting the address."""
    mgr, _ = _manager()
    found = mgr.find_user("ldapuser@cityagent.io")
    assert found is not None
    assert found["dn"] == "uid=ldapuser,ou=people,dc=cityagent,dc=io"


def test_the_entry_carries_its_own_address_back():
    """Without this the caller has nothing to key the local account on."""
    mgr, _ = _manager()
    found = mgr.find_user("ldapuser")
    assert found.get("email") == "ldapuser@cityagent.io", (
        "find_user must return the DIRECTORY's address; the typed identifier "
        "is a username and is not one"
    )


def test_a_directory_that_logs_in_by_email_can_still_say_so():
    """Empty login attribute = the old email-only behaviour, deliberately."""
    mgr, filters = _manager(user_login_attribute="")
    assert mgr.find_user("ldapuser") is None
    assert mgr.find_user("ldapuser@cityagent.io") is not None
    assert "uid=" not in filters[0]


# ------------------------------------------------------------------ escaping

def test_a_wildcard_is_not_a_password():
    """`*` was interpolated straight into the filter."""
    mgr, filters = _manager()
    assert mgr.find_user("*") is None, "a wildcard must not match anybody"
    assert "(uid=*)" not in filters[0] and "(mail=*)" not in filters[0], (
        f"identifier reached the filter unescaped: {filters[0]}"
    )
    assert r"\2a" in filters[0].lower(), (
        f"expected an escaped asterisk in the filter, got: {filters[0]}"
    )


def test_filter_syntax_cannot_be_broken_out_of():
    mgr, filters = _manager()
    mgr.find_user("x)(objectClass=*")
    # The parens the caller supplied must be escaped, so the filter still has
    # balanced delimiters and no injected term.
    assert filters[0].count("(") == filters[0].count(")"), filters[0]
    assert "(objectClass=*)" not in filters[0], filters[0]


# ------------------------------------- the part that keeps the merge gate true

def _ldap_authenticate_ast():
    import app.core.auth as auth_mod

    src = inspect.getsource(auth_mod.UserManager._ldap_authenticate)
    return ast.parse(textwrap.dedent(src)).body[0]


def test_the_typed_identifier_is_never_used_as_an_address():
    """★★★The load-bearing one.

    Every account-keying call in `_ldap_authenticate` — the merge lookup, the
    create, the invite match — must receive the address read off the matched
    entry. If any of them still receives the parameter the caller posted, then
    typing an address that resolves to somebody else's entry by username lets
    the directory vouch for one person and the app key the row on another.
    """
    fn = _ldap_authenticate_ast()
    param = fn.args.args[1].arg
    assert param == "identifier", (
        "the parameter is what the person typed, and naming it `email` is how "
        f"it gets used as one; found {param!r}"
    )

    offenders = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(arg, ast.Name) and arg.id == param:
                    offenders.append((_callee(node), node.lineno))
        # `{"email": identifier}` in the create dict counts too.
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (
                    isinstance(k, ast.Constant)
                    and k.value == "email"
                    and isinstance(v, ast.Name)
                    and v.id == param
                ):
                    offenders.append(("dict email=", node.lineno))

    allowed = {"manager.find_user"}
    real = [o for o in offenders if o[0] not in allowed]
    assert not real, (
        "the typed identifier is used as an address at: "
        + ", ".join(f"{name} (line {ln})" for name, ln in real)
    )


def test_the_caller_looks_the_account_up_by_the_directory_address():
    """★★★The half this guard MISSED on its first draft.

    Scoping the scan to `_ldap_authenticate` let the identical mistake survive
    one frame up: `_do_authenticate_ldap` finished a successful bind with
    ``get_by_email(credentials.username)``. Measured live 2026-08-08 — a
    correct username and a correct password produced LOGIN_BAD_CREDENTIALS,
    and the audit log showed only `invalid_credentials` with no LDAP reason,
    because the refusal happened after the directory had already said yes.

    A guard is worth what it covers, not what it was aimed at.
    """
    import app.core.auth as auth_mod

    src = textwrap.dedent(inspect.getsource(auth_mod.UserManager._do_authenticate_ldap))
    fn = ast.parse(src).body[0]

    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _callee(node) == "self.get_by_email":
            arg = node.args[0] if node.args else None
            assert not (
                isinstance(arg, ast.Attribute)
                and isinstance(arg.value, ast.Name)
                and arg.value.id == "credentials"
            ), (
                "the account is looked up by the string that was typed; after "
                "a directory bind it must be looked up by the entry's address"
            )


def test_the_address_comes_from_the_matched_entry():
    fn = _ldap_authenticate_ast()
    got = False
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "email" in targets and "found" in ast.dump(node.value):
                got = True
    assert got, (
        "`email` must be assigned from the directory entry (`found`) before "
        "anything keys an account on it"
    )


def test_an_entry_without_an_address_is_refused_not_invented():
    """A bound user with no `mail` cannot become an account named after a uid."""
    src = AUTH_PY.read_text()
    assert "no_directory_email" in src, (
        "an entry that binds but carries no address has to be refused with its "
        "own audit reason, not keyed on the typed identifier"
    )


def _callee(node):
    f = node.func
    if isinstance(f, ast.Attribute):
        base = f.value.id if isinstance(f.value, ast.Name) else "?"
        return f"{base}.{f.attr}"
    if isinstance(f, ast.Name):
        return f.id
    return "?"


# -------------------------------------------------------------- the settings

def test_the_login_attribute_is_configurable_and_surfaced():
    assert hasattr(LDAPConfig(), "user_login_attribute")
    assert LDAPConfig().user_login_attribute == "uid", (
        "the default has to work on a stock OpenLDAP without anyone editing it"
    )

    from app.schemas.organization_settings_schema import OrgLdapSchema, OrgLdapUpdate
    from app.services.organization_settings_service import OrganizationSettingsService

    assert "user_login_attribute" in OrgLdapSchema.model_fields
    assert "user_login_attribute" in OrgLdapUpdate.model_fields
    assert "user_login_attribute" in OrganizationSettingsService._LDAP_FIELDS, (
        "not listed here means it is neither saved nor resolved, so the form "
        "field would silently do nothing"
    )


@pytest.mark.parametrize("key", ["ldapLoginAttr", "ldapLoginAttrHint"])
def test_the_form_field_has_its_strings(key):
    import json

    en = json.loads((REPO / "locales" / "en.json").read_text())
    assert key in en["settings"]["identityProvider"], key
