"""Phase E — three users, three different databases, at the same instant.

Phase C measured each backend alone. This asks the question a real workspace
actually poses: when someone is querying the DuckDB agent, someone else the
Power BI model and a third the Fabric lakehouse — do they interfere?

WHAT INTERFERENCE WOULD LOOK LIKE

  serialisation   wall clock ≈ sum of the three, instead of ≈ the slowest.
                  Means they queued behind one another.
  contagion       the LOCAL DuckDB turn slowing down because two remote OAuth
                  turns are in flight. That would point at a shared pool or a
                  blocked loop rather than at any one connector.
  auth stampede   both user_required connectors resolving per-user credentials
                  at once. This is the pair most likely to contend, because
                  they take the same code path (schema_context_builder's
                  per-user overlay) even though they talk to different clouds.

★Each user is pinned to ONE database. Left on Auto the agent could pick any of
them and the measurement would describe the picker, not the backends.

★Baseline first, run alone, so "slower under load" has something to be slower
than. Without it a number is just a number.
"""
import json
import os
import statistics
import sys
import threading
import time
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "_um", os.path.join(os.path.dirname(os.path.abspath(__file__)), "user-multi.py"))
_um = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_um)
req = _um.req

QUESTION = "What are the top 3 values by total revenue? Give a short list."
# ★Must stay ABOVE the server's query_hard_timeout_seconds (default 900,
# backend/app/schemas/organization_settings_schema.py:458) — that is the only
# thing that kills a query, and a harness that gives up at the same instant
# cannot tell "the product killed it" from "I stopped watching". Not a round 900.
DEADLINE = 960


def one_turn(token, org, agent_id, tag, out):
    st, rep = req("POST", "/api/reports", token, org, {"title": f"concdb {tag}"})
    if st not in (200, 201):
        out[tag] = {"error": f"report HTTP {st}"}
        return
    rid = rep["id"]
    t0 = time.time()
    req("POST", f"/api/reports/{rid}/completions", token, org,
        {"prompt": {"content": QUESTION, "mentions": []},
         "background": True, "data_source_ids": [agent_id]})
    status = "timeout"
    while time.time() - t0 < DEADLINE:
        time.sleep(3)
        s, b = req("GET", f"/api/reports/{rid}/completions", token, org)
        rows = b if isinstance(b, list) else b.get("completions", [])
        ag = [r for r in rows if r.get("role") != "user"]
        # ★Wait for EVERY agent row to be terminal, not the first one. Breaking
        # on the first is a race: later rows arrive and the turn is still going,
        # which under-reports the duration.
        if ag and all(r.get("status") in ("success", "error", "stopped") for r in ag):
            status = ag[-1].get("status")
            break
    out[tag] = {"secs": round(time.time() - t0, 1), "status": status}
    req("DELETE", f"/api/reports/{rid}", token, org)


def main():
    tok = json.load(open(sys.argv[1]))
    org = tok["org"]["id"]
    admin = tok["users"]["raahulgupta07@gmail.com"]["token"]
    people = ["raahulgupta07@gmail.com", "member@cityagent.io", "localtest@cityagent.io"]
    tokens = [tok["users"][p]["token"] for p in people]

    st, ds = req("GET", "/api/data_sources", admin, org)
    rows = ds if isinstance(ds, list) else ds.get("data", [])
    want = ["City Mart Retail", "Power BI", "Microsoft Fabric"]
    agents = [next((r for r in rows if r["name"] == w), None) for w in want]
    agents = [a for a in agents if a]
    if len(agents) < 3:
        print("could not find all three databases:", [a["name"] for a in agents])
        return 1

    # ── baselines, one at a time ─────────────────────────────────────────────
    print("baseline — each database alone:")
    base = {}
    for a in agents:
        out = {}
        one_turn(admin, org, a["id"], a["name"], out)
        base[a["name"]] = out[a["name"]]
        r = out[a["name"]]
        print(f"  {a['name']:<20} {r.get('secs','-'):>6}s  {r.get('status')}")

    # ── all three at once, one user each ─────────────────────────────────────
    print("\nconcurrent — three users, three databases, same instant:")
    out = {}
    threads = []
    t0 = time.time()
    for i, a in enumerate(agents):
        th = threading.Thread(target=one_turn,
                              args=(tokens[i % len(tokens)], org, a["id"], a["name"], out))
        th.start(); threads.append(th)
    for th in threads:
        th.join()
    wall = round(time.time() - t0, 1)

    for a in agents:
        r = out.get(a["name"], {})
        b = base.get(a["name"], {}).get("secs")
        s = r.get("secs")
        delta = f"{(s - b):+.1f}s vs alone" if (isinstance(s, float) and isinstance(b, float)) else ""
        print(f"  {a['name']:<20} {s:>6}s  {r.get('status'):<8} {delta}")

    solo_sum = sum(v.get("secs", 0) for v in base.values())
    slowest = max((v.get("secs", 0) for v in base.values()), default=0)
    print(f"\n  wall clock concurrent : {wall}s")
    print(f"  sum of the three alone: {solo_sum:.1f}s   (if they serialised, wall ≈ this)")
    print(f"  slowest of them alone : {slowest:.1f}s   (if truly parallel, wall ≈ this)")
    verdict = "PARALLEL" if wall < (solo_sum + slowest) / 2 else "SERIALISED"
    print(f"  -> {verdict}")

    with open("/tmp/perf-concurrent-db.json", "w") as fh:
        json.dump({"baseline": base, "concurrent": out, "wall": wall}, fh, indent=2)


if __name__ == "__main__":
    sys.exit(main() or 0)
