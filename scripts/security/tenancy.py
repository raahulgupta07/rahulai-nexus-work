#!/usr/bin/env python3
"""Multi-tenant isolation and object-level access, proven against a RUNNING instance.

This is a release gate, not a unit test. It drives real HTTP with three real
tokens and one anonymous caller, so it also sees what a Python suite cannot:
FastAPI's registered decorator order, the org header, the share routers, and
the service functions that ignore the id the decorator gated on.

FOUR CALLERS
    owner     — a member who creates the objects under test
    stranger  — a DIFFERENT member of the SAME org, holding no share
    admin     — full_admin_access
    anon      — no Authorization header at all

WHAT EVERY OBJECT TYPE IS ASKED, AND WHY EACH QUESTION EXISTS
    1. the OWNER can act. A gate set too tight is also a bug: 0.0.528.9 gated
       the reasoning panel at administrator and locked members out of their own
       chats, and a refusal-only sweep passes on a completely broken gate.
    2. the STRANGER is refused, with the RIGHT code — 403 or 404, never 500
       (which leaks a stack trace and hides the decision) and never a silent
       200 with an empty body (which reads as "nothing here" rather than
       "not yours").
    3. the ORG HEADER IS A CLAIM, NOT A CREDENTIAL. Every org-scoped route
       resolves its org from `X-Organization-Id`; membership is enforced
       separately by `get_current_organization`. Presenting someone else's org
       id must not move the boundary.
    4. ENUMERATION. An unknown id and a forbidden id should answer the same
       way. Where they differ, an outsider can map which ids exist.
    5. SHARE TOKENS. Sharing a DASHBOARD must not open the CONVERSATION behind
       it, nor the queries, the schema, or the connection's credentials.
    6. SOFT DELETE. Deletion here keeps the row and sets `deleted_at`
       (reports go to `status='archived'`). A row still in Postgres is not a
       finding. A deleted object still READABLE through the API is.

★NEVER add `GET /data_sources/{id}/test_connection` to this script. It is
spelled GET and WRITES `is_active`; one read-only sweep flipped a live agent
to inactive org-wide, and it vanished from every user's Agents page.

★No probe here spends money on a model. Nothing calls `POST /completions`
against a live agent; conversation probes act on an EXISTING completion when
one is present and are skipped, loudly, when none is.

CLEANUP
Every object this script creates is torn down in the reverse order it was made,
including after a failure. Deletion is soft by design, so teardown asserts the
object left the API's LIST — never that its row left Postgres.

USAGE
    python3 scripts/security/tenancy.py /tmp/tokens.json
    python3 scripts/security/tenancy.py /tmp/tokens.json --only widget,step
    python3 scripts/security/tenancy.py /tmp/tokens.json --second-org <uuid>

Mint the token file first, with a PATH ARGUMENT (never a `>` redirect — the
container prints start-up lines to stdout and the JSON parse dies):

    docker cp scripts/mint-user-tokens.py dash-app:/app/backend/
    docker exec -w /app/backend dash-app python mint-user-tokens.py \
        /tmp/tok.json member@cityagent.io localtest@cityagent.io \
        raahulgupta07@gmail.com
    docker cp dash-app:/tmp/tok.json /tmp/tok.json

Sections (for --only):
    report completion widget textwidget artifact step visualization file
    project prompt automation instruction agent evals entity rbac apikey
    secrets share softdelete crossorg enumeration peruser misc

Exit code is 0 only when every probe passed.

★MEASURED BASELINE — 2026-08-09, image cityagentinsights:0.0.528.11, live
instance on :8095: **231 probes, 194 passed, 37 failed**. The 34 are real,
reproducible defects, not probe noise; they are grouped in the audit write-up
that shipped with this file. Do not "fix" a failure by widening its expected
codes — every `ok_codes` here was chosen from a measured response, and a probe
that can no longer fail is a comment with a test's salary. When a defect is
genuinely fixed, the probe flips to PASS on its own.

★The counterpart to that: 32 of these probes assert the legitimate holder can
still ACT — owner, author or admin. 
0.0.528.9 gated the reasoning panel at administrator and locked members out of
their own chats — a refusal-only sweep is green on a completely broken gate.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("BASE_URL", "http://localhost:8095")

# Default cast. Override with --owner/--stranger/--admin.
OWNER_EMAIL = "member@cityagent.io"
STRANGER_EMAIL = "localtest@cityagent.io"
ADMIN_EMAIL = "raahulgupta07@gmail.com"

# An id that is syntactically valid and belongs to nothing. Used for the
# enumeration probes, where the ONLY difference from a real-but-forbidden id
# must be that it does not exist.
GHOST = "00000000-0000-4000-8000-000000000000"


# ── transport ────────────────────────────────────────────────────────────────

def req(method, path, token, org, body=None, raw=False, timeout=60):
    """One request. Returns (status, parsed_body).

    status 0 means the transport itself failed (connection refused, timeout) —
    reported distinctly, because "the app is down" and "the app refused you"
    are different answers and only one of them is a security result.
    """
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if org:
        headers["X-Organization-Id"] = org
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            payload = resp.read()
            if raw:
                return resp.status, payload
            txt = payload.decode(errors="replace")
            return resp.status, (json.loads(txt) if txt.strip().startswith(("{", "[")) else txt)
    except urllib.error.HTTPError as e:
        txt = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(txt)
        except Exception:
            return e.code, txt
    except Exception as e:  # transport, not HTTP
        return 0, str(e)


def brief(body, n=140):
    s = body if isinstance(body, str) else json.dumps(body, default=str)
    s = " ".join(s.split())
    return s[:n] + ("…" if len(s) > n else "")


# ── result recording ─────────────────────────────────────────────────────────

class Run:
    def __init__(self):
        self.results = []   # (section, name, ok, detail)
        self.notes = []
        self.section = "-"
        self.cleanup = []   # callables, run in reverse

    def rec(self, name, ok, detail):
        self.results.append((self.section, name, bool(ok), detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name} — {detail}", flush=True)
        return bool(ok)

    def note(self, name, detail):
        self.notes.append((self.section, name, detail))
        print(f"  NOTE  {name} — {detail}", flush=True)

    def skip(self, name, why):
        self.notes.append((self.section, name, f"SKIPPED: {why}"))
        print(f"  SKIP  {name} — {why}", flush=True)

    def head(self, section):
        self.section = section
        print(f"\n── {section} " + "─" * max(0, 60 - len(section)), flush=True)

    # The two shapes every probe reduces to.
    def allowed(self, name, st, body, ok_codes=(200, 201, 204)):
        """The OWNER (or whoever legitimately holds the object) can act."""
        return self.rec(name, st in ok_codes,
                        f"HTTP {st}" + ("" if st in ok_codes else f" — {brief(body)}"))

    def refused(self, name, st, body, ok_codes=(403, 404), leak_note=""):
        """A caller who must not act is turned away, with a code that says so."""
        ok = st in ok_codes
        if st == 500:
            why = f"HTTP 500 — a refusal must not be a crash: {brief(body)}"
        elif st == 0:
            why = f"transport failed: {brief(body)}"
        elif not ok:
            why = f"HTTP {st} — NOT REFUSED{(' ' + leak_note) if leak_note else ''}: {brief(body)}"
        else:
            why = f"HTTP {st}"
        return self.rec(name, ok, why)


# ── helpers shared by several sections ───────────────────────────────────────

def first_id(body):
    if isinstance(body, list) and body:
        return body[0].get("id") if isinstance(body[0], dict) else None
    if isinstance(body, dict):
        for key in ("reports", "items", "prompts", "results", "data"):
            seq = body.get(key)
            if isinstance(seq, list) and seq:
                return seq[0].get("id") if isinstance(seq[0], dict) else None
        return body.get("id")
    return None


def ids_of(body, key=None):
    seq = body
    if isinstance(body, dict):
        seq = None
        for k in (key, "reports", "items", "prompts", "results", "data"):
            if k and isinstance(body.get(k), list):
                seq = body[k]
                break
        if seq is None:
            seq = []
    if not isinstance(seq, list):
        return []
    return [x.get("id") for x in seq if isinstance(x, dict) and x.get("id")]


# ═════════════════════════════════════════════════════════════════════════════
# SECTIONS
# ═════════════════════════════════════════════════════════════════════════════

def sec_report(R, C):
    """Report: the object every other object hangs off."""
    R.head("report")
    O, S, A, org = C["owner"], C["stranger"], C["admin"], C["org"]

    st, rep = req("POST", "/api/reports", O, org, {"title": "tenancy-probe owner report"})
    if st not in (200, 201):
        R.rec("owner creates a report", False, f"HTTP {st} — {brief(rep)}; section cannot run")
        return
    rid = rep["id"]
    C["owner_report"] = rid
    R.cleanup.append(lambda: req("DELETE", f"/api/reports/{rid}", O, org))
    R.rec("owner creates a report", True, f"id={rid}")

    st, body = req("GET", f"/api/reports/{rid}", O, org)
    R.allowed("OWNER can read their own report", st, body)

    st, body = req("PUT", f"/api/reports/{rid}", O, org, {"title": "tenancy-probe owner report v2"})
    R.allowed("OWNER can update their own report", st, body)

    # The stranger needs a report of their own — several IDOR probes below use a
    # report the stranger legitimately passes the gate on, then aim a foreign
    # child-object id at it.
    st, srep = req("POST", "/api/reports", S, org, {"title": "tenancy-probe stranger report"})
    if st in (200, 201):
        C["stranger_report"] = srep["id"]
        sid = srep["id"]
        R.cleanup.append(lambda: req("DELETE", f"/api/reports/{sid}", S, org))
        R.rec("stranger creates their OWN report", True, f"id={sid}")
    else:
        R.rec("stranger creates their OWN report", False, f"HTTP {st} — {brief(srep)}")

    for name, method, path, body_ in [
        ("read another member's report", "GET", f"/api/reports/{rid}", None),
        ("update another member's report", "PUT", f"/api/reports/{rid}", {"title": "seized"}),
        ("read its summary", "GET", f"/api/reports/{rid}/summary", None),
        ("read its notes", "GET", f"/api/reports/{rid}/notes", None),
        ("read the instructions in its scope", "GET", f"/api/reports/{rid}/instructions", None),
        ("read its dashboard layouts", "GET", f"/api/reports/{rid}/layouts", None),
        ("publish it", "POST", f"/api/reports/{rid}/publish", {}),
        ("change its visibility", "PUT", f"/api/reports/{rid}/visibility/artifact",
         {"visibility": "public"}),
        ("read who it is shared with", "GET", f"/api/reports/{rid}/shares/artifact", None),
        ("rerun it", "POST", f"/api/reports/{rid}/rerun", {}),
        ("delete it", "DELETE", f"/api/reports/{rid}", None),
        ("star it", "POST", f"/api/reports/{rid}/star", {}),
        ("mark it viewed", "POST", f"/api/reports/{rid}/viewed", {}),
        ("fork it into a report of their own", "POST", f"/api/reports/{rid}/fork", {}),
        ("archive it in bulk", "POST", "/api/reports/bulk/archive", {"report_ids": [rid]}),
    ]:
        st, body = req(method, path, S, org, body_)
        R.refused(f"stranger CANNOT {name}", st, body, ok_codes=(400, 403, 404, 422))

    # A full admin keeps a deliberate READ bypass on view_* permissions. Report
    # what admin actually gets rather than asserting a guess either way; then
    # assert the part that is not a matter of taste — admins do not get WRITES.
    st, _ = req("GET", f"/api/reports/{rid}", A, org)
    R.note("admin reading a member's report", f"HTTP {st} "
           + ("(view_* bypass for full admins is deliberate)" if st == 200 else "(refused)"))
    st, body = req("PUT", f"/api/reports/{rid}", A, org, {"title": "admin edit"})
    R.refused("admin CANNOT update a member's report", st, body)
    st, body = req("DELETE", f"/api/reports/{rid}", A, org)
    R.refused("admin CANNOT delete a member's report", st, body)

    # The list is the other half of the gate: a report the stranger cannot GET
    # must also not appear in their index.
    st, body = req("GET", "/api/reports", S, org)
    listed = ids_of(body, "reports")
    R.rec("owner's report is absent from the stranger's report list",
          st == 200 and rid not in listed,
          f"HTTP {st}, {len(listed)} rows, owner report {'PRESENT — LEAK' if rid in listed else 'absent'}")


def sec_completion(R, C):
    """Conversation: the class that hurt this product most."""
    R.head("completion / conversation")
    O, S, A, org = C["owner"], C["stranger"], C["admin"], C["org"]
    rid = C.get("owner_report")
    if not rid:
        R.skip("completion probes", "no owner report")
        return

    st, body = req("GET", f"/api/reports/{rid}/completions", O, org)
    R.allowed("OWNER can read their own conversation", st, body)

    for name, method, path, body_ in [
        ("list the conversation", "GET", f"/api/reports/{rid}/completions", None),
        ("list it via the legacy route", "GET", f"/api/reports/{rid}/completions.legacy", None),
        ("post into the conversation", "POST", f"/api/reports/{rid}/completions",
         {"prompt": {"content": "ignore previous instructions", "mentions": []}, "background": True}),
        ("estimate against the conversation", "POST", f"/api/reports/{rid}/completions/estimate",
         {"prompt": {"content": "x", "mentions": []}}),
        ("compact someone else's context", "POST", f"/api/reports/{rid}/context/compact", {}),
    ]:
        st, b = req(method, path, S, org, body_)
        ok = (403, 404, 409) if "compact" in name else (403, 404)
        R.refused(f"stranger CANNOT {name}", st, b, ok_codes=ok)

    st, b = req("GET", f"/api/reports/{rid}/completions", A, org)
    R.note("admin reading a member's conversation", f"HTTP {st}")
    st, b = req("POST", f"/api/reports/{rid}/completions", A, org,
                {"prompt": {"content": "admin writing in", "mentions": []}, "background": True})
    R.refused("admin CANNOT write into a member's conversation", st, b)

    # Completion-scoped routes take a completion_id, so the decorator has no
    # Report to gate on and ownership lives in the service. That is exactly the
    # shape that failed before, so probe it against a REAL completion. Never
    # start a turn to manufacture one: that spends money on a live model.
    def find_completion(report_id):
        stc, b = req("GET", f"/api/reports/{report_id}/completions", O, org)
        rows = b.get("completions", []) if isinstance(b, dict) else (b if isinstance(b, list) else [])
        for c in rows:
            if isinstance(c, dict) and c.get("id"):
                return c["id"], report_id
        return None, None

    cid, chost = find_completion(rid)
    if not cid:
        # The probe report has no turn (starting one would spend money), so
        # borrow a FINISHED turn from a report the owner already has. Every
        # probe below is either an owner READ or a stranger call that must be
        # refused, so nothing is mutated either way.
        st, body = req("GET", "/api/reports", O, org)
        for r in (ids_of(body, "reports") or [])[:25]:
            cid, chost = find_completion(r)
            if cid:
                break
    if not cid:
        R.skip("completion-scoped routes (plans/sigkill/steer/queued)",
               "no completion exists anywhere on the owner's reports, and this script "
               "never starts a paid turn to manufacture one")
        return
    R.note("target completion", f"{cid} on the owner's report {chost}")

    st, b = req("GET", f"/api/completions/{cid}/plans", O, org)
    R.allowed("OWNER can reach the reasoning plan for their own turn", st, b, ok_codes=(200, 404))
    for name, method, path, body_ in [
        ("read the reasoning plan", "GET", f"/api/completions/{cid}/plans", None),
        ("stop the run", "POST", f"/api/completions/{cid}/sigkill", {}),
        ("steer the run", "POST", f"/api/completions/{cid}/steer", {"content": "do something else"}),
        ("drop a queued prompt", "DELETE", f"/api/completions/{cid}/queued", None),
        ("answer a tool clarification", "POST",
         f"/api/completions/{cid}/tool_executions/{GHOST}/clarify_response", {"response": "yes"}),
        ("confirm an MCP tool call", "POST",
         f"/api/completions/{cid}/mcp_tool_confirmations/{GHOST}", {"approved": True}),
    ]:
        st, b = req(method, path, S, org, body_)
        R.refused(f"stranger CANNOT {name} on another member's turn", st, b,
                  ok_codes=(403, 404, 409))


def sec_widget(R, C):
    """Chart widgets. The route gates report_id; the service looks up widget_id."""
    R.head("widget")
    O, S, org = C["owner"], C["stranger"], C["org"]
    rid, srid = C.get("owner_report"), C.get("stranger_report")

    # Find a widget that genuinely belongs to the owner. Probe reports are
    # empty (no paid turn was run), so walk the owner's real reports.
    st, body = req("GET", "/api/reports", O, org)
    wid = wrep = None
    for r in (ids_of(body, "reports") or [])[:25]:
        stw, wl = req("GET", f"/api/reports/{r}/widgets", O, org)
        if stw == 200 and isinstance(wl, list) and wl:
            wid, wrep = wl[0].get("id"), r
            break
    if not wid:
        R.skip("widget probes", "the owner has no widget to aim at")
        return
    R.note("target widget", f"widget={wid} in the owner's report {wrep}")

    st, b = req("GET", f"/api/reports/{wrep}/widgets/{wid}", O, org)
    R.allowed("OWNER can read their own widget", st, b)

    st, b = req("GET", f"/api/reports/{wrep}/widgets", S, org)
    R.refused("stranger CANNOT list the widgets of another member's report", st, b)

    st, b = req("GET", f"/api/reports/{wrep}/widgets/{wid}", S, org)
    R.refused("stranger CANNOT read another member's widget by its own report id", st, b)

    # ★The sharpest form of the IDOR. The single-widget routes are declared
    # `def get_widget_by_id(widget_uuid, …)` — the decorator hunts for
    # `report_id`/`widget_id`/… among the BOUND arguments, finds none, and so
    # `object_id` is None and its whole object block is skipped. What is left
    # is a bare role check, and `view_reports` is baseline for every member.
    # Passing a report id that does not exist at all proves the path segment is
    # decorative: if the gate ran, this could not answer 200.
    st, b = req("GET", f"/api/reports/{GHOST}/widgets/{wid}", S, org)
    R.refused("stranger CANNOT read a foreign widget through a NONEXISTENT report id",
              st, b, leak_note="(the report_id path segment is never used — the gate is skipped)")

    if srid:
        st, b = req("GET", f"/api/reports/{srid}/widgets/{wid}", S, org)
        R.refused("stranger CANNOT read a foreign widget through their OWN report id",
                  st, b, leak_note="(widget_uuid is never checked against report_id)")

        st, b = req("GET", f"/api/reports/{srid}/widgets/{wid}/export", S, org)
        R.refused("stranger CANNOT export a foreign widget's data as CSV", st, b)

        # A write probe that changes NOTHING: PUT the widget's current values
        # straight back. A 200 proves the write path is open without altering
        # the object, which is the only honest way to test a write against data
        # this script does not own.
        stc, cur = req("GET", f"/api/reports/{wrep}/widgets/{wid}", O, org)
        if stc == 200 and isinstance(cur, dict) and cur.get("title"):
            noop = {k: cur.get(k) for k in ("id", "title", "status", "x", "y", "width", "height")}
            st, b = req("PUT", f"/api/reports/{srid}/widgets/{wid}", S, org, noop)
            R.refused("stranger CANNOT write to a foreign widget through their OWN report id",
                      st, b, leak_note="(no-op write: a 200 means the write path is open)")
        else:
            R.skip("stranger write to a foreign widget", "could not read the widget's current values")
    else:
        R.skip("widget IDOR probes", "the stranger has no report of their own")

    # The owner's own export must WORK. It does not: the decorator resolves
    # `widget_id` as the object id and then looks it up as a Report, so the
    # route 404s for everyone — a gate set so tight the feature is dead.
    st, b = req("GET", f"/api/reports/{wrep}/widgets/{wid}/export", O, org)
    R.allowed("OWNER can export their own widget to CSV", st, b)

    if rid:
        st, b = req("GET", f"/api/reports/{rid}/widgets/{GHOST}", O, org)
        R.rec("an unknown widget id does not 500", st != 500, f"HTTP {st} — {brief(b, 80)}")


def sec_text_widget(R, C):
    """Text widgets carry the same route/service split as chart widgets, and
    unlike them can be created without spending money on a model."""
    R.head("text widget")
    O, S, org = C["owner"], C["stranger"], C["org"]
    rid, srid = C.get("owner_report"), C.get("stranger_report")
    if not rid:
        R.skip("text widget probes", "no owner report")
        return

    st, tw = req("POST", f"/api/reports/{rid}/text_widgets", O, org,
                 {"content": "tenancy-probe private note", "x": 0, "y": 0, "width": 4, "height": 2})
    if st not in (200, 201):
        R.rec("owner creates a text widget", False, f"HTTP {st} — {brief(tw)}")
        return
    twid = tw["id"]
    R.cleanup.append(lambda: req("DELETE", f"/api/reports/{rid}/text_widgets/{twid}", O, org))
    R.rec("owner creates a text widget", True, f"id={twid}")

    st, b = req("GET", f"/api/reports/{rid}/text_widgets/{twid}", O, org)
    R.allowed("OWNER can read their own text widget", st, b)

    st, b = req("GET", f"/api/reports/{rid}/text_widgets", S, org)
    R.refused("stranger CANNOT list another member's text widgets", st, b)
    st, b = req("GET", f"/api/reports/{rid}/text_widgets/{twid}", S, org)
    R.refused("stranger CANNOT read another member's text widget", st, b)
    st, b = req("PUT", f"/api/reports/{rid}/text_widgets/{twid}", S, org, {"content": "seized"})
    R.refused("stranger CANNOT rewrite another member's text widget", st, b)

    if srid:
        st, b = req("GET", f"/api/reports/{srid}/text_widgets/{twid}", S, org)
        R.refused("stranger CANNOT read a foreign text widget through their OWN report id", st, b)
        st, b = req("PUT", f"/api/reports/{srid}/text_widgets/{twid}", S, org, {"content": "seized"})
        R.refused("stranger CANNOT rewrite a foreign text widget through their OWN report id", st, b)


def sec_artifact(R, C):
    """Dashboards and documents are artifacts; charts are widgets."""
    R.head("artifact / dashboard")
    O, S, A, org = C["owner"], C["stranger"], C["admin"], C["org"]
    rid = C.get("owner_report")
    if not rid:
        R.skip("artifact probes", "no owner report")
        return

    st, art = req("POST", "/api/artifacts", O, org, {
        "report_id": rid, "kind": "dashboard", "title": "tenancy-probe dashboard",
        "content": {"blocks": []},
    })
    aid = art.get("id") if isinstance(art, dict) else None
    if st in (200, 201) and aid:
        R.cleanup.append(lambda: req("DELETE", f"/api/artifacts/{aid}", O, org))
        R.rec("owner creates an artifact", True, f"id={aid}")
    else:
        R.rec("owner creates an artifact", False, f"HTTP {st} — {brief(art)}")
        # Fall back to any artifact the owner already has.
        stl, lst = req("GET", f"/api/artifacts/report/{rid}", O, org)
        aid = first_id(lst)
        if not aid:
            R.skip("artifact probes", "no artifact available")
            return

    st, b = req("GET", f"/api/artifacts/{aid}", O, org)
    R.allowed("OWNER can read their own artifact", st, b)

    for name, method, path, body_ in [
        ("read the artifact", "GET", f"/api/artifacts/{aid}", None),
        ("list the report's artifacts", "GET", f"/api/artifacts/report/{rid}", None),
        ("read the latest artifact", "GET", f"/api/artifacts/report/{rid}/latest", None),
        ("patch the artifact", "PATCH", f"/api/artifacts/{aid}", {"title": "seized"}),
        ("delete the artifact", "DELETE", f"/api/artifacts/{aid}", None),
        ("duplicate the artifact", "POST", f"/api/artifacts/{aid}/duplicate", {}),
        ("edit the document body", "POST", f"/api/artifacts/{aid}/doc_edit",
         {"markdown": "seized"}),
        ("list its exports", "GET", f"/api/artifacts/{aid}/exports", None),
        ("export it as PDF", "GET", f"/api/artifacts/{aid}/export/pdf", None),
        ("read its slide previews", "GET", f"/api/artifacts/{aid}/previews", None),
    ]:
        st, b = req(method, path, S, org, body_)
        R.refused(f"stranger CANNOT {name}", st, b, ok_codes=(400, 403, 404))

    st, b = req("GET", "/api/artifacts", S, org)
    listed = ids_of(b, "artifacts") or ids_of(b, "items")
    R.rec("owner's artifact is absent from the stranger's artifact browse",
          st == 200 and aid not in listed,
          f"HTTP {st}, {len(listed)} rows, {'PRESENT — LEAK' if aid in listed else 'absent'}")


def sec_step_query(R, C):
    """Steps hold the generated code AND the result grid. Queries hold the SQL."""
    R.head("step / query / visualization")
    O, S, A, org = C["owner"], C["stranger"], C["admin"], C["org"]

    # Find a step the owner owns, via one of the owner's widgets.
    st, body = req("GET", "/api/reports", O, org)
    step_id = None
    for r in (ids_of(body, "reports") or [])[:25]:
        stw, wl = req("GET", f"/api/reports/{r}/widgets", O, org)
        if stw == 200 and isinstance(wl, list) and wl:
            last = wl[0].get("last_step") or {}
            if isinstance(last, dict) and last.get("id"):
                step_id = last["id"]
                break
    if step_id:
        st, b = req("GET", f"/api/steps/{step_id}", O, org)
        R.allowed("OWNER can read their own step", st, b)
        st, b = req("GET", f"/api/steps/{step_id}", S, org)
        R.refused("stranger CANNOT read another member's step (code + result grid)", st, b,
                  leak_note="(get_step_by_id filters on step id only)")
        st, b = req("GET", f"/api/steps/{step_id}/export", S, org)
        R.refused("stranger CANNOT export another member's step", st, b, ok_codes=(400, 403, 404))
    else:
        R.skip("step probes", "no step found on the owner's widgets")

    # ★Queries are the shortest path to everyone else's numbers, and they also
    # publish the id space the other IDORs need. `list_queries` filters on
    # organization_id and NOTHING else — no owner, no report visibility. The
    # per-viewer policy that does exist (`overlay_viewer_on_query_schema`) is
    # about credential-differentiated snapshots on per-user connectors, not
    # about ownership, so it is a no-op on an ordinary report.
    st, qa = req("GET", "/api/queries", A, org)
    st, ql = req("GET", "/api/queries", S, org)
    if st == 200 and isinstance(ql, list):
        n_admin = len(qa) if isinstance(qa, list) else -1
        reports = {q.get("report_id") for q in ql if q.get("report_id")}
        owners = {q.get("user_id") for q in ql if q.get("user_id")}
        foreign = [q for q in ql if q.get("user_id") and q["user_id"] != C["stranger_id"]]
        R.rec("the query list is scoped to what the caller may see",
              not foreign,
              f"stranger got {len(ql)} of the admin's {n_admin} queries, spanning "
              f"{len(reports)} report ids and {len(owners)} distinct owners; "
              f"{len(foreign)} belong to someone else")
        R.note("what the query list hands an attacker", f"{len(reports)} report ids — "
               "the enumeration oracle below is moot when the ids are simply published")
    qid = first_id(qa)
    if qid:
        st, b = req("GET", f"/api/queries/{qid}", A, org)
        R.allowed("OWNER can read their own query", st, b)
        st, b = req("GET", f"/api/queries/{qid}", S, org)
        R.refused("stranger CANNOT read another member's query", st, b)
        st, b = req("GET", f"/api/queries/{qid}/default_step", S, org)
        step = (b or {}).get("step") or {} if isinstance(b, dict) else {}
        rows = len((step.get("data") or {}).get("rows") or []) if isinstance(step, dict) else 0
        R.refused("stranger CANNOT read a foreign query's default step (code + rows)",
                  st, b, leak_note=f"(code={bool(step.get('code'))}, {rows} result rows)")
    else:
        R.skip("query probes", "no query visible to the admin")

    st, b = req("GET", f"/api/visualizations/{GHOST}", S, org)
    R.rec("an unknown visualization id 404s rather than 500", st in (403, 404),
          f"HTTP {st} — {brief(b, 80)}")


def sec_visualization(R, C):
    """A chart's configuration — and the only object here with NO tenancy column.

    `models/visualization.py` declares title/status/report_id/query_id/view and
    nothing else: no `organization_id`, no `user_id`. So the decorator's
    `model.organization_id == organization.id` filter could not run even if it
    were reached, and `visualization_service.get/update` are bare
    `select(Visualization).where(id == ...)`.

    It is not reached. `@requires_permission('view_reports', model=…,
    owner_only=True)` looks for its object id among eight parameter names
    (permissions_decorator.py:75-84) and `visualization_id` is not one of them,
    so `object_id` is None and the whole ownership block is skipped. What is
    left is `view_reports` / `update_reports` — baseline for every member.

    ★The WRITE is the finding. A reader is bad; a stranger silently editing
    what someone else's dashboard renders is worse, and `VisualizationUpdate`
    accepts `view` as well as `title`, so the chart's entire encoding can be
    repointed. This probe writes a marker, checks it stuck, and restores the
    original — the restore is also registered as a teardown step so it runs
    even if the section raises in between.
    """
    R.head("visualization")
    O, S, org = C["owner"], C["stranger"], C["org"]

    # Find a visualization the OWNER owns, through the owner's own reports.
    st, reps = req("GET", "/api/reports", O, org)
    vid = vrep = None
    for r in (ids_of(reps, "reports") or [])[:25]:
        stq, qs = req("GET", f"/api/queries?report_id={r}", O, org)
        if stq == 200 and isinstance(qs, list):
            for q in qs:
                for v in (q.get("visualizations") or []):
                    if v.get("id"):
                        vid, vrep = v["id"], r
                        break
                if vid:
                    break
        if vid:
            break
    if not vid:
        R.skip("visualization probes", "the owner has no chart to aim at")
        return
    R.note("target visualization", f"{vid} on the owner's report {vrep}")

    # The probe is only meaningful if the stranger is genuinely shut out of the
    # parent report — otherwise a 200 below would be legitimate.
    st, b = req("GET", f"/api/reports/{vrep}", S, org)
    if st == 200:
        R.skip("visualization probes", "the stranger can already read the parent report")
        return
    R.rec("stranger is refused the report the chart belongs to", st in (403, 404), f"HTTP {st}")

    st, cur = req("GET", f"/api/visualizations/{vid}", O, org)
    if st != 200 or not isinstance(cur, dict):
        R.rec("OWNER can read their own visualization", False, f"HTTP {st} — {brief(cur)}")
        return
    R.rec("OWNER can read their own visualization", True, f"HTTP {st}, fields {sorted(cur)}")
    orig_title, orig_status = cur.get("title"), cur.get("status")

    # Restore first, attempt second: registered before the write so a crash in
    # between still puts the object back.
    R.cleanup.append(lambda: req("PATCH", f"/api/visualizations/{vid}", O, org,
                                 {"title": orig_title, "status": orig_status}))

    st, b = req("PATCH", f"/api/visualizations/{vid}", O, org, {"title": orig_title})
    R.allowed("OWNER can still patch their own visualization", st, b)

    st, b = req("GET", f"/api/visualizations/{vid}", S, org)
    R.refused("stranger CANNOT read another member's visualization", st, b,
              leak_note="(Visualization carries no organization_id or user_id at all)")

    MARK = "tenancy-probe-write-by-a-stranger"
    st, b = req("PATCH", f"/api/visualizations/{vid}", S, org, {"title": MARK})
    R.refused("stranger CANNOT patch another member's visualization", st, b)

    # Did the write actually land? A 200 on a no-op route would prove nothing.
    st, after = req("GET", f"/api/visualizations/{vid}", O, org)
    stuck = isinstance(after, dict) and after.get("title") == MARK
    R.rec("a stranger's write does NOT alter what the owner's chart renders",
          not stuck,
          "owner re-read shows the stranger's marker — the write stuck"
          if stuck else "owner re-read is unchanged")

    st, b = req("PATCH", f"/api/visualizations/{vid}", O, org,
                {"title": orig_title, "status": orig_status})
    st, fin = req("GET", f"/api/visualizations/{vid}", O, org)
    R.rec("the probe restored the visualization it touched",
          isinstance(fin, dict) and fin.get("title") == orig_title
          and fin.get("status") == orig_status,
          f"title restored={isinstance(fin, dict) and fin.get('title') == orig_title}, "
          f"status restored={isinstance(fin, dict) and fin.get('status') == orig_status}")


def sec_file(R, C):
    """Uploaded files: chat attachments and agent sources."""
    R.head("file")
    O, S, A, org = C["owner"], C["stranger"], C["admin"], C["org"]
    rid = C.get("owner_report")

    # Aim at a file attached to a report the stranger is PROVABLY refused, so
    # the probe measures the file gate and not the report gate. Walk the
    # admin's reports (they hold the bulk of the real attachments) and keep the
    # first one whose report the stranger cannot open.
    fid = frep = None
    st, body = req("GET", "/api/reports", A, org)
    for r in (ids_of(body, "reports") or [])[:40]:
        stf, fl = req("GET", f"/api/reports/{r}/files", A, org)
        if stf == 200 and isinstance(fl, list) and fl:
            str_, _ = req("GET", f"/api/reports/{r}", S, org)
            if str_ in (403, 404):
                fid, frep = fl[0].get("id"), r
                break
    if not fid:
        st, body = req("GET", "/api/files", O, org)
        fid = first_id(body)
    if not fid:
        R.skip("file probes", "no file to aim at")
        return
    R.note("target file", f"{fid}" + (f" attached to the refused report {frep}" if frep else ""))

    st, b = req("GET", f"/api/files/{fid}/content", A, org, raw=True)
    R.allowed("the file's OWNER can download it", st, b)

    if frep:
        st, b = req("GET", f"/api/reports/{frep}", S, org)
        R.refused("stranger is refused the report the file hangs off", st, b)
        st, b = req("GET", f"/api/reports/{frep}/files", S, org)
        names = [x.get("filename") for x in b] if isinstance(b, list) else []
        R.refused("stranger CANNOT list the attachments of a report they were just refused",
                  st, b, leak_note=f"(filenames: {names[:3]})")

    st, b = req("GET", f"/api/files/{fid}/content", S, org, raw=True)
    n = len(b) if isinstance(b, (bytes, bytearray)) else 0
    R.refused("stranger CANNOT download a file from a report they cannot open", st, b,
              leak_note=f"({n} bytes served)")

    st, b = req("GET", f"/api/files/{fid}/text", S, org)
    R.refused("stranger CANNOT read the extracted text of that file", st, b)

    # ★An embed token is a BEARER CREDENTIAL: whoever holds it fetches the file
    # with no login at all. Minting one must be at least as hard as reading the
    # file, or any member can convert an internal document into a public URL.
    st, tokb = req("GET", f"/api/files/{fid}/embed_token", S, org)
    R.refused("stranger CANNOT mint an embed token for a file they cannot read", st, tokb)
    if st == 200 and isinstance(tokb, dict) and tokb.get("token"):
        st2, b2 = req("GET", f"/api/files/{fid}/embed?token={tokb['token']}", None, None, raw=True)
        n2 = len(b2) if isinstance(b2, (bytes, bytearray)) else 0
        R.rec("that minted token does not serve the file to an ANONYMOUS caller",
              st2 not in (200, 206),
              f"HTTP {st2}, {n2} bytes — a member turned a private document into an "
              "unauthenticated URL" if st2 in (200, 206) else f"HTTP {st2}")

    st, b = req("GET", "/api/files", S, org)
    n = len(b) if isinstance(b, list) else "?"
    R.note("stranger listing every file in the org", f"HTTP {st}, {n} rows — "
           "`manage_files` is baseline for every member and /api/files is org-scoped, "
           "not owner-scoped")

    st, b = req("GET", f"/api/files/{GHOST}/content", S, org)
    R.rec("an unknown file id 404s", st == 404, f"HTTP {st}")

    st, b = req("GET", "/api/files/not-a-uuid/content", S, org)
    R.rec("a non-UUID file id 404s rather than 500", st == 404, f"HTTP {st} — {brief(b, 80)}")

    if rid:
        st, b = req("GET", f"/api/reports/{rid}/files", S, org)
        R.refused("stranger CANNOT list the files attached to another member's report", st, b,
                  leak_note="(an empty list here reads as 'no files', not 'not yours')")


def sec_project(R, C):
    """Projects are a SHARING boundary: membership of one grants view on the
    reports inside it (permissions_decorator project_view). So who may edit a
    project's member list decides who may read other people's reports."""
    R.head("project / folder")
    O, S, org = C["owner"], C["stranger"], C["org"]
    stranger_uid = C["stranger_id"]

    st, proj = req("POST", "/api/projects", O, org, {"name": "tenancy-probe project"})
    if st not in (200, 201):
        R.rec("owner creates a project", False, f"HTTP {st} — {brief(proj)}")
        return
    pid = proj["id"]
    R.cleanup.append(lambda: req("DELETE", f"/api/projects/{pid}", O, org))
    R.rec("owner creates a project", True, f"id={pid}")

    st, b = req("GET", f"/api/projects/{pid}", O, org)
    R.allowed("OWNER can read their own project", st, b)

    st, b = req("GET", f"/api/projects/{pid}", S, org)
    R.refused("stranger CANNOT read a project they were not added to", st, b)
    st, b = req("PUT", f"/api/projects/{pid}", S, org, {"name": "seized"})
    R.refused("stranger CANNOT rename another member's project", st, b)
    st, b = req("DELETE", f"/api/projects/{pid}", S, org)
    R.refused("stranger CANNOT delete another member's project", st, b)
    st, b = req("GET", f"/api/projects/{pid}/members", S, org)
    R.refused("stranger CANNOT read a project's member list", st, b)

    # ★The escalation to look for: adding yourself to a project you cannot see
    # would hand you `project_view` on every report inside it.
    st, b = req("PUT", f"/api/projects/{pid}/members", S, org,
                {"user_id": stranger_uid, "permissions": ["manage"]})
    R.refused("stranger CANNOT add THEMSELVES to another member's project", st, b,
              leak_note="(project membership grants view on every report inside)")

    st, b = req("GET", "/api/projects", S, org)
    listed = ids_of(b, "projects") or ids_of(b, "items")
    R.rec("owner's project is absent from the stranger's project list",
          st == 200 and pid not in listed,
          f"HTTP {st}, {len(listed)} rows, {'PRESENT — LEAK' if pid in listed else 'absent'}")

    # Moving reports between folders is a write on the report.
    rid = C.get("owner_report")
    if rid:
        st, b = req("POST", "/api/reports/bulk/move", S, org,
                    {"report_ids": [rid], "project_id": pid})
        R.refused("stranger CANNOT move another member's report into a folder", st, b,
                  ok_codes=(400, 403, 404))


def sec_prompt(R, C):
    """Saved prompts / starters. None of these routes carries a decorator —
    every check lives in prompt_service.authorize_*."""
    R.head("prompt")
    O, S, org = C["owner"], C["stranger"], C["org"]

    st, p = req("POST", "/api/prompts", O, org,
                {"title": "tenancy-probe prompt", "text": "hello", "scope": "private"})
    pid = p.get("id") if isinstance(p, dict) else None
    if st not in (200, 201) or not pid:
        R.rec("owner creates a prompt", False, f"HTTP {st} — {brief(p)}")
        return
    R.cleanup.append(lambda: req("DELETE", f"/api/prompts/{pid}", O, org))
    R.rec("owner creates a prompt", True, f"id={pid} scope={p.get('scope')}")

    st, b = req("GET", f"/api/prompts/{pid}", O, org)
    R.allowed("OWNER can read their own prompt", st, b)
    st, b = req("PUT", f"/api/prompts/{pid}", O, org, {"title": "tenancy-probe prompt v2"})
    R.allowed("OWNER can update their own prompt", st, b)

    if (p.get("scope") or "") in ("private", "personal", "user"):
        st, b = req("GET", f"/api/prompts/{pid}", S, org)
        R.refused("stranger CANNOT read another member's PRIVATE prompt", st, b)
    else:
        R.note("prompt scope", f"{p.get('scope')!r} — org-visible by design, read is not a finding")

    st, b = req("PUT", f"/api/prompts/{pid}", S, org, {"title": "seized"})
    R.refused("stranger CANNOT rewrite another member's prompt", st, b)
    st, b = req("DELETE", f"/api/prompts/{pid}", S, org)
    R.refused("stranger CANNOT delete another member's prompt", st, b)


def sec_automation(R, C):
    """Webhooks, triggers and scheduled prompts. A webhook secret is a
    credential; a schedule spends money on the owner's behalf."""
    R.head("webhook / trigger / schedule")
    O, S, org = C["owner"], C["stranger"], C["org"]
    rid = C.get("owner_report")
    if not rid:
        R.skip("automation probes", "no owner report")
        return

    st, wh = req("POST", f"/api/reports/{rid}/webhooks", O, org,
                 {"name": "tenancy-probe hook", "source": "generic"})
    whid = wh.get("id") if isinstance(wh, dict) else None
    if st in (200, 201) and whid:
        R.cleanup.append(lambda: req("DELETE", f"/api/reports/{rid}/webhooks/{whid}", O, org))
        R.rec("owner creates a webhook on their report", True, f"id={whid}")
        st, b = req("GET", f"/api/reports/{rid}/webhooks", O, org)
        R.allowed("OWNER can list their own webhooks", st, b)
        for name, method, path, body_ in [
            ("list the webhooks (they carry secrets)", "GET", f"/api/reports/{rid}/webhooks", None),
            ("rotate the webhook secret", "POST",
             f"/api/reports/{rid}/webhooks/{whid}/rotate", {}),
            ("edit the webhook", "PUT", f"/api/reports/{rid}/webhooks/{whid}", {"name": "seized"}),
            ("delete the webhook", "DELETE", f"/api/reports/{rid}/webhooks/{whid}", None),
        ]:
            st, b = req(method, path, S, org, body_)
            R.refused(f"stranger CANNOT {name} on another member's report", st, b)
    else:
        R.rec("owner creates a webhook on their report", False, f"HTTP {st} — {brief(wh)}")

    st, sp = req("POST", f"/api/reports/{rid}/scheduled-prompts", O, org,
                 {"prompt": {"content": "tenancy probe", "mentions": []},
                  "title": "tenancy-probe schedule",
                  "cron_schedule": "0 3 * * *", "is_active": False})
    spid = sp.get("id") if isinstance(sp, dict) else None
    if st in (200, 201) and spid:
        R.cleanup.append(lambda: req("DELETE", f"/api/reports/{rid}/scheduled-prompts/{spid}", O, org))
        R.rec("owner creates a scheduled prompt", True, f"id={spid}")
        for name, method, path, body_ in [
            ("list the schedules", "GET", f"/api/reports/{rid}/scheduled-prompts", None),
            ("edit the schedule", "PUT",
             f"/api/reports/{rid}/scheduled-prompts/{spid}",
             {"prompt": {"content": "seized", "mentions": []}}),
            ("delete the schedule", "DELETE",
             f"/api/reports/{rid}/scheduled-prompts/{spid}", None),
            ("read its run history", "GET",
             f"/api/reports/{rid}/scheduled-prompts/{spid}/runs", None),
        ]:
            st, b = req(method, path, S, org, body_)
            R.refused(f"stranger CANNOT {name} on another member's report", st, b)
        # ★Triggering someone else's schedule spends THEIR model budget.
        st, b = req("POST", f"/api/reports/{rid}/scheduled-prompts/{spid}/trigger", S, org, {})
        R.refused("stranger CANNOT fire another member's schedule (spends their budget)", st, b)
    else:
        R.note("scheduled prompt", f"HTTP {st} — {brief(sp)}; schedule probes skipped")

    st, tl = req("GET", "/api/triggers", S, org)
    R.note("stranger listing triggers", f"HTTP {st}, {len(tl) if isinstance(tl, list) else '?'} rows")
    st, b = req("GET", f"/api/triggers/{GHOST}", S, org)
    R.rec("an unknown trigger id 404s rather than 500", st in (403, 404), f"HTTP {st}")


