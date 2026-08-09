"""Migration ``oidcgrp01`` — round trip, reversibility, and prefix collision.

The migration REWRITES stored group names, so its downgrade is not decoration:
it is the only way back for an installation that rolls the release out and then
rolls it back. These tests drive the real ``upgrade()`` / ``downgrade()`` from
the version file against a real ``groups`` table carrying the real
``UNIQUE (organization_id, name)`` constraint, and read the rows back.

Two things are deliberately pinned:

  - **Reversibility is exact but scoped.** ``downgrade`` restores the raw GUID
    for exactly the rows ``upgrade`` wrote, recognising them by comparing the
    stored name against the two labels this migration can produce for that row's
    own ``external_id``. A placeholder that names a DIFFERENT id, or any name an
    admin typed, is left alone — so a rollback cannot invent a GUID for a group
    nobody relabelled.
  - **The label keeps only 8 hex digits**, so two unresolved ids sharing that
    prefix in one org would collide on a UNIQUE constraint and fail the upgrade
    for the whole deployment. The second one is spelled out in full instead.

``op.get_bind()`` is patched to a plain sync SQLAlchemy connection — the
migration only ever issues ``sa.text`` against the bind, so this exercises the
shipped code, not a paraphrase of it.
"""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa

GUID_A = "85f43b45-99ae-43a0-a780-a05c119e8b9c"
GUID_B = "11111111-2222-3333-4444-555555555555"
# Same first 8 hex digits as GUID_A — the only way the 8-char label can collide.
GUID_A_TWIN = "85f43b45-0000-0000-0000-000000000000"

LABEL_A = f"Unresolved directory group ({GUID_A[:8]}…)"
LABEL_A_FULL = f"Unresolved directory group ({GUID_A_TWIN})"


