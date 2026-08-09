"""Licensing, as this fork actually behaves.

★★★THE PRODUCT IS PERMANENTLY LICENSED. ``ee/license.py`` returns a standing
enterprise grant — no key, no server, no expiry, ``max_users``/``max_agents``
unlimited. Upstream's version of this file asserted the opposite at 13 points
("no key ⇒ 402", "an expired JWT downgrades you"), which on this tree can only
ever fail. Those were not bugs to fix; they were assertions about behaviour that
was deliberately removed.

★What replaced them, and why it is not simply "delete the paywall tests":

  1. The GRANT is asserted directly, so the unlock is documented and tested
     rather than merely true.
  2. Every gate is still exercised against a *withheld* licence, by injecting
     ``_cached_license`` as community. ``require_enterprise``,
     ``is_datasource_allowed`` and the ``user_required`` guard therefore keep
     real coverage — the enforcement code still exists and still runs, this
     fork just never hands it a reason to refuse. Deleting these would have
     left that code untested forever.
  3. The injection contract itself is pinned (``TestInjectionIsHonored``),
     because it is load-bearing for the whole e2e suite and was silently broken
     until 2026-08-07.

★What was DELETED outright: the seven licence-VALIDATOR tests (valid / expired /
invalid-signature / malformed / expiring-live / explicit-``ds_`` / endpoint-
enterprise-from-a-key). They drove ``_validate_license_key`` by signing a JWT
with a throwaway RSA keypair and swapping ``LICENSE_PUBLIC_KEY``. That validator
is gone — ``get_license_info`` never consults a configured key, so restoring the
tests would mean restoring a code path where a stray or expired
``DASH_LICENSE_KEY`` could LOCK an installation that is supposed to be
permanently unlocked. The tests went with the machinery, deliberately.
"""
import contextlib

import pytest

from app.ee import license as ee_license


@contextlib.contextmanager
def _license(**kwargs):
    """Force a specific LicenseInfo for the duration of the block.

    ★This is the mechanism that actually takes effect — see ``get_license_info``.
    Writing ``settings.dash_config.license.key`` (what upstream's fixtures did)
    is read by nothing on this fork.
    """
    saved_cached = ee_license._cached_license
    saved_initialized = ee_license._cache_initialized
    ee_license._cached_license = ee_license.LicenseInfo(**kwargs)
    ee_license._cache_initialized = True
    try:
        yield
    finally:
        ee_license._cached_license = saved_cached
        ee_license._cache_initialized = saved_initialized


@contextlib.contextmanager
def _no_license():
    """Take the path a real installation with no licence key takes.

    ★``tests/e2e/conftest.py`` injects a session-wide licence, so a test that
    does not clear it is answered by THAT grant and proves nothing about an
    unlicensed instance.
    """
    saved_cached = ee_license._cached_license
    saved_initialized = ee_license._cache_initialized
    ee_license._cached_license = None
    ee_license._cache_initialized = False
    try:
        yield
    finally:
        ee_license._cached_license = saved_cached
        ee_license._cache_initialized = saved_initialized


def _community():
    return _license(licensed=False, tier="community")


def _headers(token, org_id):
    return {"Authorization": f"Bearer {token}", "X-Organization-Id": org_id}


def _new_admin(create_user, login_user, whoami):
    user = create_user()
    token = login_user(user["email"], user["password"])
    return token, whoami(token)["organizations"][0]["id"]