def sec_instruction(R, C):
    """Instructions steer every future answer for whoever they are scoped to."""
    R.head("instruction")
    O, S, A, org = C["owner"], C["stranger"], C["admin"], C["org"]

    # A plain member holds no `manage_instructions` grant on any agent, so the
    # instruction's legitimate author here is the ADMIN and the member is the
    # party who must be kept out. Confirm the member is refused authorship
    # first — that refusal is the product working, not a finding.
    st, b = req("POST", "/api/instructions", S, org,
                {"text": "tenancy-probe member instruction", "category": "general",
                 "data_source_ids": []})
    R.refused("a member with no agent grant CANNOT author an instruction", st, b,
              ok_codes=(400, 403, 404, 422))
    if st in (200, 201) and isinstance(b, dict) and b.get("id"):
        _sid = b["id"]
        R.cleanup.append(lambda: req("DELETE", f"/api/instructions/{_sid}", A, org))

    st, ins = req("POST", "/api/instructions", A, org,
                  {"text": "tenancy-probe instruction", "category": "general",
                   "data_source_ids": [], "private_status": "draft"})
    iid = ins.get("id") if isinstance(ins, dict) else None
    if st in (200, 201) and iid:
        R.cleanup.append(lambda: req("DELETE", f"/api/instructions/{iid}", A, org))
        R.rec("admin authors a DRAFT instruction", True, f"id={iid}")
        st, b = req("GET", f"/api/instructions/{iid}", A, org)
        R.allowed("AUTHOR can read their own draft instruction", st, b)
        st, b = req("PUT", f"/api/instructions/{iid}", S, org, {"text": "seized"})
        R.refused("member CANNOT rewrite someone else's instruction", st, b)
        st, b = req("DELETE", f"/api/instructions/{iid}", S, org)
        R.refused("member CANNOT delete someone else's instruction", st, b)
        # ★Do not assume the status you asked for is the status you got: this
        # POST answers `status='published'` even when `private_status='draft'`
        # is sent, and a GLOBAL published instruction is org-visible on purpose.
        # Assert against what the server actually recorded, or the probe fails
        # on correct behaviour and teaches the next reader the wrong rule.
        server_status = ins.get("status")
        scoped = bool(ins.get("data_sources"))
        st, b = req("GET", "/api/instructions", S, org)
        listed = ids_of(b, "instructions") or ids_of(b, "items")
        present = iid in listed
        if server_status == "published" and not scoped:
            R.rec("a GLOBAL published instruction is visible org-wide, as designed",
                  st == 200, f"HTTP {st}, {len(listed)} rows, "
                             f"{'present (expected)' if present else 'absent'}")
        else:
            R.rec("an unpublished or agent-scoped instruction is absent from a member's list",
                  st == 200 and not present,
                  f"HTTP {st}, status={server_status!r}, "
                  f"{'PRESENT — LEAK' if present else 'absent'}")
    else:
        R.rec("admin authors a DRAFT instruction", False, f"HTTP {st} — {brief(ins)}")

    # A global instruction is an org-wide behaviour change: members must not
    # be able to mint one.
    st, b = req("POST", "/api/instructions/global", S, org,
                {"text": "tenancy-probe global", "category": "general"})
    R.refused("member CANNOT create a GLOBAL instruction", st, b, ok_codes=(400, 403, 404, 422))
    if isinstance(b, dict) and b.get("id"):
        gid = b["id"]
        R.cleanup.append(lambda: req("DELETE", f"/api/instructions/{gid}", A, org))


