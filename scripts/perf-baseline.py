"""Phase A — baseline API performance, with no model in the loop.

Nothing here costs money. That is the point: it separates "the product is slow"
from "the model is slow", which are different problems with different fixes and
are routinely confused.

WHAT IS MEASURED

  latency      p50 / p95 / max per endpoint over N samples. p95 rather than mean,
               because a mean hides the tail and the tail is what users report.
  dedupe       upstream c6deecad, "Dedupe concurrent duplicate GETs; share the
               in-flight session fetch". Fire K identical GETs at the same
               instant: if they are shared, wall time ≈ one request, not K.
  schema       the per-database cost of answering "what tables do you have",
               which is the first thing every chat turn needs.

★Warm-up first, discarded. The first request to a route pays for imports, lazy
mappers and connection-pool creation; folding that into p50 makes every route
look slower than it is and hides real regressions behind one-off noise.
"""
import json
import statistics
import sys
import time
import urllib.request
import urllib.error
import os
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get("BASE_URL", "http://localhost:8095")


def get(path, token, org):
    req = urllib.request.Request(
        f"{BASE}{path}",
        headers={"Authorization": f"Bearer {token}", "X-Organization-Id": org},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read()
            return (time.time() - t0) * 1000, r.status, len(body)
    except urllib.error.HTTPError as e:
        return (time.time() - t0) * 1000, e.code, 0
    except Exception:
        return (time.time() - t0) * 1000, 0, 0


def pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100.0) * (len(xs) - 1)))))
    return xs[k]


def main():
    tok = json.load(open(sys.argv[1]))
    org = tok["org"]["id"]
    token = tok["users"]["raahulgupta07@gmail.com"]["token"]
    samples = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    endpoints = [
        ("settings (public)",      "/api/settings"),
        ("reports list (sidebar)", "/api/reports?filter=my&limit=50&view=minimal"),
        ("reports list (full)",    "/api/reports?limit=50"),
        ("agents list",            "/api/data_sources"),
        ("instructions list",      "/api/instructions"),
        ("llm models",             "/api/llm/models"),
        ("changelog",              "/api/changelog"),
    ]

    print(f"{'endpoint':<26} {'n':>3} {'p50':>8} {'p95':>8} {'max':>8} {'KB':>7}  status")
    print("-" * 78)
    rows = []
    for name, path in endpoints:
        get(path, token, org)          # warm-up, discarded on purpose
        times, status, size = [], None, 0
        for _ in range(samples):
            ms, st, n = get(path, token, org)
            times.append(ms); status = st; size = n
        rows.append((name, path, pct(times, 50), pct(times, 95), max(times), size, status))
        print(f"{name:<26} {samples:>3} {pct(times,50):>7.0f}m {pct(times,95):>7.0f}m "
              f"{max(times):>7.0f}m {size/1024:>6.1f}  {status}")

    # ── the sidebar payload question ─────────────────────────────────────────
    _, _, mini = get("/api/reports?filter=my&limit=50&view=minimal", token, org)
    _, _, full = get("/api/reports?limit=50", token, org)
    print(f"\nsidebar payload: view=minimal {mini/1024:.1f}KB vs full {full/1024:.1f}KB "
          f"({(1 - mini/max(full,1))*100:.0f}% smaller)")

    # ── dedupe of concurrent identical GETs (upstream c6deecad) ──────────────
    print("\nconcurrent identical GETs — if in-flight requests are shared,")
    print("wall time stays near a single request instead of scaling with K:")
    for k in (1, 5, 10):
        path = "/api/data_sources"
        get(path, token, org)
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=k) as ex:
            list(ex.map(lambda _: get(path, token, org), range(k)))
        wall = (time.time() - t0) * 1000
        print(f"  K={k:<3} wall {wall:>7.0f}ms   per-request {wall/k:>7.0f}ms")

    # ── schema cost per database ─────────────────────────────────────────────
    print("\nschema fetch per database (the first thing every chat turn needs):")
    _, _, _ = get("/api/data_sources", token, org)
    req = urllib.request.Request(f"{BASE}/api/data_sources",
                                 headers={"Authorization": f"Bearer {token}",
                                          "X-Organization-Id": org})
    with urllib.request.urlopen(req, timeout=120) as r:
        agents = json.loads(r.read().decode())
    agents = agents if isinstance(agents, list) else agents.get("data", [])
    for a in agents:
        ms, st, n = get(f"/api/data_sources/{a['id']}/schema", token, org)
        conn = (a.get("connections") or [{}])[0]
        print(f"  {a.get('name'):<22} {conn.get('type','?'):<14} "
              f"{conn.get('auth_policy','?'):<13} {ms:>7.0f}ms  HTTP {st}  {n/1024:.1f}KB")

    out = {"endpoints": [dict(zip(
        ["name", "path", "p50_ms", "p95_ms", "max_ms", "bytes", "status"], r)) for r in rows]}
    with open("/tmp/perf-baseline.json", "w") as fh:
        json.dump(out, fh, indent=2)
    print("\nwrote /tmp/perf-baseline.json")


if __name__ == "__main__":
    main()
