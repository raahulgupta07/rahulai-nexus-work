"""Phase B — instruction write paths, and the collateral damage they do.

Modelled on upstream's own harness (backend/scripts/measure_write_ops.py, which
this fork does not carry). Upstream shipped two fixes here that we inherited:

  41a298d2  Make instruction delete fast and stop it stalling the worker
  4f8d1ab1  instruction write-path performance work

WHY A CONCURRENT GET IS THE REAL MEASUREMENT

A slow DELETE is a slow DELETE — annoying for one person. But if the work runs
on the event loop rather than in a thread, then while it runs the worker cannot
serve ANYTHING, and a trivial GET from an unrelated user queues behind it. That
is the difference between "deleting is slow" and "the whole app froze", and a
serial benchmark cannot tell them apart.

So each write is timed twice over:
  - the write's own wall time
  - a cheap GET fired at the same instant, from a DIFFERENT user

If the cheap GET's latency tracks the write's duration, the loop is blocked.
If it stays near its idle p50, the write is properly off the hot path.

★A baseline for the cheap GET is measured first, while nothing else is running.
Without it there is no way to say whether 40ms is fine or terrible.
"""
import json
import os
import statistics
import sys
import time
import threading
import urllib.error
import urllib.request

BASE = os.environ.get("BASE_URL", "http://localhost:8095")


def req(method, path, token, org, body=None):
    headers = {"Authorization": f"Bearer {token}", "X-Organization-Id": org}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=300) as resp:
            txt = resp.read().decode()
            return (time.time() - t0) * 1000, resp.status, (
                json.loads(txt) if txt.strip().startswith(("{", "[")) else txt)
    except urllib.error.HTTPError as e:
        return (time.time() - t0) * 1000, e.code, e.read().decode()[:200]
    except Exception as e:
        return (time.time() - t0) * 1000, 0, str(e)


def pct(xs, p):
    xs = sorted(xs)
    return xs[max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))]


def main():
    tok = json.load(open(sys.argv[1]))
    org = tok["org"]["id"]
    admin = tok["users"]["raahulgupta07@gmail.com"]["token"]
    other = tok["users"]["localtest@cityagent.io"]["token"]   # the innocent bystander

    # scope: instructions must be attached to an agent the actor can manage
    _, _, ds = req("GET", "/api/data_sources", admin, org)
    agents = ds if isinstance(ds, list) else ds.get("data", [])
    scope = [agents[0]["id"]] if agents else []
    print(f"agent scope: {agents[0]['name'] if agents else 'none'}\n")

    # ── 1. baseline for the cheap GET, with nothing else running ─────────────
    CHEAP = "/api/settings"
    base = []
    for _ in range(25):
        ms, _, _ = req("GET", CHEAP, other, org)
        base.append(ms)
    b50, b95 = pct(base, 50), pct(base, 95)
    print(f"cheap GET idle baseline: p50 {b50:.0f}ms  p95 {b95:.0f}ms\n")

    # ── 2. create / save / delete, each with a concurrent bystander GET ──────
    def with_bystander(fn):
        """Run fn() while a different user fires the cheap GET repeatedly."""
        samples, stop = [], threading.Event()

        def poll():
            while not stop.is_set():
                ms, _, _ = req("GET", CHEAP, other, org)
                samples.append(ms)
        th = threading.Thread(target=poll); th.start()
        t0 = time.time()
        out = fn()
        dur = (time.time() - t0) * 1000
        stop.set(); th.join()
        return dur, out, samples

    print(f"{'operation':<28} {'own ms':>8}   bystander GET during it")
    print("-" * 72)

    created = []
    for i in range(5):
        def mk():
            return req("POST", "/api/instructions", admin, org,
                       {"text": f"perf probe {i}: report revenue in thousands.",
                        "category": "general", "status": "published",
                        "is_private": True, "data_source_ids": scope})
        dur, (ms, st, body), samples = with_bystander(mk)
        if st in (200, 201) and isinstance(body, dict):
            created.append(body["id"])
        tag = f"p50 {pct(samples,50):>5.0f}ms  max {max(samples):>6.0f}ms  n={len(samples)}" if samples else "-"
        print(f"{'create instruction #'+str(i+1):<28} {dur:>8.0f}   {tag}")

    for i, iid in enumerate(created):
        def save():
            return req("PUT", f"/api/instructions/{iid}", admin, org,
                       {"text": f"perf probe {i} edited: report revenue in thousands (K)."})
        dur, (ms, st, _), samples = with_bystander(save)
        tag = f"p50 {pct(samples,50):>5.0f}ms  max {max(samples):>6.0f}ms  n={len(samples)}" if samples else "-"
        print(f"{'save instruction #'+str(i+1):<28} {dur:>8.0f}   {tag}  HTTP {st}")

    for i, iid in enumerate(created):
        def dele():
            return req("DELETE", f"/api/instructions/{iid}", admin, org)
        dur, (ms, st, _), samples = with_bystander(dele)
        tag = f"p50 {pct(samples,50):>5.0f}ms  max {max(samples):>6.0f}ms  n={len(samples)}" if samples else "-"
        print(f"{'delete instruction #'+str(i+1):<28} {dur:>8.0f}   {tag}  HTTP {st}")

    # ── 3. the verdict ───────────────────────────────────────────────────────
    print("\nreading:")
    print(f"  the bystander's idle p50 is {b50:.0f}ms. If its p50 DURING a write stays")
    print(f"  near that, the write is off the event loop and other users are unaffected.")
    print(f"  If it climbs toward the write's own duration, the worker is blocked and")
    print(f"  everyone's page stalls — which is what 41a298d2 set out to fix.")


if __name__ == "__main__":
    main()
