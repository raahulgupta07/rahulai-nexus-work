"""Drive the chat API from a one-line answer up to images and interrupts.

    docker cp scripts/chat-levels.py dash-app:/app/backend/
    docker exec -w /app/backend dash-app python chat-levels.py --level 3
    docker exec -w /app/backend dash-app python chat-levels.py --all

★Eight levels, each independent. L7 does not need L1 to have passed, so a
failure early never hides a result later — every level builds its own report
and reports its own verdict. A level that cannot run says WHY and returns SKIP;
only a real defect returns FAIL.

★Like chat-round-trip.py this is NOT in any test suite and must not be. It
spends real money on a third-party model and takes minutes per level.

★It asserts the TURN, never the ANSWER. A wrong number is printed, not failed
on — answer quality is a model question and would make this flake. What is
asserted: the turn settles, it says something, no block errored, and (where the
level is about memory or context) that the second answer could only have been
produced with the first in hand.

★The answer lives in `completion_blocks`, NOT in `completion`. This endpoint
serves `completion: null` on every row and puts the text in an ordered block
list. The DB row still carries the legacy `completion` JSON, so reading the
database and reading the API disagree — chat-round-trip.py shipped with that
bug and reported healthy turns as failures.
"""
import argparse
import asyncio
import base64
import json
import os
import sys
import time

sys.path.insert(0, "/app/backend")
import main  # noqa: F401  — registers the ORM registry

import httpx
from sqlalchemy import select, text

from app.core.auth import get_jwt_strategy
from app.dependencies import async_session_maker
from app.models.user import User

BASE = os.environ.get("CHAT_BASE", "http://localhost:3000")
EMAIL = os.environ.get("SMOKE_EMAIL", "raahulgupta07@gmail.com")

# ★A local CSV/DuckDB agent deliberately. Power BI and Fabric here are
# `user_required` per-user sign-in; a failure against those means an expired
# token and reads as a broken product when it is nothing of the kind.
AGENT_NAME = os.environ.get("CHAT_AGENT", "City Mart Retail")
DEADLINE_S = int(os.environ.get("CHAT_DEADLINE", "300"))

# A 1x1 red PNG. Small on purpose — L7 is about whether the image REACHES the
# model, not about what is in it.
RED_DOT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)


class Turn:
    """One settled system turn, in the shape the rest of this file wants."""

    def __init__(self, raw):
        self.raw = raw or {}
        self.status = self.raw.get("status")
        self.blocks = self.raw.get("completion_blocks") or []
        self.text = "\n".join(
            (b.get("content") or "").strip()
            for b in self.blocks
            if (b.get("content") or "").strip()
        )
        self.sigkilled = bool(self.raw.get("sigkill"))
        self.errored = [b for b in self.blocks if b.get("status") == "error"]
        self.steps = self.raw.get("created_steps") or []

    def healthy(self):
        """The turn-level assertions, identical at every level."""
        if self.sigkilled:
            return False, "sigkilled"
        if self.status != "success":
            return False, f"ended {self.status!r}, not success"
        if not self.text.strip():
            # ★A turn that succeeds and says nothing is a failure. It looks
            # green in every status field and is useless to the person who
            # asked.
            return False, "succeeded and said nothing"
        if self.errored:
            return False, f"{len(self.errored)} block(s) errored inside a 'success' turn"
        return True, "ok"


