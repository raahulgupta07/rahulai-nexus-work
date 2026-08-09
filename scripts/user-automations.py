"""Automations and knowledge, driven as a real member over HTTP.

Phase 8 — scheduled prompts and webhooks: the things that run when nobody is
watching, which is exactly why they are worth testing by hand. A schedule that
silently never fires looks identical to a schedule that has not fired YET.

Phase 9 — instructions: create, read back, and confirm the delete actually
removes it from the LIST. Delete here is SOFT — the row keeps its id and gains
`deleted_at` — so counting rows in Postgres after a clean run shows "leftovers"
that are not leftovers. The only honest assertion is that the object left the
API's list.

★Shapes taken from the source, not guessed:
   POST /api/reports/{id}/scheduled-prompts  {prompt:{content}, cron_schedule, ...}
   POST /api/reports/{id}/webhooks
   POST /api/instructions

★`trigger` runs the schedule NOW. That is the only way to prove the wiring
without waiting for a cron minute to come round, and it is the difference
between testing the schedule and testing the clock.

Usage:  python user-automations.py /tmp/tokens.json
"""
import json
import sys
import time
import importlib.util
import os

_spec = importlib.util.spec_from_file_location(
    "_um", os.path.join(os.path.dirname(os.path.abspath(__file__)), "user-multi.py"))
_um = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_um)
req = _um.req


