"""
Tests for SCIM 2.0 provisioning endpoints.
"""
import pytest
import hashlib
import secrets


@pytest.fixture
def enterprise_license():
    """Grant an enterprise licence carrying the SCIM feature, for one test.

    ★This used to generate a 2048-bit RSA keypair per module, sign a JWT with
    it, swap ``license_module.LICENSE_PUBLIC_KEY`` for the matching public half,
    and write the token into ``settings.dash_config.license.key``. None of that
    was read. ``get_license_info()`` on this fork returns a standing grant and
    never consults a configured key, so the fixture decided nothing — the tests
    below passed because everything is unlocked anyway, not because a licence
    had been validated. A fixture that cannot fail is not a fixture.

    ★Injecting ``_cached_license`` is the mechanism that DOES take effect (see
    ``get_license_info``), which is what the rest of the suite already uses —
    ``tests/e2e/conftest.py``, ``test_seat_cap_autoprovision``,
    ``test_pii_protection``. Same shape here, so this grant is real and a
    narrower feature list would genuinely turn SCIM off.
    """
    from app.ee import license as ee_license

    saved_cached = ee_license._cached_license
    saved_initialized = ee_license._cache_initialized
    ee_license._cached_license = ee_license.LicenseInfo(
        licensed=True,
        tier="enterprise",
        org_name="SCIM Test Corp",
        features=["scim"],
        license_id="lic_test_scim",
    )
    ee_license._cache_initialized = True
    try:
        yield
    finally:
        ee_license._cached_license = saved_cached
        ee_license._cache_initialized = saved_initialized


@pytest.fixture
def scim_setup(test_client, create_user, login_user, whoami, enterprise_license):
    """Set up a user, org, and SCIM token for testing."""
    user = create_user()
    token = login_user(user["email"], user["password"])
    user_info = whoami(token)
    org_id = user_info['organizations'][0]['id']

    # Create a SCIM token via the admin API
    response = test_client.post(
        "/api/enterprise/scim/tokens",
        json={"name": "Test SCIM Token"},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Organization-Id": org_id,
        }
    )
    assert response.status_code == 201, response.json()
    scim_data = response.json()

    return {
        "user_token": token,
        "org_id": org_id,
        "scim_token": scim_data["token"],
        "scim_token_id": scim_data["id"],
    }


def _scim_headers(scim_token):
    return {"Authorization": f"Bearer {scim_token}"}


# ============================================================================
# SCIM Token Management Tests
# ============================================================================


