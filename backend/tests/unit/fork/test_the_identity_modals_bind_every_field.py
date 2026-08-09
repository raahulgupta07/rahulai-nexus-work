"""Moving a settings form into a dialog must not drop a field.

The SSO and LDAP forms were inline on `/settings/identity-provider` and became
modal dialogs. That is a pure container change — the endpoints, payloads and
permissions are identical — but a 21-field form retyped into a new component is
exactly where one field goes quietly missing, and the failure is close to
invisible: the input is simply absent, the PUT omits it, and the service writes
the default over whatever the admin had saved. Nobody sees an error. A
directory stops matching, or a provider stops sending a scope, weeks later.

So the schema is the contract and these tests read it: every writable field on
``OrgLdapUpdate``, ``SsoProviderUpdate`` and ``SsoGoogleUpdate`` must be bound
by the component that owns it.

★These read the `.vue` files as TEXT, which is all a Python suite can do — see
the note in CLAUDE.md about the 169 frontend guards. A text scan cannot tell you
the field is wired to the right input, only that the name appears at all. That
is a real limit and it is still worth having: the defect this prevents is a name
that appears NOWHERE.
"""

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[4]
SCHEMA = REPO / "backend" / "app" / "schemas" / "organization_settings_schema.py"
COMPONENTS = REPO / "frontend" / "components" / "settings"
LDAP_MODAL = COMPONENTS / "LdapConfigModal.vue"
SSO_MODAL = COMPONENTS / "SsoProviderModal.vue"
MARK = COMPONENTS / "ProviderMark.vue"
PAGE = REPO / "frontend" / "pages" / "settings" / "identity-provider.vue"


def _schema_fields(cls: str) -> set:
    """Declared field names on a pydantic model, by source order.

    Sliced to the next `class ` so a model never inherits its neighbour's
    fields — the bug that makes a scan like this pass for the wrong reason.
    """
    src = SCHEMA.read_text(encoding="utf-8")
    m = re.search(rf"^class {cls}\(BaseModel\):(.*?)(?=^class )", src, re.S | re.M)
    assert m, f"{cls} not found in {SCHEMA.name} — the model was renamed"
    return set(re.findall(r"^    ([a-z][a-z0-9_]*)\s*:", m.group(1), re.M))


def _bound_names(path: Path) -> set:
    """Every identifier the component references as a form field."""
    src = path.read_text(encoding="utf-8")
    return set(re.findall(r"\b(?:form|ldapForm|ssoForm|model|draft)\.([a-z][a-z0-9_]*)", src))


@pytest.mark.parametrize(
    "cls,component",
    [
        ("OrgLdapUpdate", LDAP_MODAL),
        ("SsoProviderUpdate", SSO_MODAL),
        ("SsoGoogleUpdate", SSO_MODAL),
    ],
)
def test_every_writable_field_is_bound(cls, component):
    fields = _schema_fields(cls)
    # A model with no fields would make this test vacuously true — the exact
    # failure mode CLAUDE.md warns about. Prove the scan found something first.
    assert len(fields) >= 4, f"{cls} parsed as {fields!r}; the regex stopped matching"

    missing = sorted(fields - _bound_names(component))
    assert not missing, (
        f"{component.name} does not bind {missing} from {cls}. A field absent "
        f"from the form is omitted from the PUT, and the service then writes "
        f"its default over whatever the administrator had saved."
    )


def test_the_secret_fields_are_write_only():
    """The API returns `*_set: bool`, never the secret. The form must not try.

    Binding the redacted read field into the input would put the literal string
    `True` in a password box and save it as the credential.
    """
    for path, forbidden in ((LDAP_MODAL, "bind_password_set"), (SSO_MODAL, "client_secret_set")):
        src = path.read_text(encoding="utf-8")
        assert not re.search(rf"v-model[^=]*=\s*[\"'][^\"']*{forbidden}", src), (
            f"{path.name} binds {forbidden} to an input; it is a boolean flag, "
            f"not the secret, and the secret is never returned by the API"
        )


def test_the_login_attribute_reached_the_form():
    """The field that lets people sign in with a username rather than an email.

    Added the same day as this modal. It is the newest field and therefore the
    likeliest to be forgotten in a retype, which is the whole reason this file
    exists.
    """
    src = LDAP_MODAL.read_text(encoding="utf-8")
    assert "user_login_attribute" in src
    assert "ldapLoginAttrHint" in src, (
        "the field is present but unexplained; without the hint an admin cannot "
        "know it takes uid on OpenLDAP and sAMAccountName on Active Directory"
    )


