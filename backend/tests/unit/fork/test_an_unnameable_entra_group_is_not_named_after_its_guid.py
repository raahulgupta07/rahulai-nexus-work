"""An id Graph could not name must not be presented as a group named by GUID.

The OIDC groups claim carries opaque object ids, and group sync asks Graph for a
display name. Not every id resolves: directory roles and administrative units
arrive through the same claim, and a tenant can withhold individual groups from
the app registration — ``getByIds`` simply omits those.

``resolve_group_names_by_ids`` used to backfill every unresolved id with the id
itself, so the sync path could not tell "Graph could not read this" from "this
group is genuinely named after a GUID". The placeholder label was gated on the
lookup being FALSY, and a backfilled GUID is truthy, so the placeholder never
applied: raw ids like ``85f43b45-99ae-43a0-a780-a05c119e8b9c`` kept appearing in
the admin's group list beside properly resolved names.

Ported from upstream 2e811b30. ``graph_client.py`` was byte-identical to
upstream and was taken wholesale; ``group_sync_service.py`` has diverged on this
fork (name-collision handling, savepoint on concurrent creates) and the hunk was
ported by hand.

★Measured by copying the working tree and restoring ONLY graph_client.py +
group_sync_service.py from `git show HEAD:` (the pre-fix upstream files), then
running this same file against both:
   HEAD (pre-fix): 6 failed, 2 passed — the backfill named both unreadable ids
                   after themselves, and `unresolved_group_label` / `_resolved_name`
                   did not exist.
   Fixed:          8 passed.
Database-touching behaviour (relabelling stored rows) is guarded separately in
``tests/unit/test_oidc_group_relabel.py`` — it needs a schema, which this
directory's no-op ``run_migrations`` cannot provide.
"""
import json

import httpx
import pytest
from unittest.mock import patch

RESOLVED = "11111111-2222-3333-4444-555555555555"
UNREADABLE = "85f43b45-99ae-43a0-a780-a05c119e8b9c"


def _mock_httpx(handler):
    """Patch httpx.AsyncClient so every request goes through `handler`."""
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    class _Patched(original):
        def __init__(self, *a, **kw):
            kw["transport"] = transport
            super().__init__(*a, **kw)

    return patch.object(httpx, "AsyncClient", _Patched)


# ── /me/memberOf ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_member_of_marks_a_nameless_group_unresolved():
    """A group Graph returns without a readable displayName maps to None.

    Membership is still known for all three — only the NAME is unresolved, so
    the user keeps the access the claim grants them.
    """
    def handler(request):
        return httpx.Response(200, json={"value": [
            {"@odata.type": "#microsoft.graph.group", "id": "g1", "displayName": "AllFabric"},
            {"@odata.type": "#microsoft.graph.group", "id": "g2"},
            {"@odata.type": "#microsoft.graph.group", "id": "g3", "displayName": "  "},
        ]})

    with _mock_httpx(handler):
        from app.ee.oidc.graph_client import resolve_group_names
        result = await resolve_group_names("fake_token")

    assert result == {"g1": "AllFabric", "g2": None, "g3": None}


@pytest.mark.asyncio
async def test_member_of_still_filters_out_directory_roles():
    """Only #microsoft.graph.group objects become groups — pin the behaviour the
    fix reshaped the loop around, so a refactor cannot quietly widen it."""
    def handler(request):
        return httpx.Response(200, json={"value": [
            {"@odata.type": "#microsoft.graph.group", "id": "g1", "displayName": "AllFabric"},
            {"@odata.type": "#microsoft.graph.directoryRole", "id": "role-123",
             "displayName": "Global Administrator"},
        ]})

    with _mock_httpx(handler):
        from app.ee.oidc.graph_client import resolve_group_names
        result = await resolve_group_names("fake_token")

    assert result == {"g1": "AllFabric"}


# ── getByIds ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_by_ids_leaves_an_unreadable_id_none():
    """getByIds omits objects the app registration cannot read. Those ids must
    come back as None, never named after themselves."""
    def handler(request):
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json={"access_token": "app_token"})
        return httpx.Response(200, json={
            "value": [{"id": RESOLVED, "displayName": "PowerBI-ServicePrincipals"}]
        })

    with _mock_httpx(handler):
        from app.ee.oidc.graph_client import resolve_group_names_by_ids
        result = await resolve_group_names_by_ids(
            group_ids=[RESOLVED, UNREADABLE],
            tenant_id="t", client_id="c", client_secret="s",
        )

    assert result == {RESOLVED: "PowerBI-ServicePrincipals", UNREADABLE: None}


