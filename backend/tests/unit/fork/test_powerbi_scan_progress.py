"""Power BI multi-tenant scan — live progress hooks.

The scan is one long await. Before this, a member signing in saw nothing at all
until every tenant had been crawled, which on a multi-tenant account is the
entire wait. These tests pin the two properties that matter:

  1. the hooks fire AS EACH TENANT LANDS, in order, and report failures too;
  2. with no hooks passed, the function behaves exactly as it did before — the
     `powerbi_mt` OAuth path calls it that way and must not change.

No schema needed (every dependency is patched), so this belongs in the fast fork
suite.
"""
import asyncio
import inspect

import pytest

from app.services import powerbi_multitenant_scan as mt


def run(coro):
    # A fresh loop per call — nothing here is loop-bound, and `get_event_loop()`
    # raises once another test in the suite has closed the main thread's loop.
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fakes. Deliberately concrete rather than MagicMock: a MagicMock fabricates
# every attribute, so `_accepts_kwarg` and `hasattr` checks would pass vacuously
# and the test would prove nothing.
# ---------------------------------------------------------------------------
class FakeDB:
    async def execute(self, *_a, **_k):
        # The scan's prior_tables lookup is wrapped in try/except and fails open
        # to a full crawl. Raising here exercises that path.
        raise RuntimeError("no catalog in this test")


class FakeDS:
    id = "ds-1"


class FakeUser:
    id = "user-1"


class FakeClient:
    """Stands in for PowerBIClient. `tables_by_tenant` decides what each returns."""
    tables_by_tenant: dict = {}
    raise_for_tenant: set = set()

    def __init__(self, access_token=None, tenant_id=None, workspaces=None):
        self.tenant_id = tenant_id

    def get_schemas(self):  # `_accepts_kwarg` inspects this signature
        return []

    async def aget_schemas(self, **_kw):
        if self.tenant_id in FakeClient.raise_for_tenant:
            raise RuntimeError("dataset listing refused")
        return FakeClient.tables_by_tenant.get(self.tenant_id, [])


class FakeService:
    upserts: list = []

    async def _upsert_user_overlay(self, db=None, data_source=None, user=None, normalized=None):
        FakeService.upserts.append(dict(normalized or {}))


@pytest.fixture
def patched(monkeypatch):
    """Patch every external dependency of the scan and reset the fakes."""
    FakeClient.tables_by_tenant = {}
    FakeClient.raise_for_tenant = set()
    FakeService.upserts = []

    tenants = [
        {"id": "t-city-mart", "name": "City Mart Holding"},
        {"id": "t-city-holdings", "name": "City Holdings"},
    ]

    monkeypatch.setattr(
        mt, "discover_tenants_from_refresh",
        lambda *a, **k: list(tenants), raising=False,
    )
    monkeypatch.setattr(
        mt, "redeem_for_tenant",
        lambda _rt, tid, *a, **k: {"access_token": f"tok-{tid}", "refresh_token": f"rt-{tid}"},
        raising=False,
    )
    monkeypatch.setattr(
        mt, "_normalize_tables",
        lambda fresh, tid, tname: {f"{tid}.{t}": {"name": t} for t in (fresh or [])},
        raising=False,
    )

    import app.data_sources.clients.powerbi_client as pbi_mod
    monkeypatch.setattr(pbi_mod, "PowerBIClient", FakeClient, raising=False)

    import app.services.data_source_service as dss_mod
    monkeypatch.setattr(dss_mod, "DataSourceService", FakeService, raising=False)

    return tenants


async def _scan(**kw):
    return await mt.scan_all_tenants(
        db=FakeDB(),
        data_source=FakeDS(),
        user=FakeUser(),
        home_refresh_token="home-rt",
        client_id="client-abc",
        persist_tokens=False,
        **kw,
    )


# ---------------------------------------------------------------------------
# 1. Hooks are optional — the OAuth path must be untouched
# ---------------------------------------------------------------------------
def test_hooks_default_to_none():
    params = inspect.signature(mt.scan_all_tenants).parameters
    assert params["on_discovered"].default is None
    assert params["on_tenant"].default is None


