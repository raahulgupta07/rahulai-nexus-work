"""`GET /connections` and `GET /connections/{id}` must report the same count.

The Connect dialog opens with the count carried on the list payload, then
`ConnectionDetailModal.fetchDetail()` overwrites it from the detail endpoint:

    const tableCount = computed(
      () => myTableCountOverride.value ?? detail.value?.table_count ?? (props.connection?.table_count || 0))

The list endpoint scoped that number to the caller for a `user_required`
connection — those connectors index per user, into `user_data_source_tables`,
so the org catalog `connection_tables` is structurally 0 — and the detail
endpoint did not. It returned `count_catalog_rows()` straight through.

`0` is not `None`, so the `??` chain stopped at the detail value and threw the
good one away: **the dialog was strictly worse than not fetching at all.**
Measured on the local instance 2026-08-20 — six accessible Power BI tables in
the overlay, `datasource_tables` = 6, the agent page reading "6 tables from 2
tenants", the Tables tree reading 6, and the dialog beside them reading
`Tables 0`.

★What is pinned here is that the two endpoints share ONE resolver, not that
each returns some number. Two endpoints feeding the same screen drifted for
months because every test asserted a count in isolation and none compared them.

★The overlay query itself is not re-tested here: it is the list endpoint's
existing query, moved unchanged. What was missing was a caller, so that is what
these assert.
"""
import inspect

import pytest

from app.routes.connection import (
    _user_scoped_table_count,
    get_connection,
    list_connections,
)


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeDB:
    """Stands in for the session. Records whether the overlay was queried."""

    def __init__(self, overlay_count=6):
        self.overlay_count = overlay_count
        self.queries = 0

    async def execute(self, *_args, **_kwargs):
        self.queries += 1
        return _Result(self.overlay_count)


class _DS:
    def __init__(self, id_):
        self.id = id_


class _Conn:
    def __init__(self, auth_policy="user_required", data_sources=None):
        self.auth_policy = auth_policy
        self.data_sources = data_sources if data_sources is not None else [_DS("ds-1")]


class _User:
    id = "user-1"


CATALOG = 0  # what `connection_tables` holds for a per-user connector


@pytest.mark.asyncio
class TestTheCallerSeesTheirOwnTables:
    async def test_a_signed_in_user_gets_their_overlay_count_not_the_empty_catalog(self):
        db = _FakeDB(overlay_count=6)
        n = await _user_scoped_table_count(
            db, _Conn(), _User(), CATALOG, {"effective_auth": "user"}
        )
        assert n == 6
        assert db.queries == 1, "the overlay must actually be queried"

    async def test_no_proven_access_reports_zero(self):
        db = _FakeDB(overlay_count=6)
        n = await _user_scoped_table_count(
            db, _Conn(), _User(), CATALOG, {"effective_auth": "none"}
        )
        assert n == 0
        assert db.queries == 0, "no access — do not query the overlay at all"

    async def test_a_shared_credential_connection_keeps_the_catalog_count(self):
        """★Positive control. A `system_only` connection is the case the
        catalog count is RIGHT for — the fix must not reroute it."""
        db = _FakeDB(overlay_count=6)
        n = await _user_scoped_table_count(
            db, _Conn(auth_policy="system_only"), _User(), 42, None
        )
        assert n == 42
        assert db.queries == 0

    async def test_system_auth_on_a_user_required_connection_keeps_the_catalog(self):
        """`effective_auth` is neither 'user' nor 'none' — the connection is
        answering with shared credentials, so the catalog is the true count."""
        db = _FakeDB(overlay_count=6)
        n = await _user_scoped_table_count(
            db, _Conn(), _User(), 42, {"effective_auth": "system"}
        )
        assert n == 42
        assert db.queries == 0

    async def test_a_connection_linked_to_no_agent_reports_zero(self):
        db = _FakeDB(overlay_count=6)
        n = await _user_scoped_table_count(
            db, _Conn(data_sources=[]), _User(), CATALOG, {"effective_auth": "user"}
        )
        assert n == 0

    async def test_an_unknown_auth_status_does_not_invent_a_number(self):
        """Status could not be built (the service raised, so it is None). The
        honest answer is the catalog count, not a guess."""
        db = _FakeDB(overlay_count=6)
        n = await _user_scoped_table_count(db, _Conn(), _User(), 3, None)
        assert n == 3


class TestBothEndpointsUseIt:
    """★The assertion that keeps the two screens agreeing.

    A behavioural test of the resolver cannot catch the actual defect, which
    was an endpoint that never called it. These read the route source.
    """

    def test_the_list_endpoint_scopes_its_count(self):
        assert "_user_scoped_table_count" in inspect.getsource(list_connections)

    def test_the_detail_endpoint_scopes_its_count(self):
        """★This is the one that was failing in production. The detail
        endpoint returned `count_catalog_rows()` unscoped."""
        assert "_user_scoped_table_count" in inspect.getsource(get_connection)

    def test_neither_endpoint_keeps_a_private_copy_of_the_branch(self):
        """A re-inlined copy is how they drifted the first time. The overlay
        model is imported by the shared resolver and should appear in neither
        route body."""
        for fn in (list_connections, get_connection):
            src = inspect.getsource(fn)
            assert "UserDataSourceTable" not in src, (
                f"{fn.__name__} queries the overlay directly again — call "
                "_user_scoped_table_count instead"
            )