@pytest.mark.e2e
class TestScimTokenManagement:

    def test_create_scim_token(self, scim_setup, test_client):
        """Test creating a SCIM token."""
        assert scim_setup["scim_token"].startswith("bow_scim_")

    def test_list_scim_tokens(self, scim_setup, test_client):
        """Test listing SCIM tokens (prefix only, no secret)."""
        response = test_client.get(
            "/api/enterprise/scim/tokens",
            headers={
                "Authorization": f"Bearer {scim_setup['user_token']}",
                "X-Organization-Id": scim_setup["org_id"],
            }
        )
        assert response.status_code == 200
        tokens = response.json()
        assert len(tokens) >= 1
        # Should NOT contain the full token
        for t in tokens:
            assert "token" not in t or t.get("token") is None
            assert "token_prefix" in t

    def test_revoke_scim_token(self, scim_setup, test_client):
        """Test revoking a SCIM token."""
        response = test_client.delete(
            f"/api/enterprise/scim/tokens/{scim_setup['scim_token_id']}",
            headers={
                "Authorization": f"Bearer {scim_setup['user_token']}",
                "X-Organization-Id": scim_setup["org_id"],
            }
        )
        assert response.status_code == 204

        # Token should no longer work for SCIM requests
        response = test_client.get(
            "/scim/v2/Users",
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        assert response.status_code == 401

    def test_token_management_works_without_a_license(
        self, test_client, create_user, login_user, whoami,
    ):
        """SCIM token management is available with NO licence configured.

        ★Inverted from upstream's ``test_token_management_requires_license``,
        which asserted 402. This fork unlocks enterprise permanently
        (``ee/license.py`` returns a standing grant, no key, no expiry), so a
        test demanding a paywall here asserts behaviour that was deliberately
        removed, and it can only ever fail. Kept and inverted rather than
        deleted, because the route still needs covering — what changed is which
        answer is correct, not whether the answer matters.

        ★``_cached_license`` is cleared for the duration, which is the point of
        the test. ``tests/e2e/conftest.py`` injects a session-wide licence, so
        without this the route would be reached through THAT grant and the test
        would prove nothing about an unlicensed instance. Clearing it forces
        ``get_license_info()`` down its no-licence path — the one a real
        installation with no key takes.
        """
        from app.ee import license as ee_license

        user = create_user()
        token = login_user(user["email"], user["password"])
        org_id = whoami(token)['organizations'][0]['id']

        saved_cached = ee_license._cached_license
        saved_initialized = ee_license._cache_initialized
        ee_license._cached_license = None
        ee_license._cache_initialized = False
        try:
            assert ee_license.get_license_info().licensed is True, (
                "the standing enterprise grant is the premise of this test"
            )
            response = test_client.post(
                "/api/enterprise/scim/tokens",
                json={"name": "Works Without A Licence"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Organization-Id": org_id,
                }
            )
        finally:
            ee_license._cached_license = saved_cached
            ee_license._cache_initialized = saved_initialized

        assert response.status_code != 402, response.text
        # ★201, not 200 — the route CREATES a token. Asserting 200 here fails
        # with the created token sitting in the message, which reads like a
        # licensing refusal when it is the opposite.
        assert response.status_code == 201, response.text
        assert response.json()["name"] == "Works Without A Licence"


# ============================================================================
# SCIM Discovery Tests
# ============================================================================


@pytest.mark.e2e
class TestScimDiscovery:

    def test_service_provider_config(self, scim_setup, test_client):
        response = test_client.get(
            "/scim/v2/ServiceProviderConfig",
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["patch"]["supported"] is True
        assert data["bulk"]["supported"] is False
        assert data["filter"]["supported"] is True

    def test_schemas(self, scim_setup, test_client):
        response = test_client.get(
            "/scim/v2/Schemas",
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert any(s["id"] == "urn:ietf:params:scim:schemas:core:2.0:User" for s in data)

    def test_resource_types(self, scim_setup, test_client):
        response = test_client.get(
            "/scim/v2/ResourceTypes",
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert any(r["name"] == "User" for r in data)


# ============================================================================
# SCIM User CRUD Tests
# ============================================================================


@pytest.mark.e2e
class TestScimUserCrud:

    def test_list_users_empty(self, scim_setup, test_client):
        """Test listing users returns the org creator."""
        response = test_client.get(
            "/scim/v2/Users",
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        assert response.status_code == 200
        data = response.json()
        assert "totalResults" in data
        assert "Resources" in data
        assert data["totalResults"] >= 1  # At least the admin user

    def test_create_user(self, scim_setup, test_client):
        """Test provisioning a new user via SCIM."""
        response = test_client.post(
            "/scim/v2/Users",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "scim.user@example.com",
                "name": {"givenName": "SCIM", "familyName": "User"},
                "emails": [{"value": "scim.user@example.com", "type": "work", "primary": True}],
                "active": True,
                "externalId": "ext-123",
            },
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        assert response.status_code == 201
        data = response.json()
        assert data["userName"] == "scim.user@example.com"
        assert data["active"] is True
        assert data["externalId"] == "ext-123"
        assert data["id"] is not None

    def test_get_user(self, scim_setup, test_client):
        """Test getting a user by ID."""
        # Create user first
        create_resp = test_client.post(
            "/scim/v2/Users",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "get.user@example.com",
                "name": {"givenName": "Get", "familyName": "User"},
                "active": True,
            },
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        user_id = create_resp.json()["id"]

        # Get the user
        response = test_client.get(
            f"/scim/v2/Users/{user_id}",
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        assert response.status_code == 200
        assert response.json()["id"] == user_id
        assert response.json()["userName"] == "get.user@example.com"

    def test_update_user_put(self, scim_setup, test_client):
        """Test full user replacement via PUT."""
        # Create user
        create_resp = test_client.post(
            "/scim/v2/Users",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "put.user@example.com",
                "displayName": "Original Name",
                "active": True,
            },
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        user_id = create_resp.json()["id"]

        # Update via PUT
        response = test_client.put(
            f"/scim/v2/Users/{user_id}",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "put.user@example.com",
                "displayName": "Updated Name",
                "active": True,
            },
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        assert response.status_code == 200
        assert response.json()["displayName"] == "Updated Name"

    def test_patch_user_deactivate(self, scim_setup, test_client):
        """Test Okta-style PATCH to deactivate user."""
        # Create user
        create_resp = test_client.post(
            "/scim/v2/Users",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "deactivate.user@example.com",
                "displayName": "To Deactivate",
                "active": True,
            },
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        user_id = create_resp.json()["id"]

        # Okta-style PATCH deactivation
        response = test_client.patch(
            f"/scim/v2/Users/{user_id}",
            json={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [{"op": "replace", "value": {"active": False}}],
            },
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        assert response.status_code == 200
        assert response.json()["active"] is False

    def test_delete_user(self, scim_setup, test_client):
        """Test SCIM DELETE deactivates user."""
        # Create user
        create_resp = test_client.post(
            "/scim/v2/Users",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "delete.user@example.com",
                "displayName": "To Delete",
                "active": True,
            },
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        user_id = create_resp.json()["id"]

        # DELETE
        response = test_client.delete(
            f"/scim/v2/Users/{user_id}",
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        assert response.status_code == 204

        # User should still exist but be inactive
        get_resp = test_client.get(
            f"/scim/v2/Users/{user_id}",
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["active"] is False

    def test_create_duplicate_user_conflict(self, scim_setup, test_client):
        """Test creating a user that already exists in the org returns 409."""
        user_data = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "duplicate@example.com",
            "displayName": "Duplicate",
            "active": True,
        }

        # First create succeeds
        resp1 = test_client.post(
            "/scim/v2/Users",
            json=user_data,
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        assert resp1.status_code == 201

        # Second create returns 409
        resp2 = test_client.post(
            "/scim/v2/Users",
            json=user_data,
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        assert resp2.status_code == 409

    def test_filter_by_username(self, scim_setup, test_client):
        """Test filtering users by userName."""
        # Create a user
        test_client.post(
            "/scim/v2/Users",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "filter.test@example.com",
                "displayName": "Filter Test",
                "active": True,
            },
            headers=_scim_headers(scim_setup["scim_token"]),
        )

        # Filter by userName
        response = test_client.get(
            '/scim/v2/Users?filter=userName eq "filter.test@example.com"',
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["totalResults"] == 1
        assert data["Resources"][0]["userName"] == "filter.test@example.com"

    def test_get_nonexistent_user_404(self, scim_setup, test_client):
        """Test getting a non-existent user returns 404."""
        response = test_client.get(
            "/scim/v2/Users/nonexistent-uuid",
            headers=_scim_headers(scim_setup["scim_token"]),
        )
        assert response.status_code == 404


# ============================================================================
# SCIM Auth Tests
# ============================================================================


@pytest.mark.e2e
class TestScimAuth:

    def test_missing_auth_header(self, test_client):
        """Test SCIM request without auth header returns 401."""
        response = test_client.get("/scim/v2/Users")
        assert response.status_code == 401

    def test_invalid_token(self, test_client):
        """Test SCIM request with invalid token returns 401."""
        response = test_client.get(
            "/scim/v2/Users",
            headers={"Authorization": "Bearer bow_scim_invalid_token"},
        )
        assert response.status_code == 401

    def test_non_scim_token_rejected(self, test_client):
        """Test that regular API keys are rejected for SCIM."""
        response = test_client.get(
            "/scim/v2/Users",
            headers={"Authorization": "Bearer bow_regular_api_key"},
        )
        assert response.status_code == 401