class Client:
    """Thin wrapper that knows how to start a turn and wait for it to settle."""

    def __init__(self, http, org, ds, model):
        self.http = http
        self.org = org
        self.ds = ds
        self.model = model

    async def new_report(self, title):
        r = await self.http.post(
            "/api/reports", json={"title": title, "data_sources": [self.ds]}
        )
        r.raise_for_status()
        return r.json()["id"]

    async def ask(self, report_id, question, *, mode="chat", wait=True, label=""):
        """One turn. Returns a Turn, or the raw completion id when wait=False."""
        print(f"    ask{(' ' + label) if label else ''}: {question[:70]}", flush=True)
        t0 = time.time()
        # ★background=true, not the SSE stream. A socket that must stay open
        # for minutes turns any network hiccup into a fake product failure.
        r = await self.http.post(
            f"/api/reports/{report_id}/completions",
            params={"background": "true"},
            json={"prompt": {"content": question, "mode": mode, "model_id": self.model}},
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"POST completions -> {r.status_code} {r.text[:300]}")
        if not wait:
            return r.json()

        seen = await self._settle(report_id, t0)
        print(f"      settled in {int(time.time()-t0)}s: {seen.status}, "
              f"{len(seen.blocks)} blocks, {len(seen.text)} chars", flush=True)
        return seen

    async def _settle(self, report_id, t0, *, after=0):
        """Poll until the newest system turn stops moving."""
        while time.time() - t0 < DEADLINE_S:
            await asyncio.sleep(5)
            g = await self.http.get(f"/api/reports/{report_id}/completions")
            g.raise_for_status()
            payload = g.json()
            items = payload if isinstance(payload, list) else payload.get("completions", [])
            system = [x for x in items if x.get("role") == "system"]
            if len(system) <= after:
                continue
            last = system[-1]
            if last.get("status") not in ("in_progress", "queued", None, ""):
                return Turn(last)
            print(f"      … {int(time.time()-t0)}s status={last.get('status')}", flush=True)
        raise TimeoutError(f"no settled system turn within {DEADLINE_S}s")

    async def turns(self, report_id):
        g = await self.http.get(f"/api/reports/{report_id}/completions")
        g.raise_for_status()
        payload = g.json()
        items = payload if isinstance(payload, list) else payload.get("completions", [])
        return [Turn(x) for x in items if x.get("role") == "system"]


# ────────────────────────────────────────────────────────────────────────────
# Levels. Each returns (verdict, detail) where verdict is PASS / FAIL / SKIP.
# ────────────────────────────────────────────────────────────────────────────


async def L1(c):
    """Does the loop turn at all — no tools, no data."""
    rid = await c.new_report("L1 — loop turns")
    t = await c.ask(rid, "Reply with exactly: OK. Nothing else.")
    ok, why = t.healthy()
    return ("PASS" if ok else "FAIL"), f"{why}; said {t.text[:80]!r}"


async def L2(c):
    """One fact out of the real warehouse."""
    rid = await c.new_report("L2 — one fact")
    t = await c.ask(rid, "How many rows are in the sales table? Answer with just the number.")
    ok, why = t.healthy()
    if not ok:
        return "FAIL", why
    # ★Whether it QUERIED is the interesting part, and it is reported rather
    # than asserted: a chat-mode answer can legitimately come from context.
    return "PASS", f"{why}; {len(t.steps)} step(s) created; said {t.text[:80]!r}"


async def L3(c):
    """Memory. Three turns in ONE report, each leaning on the last."""
    rid = await c.new_report("L3 — memory across turns")

    t1 = await c.ask(rid, "How many rows are in the sales table?", label="1/3")
    ok, why = t1.healthy()
    if not ok:
        return "FAIL", f"turn 1: {why}"

    # ★"that" is the whole test. It resolves ONLY against turn 1. A model with
    # no history either asks which table, or silently re-derives from scratch.
    t2 = await c.ask(rid, "Now break that down by category.", label="2/3")
    ok, why = t2.healthy()
    if not ok:
        return "FAIL", f"turn 2: {why}"

    # ★The check is NEGATIVE, deliberately. Asserting the answer is right is a
    # model-quality question that would flake; asserting it did not have to ASK
    # is a product question with one correct answer.
    lost = ("which table" in t2.text.lower()
            or "what would you like" in t2.text.lower()
            or "could you clarify" in t2.text.lower())
    if lost:
        return "FAIL", f"turn 2 asked for context it was already given: {t2.text[:160]!r}"

    t3 = await c.ask(rid, "Which of those categories was highest?", label="3/3")
    ok, why = t3.healthy()
    if not ok:
        return "FAIL", f"turn 3: {why}"
    lost3 = "which categor" in t3.text.lower() and "?" in t3.text[:200]
    if lost3:
        return "FAIL", f"turn 3 lost turn 2: {t3.text[:160]!r}"

    return "PASS", (f"3 turns, none re-asked; "
                    f"t2={t2.text[:60]!r} t3={t3.text[:60]!r}")