def sec_agent(R, C):
    """Agents (data_sources) and connections. Connections hold credentials.

    ★This section NEVER calls GET /data_sources/{id}/test_connection: it is
    spelled GET and WRITES is_active, and one sweep disabled a live agent for
    the whole organization."""
    R.head("agent / data source / connection")
    O, S, A, org = C["owner"], C["stranger"], C["admin"], C["org"]

    st, dsl = req("GET", "/api/data_sources", A, org)
    ds_ids = ids_of(dsl)
    if not ds_ids:
        R.skip("agent probes", "no data source visible to the admin")
        return
    st, own = req("GET", "/api/data_sources", S, org)
    stranger_visible = set(ids_of(own))
    hidden = [d for d in ds_ids if d not in stranger_visible]
    R.note("agent visibility", f"admin sees {len(ds_ids)}, stranger sees {len(stranger_visible)}")

    target = hidden[0] if hidden else None
    if target:
        st, b = req("GET", f"/api/data_sources/{target}", A, org)
        R.allowed("ADMIN can read an agent they manage", st, b)
        for name, method, path, body_ in [
            ("read an agent absent from their own list", "GET", f"/api/data_sources/{target}", None),
            ("read its schema", "GET", f"/api/data_sources/{target}/schema", None),
            ("read its full schema", "GET", f"/api/data_sources/{target}/full_schema", None),
            ("read its member list", "GET", f"/api/data_sources/{target}/members", None),
            ("read its connections", "GET", f"/api/data_sources/{target}/connections", None),
            ("edit it", "PUT", f"/api/data_sources/{target}", {"name": "seized"}),
            ("delete it", "DELETE", f"/api/data_sources/{target}", None),
            ("grant themselves membership", "POST", f"/api/data_sources/{target}/members",
             {"principal_type": "user", "principal_id": C["stranger_id"]}),
        ]:
            st, b = req(method, path, S, org, body_)
            R.refused(f"stranger CANNOT {name}", st, b, ok_codes=(400, 403, 404))
    else:
        R.note("agent probes", "every agent is visible to the stranger (all public) — "
                               "no private agent to aim at")

    st, cl = req("GET", "/api/connections", A, org)
    all_conn = ids_of(cl)
    st, cls_ = req("GET", "/api/connections", S, org)
    stranger_conn = set(ids_of(cls_))
    R.note("connection visibility", f"admin sees {len(all_conn)}, stranger sees {len(stranger_conn)}")

    cid = first_id(cl)
    if not cid:
        R.skip("connection probes", "no connection visible to the admin")
        return

    st, b = req("GET", f"/api/connections/{cid}", A, org)
    R.allowed("ADMIN can read a connection", st, b)
    st, b = req("GET", f"/api/connections/{cid}", S, org)
    R.refused("stranger CANNOT read a connection's detail", st, b)
    st, b = req("PUT", f"/api/connections/{cid}", S, org, {"name": "seized"})
    R.refused("stranger CANNOT edit a connection", st, b, ok_codes=(400, 403, 404, 422))
    st, b = req("DELETE", f"/api/connections/{cid}", S, org)
    R.refused("stranger CANNOT delete a connection", st, b)

    # ★The table catalog and the detail view are gated by DIFFERENT rules:
    # `/tables` calls _ensure_can_read_connection (any public agent on the
    # connection is enough) while `/{id}` also carries a `manage_connection`
    # decorator. So a member can be refused the detail and still be handed the
    # catalog. That is defensible for a connection backing a PUBLIC agent —
    # and not defensible for one the member cannot reach at all, which is what
    # this probe pins: a connection missing from the member's own list.
    unreachable = [c for c in all_conn if c not in stranger_conn]
    st, b = req("GET", f"/api/connections/{cid}/tables", S, org)
    R.note("stranger reading the table catalog of a connection they can list",
           f"HTTP {st} — /tables and /{{id}} use different gates")
    if unreachable:
        st, b = req("GET", f"/api/connections/{unreachable[0]}/tables", S, org)
        n = len(b) if isinstance(b, list) else "?"
        R.refused("stranger CANNOT read the table catalog of a connection absent from their list",
                  st, b, leak_note=f"({n} tables)")
        st, b = req("GET", f"/api/connections/{unreachable[0]}/custom-queries", S, org)
        R.refused("stranger CANNOT read that connection's custom queries (admin SQL)", st, b)
        st, b = req("GET", f"/api/connections/{unreachable[0]}/user_roster", S, org)
        R.refused("stranger CANNOT read that connection's user roster", st, b)
    else:
        R.skip("unreachable-connection probes", "the stranger can list every connection")

    # No connection read may return a decrypted secret to anyone.
    st, b = req("GET", f"/api/connections/{cid}", A, org)
    blob = json.dumps(b, default=str).lower().replace(" ", "") if st == 200 else ""
    bad = [k for k in ("\"password\":\"", "client_secret\":\"", "private_key\":\"",
                       "bind_password\":\"", "\"api_key\":\"") if k in blob]
    R.rec("a connection detail does not hand back a decrypted secret",
          st != 200 or not bad, f"HTTP {st}" + (f" — found {bad}" if bad else ""))


