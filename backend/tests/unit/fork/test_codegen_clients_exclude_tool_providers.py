"""Tool-provider clients must never reach generated code as `ds_clients`.

Generated code is handed a dict called `ds_clients` and told every entry answers
`execute_query`. A tool provider (MCP server, custom API) answers no such thing
— it is reached through the `execute_mcp` tool, which builds its own client over
the connection's wire. Left in the dict, it was advertised to the coder in
`<connection_clients>` as just another queryable client, so whenever the data
the model needed was not already in a file it reached for the MCP connection and
emitted `ds_clients["Agent:Conn"].execute_mcp(...)` — a method no client has.
The attempt then fails on an AttributeError, and the failure names the generated
code rather than the dict it was given, so it reads as the model's mistake.

The filter is `tool_provider_base.codegen_clients`, and it is load-bearing at
every point the dict crosses into codegen. This file pins three things a future
port could each break independently:

  * the filter still filters (and still filters by TYPE, not by name),
  * every crossing still goes through it,
  * both tool-provider clients are still under the base class it keys on — a
    new provider that forgets to inherit is invisible to the filter and the
    old failure returns for that connector alone.

Fork note: this arrived with upstream v0.0.494. It is guarded here anyway
because our merge base for `agent_v2.py` was v0.0.493, chosen to exclude that
release's folders/projects lane — so the property survives in our tree by a
deliberate conflict resolution, not by inheriting upstream's file. Phase 4 will
revisit that same region.
"""
import ast
import inspect
import textwrap

import pytest

from app.data_sources.clients.custom_api_client import CustomApiClient
from app.data_sources.clients.mcp_client import McpClient
from app.data_sources.clients.tool_provider_base import (
    ToolProviderClient,
    codegen_clients,
)


class _FakeQueryableClient:
    """Stands in for a real data-source client: it answers execute_query."""

    def execute_query(self, sql):  # pragma: no cover - never called
        return []


class _FakeToolProvider(ToolProviderClient):
    """A provider that is NOT one of the two shipped ones, to prove the filter
    keys on the base class rather than on a hardcoded list of known types."""

    def list_tools(self):  # pragma: no cover - never called
        return []

    def call_tool(self, tool_name, arguments):  # pragma: no cover
        return {}

    def test_connection(self):  # pragma: no cover
        return {"success": True}


# ── the filter ──────────────────────────────────────────────────────────────

def test_a_tool_provider_is_dropped_and_a_real_client_is_kept():
    real = _FakeQueryableClient()
    kept = codegen_clients({"Warehouse": real, "Agent:Conn": _FakeToolProvider()})
    assert kept == {"Warehouse": real}


def test_filtering_is_by_type_not_by_key_name():
    """The keys tool providers get ("Agent:Conn") are a naming convention, not a
    guarantee. A filter written against the key would pass every test here that
    used the convention and silently let through anything that did not."""
    provider_under_an_ordinary_name = codegen_clients({"Sales": _FakeToolProvider()})
    assert provider_under_an_ordinary_name == {}

    real_under_a_provider_shaped_name = codegen_clients({"Agent:Conn": _FakeQueryableClient()})
    assert len(real_under_a_provider_shaped_name) == 1, (
        "a real client was dropped because of its key — the filter must read the type"
    )


@pytest.mark.parametrize("clients", [None, {}])
def test_an_empty_client_dict_is_handled(clients):
    assert codegen_clients(clients) == {}


def test_the_dict_is_a_copy_not_the_caller_s_own():
    """`self.clients` is the agent's live dict and is used elsewhere for the
    tool path. Returning it (or mutating it) would strip the MCP client from
    execute_mcp as well, turning a codegen fix into a broken tool."""
    original = {"Warehouse": _FakeQueryableClient(), "Agent:Conn": _FakeToolProvider()}
    codegen_clients(original)
    assert len(original) == 2, "the caller's dict was mutated"


# ── both shipped providers are actually covered ─────────────────────────────

@pytest.mark.parametrize("provider_cls", [McpClient, CustomApiClient])
def test_every_shipped_tool_provider_inherits_the_filtered_base(provider_cls):
    """The filter is an isinstance check, so a provider that does not inherit
    `ToolProviderClient` is invisible to it and reappears in codegen for that
    connector alone — the hardest version of this bug to notice."""
    assert issubclass(provider_cls, ToolProviderClient)


def test_tool_providers_do_not_answer_execute_query():
    """Why they must be filtered rather than tolerated. If a provider ever grew
    an `execute_query`, this whole guard would be arguing about nothing and
    should be reconsidered rather than left in place."""
    for provider_cls in (McpClient, CustomApiClient):
        assert not hasattr(provider_cls, "execute_query"), (
            f"{provider_cls.__name__} now answers execute_query; re-derive "
            "whether it still needs excluding from codegen"
        )


# ── every crossing goes through it ──────────────────────────────────────────

def test_the_agent_exposes_a_filtered_view_and_keeps_the_raw_one():
    """Both dicts must exist. `clients` is what execute_mcp needs; the filtered
    view is what codegen gets. Collapsing them either way breaks one of the two."""
    from app.ai.agent_v2 import AgentV2

    assert isinstance(AgentV2.codegen_clients, property)
    src = inspect.getsource(AgentV2.codegen_clients.fget)
    assert "codegen_clients(self.clients)" in src, (
        "the agent's filtered view no longer delegates to the shared filter"
    )


def test_the_agent_hands_codegen_the_filtered_dict_at_every_site():
    """Two runtime contexts are built for tools. A port that reverted either one
    to `self.clients` would restore the bug on that path only — and the sites
    are ~3000 lines apart, so a reviewer sees one of them."""
    import app.ai.agent_v2 as agent_v2

    tree = ast.parse(inspect.getsource(agent_v2))
    assignments = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for key in node.keys
        if isinstance(key, ast.Constant) and key.value == "ds_clients"
    ]
    values = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "ds_clients":
                values.append(ast.unparse(value))

    assert len(assignments) >= 2, "expected at least two ds_clients contexts in agent_v2"
    assert set(values) == {"self.codegen_clients"}, (
        f"a runtime context hands codegen an unfiltered client dict: {sorted(set(values))}"
    )


def test_the_mcp_context_builder_filters_before_it_accumulates():
    """`build_mcp_runtime_context` builds its own `ds_clients` from scratch,
    source by source, so it does not inherit the agent's property. The filter
    must be applied as each source's clients are merged in — filtering only at
    the end would still work, but filtering NOWHERE is the failure mode, and
    this is where it would go unnoticed."""
    from app.ai.tools.mcp import context as mcp_context

    src = textwrap.dedent(inspect.getsource(mcp_context))
    assert "ds_clients.update(codegen_clients(clients))" in src, (
        "the MCP runtime context accumulates raw clients into the codegen dict"
    )
