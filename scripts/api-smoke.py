"""Hit the running product's real API and report what actually answers.

    docker cp scripts/api-smoke.py dash-app:/app/backend/
    docker exec -w /app/backend dash-app python api-smoke.py            # read-only
    docker exec -w /app/backend dash-app python api-smoke.py --write    # + round-trips
    docker exec -w /app/backend dash-app python api-smoke.py --group knowledge

★This is NOT a unit test and deliberately does not live in `tests/`. The unit
suite runs against a throwaway sqlite schema with fixtures it created itself;
it can tell you a handler returns 200 for data it invented. This asks the LIVE
Postgres-backed instance, with the real org, the real connections and the real
instructions, whether the thing a person clicks actually answers. Those are
different questions and 0.0.518.1 is the proof: 5,451 unit tests green, product
broken.

★It also does not replace the browser smoke (`frontend/tests/smoke`). This
reaches the API; that reaches the rendered page. A route can serve perfect JSON
into a component that throws.

★Read-only by default. `--write` adds create/update/delete round-trips, and
every one of them cleans up after itself in a `finally` — an aborted run leaves
at most one object named `apismoke-*`, which is greppable on purpose.

★★Deleting is SOFT here, and that is the product's design, not a leaked row.
Measured: an instruction keeps its row and gains `deleted_at`; a report keeps
its row and moves to `status='archived'`. So a `select count(*) … where text
like 'apismoke-%'` over Postgres reports leftovers after a perfectly clean run.
The right assertion is the one below — that the object stops appearing in the
API's own list — because that is what a person actually sees.

★No password is involved: the live admin's password is not known to tooling, so
this mints a JWT directly, exactly like mint-smoke-state.py. It must run from
/app/backend — `import main` is what registers the ORM registry.
"""
import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, "/app/backend")
import main  # noqa: F401  — registers the ORM registry

import httpx
from sqlalchemy import select

from app.core.auth import get_jwt_strategy
from app.dependencies import async_session_maker
from app.models.user import User

BASE = os.environ.get("API_SMOKE_BASE", "http://localhost:3000")
EMAIL = os.environ.get("SMOKE_EMAIL", "raahulgupta07@gmail.com")

# ★Almost every route 400s `organization.required` without this header. A run
# that forgets it reports the whole product as broken. See CLAUDE.md.
ORG_HEADER = "X-Organization-Id"


class Ctx:
    """Ids discovered from the live instance, so later cases can chain off them."""

    def __init__(self):
        self.org = None
        self.data_source = None
        self.report = None
        self.instruction = None
        self.build = None
        self.model = None


CTX = Ctx()
RESULTS = []