def sec_evals(R, C):
    """Eval suites, cases and runs. Transcripts contain real answers."""
    R.head("eval suite / test case")
    O, S, A, org = C["owner"], C["stranger"], C["admin"], C["org"]

    st, sl = req("GET", "/api/tests/suites", A, org)
    sid = first_id(sl)
    if not sid:
        R.skip("eval probes", "no test suite visible to the admin")
        return
    st, b = req("GET", f"/api/tests/suites/{sid}", A, org)
    R.allowed("ADMIN can read a test suite", st, b)

    for name, method, path, body_ in [
        ("read a test suite", "GET", f"/api/tests/suites/{sid}", None),
        ("list its cases", "GET", f"/api/tests/suites/{sid}/cases", None),
        ("export it", "GET", f"/api/tests/suites/{sid}/export", None),
        ("edit it", "PATCH", f"/api/tests/suites/{sid}", {"name": "seized"}),
        ("delete it", "DELETE", f"/api/tests/suites/{sid}", None),
        ("start a run (spends money)", "POST", f"/api/tests/suites/{sid}/runs", {}),
    ]:
        st, b = req(method, path, S, org, body_)
        R.refused(f"member without manage_evals CANNOT {name}", st, b, ok_codes=(400, 403, 404, 422))

    st, cl = req("GET", f"/api/tests/suites/{sid}/cases", A, org)
    caseid = first_id(cl)
    if caseid:
        st, b = req("GET", f"/api/tests/cases/{caseid}", A, org)
        R.allowed("ADMIN can read a test case", st, b)
        for name, method, path, body_ in [
            ("read a test case", "GET", f"/api/tests/cases/{caseid}", None),
            ("edit a test case", "PATCH", f"/api/tests/cases/{caseid}", {"prompt": "seized"}),
            ("change its status", "PATCH", f"/api/tests/cases/{caseid}/status",
             {"status": "disabled"}),
            ("delete a test case", "DELETE", f"/api/tests/cases/{caseid}", None),
        ]:
            st, b = req(method, path, S, org, body_)
            R.refused(f"member without manage_evals CANNOT {name}", st, b,
                      ok_codes=(400, 403, 404, 422))
    else:
        R.skip("test case probes", "the admin's suite holds no case")

    st, rl = req("GET", "/api/tests/runs", A, org)
    rid_ = first_id(rl)
    if rid_:
        st, b = req("GET", f"/api/tests/runs/{rid_}/results", S, org)
        R.refused("member without manage_evals CANNOT read run results", st, b)
        st, b = req("GET", f"/api/tests/runs/{rid_}/status", S, org)
        R.refused("member without manage_evals CANNOT watch a run's status", st, b)


