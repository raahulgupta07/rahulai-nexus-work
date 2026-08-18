"""Slide decks from easy to complex, over the chat API, against a LIVE build.

    docker cp scripts/deck-tiers.py dash-app:/app/backend/
    docker exec -w /app/backend dash-app python deck-tiers.py

★Extends deck-check.py, which proves ONE deck builds. This walks seven tiers of
increasing difficulty and records, per tier, what the product actually did:
which theme was resolved, which fonts the generated code asked for, whether the
.pptx and the .pdf both come back as real packages, and where the previews are
so a person can LOOK at them. A deck that "succeeds" and renders wrong is the
failure mode this whole release exists to remove, so status is never the verdict
on its own.

★SPENDS REAL MONEY. Not a test suite.

★The tiers are ordered so a failure localises: T1 needs no data at all, so if it
fails the deck path itself is broken; T3 is the first that must query; T5 is the
first that must make a CHART DECISION (two scales are two exhibits, never two
series on one axis); T7 changes only the design system and must not re-analyse.
"""
import asyncio
import io
import json
import os
import re
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
DEADLINE_S = int(os.environ.get("CHAT_DEADLINE", "960"))
AGENT = os.environ.get("DECK_AGENT", "City Mart Retail")
ONLY = {t for t in (os.environ.get("TIERS") or "").split(",") if t}

RESULTS = []
NOTES = []


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


def gave_up(turn, body):
    """A turn that exhausted its retries still records status=success."""
    return "Unable to complete task due to repeated tool validation errors" in body


def blocks_text(turn):
    out = []
    for b in turn.get("completion_blocks") or []:
        for k in ("content", "title"):
            v = b.get(k)
            if isinstance(v, str):
                out.append(v)
    return "\n".join(out)


# --------------------------------------------------------------------------
# What the generated deck code actually asked for.
# --------------------------------------------------------------------------
FONT_RE = re.compile(r"""FONT_\w+\s*=\s*["']([^"']+)["']|\.font\.name\s*=\s*["']([^"']+)["']""")
HEX_RE = re.compile(r"RGBColor\((\d+),\s*(\d+),\s*(\d+)\)")


def code_profile(code: str) -> dict:
    fonts = set()
    for a, b in FONT_RE.findall(code or ""):
        fonts.add((a or b).strip())
    hexes = {"%02X%02X%02X" % (int(r), int(g), int(b)) for r, g, b in HEX_RE.findall(code or "")}
    return {
        "fonts": sorted(f for f in fonts if f),
        "colors": len(hexes),
        "uses_theme_var": bool(re.search(r"\btheme\s*[\[\.]", code or "")),
        "chars": len(code or ""),
    }


async def deck_of(http, rid):
    a = await http.get(f"/api/artifacts/report/{rid}")
    arts = a.json() if a.status_code == 200 else []
    arts = arts if isinstance(arts, list) else arts.get("items", [])
    decks = [x for x in arts if "slide" in json.dumps(x).lower() or "pptx" in json.dumps(x).lower()]
    return decks[-1] if decks else None


async def check_exports(http, tier, did, want_pdf):
    out = {}
    exp = await http.get(f"/api/artifacts/{did}/export/pptx")
    if exp.status_code != 200:
        record(f"{tier} pptx", "FAIL", f"{exp.status_code} {exp.text[:120]}")
    else:
        try:
            z = zipfile.ZipFile(io.BytesIO(exp.content))
            slides = [n for n in z.namelist() if n.startswith("ppt/slides/slide")]
            out["slides"] = len(slides)
            record(f"{tier} pptx", "PASS" if slides else "FAIL",
                   f"{len(exp.content):,}B, {len(slides)} slide(s)")
        except Exception as e:
            record(f"{tier} pptx", "FAIL", f"{len(exp.content)}B not a zip: {e}")
    if want_pdf:
        p = await http.get(f"/api/artifacts/{did}/export/pdf")
        if p.status_code == 200 and p.content[:4] == b"%PDF":
            record(f"{tier} pdf", "PASS", f"{len(p.content):,}B, real PDF")
        else:
            head = p.content[:4] if p.content else b""
            record(f"{tier} pdf", "FAIL", f"{p.status_code}, head={head!r}")
    return out


TIERS = [
    ("T1", "narrative, no data at all",
     "Make a 3-slide deck on our priorities for next quarter: grow basket size, "
     "cut out-of-stocks, open two outlets. No data needed — this is a talking deck.",
     False),
    ("T2", "theme named in the user's own words",
     "Make a 3-slide deck introducing our category review process. "
     "Use the ledger style.",
     False),
    ("T3", "first tier that must query",
     "Build a short 3-slide deck on the top 5 categories by net sales: a title "
     "slide, the ranked figures, and a bar chart.",
     True),
    ("T4", "long action titles — the collision repro",
     "Build a 4-slide performance review deck. Every content slide must open "
     "with a full-sentence action title stating that slide's conclusion, and a "
     "shorter grey lead-in line beneath it. Cover total net sales, the top "
     "categories, and what to do next.",
     False),
    ("T5", "two different scales — the chart decision",
     "Build a 3-slide deck comparing revenue and transaction COUNT for the top "
     "categories. Show both measures.",
     False),
    ("T6", "complex: 6 slides, several charts",
     "Build a 6-slide executive deck reviewing City Mart Retail sales: cover, "
     "headline numbers, category performance with a chart, an outlet view with "
     "a chart, a trend, and recommendations.",
     True),
]

