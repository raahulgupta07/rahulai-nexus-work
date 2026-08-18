"""Generate one deck per design system, over chat, and report what each got.

    docker cp scripts/deck-all-81.py dash-app:/app/backend/
    docker exec -w /app/backend dash-app python deck-all-81.py

★SPENDS REAL MONEY — 81 model turns. Not a test suite.

Each deck asks for its style BY NAME in the user's own words, so one run
measures both halves at once: did the request reach theme resolution, and did
the chosen system actually reach the file. The verdict per theme is not
"did a deck build" — every deck builds — it is `chosen == asked`, plus whether
the furniture painter and the enforcement pass did anything.

★CONCURRENCY is deliberately low. This host reaps containers under load and
the app is the thing under test; a fast sweep that kills dash-app measures
nothing.
"""
import asyncio, json, os, sys, time

sys.path.insert(0, "/app/backend")
import main  # noqa: F401
import httpx
from sqlalchemy import select, text

from app.core.auth import get_jwt_strategy
from app.dependencies import async_session_maker
from app.models.user import User
from app.ai.decks import pptx_themes

BASE = os.environ.get("CHAT_BASE", "http://localhost:3000")
EMAIL = os.environ.get("SMOKE_EMAIL", "raahulgupta07@gmail.com")
AGENT = os.environ.get("DECK_AGENT", "City Mart Retail")
CONC = int(os.environ.get("DECK_CONC", "3"))
DEADLINE_S = int(os.environ.get("CHAT_DEADLINE", "600"))
ONLY = [x for x in (os.environ.get("ONLY_THEMES") or "").split(",") if x]

ROWS = []


async def settle(http, rid, t0):
    while time.time() - t0 < DEADLINE_S:
        await asyncio.sleep(5)
        g = await http.get(f"/api/reports/{rid}/completions")
        if g.status_code != 200:
            continue
        p = g.json()
        items = p if isinstance(p, list) else p.get("completions", [])
        sysrows = [x for x in items if x.get("role") == "system"]
        if sysrows and sysrows[-1].get("status") not in ("in_progress", "queued", None, ""):
            return sysrows[-1]
    return None


async def observation_for(artifact_id):
    """The tool's own record of what it chose and what the passes did."""
    async with async_session_maker() as db:
        r = await db.execute(text(
            "select result_json from tool_executions "
            "where result_json::text like :pat order by created_at limit 1"
        ), {"pat": f"%{artifact_id}%"})
        row = r.scalar_one_or_none()
    if not row:
        return {}
    try:
        return (json.loads(row) if isinstance(row, str) else row).get("deck_theme") or {}
    except Exception:
        return {}


async def one(sem, http, model, agent, theme):
    async with sem:
        ask = (f"Make a short 3-slide deck introducing our category review process. "
               f"Use the {theme.name} style.")
        t0 = time.time()
        try:
            r = await http.post("/api/reports", json={
                "title": f"81 · {theme.id}", "data_sources": [str(agent)]})
            r.raise_for_status()
            rid = r.json()["id"]
            r = await http.post(f"/api/reports/{rid}/completions", params={"background": "true"},
                                json={"prompt": {"content": ask, "mode": "chat",
                                                 "model_id": str(model)}})
            if r.status_code not in (200, 201):
                ROWS.append({"id": theme.id, "verdict": "TURN", "detail": r.status_code}); return
            turn = await settle(http, rid, t0)
            if turn is None:
                ROWS.append({"id": theme.id, "verdict": "TIMEOUT", "detail": DEADLINE_S}); return

            a = await http.get(f"/api/artifacts/report/{rid}")
            arts = a.json() if a.status_code == 200 else []
            arts = arts if isinstance(arts, list) else arts.get("items", [])
            decks = [x for x in arts if (x.get("mode") == "slides")]
            if not decks:
                ROWS.append({"id": theme.id, "verdict": "NODECK", "detail": len(arts)}); return
            d = sorted(decks, key=lambda x: x.get("created_at") or "")[-1]
            obs = await observation_for(d.get("id"))
            chosen = obs.get("id")
            furn = (obs.get("furniture") or {}).get("painted") or []
            enf = obs.get("enforcement") or {}
            acted = sum(int(enf.get(k) or 0) for k in
                        ("shadows_cleared", "corners_squared", "gradients_flattened"))
            ROWS.append({
                "id": theme.id, "asked": theme.id, "chosen": chosen,
                "verdict": "MATCH" if chosen == theme.id else "MISMATCH",
                "method": obs.get("method"), "painted": len(furn),
                "enforced": acted, "status": d.get("status"),
                "artifact": d.get("id"), "secs": int(time.time() - t0),
            })
        except Exception as e:
            ROWS.append({"id": theme.id, "verdict": "ERROR", "detail": str(e)[:120]})
        print(f"  {len(ROWS):>3}/{TOTAL}  {ROWS[-1].get('verdict'):8} {theme.id}", flush=True)


async def main_():
    global TOTAL
    async with async_session_maker() as db:
        user = (await db.execute(select(User).where(User.email == EMAIL))).scalar_one()
        token = await get_jwt_strategy().write_token(user)
        org = (await db.execute(text(
            "select o.id from organizations o join memberships m on m.organization_id=o.id "
            "where m.user_id=:u order by o.created_at limit 1"), {"u": str(user.id)})).scalar_one()
        model = (await db.execute(text(
            "select id from llm_models where is_enabled = true order by is_default desc limit 1"))).scalar_one()
        agent = (await db.execute(text(
            "select id from data_sources where name=:n and deleted_at is null limit 1"),
            {"n": AGENT})).scalar_one_or_none()

    picked = [t for t in pptx_themes.all_themes() if not ONLY or t.id in ONLY]
    TOTAL = len(picked)
    print(f"{TOTAL} themes, concurrency {CONC}\n", flush=True)
    headers = {"Authorization": f"Bearer {token}", "X-Organization-Id": str(org)}
    sem = asyncio.Semaphore(CONC)
    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=180) as http:
        await asyncio.gather(*(one(sem, http, model, agent, t) for t in picked))


if __name__ == "__main__":
    TOTAL = 0
    asyncio.run(main_())
    ok = [r for r in ROWS if r.get("verdict") == "MATCH"]
    bad = [r for r in ROWS if r.get("verdict") != "MATCH"]
    print("\n=== all-81 ===", flush=True)
    print(f"  matched style: {len(ok)}/{len(ROWS)}", flush=True)
    print(f"  painted furniture: {sum(1 for r in ROWS if (r.get('painted') or 0) > 0)}", flush=True)
    print(f"  enforcement acted: {sum(1 for r in ROWS if (r.get('enforced') or 0) > 0)}", flush=True)
    for r in bad:
        print(f"  [{r.get('verdict')}] {r['id']} chosen={r.get('chosen')} {r.get('detail','')}", flush=True)
    print("\n  json:", json.dumps(ROWS), flush=True)