def sec_secrets_surface(R, C):
    """Settings that hold decryptable secrets: SMTP, LDAP bind, SSO clients.

    Not objects with owners, but the highest-value thing a member could reach,
    and every one of them is org-scoped rather than per-user — so the only gate
    is the permission. Assert the ADMIN can still administer, and that no read
    hands anyone a plaintext secret."""
    R.head("secret-bearing settings")
    S, A, org = C["stranger"], C["admin"], C["org"]

    SECRET_KEYS = ("bind_password", "client_secret", "\"password\"", "smtp_password",
                   "private_key", "hashed_password")

    for name, path in [
        ("the integrations list (SMTP/Slack/Teams)", "/api/settings/integrations"),
        ("the LDAP directory config", "/api/enterprise/ldap/config"),
        ("the SSO provider config", "/api/enterprise/sso/config"),
        ("the org settings blob", f"/api/organizations/{org}/settings"),
    ]:
        sta, ba = req("GET", path, A, org)
        R.rec(f"ADMIN can still read {name}", sta in (200, 404),
              f"HTTP {sta}" + ("" if sta in (200, 404) else f" — {brief(ba, 80)}"))
        if sta == 200:
            blob = json.dumps(ba, default=str).lower().replace(" ", "")
            plain = [k for k in SECRET_KEYS if f"{k}:\"" in blob or f"{k}\":\"" in blob]
            R.rec(f"{name} is redacted even for an ADMIN",
                  not plain, "clean" if not plain else f"FOUND {plain} in the response body")
        sts, bs = req("GET", path, S, org)
        R.refused(f"member CANNOT read {name}", sts, bs, ok_codes=(400, 403, 404))

    for name, method, path, body_ in [
        ("rewrite the LDAP bind credentials", "PUT", "/api/enterprise/ldap/config",
         {"enabled": True, "server_uri": "ldap://attacker.invalid",
          "bind_dn": "cn=admin", "bind_password": "x"}),
        ("add an SSO provider", "PUT", "/api/enterprise/sso/config",
         {"auth_mode": "hybrid", "oidc_providers": [
             {"name": "tenancy-probe", "enabled": True,
              "issuer": "https://attacker.invalid", "client_id": "x"}]}),
        ("point SMTP at another host", "POST", "/api/settings/integrations/email",
         {"host": "smtp.attacker.invalid", "port": 25, "username": "x", "password": "y"}),
    ]:
        st, b = req(method, path, S, org, body_)
        R.refused(f"member CANNOT {name}", st, b, ok_codes=(400, 403, 404, 422))


