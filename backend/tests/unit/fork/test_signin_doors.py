"""Three ways in: an admin creates you, SSO vouches for you, or the directory does.

Authenticating and being admitted are different questions, and this product only
ever answered the first one for SSO. Ten real Keycloak sign-ins were refused on
this install in a single afternoon — the provider had already verified every one
of them:

    04:45:03  OIDC id_token claims: ... email=mis@cmhl.com.mm
    04:45:03  OAuth callback failed for provider=keycloak:
              {'code': 'invitation_required', 'message': 'Sign-up is disabled.'}

Two faults, one per door:

  1. SSO had no notion of trusting a provider to ADMIT anyone. Every path in the
     gate asked whether an admin had written the person's name down first — an
     invite, or a domain list. With a directory wired up, that list already
     exists, and keeping a second copy here only creates two places to disagree.

  2. LDAP login read ``settings.dash_config.ldap`` — the FILE — while the settings
     form writes to the DATABASE. Configure LDAP in the UI and `enabled` stayed
     false forever: the hourly group sync worked, and not one directory user
     could sign in. The bind auth was built, tested, and unreachable.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
AUTH = REPO / "backend" / "app" / "core" / "auth.py"
SSO = REPO / "backend" / "app" / "services" / "sso_config_service.py"
ORG = REPO / "backend" / "app" / "services" / "organization_settings_service.py"
SCHEMA = REPO / "backend" / "app" / "schemas" / "organization_settings_schema.py"
BOWCFG = REPO / "backend" / "app" / "settings" / "dash_config.py"
IDP = REPO / "frontend" / "pages" / "settings" / "identity-provider.vue"
COMPOSABLE = REPO / "frontend" / "ee" / "composables" / "useSsoProviders.ts"
EN = REPO / "locales" / "en.json"


def _fn(src: str, header: str) -> str:
    start = src.index(header)
    rest = src[start + len(header):]
    nxt = re.search(r"\n    (?:async def |def |[A-Z_]+ = )", rest)
    return rest[: nxt.start()] if nxt else rest


# ---------------------------------------------------------------------------
# Door 2: SSO admits the people it authenticates
# ---------------------------------------------------------------------------
def test_the_sso_gate_asks_whether_the_provider_admits_new_users():
    """★The fix, in the one place the ten refusals happened."""
    src = AUTH.read_text(encoding="utf-8")
    body = _fn(src, "    async def oauth_callback(")
    assert "_provider_admits_new_users(oauth_name)" in body, (
        "the SSO gate never asks the provider — every new person is refused"
    )


def test_the_provider_check_runs_before_the_domain_list():
    """Not correctness, intent: the switch an admin actually set should be the
    reason someone is let in, not a fallback behind a list they didn't."""
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def oauth_callback(")
    assert body.index("_provider_admits_new_users") < body.index("_has_domain_invite")


def test_the_invite_and_domain_paths_still_work():
    """Additive. Nothing that admitted somebody yesterday stops today."""
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def oauth_callback(")
    assert "_has_domain_invite(account_email, session)" in body
    assert "Membership.user_id.is_(None)" in body
    assert "invitation_required" in body


def test_a_disabled_provider_admits_nobody():
    """★A switch on a provider that cannot mint a login must not be honoured."""
    body = _fn(SSO.read_text(encoding="utf-8"), "    async def provider_admits_new_users(")
    assert body.count('getattr(g, "enabled", False)') == 1
    assert body.count('getattr(p, "enabled", False)') == 1


def test_the_provider_check_fails_closed():
    """★An unreadable config must never widen access. Both layers swallow to
    False, so the ordinary invite checks decide — exactly today's behaviour."""
    svc = _fn(SSO.read_text(encoding="utf-8"), "    async def provider_admits_new_users(")
    assert "except Exception:" in svc and "return False" in svc

    mgr = _fn(AUTH.read_text(encoding="utf-8"), "    async def _provider_admits_new_users(")
    assert "except Exception:" in mgr and "return False" in mgr


def test_an_unknown_provider_name_admits_nobody():
    body = _fn(SSO.read_text(encoding="utf-8"), "    async def provider_admits_new_users(")
    # falls off the provider loop to a bare refusal
    assert re.search(r"\n            return False\n        except", body)


def test_the_gate_opens_its_own_session():
    """★The caller's session is mid-flight inside the OAuth callback; a read of
    instance-global config must not join that transaction."""
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def _provider_admits_new_users(")
    assert "async_session_maker" in body


def test_auto_provision_is_off_by_default_everywhere():
    """★Turning SSO on must never, by itself, hand an account to everyone the
    identity provider knows.

    ★Asserts that NO declaration defaults to True, rather than that some
    declaration defaults to False. The first version did the latter and stayed
    green while a real plant flipped one of the two fields on — the other `=
    False` satisfied the `in`. "At least one is safe" is not the property; "none
    is unsafe" is.
    """
    for f in (BOWCFG, SCHEMA):
        src = f.read_text(encoding="utf-8")
        for line in src.splitlines():
            # ★Strip the inline comment first — `= False  # create app user on
            # LDAP login` does not end with "= False", and the first version of
            # this assertion failed on correct code because of it.
            stripped = line.split("#", 1)[0].strip()
            # ★Only the boolean trust switches. S2 added
            # `auto_provision: AutoProvision = AutoProvision()` — the block
            # holding the ROLE for people a provider admits, which shares this
            # prefix and has no on/off state to be wrong about. Matching on the
            # name alone made this guard fail on correct code.
            if not stripped.startswith(("auto_provision:", "auto_provision_users:")):
                continue
            if stripped.split("=", 1)[0].endswith("bool "):
                assert stripped.endswith("= False"), (
                    f"{f.name}: {stripped!r} — a provider must not admit "
                    f"everyone it knows unless somebody chose that"
                )
        assert "auto_provision" in src


def test_auto_provision_survives_a_round_trip():
    """Every surface carries the field: read it, write it, resolve it.

    ★Asserts per FUNCTION, not a count over the file. A count encodes today's
    implementation — I wrote one, the admission helper legitimately added a
    second `getattr(p, ...)`, and a correct file failed a wrong test twice. What
    matters is that no surface is missing it, and a surface is a function.

    Miss the WRITE and the switch reverts on the next save. Miss the RESOLVE and
    it shows on in the form while the login gate never sees it — the failure
    that looks exactly like success.
    """
    src = SSO.read_text(encoding="utf-8")
    for header in (
        "    async def get_config(",              # read, for the form
        "    async def update_config(",           # write, to the database
        "    async def resolve_oidc_providers(",  # resolve, for the login gate
        "    async def resolve_google(",          # resolve, for the login gate
    ):
        assert "auto_provision" in _fn(src, header), f"{header.strip()} drops auto_provision"


# ---------------------------------------------------------------------------
# Door 3: LDAP login can actually reach the directory
# ---------------------------------------------------------------------------
def test_the_login_path_no_longer_reads_only_the_file():
    """★The whole fault. `_do_authenticate` gated on the file config while the
    UI wrote to the database, so a UI-configured directory authenticated
    nobody."""
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def _do_authenticate(")
    assert "await self._login_ldap_config()" in body
    assert "settings.dash_config.ldap" not in body, (
        "the login gate reads the file again — a directory configured in the UI "
        "would authenticate nobody"
    )


def test_the_bind_uses_the_same_config_that_opened_the_branch():
    """★Otherwise the gate passes on the DB config and the bind is attempted
    against the file's blank url — which surfaces as 'server unreachable'."""
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def _do_authenticate(")
    assert re.search(r"_ldap_authenticate\(\s*credentials\.username,\s*credentials\.password,\s*ldap_config", body, re.S)


def test_the_bind_helper_never_reaches_for_the_file_itself():
    src = AUTH.read_text(encoding="utf-8")
    body = _fn(src, "    async def _ldap_authenticate(")
    code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))
    assert "settings.dash_config.ldap" not in code
    assert "await self._login_ldap_config()" in code


def test_the_login_resolver_prefers_the_database():
    body = _fn(ORG.read_text(encoding="utf-8"), "    async def resolve_login_ldap_config(")
    db_read = body.index("select(OrganizationSettings)")
    file_fallback = body.index("app_settings.dash_config.ldap")
    assert db_read < file_fallback


def test_the_login_resolver_skips_disabled_blocks():
    """A saved-but-off directory must not capture the login path.

    ★The shape of this check changed when the resolver was hardened against a
    tenant naming itself the login authority — the candidates are now collected
    into a dict keyed on the enabled flag rather than skipped in a loop. The
    behavioural proof lives in test_directory_login_is_not_tenant_writable."""
    body = _fn(ORG.read_text(encoding="utf-8"), "    async def resolve_login_ldap_config(")
    assert '"enabled"' in body


def test_the_login_resolver_is_deterministic():
    """★Four workers must never authenticate against different directories.

    This used to be achieved by ordering the scan by organization id and taking
    the first row — which also meant an attacker could WIN that scan by picking
    an id that sorts first. Determinism now comes from there being at most one
    legitimate answer: the org the instance designated, or the only org on the
    instance, or none at all. Ordering carries no authority any more."""
    body = _fn(ORG.read_text(encoding="utf-8"), "    async def resolve_login_ldap_config(")
    code = "\n".join(l.split("#", 1)[0] for l in body.splitlines())
    assert "order_by(OrganizationSettings.organization_id)" not in code, (
        "sorting first is a qualification again"
    )
    assert "login_ldap_org_id" in code


def test_the_login_resolver_returns_the_owning_org():
    """A directory that vouched for somebody is also where they belong. S2 puts
    them there; this is what tells it which org."""
    body = _fn(ORG.read_text(encoding="utf-8"), "    async def resolve_login_ldap_config(")
    assert "str(designated)" in body or "org_id" in body
    assert "return app_settings.dash_config.ldap, None" in body


def test_a_database_blip_falls_back_instead_of_locking_everyone_out():
    body = _fn(AUTH.read_text(encoding="utf-8"), "    async def _login_ldap_config(")
    assert "except Exception" in body
    assert "return settings.dash_config.ldap" in body


def test_the_per_org_resolver_is_untouched():
    """★Sync uses `resolve_ldap_config`, which needs an org and must keep
    needing one. The new resolver is a second entry point, not a replacement."""
    src = ORG.read_text(encoding="utf-8")
    assert "async def resolve_ldap_config(self, db: AsyncSession, organization: Organization):" in src


# ---------------------------------------------------------------------------
# The switch has to be reachable
# ---------------------------------------------------------------------------
def test_the_sso_form_can_set_it():
    src = IDP.read_text(encoding="utf-8")
    assert 'v-model="ssoForm.auto_provision"' in src
    assert "auto_provision: ssoForm.auto_provision" in src          # provider save
    assert "auto_provision: ssoForm.auto_provision }" in src        # google save


def test_the_form_loads_the_saved_value():
    """★Without this the checkbox reads false on every open, and the first save
    of any other field silently turns admission back off."""
    src = IDP.read_text(encoding="utf-8")
    assert "ssoForm.auto_provision = !!p.auto_provision" in src
    assert "ssoForm.auto_provision = !!g.auto_provision" in src


def test_a_newly_enabled_provider_starts_untrusted():
    src = IDP.read_text(encoding="utf-8")
    assert "ssoForm.auto_provision = false" in src


def test_the_composable_declares_the_field():
    src = COMPOSABLE.read_text(encoding="utf-8")
    assert src.count("auto_provision: boolean") == 2   # provider + google


def test_the_ldap_switch_was_already_reachable():
    """`auto_provision_users` has existed and had a checkbox all along — the
    directory door was one config lookup away from working, not a rewrite."""
    assert 'v-model="ldapForm.auto_provision_users"' in IDP.read_text(encoding="utf-8")
    assert "auto_provision_users" in ORG.read_text(encoding="utf-8")


def test_the_new_strings_exist():
    """★vue-i18n renders the KEY on a miss. This has happened three times here."""
    import json

    ip = json.loads(EN.read_text(encoding="utf-8"))["settings"]["identityProvider"]
    assert ip["ssoAutoProvision"]
    assert ip["ssoAutoProvisionHint"]