RESTYLE = ("T7", "restyle only — must not re-analyse", "Make it Art Deco.")


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
        record("deck-tiers", "SKIP", f"no agent called {AGENT}")
        return

    headers = {"Authorization": f"Bearer {token}", "X-Organization-Id": str(org)}
    last_rid = None
    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=180) as http:
        for tier, label, ask, want_pdf in TIERS:
            if ONLY and tier not in ONLY:
                continue
            print(f"\n=== {tier} — {label}", flush=True)
            r = await http.post("/api/reports",
                                json={"title": f"deck-tiers {tier}", "data_sources": [str(agent)]})
            r.raise_for_status()
            rid = r.json()["id"]
            last_rid = rid
            print(f"  report {rid}\n  ask: {ask[:100]}", flush=True)

            t0 = time.time()
            r = await http.post(f"/api/reports/{rid}/completions",
                                params={"background": "true"},
                                json={"prompt": {"content": ask, "mode": "chat",
                                                 "model_id": str(model)}})
            if r.status_code not in (200, 201):
                record(f"{tier} turn", "FAIL", f"POST -> {r.status_code} {r.text[:140]}")
                continue
            try:
                turn = await settle(http, rid, t0)
            except TimeoutError as e:
                record(f"{tier} turn", "FAIL", str(e))
                continue
            secs = int(time.time() - t0)
            body = blocks_text(turn)
            if gave_up(turn, body):
                record(f"{tier} turn", "FAIL", f"{secs}s — agent GAVE UP (status says success)")
                continue
            if turn.get("status") not in ("success", "completed"):
                record(f"{tier} turn", "FAIL", f"{secs}s status={turn.get('status')}")
                continue
            record(f"{tier} turn", "PASS", f"{secs}s, {len(body)}ch")

            d = await deck_of(http, rid)
            if not d:
                record(f"{tier} artifact", "FAIL", "no slides artifact on the report")
                continue
            did, status = d.get("id"), d.get("status")
            if status == "failed":
                record(f"{tier} artifact", "FAIL", f"id={did} status=failed")
                continue
            record(f"{tier} artifact", "PASS", f"id={did} status={status}")

            prof = code_profile(d.get("content") or d.get("code") or "")
            NOTES.append((tier, did, rid, prof))
            print(f"      fonts={prof['fonts'][:4]} colors={prof['colors']} "
                  f"theme_var={prof['uses_theme_var']} code={prof['chars']}ch", flush=True)

            await check_exports(http, tier, did, want_pdf)

        # T7 — restyle the last deck, same report, no new analysis.
        if last_rid and (not ONLY or "T7" in ONLY):
            tier, label, ask = RESTYLE
            print(f"\n=== {tier} — {label}", flush=True)
            t0 = time.time()
            r = await http.post(f"/api/reports/{last_rid}/completions",
                                params={"background": "true"},
                                json={"prompt": {"content": ask, "mode": "chat",
                                                 "model_id": str(model)}})
            if r.status_code in (200, 201):
                try:
                    turn = await settle(http, last_rid, t0)
                    secs = int(time.time() - t0)
                    ok = turn.get("status") in ("success", "completed")
                    record(f"{tier} turn", "PASS" if ok else "FAIL",
                           f"{secs}s status={turn.get('status')}")
                    d = await deck_of(http, last_rid)
                    if d and d.get("status") != "failed":
                        prof = code_profile(d.get("content") or d.get("code") or "")
                        NOTES.append((tier, d.get("id"), last_rid, prof))
                        record(f"{tier} artifact", "PASS",
                               f"id={d.get('id')} fonts={prof['fonts'][:4]}")
                        await check_exports(http, tier, d.get("id"), False)
                    else:
                        record(f"{tier} artifact", "FAIL", "restyle produced no usable deck")
                except TimeoutError as e:
                    record(f"{tier} turn", "FAIL", str(e))
            else:
                record(f"{tier} turn", "FAIL", f"POST -> {r.status_code}")


if __name__ == "__main__":
    asyncio.run(main_())
    print("\n=== deck-tiers ===", flush=True)
    for name, verdict, detail in RESULTS:
        print(f"  [{verdict}] {name} — {detail}", flush=True)
    bad = [r for r in RESULTS if r[1] == "FAIL"]
    print(f"  {len(RESULTS)} case(s), {len(bad)} FAIL", flush=True)
    print("\n  previews to LOOK at (status is not the verdict):", flush=True)
    for tier, did, rid, prof in NOTES:
        print(f"    {tier}  uploads/pptx_previews/{did}/  fonts={prof['fonts'][:3]}", flush=True)