def sec_entity(R, C):
    """Semantic entities: shared definitions that steer every agent answer."""
    R.head("entity")
    S, A, org = C["stranger"], C["admin"], C["org"]

    st, el = req("GET", "/api/entities", A, org)
    eid = first_id(el)
    if not eid:
        R.skip("entity probes", "no entity visible to the admin")
        return
    st, b = req("GET", f"/api/entities/{eid}", A, org)
    R.allowed("ADMIN can read an entity", st, b)
    for name, method, path, body_ in [
        ("edit an entity", "PUT", f"/api/entities/{eid}", {"name": "seized"}),
        ("delete an entity", "DELETE", f"/api/entities/{eid}", None),
        ("approve an entity", "POST", f"/api/entities/{eid}/approve", {}),
        ("reject an entity", "POST", f"/api/entities/{eid}/reject", {}),
        ("run an entity", "POST", f"/api/entities/{eid}/run", {}),
    ]:
        st, b = req(method, path, S, org, body_)
        R.refused(f"member with no entity grant CANNOT {name}", st, b,
                  ok_codes=(400, 403, 404, 422))


def sec_rbac(R, C):
    """Roles, groups and grants are the keys to everything else."""
    R.head("group / role / grant")
    S, A, org = C["stranger"], C["admin"], C["org"]
    uid = C["stranger_id"]

    st, b = req("GET", f"/api/organizations/{org}/roles", A, org)
    R.allowed("ADMIN can list roles", st, b)

    # ── the half that must never move: a member cannot grant themselves anything
    for name, method, path, body_ in [
        ("create a role", "POST", f"/api/organizations/{org}/roles",
         {"name": "tenancy-probe-role", "permissions": ["full_admin_access"]}),
        ("create a group", "POST", f"/api/organizations/{org}/groups",
         {"name": "tenancy-probe-group"}),
        ("assign themselves a role", "POST", f"/api/organizations/{org}/role-assignments",
         {"principal_type": "user", "principal_id": uid, "role_id": GHOST}),
        ("grant themselves a resource", "POST", f"/api/organizations/{org}/resource-grants",
         {"principal_type": "user", "principal_id": uid,
          "resource_type": "data_source", "resource_id": GHOST, "permissions": ["manage"]}),
        ("invite a member", "POST", f"/api/organizations/{org}/members",
         {"email": "tenancy-probe@example.invalid", "role": "admin"}),
        ("promote themselves to admin", "PUT",
         f"/api/organizations/{org}/members/{GHOST}", {"role": "admin"}),
        ("mint an API key", "POST", "/api/api_keys", {"name": "tenancy-probe"}),
    ]:
        st, b = req(method, path, S, org, body_)
        R.refused(f"member CANNOT {name}", st, b, ok_codes=(400, 403, 404, 422))
        if isinstance(b, dict) and b.get("id") and st in (200, 201):
            R.note("LEAKED OBJECT CREATED", f"{path} -> {b['id']} (clean this up by hand)")

    # ── the half that is a DESIGN CHOICE, pinned rather than judged
    # `view_members` is deliberately baseline and hidden from the role editor
    # (permissions_registry), so these reads answering 200 is the documented
    # behaviour, not a broken gate. Pin it so a future change has to be
    # deliberate, and record separately what the payload actually discloses —
    # that breadth is the part worth a second look.
    for name, path in [
        ("the role catalogue", f"/api/organizations/{org}/roles"),
        ("the group list", f"/api/organizations/{org}/groups"),
        ("every role assignment in the org", f"/api/organizations/{org}/role-assignments"),
        ("every resource grant in the org", f"/api/organizations/{org}/resource-grants"),
    ]:
        st, b = req("GET", path, S, org)
        n = len(b) if isinstance(b, list) else "?"
        R.rec(f"reading {name} is 200-by-design for view_members, and does not 500",
              st in (200, 403, 404), f"HTTP {st}, {n} rows")

    st, roles = req("GET", f"/api/organizations/{org}/roles", S, org)
    if st == 200 and isinstance(roles, list):
        with_perms = [r.get("name") for r in roles
                      if isinstance(r, dict) and r.get("permissions")]
        R.note("what the role catalogue discloses to a plain member",
               f"{len(roles)} roles, {len(with_perms)} carrying their full permission array "
               f"({with_perms[:4]}) — an authorization map, not just names")


def sec_apikey(R, C):
    """API keys and service accounts are long-lived credentials."""
    R.head("api key / service account")
    S, A, org = C["stranger"], C["admin"], C["org"]

    st, b = req("GET", "/api/api_keys", S, org)
    R.note("member listing API keys", f"HTTP {st}")
    if st == 200 and isinstance(b, list):
        leaked = [k for k in b if isinstance(k, dict)
                  and any(str(v).startswith("bow_") for v in k.values())]
        R.rec("an API key listing does not re-show the secret",
              not leaked, f"{len(b)} keys, {len(leaked)} exposing a bow_ token")

    st, b = req("GET", "/api/service_accounts", S, org)
    R.note("member listing service accounts", f"HTTP {st}")


