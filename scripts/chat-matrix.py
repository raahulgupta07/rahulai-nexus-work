"""Every product surface, asked through the chat API, simplest question first.

    docker cp scripts/chat-matrix.py dash-app:/app/backend/
    docker exec -w /app/backend dash-app python chat-matrix.py --all
    docker exec -w /app/backend dash-app python chat-matrix.py --tier 7 --tier 9

★Twelve tiers: plain answer, schema, one query, chart, multi-step, dashboard,
files, folders, Fabric across lakehouses, insights, cross-agent mentions, and
what happens when the question cannot be answered at all.

★NOT a test suite and must never become one. It spends real money on a
third-party model and takes half an hour. `tests/` is for things that run in
seconds against a schema they built themselves; this asks the LIVE instance,
with the real warehouse and the real per-user sign-ins, whether the product
answers.

★It CAPTURES failures, it does not fix them and does not stop for them. Every
case records what came back — including the exact error text — and the run
continues. A tier that cannot run at all says why and returns SKIP; only a real
defect is FAIL.

★The assertion is the TURN, never the ANSWER. A wrong number is recorded and
printed, not failed on: answer quality is a model question and would make this
flake. Asserted: the turn settles, says something, no block errored, no 500.

★Nothing here writes to a connector or toggles an agent. `test_connection` is
spelled GET and WRITES `is_active` — calling it once against a per-user agent
disabled it org-wide and it vanished from the product. Read-only means
read-only; this only ever creates its own reports and uploads its own files.
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
DEADLINE_S = int(os.environ.get("CHAT_DEADLINE", "420"))
UPLOADS = "/app/backend/uploads"

RED_DOT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)

RESULTS = []


class Turn:
    def __init__(self, raw):
        self.raw = raw or {}
        self.status = self.raw.get("status")
        self.blocks = self.raw.get("completion_blocks") or []
        # ★The answer lives in `completion_blocks`. This endpoint serves
        # `completion: null` on every row; the DB still carries the legacy
        # `completion` JSON, so reading the database and reading the API
        # disagree. Reading the wrong one reports healthy turns as failures.
        self.text = "\n".join(
            (b.get("content") or "").strip()
            for b in self.blocks if (b.get("content") or "").strip()
        )
        self.sigkilled = bool(self.raw.get("sigkill"))
        self.errored = [b for b in self.blocks if b.get("status") == "error"]
        self.steps = self.raw.get("created_steps") or []
        self.titles = [b.get("title") for b in self.blocks if b.get("title")]

    def healthy(self):
        if self.sigkilled:
            return False, "sigkilled"
        if self.status != "success":
            return False, f"ended {self.status!r}"
        if not self.text.strip():
            return False, "succeeded and said nothing"
        if self.errored:
            msgs = "; ".join((b.get("content") or "")[:120] for b in self.errored)
            return False, f"{len(self.errored)} block(s) errored: {msgs}"
        return True, "ok"

    def tools(self):
        """Which tools the planner actually reached for, from block titles."""
        out = []
        for t in self.titles:
            if "→" in t:
                out.append(t.split("→")[-1].strip())
        return out


class Ctx:
    def __init__(self):
        self.org = None
        self.model = None
        self.agents = {}       # name -> id
        self.files = {}        # ext -> (filename, abspath)


CTX = Ctx()


def record(tier, name, verdict, detail):
    RESULTS.append((tier, name, verdict, detail))
    print(f"  [{verdict}] {name} — {detail}", flush=True)


class Client:
    def __init__(self, http):
        self.http = http

    async def report(self, title, agent_ids):
        r = await self.http.post("/api/reports",
                                 json={"title": title, "data_sources": agent_ids})
        r.raise_for_status()
        return r.json()["id"]

    async def ask(self, rid, q, *, mode="chat", label=""):
        print(f"    ask{(' '+label) if label else ''}: {q[:78]}", flush=True)
        t0 = time.time()
        # ★background=true, not the SSE stream: a socket held open for minutes
        # turns any network hiccup into a fake product failure.
        r = await self.http.post(
            f"/api/reports/{rid}/completions",
            params={"background": "true"},
            json={"prompt": {"content": q, "mode": mode, "model_id": CTX.model}},
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"POST completions -> {r.status_code} {r.text[:250]}")
        t = await self._settle(rid, t0)
        ok, why = t.healthy()
        print(f"      {int(time.time()-t0)}s  {t.status}  {len(t.blocks)}b "
              f"{len(t.text)}ch  tools={t.tools() or '-'}", flush=True)
        return t

    async def _settle(self, rid, t0):
        while time.time() - t0 < DEADLINE_S:
            await asyncio.sleep(5)
            g = await self.http.get(f"/api/reports/{rid}/completions")
            g.raise_for_status()
            p = g.json()
            items = p if isinstance(p, list) else p.get("completions", [])
            system = [x for x in items if x.get("role") == "system"]
            if not system:
                continue
            last = system[-1]
            if last.get("status") not in ("in_progress", "queued", None, ""):
                return Turn(last)
        raise TimeoutError(f"no settled turn within {DEADLINE_S}s")

    async def upload(self, rid, filename, blob, ctype):
        r = await self.http.post("/api/files",
                                 files={"file": (filename, blob, ctype)},
                                 data={"report_id": rid})
        return r

    async def artifacts(self, rid):
        # ★`/api/artifacts/report/{id}`, NOT `/api/reports/{id}/artifacts`.
        # The latter exists only on the PUBLIC share router as `/api/r/{id}/
        # artifacts`; asking for it here 404s, which this used to read as "the
        # chart produced nothing" and report as a product failure. The database
        # said otherwise. Check the route before believing the absence.
        r = await self.http.get(f"/api/artifacts/report/{rid}")
        if r.status_code != 200:
            return []
        b = r.json()
        return b if isinstance(b, list) else b.get("items", [])

    async def widgets(self, rid):
        """Charts are WIDGETS; dashboards and documents are artifacts. A chart
        case that counts artifacts is asking the wrong object for the answer."""
        r = await self.http.get(f"/api/reports/{rid}/widgets")
        if r.status_code != 200:
            return []
        b = r.json()
        return b if isinstance(b, list) else b.get("items", [])


def judge(tier, name, t, *, expect_tools=None, expect_text=None):
    """One turn -> one recorded row. Errors are captured, never raised."""
    ok, why = t.healthy()
    if not ok:
        record(tier, name, "FAIL", why)
        return False
    extra = []
    if expect_tools:
        used = " ".join(t.tools()).lower() + " " + " ".join(t.titles).lower()
        hit = [x for x in expect_tools if x.lower() in used]
        extra.append(f"tools={hit or 'NONE of ' + str(expect_tools)}")
    if expect_text:
        low = t.text.lower()
        miss = [w for w in expect_text if w.lower() not in low]
        if miss:
            extra.append(f"missing {miss}")
    extra.append(f"{len(t.steps)} step(s)")
    record(tier, name, "PASS", "; ".join(extra) + f"; said {t.text[:70]!r}")
    return True


# ────────────────────────────────────────────────────────────────────────────
# Tiers
# ────────────────────────────────────────────────────────────────────────────

CITY = "City Mart Retail"
FABRIC = "Microsoft Fabric"
PBI = "Power BI"


async def T1(c):
    """Plain answer, no tools."""
    rid = await c.report("T1 warm-up", [CTX.agents[CITY]])
    t = await c.ask(rid, "Reply with exactly: OK. Nothing else.")
    judge(1, "plain answer, no tools", t)


async def T2(c):
    """Schema reaches the prompt — asked of each agent separately.

    ★Per agent on purpose. Fabric and Power BI resolve their tables through the
    PER-USER overlay, a different code path from City Mart's canonical tables.
    One agent answering proves nothing about the others.
    """
    for name in (CITY, FABRIC, PBI):
        aid = CTX.agents.get(name)
        if not aid:
            record(2, f"schema — {name}", "SKIP", "agent not present")
            continue
        rid = await c.report(f"T2 schema {name}", [aid])
        try:
            t = await c.ask(rid, "List the tables you can see. Names only.", label=name)
            judge(2, f"schema — {name}", t)
        except Exception as e:
            record(2, f"schema — {name}", "FAIL", f"{type(e).__name__}: {e}")


async def T3(c):
    """One aggregate against the real warehouse."""
    rid = await c.report("T3 one query", [CTX.agents[CITY]])
    t = await c.ask(rid, "How many rows are in the sales table? Just the number.")
    judge(3, "single aggregate", t)


async def T4(c):
    """A chart. The widget is the deliverable, not the sentence."""
    rid = await c.report("T4 chart", [CTX.agents[CITY]])
    t = await c.ask(rid, "Show total revenue by category as a bar chart.")
    if judge(4, "bar chart", t, expect_tools=["create_data", "create_artifact"]):
        w = await c.widgets(rid)
        record(4, "chart produced a widget", "PASS" if w else "FAIL",
               f"{len(w)} widget(s)")


async def T5(c):
    """Multi-step: filter, group, rank, then draw."""
    rid = await c.report("T5 multi-step", [CTX.agents[CITY]])
    t = await c.ask(
        rid,
        "Find the top 5 product categories by revenue, work out each one's share "
        "of the total, and chart the result.",
    )
    judge(5, "multi-step then chart", t, expect_tools=["create_data"])


async def T6(c):
    """A dashboard — several widgets on one canvas."""
    rid = await c.report("T6 dashboard", [CTX.agents[CITY]])
    t = await c.ask(
        rid,
        "Build me a dashboard about sales: a total-revenue KPI, revenue by "
        "category, and a trend over time.",
    )
    if judge(6, "dashboard build", t):
        arts = await c.artifacts(rid)
        w = await c.widgets(rid)
        record(6, "dashboard produced something", "PASS" if (arts or w) else "FAIL",
               f"{len(arts)} artifact(s), {len(w)} widget(s)")
        r = await c.http.get(f"/api/reports/{rid}/layouts")
        record(6, "layout endpoint answers", "PASS" if r.status_code == 200 else "FAIL",
               f"{r.status_code}")


async def T7(c):
    """Files. One case per format, because each takes a different reader.

    ★CSV is loaded as data; docx/pdf go through document text extraction; an
    image goes to vision. A single "files work" case would pass on the easiest
    of the three and tell you nothing about the other two.
    """
    rid = await c.report("T7 files", [CTX.agents[CITY]])

    csv_blob = b"region,units,revenue\nNorth,120,4400\nSouth,80,2600\nEast,150,5100\n"
    r = await c.upload(rid, "apimatrix.csv", csv_blob, "text/csv")
    if r.status_code in (200, 201):
        t = await c.ask(rid, "In the CSV I just attached, which region has the "
                             "highest revenue? Name it.", label="csv")
        judge(7, "CSV attachment read", t, expect_text=["east"])
    else:
        record(7, "CSV attachment read", "SKIP", f"upload {r.status_code} {r.text[:120]}")

    # docx — a REAL Word file from the instance's own uploads, not a renamed
    # .txt. A renamed file only ever measures the extension check.
    docx = CTX.files.get("docx")
    if docx:
        fn, path = docx
        try:
            blob = open(path, "rb").read()
            rid2 = await c.report("T7 docx", [CTX.agents[CITY]])
            r = await c.upload(rid2, fn, blob, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            if r.status_code in (200, 201):
                t = await c.ask(rid2, "Summarise the document I attached in two sentences.", label="docx")
                judge(7, "DOCX text extraction", t)
            else:
                record(7, "DOCX text extraction", "SKIP", f"upload {r.status_code}")
        except Exception as e:
            record(7, "DOCX text extraction", "FAIL", f"{type(e).__name__}: {e}")
    else:
        record(7, "DOCX text extraction", "SKIP", "no .docx on the instance")

    xlsx = CTX.files.get("xlsx")
    if xlsx:
        fn, path = xlsx
        try:
            blob = open(path, "rb").read()
            rid3 = await c.report("T7 xlsx", [CTX.agents[CITY]])
            r = await c.upload(rid3, fn, blob, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            if r.status_code in (200, 201):
                t = await c.ask(rid3, "What is in the spreadsheet I attached? "
                                      "Describe its columns.", label="xlsx")
                judge(7, "XLSX read", t)
            else:
                record(7, "XLSX read", "SKIP", f"upload {r.status_code}")
        except Exception as e:
            record(7, "XLSX read", "FAIL", f"{type(e).__name__}: {e}")
    else:
        record(7, "XLSX read", "SKIP", "no .xlsx on the instance")

    # image -> vision. 0.0.521's fix; the default model is served through an
    # OpenAI-shaped client, the path that used to drop images SILENTLY.
    rid4 = await c.report("T7 image", [CTX.agents[CITY]])
    r = await c.upload(rid4, "reddot.png", RED_DOT_PNG, "image/png")
    if r.status_code in (200, 201):
        t = await c.ask(rid4, "What colour is the image I attached? One word.", label="png")
        ok, why = t.healthy()
        blind = [p for p in ("couldn't see", "could not see", "cannot see", "can't see",
                             "no image", "didn't receive", "unable to view")
                 if p in t.text.lower()]
        if not ok:
            record(7, "image reaches vision", "FAIL", why)
        elif blind:
            record(7, "image reaches vision", "FAIL",
                   f"★0.0.521 regression — image dropped: {t.text[:120]!r}")
        else:
            record(7, "image reaches vision", "PASS", f"said {t.text[:60]!r}")
    else:
        record(7, "image reaches vision", "SKIP", f"upload {r.status_code}")


async def T8(c):
    """Folders — instruction folders (0.0.520) reached from a conversation."""
    r = await c.http.get("/api/instructions/directories")
    if r.status_code != 200:
        record(8, "instruction folders listed", "FAIL", f"{r.status_code} {r.text[:120]}")
        return
    dirs = r.json()
    dirs = dirs if isinstance(dirs, list) else dirs.get("items", [])
    record(8, "instruction folders listed", "PASS", f"{len(dirs)} folder(s)")

    rid = await c.report("T8 knowledge", [CTX.agents[CITY]])
    t = await c.ask(rid, "What instructions or business rules are you working under? "
                         "List their titles.")
    judge(8, "instructions reach the conversation", t)

    r = await c.http.get("/api/local_runtimes")
    record(8, "local folder runtime endpoint", "PASS" if r.status_code in (200, 404) else "FAIL",
           f"{r.status_code}")


async def T9(c):
    """Fabric — 126 tables across 4 lakehouses in 3 workspaces.

    ★The per-user overlay path. City Mart proves the canonical path only; this
    is the one where a wrong `active_only` guard silently reports zero tables,
    and where a missing sign-in is NOT a product bug.
    """
    aid = CTX.agents.get(FABRIC)
    if not aid:
        record(9, "Fabric", "SKIP", "agent not present")
        return
    rid = await c.report("T9 fabric", [aid])
    try:
        t = await c.ask(rid, "How many tables can you see, and which lakehouses "
                             "do they come from?", label="discover")
        judge(9, "Fabric sees its tables", t)
    except Exception as e:
        record(9, "Fabric sees its tables", "FAIL", f"{type(e).__name__}: {e}")
        return
    try:
        t = await c.ask(rid, "Pick one table with sales data and tell me how many "
                             "rows it has.", label="query")
        ok, why = t.healthy()
        if not ok and ("sign in" in t.text.lower() or "connect" in t.text.lower()):
            record(9, "Fabric real query", "SKIP", "needs a per-user sign-in")
        else:
            judge(9, "Fabric real query", t)
    except Exception as e:
        record(9, "Fabric real query", "FAIL", f"{type(e).__name__}: {e}")


async def T10(c):
    """Insights — nobody named a table or a metric."""
    rid = await c.report("T10 insights", [CTX.agents[CITY]])
    t = await c.ask(rid, "Look at this data and tell me the three most interesting "
                         "things about it. Be specific and use real numbers.")
    ok = judge(10, "unprompted insight", t)
    if ok:
        import re
        nums = re.findall(r"\d[\d,\.]{2,}", t.text)
        # ★An "insight" with no figure in it is a paragraph of adjectives. Not
        # a hard failure — the model may legitimately describe shape rather
        # than magnitude — but it is the thing worth seeing in the output.
        record(10, "insight cites real numbers", "PASS" if nums else "FAIL",
               f"{len(nums)} figure(s): {nums[:6]}")


async def T11(c):
    """Two agents in one question — the 0.0.520 mention resolver."""
    ids = [CTX.agents[n] for n in (CITY, FABRIC) if CTX.agents.get(n)]
    if len(ids) < 2:
        record(11, "cross-agent question", "SKIP", "need two agents")
        return
    rid = await c.report("T11 cross-agent", ids)
    t = await c.ask(rid, "You have more than one data source attached. Name each one "
                         "and say what kind of data it holds.")
    judge(11, "cross-agent question", t)

    r = await c.http.get("/api/mentions/available")
    record(11, "mentions endpoint", "PASS" if r.status_code == 200 else "FAIL",
           f"{r.status_code}")


async def T12(c):
    """What must NOT happen. A suite that never asks an impossible question
    passes happily on a build that hallucinates confidently."""
    rid = await c.report("T12 refusals", [CTX.agents[CITY]])

    t = await c.ask(rid, "How many rows are in the table called "
                         "zzz_does_not_exist_9931?", label="missing table")
    ok, why = t.healthy()
    # ★Apostrophes normalised first. The model writes a CURLY one ("isn’t"),
    # so a list spelled with the straight ASCII "isn't" never matches and a
    # correct refusal is recorded as a hallucination — which is a far more
    # alarming thing to report than the harmless phrasing difference it is.
    low = t.text.lower().replace("’", "'")
    said_no = any(p in low for p in
                  ("not exist", "no such", "cannot find", "couldn't find", "not found",
                   "isn't a table", "is not a table", "unable to find", "don't have",
                   # ★And the phrasing this model actually uses. The refusal
                   # does not have to name the table as "nonexistent" — saying
                   # it is not among the connected data is the same answer.
                   "isn't in the connected", "is not in the connected",
                   "isn't in the data", "is not in the data",
                   "no table", "not among", "not available in"))
    if not ok:
        record(12, "missing table refused cleanly", "FAIL", f"turn broke: {why}")
    elif said_no:
        record(12, "missing table refused cleanly", "PASS", f"said {t.text[:90]!r}")
    else:
        record(12, "missing table refused cleanly", "FAIL",
               f"★invented an answer for a table that does not exist: {t.text[:120]!r}")

    t = await c.ask(rid, "??? %%% ...", label="malformed")
    ok, why = t.healthy()
    record(12, "malformed prompt does not 500", "PASS" if ok else "FAIL",
           why if ok else f"turn broke: {why}")


TIERS = [
    (1, "plain answer", T1),
    (2, "schema per agent", T2),
    (3, "single query", T3),
    (4, "chart", T4),
    (5, "multi-step + chart", T5),
    (6, "dashboard", T6),
    (7, "files: csv, docx, xlsx, image", T7),
    (8, "folders and instructions", T8),
    (9, "Fabric across lakehouses", T9),
    (10, "insights", T10),
    (11, "cross-agent mentions", T11),
    (12, "refusals and malformed input", T12),
]


async def setup():
    async with async_session_maker() as db:
        u = (await db.execute(select(User).where(User.email == EMAIL))).scalar_one()
        token = await get_jwt_strategy().write_token(u)
        org = (await db.execute(text(
            "select o.id from organizations o join memberships m on m.organization_id=o.id "
            "where m.user_id=:u order by o.created_at limit 1"), {"u": str(u.id)})).scalar_one()
        CTX.model = str((await db.execute(text(
            "select id from llm_models where is_enabled = true order by is_default desc limit 1"
        ))).scalar_one())
        for name in (CITY, FABRIC, PBI):
            got = (await db.execute(text(
                "select id from data_sources where name=:n and deleted_at is null limit 1"
            ), {"n": name})).scalar_one_or_none()
            if got:
                CTX.agents[name] = str(got)
        for ext in ("docx", "xlsx"):
            row = (await db.execute(text(
                "select filename, path from files where deleted_at is null "
                "and filename like :p order by created_at desc limit 1"
            ), {"p": f"%.{ext}"})).first()
            if row:
                p = row[1] if os.path.isabs(row[1]) else os.path.join("/app/backend", row[1])
                if os.path.exists(p):
                    CTX.files[ext] = (row[0], p)
    CTX.org = str(org)
    return token


async def run(which):
    token = await setup()
    headers = {"Authorization": f"Bearer {token}", "X-Organization-Id": CTX.org}
    print(f"\norg    {CTX.org}\nmodel  {CTX.model}")
    for k, v in CTX.agents.items():
        print(f"agent  {k} ({v})")
    for k, (fn, p) in CTX.files.items():
        print(f"file   {k}: {fn}")
    print()

    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=180) as http:
        c = Client(http)
        for num, title, fn in TIERS:
            if which and num not in which:
                continue
            print(f"\n── T{num} · {title} " + "─" * max(0, 44 - len(title)), flush=True)
            t0 = time.time()
            try:
                await fn(c)
            except Exception as e:  # noqa: BLE001 — one tier must not end the run
                record(num, f"T{num} crashed", "FAIL", f"{type(e).__name__}: {e}")
            print(f"  ({round(time.time()-t0,1)}s)", flush=True)

    print("\n" + "=" * 76)
    for tier, name, verdict, detail in RESULTS:
        print(f"T{tier:<3} {verdict:4}  {name}")
    p = sum(1 for r in RESULTS if r[2] == "PASS")
    f = [r for r in RESULTS if r[2] == "FAIL"]
    s = sum(1 for r in RESULTS if r[2] == "SKIP")
    print(f"\n{p} passed, {len(f)} failed, {s} skipped, {len(RESULTS)} checks")
    if f:
        print("\nfailures / captured errors:")
        for tier, name, _, detail in f:
            print(f"  T{tier} {name}\n      {detail}")
    print("=" * 76)
    return 1 if f else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", action="append", type=int)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    if not a.tier and not a.all:
        raise SystemExit("pick --tier N (repeatable) or --all")
    raise SystemExit(asyncio.run(run(set(a.tier or []))))
