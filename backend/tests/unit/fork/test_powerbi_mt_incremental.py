"""Incremental discovery for the Power BI multi-tenant fan-out.

``scan_all_tenants`` hands the canonical catalog to EVERY tenant's client as
``prior_tables`` so an already-indexed dataset is not re-introspected — a full
re-crawl per tenant is what made the post-sign-in scan take minutes. Loading
the priors is fail-OPEN: no canonical rows, or any error reading them, must
degrade to the bare ``aget_schemas()`` scan rather than break the sign-in.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import powerbi_multitenant_scan as mt
from app.services.data_source_service import DataSourceService

TENANTS = [
    {"id": "tenant-a", "name": "City Holdings"},
    {"id": "tenant-b", "name": "City Mart Holding"},
]


class _StubClient:
    """Stands in for ``PowerBIClient``, recording how each scan was called.

    ``aget_schemas`` records every call (kwargs AND positional, so the test
    reads the priors the same way whichever form the caller uses) and returns
    one tenant-specific table. The sync ``get_schemas`` exists only as the
    signature-introspection target for ``_accepts_kwarg(..., "prior_tables")``
    — the scan awaits the async wrapper, never this.
    """

    instances: list = []

    def __init__(self, access_token=None, tenant_id=None, workspaces=None, **kwargs):
        self.access_token = access_token
        self.tenant_id = tenant_id
        self.workspaces = workspaces
        self.calls: list = []
        _StubClient.instances.append(self)

    def get_schemas(self, prior_tables=None, prior_catalog=None, progress_callback=None):
        return []

    async def aget_schemas(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return [{
            "name": f"Model/{self.tenant_id}",
            "columns": [{"name": "id", "dtype": "int"}],
            "pks": [],
            "fks": [],
            "metadata_json": {"powerbi": {"datasetId": f"ds-{self.tenant_id}", "tableName": "Sales"}},
        }]

    @staticmethod
    def priors_of(call):
        args, kwargs = call
        if "prior_tables" in kwargs:
            return kwargs["prior_tables"]
        return args[0] if args else None


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeDb:
    """Async DB double serving exactly one query: the canonical-rows select."""

    def __init__(self, rows=None, raises=False):
        self.rows = rows or []
        self.raises = raises
        self.execute_calls = 0

    async def execute(self, *args, **kwargs):
        self.execute_calls += 1
        if self.raises:
            raise RuntimeError("connection reset while reading canonical tables")
        return _FakeResult(self.rows)


def _canonical_row(name, metadata_json={"powerbi": {"datasetId": "ds-1", "tableName": "Sales"}}):
    return SimpleNamespace(
        name=name,
        columns=[{"name": "id", "dtype": "int"}],
        pks=[],
        fks=[],
        metadata_json=metadata_json,
    )


def _patch(monkeypatch):
    """Patch out the network, the client and the overlay write. Returns the
    overlay mock so a test can read the merged catalog."""
    _StubClient.instances = []

    monkeypatch.setattr(mt, "discover_tenants_from_refresh", lambda *a, **k: [dict(t) for t in TENANTS])
    monkeypatch.setattr(
        mt,
        "redeem_for_tenant",
        lambda refresh_token, tenant_id, client_id, client_secret=None: {
            "access_token": f"tok-{tenant_id}",
            "refresh_token": f"rt-{tenant_id}",
        },
    )
    monkeypatch.setattr("app.data_sources.clients.powerbi_client.PowerBIClient", _StubClient)

    overlay = AsyncMock()
    monkeypatch.setattr(DataSourceService, "_upsert_user_overlay", overlay)
    return overlay


async def _scan(db):
    return await mt.scan_all_tenants(
        db=db,
        data_source=SimpleNamespace(id="ds-uuid", connections=[]),
        user=SimpleNamespace(id="user-uuid"),
        home_refresh_token="home-rt",
        client_id="client-id",
        client_secret="client-secret",
        persist_tokens=False,
    )


@pytest.mark.asyncio
async def test_priors_passed_to_every_tenant_client(monkeypatch):
    """With canonical rows on the data source, EVERY tenant's scan receives
    them as prior_tables keyed by table name (not just the first tenant)."""
    overlay = _patch(monkeypatch)
    db = _FakeDb(rows=[_canonical_row("Model/Customers"), _canonical_row("Model/Orders")])

    result = await _scan(db)

    assert len(_StubClient.instances) == 2, "both discovered tenants should be scanned"
    for client in _StubClient.instances:
        assert len(client.calls) == 1
        priors = _StubClient.priors_of(client.calls[0])
        assert set(priors.keys()) == {"Model/Customers", "Model/Orders"}
        assert set(priors["Model/Customers"].keys()) == {"columns", "pks", "fks", "metadata_json"}

    assert result["tables_merged"] == 2
    assert [t["id"] for t in result["tenants"]] == ["tenant-a", "tenant-b"]
    overlay.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_canonical_rows_uses_bare_scan(monkeypatch):
    """A first-ever scan has nothing to skip — no prior_tables is sent."""
    _patch(monkeypatch)
    db = _FakeDb(rows=[])

    result = await _scan(db)

    assert len(_StubClient.instances) == 2
    for client in _StubClient.instances:
        assert _StubClient.priors_of(client.calls[0]) is None
    assert result["tables_merged"] == 2


@pytest.mark.asyncio
async def test_priors_load_failure_fails_open(monkeypatch):
    """A DB error reading the canonical rows must not break the scan: it falls
    back to the bare full crawl and the merge still happens."""
    overlay = _patch(monkeypatch)
    db = _FakeDb(raises=True)

    result = await _scan(db)

    assert db.execute_calls == 1
    assert len(_StubClient.instances) == 2
    for client in _StubClient.instances:
        assert _StubClient.priors_of(client.calls[0]) is None
    assert result["tables_merged"] == 2
    assert result["errors"] == []
    overlay.assert_awaited_once()


@pytest.mark.asyncio
async def test_rows_without_metadata_excluded_from_priors(monkeypatch):
    """metadata_json carries the (datasetId, tableName) identity the client
    skips on — a row without it can't be skipped, so it stays out of priors."""
    _patch(monkeypatch)
    db = _FakeDb(rows=[
        _canonical_row("Model/Indexed"),
        _canonical_row("Model/NoMeta", metadata_json=None),
        _canonical_row("Model/EmptyMeta", metadata_json={}),
    ])

    await _scan(db)

    for client in _StubClient.instances:
        priors = _StubClient.priors_of(client.calls[0])
        assert set(priors.keys()) == {"Model/Indexed"}
