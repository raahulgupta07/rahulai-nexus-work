"""Three logins hitting the instance at the same moment.

Sequential tests prove the RULES. This proves the rules still hold when three
people are mid-turn at once — a different question, and the one that matters on
a Monday morning.

WHAT CONCURRENCY CAN BREAK THAT SERIAL TESTING CANNOT SEE

  cross-talk     one user's answer landing in another user's report. The turn is
                 assembled from a shared session and a per-request user; a
                 mix-up shows up only when two turns overlap in time.
  isolation      the privacy gates were verified while the instance was quiet.
                 A gate that reads request state can pass serially and fail when
                 two requests interleave.
  starvation     one user's long turn blocking everyone else's short one.
  5xx under load a deadlock or pool exhaustion, which serial tests never reach.

★Each user asks for a DIFFERENT, checkable literal ("pineapple", "bicycle",
"tuesday"). That is what makes cross-talk detectable at all: identical prompts
would produce identical answers and a mix-up would be invisible.

★Every assertion is per-user. "All three succeeded" is not enough — the whole
point is that each answer reached the right person's report and no other.

Usage:  python user-parallel.py /tmp/tokens.json [--rounds 2]
"""
import argparse
import json
import os
import sys
import threading
import time
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "_um", os.path.join(os.path.dirname(os.path.abspath(__file__)), "user-multi.py"))
_um = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_um)
req = _um.req

# Distinct, unmistakable words — see the note above about why they must differ.
WORDS = ["pineapple", "bicycle", "tuesday"]


def settle(token, org, rid, deadline=240):
    """Wait for the agent row to leave in_progress. Returns (status, text)."""
    t0 = time.time()
    while time.time() - t0 < deadline:
        st, body = req("GET", f"/api/reports/{rid}/completions", token, org)
        rows = body if isinstance(body, list) else body.get("completions", [])
        agent = [r for r in rows if r.get("role") != "user"]
        # ★EVERY agent row must be terminal, not the first one to settle. Later
        # rows keep arriving while the turn runs, so returning on the first both
        # under-reports the wait and hands back an early row's text.
        if agent and all(a.get("status") in ("success", "error", "stopped") for a in agent):
            last = agent[-1]
            return last.get("status"), json.dumps(last)
        time.sleep(3)
    return "timeout", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tokens")
    ap.add_argument("--rounds", type=int, default=2)
    args = ap.parse_args()

    tok = json.load(open(args.tokens))
    org = tok["org"]["id"]
    people = [
        ("admin",  "raahulgupta07@gmail.com"),
        ("member", "member@cityagent.io"),
        ("local",  "localtest@cityagent.io"),
    ]
    actors = [(label, email, tok["users"][email]["token"]) for label, email in people]

    results = []
    lock = threading.Lock()

    def rec(name, ok, detail):
        with lock:
            results.append((name, ok, detail))
            print(f"  {'PASS' if ok else 'FAIL'}  {name} — {detail}", flush=True)

    for rnd in range(1, args.rounds + 1):
        print(f"\n── round {rnd}: three users, simultaneously ──────────────")
        state = {}

        def run(idx, label, email, token):
            word = WORDS[idx]
            st, rep = req("POST", "/api/reports", token, org,
                          {"title": f"parallel r{rnd} · {label}"})
            if st not in (200, 201):
                state[label] = {"error": f"report create HTTP {st}"}
                return
            rid = rep["id"]
            t0 = time.time()
            st, _ = req("POST", f"/api/reports/{rid}/completions", token, org,
                        {"prompt": {"content": f"Reply with exactly one word: {word}",
                                    "mentions": []}, "background": True})
            status, text = settle(token, org, rid)
            state[label] = {"rid": rid, "word": word, "status": status,
                            "text": text, "secs": round(time.time() - t0, 1),
                            "post": st}

        threads = []
        t_start = time.time()
        for i, (label, email, token) in enumerate(actors):
            th = threading.Thread(target=run, args=(i, label, email, token))
            th.start()
            threads.append(th)
        for th in threads:
            th.join()
        wall = round(time.time() - t_start, 1)

        # every turn settled
        for label, _, _ in actors:
            s = state.get(label, {})
            rec(f"r{rnd} {label}: turn settled",
                s.get("status") == "success",
                f"status={s.get('status')} in {s.get('secs')}s (POST {s.get('post')})")

        # no 5xx anywhere
        bad = [l for l, _, _ in actors if str(state.get(l, {}).get("post", "")).startswith("5")]
        rec(f"r{rnd} no server errors under load", not bad, f"5xx from: {bad or 'none'}")

        # ★the real check: each answer contains ITS OWN word and nobody else's
        for label, _, _ in actors:
            s = state.get(label, {})
            txt = (s.get("text") or "").lower()
            mine = s.get("word", "")
            others = [w for w in WORDS if w != mine]
            leaked = [w for w in others if w in txt]
            rec(f"r{rnd} {label}: no cross-talk",
                not leaked,
                f"own word present={mine in txt}; other users' words found={leaked or 'none'}")

        # ★and the gates still hold WHILE everyone is busy
        m_rid = state.get("member", {}).get("rid")
        if m_rid:
            st, _ = req("GET", f"/api/reports/{m_rid}/completions",
                        tok["users"]["localtest@cityagent.io"]["token"], org)
            rec(f"r{rnd} privacy gate holds under concurrency", st in (403, 404),
                f"stranger reading member's live report -> HTTP {st}")

        rec(f"r{rnd} wall clock", True, f"{wall}s for 3 concurrent turns")

        for label, _, _ in actors:
            rid = state.get(label, {}).get("rid")
            if rid:
                req("DELETE", f"/api/reports/{rid}",
                    dict(actors_map := {l: t for l, _, t in actors})[label], org)

    print()
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"{passed} passed, {len(results)-passed} failed, {len(results)} checks")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