def test_scan_without_hooks_still_works(patched):
    """`powerbi_mt` calls this with no hooks. That path must behave as before."""
    FakeClient.tables_by_tenant = {"t-city-mart": ["a", "b"], "t-city-holdings": ["c"]}
    result = run(_scan())
    assert [t["name"] for t in result["tenants"]] == ["City Mart Holding", "City Holdings"]
    assert result["tables_merged"] == 3


# ---------------------------------------------------------------------------
# 2. Progress arrives as it happens
# ---------------------------------------------------------------------------
def test_discovered_hook_fires_before_any_tenant_is_crawled(patched):
    order = []

    async def on_discovered(tenants):
        order.append(("discovered", [t["name"] for t in tenants]))

    async def on_tenant(name, tables, error):
        order.append(("tenant", name, tables, error))

    FakeClient.tables_by_tenant = {"t-city-mart": ["a"], "t-city-holdings": ["b"]}
    run(_scan(on_discovered=on_discovered, on_tenant=on_tenant))

    assert order[0] == ("discovered", ["City Mart Holding", "City Holdings"])
    assert order[1] == ("tenant", "City Mart Holding", 1, None)
    assert order[2] == ("tenant", "City Holdings", 1, None)


def test_each_tenant_reports_its_own_table_count(patched):
    seen = {}

    async def on_tenant(name, tables, error):
        seen[name] = (tables, error)

    FakeClient.tables_by_tenant = {
        "t-city-mart": ["a", "b", "c", "d"],
        "t-city-holdings": ["e", "f"],
    }
    run(_scan(on_tenant=on_tenant))
    assert seen == {"City Mart Holding": (4, None), "City Holdings": (2, None)}


def test_a_tenant_that_fails_is_reported_not_skipped(patched):
    """A silent skip is how a sync "finishes" without the data somebody wanted."""
    seen = []

    async def on_tenant(name, tables, error):
        seen.append((name, tables, error))

    FakeClient.tables_by_tenant = {"t-city-mart": ["a"]}
    FakeClient.raise_for_tenant = {"t-city-holdings"}
    result = run(_scan(on_tenant=on_tenant))

    assert seen[0] == ("City Mart Holding", 1, None)
    failed = seen[1]
    assert failed[0] == "City Holdings" and failed[1] == 0
    assert failed[2] is not None            # carries a reason
    # The working tenant is still merged — one failure never voids the rest.
    assert result["tables_merged"] == 1


def test_a_tenant_whose_token_cannot_be_minted_is_reported(patched, monkeypatch):
    seen = []

    async def on_tenant(name, tables, error):
        seen.append((name, error))

    monkeypatch.setattr(
        mt, "redeem_for_tenant",
        lambda _rt, tid, *a, **k: ({} if tid == "t-city-holdings"
                                   else {"access_token": f"tok-{tid}"}),
        raising=False,
    )
    FakeClient.tables_by_tenant = {"t-city-mart": ["a"]}
    run(_scan(on_tenant=on_tenant))

    assert ("City Holdings", "could not get a Microsoft token for this tenant") in seen


# ---------------------------------------------------------------------------
# 3. Narration is never load-bearing
# ---------------------------------------------------------------------------
def test_a_hook_that_raises_does_not_break_the_scan(patched):
    """The progress display failing must not cost a member their tables."""
    async def boom(*_a, **_k):
        raise RuntimeError("progress store unavailable")

    FakeClient.tables_by_tenant = {"t-city-mart": ["a", "b"], "t-city-holdings": ["c"]}
    result = run(_scan(on_discovered=boom, on_tenant=boom))

    assert result["tables_merged"] == 3
    assert len(result["tenants"]) == 2


def test_no_tenants_discovered_reports_nothing_and_does_not_raise(patched, monkeypatch):
    monkeypatch.setattr(mt, "discover_tenants_from_refresh", lambda *a, **k: [], raising=False)
    fired = []

    async def on_discovered(tenants):
        fired.append(tenants)

    result = run(_scan(on_discovered=on_discovered))
    assert result["tenants"] == []
    assert "no_tenants_discovered" in result["errors"]
    # Nothing to announce, so nothing was announced.
    assert fired == []
