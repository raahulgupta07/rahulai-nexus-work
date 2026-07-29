"""The migration history must be a single line: one base, one head, nothing orphaned.

★ Alembic refuses to run `upgrade head` when more than one head exists — it
cannot know which end you meant. So a second head is not a warning, it is an app
that will not start, discovered at boot on whichever machine deploys next.

This is not hypothetical and it is not only our own mistake to make. Upstream
v0.0.490 shipped `idxuser01` and `mainbuild01` both declaring `entraprof01` as
their parent, with neither chaining onto the other. Two heads, in a tagged
release. Their own `alembic upgrade head` could not run against it. We caught it
by reading the files before porting; nothing in either project caught it
automatically. This test is that missing check.

The failure is easy to reintroduce: two people (or two ports) each add a
migration onto the same parent, and each one works fine in isolation. It only
breaks once both are present.

Sixth instance in this codebase of the same shape — a claim that nothing
enforces (see also the permission registry vs the seeded role, the .gitignore
rule over already-tracked files, upgrade.sh's rollback promise, the auto-publish
gate reading contained rather than changed content, and pyproject.toml vs
uv.lock). The fix for that shape is always the same: make something fail when
the claim and the reality disagree.

★ Uses alembic's own ScriptDirectory rather than a regex over the files. A regex
gets this wrong: `down_revision` is sometimes a tuple (merge migrations), and a
hand-rolled parser that only matches the single-string form reports ~22 phantom
heads on a tree that alembic correctly reads as one. The parser has to be the
same one alembic uses, or the guard is wrong on the day it lands.

ScriptDirectory reads the files directly. It does not import env.py, does not
build a settings object and does not open a database connection, so this stays
in the fast fork suite.
"""
from pathlib import Path

from alembic.script import ScriptDirectory

_BACKEND = Path(__file__).resolve().parents[3]
_ALEMBIC_DIR = _BACKEND / "alembic"
_VERSIONS_DIR = _ALEMBIC_DIR / "versions"


def _scripts() -> ScriptDirectory:
    return ScriptDirectory(dir=str(_ALEMBIC_DIR))


def test_exactly_one_head():
    heads = _scripts().get_heads()
    assert len(heads) == 1, (
        f"alembic has {len(heads)} heads: {sorted(heads)}. `alembic upgrade head` "
        "cannot run in this state, so the app will fail to start on the next "
        "deploy. Two migrations were almost certainly added onto the same parent "
        "— re-point the later one's `down_revision` at the earlier one so the "
        "history is a single line."
    )


def test_exactly_one_base():
    bases = _scripts().get_bases()
    assert len(bases) == 1, (
        f"alembic has {len(bases)} bases: {sorted(bases)}. A second base means a "
        "migration chain that starts from nothing and never joins the real "
        "history, so it will never be applied."
    )


def test_every_migration_file_is_reachable_from_the_head():
    """A file that alembic cannot parse, or that no chain reaches, is dead — it
    will never run, which looks identical to it having run."""
    scripts = _scripts()
    reachable = {rev.revision for rev in scripts.walk_revisions()}
    on_disk = [p for p in _VERSIONS_DIR.glob("*.py") if p.name != "__init__.py"]

    assert len(reachable) == len(on_disk), (
        f"{len(on_disk)} migration files on disk but {len(reachable)} reachable "
        "from the head. Some migration is orphaned or unparseable, and will "
        "never be applied."
    )


def test_the_guard_actually_walked_the_history():
    """Guards the guard: if ScriptDirectory ever pointed at an empty or wrong
    directory, every assertion above would pass vacuously — a tree with zero
    migrations has zero extra heads. That is the exact failure mode this file
    exists to prevent, so assert we really read the history."""
    reachable = {rev.revision for rev in _scripts().walk_revisions()}
    assert len(reachable) > 150, (
        f"only walked {len(reachable)} revisions — ScriptDirectory is probably "
        f"pointed at the wrong directory ({_ALEMBIC_DIR}), and the single-head "
        "check above is now vacuous"
    )
