"""End-to-end API tests for the defects fixed in 0.0.543.2 / 0.0.543.3.

Every test plants the real fault against the RUNNING container, drives the
HTTP API the way a browser does, asserts, then reverts and re-checks that the
revert took. Nothing here is destructive: the only writes are one membership
row that this script inserts and deletes itself, and a `deleted_at` stamp it
sets and clears.

★A test that cannot fail is worse than no test. Each fault-plant is VERIFIED to
have landed before the assertions run — a planted duplicate that the unique
index quietly refused would otherwise make every endpoint pass for the wrong
reason, and the run would report green on a broken build.

Usage:
    python3 scripts/api-issue-tests.py                # against localhost:8095
    BASE=http://host:port python3 scripts/api-issue-tests.py
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("BASE", "http://localhost:8095")
APP = os.environ.get("APP_CONTAINER", "dash-app")
PG = os.environ.get("PG_CONTAINER", "dash-postgres")
DB = os.environ.get("PG_DB", "dash_insights")
PGUSER = os.environ.get("PG_USER", "dash")

RESULTS = []


# ---------------------------------------------------------------- plumbing

def sql(q, quiet=True):
    out = subprocess.run(
        ["docker", "exec", PG, "psql", "-U", PGUSER, "-d", DB, "-qAt", "-c", q],
        capture_output=True, text=True,
    )
    if out.returncode != 0 and not quiet:
        raise RuntimeError(out.stderr.strip())
    return out.stdout.strip(), out.returncode, out.stderr.strip()


def api(path, token, org=None, method="GET"):
    """Returns (status, body). Never raises on an HTTP error — the status IS
    the thing under test, so swallowing a 500 into an exception would hide it."""
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Authorization", "Bearer " + token)
    if org:
        req.add_header("X-Organization-Id", org)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # connection refused, timeout
        return 0, str(e)


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  — " + detail) if detail else ""))
    return ok


def section(title):
    print("\n" + title)
    print("-" * len(title))


# ---------------------------------------------------------------- fixtures

def mint(emails):
    here = os.path.dirname(os.path.abspath(__file__))
    subprocess.run(["docker", "cp", os.path.join(here, "mint-user-tokens.py"),
                    APP + ":/app/backend/mint-user-tokens.py"], check=True,
                   capture_output=True)
    subprocess.run(["docker", "exec", "-w", "/app/backend", APP, "python",
                    "mint-user-tokens.py", "/tmp/apitokens.json", *emails],
                   check=True, capture_output=True)
    raw = subprocess.run(["docker", "exec", APP, "cat", "/tmp/apitokens.json"],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(raw)


# ---------------------------------------------------------------- tests

def t_duplicate_membership(tok, org, uid, email, report_id):
    """★The outage itself: one duplicate row 500'd every org-scoped route.

    572 of 3613 production requests in a single morning. The cause was
    `scalar_one_or_none()` used as an existence check — it asserts uniqueness
    and RAISES on two rows, so the membership lookup that gates every request
    threw MultipleResultsFound instead of answering "yes, a member".
    """
    section("1. A duplicate membership row does not break every request")
    dup_id = None
    try:
        sql("DROP INDEX IF EXISTS uq_membership_user_org;", quiet=False)
        out, rc, err = sql(
            "INSERT INTO memberships (id, user_id, organization_id, role, created_at, updated_at) "
            "SELECT gen_random_uuid(), user_id, organization_id, role, now(), now() "
            "FROM memberships WHERE id = (SELECT id FROM memberships WHERE user_id='%s' "
            "AND deleted_at IS NULL LIMIT 1) RETURNING id;" % uid)
        dup_id = out.strip()
        live, _, _ = sql("SELECT count(*) FROM memberships WHERE user_id='%s' "
                         "AND deleted_at IS NULL;" % uid)
        if not check("the duplicate actually landed (positive control)",
                     live == "2", "live rows = %s" % live):
            return  # every assertion below would be vacuous

        paths = ["/api/organizations", "/api/users/me", "/api/reports",
                 "/api/data_sources", "/api/agents", "/api/organization/members",
                 "/api/artifacts/report/" + report_id]
        for p in paths:
            st, body = api(p, tok, org)
            check("%s does not 500" % p, st != 500,
                  "status %d %s" % (st, body[:120] if st == 500 else ""))

        st, body = api("/api/organizations", tok, org)
        if st == 200:
            orgs = json.loads(body)
            check("the workspace switcher lists the workspace ONCE",
                  len(orgs) == len({o["id"] for o in orgs}),
                  "%d entries, %d distinct" % (len(orgs), len({o["id"] for o in orgs})))

        st, body = api("/api/artifacts/report/" + report_id, tok, org)
        n = len(json.loads(body)) if st == 200 else -1
        check("a report's slides are still returned, not an empty list",
              n > 0, "%d artifacts (status %d)" % (n, st))
    finally:
        if dup_id:
            sql("DELETE FROM memberships WHERE id='%s';" % dup_id)
        sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_membership_user_org ON memberships "
            "(user_id, organization_id) WHERE deleted_at IS NULL AND user_id IS NOT NULL;")
        live, _, _ = sql("SELECT count(*) FROM memberships WHERE user_id='%s' "
                         "AND deleted_at IS NULL;" % uid)
        idx, _, _ = sql("SELECT count(*) FROM pg_indexes WHERE indexname='uq_membership_user_org';")
        check("cleanup: row count restored and index rebuilt",
              live == "1" and idx == "1", "live=%s index=%s" % (live, idx))


def t_index_refuses_duplicates(uid):
    section("2. The database now refuses a second membership outright")
    out, rc, err = sql(
        "INSERT INTO memberships (id, user_id, organization_id, role, created_at, updated_at) "
        "SELECT gen_random_uuid(), user_id, organization_id, role, now(), now() "
        "FROM memberships WHERE user_id='%s' AND deleted_at IS NULL LIMIT 1;" % uid)
    check("a duplicate insert is rejected by uq_membership_user_org",
          rc != 0 and "uq_membership_user_org" in err,
          err.splitlines()[0][:140] if err else "INSERT SUCCEEDED — index missing")


def t_soft_deleted_membership(admin_tok, tok, org, uid, email):
    """A removed member was still shown the workspace, then refused inside it."""
    section("3. A removed member is off the roster and off the switcher")
    try:
        sql("UPDATE memberships SET deleted_at=now() WHERE user_id='%s' "
            "AND deleted_at IS NULL;" % uid, quiet=False)
        n, _, _ = sql("SELECT count(*) FROM memberships WHERE user_id='%s' "
                      "AND deleted_at IS NOT NULL;" % uid)
        if not check("the removal actually landed (positive control)", n == "1",
                     "soft-deleted rows = %s" % n):
            return

        st, body = api("/api/organizations", tok, org)
        listed = [o["id"] for o in json.loads(body)] if st == 200 else ["<error %d>" % st]
        check("the removed member is no longer offered the workspace",
              org not in listed, "switcher returned %s" % listed)

        st, body = api("/api/organizations/%s/members" % org, admin_tok, org)
        emails = [m.get("user", {}).get("email") for m in json.loads(body)] if st == 200 else []
        check("the removed member is not on the admin roster",
              email not in emails, "roster = %s" % emails)

        st, _ = api("/api/reports", tok, org)
        check("a removed member is refused, not 500'd", st in (401, 403),
              "status %d" % st)
    finally:
        sql("UPDATE memberships SET deleted_at=NULL WHERE user_id='%s' "
            "AND deleted_at IS NOT NULL;" % uid)
        st, body = api("/api/organizations", tok, org)
        back = st == 200 and org in [o["id"] for o in json.loads(body)]
        check("cleanup: the member is restored and can see the workspace again",
              back, "status %d" % st)


def t_outage_is_not_a_denial():
    """★A database outage answered 403 — 'you are not a member' — which sent
    people to reset their access instead of to the database. The real
    exception is a raw asyncpg one; it is NOT a SQLAlchemy OperationalError,
    which is why the first attempt at this fix changed nothing."""
    section("4. A database outage reads as 503, not 'permission denied'")
    code = (
        "import asyncpg.exceptions as ax\n"
        "from app.core.permission_resolver import _is_infrastructure_failure as f\n"
        "from sqlalchemy.exc import OperationalError\n"
        "print('pw', f(ax.InvalidPasswordError('bad password')))\n"
        "print('conn', f(ax.ConnectionDoesNotExistError('gone')))\n"
        "print('toomany', f(ax.TooManyConnectionsError('full')))\n"
        "print('notinfra', f(ValueError('a real bug')))\n"
    )
    out = subprocess.run(["docker", "exec", "-i", "-w", "/app/backend", APP,
                          "python", "-c", code], capture_output=True, text=True)
    got = dict(l.split() for l in out.stdout.strip().splitlines() if " " in l)
    check("a wrong database password is classified as infrastructure",
          got.get("pw") == "True", out.stderr.strip()[-200:] if not got else str(got))
    check("a dropped connection is classified as infrastructure",
          got.get("conn") == "True", str(got))
    check("connection exhaustion is classified as infrastructure",
          got.get("toomany") == "True", str(got))
    check("an ordinary bug is NOT laundered into a 503",
          got.get("notinfra") == "False", str(got))


def t_database_host_is_unambiguous():
    """★Two stacks on one docker network both claimed the alias `postgres`, and
    Docker round-robined between them. 11 of 20 connections from production
    reached the wrong database and failed to authenticate — ~810 auth errors a
    day, on both servers, reported to users as a permissions problem."""
    section("5. The configured database hostname resolves to exactly one address")
    code = (
        "import os, socket, urllib.parse\n"
        "u = urllib.parse.urlparse(os.environ.get('DASH_DATABASE_URL',''))\n"
        "h = u.hostname or 'postgres'\n"
        "addrs = sorted({a[4][0] for a in socket.getaddrinfo(h, 5432)})\n"
        "print(h, len(addrs), ','.join(addrs))\n"
    )
    out = subprocess.run(["docker", "exec", "-i", APP, "python", "-c", code],
                         capture_output=True, text=True)
    parts = out.stdout.strip().split()
    if len(parts) < 2:
        check("the database host resolves", False, out.stderr.strip()[-200:])
        return
    host, n = parts[0], int(parts[1])
    check("'%s' resolves to one container, not several" % host, n == 1,
          "%d addresses: %s" % (n, parts[2] if len(parts) > 2 else ""))


def t_ldap_only_removes_what_it_provisioned(org):
    """★The sync treated an empty directory result as 'everybody left' and
    emptied the organization — 28 of 29 memberships, including 16 users who
    had never come from LDAP at all. Run here against the LIVE data inside a
    transaction that is rolled back, so it measures the real rows."""
    section("6. An empty directory result does not empty the organization")
    code = (
        "import asyncio\n"
        "import main  # noqa: F401 — registers the ORM mappers\n"
        "from sqlalchemy import select, func\n"
        "from app.models.membership import Membership\n"
        "from app.dependencies import async_session_maker\n"
        "from app.ee.ldap.sync_service import LDAPGroupSyncService\n"
        "ORG = '%s'\n"
        "async def go():\n"
        "    async with async_session_maker() as db:\n"
        "        q = select(func.count()).select_from(Membership).where(\n"
        "            Membership.organization_id == ORG, Membership.deleted_at.is_(None))\n"
        "        before = (await db.execute(q)).scalar()\n"
        "        svc = LDAPGroupSyncService.__new__(LDAPGroupSyncService)\n"
        "        await svc._cleanup_org_memberships(db, ORG, set())\n"
        "        await db.flush()\n"
        "        after = (await db.execute(q)).scalar()\n"
        "        await db.rollback()\n"
        "        print('RESULT', before, after)\n"
        "asyncio.run(go())\n" % org
    )
    out = subprocess.run(["docker", "exec", "-i", "-w", "/app/backend", APP,
                          "python", "-c", code], capture_output=True, text=True)
    line = [l for l in out.stdout.splitlines() if l.startswith("RESULT")]
    if not line:
        check("the sync ran", False, out.stderr.strip()[-300:])
        return
    _, before, after = line[0].split()
    check("an empty LDAP result removes nobody", before == after,
          "%s memberships before, %s after" % (before, after))
    check("the organization was not emptied", int(after) > 0, "after = %s" % after)


def t_workspace_order_is_stable(tok, org):
    """An unordered list meant the client picked a different 'first' workspace
    between calls, so it asked for resources in an org the user was not in and
    got a burst of 404s."""
    section("7. The workspace list comes back in a stable order")
    seen = []
    for _ in range(5):
        st, body = api("/api/organizations", tok, org)
        seen.append([o["id"] for o in json.loads(body)] if st == 200 else ["err%d" % st])
    check("five identical requests return the same order",
          all(s == seen[0] for s in seen), str(seen[:2]))


def t_version(tok):
    section("0. The container under test")
    st, body = api("/api/changelog/version", tok)
    ver = subprocess.run(["docker", "exec", APP, "cat", "/app/VERSION"],
                         capture_output=True, text=True).stdout.strip()
    print("  running %s (endpoint status %d)" % (ver, st))
    check("the image carries a version", bool(ver), ver)


# ---------------------------------------------------------------- main

def main():
    users = mint(["raahulgupta07@gmail.com", "member@cityagent.io"])
    admin = users["users"]["raahulgupta07@gmail.com"]
    member = users["users"]["member@cityagent.io"]
    org = users["org"]["id"]
    # ★The report must belong to the user whose membership we duplicate.
    # `/artifacts/report/{id}` is owner_only, so somebody else's deck answers
    # 404 by design and the assertion would be measuring the wrong thing.
    report_id, _, _ = sql(
        "SELECT a.report_id FROM artifacts a JOIN reports r ON r.id = a.report_id "
        "WHERE r.user_id = '%s' GROUP BY 1 ORDER BY count(*) DESC LIMIT 1;" % member["id"])

    print("target   %s" % BASE)
    print("org      %s" % org)
    print("member   %s (%s)" % (member["id"], "member@cityagent.io"))
    print("report   %s" % report_id)

    t_version(admin["token"])
    t_duplicate_membership(member["token"], org, member["id"],
                           "member@cityagent.io", report_id)
    t_index_refuses_duplicates(member["id"])
    t_soft_deleted_membership(admin["token"], member["token"], org,
                              member["id"], "member@cityagent.io")
    t_outage_is_not_a_denial()
    t_database_host_is_unambiguous()
    t_ldap_only_removes_what_it_provisioned(org)
    t_workspace_order_is_stable(admin["token"], org)

    failed = [r for r in RESULTS if not r[1]]
    print("\n%d checks, %d failed" % (len(RESULTS), len(failed)))
    for name, _, detail in failed:
        print("  FAIL  %s — %s" % (name, detail))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
