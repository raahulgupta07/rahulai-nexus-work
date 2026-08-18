"""Pre-execution check of generated table references against the known catalog.

Why this exists
---------------
Generated SQL can name a table under the WRONG database/catalog/schema while
still being routed to a live connection. Nothing local rejects it, so the
mistake is only discovered by the server — measured at 24-42s on Microsoft
Fabric, where the failure text (``[42S02] Invalid object name 'dbo.x'``) names
no database at all because the client strips the leading ``DB.`` segment before
sending. The user waits half a minute for a red step that the retry loop then
silently repairs.

This module turns that round trip into a local error carrying the correct
fully-qualified name, so the very next attempt has what it needs. It is
connector-agnostic: the catalog it reads (``datasource_tables``) is shared by
every connector, and the same mistake is available to Snowflake
(``db.schema.table``), BigQuery (``project.dataset.table``), Databricks
(``catalog.schema.table``), Redshift and SQL Server the moment they connect.

★FAIL OPEN
----------
A false positive here blocks a legitimate query — strictly worse than the
latency bug being fixed. Every uncertainty therefore resolves to "allow":

* no catalog, an empty catalog, or a catalog that cannot be loaded  -> allow
* code that does not parse, or a query argument that is not a plain
  string literal (f-strings, concatenations, formats)               -> allow
* a reference we cannot attribute to exactly one data source        -> allow
* an unqualified (single-segment) reference                         -> allow
* a name the catalog matches under any suffix reading               -> allow
* a missing name with zero, or more than one, alternative location  -> allow

The ONLY blocking verdict is "this exact qualified name is absent from the
catalog AND exactly one catalog entry carries the same leaf table name". Never
"I could not find it" — only "I found it somewhere else".
"""

import ast
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# One segment of a qualified name: a bare identifier, or a bracket/double-quote/
# backtick quoted one (SQL Server, ANSI, MySQL respectively). Anything else
# (parameters, temp tables, `{placeholders}`, subqueries) simply fails to match
# and is skipped — which is the fail-open direction.
_SEGMENT = r'(?:[A-Za-z_][A-Za-z0-9_$#@]*|\[[^\[\]]+\]|"[^"]+"|`[^`]+`)'

# A table reference is what follows FROM / JOIN. Trailing aliases, ON clauses
# and parentheses are outside the match by construction.
_TABLE_REF_REGEX = re.compile(
    r'(?:\bFROM|\bJOIN)\s+(' + _SEGMENT + r'(?:\s*\.\s*' + _SEGMENT + r')*)',
    re.IGNORECASE,
)

# Names of the query surface a data-source client exposes to generated code.
_QUERY_METHODS = frozenset({'execute_query', 'query'})

# The dict names the codegen prompt teaches. Anything else is not a data-source
# client as far as this check is concerned.
_CLIENT_DICTS = frozenset({'ds_clients', 'db_clients'})


@dataclass
class TableReference:
    """A qualified table name found in a query literal handed to a client."""

    raw: str
    parts: Tuple[str, ...]
    # Every client key the query could have been sent to. Usually one, but the
    # generated `ds_clients.get("A") or ds_clients.get("B")` fallback yields two
    # and which one wins is a runtime question. All of them must agree on the
    # catalog before any verdict is reached — see check_table_references.
    client_keys: Tuple[str, ...]


@dataclass
class CatalogEntry:
    """One catalog row, pre-split so matching never re-parses."""

    name: str
    parts: Tuple[str, ...]


@dataclass
class Catalog:
    """The known tables of one data source, keyed by the client that reaches it.

    ``database`` is the single database / catalog / project the client is bound
    to, lowercased, when that is a plain identifier — see `_container_name`. It
    is the ONLY thing that makes a surplus leading segment judgeable: a catalog
    that stores `PUBLIC.ORDERS` cannot by itself say whether
    `WRONG_DB.PUBLIC.ORDERS` is wrong, because the entry carries no database at
    all. None means "unknown", and every surplus segment is then allowed.
    """

    client_key: str
    entries: List[CatalogEntry] = field(default_factory=list)
    database: Optional[str] = None