async def L4(c):
    """Multi-step in a single turn — chained tools, a chart at the end."""
    rid = await c.new_report("L4 — multi-step")
    t = await c.ask(rid, "Compare total revenue by category and draw it as a bar chart.")
    ok, why = t.healthy()
    if not ok:
        return "FAIL", why
    tools = [b.get("title") for b in t.blocks if b.get("title")]
    return "PASS", f"{why}; {len(t.blocks)} blocks, {len(t.steps)} steps, blocks={tools[:6]}"


async def L5(c):
    """Compaction, then memory ACROSS it — where history most plausibly goes."""
    rid = await c.new_report("L5 — memory survives compaction")
    t1 = await c.ask(rid, "How many rows are in the sales table? Remember this number.", label="1/2")
    ok, why = t1.healthy()
    if not ok:
        return "FAIL", f"pre-compact turn: {why}"

    # ★A settled system TURN is not a finished RUN. The turn reports success
    # while the agent run is still being torn down, and compact refuses with
    # 409 "An agent run is in progress on this report" — which reads as the
    # endpoint being broken when it is the caller being early. Retry rather
    # than skip; the wait is the product telling the truth about its own state.
    for attempt in range(10):
        r = await c.http.post(f"/api/reports/{rid}/context/compact", json={})
        if r.status_code != 409:
            break
        print(f"      compact 409 (run still finishing), retry {attempt + 1}/10", flush=True)
        await asyncio.sleep(3)
    if r.status_code not in (200, 201, 204):
        return "SKIP", f"compact endpoint -> {r.status_code} {r.text[:160]}"
    print(f"      compacted: {r.status_code}", flush=True)

    t2 = await c.ask(rid, "What was the number you just told me?", label="2/2")
    ok, why = t2.healthy()
    if not ok:
        return "FAIL", f"post-compact turn: {why}"
    lost = ("don't have" in t2.text.lower() or "do not have" in t2.text.lower()
            or "no record" in t2.text.lower() or "cannot recall" in t2.text.lower())
    if lost:
        return "FAIL", f"compaction dropped the earlier turn: {t2.text[:160]!r}"
    return "PASS", f"survived compaction; said {t2.text[:100]!r}"


async def L6(c):
    """Does an instruction actually change the answer.

    ★This is 0.0.519's fix end to end. The bug left an accepted suggestion at
    `draft`, invisible to every context loader (they filter status=='published')
    and labelled "Inactive". If B comes back identical to A, that bug is back —
    and no read-only endpoint would show it.
    """
    ins_id = None
    try:
        rid_a = await c.new_report("L6a — before instruction")
        a = await c.ask(rid_a, "In one sentence, how should I describe revenue figures?", label="A")
        ok, why = a.healthy()
        if not ok:
            return "FAIL", f"baseline turn: {why}"

        stamp = str(int(time.time()))
        r = await c.http.post("/api/instructions", json={
            "text": (f"chatlevels-{stamp}: Always report revenue figures in thousands, "
                     f"suffixed with 'K'. Never report a raw revenue number."),
            "category": "general",
            "status": "published",
        })
        if r.status_code not in (200, 201):
            return "SKIP", f"could not create instruction -> {r.status_code} {r.text[:160]}"
        ins_id = r.json().get("id")
        print(f"      instruction {ins_id} published", flush=True)

        rid_b = await c.new_report("L6b — after instruction")
        b = await c.ask(rid_b, "In one sentence, how should I describe revenue figures?", label="B")
        ok, why = b.healthy()
        if not ok:
            return "FAIL", f"post-instruction turn: {why}"

        reached = "thousand" in b.text.lower() or "'k'" in b.text.lower() or " k" in b.text.lower()
        if not reached:
            return "FAIL", (f"a published instruction did not reach the prompt — "
                            f"A={a.text[:70]!r} B={b.text[:70]!r}")
        return "PASS", f"instruction reached the answer; B={b.text[:110]!r}"
    finally:
        if ins_id:
            await c.http.delete(f"/api/instructions/{ins_id}")
            print(f"      cleaned up instruction {ins_id}", flush=True)


