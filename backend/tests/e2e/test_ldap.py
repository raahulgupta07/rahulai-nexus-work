"""
Tests for LDAP directory sync and admin endpoints.

Uses mocked LDAP responses — does not require a real LDAP server.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
# ★Still needed at line ~192 for a mocked sync timestamp, even though the
# licence keygen that also used it is gone. `timedelta` went with the keygen.
from datetime import datetime, timezone


@pytest.fixture
def enterprise_license():
    """Grant an enterprise licence carrying the LDAP feature, for one test.

    ★See the same fixture in ``test_scim.py``. The previous version generated a
    2048-bit RSA keypair per module, signed a JWT, swapped
    ``license_module.LICENSE_PUBLIC_KEY`` and wrote the token to
    ``settings.dash_config.license.key`` — none of which is read.
    ``get_license_info()`` returns a standing grant on this fork and never
    consults a configured key, so the fixture decided nothing.

    ★Injecting ``_cached_license`` is the mechanism that DOES take effect, so
    this grant is real and a narrower feature list would genuinely turn LDAP off.
    """
    from app.ee import license as ee_license

    saved_cached = ee_license._cached_license
    saved_initialized = ee_license._cache_initialized
    ee_license._cached_license = ee_license.LicenseInfo(
        licensed=True,
        tier="enterprise",
        org_name="LDAP Test Corp",
        features=["ldap"],
        license_id="lic_test_ldap",
    )
    ee_license._cache_initialized = True
    try:
        yield
    finally:
        ee_license._cached_license = saved_cached
        ee_license._cache_initialized = saved_initialized


@pytest.fixture
def enable_ldap():
    """Enable LDAP in settings for the duration of the test."""
    from app.settings.config import settings

    original_enabled = settings.dash_config.ldap.enabled
    settings.dash_config.ldap.enabled = True
    settings.dash_config.ldap.url = "ldaps://mock-ldap.test:636"
    settings.dash_config.ldap.base_dn = "dc=test,dc=com"
    settings.dash_config.ldap.bind_dn = "cn=admin,dc=test,dc=com"
    settings.dash_config.ldap.bind_password = "admin_pass"
    yield
    settings.dash_config.ldap.enabled = original_enabled


@pytest.fixture
def ldap_setup(test_client, create_user, login_user, whoami, enterprise_license, enable_ldap):
    """Set up a user, org, and auth headers for LDAP admin endpoints."""
    user = create_user()
    token = login_user(user["email"], user["password"])
    user_info = whoami(token)
    org_id = user_info['organizations'][0]['id']

    return {
        "user_token": token,
        "org_id": org_id,
        "headers": {
            "Authorization": f"Bearer {token}",
            "X-Organization-Id": org_id,
        },
    }


# ── Mock data ──

MOCK_LDAP_USERS = [
    {"dn": "cn=alice,ou=Users,dc=test,dc=com", "email": "alice@test.com", "name": "Alice Smith"},
    {"dn": "cn=bob,ou=Users,dc=test,dc=com", "email": "bob@test.com", "name": "Bob Jones"},
    {"dn": "cn=charlie,ou=Users,dc=test,dc=com", "email": "charlie@test.com", "name": "Charlie Brown"},
]

MOCK_LDAP_GROUPS = [
    {
        "dn": "cn=Engineering,ou=Groups,dc=test,dc=com",
        "name": "Engineering",
        "members": [
            "cn=alice,ou=Users,dc=test,dc=com",
            "cn=bob,ou=Users,dc=test,dc=com",
        ],
    },
    {
        "dn": "cn=Marketing,ou=Groups,dc=test,dc=com",
        "name": "Marketing",
        "members": [
            "cn=charlie,ou=Users,dc=test,dc=com",
        ],
    },
]


def _mock_connection_manager():
    """Create a mock LDAPConnectionManager."""
    mock = MagicMock()
    mock.search_users.return_value = MOCK_LDAP_USERS
    mock.search_groups.return_value = MOCK_LDAP_GROUPS
    mock.test_connection.return_value = {
        "connected": True,
        "server": "ldaps://mock-ldap.test:636",
        "vendor": "MockLDAP",
    }
    return mock


# ============================================================================
# LDAP Admin Endpoint Tests
# ============================================================================


@pytest.mark.e2e
class TestLdapTestConnection:

    def test_test_connection_success(self, ldap_setup, test_client):
        """Test the test-connection endpoint with mocked LDAP."""
        with patch("app.ee.ldap.routes.LDAPConnectionManager") as MockCM:
            mock_cm = _mock_connection_manager()
            MockCM.return_value = mock_cm

            response = test_client.get(
                "/api/enterprise/ldap/test-connection",
                headers=ldap_setup["headers"],
            )
            assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.json()}"
            data = response.json()
            assert data["connected"] is True
            assert data["server"] == "ldaps://mock-ldap.test:636"

    def test_test_connection_failure(self, ldap_setup, test_client):
        """Test the test-connection endpoint when LDAP is unreachable."""
        with patch("app.ee.ldap.routes.LDAPConnectionManager") as MockCM:
            mock_cm = MagicMock()
            mock_cm.test_connection.return_value = {
                "connected": False,
                "server": "ldaps://mock-ldap.test:636",
                "error": "Connection refused",
            }
            MockCM.return_value = mock_cm

            response = test_client.get(
                "/api/enterprise/ldap/test-connection",
                headers=ldap_setup["headers"],
            )
            assert response.status_code == 200
            data = response.json()
            assert data["connected"] is False
            assert "error" in data


@pytest.mark.e2e
class TestLdapSyncStatus:

    def test_get_status_initial(self, ldap_setup, test_client):
        """Test getting sync status when no sync has run."""
        response = test_client.get(
            "/api/enterprise/ldap/sync/status",
            headers=ldap_setup["headers"],
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ldap_configured"] is True
        assert data["last_sync"] is None


@pytest.mark.e2e
class TestLdapSync:

    def test_trigger_sync(self, ldap_setup, test_client):
        """Test triggering an LDAP group sync."""
        with patch("app.ee.ldap.routes.LDAPGroupSyncService") as MockService:
            from app.ee.ldap.schemas import SyncResult

            mock_service = MagicMock()
            mock_service.sync_groups = AsyncMock(return_value=SyncResult(
                groups_created=2,
                groups_updated=0,
                groups_removed=0,
                memberships_added=3,
                memberships_removed=0,
                users_not_found=0,
                errors=[],
                timestamp=datetime.now(timezone.utc),
            ))
            MockService.return_value = mock_service

            response = test_client.post(
                "/api/enterprise/ldap/sync",
                headers=ldap_setup["headers"],
            )
            assert response.status_code == 200

    def test_preview_sync(self, ldap_setup, test_client):
        """Test the sync preview (dry run)."""
        with patch("app.ee.ldap.routes.LDAPGroupSyncService") as MockService:
            from app.ee.ldap.schemas import LDAPSyncPreview, LDAPGroupPreview

            mock_service = MagicMock()
            mock_service.preview_sync = AsyncMock(return_value=LDAPSyncPreview(
                groups_to_create=2,
                groups_to_update=0,
                groups_to_remove=0,
                total_membership_changes=3,
                groups=[
                    LDAPGroupPreview(
                        dn="cn=Engineering,ou=Groups,dc=test,dc=com",
                        name="Engineering",
                        member_count=2,
                        exists_in_app=False,
                        members_to_add=2,
                        members_to_remove=0,
                    ),
                    LDAPGroupPreview(
                        dn="cn=Marketing,ou=Groups,dc=test,dc=com",
                        name="Marketing",
                        member_count=1,
                        exists_in_app=False,
                        members_to_add=1,
                        members_to_remove=0,
                    ),
                ],
            ))
            MockService.return_value = mock_service

            response = test_client.get(
                "/api/enterprise/ldap/sync/preview",
                headers=ldap_setup["headers"],
            )
            assert response.status_code == 200
            data = response.json()
            assert data["groups_to_create"] == 2
            assert len(data["groups"]) == 2


@pytest.mark.e2e
class TestLdapAvailableWithoutLicense:
    """LDAP is reachable with NO licence configured.

    ★Inverted from upstream's ``TestLdapRequiresLicense``, which asserted 402 on
    both routes. This fork unlocks enterprise permanently (``ee/license.py``
    returns a standing grant, no key, no expiry), so a paywall assertion here
    can only ever fail. Inverted rather than deleted — the routes still need
    covering; what changed is which answer is correct.

    ★Both tests clear ``_cached_license`` for the duration. That is the whole
    point: ``tests/e2e/conftest.py`` injects a session-wide licence, so without
    clearing it these would pass through THAT grant and prove nothing about an
    unlicensed instance. Clearing forces the no-licence path a real install with
    no key actually takes.

    ★They assert ``!= 402`` rather than a specific success code. Whether the
    directory is reachable in a test container is not the subject — the absence
    of a licensing refusal is. Pinning 200 here would make this a flaky LDAP
    connectivity test wearing a licensing test's name.
    """

    @staticmethod
    def _without_license(fn):
        from app.ee import license as ee_license

        saved_cached = ee_license._cached_license
        saved_initialized = ee_license._cache_initialized
        ee_license._cached_license = None
        ee_license._cache_initialized = False
        try:
            assert ee_license.get_license_info().licensed is True, (
                "the standing enterprise grant is the premise of these tests"
            )
            return fn()
        finally:
            ee_license._cached_license = saved_cached
            ee_license._cache_initialized = saved_initialized

    def test_sync_available_without_license(
        self, test_client, create_user, login_user, whoami, enable_ldap,
    ):
        user = create_user()
        token = login_user(user["email"], user["password"])
        org_id = whoami(token)['organizations'][0]['id']

        response = self._without_license(lambda: test_client.post(
            "/api/enterprise/ldap/sync",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Organization-Id": org_id,
            },
        ))
        assert response.status_code != 402, response.text

    def test_test_connection_available_without_license(
        self, test_client, create_user, login_user, whoami, enable_ldap,
    ):
        user = create_user()
        token = login_user(user["email"], user["password"])
        org_id = whoami(token)['organizations'][0]['id']

        response = self._without_license(lambda: test_client.get(
            "/api/enterprise/ldap/test-connection",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Organization-Id": org_id,
            },
        ))
        assert response.status_code != 402, response.text


@pytest.mark.e2e
class TestLdapRequiresConfig:

    def test_sync_requires_ldap_enabled(
        self, test_client, create_user, login_user, whoami, enterprise_license,
    ):
        """Test that LDAP sync fails when LDAP is not configured."""
        from app.settings.config import settings

        user = create_user()
        token = login_user(user["email"], user["password"])
        org_id = whoami(token)['organizations'][0]['id']

        # Ensure LDAP is disabled
        settings.dash_config.ldap.enabled = False

        response = test_client.post(
            "/api/enterprise/ldap/sync",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Organization-Id": org_id,
            },
        )
        assert response.status_code == 400
        assert "not configured" in response.json()["detail"].lower()
