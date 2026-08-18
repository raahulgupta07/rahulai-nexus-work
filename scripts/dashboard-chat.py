"""One chat turn that must produce a DETAILED dashboard, against a LIVE build.

    docker cp scripts/dashboard-chat.py dash-app:/app/backend/
    docker exec -w /app/backend dash-app python dashboard-chat.py

★SPENDS REAL MONEY. Not a test suite.

★Status is not the verdict. A turn that exhausts its retries still records
status=success, so this reads the blocks for the give-up marker, then counts
what the dashboard artifact actually CONTAINS — widgets, and how many of them
carry a chart rather than a bare number. A dashboard that "succeeded" with one
text tile is the failure this checks for.
"""
import asyncio, json, os, re, sys, time

sys.path.insert(0, "/app/backend")
import main  # noqa: F401  — registers the ORM registry

import httpx
from sqlalchemy import select, text

from app.core.auth import get_jwt_strategy
from app.dependencies import async_session_maker
from app.models.user import User

BASE = os.environ.get("CHAT_BASE", "http://localhost:3000")
EMAIL = os.environ.get("SMOKE_EMAIL", "raahulgupta07@gmail.com")
DEADLINE_S = int(os.environ.get("CHAT_DEADLINE", "1200"))
AGENT = os.environ.get("DECK_AGENT", "City Mart Retail")

ASK = os.environ.get("DASH_ASK") or (
    "Build me a detailed sales dashboard for City Mart Retail. I want the "
    "headline numbers at the top — total net sales, transactions, average "
    "basket — then a chart of net sales by category, a chart of the top "
    "outlets, and a trend of sales over time. Label everything clearly."
)


async def settle(http, rid, t0):
    while time.time() - t0 < DEADLINE_S:
        await asyncio.sleep(5)
        g = await http.get(f"/api/reports/{rid}/completions")
        g.raise_for_status()
        p = g.json()
        items = p if isinstance(p, list) else p.get("completions", [])
        system = [x for x in items if x.get("role") == "system"]
        if system and system[-1].get("status") not in ("in_progress", "queued", None, ""):
            return system[-1]
    raise TimeoutError(f"no settled turn within {DEADLINE_S}s")


def blocks_text(turn):
    out = []
    for b in turn.get("completion_blocks") or []:
        for k in ("content", "title"):
            v = b.get(k)
            if isinstance(v, str):
                out.append(v)
    return "\n".join(out)


async def main_():
    async with async_session_maker() as db:
        user = (await db.execute(select(User).where(User.email == EMAIL))).scalar_one()
        token = await get_jwt_strategy().write_token(user)
        org = (await db.execute(text(
            "select o.id from organizations o join memberships m on m.organization_id=o.id "
            "where m.user_id=:u order by o.created_at limit 1"), {"u": str(user.id)})).scalar_one()
        model = (await db.execute(text(
            "select id from llm_models where is_enabled = true order by is_default desc limit 1"
        ))).scalar_one()
        agent = (await db.execute(text(
            "select id from data_sources where name=:n and deleted_at is null limit 1"
        ), {"n": AGENT})).scalar_one_or_none()

    if not agent:
        print(f"[SKIP] no agent called {AGENT}")
        return

    headers = {"Authorization": f"Bearer {token}", "X-Organization-Id": str(org)}
    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=180) as http:
        r = await http.post("/api/reports",
                            json={"title": "Detailed sales dashboard",
                                  "data_sources": [str(agent)]})
        r.raise_for_status()
        rid = r.json()["id"]
        print(f"report {rid}", flush=True)
        print(f"ask   : {ASK[:110]}...", flush=True)

        t0 = time.time()
        r = await http.post(f"/api/reports/{rid}/completions",
                            params={"background": "true"},
                            json={"prompt": {"content": ASK, "mode": "chat",
                                             "model_id": str(model)}})
        if r.status_code not in (200, 201):
            print(f"[FAIL] POST -> {r.status_code} {r.text[:200]}")
            return

        turn = await settle(http, rid, t0)
        secs = int(time.time() - t0)
        body = blocks_text(turn)
        if "Unable to complete task due to repeated tool validation errors" in body:
            print(f"[FAIL] {secs}s — the agent GAVE UP (status still says {turn.get('status')})")
            return
        print(f"[turn] {secs}s status={turn.get('status')} {len(body)}ch", flush=True)

        a = await http.get(f"/api/artifacts/report/{rid}")
        arts = a.json() if a.status_code == 200 else []
        arts = arts if isinstance(arts, list) else arts.get("items", [])
        dash = [x for x in arts if (x.get("mode") or "") not in ("slides", "doc")]
        if not dash:
            print(f"[FAIL] no dashboard artifact on the report (modes seen: "
                  f"{sorted({x.get('mode') for x in arts})})")
            return
        d = dash[-1]
        did = d.get("id")
        print(f"[artifact] id={did} mode={d.get('mode')} status={d.get('status')}", flush=True)

        w = await http.get(f"/api/reports/{rid}/widgets")
        widgets = w.json() if w.status_code == 200 else []
        widgets = widgets if isinstance(widgets, list) else widgets.get("widgets", [])
        charted = [x for x in widgets
                   if "chart" in json.dumps(x).lower() or "series" in json.dumps(x).lower()]
        print(f"[widgets] {len(widgets)} total, {len(charted)} carrying a chart", flush=True)
        for x in widgets[:12]:
            print(f"    - {str(x.get('title') or x.get('name'))[:70]}", flush=True)

        print(f"\nOPEN: /r/{rid}?artifact={did}", flush=True)
        print(f"      /dashboards   (the card for this artifact lands here)", flush=True)


if __name__ == "__main__":
    asyncio.run(main_())