@pytest.mark.e2e
class TestStandingGrant:
    """With NO licence configured, the instance is fully enterprise."""

    def test_no_license_still_grants_enterprise(self, test_client):
        with _no_license():
            info = ee_license.get_license_info()

        assert info.licensed is True
        assert info.tier == "enterprise"
        assert info.expires_at is None, "a grant that can expire is not a grant"
        assert ee_license.is_enterprise_licensed() is True

    def test_grant_includes_every_enterprise_feature(self, test_client):
        with _no_license():
            info = ee_license.get_license_info()
            # ★Compared against TIER_FEATURES rather than a second hardcoded
            # list, so adding a feature there cannot leave this test asserting
            # a stale set and silently passing.
            for feature in ee_license.TIER_FEATURES["enterprise"]:
                assert ee_license.has_feature(feature) is True, feature
        assert set(info.features) == set(ee_license.TIER_FEATURES["enterprise"])

    def test_grant_has_no_seat_or_agent_cap(self, test_client):
        with _no_license():
            assert ee_license.get_max_users() == -1
            assert ee_license.get_max_agents() == -1

    def test_every_enterprise_datasource_is_allowed(self, test_client):
        with _no_license():
            for ds_type in ee_license.ENTERPRISE_DATASOURCES:
                assert ee_license.is_datasource_allowed(ds_type) is True, ds_type


@pytest.mark.e2e
class TestInjectionIsHonored:
    """``_cached_license`` must override the grant.

    ★★★This is a regression guard, not a licensing test. Until 2026-08-07
    ``get_license_info`` returned the grant unconditionally and DISCARDED this
    global — so ``tests/e2e/conftest.py``, ``test_seat_cap_autoprovision``,
    ``test_pii_protection``, ``test_connection_rate_limit`` and
    ``rbac/conftest.py`` were all injecting licences that did nothing. Seat-cap
    enforcement had no coverage at all as a result. If this class fails, that
    whole family of tests has quietly stopped meaning anything again.
    """

    def test_injected_quota_wins_over_the_grant(self, test_client):
        with _license(licensed=True, tier="enterprise", max_users=3, max_agents=2):
            assert ee_license.get_max_users() == 3
            assert ee_license.get_max_agents() == 2

    def test_injected_community_wins_over_the_grant(self, test_client):
        with _community():
            assert ee_license.get_license_info().licensed is False
            assert ee_license.is_enterprise_licensed() is False

    def test_injected_feature_list_is_not_widened(self, test_client):
        with _license(licensed=True, tier="enterprise", features=["audit_logs"]):
            assert ee_license.has_feature("audit_logs") is True
            # ★In TIER_FEATURES["enterprise"], so a dropped injection would
            # answer True here off the grant. That is exactly the bug this
            # catches — picking a feature OUTSIDE the tier would pass either way.
            assert ee_license.has_feature("pii_protection") is False


@pytest.mark.e2e
class TestHasFeature:

    def test_tier_defaults_apply_when_no_explicit_features(self, test_client):
        with _license(licensed=True, tier="enterprise", features=[]):
            for feature in ee_license.TIER_FEATURES["enterprise"]:
                assert ee_license.has_feature(feature) is True, feature

    def test_team_tier_does_not_get_enterprise_features(self, test_client):
        with _license(licensed=True, tier="team", features=[]):
            assert ee_license.has_feature("audit_logs") is True
            assert ee_license.has_feature("scim") is False

    def test_community_has_no_features(self, test_client):
        with _community():
            assert ee_license.has_feature("audit_logs") is False
            assert ee_license.has_feature("scim") is False


@pytest.mark.e2e
class TestLicenseAPIEndpoint:

    def test_endpoint_reports_enterprise_without_a_license(self, test_client):
        with _no_license():
            response = test_client.get("/api/license")

        assert response.status_code == 200
        data = response.json()
        assert data["licensed"] is True
        assert data["tier"] == "enterprise"

    def test_endpoint_reflects_a_withheld_license(self, test_client):
        # ★Proves the endpoint reads the licence live rather than serving a
        # constant. Without this, the test above passes on a hardcoded response.
        with _community():
            response = test_client.get("/api/license")

        assert response.status_code == 200
        assert response.json()["licensed"] is False
        assert response.json()["tier"] == "community"