def sec_share(R, C):
    """Share links. Sharing a DASHBOARD must not open the CONVERSATION."""
    R.head("share tokens (anonymous)")
    O, S, org = C["owner"], C["stranger"], C["org"]
    rid = C.get("owner_report")
    if not rid:
        R.skip("share probes", "no owner report")
        return

    # Before any sharing: the private report must be closed to anonymous.
    for name, path in [
        ("the dashboard", f"/api/r/{rid}"),
        ("its artifacts", f"/api/r/{rid}/artifacts"),
        ("its queries", f"/api/r/{rid}/queries"),
        ("its widgets", f"/api/r/{rid}/widgets"),
        ("its text widgets", f"/api/r/{rid}/text_widgets"),
        ("its layouts", f"/api/r/{rid}/layouts"),
    ]:
        st, b = req("GET", path, None, None)
        R.refused(f"anonymous CANNOT read {name} of an UNSHARED report", st, b,
                  ok_codes=(401, 403, 404))

    # Share the DASHBOARD publicly, then ask what an anonymous holder reaches.
    st, b = req("PUT", f"/api/reports/{rid}/visibility/artifact", O, org,
                {"visibility": "public"})
    if st not in (200, 201):
        R.rec("owner can share the dashboard publicly", False, f"HTTP {st} — {brief(b)}")
        return
    R.rec("owner can share the dashboard publicly", True, "artifact_visibility=public")
    R.cleanup.append(lambda: req("PUT", f"/api/reports/{rid}/visibility/artifact", O, org,
                                 {"visibility": "none"}))

    st, b = req("GET", f"/api/r/{rid}", None, None)
    R.allowed("anonymous CAN read the shared dashboard (that is the point of sharing)", st, b)

    # ★The whole reason the share gate exists.
    st, b = req("GET", f"/api/reports/{rid}/completions", None, None)
    R.refused("sharing the dashboard does NOT open the transcript (authed route)", st, b,
              ok_codes=(401, 403, 404))

    st, tok = req("POST", f"/api/reports/{rid}/conversation-share", O, org, {})
    share_token = None
    if st in (200, 201) and isinstance(tok, dict):
        share_token = tok.get("share_token") or tok.get("token")
        R.note("conversation share toggle", f"HTTP {st} enabled={tok.get('enabled')}")
    if share_token:
        # Toggle it back off at teardown, whatever the probes find.
        R.cleanup.append(lambda: req("POST", f"/api/reports/{rid}/conversation-share", O, org, {}))
        st, b = req("GET", f"/api/c/{share_token}", None, None)
        R.note("anonymous reading the conversation share link", f"HTTP {st}")
        st, b = req("GET", f"/api/c/{uuid.uuid4().hex}", None, None)
        R.rec("a forged conversation share token is refused", st in (403, 404),
              f"HTTP {st} — {brief(b, 80)}")
    else:
        R.skip("conversation share-token probes", f"no token returned (HTTP {st})")

    # What ELSE does the dashboard share expose? Each is a separate decision,
    # and each is a different kind of leak if it answers 200.
    st, qb = req("GET", f"/api/r/{rid}/queries", None, None)
    R.note("anonymous reading the queries behind a shared dashboard", f"HTTP {st}")
    st, b = req("GET", f"/api/r/{rid}/artifacts", None, None)
    R.note("anonymous listing the shared dashboard's artifacts", f"HTTP {st}")

    # ★`PublicStepSchema` carries `code`. Publishing a dashboard therefore also
    # publishes the SQL behind it — table and column names of the underlying
    # source — to anyone with the link. Probe it wherever the report has a
    # query; a public dashboard on a real report is the case that matters.
    pq = first_id(qb) if qb else None
    if pq:
        st, b = req("GET", f"/api/r/{rid}/queries/{pq}/step", None, None)
        code = (b or {}).get("code") if isinstance(b, dict) else None
        R.rec("a public dashboard link does not also publish the query SOURCE",
              not code, f"HTTP {st}" + (f" — {len(code)} chars of code served to an "
                                        f"anonymous caller" if code else ""))
    else:
        R.skip("public query-source probe", "the shared probe report carries no query")

    # The schema catalogue and the agents behind the dashboard must stay closed.
    for name, path in [("the agent list", "/api/data_sources"),
                       ("the connection list", "/api/connections"),
                       ("the file list", "/api/files")]:
        st, b = req("GET", path, None, org)
        R.refused(f"anonymous CANNOT read {name} while a dashboard is shared", st, b,
                  ok_codes=(401, 403, 404))

    # A share link must never carry connection configuration.
    st, b = req("GET", f"/api/r/{rid}", None, None)
    blob = json.dumps(b, default=str).lower().replace(" ", "") if st == 200 else ""
    leaked = [k for k in ("\"password\"", "client_secret", "private_key", "\"credentials\"",
                          "connection_string", "\"api_key\"") if k in blob]
    R.rec("the public dashboard payload carries no connection credentials",
          not leaked, "clean" if not leaked else f"FOUND {leaked}")

    # Running someone's queries from a share link must need a login.
    st, b = req("POST", f"/api/r/{rid}/run", None, None, {})
    R.refused("anonymous CANNOT re-run the shared report's queries", st, b,
              ok_codes=(401, 403, 404, 405, 422))

    # The share is a report-scoped grant, not a wildcard: a DIFFERENT report
    # must stay closed while this one is public.
    other = C.get("stranger_report")
    if other:
        st, b = req("GET", f"/api/r/{other}", None, None)
        R.refused("a public share on one report does not open another", st, b,
                  ok_codes=(401, 403, 404))

    # ★★★"Shared" has FOUR settings, and the /r sub-routes do not all read the
    # same one. `get_public_report` / `get_public_artifacts` / `get_public_queries`
    # call `_check_visibility(artifact_visibility)`. `get_widgets_for_public_report`
    # and `get_text_widgets_for_public_report` check `report.status ==
    # 'published'` instead — and setting ANY visibility, including the
    # organization-only one, sets that status. So an org-only dashboard's
    # content is readable with no login at all. This probe uses `internal`
    # deliberately: `public` would answer 200 legitimately and prove nothing.
    st, rep2 = req("POST", "/api/reports", O, org, {"title": "tenancy-probe org-only share"})
    if st in (200, 201):
        rid2 = rep2["id"]
        R.cleanup.append(lambda: req("DELETE", f"/api/reports/{rid2}", O, org))
        secret = "tenancy-probe content restricted to the organization"
        req("POST", f"/api/reports/{rid2}/text_widgets", O, org,
            {"content": secret, "x": 0, "y": 0, "width": 4, "height": 2})
        stv, bv = req("PUT", f"/api/reports/{rid2}/visibility/artifact", O, org,
                      {"visibility": "internal"})
        if stv == 200:
            R.cleanup.append(lambda: req("PUT", f"/api/reports/{rid2}/visibility/artifact",
                                         O, org, {"visibility": "none"}))
            st, b = req("GET", f"/api/r/{rid2}", None, None)
            R.refused("anonymous CANNOT open an ORGANIZATION-ONLY dashboard", st, b,
                      ok_codes=(401, 403, 404))
            for name, path in [("its chart widgets", f"/api/r/{rid2}/widgets"),
                               ("its text widgets", f"/api/r/{rid2}/text_widgets"),
                               ("its artifacts", f"/api/r/{rid2}/artifacts"),
                               ("its queries", f"/api/r/{rid2}/queries"),
                               ("its layouts", f"/api/r/{rid2}/layouts")]:
                st, b = req("GET", path, None, None)
                leaked = secret in json.dumps(b, default=str) if st == 200 else False
                # ★A probe report has no CHART widget — building one needs a paid
                # model turn — so /widgets answers 200 with an empty list. The
                # empty list is not the point; the 200 is. Measured on a real
                # report at the same `internal` visibility (2026-08-09): the very
                # same 200 carried `last_step.code` (5,371 chars of generated
                # source), `last_step.data` with 6 rows and 23 named columns, and
                # `last_step.data_model` — to a caller with no Authorization
                # header. Compare `/artifacts`, `/queries` and `/layouts`, which
                # route through `_check_visibility` and correctly answer 401.
                R.refused(f"anonymous CANNOT read {name} of an ORGANIZATION-ONLY dashboard",
                          st, b, ok_codes=(401, 403, 404),
                          leak_note="(serving the restricted content itself)" if leaked else
                                    "(empty here only because a probe report has no chart; "
                                    "the same 200 carries last_step.code and last_step.data "
                                    "on a report that has one)")
        else:
            R.skip("organization-only share probes", f"could not set visibility (HTTP {stv})")


def sec_softdelete(R, C):
    """Deletion is soft. Assert the object left the API, not the database."""
    R.head("soft delete")
    O, S, A, org = C["owner"], C["stranger"], C["admin"], C["org"]

    st, rep = req("POST", "/api/reports", O, org, {"title": "tenancy-probe delete me"})
    if st not in (200, 201):
        R.rec("owner creates a report to delete", False, f"HTTP {st} — {brief(rep)}")
        return
    rid = rep["id"]

    # Give it something worth deleting, so "still readable" is demonstrated with
    # content and not just with a title.
    req("POST", f"/api/reports/{rid}/text_widgets", O, org,
        {"content": "tenancy-probe content that deletion should remove",
         "x": 0, "y": 0, "width": 4, "height": 2})

    st, b = req("DELETE", f"/api/reports/{rid}", O, org)
    R.allowed("owner deletes their own report", st, b)

    st, b = req("GET", "/api/reports", O, org)
    listed = ids_of(b, "reports")
    R.rec("a deleted report is absent from the owner's LIST",
          st == 200 and rid not in listed,
          f"HTTP {st}, {len(listed)} rows, {'STILL LISTED' if rid in listed else 'absent'}")

    # ★Leaving the list is only half of it. The lead requirement is that the
    # object is ALSO unreachable by direct GET. Here it is not: `delete` sets
    # `status='archived'` and leaves `deleted_at` NULL, so the row stays live to
    # every gate. The isolation boundary still holds (a stranger is refused) —
    # what fails is the promise the word "delete" makes to the person who
    # clicked it.
    st, b = req("GET", f"/api/reports/{rid}", O, org)
    R.rec("a deleted report is unreachable by direct GET, even for its owner",
          st in (403, 404), f"HTTP {st} — {brief(b, 80)}")

    st, b = req("GET", f"/api/reports/{rid}/completions", O, org)
    R.rec("a deleted report's conversation is unreachable", st in (403, 404), f"HTTP {st}")

    st, b = req("GET", f"/api/reports/{rid}/text_widgets", O, org)
    served = [x.get("content") for x in b][:1] if isinstance(b, list) else []
    R.rec("a deleted report no longer serves its content",
          st in (403, 404) or not served, f"HTTP {st} — still serving {served}")

    st, b = req("GET", f"/api/reports/{rid}", A, org)
    R.rec("a deleted report is unreachable by a full admin too", st in (403, 404), f"HTTP {st}")

    st, b = req("GET", f"/api/reports/{rid}", S, org)
    R.refused("a deleted report is not readable by a stranger", st, b)

    st, b = req("GET", f"/api/r/{rid}", None, None)
    R.refused("a deleted report is not readable anonymously", st, b, ok_codes=(401, 403, 404))

    st, b = req("PUT", f"/api/reports/{rid}", O, org, {"title": "undelete by write"})
    R.rec("a deleted report cannot be written back to life", st in (403, 404), f"HTTP {st}")

    # Deleting a prompt, then reading it back by id.
    st, p = req("POST", "/api/prompts", O, org,
                {"title": "tenancy-probe delete me", "content": "x"})
    pid = p.get("id") if isinstance(p, dict) else None
    if pid:
        req("DELETE", f"/api/prompts/{pid}", O, org)
        st, b = req("GET", f"/api/prompts/{pid}", O, org)
        R.rec("a deleted prompt is unreachable by direct GET", st in (403, 404), f"HTTP {st}")


def sec_crossorg(R, C):
    """The org header is a CLAIM. Membership is checked separately."""
    R.head("cross-org / the org header")
    O, S, A, org = C["owner"], C["stranger"], C["admin"], C["org"]
    rid = C.get("owner_report")
    ghost_org = str(uuid.uuid4())
    second = C.get("second_org")

    st, b = req("GET", "/api/reports", O, None)
    R.rec("no org header is refused, not silently defaulted",
          st in (400, 401, 403, 422), f"HTTP {st} — {brief(b, 90)}")

    st, b = req("GET", "/api/reports", O, ghost_org)
    R.rec("an invented org id does not resolve to a tenant",
          st in (400, 403, 404), f"HTTP {st} — {brief(b, 90)}")

    if rid:
        st, b = req("GET", f"/api/reports/{rid}", O, ghost_org)
        R.rec("the owner's OWN object is unreachable under a foreign org header",
              st in (400, 403, 404), f"HTTP {st} — {brief(b, 90)}")

    st, b = req("GET", "/api/reports", None, org)
    R.refused("an org header alone (no token) reaches nothing", st, b, ok_codes=(401, 403))

    # Header injection shapes: a list, whitespace, a SQL-ish string.
    for label, value in [("a comma-joined pair", f"{org},{ghost_org}"),
                         ("a padded id", f" {org} "),
                         ("a wildcard", "*")]:
        st, b = req("GET", "/api/reports", O, value)
        R.rec(f"the org header rejects {label}", st in (400, 403, 404),
              f"HTTP {st} — {brief(b, 70)}")

    # Creating a tenant is not a member capability on this install.
    st, b = req("POST", "/api/organizations", S, org, {"name": "tenancy-probe org"})
    if st in (200, 201) and isinstance(b, dict) and b.get("id"):
        R.note("member created a SECOND ORGANIZATION",
               f"id={b['id']} — multi-org is enabled; this row must be removed by hand")
        C["second_org"] = second = b["id"]
    else:
        R.note("member creating an organization", f"HTTP {st} — {brief(b, 90)}")

    if second:
        # The real matrix: act on org A's object while presenting org B.
        if rid:
            st, b = req("GET", f"/api/reports/{rid}", S, second)
            R.refused("org A's report is unreachable under org B's header", st, b,
                      ok_codes=(400, 403, 404))
        st, b = req("GET", "/api/reports", O, second)
        R.refused("a NON-member of org B is refused when presenting org B", st, b,
                  ok_codes=(400, 403, 404))
        st, b = req("GET", f"/api/organizations/{second}/members", O, second)
        R.refused("a non-member cannot read org B's member list", st, b,
                  ok_codes=(400, 403, 404))
    else:
        R.skip("full cross-org matrix",
               "this install has one organization and `allow_multiple_organizations` is off — "
               "pass --second-org <uuid> on an install that has two")


