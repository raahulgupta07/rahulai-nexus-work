"""Probe the surfaces the 532→541 port ADDED. Companion to api-smoke.py.

    docker cp scripts/api-smoke-541.py dash-app:/app/backend/
    docker exec -w /app/backend dash-app python api-smoke-541.py
    docker exec -w /app/backend dash-app python api-smoke-541.py --json /tmp/r.json

★api-smoke.py predates this release and covers NONE of it: every check in there
passed on 0.0.538 too, so a green run there says nothing about the port. This
file asks only about what 539/540/541 brought — the OAuth application server,
the per-agent export bundle, and the three new connector types.

★Read-only by DEFAULT is not possible here: an OAuth client that is never
created cannot be listed, rotated or revoked, and the consent screen has nothing
to describe. So this creates ONE client named `apismoke541-*`, exercises it, and
deletes it in a `finally`. Nothing else is written.

★The metadata endpoints MUST answer anonymously. RFC 9728 is a discovery
document a client fetches BEFORE it has any token; gating it breaks every app
that would ever register. `tests/unit/fork/test_every_route_is_gated.py` carries
the same fact as a PUBLIC_BY_DESIGN entry — this proves it against the running
server rather than against the source.
"""
import argparse
import asyncio
import io
import json
import os
import sys
import time
import zipfile

sys.path.insert(0, "/app/backend")
import main  # noqa: F401  — registers the ORM registry

import httpx
from sqlalchemy import select

from app.core.auth import get_jwt_strategy
from app.dependencies import async_session_maker
from app.models.user import User

BASE = os.environ.get("API_SMOKE_BASE", "http://localhost:3000")
EMAIL = os.environ.get("SMOKE_EMAIL", "raahulgupta07@gmail.com")
ORG_HEADER = "X-Organization-Id"
TAG = f"apismoke541-{int(time.time())}"

RESULTS = []


