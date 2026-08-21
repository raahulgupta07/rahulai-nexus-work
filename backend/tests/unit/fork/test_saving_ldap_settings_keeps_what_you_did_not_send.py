"""DEF-D — the LDAP settings save was a REPLACE that reads as a PATCH.

`update_ldap` rebuilt the whole block from the payload:

    ldap = {"enabled": bool(data.enabled)}
    for f in self._LDAP_FIELDS:
        ldap[f] = getattr(data, f)

so every field the caller omitted was written as that field's pydantic
**default** — not null, the default, which is worse, because a reset looks like
a decision somebody made. The request still answered 200.

Measured live on 0.0.543.15, both halves inside one hour:

  * a PUT of only ``{"group_search_filter": …}`` wiped ``enabled``, ``url``,
    ``bind_dn`` and ``base_dn``. The next call answered
    ``400 "LDAP is not configured"`` — directory sign-in gone for the whole
    organization, from a request that reported success.
  * a PUT naming 13 fields but omitting ``auto_provision_users`` set it False,
    so only BRAND-NEW people were refused (``ldap_not_provisioned`` in the log)
    while existing accounts kept working. That reads as an intermittent
    directory fault. It cost a wrong diagnosis: three journey tests went red and
    were briefly read as an LDAP regression. ``use_ssl`` flipped to its default
    True against an ``ldap://`` URL in the same request.

★The author already knew this shape and had solved it for exactly one field —
``bind_password_enc`` is preserved on omission. That single line is the proof
the problem was understood and generalised nowhere.

★Same landmine as ``ReportScheduleRequest.cron_expression_supplied``, already
recorded in CLAUDE.md: nothing but ``model_fields_set`` separates "field
omitted" from "field explicitly null".

★Runs against the real service method with a fake session — no schema, so it is
safe in `tests/unit/fork/` where the migration fixture is a no-op.
"""
import ast
import asyncio
import pathlib
import re

import pytest
from fastapi import HTTPException

from app.schemas.organization_settings_schema import OrgLdapUpdate


BACKEND = pathlib.Path(__file__).resolve().parents[3]
SERVICE = BACKEND / "app" / "services" / "organization_settings_service.py"


# --------------------------------------------------------------------------
# A settings row and a session, thin enough to need no database.
# --------------------------------------------------------------------------

class _Settings:
    def __init__(self, config):
        self.id = "settings-1"
        self.config = config
        self.updated_at = None


class _Session:
    """Enough AsyncSession for `update_ldap`: add / commit / refresh."""

    def __init__(self):
        self.committed = 0

    def add(self, _obj):
        pass

    async def commit(self):
        self.committed += 1

    async def refresh(self, _obj):
        pass

    async def execute(self, *_a, **_k):  # audit_service.log may reach for it
        raise RuntimeError("no database in this test")


class _Org:
    id = "org-1"


class _User:
    id = "user-1"


def _save(existing_ldap, payload_json):
    """Run the real `update_ldap` over `existing_ldap` and return the stored block.

    `payload_json` is a dict of ONLY the keys the caller sends — that is the
    whole point, so it is built with `model_validate` rather than by naming
    every field.
    """
    from app.services.organization_settings_service import OrganizationSettingsService

    svc = OrganizationSettingsService()
    row = _Settings({"ldap": dict(existing_ldap)} if existing_ldap is not None else {})

    async def _get_settings(db, organization, current_user):
        return row

    svc.get_settings = _get_settings  # bound-method override on the instance

    # ★`flag_modified` wants a real mapped instance (`_sa_instance_state`), and
    # it is a persistence detail, not the behaviour under test. Stubbed
    # EXPLICITLY rather than left to be swallowed: my first version of this
    # helper had a broad `except Exception` that hid the resulting
    # AttributeError, so 15 of 16 cases "passed" without the service ever
    # completing. A test that cannot fail is worse than no test.
    import app.services.organization_settings_service as _svc_mod
    _real_flag = _svc_mod.flag_modified
    _svc_mod.flag_modified = lambda *_a, **_k: None

    payload = OrgLdapUpdate.model_validate(payload_json)
    try:
        asyncio.run(svc.update_ldap(_Session(), _Org(), _User(), payload))
    except HTTPException:
        # ★Never swallowed. The refusal IS the behaviour under test in
        # `test_enabling_without_a_url_is_still_refused`, and my first version of
        # this helper caught it in a broad `except` whose escape condition
        # ("has an ldap key") was true for the empty-org case too — so a
        # correctly-refusing service read as not refusing at all.
        raise
    except RuntimeError as e:
        # The audit write is the only thing that wants a real database, and it
        # runs AFTER the config is committed.
        if "no database" not in str(e):
            raise
    finally:
        _svc_mod.flag_modified = _real_flag
    return row.config["ldap"]


FULL = {
    "enabled": True,
    "url": "ldap://test-ldap:1389",
    "bind_dn": "cn=admin,dc=cityagent,dc=io",
    "base_dn": "dc=cityagent,dc=io",
    "use_ssl": False,
    "user_search_base": "ou=people,dc=cityagent,dc=io",
    "user_search_filter": "(objectClass=inetOrgPerson)",
    "group_search_filter": "(objectClass=groupOfNames)",
    "auto_provision_users": True,
    "sync_interval_minutes": 60,
    "bind_password_enc": "gAAAAA-pretend-ciphertext",
}


