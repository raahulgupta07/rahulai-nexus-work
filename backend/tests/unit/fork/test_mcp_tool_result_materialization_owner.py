"""Materializing an MCP tool result must find the acting user, or the insert dies.

`execute_mcp` writes a tool's output to a file so `write_csv` / `create_data` can
read it. That `File` row carries the acting user, and `files.user_id` is NOT
NULL — so failing to resolve the user is not a cosmetic attribution miss, it is
an IntegrityError on the insert.

Two dicts describe the same person under different names: the agent loop puts
the acting user under `"user"`, while some other callers set `"current_user"`.
Reading only the latter left `user_id` None, so **every materialization inside
an agent run died on the insert** — a CSV result silently fell back to an inline
preview, and a JSON result produced no file at all. Neither surfaced as an
error the user could see; the tool simply produced less than it claimed to.

The fix is the fallback `runtime_ctx.get("user") or runtime_ctx.get("current_user")`.
It depends on two facts that live in different files and can drift apart:

  * every resolution reads `"user"` FIRST — reversing the order reintroduces the
    bug for the agent path, which is the only path that fails,
  * the agent's runtime context still publishes the user under `"user"` — rename
    that key and the fallback resolves to None again, silently.

Both are pinned here. Fork note: this fix arrived with upstream v0.0.493's MCP
lane, taken during the v0.0.494 port because 494 could not build without it.
The rest of 493 is deliberately deferred, so this line is in our tree by a
merge-base decision rather than by inheriting the release — and Phase 4 will
merge over the same files.
"""
import ast
import inspect
import textwrap

import pytest

import app.ai.tools.implementations.execute_mcp as execute_mcp_module
from app.models.file import File


def _is_runtime_get(node, key: str | None = None) -> bool:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "runtime_ctx"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    ):
        return False
    return key is None or node.args[0].value == key


def _parse(module) -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(module)))


def _runtime_user_lookups(module) -> list[list[str]]:
    """Every `runtime_ctx.get(...) or runtime_ctx.get(...)` chain, as the ordered
    list of keys it consults.

    Parsed rather than grepped: the defect was entirely in the ORDER of two
    identical-looking calls, which a text search cannot see. The surrounding
    comment quotes both key names too, so grep would match the explanation.
    """
    chains = []
    for node in ast.walk(_parse(module)):
        if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
            continue
        keys = [
            operand.args[0].value
            for operand in node.values
            if _is_runtime_get(operand)
        ]
        if keys and any(k in ("user", "current_user") for k in keys):
            chains.append(keys)
    return chains


def _unchained_current_user_lookups(module) -> list[int]:
    """Line numbers of `runtime_ctx.get("current_user")` calls that are NOT the
    fallback half of a user-first chain.

    Inspecting only the chains is not enough: reverting one site to a bare
    `runtime_ctx.get("current_user")` removes the BoolOp altogether, so a
    chain-only check sees nothing wrong and reports every remaining chain as
    correct. That is the partial-revert a merge actually produces, and it is
    the case this function exists for.
    """
    tree = _parse(module)
    chained = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
            continue
        if not (node.values and _is_runtime_get(node.values[0], "user")):
            continue
        for operand in node.values[1:]:
            if _is_runtime_get(operand, "current_user"):
                chained.add(id(operand))

    return [
        node.lineno
        for node in ast.walk(tree)
        if _is_runtime_get(node, "current_user") and id(node) not in chained
    ]


# ── why a missed user is fatal rather than untidy ───────────────────────────

def test_the_file_owner_column_is_not_nullable():
    """If this ever became nullable the failure would change shape entirely —
    from a hard insert error to an unowned file — and the tests below would be
    guarding against the wrong consequence."""
    assert File.__table__.c.user_id.nullable is False


# ── the resolution order, at every site ─────────────────────────────────────

def test_every_runtime_user_lookup_prefers_the_agent_loop_s_key():
    chains = _runtime_user_lookups(execute_mcp_module)
    assert chains, "no runtime_ctx user lookups found — has the module moved?"
    for keys in chains:
        assert keys[0] == "user", (
            f"a runtime_ctx user lookup reads {keys} — reading 'current_user' "
            "first resolves to None on the agent path, and files.user_id is NOT NULL"
        )


def test_no_site_reads_current_user_on_its_own():
    """The partial revert: one site loses the chain entirely and reads only
    `current_user`. Every other site still looks correct, so a check that only
    inspects chains reports the file as healthy while that one path is back to
    resolving None on every agent run."""
    stray = _unchained_current_user_lookups(execute_mcp_module)
    assert stray == [], (
        f"lines {stray} read runtime_ctx['current_user'] without trying 'user' "
        "first — on the agent path that resolves to None, and files.user_id is NOT NULL"
    )


def test_the_fallback_is_still_there_for_the_other_callers():
    """The fix is a fallback, not a swap. Callers outside the agent loop set
    only `current_user`; dropping it would move the same failure onto them."""
    chains = _runtime_user_lookups(execute_mcp_module)
    assert all("current_user" in keys for keys in chains), (
        "a lookup no longer falls back to current_user"
    )


def test_every_materialization_path_resolves_the_user():
    """The writers are separate methods that each build their own `File`. The
    original fix had to touch every one; a partial revert leaves one output
    format working and another not, which reads as a data problem rather than a
    permissions one.

    Discovered rather than listed, so a NEW writer added by a later port is
    covered the day it lands instead of the day someone remembers this file.
    """
    tool_cls = execute_mcp_module.ExecuteMCPTool
    writers = [
        name for name in dir(tool_cls)
        if name.startswith("_materialize_")
    ]
    assert len(writers) >= 2, f"expected several materialization paths, found {writers}"

    owners = []
    for name in writers:
        src = inspect.getsource(getattr(tool_cls, name))
        if "File(" not in src:
            continue  # not a writer of a File row; nothing to own
        owners.append(name)
        assert 'runtime_ctx.get("user")' in src, f"{name} does not resolve the acting user"
        assert "user_id=" in src, f"{name} no longer stamps an owner on the File"

    assert owners, f"none of {writers} constructs a File — has the row moved?"


# ── the other half: the key the agent actually publishes ────────────────────

def test_the_agent_loop_publishes_the_user_under_that_key():
    """The fallback only works because the agent's runtime context uses "user".
    Renaming that key would break every lookup above while leaving them all
    looking correct — the failure would appear in execute_mcp, three files away
    from the edit that caused it."""
    import app.ai.agent_v2 as agent_v2

    tree = ast.parse(inspect.getsource(agent_v2))
    contexts = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        and any(
            isinstance(k, ast.Constant) and k.value == "ds_clients"
            for k in node.keys
        )
    ]
    assert contexts, "could not find the agent's tool runtime context"
    for ctx in contexts:
        keys = {k.value for k in ctx.keys if isinstance(k, ast.Constant)}
        assert "user" in keys, (
            f"a tool runtime context does not publish the acting user under 'user': "
            f"{sorted(keys)}"
        )


def test_the_agent_does_not_publish_it_under_the_other_name_instead():
    """Guard the guard. If the agent ever set BOTH keys, the ordering tests
    above would keep passing while proving nothing — the bug they exist for
    could not occur, and a later removal of the fallback would go unnoticed."""
    import app.ai.agent_v2 as agent_v2

    src = inspect.getsource(agent_v2)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
        if "ds_clients" in keys:
            assert "current_user" not in keys, (
                "the agent now publishes both key names; the fallback is no longer "
                "load-bearing and these tests no longer prove anything"
            )