def record(group, name, ok, detail=""):
    RESULTS.append((group, name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name} — {detail}")


async def g_metadata(anon):
    """540: RFC 9728 / 8414 discovery. Anonymous, or no app can ever register."""
    for path, must_have in (
        ("/.well-known/oauth-protected-resource", "authorization_servers"),
        ("/.well-known/oauth-protected-resource/api", "authorization_servers"),
        ("/.well-known/oauth-authorization-server", "token_endpoint"),
    ):
        r = await anon.get(path)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        record("metadata", f"anonymous GET {path}", r.status_code == 200 and must_have in body,
               f"{r.status_code}, {must_have}={'present' if must_have in body else 'MISSING'}")

    r = await anon.get("/.well-known/oauth-authorization-server")
    scopes = (r.json() or {}).get("scopes_supported") or []
    record("metadata", "advertised scopes are the two this server issues",
           sorted(scopes) == ["app", "mcp"], f"{scopes}")


async def g_clients(client, anon):
    """540: the application registry — create, list, consent info, rotate, revoke."""
    created = None
    try:
        r = await client.post("/api/oauth/clients", json={
            "name": TAG, "scopes": "app mcp",
            "redirect_uris": ["https://example.invalid/cb"],
        })
        ok = r.status_code == 200 and r.json().get("client_id")
        created = r.json() if ok else None
        record("oauth", "register an application", bool(ok),
               f"{r.status_code}, client_id={(created or {}).get('client_id', '—')}")
        if not created:
            return

        secret = created.get("client_secret")
        record("oauth", "the secret is returned once, at creation", bool(secret),
               "present" if secret else "MISSING — the app could never authenticate")

        r = await client.get("/api/oauth/clients")
        rows = r.json() if r.status_code == 200 else []
        mine = [c for c in rows if c.get("name") == TAG]
        record("oauth", "it appears in the list", r.status_code == 200 and len(mine) == 1,
               f"{r.status_code}, {len(mine)} match of {len(rows)}")

        leaked = [k for c in rows for k in c if "secret" in k.lower() and c.get(k)]
        record("oauth", "the list never carries a secret", not leaked,
               "clean" if not leaked else f"LEAKED {sorted(set(leaked))}")

        cid = created["client_id"]
        r = await client.get(f"/api/oauth/clients/{cid}/info",
                             params={"redirect_uri": "https://example.invalid/cb", "scope": "app"})
        info = r.json() if r.status_code == 200 else {}
        record("oauth", "consent screen can describe the app", r.status_code == 200 and info.get("name") == TAG,
               f"{r.status_code}, name={info.get('name', '—')}, scopes={info.get('requested_scopes')}")

        r = await client.get(f"/api/oauth/clients/{cid}/info",
                             params={"redirect_uri": "https://attacker.invalid/cb"})
        record("oauth", "an unregistered redirect_uri is refused", r.status_code == 400,
               f"{r.status_code} (400 expected)")

        r = await client.get(f"/api/oauth/clients/{cid}/info", params={"scope": "everything"})
        record("oauth", "an unsupported scope is refused", r.status_code == 400,
               f"{r.status_code} (400 expected)")

        r = await client.post(f"/api/oauth/clients/{created['id']}/rotate")
        new_secret = (r.json() or {}).get("client_secret") if r.status_code == 200 else None
        record("oauth", "rotating issues a different secret",
               bool(new_secret) and new_secret != secret,
               f"{r.status_code}, changed={new_secret != secret if new_secret else 'n/a'}")

        r = await anon.get("/api/oauth/clients")
        record("oauth", "anonymous cannot list applications", r.status_code in (401, 403),
               f"{r.status_code}")

        r = await client.post("/api/oauth/token", data={
            "grant_type": "authorization_code", "code": "not-a-real-code",
            "client_id": cid, "client_secret": new_secret or secret,
            "redirect_uri": "https://example.invalid/cb",
        })
        record("oauth", "an invented authorization code is refused",
               r.status_code in (400, 401), f"{r.status_code} (never 200, never 500)")
    finally:
        if created:
            r = await client.delete(f"/api/oauth/clients/{created['id']}")
            record("oauth", "revoke it (cleanup)", r.status_code == 200, f"{r.status_code}")
            r = await client.get("/api/oauth/clients")
            gone = all(c.get("name") != TAG for c in (r.json() or []))
            record("oauth", "a revoked application leaves the list", gone,
                   "absent" if gone else "STILL LISTED")


async def g_export(client):
    """541: the per-agent bundle. A zip that does not open is not a bundle."""
    r = await client.get("/api/data_sources")
    agents = r.json() if r.status_code == 200 else []
    if not agents:
        record("export", "an agent exists to export", False, "no agents on this install")
        return
    agent = agents[0]
    r = await client.get(f"/api/data_sources/{agent['id']}/instructions/export")
    ok = r.status_code == 200
    record("export", "export a single agent", ok,
           f"{r.status_code}, {len(r.content)} bytes, {r.headers.get('content-type')}")
    if not ok:
        return

    disp = r.headers.get("content-disposition", "")
    record("export", "it downloads as a named file", "attachment" in disp and "agent-export.zip" in disp,
           disp[:90] or "no content-disposition")

    try:
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        names = zf.namelist()
        bad = zf.testzip()
        record("export", "the zip actually opens", bad is None and bool(names),
               f"{len(names)} entries: {', '.join(names[:6])}")
    except Exception as exc:  # noqa: BLE001
        record("export", "the zip actually opens", False, f"{type(exc).__name__}: {exc}")
        return

    r = await client.get("/api/data_sources/00000000-0000-0000-0000-000000000000/instructions/export")
    record("export", "exporting an unknown agent 404s (not 500)", r.status_code in (400, 403, 404),
           f"{r.status_code}")


async def g_connectors(client):
    """539: the three new sources must be offerable, not merely importable."""
    r = await client.get("/api/available_data_sources")
    if r.status_code != 200:
        r = await client.get("/api/data_sources/available")
    rows = r.json() if r.status_code == 200 else []
    names = {str(x.get("type") or x.get("name") or x).lower() for x in rows} if isinstance(rows, list) else set()
    blob = json.dumps(rows).lower()
    for label, needles in (
        ("Power BI file (.pbix)", ("pbix",)),
        ("monday.com", ("monday",)),
        ("SharePoint Lists", ("sharepoint_list", "sharepointlist", "sharepoint list")),
    ):
        hit = any(n in blob for n in needles)
        record("connectors", f"{label} is offered", hit,
               "present" if hit else f"absent from {len(names)} offered types")


async def mint():
    async with async_session_maker() as db:
        row = await db.execute(select(User).where(User.email == EMAIL))
        user = row.scalar_one_or_none()
        if user is None:
            raise SystemExit(f"no user {EMAIL} — set SMOKE_EMAIL to a real account")
        token = await get_jwt_strategy().write_token(user)
        from app.models.membership import Membership
        row = await db.execute(select(Membership).where(Membership.user_id == str(user.id)))
        m = row.scalars().first()
        if m is None:
            raise SystemExit(f"{EMAIL} belongs to no organization")
        return token, str(m.organization_id)


async def run(out_path):
    token, org = await mint()
    print(f"\nbase={BASE}  user={EMAIL}  org={org}  tag={TAG}\n")
    headers = {"Authorization": f"Bearer {token}", ORG_HEADER: org}
    async with httpx.AsyncClient(base_url=BASE, timeout=120, headers=headers) as client, \
               httpx.AsyncClient(base_url=BASE, timeout=60) as anon:
        for name, fn, args in (
            ("metadata", g_metadata, (anon,)),
            ("oauth", g_clients, (client, anon)),
            ("export", g_export, (client,)),
            ("connectors", g_connectors, (client,)),
        ):
            print(f"\n── {name} " + "─" * (60 - len(name)))
            try:
                await fn(*args)
            except Exception as exc:  # noqa: BLE001
                record(name, "group crashed", False, repr(exc))

    failed = [r for r in RESULTS if not r[2]]
    print("\n" + "=" * 68)
    print(f"{len(RESULTS) - len(failed)} passed, {len(failed)} failed, {len(RESULTS)} checks")
    if failed:
        print("\nfailures:")
        for group, name, _, detail in failed:
            print(f"  {group}/{name} — {detail}")
    print("=" * 68)

    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump([{"group": g, "name": n, "ok": o, "detail": d} for g, n, o, d in RESULTS], fh, indent=2)
        print(f"wrote {out_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="also write the results to this path")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(run(a.json)))