def test_every_saved_field_can_actually_be_set():
    """A field in the PUT with no control is a value nobody can change.

    ★Measured 2026-08-08: `icon` was read from the canonical row, held on the
    form and written in the save payload — with no input anywhere. So the mark
    was permanently whatever the row shipped with, and the only clue was that
    the picker in the design never got built. Nothing failed; the field simply
    did not exist for the user.

    This walks the SAVE payload and requires each field it sends to be reachable
    from a control, so the next silently-unsettable field fails here instead of
    being noticed in a screenshot.
    """
    src = SSO_MODAL.read_text(encoding="utf-8")

    # The object literal handed to the save call: `icon: form.icon,` etc.
    sent = set(re.findall(r"^\s+([a-z_]+):\s*form\.[a-z_]+,?\s*$", src, re.M))
    assert len(sent) >= 6, f"payload scan found only {sent!r}; the save shape moved"

    # `name` identifies the entry rather than being edited, and `scopes` is
    # derived from the scopesText input.
    exempt = {"name", "scopes"}
    for field in sorted(sent - exempt):
        bound = (
            f'v-model="form.{field}"' in src
            or f"v-model='form.{field}'" in src
            or f'v-model.trim="form.{field}"' in src
        )
        assert bound, (
            f"the save sends `{field}` but no control writes it — it can only "
            f"ever hold whatever it was initialised with"
        )


def test_the_chosen_logo_reaches_every_screen_that_shows_it():
    """★★★A setting nobody reads is worse than a missing setting.

    Measured 2026-08-08, reported by the user: picking a logo in Settings
    changed nothing anywhere. The value saved correctly — and then all three
    consumers dropped it.

      1. `ssoRows` spread the canonical row and never read the saved `icon`,
         so the settings list kept the default forever;
      2. the public `/api/settings` feed did not include `icon` at all, so the
         sign-in page could not have used it even if it wanted to;
      3. the sign-in page drew ONE hardcoded shield for every OIDC provider.

    Each is invisible on its own — no error, no warning, just a control that
    appears to do nothing. This walks the whole path instead of any one hop.
    """
    page = PAGE.read_text(encoding="utf-8")
    assert re.search(r"icon:\s*\(p && p\.icon\)", page), (
        "the settings row ignores the saved icon and always shows the canonical "
        "default, so the picker looks broken"
    )

    feed = (REPO / "backend" / "app" / "routes" / "dash_settings.py").read_text(encoding="utf-8")
    block = feed[feed.index('"oidc_providers"'):]
    block = block[: block.index("]")]
    assert '"icon"' in block, (
        "the public settings feed omits `icon`, so the sign-in page never "
        "receives the administrator's choice"
    )

    signin = (REPO / "frontend" / "pages" / "users" / "sign-in.vue").read_text(encoding="utf-8")
    assert ":icon=\"p.icon\"" in signin, (
        "the sign-in page does not render the provider's own mark; every "
        "provider would look identical on the one screen the setting names"
    )

    # ★★★And the value has to REACH that binding. The page rebuilds each
    # provider field by field from the feed, so a field not named in the map is
    # dropped — `icon` was, and the button fell back to a letter tile while the
    # feed carried the right value. The first version of this guard checked the
    # binding and stopped there, which is why the bug survived it: a template
    # that reads `p.icon` proves nothing when nothing writes `p.icon`.
    m = re.search(r"\.map\(\(p: any\) => \(\{(.*?)\}\)\)", signin, re.S)
    assert m, "the provider map in sign-in.vue was restructured; re-verify by hand"
    assert re.search(r"\bicon:", m.group(1)), (
        "sign-in.vue's provider map does not copy `icon`, so the binding above "
        "always receives undefined"
    )


def test_an_unknown_provider_still_gets_a_chip():
    """ProviderMark must degrade to the letter tile it replaced.

    A provider we have no artwork for has to render as it did before, not as an
    empty square. This is the difference between adding marks and removing the
    fallback.
    """
    src = MARK.read_text(encoding="utf-8")
    assert "v-else" in src, "no fallback branch — an unknown icon renders nothing"
    # The four colours the page used before this component existed.
    for cls in ("bg-red-500", "bg-indigo-500", "bg-sky-600", "bg-slate-500"):
        assert cls in src, f"fallback lost the {cls} chip colour"


def test_no_provider_logo_is_fetched_over_the_network():
    """A hosted logo cannot work here, and fails silently when it doesn't.

    The app serves a strict CSP. An external image URL is blocked, and a blocked
    <img> is an empty box, not an error anyone sees.
    """
    src = MARK.read_text(encoding="utf-8")
    assert not re.search(r"""(?:src|href)\s*=\s*["']https?://""", src), (
        "ProviderMark references a remote asset; the CSP blocks it and the row "
        "renders an empty tile"
    )


def test_the_inline_forms_are_gone_from_the_page():
    """Two ways to edit the same config is how the two drift apart.

    If the inline form survives alongside the modal, a field added later gets
    added to one of them.
    """
    src = PAGE.read_text(encoding="utf-8")
    assert "LdapConfigModal" in src and "SsoProviderModal" in src, (
        "the page does not mount the modals"
    )
    assert "ssoEditingKey" not in src, (
        "the inline SSO edit form is still in the page beside the modal"
    )
