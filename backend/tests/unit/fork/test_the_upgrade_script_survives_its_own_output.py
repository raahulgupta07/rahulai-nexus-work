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