def _load_migration():
    path = (Path(__file__).resolve().parents[2] / "alembic" / "versions"
            / "oidcgrp01_relabel_guid_named_oidc_groups.py")
    spec = importlib.util.spec_from_file_location("_oidcgrp01_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def conn():
    """A `groups` table with the constraint that makes a collision fatal."""
    engine = sa.create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(sa.text(
            "CREATE TABLE groups ("
            " id TEXT PRIMARY KEY,"
            " organization_id TEXT NOT NULL,"
            " name TEXT NOT NULL,"
            " external_id TEXT,"
            " external_provider TEXT,"
            " UNIQUE (organization_id, name))"
        ))
    with engine.begin() as c:
        yield c
    engine.dispose()


def _add(conn, org, name, external_id=None, provider="oidc"):
    conn.execute(
        sa.text("INSERT INTO groups (id, organization_id, name, external_id,"
                " external_provider) VALUES (:i, :o, :n, :e, :p)"),
        {"i": str(uuid.uuid4()), "o": org, "n": name, "e": external_id, "p": provider},
    )


def _names(conn, org):
    rows = conn.execute(
        sa.text("SELECT external_id, name FROM groups WHERE organization_id = :o"),
        {"o": org},
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _run(mod, conn, fn):
    with patch.object(mod.op, "get_bind", return_value=conn):
        fn()


# ── what it relabels, and what it refuses to touch ───────────────────────────

def test_upgrade_relabels_only_guid_named_oidc_rows(conn):
    mod = _load_migration()
    org = "org1"
    _add(conn, org, GUID_A, GUID_A)                      # relabel
    _add(conn, org, "Engineering", "Engineering")        # readable claim value
    _add(conn, org, "Finance", GUID_B)                   # resolved, real name
    # A GUID-named row from ANOTHER directory. Same shape, not ours to relabel —
    # LDAP has its own naming rules and this migration must stay in its lane.
    _add(conn, org, GUID_A_TWIN, GUID_A_TWIN, provider="ldap")

    _run(mod, conn, mod.upgrade)

    assert _names(conn, org) == {
        GUID_A: LABEL_A,
        "Engineering": "Engineering",
        GUID_B: "Finance",
        GUID_A_TWIN: GUID_A_TWIN,
    }


def test_upgrade_is_idempotent(conn):
    """A relabelled row no longer matches the predicate, so a re-run is a no-op —
    which is what makes a partially-applied upgrade safe to repeat."""
    mod = _load_migration()
    org = "org1"
    _add(conn, org, GUID_A, GUID_A)

    _run(mod, conn, mod.upgrade)
    first = _names(conn, org)
    _run(mod, conn, mod.upgrade)

    assert _names(conn, org) == first == {GUID_A: LABEL_A}


# ── reversibility ────────────────────────────────────────────────────────────

def test_downgrade_restores_exactly_what_upgrade_wrote(conn):
    """The round trip is lossless for the rows this migration touched."""
    mod = _load_migration()
    org = "org1"
    _add(conn, org, GUID_A, GUID_A)
    _add(conn, org, "Finance", GUID_B)
    before = _names(conn, org)

    _run(mod, conn, mod.upgrade)
    assert _names(conn, org)[GUID_A] == LABEL_A

    _run(mod, conn, mod.downgrade)
    assert _names(conn, org) == before


def test_downgrade_leaves_a_placeholder_written_by_the_service_for_another_id(conn):
    """A label naming a DIFFERENT id is not this migration's work. Rolling back
    must not invent a GUID name for a row nobody relabelled."""
    mod = _load_migration()
    org = "org1"
    # Name labels GUID_A, but the row's own external_id is GUID_B.
    _add(conn, org, LABEL_A, GUID_B)

    _run(mod, conn, mod.downgrade)

    assert _names(conn, org) == {GUID_B: LABEL_A}


def test_downgrade_never_touches_an_admin_typed_name(conn):
    mod = _load_migration()
    org = "org1"
    _add(conn, org, "Finance", GUID_B)

    _run(mod, conn, mod.downgrade)

    assert _names(conn, org) == {GUID_B: "Finance"}


def test_downgrade_is_idempotent(conn):
    mod = _load_migration()
    org = "org1"
    _add(conn, org, GUID_A, GUID_A)
    _run(mod, conn, mod.upgrade)
    _run(mod, conn, mod.downgrade)
    once = _names(conn, org)
    _run(mod, conn, mod.downgrade)

    assert _names(conn, org) == once == {GUID_A: GUID_A}


# ── the collision the UNIQUE constraint would otherwise turn into an outage ──

def test_two_ids_sharing_a_prefix_do_not_collide(conn):
    """Both rows are GUID-named and share their first 8 hex digits, so the short
    label is the same string for both. Without the fallback the second UPDATE
    violates UNIQUE (organization_id, name) and the upgrade fails for the entire
    deployment — not just for this org."""
    mod = _load_migration()
    org = "org1"
    _add(conn, org, GUID_A, GUID_A)
    _add(conn, org, GUID_A_TWIN, GUID_A_TWIN)

    _run(mod, conn, mod.upgrade)

    got = _names(conn, org)
    assert got == {GUID_A: LABEL_A, GUID_A_TWIN: LABEL_A_FULL}
    assert len(set(got.values())) == 2, "the two rows must not share a name"


def test_a_prefix_collision_survives_the_round_trip(conn):
    """The full-id fallback is also recognised by downgrade, so the collision
    path is reversible too — not merely non-fatal."""
    mod = _load_migration()
    org = "org1"
    _add(conn, org, GUID_A, GUID_A)
    _add(conn, org, GUID_A_TWIN, GUID_A_TWIN)
    before = _names(conn, org)

    _run(mod, conn, mod.upgrade)
    _run(mod, conn, mod.downgrade)

    assert _names(conn, org) == before


def test_a_collision_with_a_name_an_admin_already_holds_is_skipped(conn):
    """Someone already named a group exactly what the label would be. Taking it
    would fail the constraint; the row keeps its GUID and stays visible instead
    of the upgrade dying."""
    mod = _load_migration()
    org = "org1"
    _add(conn, org, LABEL_A, None, provider=None)     # admin-made, holds the name
    _add(conn, org, GUID_A, GUID_A)                   # would want that same name

    _run(mod, conn, mod.upgrade)

    got = _names(conn, org)
    assert got[GUID_A] == f"Unresolved directory group ({GUID_A})"
    assert got[None] == LABEL_A


def test_the_same_prefix_in_two_orgs_is_not_a_collision(conn):
    """The constraint is per-org, so the short label is free in each."""
    mod = _load_migration()
    _add(conn, "org1", GUID_A, GUID_A)
    _add(conn, "org2", GUID_A, GUID_A)

    _run(mod, conn, mod.upgrade)

    assert _names(conn, "org1") == {GUID_A: LABEL_A}
    assert _names(conn, "org2") == {GUID_A: LABEL_A}
