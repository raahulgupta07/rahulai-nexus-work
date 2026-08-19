"""A long changelog entry must not be able to abort the upgrade.

WHAT THIS COST
--------------
Deploying `0.0.543.5` to the dev server, `upgrade.sh` stopped dead immediately
after printing "What is new", with **exit 141** and no error message. 141 is
128 + SIGPIPE. The line was:

    awk '/^## Version/{n++} n<=3' CHANGELOG.md | head -30 | sed 's/^/      /'

Under `set -euo pipefail` that is a trap. `head -30` exits the moment it has
thirty lines; `awk` is then killed by SIGPIPE writing line thirty-one;
`pipefail` makes the pipeline's status 141 and `set -e` ends the script — before
the build, before the swap, before anything.

★★★It had worked for every previous release by luck: the notes for the newest
three versions had never exceeded thirty lines. **Writing a longer changelog
entry broke the deployment script.** Nothing about the release itself was wrong.

★It failed SAFE — the database dump and the rollback tag were already taken and
nothing was swapped — but the upgrade could not proceed at all. And `--dry-run`
died at the same line while still printing everything above it, so a dry run
that ends there looks exactly like a dry run that finished.

WHAT IS PINNED HERE
-------------------
The script's own output must never be able to stop it. `head`, `tail` and
similar early-exit consumers kill their producer, so they may not appear on the
producing side of a pipe in a `pipefail` script — truncate inside `awk`, which
exits by itself.

★★★AND A SECOND ONE, forty lines below, that this guard missed on the day it
was written. `PORT="$(docker port "$APP" 3000 | head -1 | sed ...)"` — on a stack
behind a reverse proxy there is no published port, `docker port` exits non-zero,
the substitution fails under `pipefail` and the script ends. Silently, right
after the swap, so everything that verifies the deployment never runs: the
health gate, the migration head, the served version, and the ROLLBACK advice on
a failed deploy. Both live installations are proxied. Measured deploying
0.0.543.6: EXIT=1, log stopping at "Started", app in fact healthy.

The first guard was scoped to producers reading a FILE, so it passed that line
without looking. A guard written from one instance of a pattern tends to fit
that instance. The rule below is now about the CONSUMER of the substitution — a
command substitution whose value the script goes on to use must not be able to
kill it.

★This reads the shell source rather than running it: the script needs docker, a
running stack and a real database, none of which the fork suite has.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
UPGRADE = REPO / "upgrade.sh"


def _code(text: str) -> str:
    """Executable lines only — the explanation above quotes the broken form."""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


def test_the_script_still_stops_on_a_failing_pipeline():
    """The premise. Without `pipefail` none of this matters — and a build whose
    failure is swallowed mid-pipeline is far worse than this bug."""
    assert re.search(r"^set -euo pipefail", _code(UPGRADE.read_text(encoding="utf-8")), re.M)


def test_no_file_is_read_into_an_early_exit_reader():
    """★`head`/`tail` on the RIGHT of a pipe kills whatever is on the left.

    ★Scoped to producers that read a FILE, and that is the whole judgement in
    this test. `docker ps | head -1` and `printf | head -1` are also SIGPIPE in
    principle and never in practice: their output is a few hundred bytes, it
    fits in the pipe buffer, the producer is finished before the reader leaves.
    Failing those would be noise, and a guard that cries wolf gets deleted.

    A file is different — it is unbounded and it GROWS. `CHANGELOG.md` gained
    one entry and crossed the line, which is precisely how this bug arrived
    after working for every previous release.
    """
    offenders = []
    for n, line in enumerate(_code(UPGRADE.read_text(encoding="utf-8")).splitlines(), 1):
        if not re.search(r"\|\s*(head|tail)\b", line):
            continue
        if "|| true" in line:          # explicitly tolerated by its author
            continue
        if re.search(r"\b\S+\.(md|log|dump|txt|json|yaml|yml)\b", line):
            offenders.append(f"{n}: {line.strip()}")
    assert not offenders, (
        "a FILE is piped into an early-exit reader under `pipefail`; when the "
        "reader stops first the producer dies of SIGPIPE and takes the whole "
        "upgrade with it, and a file only ever grows:\n  " + "\n  ".join(offenders)
    )


def test_the_release_notes_are_printed_without_a_pipe():
    code = _code(UPGRADE.read_text(encoding="utf-8"))
    notes = [l for l in code.splitlines() if "CHANGELOG.md" in l and "awk" in l]
    assert notes, "the What-is-new section no longer reads CHANGELOG.md"
    for line in notes:
        assert "|" not in line, (
            "the changelog excerpt is piped again — truncate inside awk: " + line.strip()
        )
        assert "exit}" in line.replace(" ", "") or "exit }" in line, (
            "awk reads the whole file instead of stopping, so a long changelog "
            "is still read in full: " + line.strip()
        )


def test_the_health_check_works_without_a_published_port():
    """★★★The gate, the migration check, the served version and the rollback
    advice all sit BELOW the port lookup. On a proxied install — which is every
    production-shaped one — losing that line loses all of them."""
    code = _code(UPGRADE.read_text(encoding="utf-8"))

    port_lines = [l for l in code.splitlines() if "docker port" in l]
    assert port_lines, "the port lookup is gone entirely"
    for line in port_lines:
        assert "|| true" in line, (
            "`docker port` exits non-zero when nothing is published, and under "
            "`pipefail` that ends the script mid-deploy: " + line.strip()
        )

    assert "docker exec" in code and "localhost:3000/health" in code, (
        "there is no in-container health check, so an install with no published "
        "port cannot be verified at all"
    )


def test_every_command_substitution_the_script_relies_on_can_fail_safely():
    """★The general form of both bugs: `X=$(cmd | cmd)` under `set -euo pipefail`
    ends the run if any stage exits non-zero. Assignments that tolerate failure
    say so with `|| true`, `|| echo`, or an `if`."""
    offenders = []
    for n, line in enumerate(_code(UPGRADE.read_text(encoding="utf-8")).splitlines(), 1):
        stripped = line.strip()
        if not re.match(r'^[A-Z_]+="?\$\(', stripped):
            continue
        if "|" not in stripped:
            continue          # a single command; `set -e` failing here is intended
        if "|| true" in stripped or "|| echo" in stripped:
            continue
        offenders.append(f"{n}: {stripped}")
    assert not offenders, (
        "a piped command substitution has no failure path; if any stage exits "
        "non-zero the whole upgrade ends, and it ends without a message:\n  "
        + "\n  ".join(offenders)
    )