def record(group, name, ok, detail):
    RESULTS.append((group, name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name} — {detail}", flush=True)


async def hit(client, method, path, *, expect=(200,), json_body=None, params=None):
    """One request. Returns (ok, status, body, seconds)."""
    t0 = time.time()
    try:
        r = await client.request(method, path, json=json_body, params=params)
    except Exception as exc:  # noqa: BLE001 — a connection error is a result too
        return False, 0, repr(exc), time.time() - t0
    dt = time.time() - t0
    try:
        body = r.json()
    except Exception:  # noqa: BLE001 — some routes serve bytes (docx, png)
        body = r.content[:200]
    return r.status_code in expect, r.status_code, body, dt


def count_of(body):
    """How many things came back — for the one-line detail column."""
    # ★A handler may legitimately answer `null` (console/recent-widgets does
    # when nothing has been built yet). That is a valid 200 body, not an error,
    # and the first version of this crashed the whole console group on it.
    if body is None:
        return "null"
    if isinstance(body, list):
        return f"{len(body)} items"
    if isinstance(body, dict):
        for key in ("items", "data", "results", "instructions", "reports"):
            if isinstance(body.get(key), list):
                return f"{len(body[key])} {key}"
        return f"{len(body)} keys"
    return f"{len(body)} bytes"


# ────────────────────────────────────────────────────────────────────────────
# Groups. Each is (name, coroutine). Paths are the REAL mounted paths, read out
# of the routers — not guessed. A 404 here means the route moved, which is
# itself worth failing on.
# ────────────────────────────────────────────────────────────────────────────


async def g_public(client):
    """Unauthenticated surface — the login page depends on all of it."""
    for name, path, check in [
        ("app boots", "/", None),
        ("public settings feed", "/api/settings", None),
        ("i18n config", "/api/config/i18n", None),
        ("changelog", "/api/changelog", None),
        ("instance branding", "/api/instance/branding", None),
    ]:
        ok, status, body, dt = await hit(client, "GET", path)
        record("public", name, ok, f"{status} in {dt:.2f}s, {count_of(body)}")
        if check:
            check(body)


async def g_identity(client):
    """Who am I, what org, what may I do."""
    ok, status, body, dt = await hit(client, "GET", "/api/users/me")
    record("identity", "current user", ok, f"{status}, {body.get('email') if isinstance(body, dict) else ''}")

    ok, status, body, dt = await hit(client, "GET", "/api/organizations")
    n = len(body) if isinstance(body, list) else 0
    record("identity", "organizations", ok and n > 0, f"{status}, {n} orgs")

    ok, status, body, _ = await hit(client, "GET", "/api/organization/members")
    record("identity", "members", ok, f"{status}, {count_of(body)}")

    ok, status, body, _ = await hit(client, "GET", "/api/organization/groups")
    record("identity", "groups", ok, f"{status}, {count_of(body)}")

    ok, status, body, _ = await hit(client, "GET", "/api/permissions/registry")
    record("identity", "permission registry", ok, f"{status}, {count_of(body)}")

    # People & Identities — our own page, merged-identity view.
    ok, status, body, _ = await hit(
        client, "GET", f"/api/organizations/{CTX.org}/people"
    )
    record("identity", "people (merged identities)", ok, f"{status}, {count_of(body)}")

    ok, status, body, _ = await hit(
        client, "GET", f"/api/organizations/{CTX.org}/roles"
    )
    record("identity", "roles", ok, f"{status}, {count_of(body)}")


async def g_license(client):
    """The fork's permanent enterprise grant. A regression here relocks the UI."""
    ok, status, body, _ = await hit(client, "GET", "/api/license")
    feats = body.get("features") if isinstance(body, dict) else None
    licensed = isinstance(body, dict) and body.get("licensed") is True
    tier = body.get("tier") if isinstance(body, dict) else None
    record(
        "license",
        "permanent enterprise grant",
        ok and licensed and tier == "enterprise",
        f"{status}, licensed={licensed} tier={tier} features={len(feats) if feats else 0}",
    )

    ok, status, body, _ = await hit(client, "GET", "/api/instance/features")
    record("license", "instance features", ok, f"{status}, {count_of(body)}")


async def g_connections(client):
    """Connectors, agents, schemas — the data half of the product."""
    ok, status, body, _ = await hit(client, "GET", "/api/available_data_sources")
    record("connections", "connector catalog (available)", ok, f"{status}, {count_of(body)}")

    ok, status, body, _ = await hit(client, "GET", "/api/connectors/catalog")
    record("connections", "connector catalog (full)", ok, f"{status}, {count_of(body)}")

    ok, status, body, _ = await hit(client, "GET", "/api/connector-toggles")
    record("connections", "connector toggles", ok, f"{status}, {count_of(body)}")

    ok, status, body, _ = await hit(client, "GET", "/api/data_sources")
    n = len(body) if isinstance(body, list) else 0
    if n:
        CTX.data_source = body[0].get("id")
    record("connections", "agents / data sources", ok, f"{status}, {n} agents")

    ok, status, body, _ = await hit(client, "GET", "/api/data_sources/active")
    record("connections", "active agents", ok, f"{status}, {count_of(body)}")

    ok, status, body, _ = await hit(client, "GET", "/api/connections")
    record("connections", "connections", ok, f"{status}, {count_of(body)}")

    if not CTX.data_source:
        record("connections", "per-agent detail", False, "no agent exists — skipped")
        return

    ds = CTX.data_source
    for name, path in [
        ("agent detail", f"/api/data_sources/{ds}"),
        ("agent schema", f"/api/data_sources/{ds}/schema"),
        ("agent full schema", f"/api/data_sources/{ds}/full_schema"),
        ("agent members", f"/api/data_sources/{ds}/members"),
        ("agent connections", f"/api/data_sources/{ds}/connections"),
        ("agent training status", f"/api/data_sources/{ds}/training-status"),
        ("agent learn status", f"/api/data_sources/{ds}/learn-status"),
    ]:
        ok, status, body, dt = await hit(client, "GET", path)
        record("connections", name, ok, f"{status} in {dt:.2f}s, {count_of(body)}")

    # ★404 is a legitimate answer here: a connector that has never run a
    # metadata indexing job has no job row to return. Only a 500 is a defect.
    ok, status, body, dt = await hit(
        client, "GET", f"/api/data_sources/{ds}/metadata_resources", expect=(200, 404)
    )
    record("connections", "agent metadata resources", ok,
           f"{status} in {dt:.2f}s ({'no indexing job yet' if status == 404 else count_of(body)})")

    # ★★★`test_connection` is NOT read-only and is deliberately NOT called here.
    # It is spelled GET, but on a system-creds agent it WRITES `is_active` to
    # reflect the result. Measured 2026-08-04: this suite called it once
    # against whichever agent happened to be first in the list, that agent was
    # a per-user connector with no sign-in, and it was switched off org-wide —
    # it vanished from the Agents page, and only `updated_at` recorded that a
    # "read-only" sweep had done it.
    #
    # ★The service-side guard that should have prevented that was broken and is
    # now fixed (see data_source_service.test_data_source_connection). This
    # stays out anyway: a pass that claims to be read-only must not choose its
    # target by list position and then mutate it. Connectivity belongs in a
    # run that says it writes.
    record("connections", "test_connection", True,
           "deliberately not called — it writes is_active; see the comment")


async def g_knowledge(client):
    """Instructions, folders, review hunks — most of what 0.0.520 changed."""
    ok, status, body, _ = await hit(client, "GET", "/api/instructions")
    items = body if isinstance(body, list) else body.get("items", []) if isinstance(body, dict) else []
    if items:
        CTX.instruction = items[0].get("id")
    record("knowledge", "instructions", ok, f"{status}, {len(items)} instructions")

    for name, path in [
        ("instruction counts", "/api/instructions/counts"),
        ("instruction activity", "/api/instructions/activity"),
        ("categories", "/api/instructions/categories"),
        ("statuses", "/api/instructions/statuses"),
        ("source types", "/api/instructions/source-types"),
        ("labels", "/api/instructions/labels"),
        ("available references", "/api/instructions/available-references"),
        ("pending changes", "/api/instructions/pending-changes"),
        # ★0.0.520: instruction folders. New table + new routes; if the
        # migration did not run this 500s rather than returning [].
        ("directories (0.0.520 folders)", "/api/instructions/directories"),
    ]:
        ok, status, body, dt = await hit(client, "GET", path)
        record("knowledge", name, ok, f"{status} in {dt:.2f}s, {count_of(body)}")

    ok, status, body, _ = await hit(
        client, "GET", "/api/knowledge/search", params={"q": "a"}
    )
    record("knowledge", "knowledge search", ok, f"{status}, {count_of(body)}")

    ok, status, body, _ = await hit(client, "GET", "/api/mentions/available")
    record("knowledge", "mentions available (0.0.520 resolver)", ok, f"{status}, {count_of(body)}")

    if CTX.instruction:
        ins = CTX.instruction
        for name, path in [
            ("instruction detail", f"/api/instructions/{ins}"),
            ("instruction versions", f"/api/instructions/{ins}/versions"),
            # ★0.0.520: per-hunk accept/reject. The route existing is the
            # cheapest proof the port's headline feature actually mounted.
            ("review hunks (0.0.520 diffs)", f"/api/instructions/{ins}/review-hunks"),
            ("pending builds", f"/api/instructions/{ins}/pending-builds"),
            ("resolved evals", f"/api/instructions/{ins}/resolved-evals"),
        ]:
            ok, status, body, dt = await hit(client, "GET", path)
            record("knowledge", name, ok, f"{status} in {dt:.2f}s, {count_of(body)}")


async def g_builds(client):
    """Context builds. 0.0.519's only genuine gap lived here."""
    ok, status, body, _ = await hit(client, "GET", "/api/builds")
    all_builds = body if isinstance(body, list) else []
    record("builds", "builds", ok, f"{status}, {len(all_builds)} builds")

    ok, status, body, dt = await hit(client, "GET", "/api/builds/main")
    if isinstance(body, dict):
        CTX.build = body.get("id")
    record("builds", "main build", ok, f"{status} in {dt:.2f}s, {count_of(body)}")

    if CTX.build:
        # ★0.0.519's fix: an accepted AI suggestion was left at `draft`, so it
        # was invisible to every context loader and shown as "Inactive". This
        # is the endpoint that reveals it — a published instruction whose
        # content is missing here is that bug, back.
        ok, status, body, dt = await hit(
            client, "GET", f"/api/builds/{CTX.build}/contents"
        )
        record("builds", "main build contents (0.0.519 fix)", ok, f"{status} in {dt:.2f}s, {count_of(body)}")

        # ★`compare_to` is REQUIRED — a diff has two sides and the route will
        # not invent the other one. Omitting it 422s, which reads as the
        # endpoint being broken when the caller simply asked a half question.
        other = next((b.get("id") for b in all_builds if b.get("id") != CTX.build), None)
        if other:
            ok, status, body, dt = await hit(
                client, "GET", f"/api/builds/{CTX.build}/diff",
                params={"compare_to": other},
            )
            record("builds", "build diff (two real builds)", ok, f"{status} in {dt:.2f}s, {count_of(body)}")

            ok, status, body, dt = await hit(
                client, "GET", f"/api/builds/{CTX.build}/diff/details",
                params={"compare_to": other},
            )
            record("builds", "build diff details", ok, f"{status} in {dt:.2f}s, {count_of(body)}")
        else:
            record("builds", "build diff (two real builds)", True, "only one build exists — skipped")


async def g_reports(client):
    """Chats, dashboards, artifacts — what the user opens first."""
    ok, status, body, dt = await hit(client, "GET", "/api/reports")
    items = body if isinstance(body, list) else body.get("items", []) if isinstance(body, dict) else []
    if items:
        CTX.report = items[0].get("id")
    record("reports", "reports", ok, f"{status} in {dt:.2f}s, {len(items)} reports")

    # ★`ids` is REQUIRED and comma-separated — this route is the list-badge
    # poller, so it answers about the rows the page is showing, never "all".
    # With no reports at all there is nothing to ask about.
    if items:
        ids = ",".join(str(i.get("id")) for i in items[:20])
        ok, status, body, dt = await hit(
            client, "GET", "/api/reports/activity", params={"ids": ids}
        )
        record("reports", "report activity (live badges)", ok, f"{status} in {dt:.2f}s, {count_of(body)}")
    else:
        record("reports", "report activity (live badges)", True, "no reports exist — skipped")

    for name, path in [
        ("report refreshes", "/api/report-refreshes"),
        ("projects", "/api/projects"),
        ("prompts", "/api/prompts"),
        ("scheduled prompts", "/api/scheduled-prompts"),
        ("notifications", "/api/notifications"),
        ("notification count", "/api/notifications/count"),
        ("review queue", "/api/review"),
        ("review count", "/api/review/count"),
    ]:
        ok, status, body, dt = await hit(client, "GET", path)
        record("reports", name, ok, f"{status} in {dt:.2f}s, {count_of(body)}")

    if CTX.report:
        rid = CTX.report
        for name, path in [
            ("report detail", f"/api/reports/{rid}"),
            ("report summary", f"/api/reports/{rid}/summary"),
            ("report notes", f"/api/reports/{rid}/notes"),
            ("report instructions", f"/api/reports/{rid}/instructions"),
            ("report layouts", f"/api/reports/{rid}/layouts"),
        ]:
            ok, status, body, dt = await hit(client, "GET", path)
            record("reports", name, ok, f"{status} in {dt:.2f}s, {count_of(body)}")


async def g_automation(client):
    """The scheduler half — what runs without anyone clicking."""
    for name, path in [
        ("keeper", "/api/keeper"),
        # ★The tab that says what runs by itself. Its two stale unit tests were
        # this session's first task; this is the live counterpart.
        ("keeper schedule", "/api/keeper/schedule"),
        ("keeper activity", "/api/keeper/activity"),
        ("auto-learn settings", "/api/auto-learn"),
        ("triggers", "/api/triggers"),
        ("webhooks", "/api/webhooks"),
    ]:
        ok, status, body, dt = await hit(client, "GET", path, expect=(200, 404))
        record("automation", name, ok, f"{status} in {dt:.2f}s, {count_of(body)}")


async def g_models(client):
    """LLM providers and models. 0.0.518.3 retired Google ids and added Opus 5."""
    ok, status, body, _ = await hit(client, "GET", "/api/llm/available_providers")
    record("models", "available providers", ok, f"{status}, {count_of(body)}")

    ok, status, body, _ = await hit(client, "GET", "/api/llm/providers")
    record("models", "configured providers", ok, f"{status}, {count_of(body)}")

    ok, status, body, _ = await hit(client, "GET", "/api/llm/models")
    models = body if isinstance(body, list) else []
    default = next((m for m in models if m.get("is_default")), None)
    if default:
        CTX.model = default.get("model_id") or default.get("id")
    record("models", "configured models", ok, f"{status}, {len(models)} models, default={CTX.model}")

    # ★0.0.518.3: retired Google ids were replaced. A live model whose id is a
    # retired one answers 404 from the provider at chat time, not here — so
    # this names them rather than failing, and the check is the catalog's.
    ok, status, body, _ = await hit(client, "GET", "/api/llm/available_models")
    ids = json.dumps(body)
    retired = [m for m in ("gemini-1.5-pro", "gemini-1.5-flash") if m in ids]
    record("models", "no retired Google ids in catalog", ok and not retired,
           f"{status}, retired present: {retired or 'none'}")
    record("models", "Opus 5 in catalog", "claude-opus-5" in ids,
           "claude-opus-5 " + ("present" if "claude-opus-5" in ids else "MISSING"))

    ok, status, body, _ = await hit(client, "GET", "/api/llm/fallback_order")
    record("models", "fallback order", ok, f"{status}, {count_of(body)}")


async def g_console(client):
    """App analytics / observability. Heavy aggregate queries — also a perf read."""
    for name, path in [
        ("app analytics", "/api/console/app-analytics"),
        ("metrics", "/api/console/metrics"),
        ("metrics timeseries", "/api/console/metrics/timeseries"),
        ("top users", "/api/console/metrics/top-users"),
        ("tool usage", "/api/console/metrics/tool-usage"),
        ("llm usage", "/api/console/metrics/llm-usage"),
        ("cost", "/api/console/metrics/cost"),
        ("table usage", "/api/console/metrics/table-usage"),
        ("recent widgets", "/api/console/recent-widgets"),
        ("issues (compact)", "/api/console/issues/compact"),
    ]:
        ok, status, body, dt = await hit(client, "GET", path)
        # ★A console query over a real database is where an N+1 shows up. Slow
        # is reported, not failed — the threshold is a judgement, the number is
        # the evidence.
        slow = " ★SLOW" if dt > 3 else ""
        record("console", name, ok, f"{status} in {dt:.2f}s, {count_of(body)}{slow}")


async def g_enterprise(client):
    """EE surface the fork unlocked permanently — SSO, LDAP, audit, SCIM."""
    for name, path in [
        ("SSO config (redacted)", "/api/enterprise/sso/config"),
        ("LDAP config (redacted)", "/api/enterprise/ldap/config"),
        ("organization settings", "/api/organization_settings"),
        ("usage limits", "/api/usage_limits"),
    ]:
        ok, status, body, dt = await hit(client, "GET", path, expect=(200, 404))
        record("enterprise", name, ok, f"{status} in {dt:.2f}s, {count_of(body)}")

    # ★403 here is the access gate DOING ITS JOB, not a failure. Settings →
    # Access has three states and `require_access` fails closed, so an org that
    # has never switched API keys on refuses at the route as well as hiding the
    # tab — hiding a tab is not access control. Both answers are correct; the
    # only wrong one is a 500. Which state we are in is reported, not asserted.
    ok, status, body, dt = await hit(client, "GET", "/api/api_keys", expect=(200, 403))
    gate = "gated off (403, enforced at the route)" if status == 403 else count_of(body)
    record("enterprise", "api keys / access gate", ok, f"{status} in {dt:.2f}s, {gate}")

    # ★Secrets must never come back. This is the one case here that is a
    # security assertion rather than an availability one: the read shapes
    # expose `*_set: bool`, never the ciphertext and never the plaintext.
    ok, status, body, _ = await hit(client, "GET", "/api/enterprise/sso/config")
    blob = json.dumps(body) if not isinstance(body, bytes) else ""
    leaked = [k for k in ("client_secret", "bind_password", "gAAAAA") if k in blob and f"{k}_set" not in blob]
    record("enterprise", "no secret leaks in SSO config", not leaked,
           f"leaked keys: {leaked or 'none'}")


async def g_write(client):
    """Round-trips that create then delete. Only with --write."""
    stamp = str(int(time.time()))

    # 1. Instruction: create → read → update → delete.
    ins_id = None
    try:
        ok, status, body, _ = await hit(
            client, "POST", "/api/instructions",
            expect=(200, 201),
            json_body={
                "text": f"apismoke-{stamp}: this row is created and deleted by scripts/api-smoke.py.",
                "category": "general",
                "status": "draft",
            },
        )
        ins_id = body.get("id") if isinstance(body, dict) else None
        record("write", "create instruction", ok and bool(ins_id), f"{status}, id={ins_id}")

        if ins_id:
            ok, status, body, _ = await hit(client, "GET", f"/api/instructions/{ins_id}")
            found = isinstance(body, dict) and stamp in json.dumps(body)
            record("write", "read it back", ok and found, f"{status}, text matches: {found}")

            ok, status, body, _ = await hit(
                client, "PUT", f"/api/instructions/{ins_id}",
                json_body={"text": f"apismoke-{stamp}: edited."},
            )
            record("write", "update it", ok, f"{status}")
    finally:
        if ins_id:
            ok, status, _, _ = await hit(
                client, "DELETE", f"/api/instructions/{ins_id}", expect=(200, 204)
            )
            record("write", "delete it (cleanup)", ok, f"{status}")

            # ★The delete is soft, so "it is gone" can only mean "the list no
            # longer serves it". Checking the row instead would fail forever.
            _, _, body, _ = await hit(client, "GET", "/api/instructions")
            gone = stamp not in json.dumps(body if not isinstance(body, bytes) else "")
            record("write", "deleted instruction leaves the list", gone,
                   "absent from GET /instructions" if gone else "STILL LISTED")

    # 2. Instruction folder — 0.0.520's new object. Create → list → delete.
    dir_id = None
    try:
        ok, status, body, _ = await hit(
            client, "POST", "/api/instructions/directories",
            expect=(200, 201),
            json_body={"name": f"apismoke-{stamp}"},
        )
        dir_id = body.get("id") if isinstance(body, dict) else None
        record("write", "create folder (0.0.520)", ok and bool(dir_id), f"{status}, id={dir_id}")

        if dir_id:
            ok, status, body, _ = await hit(client, "GET", "/api/instructions/directories")
            seen = f"apismoke-{stamp}" in json.dumps(body)
            record("write", "folder appears in list", ok and seen, f"{status}, listed: {seen}")
    finally:
        if dir_id:
            ok, status, _, _ = await hit(
                client, "DELETE", f"/api/instructions/directories/{dir_id}", expect=(200, 204)
            )
            record("write", "delete folder (cleanup)", ok, f"{status}")

    # 3. Report: create → read → delete. The object every chat starts as.
    rep_id = None
    try:
        ok, status, body, _ = await hit(
            client, "POST", "/api/reports",
            expect=(200, 201),
            json_body={"title": f"apismoke-{stamp}"},
        )
        rep_id = body.get("id") if isinstance(body, dict) else None
        record("write", "create report", ok and bool(rep_id), f"{status}, id={rep_id}")

        if rep_id:
            ok, status, body, _ = await hit(client, "GET", f"/api/reports/{rep_id}")
            record("write", "read report back", ok, f"{status}")
    finally:
        if rep_id:
            ok, status, _, _ = await hit(
                client, "DELETE", f"/api/reports/{rep_id}", expect=(200, 204)
            )
            record("write", "delete report (cleanup)", ok, f"{status}")

            # ★Same again: the report moves to `status='archived'` rather than
            # leaving the table, and the list is what proves it went away.
            _, _, body, _ = await hit(client, "GET", "/api/reports")
            gone = stamp not in json.dumps(body if not isinstance(body, bytes) else "")
            record("write", "deleted report leaves the list", gone,
                   "absent from GET /reports" if gone else "STILL LISTED")


async def g_negative(client):
    """What must NOT work. An availability suite that never checks a refusal
    will happily pass on a build that authorises everyone."""
    # No org header → the documented 400.
    async with httpx.AsyncClient(
        base_url=BASE, timeout=30,
        headers={"Authorization": client.headers["Authorization"]},
    ) as bare:
        ok, status, body, _ = await hit(bare, "GET", "/api/reports", expect=(400, 401, 403, 422))
        record("negative", "no org header is refused", ok, f"{status}")

    # No token at all.
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as anon:
        ok, status, body, _ = await hit(anon, "GET", "/api/reports", expect=(401, 403))
        record("negative", "anonymous is refused", ok, f"{status}")

        ok, status, body, _ = await hit(anon, "GET", "/api/llm/models", expect=(401, 403))
        record("negative", "anonymous cannot read models", ok, f"{status}")

    # A well-formed but nonexistent id must 404, not 500.
    ok, status, body, _ = await hit(
        client, "GET", "/api/reports/00000000-0000-0000-0000-000000000000",
        expect=(400, 403, 404),
    )
    record("negative", "unknown report 404s (not 500)", ok, f"{status}")

    ok, status, body, _ = await hit(
        client, "GET", "/api/data_sources/00000000-0000-0000-0000-000000000000",
        expect=(400, 403, 404),
    )
    record("negative", "unknown agent 404s (not 500)", ok, f"{status}")


GROUPS = [
    ("public", g_public),
    ("identity", g_identity),
    ("license", g_license),
    ("connections", g_connections),
    ("knowledge", g_knowledge),
    ("builds", g_builds),
    ("reports", g_reports),
    ("automation", g_automation),
    ("models", g_models),
    ("console", g_console),
    ("enterprise", g_enterprise),
    ("negative", g_negative),
    ("write", g_write),  # --write only; skipped otherwise
]


async def mint():
    """A short-lived admin JWT plus the org it belongs to."""
    async with async_session_maker() as db:
        row = await db.execute(select(User).where(User.email == EMAIL))
        user = row.scalar_one_or_none()
        if user is None:
            raise SystemExit(f"no user {EMAIL} — set SMOKE_EMAIL to a real account")
        token = await get_jwt_strategy().write_token(user)

        from app.models.membership import Membership

        row = await db.execute(
            select(Membership).where(Membership.user_id == str(user.id))
        )
        m = row.scalars().first()
        if m is None:
            raise SystemExit(f"{EMAIL} belongs to no organization")
        return token, str(m.organization_id)


async def run(selected, do_write):
    token, org = await mint()
    CTX.org = org
    print(f"\nbase={BASE}  user={EMAIL}  org={org}\n")

    headers = {"Authorization": f"Bearer {token}", ORG_HEADER: org}
    async with httpx.AsyncClient(base_url=BASE, timeout=60, headers=headers) as client:
        for name, fn in GROUPS:
            if selected and name not in selected:
                continue
            if name == "write" and not do_write:
                continue
            print(f"\n── {name} " + "─" * (60 - len(name)))
            try:
                await fn(client)
            except Exception as exc:  # noqa: BLE001 — one bad group must not end the run
                record(name, "group crashed", False, repr(exc))

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[2]]
    print("\n" + "=" * 68)
    print(f"{total - len(failed)} passed, {len(failed)} failed, {total} checks")
    if failed:
        print("\nfailures:")
        for group, name, _, detail in failed:
            print(f"  {group}/{name} — {detail}")
    print("=" * 68)
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", action="append", help="run only these groups (repeatable)")
    ap.add_argument("--write", action="store_true", help="also run create/delete round-trips")
    ap.add_argument("--list", action="store_true", help="print the groups and exit")
    a = ap.parse_args()
    if a.list:
        for n, _ in GROUPS:
            print(n)
        raise SystemExit(0)
    raise SystemExit(asyncio.run(run(set(a.group or []), a.write)))
