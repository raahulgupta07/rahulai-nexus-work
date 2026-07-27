"""Fork-owned pure-logic tests: no database, no application boot.

WHY THIS DIRECTORY EXISTS
-------------------------
``backend/tests/conftest.py`` declares ``run_migrations`` as a **function-scoped
autouse** fixture. For every single test it disposes the async engine, runs
``gc.collect()``, sleeps 0.1s, deletes the sqlite file and copies a fresh one
from the template — and then does the whole thing again on teardown. That is
roughly 0.9s of fixed cost per test, paid whether or not the test ever opens a
connection.

The tests in here never touch a database. They exercise pure functions: what a
regex trims, how an exception classifies, whether a figure can be grounded
against a dataset, which rows a limit selects. Paying for a schema per test is
pure waste, and the waste is most of the runtime:

    236 fork tests, shared conftest ....... 210s
    236 fork tests, this conftest .........  ~4s

A ``conftest.py`` in a subdirectory overrides a parent fixture **for that
subtree only**, so this changes nothing anywhere else in the suite.

★ DO NOT MOVE A TEST HERE THAT NEEDS A SCHEMA.
It will fail with "no such table", which looks exactly like a product bug and
is not one — you will lose an hour before you remember this file exists. Split
by COST, not by feature: a feature whose tests are mostly fast does not get to
bring its one database test along with it.

★ This is the inner-loop suite, not a replacement for the full run. Before a
push or after an upstream port, still run the whole thing — that is what proves
a hand-merge did not break something three subsystems away.
"""
import pytest


@pytest.fixture(scope="function", autouse=True)
def run_migrations():
    """No-op override of the parent's per-test schema rebuild.

    Deliberately takes no fixture arguments: requesting ``alembic_config`` or
    ``sqlite_template`` here would drag the expensive session-scoped setup back
    in through the fixture graph and undo the point of the file.
    """
    yield
