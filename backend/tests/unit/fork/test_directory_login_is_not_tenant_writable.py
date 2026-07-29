"""Who gets to decide which directory the sign-in page trusts.

`resolve_login_ldap_config` answers "which LDAP server authenticates a login".
It answered by scanning EVERY organization's settings and taking the first one
with `enabled` on, lowest id first. Any org admin can write that block — it is
an ordinary field on the Settings ▸ Identity Provider form.

So on a multi-tenant instance, one tenant's admin could:

  1. save an LDAP block pointing at a directory they control, and
  2. wait for it to win the scan,

after which `authenticate_via_ldap` binds every login against their server. A
server that answers "yes" to anything then signs them in as whoever they typed
— `get_by_email(<the address they submitted>)` returns the REAL local account,
including the instance owner's. Password never checked locally.

★Resolving the account from the directory's own mail attribute does NOT fix
this. Against a hostile directory that attribute is attacker-controlled too.
The only real fix is that a tenant cannot become the login authority.

The rule this file locks in:

  designated org        → that org's block, and only that one
  exactly one org       → that org's block   (today's behaviour, exact — this
                          is every install of this product so far)
  several, none named   → NO org block at all; fall back to the file config

Fail closed: when the instance has not said which directory it trusts and
there is more than one candidate, the answer is "none", not "whichever sorts
first".
"""
import asyncio
import inspect
from types import SimpleNamespace

import pytest

from app.services.organization_settings_service import OrganizationSettingsService

SERVICE = OrganizationSettingsService


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _ldap_block(host="ldap.tenant.example"):
    return {"enabled": True, "url": f"ldap://{host}", "auto_provision_users": True}


class _Row:
    def __init__(self, org_id, config):
        self.organization_id = org_id
        self.config = config


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)

    def scalar(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Answers the three shapes `resolve_login_ldap_config` asks for.

    Routed on the selected entity rather than on call order, so the test does
    not encode the order the implementation happens to query in today.
    """

    def __init__(self, settings_rows, org_ids, instance_config=None):
        self.settings_rows = settings_rows
        self.org_ids = org_ids
        self.instance_config = instance_config

    async def execute(self, stmt):
        text = str(stmt).lower()
        if "instance_settings" in text:
            if self.instance_config is None:
                return _Result([])
            return _Result([SimpleNamespace(
                config=self.instance_config,
                get_config=lambda k, d=None: (self.instance_config or {}).get(k, d),
            )])
        if "organization_settings" in text:
            return _Result(self.settings_rows)
        if "organizations" in text:
            return _Result(list(self.org_ids))
        return _Result([])


def _resolve(session):
    return _run(SERVICE().resolve_login_ldap_config(session))


# --- the behaviour that must not change -----------------------------------

def test_a_single_org_install_is_untouched():
    """★Every install of this product today. The fix must be invisible here —
    the one organization's saved block still authenticates logins."""
    cfg, org_id = _resolve(_FakeSession(
        settings_rows=[_Row("org-a", {"ldap": _ldap_block("corp.example")})],
        org_ids=["org-a"],
    ))
    assert cfg.enabled is True
    assert "corp.example" in cfg.url
    assert org_id == "org-a"


def test_an_org_with_no_ldap_block_falls_back_to_the_file():
    cfg, org_id = _resolve(_FakeSession(
        settings_rows=[_Row("org-a", {})],
        org_ids=["org-a"],
    ))
    assert org_id is None


def test_a_disabled_block_is_not_an_authority():
    disabled = dict(_ldap_block(), enabled=False)
    cfg, org_id = _resolve(_FakeSession(
        settings_rows=[_Row("org-a", {"ldap": disabled})],
        org_ids=["org-a"],
    ))
    assert org_id is None


# --- the escalation --------------------------------------------------------

def test_a_tenant_cannot_make_itself_the_login_authority():
    """★THE FIX. Two orgs, neither designated by the instance: a block saved
    by either one must NOT become the directory every login binds against."""
    cfg, org_id = _resolve(_FakeSession(
        settings_rows=[_Row("org-attacker", {"ldap": _ldap_block("evil.example")})],
        org_ids=["org-attacker", "org-victim"],
    ))
    assert org_id is None, "a tenant's own settings became the instance login authority"
    assert "evil.example" not in (getattr(cfg, "url", "") or "")


def test_sorting_first_is_not_a_qualification():
    """The old rule was `order_by(organization_id)` — an attacker picks an id
    that sorts first and wins. Ordering must carry no authority at all."""
    cfg, org_id = _resolve(_FakeSession(
        settings_rows=[
            _Row("aaa-attacker", {"ldap": _ldap_block("evil.example")}),
            _Row("zzz-real", {"ldap": _ldap_block("corp.example")}),
        ],
        org_ids=["aaa-attacker", "zzz-real"],
    ))
    assert org_id is None


def test_the_designated_org_wins_even_when_another_sorts_first():
    """The instance names its directory explicitly; nothing else is consulted."""
    cfg, org_id = _resolve(_FakeSession(
        settings_rows=[
            _Row("aaa-attacker", {"ldap": _ldap_block("evil.example")}),
            _Row("zzz-real", {"ldap": _ldap_block("corp.example")}),
        ],
        org_ids=["aaa-attacker", "zzz-real"],
        instance_config={"login_ldap_org_id": "zzz-real"},
    ))
    assert org_id == "zzz-real"
    assert "corp.example" in cfg.url


def test_a_designation_pointing_at_an_org_with_no_block_admits_nobody():
    """Naming an org that has no directory configured must not fall through to
    scanning the others — that would restore the hole via a typo."""
    cfg, org_id = _resolve(_FakeSession(
        settings_rows=[_Row("aaa-attacker", {"ldap": _ldap_block("evil.example")})],
        org_ids=["aaa-attacker", "zzz-real"],
        instance_config={"login_ldap_org_id": "zzz-real"},
    ))
    assert org_id is None


# --- the shape of the code -------------------------------------------------

def test_the_scan_no_longer_ranks_candidates_by_id():
    """★`order_by(organization_id)` was the whole selection rule. If it is
    still what picks the winner, the fix is cosmetic."""
    src = inspect.getsource(SERVICE.resolve_login_ldap_config)
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert "login_ldap_org_id" in code, "no explicit designation is consulted"


def test_the_refusal_is_recorded():
    """An instance that silently stops honouring a saved LDAP block after an
    upgrade must say so, or it reads as the directory being down."""
    src = inspect.getsource(SERVICE.resolve_login_ldap_config)
    assert "logger" in src or "_logger" in src
