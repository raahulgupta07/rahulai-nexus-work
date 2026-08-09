"""Generated code may run a query. It may not read the connection's password.

MEASURED DEFECT (2026-08-09, static — read from the code, not executed live).
`QueryCapturingClientWrapper.__getattr__` delegated EVERY attribute to the raw
client (`return getattr(self._original, name)`), and `CodeSecurityVisitor`
rejects only DUNDER attribute names. Credentials on the real clients are ordinary
attributes — `postgresql_client.py` holds `self.password` and
`self.pg_uri = "postgresql://user:password@host"`, and 33 clients share that
shape. So this was validator-clean and needed no escape trick at all:

    for k in db_clients:
        print(db_clients[k].password, db_clients[k].pg_uri)

...returning the plaintext credentials of every connected warehouse into the step
output that the conversation displays. For a self-hosted analytics product those
credentials are at least as damaging as the app's own encryption key.

★The provenance-gated file reader does NOT close this, and neither does
restricting `__builtins__`: this is a plain attribute read on an object the app
deliberately hands to generated code. The three fixes are independent.

★The allow-list is derived by measurement — every `ds_clients[...]` /
`db_clients[...]` access across backend/app/ai is `execute_query` or
`execute_mcp`. Adding a credential-bearing name here to make an analysis work
would reopen this exactly.
"""
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[3]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pytest  # noqa: E402

# ★Only the wrapper is imported at module level. `_CLIENT_PASSTHROUGH` is
# imported inside the one test that needs it, so that running this guard against
# a PRE-FIX tree (where the constant does not exist) produces real FAILURES on
# the credential tests rather than a collection error. A collection error proves
# the file imports the fix; it does not prove the guard detects the bug.
from app.ai.code_execution.code_execution import QueryCapturingClientWrapper  # noqa: E402


class _FakeClient:
    """Shaped like the real SQL clients, including how they hold credentials."""

    def __init__(self):
        self.host = "warehouse.internal"
        self.user = "analytics"
        self.password = "s3cret-warehouse-password"
        self.pg_uri = "postgresql://analytics:s3cret-warehouse-password@warehouse.internal:5432/db"
        self.database = "db"
        self.api_key = "sk-not-a-real-key"
        self.refresh_token = "not-a-real-token"

    def execute_query(self, query, *args, **kwargs):
        return [{"n": 1}]

    def execute_mcp(self, *args, **kwargs):
        return {"ok": True}


def _wrap():
    """A wrapper around the fake client, built the way the executor builds them."""
    return QueryCapturingClientWrapper(
        _FakeClient(), captured_queries=[], captured_timings=[]
    )


CREDENTIAL_ATTRS = ["password", "pg_uri", "user", "host", "api_key",
                    "refresh_token", "database"]


@pytest.mark.parametrize("attr", CREDENTIAL_ATTRS)
def test_a_credential_attribute_is_not_reachable(attr):
    wrapper = _wrap()
    with pytest.raises(AttributeError):
        getattr(wrapper, attr)


def test_the_password_never_appears_in_the_error_either():
    """Refusing must not leak the value in the message it refuses with."""
    wrapper = _wrap()
    try:
        wrapper.password
    except AttributeError as exc:
        assert "s3cret-warehouse-password" not in str(exc)
    else:
        pytest.fail("password was readable")


def test_execute_query_still_works():
    """The fix is worthless if it breaks the one thing generated code must do."""
    wrapper = _wrap()
    assert wrapper.execute_query("SELECT 1") == [{"n": 1}]


def test_execute_mcp_still_works():
    wrapper = _wrap()
    assert wrapper.execute_mcp() == {"ok": True}


def test_query_alias_still_works():
    """`.query(...)` is an explicit method on the wrapper, not a passthrough."""
    wrapper = _wrap()
    assert wrapper.query("SELECT 1") == [{"n": 1}]


def test_the_allowlist_is_still_an_allowlist():
    """Self-test: without this, emptying _CLIENT_PASSTHROUGH passes vacuously.

    Every case above would still pass if the passthrough set were deleted — the
    credential tests would pass for the wrong reason (nothing is reachable) and
    only the execute_query tests would catch it. Pin the shape explicitly.
    """
    from app.ai.code_execution.code_execution import _CLIENT_PASSTHROUGH
    assert "execute_query" in _CLIENT_PASSTHROUGH
    assert "password" not in _CLIENT_PASSTHROUGH
    assert "pg_uri" not in _CLIENT_PASSTHROUGH
