"""Run every security probe and return a single verdict.

Exits non-zero if ANY probe fails, so this can gate a release the way the fork
suite does. Read scripts/security/README.md before trusting a green run — the
"what this cannot see" section is the honest half of the result.

Usage:  python3 scripts/security/run-all.py /tmp/sec-tokens.json [--only tenancy]
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

# Ordered cheapest-first, so a broken instance is obvious before the slow ones.
PROBES = [
    ("tenancy", "tenancy.py", "one user reaching another user's objects"),
    ("secrets", "secrets.py", "secrets leaving the server"),
    ("injection", "injection.py", "user input reaching SQL / LDAP / a path / an outbound request"),
]

# The static route audit runs under pytest in the /src runner, not as a script.
STATIC_NOTE = (
    "backend/tests/unit/fork/test_every_route_is_gated.py runs with the fork "
    "suite — it is static (AST over every route) and needs no live instance."
)


def preflight(base):
    """Refuse to report anything if the instance is not actually answering.

    A suite that silently measures a dead app returns green for the same reason a
    suite that measures nothing does.
    """
    import urllib.request
    import urllib.error
    try:
        with urllib.request.urlopen(f"{base}/api/settings", timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print(f"  instance at {base} is not answering: {e}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tokens", help="path written by scripts/mint-user-tokens.py")
    ap.add_argument("--only", default="", help="comma-separated probe names")
    args = ap.parse_args()

    base = os.environ.get("BASE_URL", "http://localhost:8095")

    if not os.path.exists(args.tokens):
        print(f"tokens file not found: {args.tokens}")
        print("mint it with a PATH ARGUMENT, never a `>` redirect:")
        print("  docker exec -w /app/backend dash-app python mint-user-tokens.py /tmp/sec-tokens.json")
        return 2

    # ★A token file that begins with a log line is the classic failure here.
    with open(args.tokens) as fh:
        head = fh.read(1).strip()
    if head != "{":
        print(f"{args.tokens} does not start with '{{' — it was probably written with")
        print("a `>` redirect, which captures the app's start-up log lines. Re-mint it")
        print("passing the path as an ARGUMENT.")
        return 2

    print(f"instance: {base}")
    if not preflight(base):
        return 2
    print()

    want = [w.strip() for w in args.only.split(",") if w.strip()]
    results = []
    for name, script, asks in PROBES:
        if want and name not in want:
            continue
        path = os.path.join(HERE, script)
        if not os.path.exists(path):
            print(f"[skip] {name:<10} {script} not present")
            results.append((name, None, 0.0))
            continue
        print(f"[run ] {name:<10} — {asks}")
        t0 = time.time()
        proc = subprocess.run([sys.executable, path, args.tokens],
                              cwd=REPO, capture_output=True, text=True)
        secs = time.time() - t0
        sys.stdout.write(proc.stdout)
        if proc.stderr.strip():
            sys.stdout.write(proc.stderr)
        results.append((name, proc.returncode, secs))
        print(f"[{'ok  ' if proc.returncode == 0 else 'FAIL'}] {name:<10} {secs:.1f}s\n")

    print("=" * 70)
    failed = [n for n, rc, _ in results if rc not in (0, None)]
    skipped = [n for n, rc, _ in results if rc is None]
    for name, rc, secs in results:
        state = "skipped" if rc is None else ("PASS" if rc == 0 else "FAIL")
        print(f"  {name:<12} {state:<8} {secs:>5.1f}s")
    print()
    print(f"  {STATIC_NOTE}")
    print()
    if skipped:
        # ★A skipped probe is not a pass. Saying so out loud is the whole point:
        # "security suite green" over a suite that ran two of four checks is the
        # kind of claim that survives until an incident disproves it.
        print(f"  ★{len(skipped)} probe(s) DID NOT RUN: {', '.join(skipped)}.")
        print("   This run does not cover them. Do not describe it as a full pass.")
    if failed:
        print(f"  ★FAILED: {', '.join(failed)}")
        return 1
    print("  Every probe that ran, passed. Read README.md's 'what this cannot")
    print("  see' before calling the application secure — this suite answers the")
    print("  questions someone thought to ask, and nothing else.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
