"""Sign-in expiry, derived once on the server.

`token_expires_at` has been on the status payload for a while and nothing acted
on it, so a member found out their Microsoft sign-in had lapsed by asking a
question and getting nothing back. These fields turn that date into the two
facts a screen can use.

★Derived on the SERVER, not in the browser, so a strip, a picker row and the
admin roster can never disagree about whether somebody's sign-in still works.
"""
from datetime import datetime, timedelta

import pytest

from app.services.user_data_source_credentials_service import (
    UserDataSourceCredentialsService as Svc,
)


class Row:
    """Minimal stand-in for a credential row. Concrete, not a MagicMock — a mock
    fabricates every attribute, so `getattr(row, 'expires_at', None)` would
    return a Mock and the 'no stored expiry' branch could never be reached."""
    def __init__(self, updated_at=None, created_at=None, last_used_at=None,
                 expires_at=None, metadata_json=None):
        self.updated_at = updated_at
        self.created_at = created_at
        self.last_used_at = last_used_at
        self.expires_at = expires_at
        self.metadata_json = metadata_json


class Conn:
    def __init__(self, type_):
        self.type = type_


def life(conn_type, **row_kw):
    return Svc._token_lifecycle(Conn(conn_type), Row(**row_kw))


# ---------------------------------------------------------------------------
# The sign-in connectors: a 90-day sliding window
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("conn_type", ["fabric_user", "powerbi_user"])
def test_a_fresh_sign_in_is_not_expiring(conn_type):
    out = life(conn_type, updated_at=datetime.utcnow())
    assert out["expired"] is False
    assert out["expiring_soon"] is False
    assert out["expires_in_days"] == 89   # 90 days minus the part-day elapsed


@pytest.mark.parametrize("conn_type", ["fabric_user", "powerbi_user"])
def test_inside_a_week_is_expiring_soon_but_still_works(conn_type):
    """A warning, not a failure. The agent still answers."""
    out = life(conn_type, updated_at=datetime.utcnow() - timedelta(days=85))
    assert out["expired"] is False
    assert out["expiring_soon"] is True
    assert out["expires_in_days"] <= 7


@pytest.mark.parametrize("conn_type", ["fabric_user", "powerbi_user"])
def test_past_the_window_is_expired(conn_type):
    out = life(conn_type, updated_at=datetime.utcnow() - timedelta(days=91))
    assert out["expired"] is True
    assert out["expiring_soon"] is False      # already gone, not "soon"
    assert out["expires_in_days"] == 0


def test_the_boundary_is_seven_days_not_eight():
    """Pinned because a warning window that quietly widens becomes noise."""
    eight = life("fabric_user", updated_at=datetime.utcnow() - timedelta(days=81, hours=12))
    seven = life("fabric_user", updated_at=datetime.utcnow() - timedelta(days=83))
    assert eight["expiring_soon"] is False
    assert seven["expiring_soon"] is True


# ---------------------------------------------------------------------------
# Everything else: never invent a deadline
# ---------------------------------------------------------------------------
def test_a_connector_with_no_stored_expiry_claims_nothing():
    """★An absent expiry must not read as "expires today". Silence is correct."""
    out = life("postgresql", updated_at=datetime.utcnow())
    assert "token_expires_at" not in out
    assert out.get("expired", False) is False
    assert out.get("expiring_soon", False) is False
    assert out.get("expires_in_days") is None


def test_a_connector_with_a_real_stored_expiry_uses_it():
    out = life("onedrive", updated_at=datetime.utcnow(),
               expires_at=datetime.utcnow() - timedelta(hours=1))
    assert out["expired"] is True


def test_no_credential_row_yields_nothing_at_all():
    assert Svc._token_lifecycle(Conn("fabric_user"), None) == {}


def test_a_row_with_no_dates_does_not_fabricate_an_expiry():
    out = life("fabric_user")
    assert out["token_expires_at"] is None
    assert out.get("expired", False) is False
    assert out.get("expires_in_days") is None


# ---------------------------------------------------------------------------
# The status schema must actually carry the fields
# ---------------------------------------------------------------------------
def test_the_status_schema_exposes_all_three():
    from app.schemas.data_source_schema import DataSourceUserStatus
    fields = DataSourceUserStatus.model_fields
    for name in ("expired", "expiring_soon", "expires_in_days"):
        assert name in fields, name
    # Default to "nothing is wrong" — a status that cannot be computed must not
    # put a red badge over a working connection.
    assert fields["expired"].default is False
    assert fields["expiring_soon"].default is False
    assert fields["expires_in_days"].default is None
