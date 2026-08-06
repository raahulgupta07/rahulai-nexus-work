"""A per-user connector failing to connect must not switch the agent off.

`test_data_source_connection` reflects connectivity onto the org-wide
`DataSource.is_active` flag. That is right for a system-credentials agent: if
the warehouse is unreachable with the org's own credentials, it is down for
everyone. It is WRONG for a `user_required` agent, where a failure usually
means only that THIS caller has not signed in yet.

★★★The guard existed and did not work. It read

    getattr(data_source, "auth_policy", "system_only") == "system_only"

but `auth_policy` is a column on **Connection**, not on DataSource — it moved
there along with type/config/credentials, and models/data_source.py says so in
a comment. A `getattr` for an attribute that does not exist returns the default,
so the condition evaluated `"system_only" == "system_only"` for every agent
and the guard never once guarded anything.

★★★Why it mattered more than it looks: the Agents page lists
`/data_sources/active`, so a deactivated agent does not appear greyed out or
flagged — it is simply GONE, org-wide, with nothing logged and no message. It
was found on 2026-08-04 only because a read-only API sweep happened to call the
endpoint against a per-user agent, and the sole evidence was `updated_at`
moving. Nothing else in the product said a word.

These tests assert the two halves separately, because a single test that only
covers the system_only case is what let the broken guard look correct.
"""
import inspect
import re

from app.services.data_source_service import DataSourceService


_RAW = inspect.getsource(DataSourceService.test_data_source_connection)

# ★Comments stripped before scanning. `inspect.getsource` returns them, and the
# fix for this defect NAMES the broken expression in a comment so the next
# reader knows what not to write — which made the first version of this test
# fail against the fixed code, citing its own explanation as the bug.
SOURCE = "\n".join(line.split("#", 1)[0] for line in _RAW.splitlines())


def test_the_guard_does_not_read_auth_policy_off_the_data_source():
    """The exact expression that was broken must not come back.

    A grep-shaped assertion on purpose: the defect is not that the flag is
    written, it is WHERE the policy is read from, and that is visible in the
    source without standing up a warehouse to fail against.
    """
    offending = re.search(
        r"getattr\(\s*data_source\s*,\s*[\"']auth_policy[\"']", SOURCE
    )
    assert offending is None, (
        "auth_policy is read off DataSource again. It lives on Connection; "
        "getattr falls through to its default there, so the guard is always "
        "true and every user_required agent gets disabled org-wide on a "
        "failed connection test."
    )


def test_the_guard_reads_auth_policy_off_the_connections():
    """...and reads it from the object that actually carries it."""
    assert "data_source.connections" in SOURCE, (
        "the auth-policy guard must derive the policy from the agent's "
        "connections, which is where the column lives"
    )


class _Conn:
    def __init__(self, policy):
        self.auth_policy = policy


def _system_only(connections):
    """The predicate as the service computes it, kept in one place.

    Mirrors the service rather than importing a helper because the logic is
    three lines inline; if it is ever extracted, this test should import it.
    """
    policies = {getattr(c, "auth_policy", "system_only") or "system_only"
                for c in connections}
    return policies == {"system_only"}


def test_a_user_required_agent_is_not_treated_as_system_only():
    assert _system_only([_Conn("user_required")]) is False


def test_a_system_only_agent_still_is():
    """The behaviour being preserved — an org-credentials agent that cannot
    connect genuinely is down for everyone, and must still be marked so."""
    assert _system_only([_Conn("system_only")]) is True


def test_a_mixed_agent_is_not_treated_as_system_only():
    """★Fails safe. One per-user connection in the set is enough to make a
    failure ambiguous, and an ambiguous failure must not disable anything."""
    assert _system_only([_Conn("system_only"), _Conn("user_required")]) is False


def test_a_connection_with_no_policy_defaults_to_system_only():
    """A null column is the documented default, not an unknown."""
    assert _system_only([_Conn(None)]) is True


def test_no_code_in_the_service_reads_auth_policy_off_a_data_source():
    """The whole file, not just the one function that was found first.

    ★There were TWO sites with this defect and the second was found only
    because a regex written for the first happened to sweep the file. In
    `test_data_source_connection` the always-true guard disabled a per-user
    agent org-wide; in `_get_prompt_schema` the always-FALSE twin left a branch
    that had never run since it was written. Same wrong object, opposite
    comparison, opposite symptom — which is exactly why a test pinned to one
    function would have caught neither the other one nor the next.

    ★Parsed with `ast`, not grepped. Both fixes NAME the broken expression so
    the next reader knows what not to write — one in a `#` comment, one inside a
    docstring — and a text scan reads those as live code, failing while citing
    the very sentence that explains it. Stripping strings instead does not work
    either: `"auth_policy"` IS a string literal, so a stripper removes the thing
    being looked for and the test can never fail at all. Both mistakes were made
    here before this landed on the parse tree, where a comment is not a node and
    an argument still is.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(inspect.getfile(DataSourceService)).read_text(encoding="utf-8"))

    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "getattr"):
            continue
        if len(node.args) < 2:
            continue
        target, attr = node.args[0], node.args[1]
        if not (isinstance(target, ast.Name) and target.id in ("data_source", "ds")):
            continue
        if isinstance(attr, ast.Constant) and attr.value == "auth_policy":
            hits.append(node.lineno)

    assert not hits, (
        f"{len(hits)} site(s) read auth_policy off a DataSource, at line(s) {hits}. "
        "The column is on Connection — getattr falls through to its 'system_only' "
        "default, so the comparison is decided at write time, not run time. "
        "Resolve it from the linked connection (see "
        "schema_context_builder._resolve_user_access)."
    )