@pytest.mark.asyncio
async def test_get_by_ids_treats_a_blank_display_name_as_unresolved():
    """`displayName: ""` is not a name. Taken literally it would create a group
    with an empty label, which the unique (org, name) constraint then makes the
    only such group the org can ever have."""
    def handler(request):
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json={"access_token": "app_token"})
        return httpx.Response(200, json={"value": [{"id": UNREADABLE, "displayName": "  "}]})

    with _mock_httpx(handler):
        from app.ee.oidc.graph_client import resolve_group_names_by_ids
        result = await resolve_group_names_by_ids(
            group_ids=[UNREADABLE], tenant_id="t", client_id="c", client_secret="s",
        )

    assert result == {UNREADABLE: None}


@pytest.mark.asyncio
async def test_get_by_ids_batches_beyond_fifteen_and_returns_every_id():
    """The rewrite moved the URL out of the loop and seeded the result with
    dict.fromkeys — check every id still comes back, in batches of 15."""
    ids = [f"{i:08d}-0000-0000-0000-000000000000" for i in range(32)]
    batches = []

    def handler(request):
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json={"access_token": "app_token"})
        body = json.loads(request.content)
        batches.append(body["ids"])
        return httpx.Response(200, json={
            "value": [{"id": g, "displayName": f"Group-{g[:8]}"} for g in body["ids"]]
        })

    with _mock_httpx(handler):
        from app.ee.oidc.graph_client import resolve_group_names_by_ids
        result = await resolve_group_names_by_ids(
            group_ids=ids, tenant_id="t", client_id="c", client_secret="s",
        )

    assert [len(b) for b in batches] == [15, 15, 2]
    assert set(result) == set(ids)
    assert all(v is not None for v in result.values())


# ── the labelling rule ───────────────────────────────────────────────────────

def test_only_a_guid_shaped_id_gets_the_placeholder():
    """Okta, Keycloak, Auth0 and Entra-with-sAMAccountName put the readable name
    straight in the claim. Labelling "Engineering" as unresolved would be a
    downgrade, so a non-GUID claim value is taken as the name it already is."""
    from app.ee.oidc.group_sync_service import unresolved_group_label

    assert unresolved_group_label(UNREADABLE) == "Unresolved directory group (85f43b45…)"
    assert unresolved_group_label("Engineering") == "Engineering"
    assert unresolved_group_label("CN=Sales,OU=Groups,DC=corp") == "CN=Sales,OU=Groups,DC=corp"


def test_a_name_equal_to_the_id_does_not_count_as_resolved():
    """Older callers backfilled unresolved ids with the id itself, and rows
    created that way are still in the database — so a lookup answering with the
    id must read as unresolved, not as a resolution."""
    from app.ee.oidc.group_sync_service import _resolved_name

    assert _resolved_name({UNREADABLE: UNREADABLE}, UNREADABLE) is None
    assert _resolved_name({UNREADABLE: None}, UNREADABLE) is None
    assert _resolved_name({UNREADABLE: "  "}, UNREADABLE) is None
    assert _resolved_name({}, UNREADABLE) is None
    assert _resolved_name({UNREADABLE: "PowerBI-SPs"}, UNREADABLE) == "PowerBI-SPs"


def test_the_service_and_the_migration_write_the_same_label():
    """Migration ``oidcgrp01`` relabels rows already stored under a raw GUID, and
    its downgrade recognises its own work by pattern. If the service ever writes
    a different shape, the migration silently stops matching what the service
    produces and an existing list keeps its GUIDs."""
    import importlib.util
    from pathlib import Path

    from app.ee.oidc.group_sync_service import unresolved_group_label

    path = (Path(__file__).resolve().parents[3] / "alembic" / "versions"
            / "oidcgrp01_relabel_guid_named_oidc_groups.py")
    assert path.exists(), path
    spec = importlib.util.spec_from_file_location("_oidcgrp01", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.down_revision == "dpanl0001", (
        "this fork's single head is dpanl0001; upstream's parent would fork the graph"
    )
    assert mod._LABEL_RE.match(unresolved_group_label(UNREADABLE))
    assert not mod._LABEL_RE.match(unresolved_group_label("Engineering"))
    assert bool(mod._GUID_RE.match(UNREADABLE))
    assert not mod._GUID_RE.match("Engineering")