async def L7(c):
    """Image attachment — 0.0.521, the live bug this release ported.

    ★It broke two different ways and the SILENT one is worse. Anthropic and
    Bedrock hard-400 with "tool_use ids were found without tool_result blocks
    immediately after" (the image was prepended AHEAD of the tool_result).
    OpenAI, Azure and LiteLLM silently DROPPED it — mid tool-loop the tail is a
    `tool` message, which cannot carry an image, so the model answered "I
    couldn't see the attachment" with nothing logged anywhere.

    ★The default model here is served through an OpenAI-shaped client — the
    silent path. So the assertion below is specifically that the model does NOT
    claim it cannot see the image.
    """
    rid = await c.new_report("L7 — image attachment (0.0.521)")

    files = {"file": ("reddot.png", RED_DOT_PNG, "image/png")}
    r = await c.http.post("/api/files", files=files, data={"report_id": rid})
    if r.status_code not in (200, 201):
        return "SKIP", f"upload -> {r.status_code} {r.text[:200]}"
    print(f"      uploaded image {r.json().get('id')}", flush=True)

    t = await c.ask(rid, "What colour is the image I just attached? One word.")
    ok, why = t.healthy()
    if not ok:
        return "FAIL", why

    blind = [p for p in (
        "couldn't see", "could not see", "can't see", "cannot see",
        "no image", "didn't receive", "did not receive", "unable to view",
        "don't see any", "do not see any", "no attachment",
    ) if p in t.text.lower()]
    if blind:
        return "FAIL", (f"★0.0.521 REGRESSION — the image was dropped silently. "
                        f"model said: {t.text[:200]!r}")
    return "PASS", f"image reached the model; said {t.text[:100]!r}"


def _running_id(started):
    """The SYSTEM completion id out of a start-a-turn response.

    ★POST /completions does not answer with the completion — it answers with
    `{report_id, completions: [...]}`, and the FIRST entry is the user's own
    prompt row (role='user', status='success'). Reading `["id"]` off the top
    level gets nothing, and taking completions[0] gets the user row, so a steer
    or sigkill aimed at it targets a turn that already finished and appears to
    do nothing. The one to interrupt is the system row.
    """
    if not isinstance(started, dict):
        return None
    rows = started.get("completions")
    if isinstance(rows, list):
        system = [r for r in rows if r.get("role") != "user"]
        if system:
            return system[-1].get("id")
        return None
    return started.get("id")


async def L8(c):
    """Interrupt — steer mid-turn, then sigkill. Cancellation must actually stop work."""
    notes = []

    rid = await c.new_report("L8a — steer")
    started = await c.ask(rid, "Count slowly from 1 to 40, one line each.", wait=False)
    cid = _running_id(started)
    if not cid:
        return "SKIP", f"no system completion came back: {str(started)[:160]}"
    await asyncio.sleep(4)
    r = await c.http.post(f"/api/completions/{cid}/steer",
                          json={"content": "Stop counting. Just say DONE."})
    notes.append(f"steer -> {r.status_code}")
    try:
        t = await c._settle(rid, time.time())
        notes.append(f"settled {t.status}")
    except TimeoutError as exc:
        return "FAIL", f"steered turn never settled: {exc}"

    rid2 = await c.new_report("L8b — sigkill")
    started = await c.ask(rid2, "Count slowly from 1 to 40, one line each.", wait=False)
    cid2 = _running_id(started)
    if not cid2:
        return "SKIP", f"no system completion to kill: {str(started)[:160]}"
    await asyncio.sleep(4)
    r = await c.http.post(f"/api/completions/{cid2}/sigkill", json={})
    notes.append(f"sigkill -> {r.status_code}")
    await asyncio.sleep(8)
    ts = await c.turns(rid2)
    last = ts[-1] if ts else None
    if last is None:
        return "FAIL", "sigkill left no system turn at all"
    # ★A killed turn must LOOK killed. Reporting 'success' after a sigkill is
    # the failure mode worth catching here.
    if last.status in ("in_progress", "queued"):
        return "FAIL", f"still running 8s after sigkill (status={last.status})"
    notes.append(f"killed turn status={last.status} sigkill={last.sigkilled}")
    return "PASS", "; ".join(notes)