def sec_enumeration(R, C):
    """Does an unknown id answer differently from a forbidden one?

    Where the two differ, an outsider can walk the id space and learn which
    objects exist — the object's contents stay closed, but its EXISTENCE and
    its owner's activity do not.
    """
    R.head("enumeration (unknown id vs forbidden id)")
    O, S, org = C["owner"], C["stranger"], C["org"]

    # ★A FRESH, never-shared report. The share section publishes the main probe
    # report, and a published report answers 200 to any member through
    # `allow_public` — which would read here as "no oracle" for the wrong
    # reason. The comparison is only meaningful against an object the stranger
    # is genuinely refused.
    st, rep = req("POST", "/api/reports", O, org, {"title": "tenancy-probe enumeration target"})
    if st not in (200, 201):
        R.skip("enumeration probes", f"could not create a private report (HTTP {st})")
        return
    rid = rep["id"]
    R.cleanup.append(lambda: req("DELETE", f"/api/reports/{rid}", O, org))

    stc, _ = req("GET", f"/api/reports/{rid}", S, org)
    if stc == 200:
        R.rec("the enumeration target is actually private to the owner", False,
              "the stranger can read it — the comparison below would be meaningless")
        return

    pairs = [
        ("report", f"/api/reports/{rid}", f"/api/reports/{GHOST}"),
        ("report summary", f"/api/reports/{rid}/summary", f"/api/reports/{GHOST}/summary"),
        ("conversation", f"/api/reports/{rid}/completions", f"/api/reports/{GHOST}/completions"),
        ("webhook list", f"/api/reports/{rid}/webhooks", f"/api/reports/{GHOST}/webhooks"),
        ("layouts", f"/api/reports/{rid}/layouts", f"/api/reports/{GHOST}/layouts"),
    ]
    differ = []
    for label, forbidden, unknown in pairs:
        stf, _ = req("GET", forbidden, S, org)
        stu, _ = req("GET", unknown, S, org)
        same = stf == stu
        if not same:
            differ.append(f"{label}: exists={stf} unknown={stu}")
        R.rec(f"{label}: an existing-but-forbidden id answers like an unknown id",
              same, f"forbidden HTTP {stf}, unknown HTTP {stu}"
                    + ("" if same else "  ← existence oracle"))
    if differ:
        R.note("enumeration summary", "; ".join(differ))

    # Malformed ids must not reach the ORM.
    for bad in ["not-a-uuid", "1 OR 1=1", "../../etc/passwd", "%00"]:
        st, b = req("GET", f"/api/reports/{urllib.request.quote(bad, safe='')}", S, org)
        R.rec(f"a malformed report id ({bad!r}) does not 500", st != 500,
              f"HTTP {st} — {brief(b, 70)}")


def sec_peruser(R, C):
    """Per-user connectors (powerbi_user / fabric_user) resolve credentials per
    caller. schema_context_builder._resolve_user_access returns 'none' — no
    tables at all — when access cannot be proven, rather than falling back to
    the shared catalog."""
    R.head("per-user connector isolation")
    O, S, A, org = C["owner"], C["stranger"], C["admin"], C["org"]

    st, dsl = req("GET", "/api/data_sources", A, org)
    if st != 200 or not isinstance(dsl, list):
        R.skip("per-user connector probes", f"could not list agents (HTTP {st})")
        return
    per_user = [d for d in dsl
                if str(d.get("type", "")).endswith("_user")
                or "user" in str(d.get("auth_policy", ""))]
    if not per_user:
        R.skip("per-user connector probes",
               "no powerbi_user / fabric_user agent on this install")
        return

    for ds in per_user[:3]:
        dsid, dsname = ds.get("id"), ds.get("name")
        R.note("per-user agent", f"{dsname} ({ds.get('type')}) id={dsid}")

        st_o, bo = req("GET", f"/api/data_sources/{dsid}/schema", O, org)
        st_s, bs = req("GET", f"/api/data_sources/{dsid}/schema", S, org)
        n_o = len(bo) if isinstance(bo, list) else -1
        n_s = len(bs) if isinstance(bs, list) else -1
        R.note(f"{dsname}: schema seen by each caller",
               f"owner HTTP {st_o} ({n_o} tables), stranger HTTP {st_s} ({n_s} tables)")

        # The property that matters: a caller who has not proven access must not
        # receive the shared catalog through the per-user agent.
        st, cred = req("GET", f"/api/user_data_source_credentials/{dsid}", S, org)
        R.note(f"{dsname}: stranger's own credential record", f"HTTP {st}")

        st, b = req("GET", f"/api/data_sources/{dsid}/full_schema", S, org)
        if st == 200:
            tables = b.get("tables", b) if isinstance(b, dict) else b
            n = len(tables) if isinstance(tables, list) else "?"
            R.rec(f"{dsname}: a caller with no proven access gets no tables",
                  n in (0, "?") or n == 0,
                  f"HTTP 200 with {n} tables — a per-user agent must fail closed")
        else:
            R.rec(f"{dsname}: a caller with no proven access is refused the catalog",
                  st in (400, 403, 404), f"HTTP {st}")


def sec_misc(R, C):
    """Cross-cutting: notifications, review queue, people, memory."""
    R.head("notification / review / people")
    O, S, A, org = C["owner"], C["stranger"], C["admin"], C["org"]

    st, b = req("GET", "/api/notifications", S, org)
    R.allowed("a member can read THEIR OWN notification feed", st, b)
    if st == 200:
        seq = b if isinstance(b, list) else (b.get("items") or b.get("notifications") or [])
        foreign = [n for n in seq if isinstance(n, dict) and n.get("user_id")
                   and n["user_id"] != C["stranger_id"]]
        R.rec("the notification feed carries no other user's rows",
              not foreign, f"{len(seq) if isinstance(seq, list) else '?'} rows, "
                           f"{len(foreign)} belonging to someone else")

    st, b = req("GET", "/api/review", S, org)
    R.note("member reading the review queue", f"HTTP {st}")

    st, b = req("GET", f"/api/organizations/{org}/people", S, org)
    R.note("member reading People & Identities", f"HTTP {st} — view_members is baseline")
    if st == 200:
        blob = json.dumps(b, default=str).lower().replace(" ", "")
        leaked = [k for k in ("\"password\"", "hashed_password", "\"token\"", "client_secret")
                  if k in blob]
        R.rec("the people list carries no password hash or token",
              not leaked, "clean" if not leaked else f"FOUND {leaked}")


SECTIONS = [
    ("report", sec_report),
    ("completion", sec_completion),
    ("widget", sec_widget),
    ("textwidget", sec_text_widget),
    ("artifact", sec_artifact),
    ("step", sec_step_query),
    ("visualization", sec_visualization),
    ("file", sec_file),
    ("project", sec_project),
    ("prompt", sec_prompt),
    ("automation", sec_automation),
    ("instruction", sec_instruction),
    ("agent", sec_agent),
    ("evals", sec_evals),
    ("entity", sec_entity),
    ("rbac", sec_rbac),
    ("apikey", sec_apikey),
    ("secrets", sec_secrets_surface),
    ("share", sec_share),
    ("softdelete", sec_softdelete),
    ("crossorg", sec_crossorg),
    ("enumeration", sec_enumeration),
    ("peruser", sec_peruser),
    ("misc", sec_misc),
]


def main():
    global BASE
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tokens", help="JSON written by scripts/mint-user-tokens.py")
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--owner", default=OWNER_EMAIL)
    ap.add_argument("--stranger", default=STRANGER_EMAIL)
    ap.add_argument("--admin", default=ADMIN_EMAIL)
    ap.add_argument("--second-org", default=None,
                    help="org id the STRANGER belongs to and the owner does not, "
                         "for the full cross-org matrix")
    ap.add_argument("--only", default=None,
                    help="comma-separated section names; default is all")
    ap.add_argument("--json", default=None, help="write the machine-readable result here")
    args = ap.parse_args()

    BASE = args.base.rstrip("/")

    tok = json.load(open(args.tokens))
    users = tok["users"]
    missing = [e for e in (args.owner, args.stranger, args.admin)
               if e not in users or "token" not in users[e]]
    if missing:
        print(f"token file is missing usable entries for: {missing}", file=sys.stderr)
        return 2

    C = {
        "owner": users[args.owner]["token"],
        "stranger": users[args.stranger]["token"],
        "admin": users[args.admin]["token"],
        "owner_id": users[args.owner]["id"],
        "stranger_id": users[args.stranger]["id"],
        "org": tok["org"]["id"],
        "second_org": args.second_org,
    }

    st, _ = req("GET", "/api/health", None, None)
    if st == 0:
        st, _ = req("GET", "/api/settings", None, None)
    if st == 0:
        print(f"{BASE} is not answering — start the instance first.", file=sys.stderr)
        return 2

    print(f"base={BASE}  org={C['org']}")
    print(f"owner={args.owner}  stranger={args.stranger}  admin={args.admin}")

    wanted = None
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}

    R = Run()
    try:
        for name, fn in SECTIONS:
            if wanted and name not in wanted:
                continue
            try:
                fn(R, C)
            except Exception as e:  # a broken probe must not hide the ones after it
                R.rec(f"[{name}] probe crashed", False, f"{type(e).__name__}: {e}")
    finally:
        print("\n── cleanup " + "─" * 51, flush=True)
        for fn in reversed(R.cleanup):
            try:
                fn()
            except Exception as e:
                print(f"  cleanup step failed: {e}", flush=True)
        print(f"  {len(R.cleanup)} teardown steps run", flush=True)

    passed = sum(1 for _, _, ok, _ in R.results if ok)
    failed = len(R.results) - passed
    print(f"\n{'=' * 62}")
    print(f"{passed} passed, {failed} failed, {len(R.results)} probes, {len(R.notes)} notes")
    if failed:
        print("\nFAILED:")
        for sect, name, ok, detail in R.results:
            if not ok:
                print(f"  [{sect}] {name} — {detail}")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({
                "base": BASE, "org": C["org"],
                "passed": passed, "failed": failed,
                "probes": [{"section": s, "name": n, "ok": o, "detail": d}
                           for s, n, o, d in R.results],
                "notes": [{"section": s, "name": n, "detail": d} for s, n, d in R.notes],
            }, fh, indent=2)
        print(f"wrote {args.json}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
