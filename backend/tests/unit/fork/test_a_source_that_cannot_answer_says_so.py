"""Eleven connectors reported a permission failure as an empty database.

`get_tables` wrapped its whole body in `except Exception: logger.error(...);
return []`. Both callers — `ConnectionService._avalidate_schema_access` and
`DataSourceService.test_new_data_source_connection` — read the result as a
COUNT, so a credential expiry, a revoked grant or a network fault arrived as
"connected, 0 tables". The agent then told the user the data does not exist.

★★★That is the worst failure shape this product has: a confident answer that is
wrong, with no error anywhere to contradict it. A raised exception is strictly
better — both callers already catch, and turn it into
"Connected but cannot read schema: <reason>", which is true.

The same principle applied twice more in this pass, at the two places a
connector silently CHOSE for the user:

* Power BI matched a bare table name against every semantic model and returned
  the first hit, so two models each holding a `Sales` table meant the answer
  came from whichever the dictionary listed first.
* SSAS ran with no catalog when it could not derive one, which the server
  resolves to its default model.

Both now raise, and both only once ambiguity is PROVEN — a single candidate is
still used silently, so nothing that worked before now asks a question.
"""

import ast
from pathlib import Path

import pytest

CLIENTS = Path("app/data_sources/clients")

# The eleven measured on 2026-08-17, by name, so a connector cannot quietly
# regress back to swallowing.
SWALLOWED = (
    "aws_athena_client.py",
    "azure_data_explorer_client.py",
    "druid_client.py",
    "graph_list_client.py",
    "mariadb_client.py",
    "opensearch_client.py",
    "pinot_client.py",
    "presto_client.py",
    "sqlite_client.py",
    "sybase_client.py",
    "trino_client.py",
)

SCHEMA_METHODS = ("get_tables", "aget_schemas", "get_schemas")


def _empty_returns_in_handlers(path: Path):
    """Every `return []` / `return {}` sitting inside an except: in a schema method."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in SCHEMA_METHODS):
            continue
        for handler in ast.walk(node):
            if not isinstance(handler, ast.ExceptHandler):
                continue
            for item in ast.walk(handler):
                if (
                    isinstance(item, ast.Return)
                    and isinstance(item.value, (ast.List, ast.Dict))
                    and not (getattr(item.value, "elts", None) or getattr(item.value, "keys", None))
                ):
                    found.append((node.name, item.lineno))
    return found


@pytest.mark.parametrize("filename", SWALLOWED)
def test_a_named_connector_no_longer_reports_a_failure_as_an_empty_schema(filename):
    path = CLIENTS / filename
    assert path.exists(), f"{filename} moved — re-point this guard, do not delete it"
    leaks = _empty_returns_in_handlers(path)
    assert not leaks, (
        f"{filename} turns a schema failure back into an empty list at {leaks} — "
        "the caller reads that as 'connected, 0 tables'"
    )


def test_no_connector_anywhere_swallows_a_schema_failure():
    """★The list above is the measured set; this is the one that catches a NEW
    connector arriving with the same habit, including one ported from upstream."""
    offenders = {
        path.name: leaks
        for path in sorted(CLIENTS.glob("*_client.py"))
        if (leaks := _empty_returns_in_handlers(path))
    }
    assert not offenders, offenders


def test_the_repaired_handlers_actually_raise():
    """A handler that logs and falls off the end returns None, which the caller
    reports as 'does not support schema introspection' — a different lie."""
    for filename in SWALLOWED:
        source = (CLIENTS / filename).read_text(encoding="utf-8")
        assert "\n            raise\n" in source or "\n                raise\n" in source, filename


# --------------------------------------------------------------------------
# the two places a connector CHOSE for the user
# --------------------------------------------------------------------------

def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    raise AssertionError(f"{name} not found in {path.name}")


def test_power_bi_no_longer_answers_from_whichever_model_came_first():
    body = _function_source(CLIENTS / "powerbi_client.py", "_resolve_ids_from_metadata")
    assert "raise ValueError" in body, (
        "a bare table name matching two semantic models must ask, not pick"
    )
    assert "is ambiguous" in body


def test_power_bi_still_resolves_an_unambiguous_name_silently():
    """★The half that keeps this from being a regression: one candidate is used,
    exactly as before. A change that made every lookup ask would be worse than
    the bug."""
    body = _function_source(CLIENTS / "powerbi_client.py", "_resolve_ids_from_metadata")
    assert "if len(matches) == 1:" in body
    assert "len(dataset_ids) > 1" in body, (
        "one dataset reachable under two spellings is not a real choice"
    )


def test_ssas_refuses_to_guess_a_catalog_only_when_there_is_more_than_one():
    body = _function_source(CLIENTS / "xmla_base.py", "_resolve_catalog")
    assert "_list_catalogs()" in body
    assert "if len(catalogs) == 1:" in body, "a single-catalog server must not start asking"
    assert "raise ValueError" in body


def test_the_ssas_client_defers_to_that_one_rule():
    """Two catalog resolvers existed; only the base one was repaired first. The
    subclass has to reach it or the fix is inert on the connector people use."""
    body = _function_source(CLIENTS / "analysis_services_client.py", "execute_query")
    assert "self._resolve_catalog(" in body