@pytest.mark.e2e
class TestAuditLogsGating:
    """``@require_enterprise("audit_logs")`` — open here, but still enforcing."""

    def test_audit_logs_available_without_a_license(
        self, test_client, create_user, login_user, whoami,
    ):
        token, org_id = _new_admin(create_user, login_user, whoami)
        with _no_license():
            response = test_client.get("/api/enterprise/audit", headers=_headers(token, org_id))

        assert response.status_code == 200, response.text
        assert "items" in response.json()

    def test_audit_logs_refused_when_the_license_is_withheld(
        self, test_client, create_user, login_user, whoami,
    ):
        # ★Keeps require_enterprise's REFUSAL path covered. This fork never
        # reaches it in production, and the decorator is still live code.
        token, org_id = _new_admin(create_user, login_user, whoami)
        with _community():
            response = test_client.get("/api/enterprise/audit", headers=_headers(token, org_id))

        assert response.status_code == 402
        assert "enterprise license" in response.json()["detail"].lower()


@pytest.mark.e2e
class TestDataSourceLicensing:

    def test_community_datasources_always_allowed(self, test_client):
        with _community():
            assert ee_license.is_datasource_allowed("postgresql") is True
            assert ee_license.is_datasource_allowed("mysql") is True
            assert ee_license.is_datasource_allowed("sqlite") is True

    def test_enterprise_datasources_allowed_without_a_license(self, test_client):
        with _no_license():
            assert ee_license.is_datasource_allowed("powerbi") is True
            assert ee_license.is_datasource_allowed("qvd") is True

    def test_enterprise_datasources_refused_when_withheld(self, test_client):
        with _community():
            assert ee_license.is_datasource_allowed("powerbi") is False
            assert ee_license.is_datasource_allowed("qvd") is False

    def test_explicit_ds_features_restrict_to_those_only(self, test_client):
        with _license(licensed=True, tier="enterprise", features=["ds_powerbi"]):
            assert ee_license.is_datasource_allowed("powerbi") is True
            assert ee_license.is_datasource_allowed("qvd") is False


@pytest.mark.e2e
class TestUserAuthPolicyLicensing:
    """``auth_policy=user_required`` is the per-user connector path."""

    def test_user_required_allowed_without_a_license(
        self, test_client, create_user, login_user, whoami,
    ):
        token, org_id = _new_admin(create_user, login_user, whoami)
        with _no_license():
            response = test_client.post(
                "/api/connections",
                json={
                    "name": "Test Connection",
                    "type": "postgresql",
                    "config": {"host": "localhost", "port": 5432, "database": "test"},
                    "credentials": {"username": "test", "password": "test"},
                    "auth_policy": "user_required",
                },
                headers=_headers(token, org_id),
            )

        # ★!= 402, not == 200: there is no real postgres here, so connection
        # validation may legitimately refuse. The licence check is the subject.
        assert response.status_code != 402, response.text

    def test_user_required_refused_when_the_license_is_withheld(
        self, test_client, create_user, login_user, whoami,
    ):
        token, org_id = _new_admin(create_user, login_user, whoami)
        with _community():
            response = test_client.post(
                "/api/connections",
                json={
                    "name": "Test Connection",
                    "type": "postgresql",
                    "config": {"host": "localhost", "port": 5432, "database": "test"},
                    "credentials": {"username": "test", "password": "test"},
                    "auth_policy": "user_required",
                },
                headers=_headers(token, org_id),
            )

        assert response.status_code == 402
        assert "enterprise license" in response.json()["detail"].lower()

    def test_system_only_allowed_without_a_license(
        self, test_client, create_user, login_user, whoami,
    ):
        token, org_id = _new_admin(create_user, login_user, whoami)
        with _no_license():
            response = test_client.post(
                "/api/connections",
                json={
                    "name": "Test Connection",
                    "type": "postgresql",
                    "config": {"host": "localhost", "port": 5432, "database": "test"},
                    "credentials": {"username": "test", "password": "test"},
                    "auth_policy": "system_only",
                },
                headers=_headers(token, org_id),
            )

        assert response.status_code != 402, response.text