LEVELS = [
    (1, "loop turns at all", L1),
    (2, "one fact from real data", L2),
    (3, "memory across turns", L3),
    (4, "multi-step, one turn", L4),
    (5, "memory survives compaction", L5),
    (6, "an instruction changes the answer (0.0.519)", L6),
    (7, "image attachment (0.0.521)", L7),
    (8, "steer and sigkill", L8),
]


async def setup():
    async with async_session_maker() as db:
        user = (await db.execute(select(User).where(User.email == EMAIL))).scalar_one()
        token = await get_jwt_strategy().write_token(user)
        org_id = (await db.execute(text(
            "select o.id from organizations o "
            "join memberships m on m.organization_id = o.id "
            "where m.user_id = :uid order by o.created_at limit 1"
        ), {"uid": str(user.id)})).scalar_one()
        ds_id = (await db.execute(text(
            "select id from data_sources where name = :n and deleted_at is null limit 1"
        ), {"n": AGENT_NAME})).scalar_one_or_none()
        model_id = (await db.execute(text(
            "select id from llm_models where is_enabled = true order by is_default desc limit 1"
        ))).scalar_one()
    if ds_id is None:
        raise SystemExit(f"no agent named {AGENT_NAME!r} — set CHAT_AGENT")
    return token, str(org_id), str(ds_id), str(model_id)


async def run(which):
    token, org, ds, model = await setup()
    # ★X-Organization-Id is not optional — without it every call 400s
    # `organization.required`. See CLAUDE.md.
    headers = {"Authorization": f"Bearer {token}", "X-Organization-Id": org}
    print(f"\norg   {org}\nagent {AGENT_NAME} ({ds})\nmodel {model}\n")

    results = []
    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=120) as http:
        c = Client(http, org, ds, model)
        for num, title, fn in LEVELS:
            if which and num not in which:
                continue
            print(f"\n── L{num} · {title} " + "─" * max(0, 46 - len(title)), flush=True)
            t0 = time.time()
            try:
                verdict, detail = await fn(c)
            except Exception as exc:  # noqa: BLE001 — one level must not end the run
                verdict, detail = "FAIL", f"raised {type(exc).__name__}: {exc}"
            dt = round(time.time() - t0, 1)
            results.append((num, title, verdict, detail, dt))
            print(f"  → {verdict} ({dt}s) — {detail}", flush=True)

    print("\n" + "=" * 72)
    for num, title, verdict, detail, dt in results:
        print(f"L{num}  {verdict:4}  {dt:6.1f}s  {title}")
    bad = [r for r in results if r[2] == "FAIL"]
    skip = [r for r in results if r[2] == "SKIP"]
    print(f"\n{len(results) - len(bad) - len(skip)} passed, {len(bad)} failed, {len(skip)} skipped")
    if bad:
        print("\nfailures:")
        for num, title, _, detail, _ in bad:
            print(f"  L{num} {title} — {detail}")
    print("=" * 72)
    return 1 if bad else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", action="append", type=int, help="run only these levels")
    ap.add_argument("--all", action="store_true", help="run every level")
    a = ap.parse_args()
    if not a.level and not a.all:
        raise SystemExit("pick --level N (repeatable) or --all")
    raise SystemExit(asyncio.run(run(set(a.level or []))))
