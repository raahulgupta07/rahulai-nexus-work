"""ServiceNow `verify_ssl` and `infer_schema_from_data` — the cover upstream did not ship.

Both flags arrived in upstream v0.0.491 with **no test anywhere in the tree**
(`grep -rl 'verify_ssl|infer_schema_from_data' tests/` names three unrelated
connectors and never `test_servicenow_client.py`). They are exactly the kind of
setting that fails silently:

  * `verify_ssl` is read once, at session construction. If it were dropped on
    the way from config to `requests.Session`, every install would keep working
    — TLS verification simply stays on, and the operator who set the flag to
    reach an internal CA gets a certificate error they were told they had
    turned off.
  * `infer_schema_from_data` reroutes both `test_connection` and schema
    discovery away from `sys_dictionary`/`sys_db_object`. If it were ignored,
    the connector would probe metadata tables the account cannot read and
    report "not connected" for a connection that is perfectly usable — the
    precise failure the flag exists to remove.

Neither is caught by any assertion in the connector's own 23 tests.
"""
import pytest

from app.data_sources.clients.servicenow_client import ServiceNowClient
from app.schemas.data_sources.configs import ServiceNowConfig


# ── the config surface ──────────────────────────────────────────────────────

def test_both_flags_exist_on_the_config():
    fields = ServiceNowConfig.model_fields
    assert "verify_ssl" in fields
    assert "infer_schema_from_data" in fields


def test_defaults_are_the_safe_ones():
    """Verification on, inference off. A default that flipped either way would
    weaken TLS or change discovery for every existing connection on upgrade."""
    assert ServiceNowConfig.model_fields["verify_ssl"].default is True
    assert ServiceNowConfig.model_fields["infer_schema_from_data"].default is False


# ── verify_ssl actually reaches the HTTP session ────────────────────────────

@pytest.mark.parametrize("flag", [True, False])
def test_verify_ssl_reaches_the_session(flag):
    """The whole point of the flag is the value on the session object. Assert
    that, not the attribute on the client — the attribute could be set and
    never used."""
    client = ServiceNowClient(
        instance_url="https://example.service-now.com",
        username="u",
        password="p",
        verify_ssl=flag,
    )
    with client.connect() as session:
        assert session.verify is flag


def test_verify_ssl_defaults_to_on_when_not_passed():
    client = ServiceNowClient(
        instance_url="https://example.service-now.com", username="u", password="p"
    )
    with client.connect() as session:
        assert session.verify is True


# ── infer_schema_from_data changes behaviour, not just state ────────────────

def test_infer_flag_is_coerced_to_bool():
    """Config values arrive from JSON and can be strings. `'false'` is truthy in
    Python, so a missing coercion would silently enable inference."""
    client = ServiceNowClient(
        instance_url="https://x", username="u", password="p",
        infer_schema_from_data=1, verify_ssl=0,
    )
    assert client.infer_schema_from_data is True
    assert client.verify_ssl is False


def test_infer_mode_probes_configured_tables_not_metadata(monkeypatch):
    """In inference mode `test_connection` must never touch sys_user or
    sys_dictionary — the account is assumed to lack them."""
    client = ServiceNowClient(
        instance_url="https://x", username="u", password="p",
        tables="em_event,em_alert", infer_schema_from_data=True,
    )

    touched = []

    def fake_get(session, path, params):
        touched.append(path)
        return {"result": []}

    monkeypatch.setattr(client, "_get", fake_get)
    monkeypatch.setattr(client, "_can_read_table", lambda s, t: True)

    result = client.test_connection()

    assert result["success"] is True
    assert not any("sys_dictionary" in p or "sys_db_object" in p or "sys_user" in p
                   for p in touched), f"metadata tables were probed: {touched}"


def test_metadata_mode_still_probes_sys_user(monkeypatch):
    """The default path must be unchanged — this is the regression guard for
    every existing ServiceNow connection."""
    client = ServiceNowClient(
        instance_url="https://x", username="u", password="p",
        tables="em_event", infer_schema_from_data=False,
    )

    touched = []
    monkeypatch.setattr(
        client, "_get",
        lambda session, path, params: (touched.append(path), {"result": []})[1],
    )

    client.test_connection()

    assert any("sys_user" in p for p in touched), \
        f"default mode stopped probing sys_user: {touched}"
