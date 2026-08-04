"""Ask the product a real question through the chat API and check the turn works.

★Run this before a release. It is the only check that exercises the agent loop
end to end — reports, completions, the model, and (if the question needs one) a
generated query against a real connector.

    docker cp scripts/chat-round-trip.py dash-app:/app/backend/
    docker exec -w /app/backend dash-app python chat-round-trip.py

★NOT part of any test suite, deliberately. It spends real money on a third-party
model and takes minutes; a 65-second release gate is no place for either. Nor
does it assert that the ANSWER is right — that is a model-quality question and
would make it flake. It asserts the turn completes, says something, and does not
500. A wrong number is reported, not failed on.

★★★And it would NOT have caught the 0.0.518.1 outage. That bug was in
`DataSourceSelector.vue`; the backend was never involved. The browser half lives
in `frontend/tests/chat/`. Neither substitutes for the other.
"""
import asyncio
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

# ★A local CSV/DuckDB agent on purpose. The Power BI and Fabric connections here
# are `user_required` per-user sign-in — a failure against those means an expired
# token, and reads as a broken product when it is nothing of the kind.
AGENT_NAME = os.environ.get("CHAT_AGENT", "City Mart Retail")

QUESTION = os.environ.get(
    "CHAT_QUESTION",
    "How many rows are in the sales table? Answer with just the number.",
)
DEADLINE_S = int(os.environ.get("CHAT_DEADLINE", "300"))


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
            "select id from llm_models where is_enabled = true "
            "order by is_default desc limit 1"
        ))).scalar_one()
    if ds_id is None:
        raise SystemExit(f"no agent named {AGENT_NAME!r} — set CHAT_AGENT")
    return token, str(org_id), str(ds_id), str(model_id)


def fail(msg):
    print(f"\nCHAT ROUND-TRIP: FAIL — {msg}")
    raise SystemExit(1)


async def run():
    token, org_id, ds_id, model_id = await setup()
    # ★X-Organization-Id is not optional — without it every call 400s
    # `organization.required`. See CLAUDE.md.
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Organization-Id": org_id,
        "Content-Type": "application/json",
    }
    print(f"org   {org_id}\nagent {AGENT_NAME} ({ds_id})\nmodel {model_id}\nq     {QUESTION}\n")

    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=60) as c:
        r = await c.post("/api/reports", json={
            "title": "release check — chat round trip",
            "data_sources": [ds_id],
        })
        if r.status_code != 200:
            fail(f"POST /api/reports -> {r.status_code} {r.text[:300]}")
        report_id = r.json()["id"]
        print(f"report {report_id}")

        # ★background=true, not the SSE stream: a socket that must stay open for
        # minutes turns any network hiccup into a fake product failure.
        t0 = time.time()
        r = await c.post(
            f"/api/reports/{report_id}/completions",
            params={"background": "true"},
            json={"prompt": {"content": QUESTION, "mode": "chat", "model_id": model_id}},
        )
        if r.status_code not in (200, 201):
            fail(f"POST completions -> {r.status_code} {r.text[:300]}")
        print("turn started, waiting…")

        last = None
        while time.time() - t0 < DEADLINE_S:
            await asyncio.sleep(5)
            g = await c.get(f"/api/reports/{report_id}/completions")
            if g.status_code != 200:
                fail(f"GET completions -> {g.status_code} {g.text[:200]}")
            payload = g.json()
            items = payload if isinstance(payload, list) else payload.get("completions", [])
            system = [x for x in items if x.get("role") == "system"]
            if not system:
                continue
            last = system[-1]
            status = last.get("status")
            if status not in ("in_progress", "queued", None, ""):
                break
            print(f"  … {int(time.time()-t0)}s  status={status}")
        else:
            fail(f"no settled system turn within {DEADLINE_S}s (last={last and last.get('status')})")

        elapsed = round(time.time() - t0, 1)
        status = last.get("status")

        # ★The answer lives in `completion_blocks`, NOT in `completion`.
        # This endpoint is the v2 shape: it serves `completion: null` on every
        # row and puts the turn's text in an ordered list of blocks. The DB row
        # still carries the legacy `completion` JSON, so reading the database
        # and reading the API disagree — the first version of this script read
        # `completion.content`, got "", and reported a healthy turn as a failure.
        blocks = last.get("completion_blocks") or []
        body = "\n".join(
            (b.get("content") or "").strip() for b in blocks if (b.get("content") or "").strip()
        )
        print(f"\nstatus  {status}\nelapsed {elapsed}s\nblocks  {len(blocks)}")
        for b in blocks:
            print(f"  [{b.get('block_index')}] {b.get('title')} ({b.get('status')}): "
                  f"{(b.get('content') or '')[:160]}")

        if last.get("sigkill"):
            fail("the turn was sigkilled")
        if status != "success":
            fail(f"turn ended {status!r}, not success")
        if not body.strip():
            fail("the turn succeeded and said nothing — an empty answer is a failure")
        if any(b.get("status") == "error" for b in blocks):
            fail("a block errored inside a turn that reported success")

        # Did it actually touch the data, or just talk? Not fatal — a chat-mode
        # answer can legitimately come from context — but worth printing.
        #
        # ★From the COMPLETION's own `created_steps`, not from
        # `GET /api/reports/{id}`, which does not carry steps and answers 0 for
        # a turn that ran real SQL. That is the same mistake as reading
        # `completion.content` above: a plausible field that is always empty
        # reads as a real measurement. Verified against the database — the turn
        # below generated `SELECT COUNT(*) FROM fact_sales` and executed it.
        made = last.get("created_steps") or []
        print(f"steps   {len(made)}")

    print("\nCHAT ROUND-TRIP: PASS")
    print("★the ANSWER is not asserted — check the number above by eye.")


asyncio.run(run())