def _strip_quotes(segment: str) -> str:
    segment = segment.strip()
    if len(segment) >= 2:
        if segment[0] == '[' and segment[-1] == ']':
            return segment[1:-1]
        if segment[0] == segment[-1] and segment[0] in ('"', '`', "'"):
            return segment[1:-1]
    return segment


def split_qualified_name(name: str) -> Tuple[str, ...]:
    """Split a possibly-quoted qualified name into lowercased segments.

    Both `.` and `/` separate. Power BI and Analysis Services store a catalog
    entry as `Dataset/Table`, so splitting on the dot alone leaves the whole
    string as one segment and its leaf table name is never comparable with a
    dotted reference to the same table.

    Returns an empty tuple when any segment comes out empty, which routes the
    caller into the allow branch rather than into a half-parsed comparison.
    """
    if not isinstance(name, str) or not name.strip():
        return ()
    parts = tuple(_strip_quotes(p).lower() for p in re.split(r'[./]', name))
    if any(not p for p in parts):
        return ()
    return parts


def _string_literal(node: ast.AST) -> Optional[str]:
    """The value of a plain string literal, or None for anything dynamic.

    JoinedStr (f-string), BinOp concatenation, `.format(...)` and `%` are all
    None on purpose: their runtime value is unknown here, and guessing at it is
    exactly how a false positive would be born.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _collect_bindings(
    tree: ast.AST,
) -> Tuple[Dict[str, Optional[str]], Dict[str, Optional[Tuple[str, ...]]]]:
    """Map local names to string literals and to client keys.

    Both maps use None as a poison value: a name assigned more than once, or
    assigned something we cannot read, is recorded as None so every later
    lookup gives up instead of using a stale first binding.
    """
    assigned: Dict[str, List[ast.AST]] = {}

    for node in ast.walk(tree):
        targets: Sequence[ast.AST]
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        elif isinstance(node, (ast.AugAssign, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith, ast.NamedExpr)):
            # Loop variables, `with ... as x`, `x += ...` and walrus bindings are
            # all values we cannot read statically. Record them as unreadable so
            # a same-named literal elsewhere can never be used for them.
            targets = _bound_names(node)
            value = None
        else:
            continue

        for target in targets:
            if isinstance(target, ast.Name):
                assigned.setdefault(target.id, []).append(value)

    literals: Dict[str, Optional[str]] = {}
    clients: Dict[str, Optional[Tuple[str, ...]]] = {}
    for name, values in assigned.items():
        # Bound more than once -> which binding reaches the call is a control
        # flow question this check does not answer. Give up on the name.
        only = values[0] if len(values) == 1 else None
        literals[name] = _string_literal(only) if only is not None else None
        clients[name] = _client_keys_from_expr(only) if only is not None else None

    return literals, clients


def _bound_names(node: ast.AST) -> List[ast.AST]:
    """Name targets bound by a statement we cannot evaluate."""
    if isinstance(node, ast.AugAssign):
        return [node.target]
    if isinstance(node, ast.NamedExpr):
        return [node.target]
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return [n for n in ast.walk(node.target) if isinstance(n, ast.Name)]
    if isinstance(node, (ast.With, ast.AsyncWith)):
        out: List[ast.AST] = []
        for item in node.items:
            if item.optional_vars is not None:
                out.extend(n for n in ast.walk(item.optional_vars) if isinstance(n, ast.Name))
        return out
    return []


def _is_client_dict(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id in _CLIENT_DICTS


def _client_keys_from_expr(node: ast.AST) -> Optional[Tuple[str, ...]]:
    """Every client key an expression could evaluate to, or None if unprovable.

    Covers the shapes the coder actually emits. Production overwhelmingly
    writes the `.get()` form, usually with an `or` fallback:

        ds_clients["key"]
        ds_clients.get("key")
        ds_clients.get("key", <default>)
        ds_clients.get("a") or ds_clients.get("b")

    Resolution must still PROVE the object came from the client map — a bare
    name, an attribute, a function return, or a `.get()` on some other dict all
    return None, which routes the caller into the allow branch.
    """
    if isinstance(node, ast.Subscript):
        if not _is_client_dict(node.value):
            return None
        key = _string_literal(node.slice)
        return (key,) if key else None

    if isinstance(node, ast.Call):
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != 'get':
            return None
        if not _is_client_dict(func.value):
            return None
        key = _string_literal(node.args[0]) if node.args else None
        if not key:
            return None
        keys = (key,)
        # A second positional is the fallback the call returns on a miss, so it
        # is as reachable as the key itself. `None` is inert (the attribute
        # access would fail before any query); another client expression folds
        # in; anything else is an object we cannot account for -> give up.
        if len(node.args) > 1:
            default = node.args[1]
            if isinstance(default, ast.Constant) and default.value is None:
                pass
            else:
                fallback = _client_keys_from_expr(default)
                if fallback is None:
                    return None
                keys = keys + fallback
        elif node.keywords:
            return None
        return keys

    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        # `a or b` returns whichever operand is truthy at runtime, so every
        # operand is a candidate. One unresolvable operand voids the whole
        # expression.
        keys: Tuple[str, ...] = ()
        for value in node.values:
            resolved = _client_keys_from_expr(value)
            if resolved is None:
                return None
            keys = keys + resolved
        return keys or None

    return None


def _resolve_client_keys(
    func_value: ast.AST,
    clients: Dict[str, Optional[Tuple[str, ...]]],
) -> Optional[Tuple[str, ...]]:
    direct = _client_keys_from_expr(func_value)
    if direct is not None:
        return direct
    if isinstance(func_value, ast.Name):
        return clients.get(func_value.id)
    return None


def _query_argument(call: ast.Call, literals: Dict[str, Optional[str]]) -> Optional[str]:
    """The SQL string of a query call, when it is statically knowable."""
    node: Optional[ast.AST] = None
    if call.args:
        node = call.args[0]
    else:
        for kw in call.keywords:
            if kw.arg in ('query', 'sql'):
                node = kw.value
                break
    if node is None:
        return None
    text = _string_literal(node)
    if text is not None:
        return text
    if isinstance(node, ast.Name):
        return literals.get(node.id)
    return None


def extract_table_references(code: str) -> List[TableReference]:
    """Qualified table names reached through a data-source client's query call.

    Extraction is deliberately narrow. It walks the AST rather than the source
    text, and only looks at the first argument of `execute_query` / `query`
    called on something that provably came out of the client map — see
    `_client_keys_from_expr` for the accepted shapes. That excludes, by
    construction and not by filtering:

    * comments and docstrings (never present in the AST as expressions we read)
    * `print("... FROM x ...")` and any other non-client call
    * pandas / numpy code, which never reaches a client's query method
    * dynamically built SQL, which yields no literal at all

    Unqualified names (CTEs, aliases, `#temp`, table functions) are dropped
    here, so they can never reach a blocking comparison.
    """
    if not isinstance(code, str) or not code.strip():
        return []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    literals, clients = _collect_bindings(tree)

    references: List[TableReference] = []
    seen: Set[Tuple[Tuple[str, ...], Tuple[str, ...]]] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _QUERY_METHODS:
            continue
        client_keys = _resolve_client_keys(node.func.value, clients)
        if not client_keys:
            continue
        sql = _query_argument(node, literals)
        if not sql:
            continue
        for match in _TABLE_REF_REGEX.finditer(sql):
            raw = re.sub(r'\s*\.\s*', '.', match.group(1).strip())
            parts = split_qualified_name(raw)
            # Show the name unquoted — backticks/brackets in the message would
            # collide with the backticks the message itself uses.
            raw = '.'.join(_strip_quotes(p) for p in raw.split('.')) or raw
            if len(parts) < 2:
                # Unqualified: nothing to be wrong ABOUT, and the common source
                # of a bare name is a CTE or an alias.
                continue
            marker = (client_keys, parts)
            if marker in seen:
                continue
            seen.add(marker)
            references.append(TableReference(raw=raw, parts=parts, client_keys=client_keys))

    return references


_PLAIN_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_$#@-]*$')


def _container_name(client: Any) -> Optional[str]:
    """The one database / catalog / project a client is bound to, lowercased.

    Read off the LIVE client object rather than out of `Connection.config`,
    because every connector spells the field differently and the client is the
    place that already resolved it: `database` (Postgres, MySQL, SQL Server,
    Snowflake, Redshift, Athena, ClickHouse, Vertica, Teradata, SAP HANA,
    Fabric, ADX, Pinot), `catalog` (Databricks, Trino, Presto, Spark, XMLA),
    `project_id` (BigQuery, GCP, PostHog).

    Returns None unless the value is a plain identifier. A DuckDB `database` is
    a FILE PATH and a Fabric federated client is bound to no single lakehouse at
    all; treating either as a container name would judge a correct query against
    a name the SQL could never have carried. None disables the surplus check.
    """
    for attribute in ('database', 'catalog', 'project_id'):
        value = getattr(client, attribute, None)
        if isinstance(value, str) and _PLAIN_IDENTIFIER.match(value.strip()):
            return value.strip().lower()
    return None


def _matches(
    reference: Tuple[str, ...],
    entry: Tuple[str, ...],
    database: Optional[str] = None,
) -> bool:
    """True when the two names can denote the same table under any suffix reading.

    Under-qualifying is always fine: catalogs store names at whatever depth the
    connector reported, and `dbo.orders` legitimately denotes
    `SalesDB.dbo.orders`.

    OVER-qualifying is the direction that carries the bug. When the reference is
    deeper than the entry, its surplus LEADING segment is a database the catalog
    never mentioned, and a bare suffix reading accepts any value there —
    `WRONG_DB.PUBLIC.ORDERS` against an entry of `PUBLIC.ORDERS`. That is why
    the check caught Fabric and Databricks (whose catalogs store all three
    segments, so the comparison is an exact one) and silently allowed Snowflake,
    BigQuery, Postgres and Trino, whose catalogs store two segments or one.

    So a surplus segment must be ACCOUNTED FOR, and it can only be accounted for
    against the container the client is actually bound to. Every uncertainty
    still allows:

    * no known container name                       -> allow
    * a surplus of more than one segment            -> allow (unreadable shape)
    * a surplus segment equal to the container name -> allow (correct name)
    """
    if reference == entry:
        return True
    if len(reference) <= len(entry):
        return entry[-len(reference):] == reference
    if reference[-len(entry):] != entry:
        return False
    surplus = reference[:-len(entry)]
    if database is None or len(surplus) != 1:
        return True
    return surplus[0] == database


def _agreed_catalog(
    client_keys: Tuple[str, ...],
    catalogs: Dict[str, Catalog],
    known_client_keys: Optional[Set[str]] = None,
) -> Optional[Catalog]:
    """The one catalog every LIVE candidate client agrees on, or None.

    `ds_clients.get("A") or ds_clients.get("B")` picks its client at runtime.
    If A and B are backed by different data sources, judging the query against
    either one would be a guess, so any disagreement gives up.

    ★A candidate that is not a key of `ds_clients` at all is DEAD, not unknown:
    `.get` returns None for it and the `or` falls through. The coder's habitual
    `get("Source:user-1") or get("Source")` fallback names a key that usually
    does not exist, and treating that as "unknown" would make the check inert
    on the exact shape production emits. `known_client_keys` is the authority on
    which keys exist; without it, every uncovered key is treated as unknown and
    the whole reference is allowed.
    """
    resolved: Optional[Catalog] = None
    for key in client_keys:
        catalog = catalogs.get(key)
        if catalog is None or not catalog.entries:
            if known_client_keys is not None and key not in known_client_keys:
                continue  # dead operand — cannot be the client at runtime
            return None
        if resolved is None:
            resolved = catalog
        elif (
            catalog.database != resolved.database
            or {e.name for e in catalog.entries} != {e.name for e in resolved.entries}
        ):
            return None
    return resolved


def check_table_references(
    code: str,
    catalogs: Dict[str, Catalog],
    known_client_keys: Optional[Set[str]] = None,
) -> Optional[str]:
    """A repair message when a reference is provably misqualified, else None.

    `catalogs` maps client key -> the COMPLETE table list of the data source
    behind it. Completeness is the contract: a truncated or filtered list would
    make a present table look absent, so callers must never pass a sampled one.

    `known_client_keys` is every key of `ds_clients`, used only to tell a dead
    `or` operand from an unknown client — see `_agreed_catalog`.
    """
    if not catalogs:
        return None

    problems: List[str] = []
    for reference in extract_table_references(code):
        catalog = _agreed_catalog(reference.client_keys, catalogs, known_client_keys)
        if catalog is None or not catalog.entries:
            # Unknown client, a source whose catalog has not synced, or an
            # `A or B` fallback whose two clients disagree about the tables.
            continue
        if any(
            _matches(reference.parts, entry.parts, catalog.database)
            for entry in catalog.entries
        ):
            continue
        leaf = reference.parts[-1]
        alternatives = sorted({
            entry.name for entry in catalog.entries
            if entry.parts and entry.parts[-1] == leaf
        })
        if len(alternatives) != 1:
            # 0 -> the catalog does not know this table at all; it may be a
            # view, a CTE we misread, or a stale sync. 2+ -> genuinely
            # ambiguous. Both allow.
            continue
        problems.append(
            # The catalog's own key, not the reference's first candidate — that
            # one may be a dead `or` operand that does not exist in ds_clients.
            f"`{reference.raw}` does not exist on client \"{catalog.client_key}\" — "
            f"that table is `{alternatives[0]}`."
        )

    if not problems:
        return None
    return (
        "Table reference check failed before execution. " + " ".join(problems) +
        " Use the EXACT fully-qualified name shown above in the FROM/JOIN clause "
        "(it is the name listed in the schema), then retry."
    )


async def load_catalogs(ds_clients: Optional[Dict[str, Any]], context_hub: Any) -> Dict[str, Catalog]:
    """Build the per-client catalog from `datasource_tables`.

    Reads the catalog table directly rather than the rendered schema excerpt:
    the excerpt is top-k filtered for the prompt, and a table missing only
    because it was trimmed would read as a table that does not exist.

    Returns an empty mapping on any failure, which the checker treats as
    "allow everything".
    """
    if not ds_clients or context_hub is None:
        return {}
    db = getattr(context_hub, 'db', None)
    data_sources = getattr(context_hub, 'data_sources', None)
    if db is None or not data_sources:
        return {}

    try:
        from sqlalchemy import select

        from app.models.datasource_table import DataSourceTable

        # Client keys are `<data source name>` or `<data source name>:<suffix>`
        # (see agent_v2's liveness filter), so a client is attributed to the
        # source whose name it carries. A key matching two sources is dropped.
        by_source: Dict[str, List[str]] = {}
        for key in ds_clients.keys():
            matched = [
                ds for ds in data_sources
                if getattr(ds, 'name', None)
                and (key == ds.name or key.startswith(f"{ds.name}:"))
            ]
            if len(matched) != 1:
                continue
            source_id = str(getattr(matched[0], 'id', '') or '')
            if source_id:
                by_source.setdefault(source_id, []).append(key)

        if not by_source:
            return {}

        result = await db.execute(
            select(DataSourceTable.datasource_id, DataSourceTable.name).where(
                DataSourceTable.datasource_id.in_(list(by_source.keys())),
                DataSourceTable.is_active.is_(True),
            )
        )

        entries_by_source: Dict[str, List[CatalogEntry]] = {}
        for source_id, name in result.all():
            parts = split_qualified_name(name)
            if not parts:
                continue
            entries_by_source.setdefault(str(source_id), []).append(
                CatalogEntry(name=name, parts=parts)
            )

        catalogs: Dict[str, Catalog] = {}
        for source_id, keys in by_source.items():
            entries = entries_by_source.get(source_id) or []
            if not entries:
                continue
            for key in keys:
                catalogs[key] = Catalog(
                    client_key=key,
                    entries=entries,
                    database=_container_name(ds_clients.get(key)),
                )
        return catalogs
    except Exception:
        return {}


async def canonicalize_table_names(
    db: Any,
    data_source_id: Any,
    names: Sequence[Any],
) -> List[Any]:
    """Rewrite model-supplied table names to the catalog's own spelling.

    An MCP caller hands `tables` straight through as `explicit_tables`, and the
    codegen prompt then presents that list as the names the planner RESOLVED
    against the catalog. Nothing had resolved them, so a model that mis-qualified
    a table was shown its own mistake back under an authoritative heading — the
    one input it has no reason to doubt.

    Resolution is per-name and conservative: a name is rewritten only when
    exactly ONE catalog entry can denote it under a suffix reading. Zero matches
    (a view, a stale sync, a table the catalog never learned) and two or more
    (genuinely ambiguous) both keep the caller's own string, so this can narrow a
    wrong name but never invent one. Any failure returns `names` unchanged.
    """
    if not names or db is None or not data_source_id:
        return list(names or [])
    try:
        from sqlalchemy import select

        from app.models.datasource_table import DataSourceTable

        result = await db.execute(
            select(DataSourceTable.name).where(
                DataSourceTable.datasource_id == str(data_source_id),
                DataSourceTable.is_active.is_(True),
            )
        )
        entries = [
            CatalogEntry(name=name, parts=split_qualified_name(name))
            for (name,) in result.all()
            if split_qualified_name(name)
        ]
        if not entries:
            return list(names)

        resolved: List[Any] = []
        for name in names:
            parts = split_qualified_name(name) if isinstance(name, str) else ()
            if not parts:
                resolved.append(name)
                continue
            candidates = sorted({
                entry.name for entry in entries if _matches(parts, entry.parts)
            })
            if not candidates:
                # No suffix reading works, so the qualifier itself is wrong —
                # the CFC shape, `CFC_Lakehouse.dbo.x` for a table living in
                # `LK_CFC_Sales`. Fall back to the leaf, under the same
                # exactly-one rule the blocking check uses for its repair
                # message. Still never invents: an unknown or ambiguous leaf
                # keeps the caller's string.
                candidates = sorted({
                    entry.name for entry in entries
                    if entry.parts and entry.parts[-1] == parts[-1]
                })
            resolved.append(candidates[0] if len(candidates) == 1 else name)
        return resolved
    except Exception:
        return list(names)


async def verify_table_references(
    code: str,
    ds_clients: Optional[Dict[str, Any]],
    context_hub: Any,
) -> Optional[str]:
    """Entry point: a repair message, or None to execute as generated.

    The whole body is guarded — a bug in this check must never be able to fail
    a run that would otherwise have succeeded.
    """
    try:
        catalogs = await load_catalogs(ds_clients, context_hub)
        if not catalogs:
            return None
        return check_table_references(code, catalogs, set((ds_clients or {}).keys()))
    except Exception:
        return None