class TestTheTwoMeasuredFailures:
    def test_a_one_field_put_does_not_wipe_the_server(self):
        """★The first measured failure, exactly as it happened."""
        after = _save(FULL, {"group_search_filter": "(objectClass=posixGroup)"})
        assert after["group_search_filter"] == "(objectClass=posixGroup)"
        assert after["enabled"] is True
        assert after["url"] == "ldap://test-ldap:1389"
        assert after["bind_dn"] == "cn=admin,dc=cityagent,dc=io"
        assert after["base_dn"] == "dc=cityagent,dc=io"

    def test_omitting_auto_provision_does_not_switch_it_off(self):
        """★The second, and the crueller one: it refuses only NEW people, so it
        looks like an intermittent directory fault rather than a setting."""
        after = _save(FULL, {"url": "ldap://elsewhere:1389", "enabled": True})
        assert after["auto_provision_users"] is True

    def test_omitting_use_ssl_does_not_flip_it_to_the_default(self):
        """`use_ssl` defaults to True, so a plain `ldap://` install had TLS
        switched on underneath it by a request that never mentioned TLS."""
        after = _save(FULL, {"bind_dn": "cn=other,dc=cityagent,dc=io"})
        assert after["use_ssl"] is False

    def test_omitting_enabled_does_not_disable_the_directory(self):
        """★The dangerous one. Directory sign-in off for the whole org, 200."""
        after = _save(FULL, {"page_size": 250})
        assert after["enabled"] is True


class TestWhatOmissionMeansIsNotWhatNullMeans:
    def test_an_explicit_null_still_clears(self):
        """★Absence preserves; an explicit null CLEARS. Collapsing the two is
        the same defect with the sign reversed — you could never empty a field
        again."""
        after = _save(FULL, {"group_search_base": None})
        assert after["group_search_base"] is None

    def test_an_explicit_false_is_honoured(self):
        """A boolean is where "omitted" and "sent false" look most alike."""
        after = _save(FULL, {"auto_provision_users": False})
        assert after["auto_provision_users"] is False

    def test_disabling_is_still_possible(self):
        after = _save(FULL, {"enabled": False})
        assert after["enabled"] is False


class TestAFirstWriteStillGetsCompleteDefaults:
    def test_an_empty_org_gets_the_schema_defaults(self):
        """★There is nothing to preserve on a first write, so the defaults are
        the right answer — a merge that stored only the named keys would leave a
        half-populated block and `resolve_ldap_config` reading missing fields."""
        after = _save({}, {"enabled": True, "url": "ldap://x:389", "base_dn": "dc=x"})
        assert after["user_email_attribute"] == "mail"
        assert after["group_member_attribute"] == "member"
        assert after["page_size"] == 500

    def test_every_field_is_present_after_a_first_write(self):
        from app.services.organization_settings_service import OrganizationSettingsService
        after = _save({}, {"enabled": True, "url": "ldap://x:389", "base_dn": "dc=x"})
        for f in OrganizationSettingsService._LDAP_FIELDS:
            assert f in after, f
        assert "enabled" in after


class TestTheThingsThisMustNotChange:
    def test_the_password_is_still_kept_on_omission(self):
        """★This was the ONE field already handled. It must stay handled — the
        form deliberately never round-trips the secret."""
        after = _save(FULL, {"url": "ldap://elsewhere:1389"})
        assert after["bind_password_enc"] == "gAAAAA-pretend-ciphertext"

    def test_the_full_form_post_is_unchanged(self):
        """★The settings UI sends every field. A merge must produce exactly what
        a replace produced for that caller, or this fix breaks the screen it was
        supposed to leave alone."""
        payload = dict(FULL)
        payload.pop("bind_password_enc")
        after = _save(FULL, payload)
        for k, v in payload.items():
            assert after[k] == v, k

    def test_enabling_without_a_url_is_still_refused(self):
        with pytest.raises(HTTPException) as exc:
            _save({}, {"enabled": True, "base_dn": "dc=x"})
        assert exc.value.status_code == 400

    def test_validation_sees_the_MERGED_state_not_just_the_payload(self):
        """★A PUT that only flips `enabled` must validate against the url the
        org already has, not against the payload's empty one — otherwise
        enabling an already-configured directory 400s."""
        after = _save(FULL, {"enabled": True})
        assert after["url"] == "ldap://test-ldap:1389"


class TestTheMechanismIsTheRightOne:
    def test_it_reads_model_fields_set(self):
        """★Nothing else separates omitted from explicitly-null. A fix that
        compared values to defaults would make it impossible to ever set a field
        back to its default on purpose."""
        src = SERVICE.read_text(encoding="utf-8")
        block = src[src.index("async def update_ldap"):]
        block = block[:block.index("current_config[\"ldap\"] = ldap")]
        assert "model_fields_set" in block

    def test_the_old_unconditional_rebuild_is_gone(self):
        src = SERVICE.read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "update_ldap"
        )
        body = ast.unparse(fn)
        body = re.sub(r'^\s*#.*$', '', body, flags=re.M)
        assert 'ldap = {\'enabled\': bool(data.enabled)}' not in body

    def test_the_original_defect_is_still_detected(self):
        """★Carry the red proof IN the test. Reconstruct the old rebuild and
        require it to still lose the fields it lost, so this file keeps meaning
        something after the broken code is long gone."""
        fields = ("url", "use_ssl", "auto_provision_users")
        payload = OrgLdapUpdate.model_validate({"group_search_filter": "(objectClass=posixGroup)"})
        replaced = {"enabled": bool(payload.enabled)}
        for f in fields:
            replaced[f] = getattr(payload, f)
        assert replaced["enabled"] is False          # the org would be switched off
        assert replaced["url"] is None               # the server address, gone
        assert replaced["use_ssl"] is True           # flipped to a default nobody sent
        assert replaced["auto_provision_users"] is False