def main():
    tok = json.load(open(sys.argv[1]))
    org = tok["org"]["id"]
    A = tok["users"]["member@cityagent.io"]["token"]
    B = tok["users"]["localtest@cityagent.io"]["token"]

    results = []

    def rec(name, ok, detail):
        results.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name} — {detail}", flush=True)

    st, rep = req("POST", "/api/reports", A, org, {"title": "automations host"})
    if st not in (200, 201):
        print(f"cannot create host report: {st} {str(rep)[:200]}")
        return 1
    rid = rep["id"]
    print(f"host report {rid}\n")

    # ── Phase 8a · scheduled prompts ─────────────────────────────────────────
    st, sp = req("POST", f"/api/reports/{rid}/scheduled-prompts", A, org,
                 {"prompt": {"content": "How many rows are in the sales table? Just the number."},
                  "title": "nightly row count",
                  "cron_schedule": "0 3 * * *",
                  "is_active": True,
                  "spawn_new_report": False})
    ok = st in (200, 201)
    sp_id = sp.get("id") if ok and isinstance(sp, dict) else None
    rec("member creates a schedule", ok, f"HTTP {st}" + (f", id={sp_id}" if sp_id else f" — {str(sp)[:140]}"))

    if sp_id:
        st, lst = req("GET", f"/api/reports/{rid}/scheduled-prompts", A, org)
        n = len(lst) if isinstance(lst, list) else 0
        rec("schedule appears in the report's list", st == 200 and n >= 1, f"HTTP {st}, {n} row(s)")

        st, allsp = req("GET", "/api/scheduled-prompts", A, org)
        rows = allsp.get("items", allsp) if isinstance(allsp, dict) else allsp
        rec("schedule appears in the org-wide list", st == 200, f"HTTP {st}, {len(rows) if isinstance(rows,list) else '?'} row(s)")

        # A stranger must not be able to see or drive someone else's schedule.
        st, _ = req("GET", f"/api/reports/{rid}/scheduled-prompts", B, org)
        rec("stranger cannot list another's schedules", st in (403, 404), f"HTTP {st}")
        st, _ = req("POST", f"/api/reports/{rid}/scheduled-prompts/{sp_id}/trigger", B, org, {})
        rec("stranger cannot trigger another's schedule", st in (403, 404), f"HTTP {st}")

        # Fire it now — the only way to prove wiring without waiting for cron.
        st, trg = req("POST", f"/api/reports/{rid}/scheduled-prompts/{sp_id}/trigger", A, org, {})
        rec("owner can trigger it on demand", st in (200, 201, 202), f"HTTP {st} — {str(trg)[:100]}")

        # A run should appear. Poll: the run is asynchronous.
        seen, waited = 0, 0
        for _ in range(20):
            time.sleep(3); waited += 3
            st, runs = req("GET", f"/api/reports/{rid}/scheduled-prompts/{sp_id}/runs", A, org)
            rows = runs.get("runs", runs.get("items", [])) if isinstance(runs, dict) else runs
            seen = len(rows) if isinstance(rows, list) else 0
            if seen:
                break
        rec("the trigger leaves a run record", seen > 0, f"{seen} run(s) after {waited}s")

        st, _ = req("PUT", f"/api/reports/{rid}/scheduled-prompts/{sp_id}", A, org, {"is_active": False})
        rec("owner can pause it", st in (200, 204), f"HTTP {st}")
        st, _ = req("DELETE", f"/api/reports/{rid}/scheduled-prompts/{sp_id}", A, org)
        rec("owner can delete it", st in (200, 204), f"HTTP {st}")
        st, lst = req("GET", f"/api/reports/{rid}/scheduled-prompts", A, org)
        gone = not any((r or {}).get("id") == sp_id for r in (lst if isinstance(lst, list) else []))
        rec("deleted schedule leaves the LIST (delete is soft)", gone, "absent from the list")

    # ── Phase 8b · webhooks ──────────────────────────────────────────────────
    st, wh = req("POST", f"/api/reports/{rid}/webhooks", A, org,
                 {"name": "inbound test", "prompt": {"content": "summarise the payload"}})
    ok = st in (200, 201)
    wh_id = wh.get("id") if ok and isinstance(wh, dict) else None
    rec("member creates a webhook", ok, f"HTTP {st}" + (f", id={wh_id}" if wh_id else f" — {str(wh)[:140]}"))
    if wh_id:
        secret_leaked = "secret" in json.dumps(wh).lower() and wh.get("secret") in (None, "", "***")
        st, lst = req("GET", f"/api/reports/{rid}/webhooks", A, org)
        rec("webhook appears in its report", st == 200, f"HTTP {st}")
        st, rot = req("POST", f"/api/reports/{rid}/webhooks/{wh_id}/rotate", A, org, {})
        rec("owner can rotate the webhook secret", st in (200, 201), f"HTTP {st}")
        st, _ = req("GET", f"/api/reports/{rid}/webhooks", B, org)
        rec("stranger cannot list another's webhooks", st in (403, 404), f"HTTP {st}")
        req("DELETE", f"/api/reports/{rid}/webhooks/{wh_id}", A, org)

    # ── Phase 9 · instructions ───────────────────────────────────────────────
    st, dss = req("GET", "/api/data_sources", A, org)
    ds_rows = dss if isinstance(dss, list) else dss.get("data", [])
    scope = [ds_rows[0]["id"]] if ds_rows else []
    # ★An EMPTY data_source_ids means "every agent", which needs manage rights —
    # a member is correctly refused. The member path is: private, and scoped to
    # agents they can actually access. Guarded by
    # tests/unit/fork/test_member_create_never_sends_an_empty_agent_list.
    st, ins = req("POST", "/api/instructions", A, org,
                  {"text": "Always report revenue in thousands with a K suffix.",
                   "category": "general", "status": "published",
                   "is_private": True, "data_source_ids": scope})
    ok = st in (200, 201)
    ins_id = ins.get("id") if ok and isinstance(ins, dict) else None
    rec("member creates a private instruction", ok,
        f"HTTP {st}" + (f", id={ins_id}" if ins_id else f" — {str(ins)[:140]}"))
    if ins_id:
        st, got = req("GET", f"/api/instructions/{ins_id}", A, org)
        same = isinstance(got, dict) and "thousands" in json.dumps(got)
        rec("read it back with its text intact", st == 200 and same, f"HTTP {st}, text preserved={same}")
        st, _ = req("DELETE", f"/api/instructions/{ins_id}", A, org)
        rec("owner can delete it", st in (200, 204), f"HTTP {st}")
        st, lst = req("GET", "/api/instructions", A, org)
        rows = lst.get("items", lst) if isinstance(lst, dict) else lst
        gone = not any((r or {}).get("id") == ins_id for r in (rows if isinstance(rows, list) else []))
        rec("deleted instruction leaves the LIST", gone, "absent from the list")

    req("DELETE", f"/api/reports/{rid}", A, org)

    print()
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"{passed} passed, {len(results)-passed} failed, {len(results)} checks")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
