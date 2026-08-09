"""Unit tests for licence quota reads (max_users / max_agents).

Pure helpers — no DB, no signing key.

★★★THIS FILE PREVIOUSLY ASSERTED THE OPPOSITE, ON PURPOSE, AND WAS RIGHT TO.
``test_getters_ignore_any_cached_caps`` pinned the behaviour that
``get_license_info()`` returns a fixed unlimited grant and never consults
``_cached_license``, and its docstring said it should fail "if licensing gates
are ever reintroduced without that being an explicit decision." It fired on
0.0.526.1. This is that explicit decision, recorded here rather than silently
deleting the guard that caught it:

  - Production behaviour is UNCHANGED. No production code writes
    ``_cached_license``; the sole non-test assignment in the tree is
    ``clear_license_cache()`` setting it to ``None``. A running instance still
    gets the permanent enterprise grant with unlimited quotas.
  - What changed is that an EXPLICIT injection is now honoured, because eight
    test modules (``tests/e2e/conftest.py``, ``test_seat_cap_autoprovision``,
    ``test_pii_protection``, ``test_connection_rate_limit``,
    ``rbac/conftest.py`` …) drive scenarios that way and every one of them was
    being silently discarded. Seat-cap enforcement in the SCIM/OIDC/LDAP
    auto-provisioning paths had NO coverage as a result.
  - So the guard's real intent — "no licensing gate appears without a decision"
    — is preserved by ``test_an_unwritten_cache_still_grants_everything``,
    which pins the property that actually matters.

★``_coerce_limit`` and its parametrised test went with the licence VALIDATOR
(``_validate_license_key``) in the same release. It normalised quota claims out
of a signed JWT; with no JWT parsing left it had no caller, and keeping a
function alive purely to keep its test alive is backwards.
"""
import pytest

import app.ee.license as license_mod
from app.ee.license import LicenseInfo, get_max_users, get_max_agents


def test_license_info_defaults_to_unlimited():
    info = LicenseInfo()
    assert info.max_users == -1
    assert info.max_agents == -1


@pytest.fixture
def restore_license_cache():
    prev_cache = license_mod._cached_license
    prev_init = license_mod._cache_initialized
    yield
    license_mod._cached_license = prev_cache
    license_mod._cache_initialized = prev_init


def test_an_unwritten_cache_still_grants_everything(restore_license_cache):
    """The property that protects the product: nothing set → no limits.

    ★This is the one that stands in for the old inverted guard. Production never
    writes ``_cached_license``, so this is the state a real installation is
    always in. If a future change makes an unlicensed instance capped, this
    fails — which was the original guard's whole purpose.
    """
    license_mod._cached_license = None
    license_mod._cache_initialized = False

    info = license_mod.get_license_info()
    assert info.licensed is True
    assert info.tier == "enterprise"
    assert get_max_users() == -1
    assert get_max_agents() == -1


def test_an_injected_cap_is_honoured(restore_license_cache):
    """Tests can impose a cap; the product never does.

    ★Inverted from ``test_getters_ignore_any_cached_caps``. See the module
    docstring for why. Without this, ``test_seat_cap_autoprovision`` and
    ``test_license_limits`` (e2e) go back to passing vacuously.
    """
    license_mod._cached_license = LicenseInfo(
        licensed=True, tier="enterprise", max_users=7, max_agents=3
    )
    license_mod._cache_initialized = True
    assert get_max_users() == 7
    assert get_max_agents() == 3


def test_getters_unlimited_when_unlicensed(restore_license_cache):
    """A community licence carries no explicit quota, so it reads as unlimited.

    ★Quotas are a property of an ACTIVE licence. Community mode blocks features
    via ``has_feature`` / ``require_enterprise``, not via a seat cap of zero —
    asserting 0 here would invent enforcement that does not exist.
    """
    license_mod._cached_license = LicenseInfo(licensed=False, tier="community")
    license_mod._cache_initialized = True
    assert get_max_users() == -1
    assert get_max_agents() == -1
