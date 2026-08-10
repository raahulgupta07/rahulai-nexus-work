"""Slide decks, end to end: ask for one, then export the .pptx and open it.

    docker cp scripts/deck-check.py dash-app:/app/backend/
    docker exec -w /app/backend dash-app python deck-check.py

★Neither chat-levels.py nor chat-matrix.py covers PowerPoint. chat-matrix T6
covers dashboards and T7 covers csv/docx/xlsx/image ATTACHMENTS — reading files
in, not generating one. A deck is the only artifact this product BUILDS as a
binary, through `generate_slides`, and that path has its own history: the model
hallucinates python-pptx APIs (`chart.plot_area`, `chart.chart_area` — neither
exists) and unguarded divisions blow up on data that aggregates to zero.
`sanitize_pptx_code()` neutralises the first class at generation time.

★Like its siblings this SPENDS REAL MONEY and is not a test suite. It asserts
the ARTIFACT, never the aesthetics: status is not `failed`, a `pptx_path`
exists, the export route returns a real OOXML package, and the package opens
and contains at least one slide.

★A failed deck is `status='failed'` with a NULL `pptx_path`, and the export
route then answers 400 "Slides generation failed". That 400 is the product
telling the truth, so it is recorded as a FAIL of the deck, not of the route.
"""
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
from sqlalchemy import select, text

from app.core.auth import get_jwt_strategy
from app.dependencies import async_session_maker
from app.models.user import User

BASE = os.environ.get("CHAT_BASE", "http://localhost:3000")
EMAIL = os.environ.get("SMOKE_EMAIL", "raahulgupta07@gmail.com")
# Above the server's query_hard_timeout_seconds (900) for the same reason as
# chat-matrix: equal values make "the product killed it" and "I stopped
# watching" the same log line.
DEADLINE_S = int(os.environ.get("CHAT_DEADLINE", "960"))
AGENT = os.environ.get("DECK_AGENT", "City Mart Retail")

RESULTS = []


def record(name, verdict, detail):
    RESULTS.append((name, verdict, detail))
    print(f"  [{verdict}] {name} — {detail}", flush=True)


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
    # ★The answer lives in completion_blocks, NOT in `completion` — this
    # endpoint serves `completion: null` on every row.
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
        record("deck", "SKIP", f"no agent called {AGENT}")
        return

    headers = {"Authorization": f"Bearer {token}", "X-Organization-Id": str(org)}
    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=120) as http:
        r = await http.post("/api/reports",
                            json={"title": "deck-check", "data_sources": [str(agent)]})
        r.raise_for_status()
        rid = r.json()["id"]
        print(f"  report {rid}", flush=True)

        q = ("Build a short slide deck (3 slides) on the top 5 categories by net sales: "
             "a title slide, a slide with the ranked table, and a slide with a bar chart.")
        print(f"    ask: {q[:78]}", flush=True)
        t0 = time.time()
        r = await http.post(f"/api/reports/{rid}/completions",
                            params={"background": "true"},
                            json={"prompt": {"content": q, "mode": "chat", "model_id": str(model)}})
        if r.status_code not in (200, 201):
            record("deck turn", "FAIL", f"POST completions -> {r.status_code} {r.text[:200]}")
            return
        turn = await settle(http, rid, t0)
        text_out = blocks_text(turn)
        print(f"      {int(time.time()-t0)}s status={turn.get('status')} {len(text_out)}ch", flush=True)

        if turn.get("status") not in ("success", "completed"):
            record("deck turn", "FAIL", f"turn status={turn.get('status')}")
            return
        record("deck turn", "PASS", f"settled in {int(time.time()-t0)}s")

        # ★`/api/artifacts/report/{id}` — the other spelling exists only on the
        # public share router and 404s here, which reads as "no deck was made".
        a = await http.get(f"/api/artifacts/report/{rid}")
        arts = a.json() if a.status_code == 200 else []
        arts = arts if isinstance(arts, list) else arts.get("items", [])
        decks = [x for x in arts
                 if "slide" in json.dumps(x).lower() or "pptx" in json.dumps(x).lower()]
        if not decks:
            record("deck artifact", "FAIL",
                   f"no slides artifact on the report (artifacts seen: {len(arts)})")
            return
        d = decks[0]
        did, status = d.get("id"), d.get("status")
        record("deck artifact", "PASS" if status != "failed" else "FAIL",
               f"id={did} status={status}")
        if status == "failed":
            return

        exp = await http.get(f"/api/artifacts/{did}/export/pptx")
        if exp.status_code != 200:
            record("pptx export", "FAIL", f"{exp.status_code} {exp.text[:160]}")
            return
        blob = exp.content
        # A .pptx is an OOXML zip. Anything else — an HTML error page, an empty
        # body — is not a deck however healthy the status code looks.
        try:
            z = zipfile.ZipFile(io.BytesIO(blob))
            slides = [n for n in z.namelist() if n.startswith("ppt/slides/slide")]
        except Exception as e:
            record("pptx export", "FAIL", f"{len(blob)}B and not a zip: {e}")
            return
        record("pptx export", "PASS" if slides else "FAIL",
               f"{len(blob)}B, {len(slides)} slide(s): {slides[:4]}")


if __name__ == "__main__":
    asyncio.run(main_())
    print("\n=== deck-check ===", flush=True)
    for name, verdict, detail in RESULTS:
        print(f"  [{verdict}] {name} — {detail}", flush=True)
    bad = [r for r in RESULTS if r[1] == "FAIL"]
    print(f"  {len(RESULTS)} case(s), {len(bad)} FAIL", flush=True)
