"""Upstream 0.0.528 replaces DEFAULT_MEMBER_PERMISSIONS wholesale. Ours must survive.

Upstream introduces ``BASELINE_PERMISSIONS`` — the permissions hidden from the
role editor, granted by the resolver to every member — and then writes::

    DEFAULT_MEMBER_PERMISSIONS = list(BASELINE_PERMISSIONS)

That is a REPLACEMENT of the whole literal, not an edit to it. Our fork appends
``create_file_data_source`` to that literal so a plain member can build an
upload-only CSV agent (see CLAUDE.md, "Member file-agents"). Taking upstream's
hunk as written silently drops it, and the failure is a 403 on
``POST /data_sources`` for every member — with nothing in the fork suite going
red, because every other guard we have checks the registry's *categories*, not
the seeded member role.

★The obvious-looking fix is wrong. ``create_file_data_source`` cannot simply be
added to ``BASELINE_PERMISSIONS``: upstream defines that set as exactly the
HIDDEN ones, and states the invariant "hidden ⇒ baseline, and the two sets are
deliberately the same set" — the equivalence is what makes hiding safe. Ours is
a VISIBLE, grantable checkbox in "Data & Connections", so an org that wants to
withhold it from a custom role must be able to. The resolution is to keep it as
an explicit addition on the member seed:

    DEFAULT_MEMBER_PERMISSIONS = list(BASELINE_PERMISSIONS) + ["create_file_data_source"]

Proven to fail before it was believed (2026-08-08): with the naive
``list(BASELINE_PERMISSIONS)`` in place, ``test_a_member_can_still_create_a_file_agent``
and ``test_our_permission_is_not_smuggled_into_the_baseline`` both fail; with
the resolution above, all four pass.
"""
import pytest

from app.core.permissions_registry import (
    ALL_PERMISSIONS,
    DEFAULT_MEMBER_PERMISSIONS,
    HIDDEN_PERMISSION_CATEGORIES,
    PERMISSION_CATEGORIES,
)


def _hidden() -> set:
    return {p for perms in HIDDEN_PERMISSION_CATEGORIES.values() for p in perms}


def _visible() -> set:
    return {p for perms in PERMISSION_CATEGORIES.values() for p in perms}


def test_a_member_can_still_create_a_file_agent():
    """The seeded member role carries our fork's file-agent permission.

    This is the one that catches upstream's wholesale replacement.
    """
    assert "create_file_data_source" in DEFAULT_MEMBER_PERMISSIONS, (
        "create_file_data_source fell off the member seed. Upstream 0.0.528 "
        "replaces DEFAULT_MEMBER_PERMISSIONS with list(BASELINE_PERMISSIONS); "
        "ours must be list(BASELINE_PERMISSIONS) + ['create_file_data_source']."
    )


def test_our_permission_is_not_smuggled_into_the_baseline():
    """It stays a VISIBLE checkbox, so a custom role can withhold it.

    Adding it to the hidden set would grant it to every member unconditionally
    and remove it from the role editor — the opposite of what a per-org opt-out
    needs, and a violation of upstream's hidden-set definition.
    """
    assert "create_file_data_source" in _visible(), (
        "create_file_data_source must remain grantable in the role editor"
    )
    assert "create_file_data_source" not in _hidden(), (
        "create_file_data_source must NOT be hidden — hidden implies baseline, "
        "which would make it un-withholdable"
    )


def test_the_member_seed_covers_every_hidden_permission():
    """Upstream's invariant, asserted on our side of the merge.

    A hidden permission cannot be granted by the role editor, so if it is not
    on the member seed it is unreachable and a custom role produces a user who
    cannot open a report or attach a file.
    """
    missing = _hidden() - set(DEFAULT_MEMBER_PERMISSIONS)
    assert not missing, f"hidden but not seeded on member: {sorted(missing)}"


def test_the_member_seed_names_only_real_permissions():
    unknown = set(DEFAULT_MEMBER_PERMISSIONS) - set(ALL_PERMISSIONS)
    assert not unknown, f"member seed names permissions that do not exist: {sorted(unknown)}"
