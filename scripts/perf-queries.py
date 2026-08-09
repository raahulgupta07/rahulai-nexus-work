"""Phase C+D — query performance and tool usage, per database, easy to complex.

Three backends that are structurally different, which is the whole point:

  City Mart Retail  duckdb        system_only    local file, no auth hop
  Power BI          powerbi_user  user_required  OAuth per user, remote semantic model
  Microsoft Fabric  fabric_user   user_required  OAuth per user, remote lakehouse

The same question costs different things on each, and the difference is not the
model — it is the connector, the auth hop, and how much schema has to be
rendered into the prompt before the model sees anything.

TIERS, deliberately ordered so a failure at T1 makes T3 meaningless

  T1 trivial     no data access at all — isolates model+transport latency
  T2 schema      "what tables do you have" — the first thing every turn needs
  T3 count       one aggregate, one table
  T4 groupby     aggregate + sort + limit
  T5 join        two tables
  T6 chart       analysis that must also produce a widget

★Measured per turn: wall time, tool calls actually fired, step count, and the
answer's length. The tool list is the interesting part — the same question can
be answered with one `create_data` or with four, and that difference is invisible
in a wall-clock number alone.

★Each tier runs in its OWN report. Reusing one report would let earlier context
make later questions cheaper, which flatters the numbers and hides regressions.

Usage:  python perf-queries.py /tmp/tokens.json [--tiers T1,T3] [--agents "City Mart Retail"]
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("BASE_URL", "http://localhost:8095")
DEADLINE = int(os.environ.get("PERF_DEADLINE", "600"))


def req(method, path, token, org, body=None):
    headers = {"Authorization": f"Bearer {token}", "X-Organization-Id": org}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=DEADLINE + 60) as resp:
            txt = resp.read().decode()
            return resp.status, (json.loads(txt) if txt.strip().startswith(("{", "[")) else txt)
    except urllib.error.HTTPError as e:
        txt = e.read().decode()
        try:
            return e.code, json.loads(txt)
        except Exception:
            return e.code, txt
    except Exception as e:
        return 0, str(e)


TIERS = [
    ("T1", "trivial",  "Reply with exactly one word: ready"),
    ("T2", "schema",   "List the tables you can see. Names only."),
    ("T3", "count",    "How many rows are in the largest table? Just the number."),
    ("T4", "groupby",  "What are the top 5 values by total revenue? Give a short list."),
    ("T5", "join",     "Join the main fact table to a dimension and give me one combined figure."),
    ("T6", "chart",    "Show total revenue by category as a bar chart."),
]


def run_turn(token, org, agent_id, agent_name, prompt):
    """One question, in its own report. Returns a measurement dict."""
    st, rep = req("POST", "/api/reports", token, org,
                  {"title": f"perf · {agent_name[:20]} · {int(time.time())%100000}"})
    if st not in (200, 201):
        return {"error": f"report create HTTP {st}"}
    rid = rep["id"]

    # Pin the question to ONE agent, so the number describes that backend and
    # not whatever Auto happened to pick.
    body = {"prompt": {"content": prompt, "mentions": []},
            "background": True,
            "data_source_ids": [agent_id]}
    t0 = time.time()
    st, _ = req("POST", f"/api/reports/{rid}/completions", token, org, body)
    if st not in (200, 201):
        req("DELETE", f"/api/reports/{rid}", token, org)
        return {"error": f"completion POST HTTP {st}"}

    status, tools, steps, chars, blocks = "timeout", [], 0, 0, 0
    while time.time() - t0 < DEADLINE:
        time.sleep(2)
        s, body2 = req("GET", f"/api/reports/{rid}/completions", token, org)
        rows = body2 if isinstance(body2, list) else body2.get("completions", [])
        agent_rows = [r for r in rows if r.get("role") != "user"]
        # ★EVERY agent row must be terminal, not just the first one. Breaking on
        # the first is a race: later rows are still arriving while the turn runs,
        # so the wall time describes a fragment of the turn and reads as fast.
        if agent_rows and all(
                r.get("status") in ("success", "error", "stopped") for r in agent_rows):
            last = agent_rows[-1]
            status = last.get("status")
            blob = json.dumps(last)
            for t in ("create_data", "create_artifact", "edit_artifact", "read_file",
                      "create_dashboard", "read_query", "search_instructions",
                      "create_doc", "get_connection", "list_connections"):
                if f'"{t}"' in blob:
                    tools.append(t)
            steps = blob.count('"step_id"')
            blocks = len(last.get("blocks") or []) if isinstance(last.get("blocks"), list) else 0
            chars = len(str(last.get("content") or blob))
            break
    secs = round(time.time() - t0, 1)
    req("DELETE", f"/api/reports/{rid}", token, org)
    return {"secs": secs, "status": status, "tools": sorted(set(tools)),
            "steps": steps, "blocks": blocks, "chars": chars}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tokens")
    ap.add_argument("--tiers", default="")
    ap.add_argument("--agents", default="")
    args = ap.parse_args()

    tok = json.load(open(args.tokens))
    org = tok["org"]["id"]
    token = tok["users"]["raahulgupta07@gmail.com"]["token"]

    st, ds = req("GET", "/api/data_sources", token, org)
    agents = ds if isinstance(ds, list) else ds.get("data", [])
    if args.agents:
        want = [a.strip() for a in args.agents.split(",")]
        agents = [a for a in agents if a.get("name") in want]
    tiers = TIERS
    if args.tiers:
        want = [t.strip() for t in args.tiers.split(",")]
        tiers = [t for t in TIERS if t[0] in want]

    print(f"{'agent':<20} {'type':<14} {'tier':<5} {'secs':>7} {'status':<9} "
          f"{'steps':>5} {'chars':>6}  tools")
    print("-" * 100)

    results = []
    for a in agents:
        conn = (a.get("connections") or [{}])[0]
        for code, label, prompt in tiers:
            m = run_turn(token, org, a["id"], a.get("name", "?"), prompt)
            m.update({"agent": a.get("name"), "type": conn.get("type"),
                      "auth": conn.get("auth_policy"), "tier": code, "label": label})
            results.append(m)
            if "error" in m:
                print(f"{a.get('name','?'):<20} {str(conn.get('type')):<14} {code:<5} "
                      f"{'-':>7} {'ERROR':<9} {'-':>5} {'-':>6}  {m['error']}")
            else:
                print(f"{a.get('name','?'):<20} {str(conn.get('type')):<14} {code:<5} "
                      f"{m['secs']:>7} {m['status']:<9} {m['steps']:>5} {m['chars']:>6}  "
                      f"{','.join(m['tools']) or '-'}")
            sys.stdout.flush()

    with open("/tmp/perf-queries.json", "w") as fh:
        json.dump(results, fh, indent=2)

    # ── the comparison the whole exercise exists for ─────────────────────────
    print("\nper-database totals (successful turns only):")
    for a in agents:
        rows = [r for r in results if r.get("agent") == a.get("name")
                and r.get("status") == "success"]
        if not rows:
            print(f"  {a.get('name'):<20} no successful turns")
            continue
        times = [r["secs"] for r in rows]
        print(f"  {a.get('name'):<20} n={len(rows):<3} median {statistics.median(times):>6.1f}s  "
              f"total {sum(times):>7.1f}s  slowest {max(times):>6.1f}s")
    print("\nwrote /tmp/perf-queries.json")


if __name__ == "__main__":
    main()
