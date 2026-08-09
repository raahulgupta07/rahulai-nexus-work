import asyncio
import contextvars
import inspect
import io
import math
import os
import sys
import ast
import re
import threading
import time as _time
import pandas as pd
import numpy as np
import datetime
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from typing import Dict, Any, Tuple, List, Optional, Callable, Coroutine

from app.ai.http.safe_client import SafeHttpClient

# stdout capture for sandboxed user code.
#
# The old approach (`redirect_stdout` + a global lock) serialized the ENTIRE
# exec+generate_df window across all concurrent executions, because
# redirect_stdout mutates the process-global sys.stdout. The lock's comment
# argued user code is "CPU-bound by the GIL, so wall-clock impact is
# negligible" — untrue for generate_df, which runs I/O-bound warehouse
# queries that release the GIL. The lock therefore serialized every data
# fetch in the process: across concurrent completions, and across the
# parallel multi-tool batches introduced for agent_v2.
#
# Replacement: install a process-wide stdout ROUTER once. Each code-exec
# worker thread binds its own capture buffer; writes from a thread with a
# bound buffer go there, everything else falls through to the original
# stdout. No global mutation per execution → no lock → executions overlap.
# (Prints from the per-query timeout threads spawned inside generate_df go
# to the fallback, not the capture — client internals don't print, and the
# old behavior for those threads was "whichever buffer happened to be
# globally active", which was strictly worse.)
class _ThreadLocalStdoutRouter:
    """sys.stdout replacement routing writes to a per-thread buffer."""

    def __init__(self, fallback):
        self._fallback = fallback
        self._local = threading.local()

    def bind(self, buffer) -> None:
        self._local.buffer = buffer

    def unbind(self) -> None:
        self._local.buffer = None

    def _target(self):
        buf = getattr(self._local, "buffer", None)
        return buf if buf is not None else self._fallback

    def write(self, s):
        return self._target().write(s)

    def writelines(self, lines):
        return self._target().writelines(lines)

    def flush(self):
        try:
            return self._target().flush()
        except Exception:
            pass

    def isatty(self):
        try:
            return bool(self._target().isatty())
        except Exception:
            return False

    def __getattr__(self, name):
        return getattr(self._target(), name)


_STDOUT_ROUTER_INSTALL_LOCK = threading.Lock()


def _stdout_router() -> _ThreadLocalStdoutRouter:
    """Return the installed router, installing it idempotently.

    Re-checks sys.stdout each call: test harnesses (pytest capsys) and some
    servers swap sys.stdout at runtime; wrapping the current object keeps
    their capture working (writes fall through to it when no buffer bound).
    """
    with _STDOUT_ROUTER_INSTALL_LOCK:
        current = sys.stdout
        if isinstance(current, _ThreadLocalStdoutRouter):
            return current
        router = _ThreadLocalStdoutRouter(current)
        sys.stdout = router
        return router
from app.schemas.organization_settings_schema import OrganizationSettingsConfig, FeatureState
from app.data_sources import query_concurrency
from app.services.usage_policy_service import UsageLimitContext
from app.services.connection_rate_limit_service import connection_rate_limit_service
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.ai.context.builders.code_context_builder import CodeContextBuilder
from app.ai.schemas.codegen import CodeGenContext, CodeGenRequest
from app.ai.code_execution.loadables import extract_loadable_refs
from app.core.otel import get_tracer
from opentelemetry.trace import StatusCode
from app.errors.app_error import AppError
from app.errors.codes import ErrorCode

# Hard fallback when neither connection nor org settings define a value.
DEFAULT_QUERY_TIMEOUT_SECONDS = 60
#: Outer limit for one query when nothing else is configured. Fifteen minutes:
#: far above any healthy query, far below "forever". Only this ends a query —
#: DEFAULT_QUERY_TIMEOUT_SECONDS above is now a progress mark.
DEFAULT_HARD_TIMEOUT_SECONDS = 900
#: How often the wait loop wakes to check on a running query. Small enough that
#: the hard limit is honoured promptly, large enough not to spin.
_PROGRESS_TICK_SECONDS = 15

_tracer = get_tracer(__name__)

import logging
from sqlalchemy.exc import OperationalError

logger = logging.getLogger(__name__)


def _is_sqlite_lock_error(exc: Exception) -> bool:
    """True for SQLite's single-writer lock timeout (dev/sandbox databases)."""
    if not isinstance(exc, OperationalError):
        return False
    message = str(exc).lower()
    return "database is locked" in message or "database table is locked" in message

# Dedicated thread pool for user code execution.
# Keeps code-exec threads isolated from the default asyncio executor so that
# stuck DB/network calls in generated code cannot starve other server operations.
# When all workers are occupied, new submissions queue; the idle-timeout in the
# tool runner will cancel queued futures (via Future.cancel()) before they start,
# preventing unbounded queue growth.
_CODE_EXEC_POOL = ThreadPoolExecutor(
    max_workers=min(8, (os.cpu_count() or 4) * 2),
    thread_name_prefix="bow_code_exec",
)


# =============================================================================
# Security Exceptions
# =============================================================================

class CodeSecurityError(Exception):
    """Base exception for code security violations."""
    pass


class UnsafePythonError(CodeSecurityError):
    """Raised when Python code contains dangerous constructs."""
    pass


class UnsafeSQLError(CodeSecurityError):
    """Raised when SQL query contains dangerous operations."""
    pass


class QueryTimeoutError(AppError):
    """Raised when a wrapped client.execute_query exceeds its wall-clock budget.

    Caught by the surrounding exception handler in generate_and_execute_stream_v2
    and surfaced to the planner via captured_timings -> observation.db_message.
    The underlying DB query may keep running on the server until the connection
    is closed; we just stop waiting for it.
    """

    def __init__(self, timeout_seconds: int, sql: Optional[str] = None) -> None:
        message = (
            f"Query exceeded the {timeout_seconds}s hard limit and was abandoned. "
            "It was already reported as still running and given the full budget, "
            "so this is not a slow query — it is one that will not finish as "
            "written. Run multiple smaller queries instead of one large scan; "
            f"each execute_query call gets its own {timeout_seconds}s budget. "
            "Use LIMIT, narrower filters, or aggregation. Do NOT answer as "
            "though this query returned — say which part of the question it "
            "covered and that the data for it is missing."
        )
        super().__init__(
            ErrorCode.QUERY_TIMEOUT,
            message,
            status_code=408,
            params={"timeout_seconds": int(timeout_seconds)},
        )
        self.timeout_seconds = int(timeout_seconds)
        self.sql = sql


class SwallowedQueryError(AppError):
    """Raised when a query failed but the generated code returned an empty frame.

    Generated code that wraps `execute_query` in `try/except` and falls back to
    an empty DataFrame turns a hard failure into a successful-looking 0-row
    answer: the step is marked `success`, the retry loop never fires, and the
    widget renders correct-looking headers over no data. Worse, the step is then
    recorded as a successful use of those tables (`emit_table_usage_*` reads
    `step.status`), so the broken code becomes a "similar successful snippet"
    fed back into later codegen for the same tables.

    The prompts ask the model not to do this, but a prompt cannot be relied on
    to hold. This makes the swallow structurally impossible: the wrapper already
    records every failed query in `captured_timings`, so an empty result plus a
    failed query is caught here and raised, feeding the real error back into the
    retry loop.

    Deliberate trade-off: code that legitimately recovers from a failed query
    and then legitimately returns zero rows is also caught. That is the correct
    bias — when a query has failed, an empty result cannot be distinguished from
    a broken one, and a retry costs less than silently reporting "no data".
    """

    def __init__(self, errors: List[str]) -> None:
        detail = "; ".join(errors[:3])
        message = (
            "A query failed but the code returned an empty DataFrame instead of "
            f"letting the error surface. Underlying error(s): {detail}. "
            "Do NOT wrap execute_query in try/except that falls back to an empty "
            "DataFrame — fix the failing call so the query actually runs."
        )
        super().__init__(
            ErrorCode.QUERY_FAILED_SILENTLY,
            message,
            status_code=422,
            params={"query_errors": list(errors[:3])},
        )
        self.errors = list(errors)


def resolve_query_timeout(client, organization_settings) -> int:
    """Per-connection timeout resolution.

    Connection.config['query_timeout_seconds'] (stashed onto the client as
    `_bow_connection_query_timeout`) wins. Otherwise the org default; otherwise
    the hard fallback. A connection setting can only tighten the budget — values
    <= 0 are ignored at every layer.
    """
    conn_value = getattr(client, "_bow_connection_query_timeout", None)
    if isinstance(conn_value, (int, float)) and conn_value > 0:
        return int(conn_value)
    if organization_settings is not None:
        try:
            org_cfg = organization_settings.get_config("query_timeout_seconds")
            org_value = org_cfg.value if hasattr(org_cfg, "value") else org_cfg
            if isinstance(org_value, (int, float)) and org_value > 0:
                return int(org_value)
        except Exception:
            pass
    return DEFAULT_QUERY_TIMEOUT_SECONDS


def resolve_hard_timeout(client, organization_settings, soft_seconds: int) -> int:
    """The outer limit for one query — the only thing that actually ends it.

    ★The soft value above used to BE the kill. A query that ran past it was
    abandoned mid-flight: the wrapper stopped waiting, asked the source to
    cancel (best effort, frequently declined), and discarded whatever the
    thread was computing. A retry then started the identical scan again,
    alongside the first one still running on the warehouse. Six minutes spent,
    two live scans, nothing kept — and an answer built on whichever subset
    happened to finish.

    Same resolution order as the soft mark: a connection may tighten it, an org
    setting sets the default, and the constant is the floor of last resort.
    Never below the soft mark — a hard limit inside the progress mark would kill
    every query before it was ever reported as slow.
    """
    resolved = float(DEFAULT_HARD_TIMEOUT_SECONDS)
    conn_value = getattr(client, "_bow_connection_hard_timeout", None)
    if isinstance(conn_value, (int, float)) and conn_value > 0:
        resolved = float(conn_value)
    elif organization_settings is not None:
        try:
            org_cfg = organization_settings.get_config("query_hard_timeout_seconds")
            org_value = org_cfg.value if hasattr(org_cfg, "value") else org_cfg
            if isinstance(org_value, (int, float)) and org_value > 0:
                resolved = float(org_value)
        except Exception:
            pass
    return max(float(soft_seconds), resolved)


# =============================================================================
# AST-based Python Code Validation
# =============================================================================

# Modules that should never be imported
# Extensions read_text refuses: their bytes are a container, not text. Pointing
# the model at read_file (which has real extractors and an image fallback) beats
# handing back zip/PDF noise it will try to interpret.
_READ_TEXT_REFUSES = {"pdf", "docx", "pptx", "xlsx", "xls", "png", "jpg", "jpeg", "gif", "webp"}

# A single text read is capped so one call can't blow out memory or the frame
# built from it. Callers that need more should page with read_file.
READ_TEXT_MAX_CHARS = 5_000_000


def _build_read_text(excel_files):
    """`read_text(file_or_path)` for generated code, scoped to `excel_files`.

    The sandbox forbids `open`, so this is the only text reader — and it stays
    safe by resolving only against the files this run was handed. An arbitrary
    path is refused rather than read, which is the property that made banning
    `open` worth doing in the first place.
    """
    allowed = {}
    for f in (excel_files or []):
        path = getattr(f, "path", None)
        if path:
            allowed[str(path)] = f

    def read_text(file_or_path, encoding: str = "utf-8") -> str:
        path = getattr(file_or_path, "path", None) or str(file_or_path or "")
        if path not in allowed:
            raise ValueError(
                f"read_text: {path!r} is not one of this run's files. Pass an "
                "entry from `excel_files`, e.g. read_text(excel_files[0])."
            )
        name = str(getattr(allowed[path], "filename", "") or path)
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in _READ_TEXT_REFUSES:
            raise ValueError(
                f"read_text: {name} is a {ext} file, not text. Read it with the "
                "read_file tool instead — it has a proper extractor for this format."
            )
        with open(path, "r", encoding=encoding, errors="replace") as fh:
            content = fh.read(READ_TEXT_MAX_CHARS + 1)
        if len(content) > READ_TEXT_MAX_CHARS:
            return (
                content[:READ_TEXT_MAX_CHARS]
                + f"\n[TRUNCATED at {READ_TEXT_MAX_CHARS} chars — page the rest with the read_file tool]"
            )
        return content

    return read_text


FORBIDDEN_MODULES = frozenset({
    'os', 'subprocess', 'sys', 'shutil', 'importlib', 'builtins',
    'code', 'pty', 'socket', 'requests', 'urllib', 'urllib3', 'http',
    'httpx', 'aiohttp', 'httplib2', 'curl_cffi', 'ftplib',
    'telnetlib', 'smtplib', 'poplib', 'imaplib', 'nntplib',
    'multiprocessing', 'threading', 'concurrent', 'asyncio',
    'ctypes', 'cffi', 'pickle', 'shelve', 'marshal',
    'tempfile', 'pathlib', 'glob', 'fnmatch',
    'signal', 'resource', 'sysconfig', 'platform',
    'webbrowser', 'antigravity', 'this',
})

# Built-in functions that should never be called
FORBIDDEN_BUILTINS = frozenset({
    'eval', 'exec', 'compile', 'open', 'input',
    '__import__', 'globals', 'locals', 'vars',
    'getattr', 'setattr', 'delattr', 'hasattr',
    'breakpoint', 'exit', 'quit',
    'memoryview', 'bytearray',
})

# Library entry points that read straight off the filesystem. `open` is already
# forbidden, but pandas/numpy/pyarrow implement their own IO and never call it,
# so a hardcoded path would otherwise sidestep every data-access boundary.
#
# These readers are ALSO the sanctioned way to read uploaded files
# (`pd.read_excel(excel_files[0].path)` — see coder._file_access_rules), so the
# call itself must stay legal. Only a **literal** path is rejected: a real
# uploaded file always arrives as `excel_files[i].path`, never as a string the
# model typed out.
_FILE_IO_NAMESPACES = frozenset({'pd', 'pandas', 'np', 'numpy', 'pa', 'pyarrow', 'duckdb'})

# The only collections whose `.path` is a real uploaded file. A reader may open
# a path only when it derives from one of these — see
# CodeSecurityVisitor._is_sanctioned_path for why this is an allow-list and not
# a list of bad paths.
_SANCTIONED_FILE_COLLECTIONS = frozenset({'excel_files'})


def _build_safe_builtins() -> dict:
    """The builtins generated code may resolve, and no others.

    ★`exec(code, namespace)` with no `__builtins__` key makes CPython inject the
    REAL builtins module at runtime. The AST validator was therefore the only
    wall, and a denylist wall has to be complete to work — every builtin someone
    forgot to name was reachable. `type` was not on it, which is what made the
    type-graph walk possible in the first place.

    Supplying this dict changes the failure mode: a name that is not here cannot
    be resolved AT ALL, so forgetting to deny something is no longer a hole. That
    is the same default-deny shape as `services/file_formats.py` and as the
    provenance rule above.

    Chosen by MEASUREMENT, not taste — realistic `generate_df` bodies were run
    against this set: aggregation, try/except, coercion, enumerate/zip, isinstance
    dispatch, a locally-defined helper class, and min/max/any/all/filter. Anything
    that broke was added back deliberately:

      - `__build_class__` — without it `class Foo:` inside generate_df raises.
      - the exception classes — try/except is in almost every generated body, and
        a missing ValueError turns a handled case into a crash.
      - `type` — used for `type(v).__name__` dispatch. It stays callable; the
        attributes that turn it into a graph walk (`__base__`, `__getattribute__`)
        are blocked in FORBIDDEN_ATTRIBUTES instead. That is the narrower cut.

    Deliberately ABSENT: eval, exec, compile, open, input, __import__, globals,
    locals, vars, getattr, setattr, delattr, hasattr, breakpoint, exit, quit,
    memoryview, bytearray — the FORBIDDEN_BUILTINS set. They were already refused
    by the AST check; now they also cannot be reached by any spelling.
    """
    import builtins as _b

    names = (
        # data wrangling
        "len range dict list tuple set frozenset sum min max sorted reversed "
        "enumerate zip map filter any all abs round divmod pow "
        # types and coercion
        "str int float bool bytes complex isinstance issubclass repr format "
        "hash id ord chr hex oct bin type "
        # iteration, objects, output
        "iter next slice print callable object super staticmethod classmethod "
        "property __build_class__ "
        # exceptions — try/except appears in almost every generated body
        "Exception BaseException ValueError TypeError KeyError IndexError "
        "AttributeError ZeroDivisionError ArithmeticError RuntimeError "
        "StopIteration NotImplementedError OverflowError FloatingPointError "
        "LookupError NameError UnicodeDecodeError ImportError OSError IOError "
        "MemoryError RecursionError AssertionError"
    ).split()

    safe = {n: getattr(_b, n) for n in names if hasattr(_b, n)}
    # `__name__` is read by class machinery; a plain string is enough and leaks
    # nothing about the host module.
    safe["__name__"] = "generated_code"

    # ★An `import` STATEMENT compiles to a call to `__import__`, so dropping it
    # breaks `import pandas as pd` — which most generated bodies open with. That
    # is not hypothetical: removing it turned 5 passing tests red, all of them
    # ordinary analysis code, and the failure surfaced as
    # `ImportError: __import__ not found` from inside the model's own script.
    #
    # So `import` keeps working, through a wrapper that enforces the SAME
    # FORBIDDEN_MODULES the AST check enforces. The AST check catches the import
    # written literally; this catches it however it arrives, and the two agree
    # by construction because both read the one frozenset.
    _real_import = _b.__import__

    def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = (name or "").split(".")[0]
        if root in FORBIDDEN_MODULES:
            raise ImportError(
                f"import of {name!r} is not permitted in generated code"
            )
        return _real_import(name, globals, locals, fromlist, level)

    safe["__import__"] = _guarded_import
    return safe
FORBIDDEN_FILE_READERS = frozenset({
    'read_parquet', 'read_csv', 'read_json', 'read_excel', 'read_table',
    'read_feather', 'read_orc', 'read_hdf', 'read_pickle', 'read_sas',
    'read_stata', 'read_spss', 'read_fwf', 'read_html', 'read_xml',
    'load', 'fromfile', 'loadtxt', 'genfromtxt', 'memmap',
    'connect', 'read_csv_auto', 'read_ndjson',
})

# Attribute access patterns that indicate sandbox escape attempts
FORBIDDEN_ATTRIBUTES = frozenset({
    '__class__', '__bases__', '__mro__', '__subclasses__',
    '__globals__', '__code__', '__closure__', '__func__',
    '__self__', '__dict__', '__builtins__', '__import__',
    '__loader__', '__spec__', '__path__', '__file__',
    '__cached__', '__annotations__',
    # ★Added 2026-08-09. `__bases__` was blocked but its SINGULAR sibling
    # `__base__` was not, and `getattr` the builtin was blocked while the
    # dunder METHOD that does the same job was not. Together they walked the
    # type graph with no forbidden name touched at all:
    #
    #     type(()).__base__.__getattribute__(obj, '__subclasses__')()
    #
    # Verified against this very validator: it returned zero errors. Reaching
    # the subclass list was confirmed; a complete chain from there to a
    # credential was NOT demonstrated (the usual next hop, `__init__.__globals__`,
    # is blocked above) — so this is closing a proven bypass of the wall, not a
    # proven exfiltration.
    #
    # ★`type` itself stays a legal CALL: generated code uses `type(v).__name__`
    # for dispatch, and banning it breaks real analyses. Blocking the attributes
    # that turn a type into a graph walk is the narrower cut.
    '__base__', '__getattribute__', '__getattr__', '__setattr__', '__delattr__',
    '__reduce__', '__reduce_ex__', '__init_subclass__', '__subclasshook__',
    '__weakref__', '__module__',
})


class CodeSecurityVisitor(ast.NodeVisitor):
    """AST visitor that checks for dangerous code patterns."""

    def __init__(self):
        self.errors: List[str] = []
        # Names currently bound to a sanctioned uploaded-file path, and names
        # bound by `for f in excel_files`. Both are scope-insensitive on
        # purpose: erring toward "this name might be a real file path" only ever
        # makes the check MORE permissive about legitimate code, never about
        # where a path may point — a name that was never derived from
        # excel_files is not in either set, so an arbitrary path stays refused.
        self._sanctioned_path_names: set = set()
        self._file_bound_names: set = set()

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            module_name = alias.name.split('.')[0]
            if module_name in FORBIDDEN_MODULES:
                self.errors.append(f"Forbidden import: '{alias.name}'")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            module_name = node.module.split('.')[0]
            if module_name in FORBIDDEN_MODULES:
                self.errors.append(f"Forbidden import: 'from {node.module}'")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # Check for forbidden built-in calls like eval(), exec(), open()
        if isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_BUILTINS:
                self.errors.append(f"Forbidden function call: '{node.func.id}()'")

        # Check for __import__('os') style calls
        if isinstance(node.func, ast.Name) and node.func.id == '__import__':
            self.errors.append("Forbidden function call: '__import__()'")

        # Reading a HARDCODED path through an injected library. `open` is
        # already banned, but pandas/numpy do their own IO and never touch it,
        # so `pd.read_parquet('/app/uploads/...')` would otherwise sidestep
        # every data-access boundary the app enforces. Accelerated artifacts
        # are encrypted (so this is depth, not the boundary itself), but the
        # attempt should fail loudly rather than quietly return something.
        #
        # Reading an UPLOADED file with the same functions stays legal, because
        # its path arrives as `excel_files[i].path` rather than a literal.
        if isinstance(node.func, ast.Attribute):
            base = node.func.value
            base_name = base.id if isinstance(base, ast.Name) else None
            if base_name in _FILE_IO_NAMESPACES and node.func.attr in FORBIDDEN_FILE_READERS:
                first_arg = node.args[0] if node.args else None
                if not self._is_sanctioned_path(first_arg):
                    self.errors.append(
                        f"Forbidden file read: '{base_name}.{node.func.attr}()' with a "
                        f"path that does not come from an uploaded file — read "
                        f"uploaded files via excel_files[i].path and source data "
                        f"via ds_clients"
                    )

        self.generic_visit(node)

    # ── provenance, not pattern-matching ─────────────────────────────────────
    #
    # ★This check used to reject only `ast.Constant` and `ast.JoinedStr`, i.e. a
    # path spelled as ONE literal or an f-string. Every other expression that
    # produces the same string sailed through, because a BinOp is simply a
    # different node type:
    #
    #     pd.read_csv('/etc/' + 'passwd')                  # BinOp
    #     pd.read_csv(''.join(['/proc/self/', 'environ']))  # Call
    #     p = '/app/backend/.env'; pd.read_csv(p)           # Name
    #
    # Measured 2026-08-09 in an isolated container: the validator passed
    # `pd.read_csv('/proc/self/' + 'environ', sep='\x00')` with zero errors, and
    # that call recovered a marker planted in the launch environment. Since
    # DASH_ENCRYPTION_KEY is set at container launch it sits in that same file —
    # and the same key signs session JWTs, so one prompt-injected generate_df
    # yields both every stored credential and the ability to forge any session.
    #
    # A denylist of bad path shapes cannot be completed; there are always more
    # ways to build a string. So the rule is inverted to match the comment this
    # module already carried: a reader may open a path only when that path
    # DERIVES FROM an uploaded file. Everything else is refused by construction,
    # which is the same default-deny shape as services/file_formats.py.
    def _is_sanctioned_path(self, node) -> bool:
        """True only for `excel_files[...].path`, or a name bound to one.

        Deliberately narrow. Widening it means widening what generated code may
        open, so a new legitimate shape should be added here explicitly and with
        a test, never by loosening the rule.
        """
        if node is None:
            # No positional path at all (e.g. duckdb.connect()) — nothing to
            # open from the filesystem, so nothing to authorize.
            return True

        # excel_files[i].path  /  excel_files[i].path.strip() etc. resolve down
        # to the same attribute access on a subscript of `excel_files`.
        if isinstance(node, ast.Attribute) and node.attr == "path":
            target = node.value
            if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                if target.value.id in _SANCTIONED_FILE_COLLECTIONS:
                    return True
            # `f.path` where f came from `for f in excel_files`
            if isinstance(target, ast.Name) and target.id in self._file_bound_names:
                return True

        # A name previously assigned from a sanctioned path.
        if isinstance(node, ast.Name) and node.id in self._sanctioned_path_names:
            return True

        return False

    def visit_Assign(self, node: ast.Assign):
        """Track `p = excel_files[0].path` so the name stays usable.

        Only a DIRECT assignment from a sanctioned expression propagates. A name
        built by concatenation or any other computation is never sanctioned, so
        laundering a path through a variable does not work.
        """
        if self._is_sanctioned_path(node.value):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    self._sanctioned_path_names.add(tgt.id)
        else:
            # Re-binding a previously sanctioned name to something else must
            # REVOKE it, or `p = excel_files[0].path; p = '/app/.env'` passes.
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    self._sanctioned_path_names.discard(tgt.id)
        self.generic_visit(node)

    def visit_For(self, node: ast.For):
        """`for f in excel_files:` makes `f.path` sanctioned inside the loop."""
        if isinstance(node.iter, ast.Name) and node.iter.id in _SANCTIONED_FILE_COLLECTIONS:
            if isinstance(node.target, ast.Name):
                self._file_bound_names.add(node.target.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        # Check for direct access to forbidden attributes like obj.__class__
        if node.attr in FORBIDDEN_ATTRIBUTES:
            self.errors.append(f"Forbidden attribute access: '{node.attr}'")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant):
        # Check string literals for dangerous SQL operations. Uses the
        # structural regex so prose like "create a chart" or "update the
        # description" isn't flagged — we only match keywords that appear in
        # real SQL context (CREATE TABLE, DELETE FROM, UPDATE x SET, ...).
        if isinstance(node.value, str) and len(node.value) > 5:
            match = _FORBIDDEN_SQL_IN_STRING_REGEX.search(node.value)
            if match:
                snippet = node.value[:50].replace('\n', ' ')
                self.errors.append(
                    f"Forbidden SQL operation '{match.group()}' in string: \"{snippet}...\""
                )
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr):
        # Check f-string parts for dangerous SQL using the same structural
        # regex — prose inside f-strings shouldn't trip the validator either.
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                match = _FORBIDDEN_SQL_IN_STRING_REGEX.search(part.value)
                if match:
                    snippet = part.value[:50].replace('\n', ' ')
                    self.errors.append(
                        f"Forbidden SQL operation '{match.group()}' in f-string: \"{snippet}...\""
                    )
        self.generic_visit(node)


def validate_python_code(code: str) -> None:
    """
    Validate Python code for security issues using AST analysis.

    Raises:
        UnsafePythonError: If the code contains dangerous constructs.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        # Let syntax errors pass through - they'll fail at exec() time
        # with a more descriptive error
        return

    visitor = CodeSecurityVisitor()
    visitor.visit(tree)

    if visitor.errors:
        raise UnsafePythonError(
            f"Code contains forbidden constructs: {'; '.join(visitor.errors)}"
        )


# =============================================================================
# SQL Query Validation
# =============================================================================

# SQL keywords that indicate write/modify operations
FORBIDDEN_SQL_PATTERNS = [
    r'\bINSERT\b',
    r'\bUPDATE\b',
    r'\bDELETE\b',
    r'\bDROP\b',
    r'\bTRUNCATE\b',
    r'\bALTER\b',
    r'\bCREATE\b',
    r'\bGRANT\b',
    r'\bREVOKE\b',
    r'\bEXEC\b',
    r'\bEXECUTE\b',
    r'\bMERGE\b',
    r'\bCALL\b',
    r'\bREPLACE\b',
    r'\bLOAD\b',
    r'\bINTO\s+OUTFILE\b',
    r'\bINTO\s+DUMPFILE\b',
]

# Pre-compile regex for performance
_FORBIDDEN_SQL_REGEX = re.compile(
    '|'.join(FORBIDDEN_SQL_PATTERNS),
    re.IGNORECASE
)

# Structural SQL-write patterns — used when scanning Python string literals so
# prose like "the user wants to create a chart" or "delete outdated rows from
# the description" doesn't trigger the bare-verb match above. Each pattern
# requires the keyword to sit next to a syntactic partner that real SQL always
# has (TABLE/VIEW/INTO/FROM/SET/...), which prose basically never does.
_FORBIDDEN_SQL_IN_STRING_PATTERNS = [
    r'\bCREATE\s+(OR\s+REPLACE\s+)?(TEMP(ORARY)?\s+)?(TABLE|VIEW|INDEX|DATABASE|SCHEMA|FUNCTION|PROCEDURE|TRIGGER|SEQUENCE|ROLE|USER|MATERIALIZED)\b',
    r'\bDROP\s+(TABLE|VIEW|INDEX|DATABASE|SCHEMA|FUNCTION|PROCEDURE|TRIGGER|SEQUENCE|COLUMN|CONSTRAINT|ROLE|USER)\b',
    r'\bALTER\s+(TABLE|VIEW|INDEX|DATABASE|SCHEMA|COLUMN|SEQUENCE|ROLE|USER)\b',
    r'\bTRUNCATE\s+(TABLE\s+)?\w+',
    r'\bINSERT\s+INTO\b',
    r'\bUPDATE\s+[\w.`"\[\]]+(\s+AS\s+\w+|\s+\w+)?\s+SET\b',
    r'\bDELETE\s+FROM\b',
    r'\bMERGE\s+INTO\b',
    r'\bREPLACE\s+INTO\b',
    r'\bGRANT\s+[\w,\s*]+\s+ON\b',
    r'\bREVOKE\s+[\w,\s*]+\s+(ON|FROM)\b',
    r'\bEXEC(UTE)?\s+(\w+\.)*\w+',
    r'\bCALL\s+\w+\s*\(',
    r'\bLOAD\s+DATA\b',
    r'\bINTO\s+OUTFILE\b',
    r'\bINTO\s+DUMPFILE\b',
]
_FORBIDDEN_SQL_IN_STRING_REGEX = re.compile(
    '|'.join(_FORBIDDEN_SQL_IN_STRING_PATTERNS),
    re.IGNORECASE,
)


def estimate_result_size_bytes(result: Any) -> int:
    """Best-effort size of the result payload exposed to generated code."""
    if result is None:
        return 0
    if isinstance(result, bytes):
        return len(result)
    if isinstance(result, str):
        return len(result.encode("utf-8"))
    if isinstance(result, pd.DataFrame):
        try:
            return len(result.to_json(orient="records", date_format="iso").encode("utf-8"))
        except Exception:
            return int(result.memory_usage(deep=True).sum())
    try:
        return len(json.dumps(result, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        return sys.getsizeof(result)


def validate_sql_query(query: str) -> None:
    """
    Validate SQL query to ensure it's read-only.

    Raises:
        UnsafeSQLError: If the query contains write/modify operations.
    """
    if not isinstance(query, str):
        return

    match = _FORBIDDEN_SQL_REGEX.search(query)
    if match:
        raise UnsafeSQLError(
            f"SQL query contains forbidden operation: '{match.group()}'. "
            "Only SELECT queries are allowed."
        )


# =============================================================================
# Known DB-error remediation hints
# =============================================================================

# Some database errors are cryptic enough that the raw message alone doesn't
# tell the coder how to fix them, so it keeps regenerating the same broken
# query across retries. Each entry maps a case-insensitive signature found in
# the error text to an actionable hint appended to the retry feedback. Keep
# this list SHORT and high-signal — only errors where the fix is unambiguous.
_DB_ERROR_HINTS: List[Tuple[str, str]] = [
    (
        "ORA-12704",
        # Oracle character set mismatch: an expression combines national-charset
        # text (NVARCHAR2/NCHAR/NCLOB) with database-charset text (VARCHAR2/CHAR/
        # CLOB/a plain literal), usually across a UNION/UNION ALL, CASE/DECODE/
        # COALESCE/NVL, concatenation, or comparison.
        "Hint: ORA-12704 means the query combines text of two different Oracle "
        "character sets — a national-charset column (NVARCHAR2/NCHAR/NCLOB) mixed "
        "with a database-charset value (VARCHAR2/CHAR/CLOB or a plain 'literal'). "
        "This usually happens across a UNION/UNION ALL, CASE/DECODE/COALESCE/NVL, "
        "string concatenation (||), or comparison. Fix it by normalizing every "
        "text branch to ONE character set: wrap each NVARCHAR2/NCHAR column (and "
        "any mismatched literal) in TO_CHAR(...) so all branches share the "
        "database charset — e.g. SELECT TO_CHAR(a) ... UNION ALL SELECT TO_CHAR(b) ... "
        "Apply the conversion to EVERY branch of the offending expression, then retry."
    ),
]


def augment_db_error_hint(error_text: str) -> str:
    """Append a remediation hint when the error matches a known signature.

    Returns the error text unchanged when nothing matches. Reactive companion
    to the proactive guidance in each client's `description`: the hint rides
    the actual failure into the retry feedback, right next to the failing
    code, regardless of how the query was generated.
    """
    if not isinstance(error_text, str) or not error_text:
        return error_text
    haystack = error_text.upper()
    hints = [hint for signature, hint in _DB_ERROR_HINTS if signature.upper() in haystack]
    if not hints:
        return error_text
    return error_text + "\n" + "\n".join(hints)


# =============================================================================
# Query Capturing Wrapper (captures queries passed to execute_query)
# =============================================================================

# The ONLY attributes of a real database client that model-authored code may
# reach through the wrapper. Everything else raises AttributeError — see
# QueryCapturingClientWrapper.__getattr__ for the credential leak this closes.
#
# Derived by measurement, not taste: a sweep of every `ds_clients[...]` /
# `db_clients[...]` attribute access across backend/app/ai finds exactly
# `execute_query` (40 occurrences) and `execute_mcp` (10). `query` is handled by
# an explicit method on the wrapper above, so it never reaches here.
_CLIENT_PASSTHROUGH = frozenset({
    'execute_query',
    'execute_mcp',
})


class QueryCapturingClientWrapper:
    """Wrapper around a database client that captures all queries passed to execute_query.

    Works with any client that has an execute_query method (SQL, MongoDB, etc.).
    Optionally accumulates per-query wall-clock timing into captured_timings.

    ★Two budgets, and only one of them ends a query. `query_timeout_seconds` is
    a **progress mark**: passing it records `ran_long_seconds` on the timing
    entry so the planner and the operator can tell "alive" from "hung", and the
    query keeps running. `hard_timeout_seconds` is the outer limit and the only
    thing that raises QueryTimeoutError. It can never be below the progress
    mark — a hard limit inside it would kill every query before one was ever
    reported as slow.

    This docstring used to say the soft value raised, which is what it did
    before the split. Nine upstream tests were written against that sentence
    and kept passing `query_timeout_seconds` alone, so they silently ran on the
    900s default and failed `DID NOT RAISE`; see
    `tests/unit/fork/test_slow_query_survives.py` for why the split exists.

    The orphan thread is left daemon so it doesn't block process exit; the
    DB-side query may continue until the connection is closed.
    """

    def __init__(
        self,
        original_client,
        captured_queries: List[str],
        captured_timings: List[dict],
        usage_context: Optional[UsageLimitContext] = None,
        client_key: Optional[str] = None,
        query_timeout_seconds: int = DEFAULT_QUERY_TIMEOUT_SECONDS,
        hard_timeout_seconds: Optional[int] = None,
        max_concurrent_queries: Optional[int] = None,
        parked_queries: Optional[Dict[str, Any]] = None,
    ):
        self._original = original_client
        self._captured_queries = captured_queries
        self._captured_timings = captured_timings
        self._usage_context = usage_context
        self._client_key = client_key
        # ★float, not int. `int(0.5)` is 0, and 0 means "kill immediately" —
        # so a sub-second value configured anywhere (or passed by a test) used
        # to silently become the harshest possible setting instead of the
        # gentlest. Whole seconds are the norm; truncating them changes nothing.
        self._query_timeout_seconds = (
            float(query_timeout_seconds)
            if isinstance(query_timeout_seconds, (int, float)) and query_timeout_seconds > 0
            else float(DEFAULT_QUERY_TIMEOUT_SECONDS)
        )
        # The outer limit. The value above is a progress mark; only this ends a
        # query. Never below the soft mark — see resolve_hard_timeout.
        self._hard_timeout_seconds = max(
            self._query_timeout_seconds,
            float(hard_timeout_seconds)
            if isinstance(hard_timeout_seconds, (int, float)) and hard_timeout_seconds > 0
            else float(DEFAULT_HARD_TIMEOUT_SECONDS),
        )
        # Set by _call_with_timeout when it asks the source to cancel; surfaced
        # on the timing entry so a timeout shows whether the query is still
        # running on the database or was actually stopped.
        self._last_cancel_outcome: Optional[str] = None
        # How long a query had been running when it was last reported as slow.
        self._last_progress_seconds: Optional[int] = None
        # Abandoned queries from THIS RUN, keyed by connection + SQL, so an
        # identical retry waits on the running scan instead of starting a
        # second one. Never shared across runs — see _park_orphan.
        #
        # ★Supplied by the caller, not owned here. It used to be `{}` on every
        # wrapper, which quietly defeated the whole mechanism: the retry loop
        # rebuilds wrappers on every attempt (`execute_code` calls
        # `wrap_clients_for_capture`), so attempt 2 never saw what attempt 1
        # parked and launched the duplicate scan parking exists to prevent.
        # `StreamingCodeExecutor` owns the map and lives for exactly one run by
        # one user, which is the widest scope that is still safe.
        self._parked: Dict[str, Any] = parked_queries if parked_queries is not None else {}
        self._max_concurrent_queries = (
            int(max_concurrent_queries)
            if isinstance(max_concurrent_queries, (int, float)) and max_concurrent_queries > 0
            else query_concurrency.DEFAULT_MAX_CONCURRENT_QUERIES
        )

    def execute_query(self, query: str, *args, **kwargs):
        """Intercept execute_query calls to capture the query string and wall-clock duration."""
        if isinstance(query, str):
            self._captured_queries.append(query)
        idx = len(self._captured_timings)
        _q_start = _time.monotonic()
        with _tracer.start_as_current_span("datasource.execute_query") as span:
            span.set_attribute("datasource.type", type(self._original).__name__)
            span.set_attribute("datasource.query_timeout_seconds", self._query_timeout_seconds)
            try:
                self._enforce_rate_limit(query)
                self._consume_query_quota(query)
                # Hold a per-connection concurrency slot for the duration of
                # the query. A burst queues here instead of arriving at the
                # source all at once; the wait budget is the same wall clock
                # the query itself would have been given, because a slot that
                # never opens in that time is a failure either way.
                span.set_attribute("datasource.max_concurrent_queries", self._max_concurrent_queries)
                # ★Waiting for a SLOT and being allowed to RUN are different
                # budgets. They used to be the same number, which was harmless
                # while that number was the kill. Now that a query may run for
                # the full hard limit, reusing it here would queue a burst for
                # fifteen minutes before any of them started. The wait to begin
                # stays on the progress mark.
                with query_concurrency.slot(
                    self._connection_id(),
                    self._max_concurrent_queries,
                    wait_seconds=self._query_timeout_seconds,
                    connection_name=getattr(self._original, "_bow_connection_name", None),
                ):
                    result = self._call_with_timeout(query, args, kwargs)
                _q_ms = (_time.monotonic() - _q_start) * 1000.0
                rows = len(result) if hasattr(result, '__len__') else None
                result_bytes = estimate_result_size_bytes(result)
                self._consume_data_bytes_quota(query, result_bytes, rows)
                if rows is not None:
                    span.set_attribute("datasource.result_rows", rows)
                span.set_attribute("datasource.result_bytes", result_bytes)
                _timing = {
                    "index": idx,
                    "query_ms": round(_q_ms, 1),
                    "rows": rows,
                    "result_bytes": result_bytes,
                    "sql": query[:500] if isinstance(query, str) else None,
                }
                if self._last_progress_seconds is not None:
                    # It passed the progress mark and still returned. Worth
                    # recording: this is the case that used to be a failure.
                    _timing["ran_long_seconds"] = self._last_progress_seconds
                    _timing["soft_timeout_seconds"] = self._query_timeout_seconds
                self._captured_timings.append(_timing)
                return result
            except QueryTimeoutError as e:
                _q_ms = (_time.monotonic() - _q_start) * 1000.0
                self._captured_timings.append({
                    "index": idx,
                    "query_ms": round(_q_ms, 1),
                    "rows": None,
                    "sql": query[:500] if isinstance(query, str) else None,
                    "error": str(e)[:200],
                    "error_type": "timeout",
                    "timeout_seconds": self._hard_timeout_seconds,
                    "soft_timeout_seconds": self._query_timeout_seconds,
                    "cancellation": self._last_cancel_outcome,
                })
                if self._last_cancel_outcome:
                    span.set_attribute("datasource.cancellation", self._last_cancel_outcome)
                span.set_status(StatusCode.ERROR, str(e))
                span.record_exception(e)
                raise
            except Exception as e:
                _q_ms = (_time.monotonic() - _q_start) * 1000.0
                self._captured_timings.append({
                    "index": idx,
                    "query_ms": round(_q_ms, 1),
                    "rows": None,
                    "sql": query[:500] if isinstance(query, str) else None,
                    "error": str(e)[:200],
                })
                span.set_status(StatusCode.ERROR, str(e))
                span.record_exception(e)
                raise

    def _call_with_timeout(self, query, args, kwargs):
        """Run original.execute_query in a daemon thread; abandon it on timeout.

        Threading is intentional rather than asyncio.wait_for: we're already
        inside a sync code-exec worker (user code is run via exec()), so we
        cannot await. ThreadPoolExecutor would risk pool exhaustion when many
        long queries pile up, hence a fresh per-call daemon thread.

        Abandoning the thread frees BOW but not the source: the statement keeps
        running there until it completes on its own. So before raising we ask
        the database to cancel it (`query_cancellation`), naming the thread we
        are about to orphan — the client may have other queries in flight and
        those must survive.
        """
        holder: Dict[str, Any] = {}

        def runner():
            try:
                holder["value"] = self._original.execute_query(query, *args, **kwargs)
            except BaseException as exc:
                holder["exc"] = exc

        t = threading.Thread(
            target=runner,
            name="bow_query_timeout_guard",
            daemon=True,
        )
        # An identical query already abandoned in this same tool execution is
        # still running on the source. Collect it rather than starting a second
        # scan of the same table beside the first.
        collected = self._collect_parked(query)
        if collected is not None:
            if "exc" in collected:
                raise collected["exc"]
            return collected.get("value")

        t.start()

        # ★Wait in slices rather than one join. The soft mark is a PROGRESS
        # REPORT — it records that the query is still running and keeps waiting.
        # Only the hard limit ends anything. Before this the soft value was the
        # kill, so a warehouse that needed four minutes could never answer at
        # all: the wrapper gave up at three, the thread carried on computing,
        # and its result was thrown away.
        waited = 0.0
        soft = float(self._query_timeout_seconds)
        hard = float(self._hard_timeout_seconds)
        self._last_progress_seconds = None
        while t.is_alive() and waited < hard:
            slice_seconds = min(_PROGRESS_TICK_SECONDS, hard - waited)
            t.join(slice_seconds)
            waited += slice_seconds
            if t.is_alive() and waited >= soft:
                # Recorded on the timing entry and logged. Both the planner and
                # the operator can see a query is alive rather than hung.
                self._last_progress_seconds = int(waited)
                logger.info(
                    "Query still running after %ss (hard limit %ss)", int(waited), int(hard)
                )

        if t.is_alive():
            self._last_cancel_outcome = self._cancel_orphan(t)
            self._park_orphan(query, t, holder)
            raise QueryTimeoutError(
                hard,
                sql=query if isinstance(query, str) else None,
            )
        if "exc" in holder:
            raise holder["exc"]
        return holder.get("value")

    def _park_key(self, query: str) -> Optional[str]:
        """Identity of a query for parking: this connection plus this SQL."""
        conn = self._connection_id()
        if not conn or not isinstance(query, str):
            return None
        import hashlib

        return f"{conn}:{hashlib.sha256(query.encode('utf-8')).hexdigest()}"

    def _park_orphan(self, query, thread: threading.Thread, holder: Dict[str, Any]) -> None:
        """Keep an abandoned query's thread so an identical retry can wait on it.

        ★The thread is still computing. Cancellation is best effort and sources
        routinely decline it, so the work continues either way — it was simply
        discarded, and the model's retry launched a SECOND scan of the same
        table alongside the first. Parking turns a wasted scan into one the
        retry can collect.

        ★Scoped to THIS RUN — the map is owned by `StreamingCodeExecutor`, which
        is constructed once per tool invocation, and handed to every wrapper it
        builds. It is deliberately not a cross-run cache: on a
        per-user-credentialed connection the same SQL run by two people can
        legitimately return different rows, and a shared result keyed on the SQL
        alone would serve one person's data to another. One run is one user, so
        that is the widest scope that stays safe.

        ★This used to say "scoped to THIS WRAPPER … the observed failure is an
        immediate identical retry, which this covers". It did not cover it. The
        retry loop calls `execute_code` again, `execute_code` calls
        `wrap_clients_for_capture`, and every attempt therefore got brand-new
        wrappers holding an empty `_parked` — so the mechanism only ever fired
        when one generated code blob ran the same SQL twice, and the case it was
        written for launched the duplicate scan anyway. See
        `tests/unit/fork/test_retry_does_not_rescan.py`.
        """
        key = self._park_key(query)
        if key is None:
            return
        self._parked[key] = (thread, holder)

    def _collect_parked(self, query) -> Optional[Dict[str, Any]]:
        """If this exact query is already in flight here, wait on it instead.

        Returns the holder once the parked thread finishes, or None when there
        is nothing parked or it is still running — in which case the caller
        starts fresh, exactly as before.
        """
        key = self._park_key(query)
        if key is None:
            return None
        parked = self._parked.get(key)
        if parked is None:
            return None
        thread, holder = parked
        remaining = float(self._hard_timeout_seconds)
        thread.join(remaining)
        if thread.is_alive():
            # Still going after a second full budget. Stop tracking it so the
            # next retry does not queue behind it forever.
            self._parked.pop(key, None)
            return None
        self._parked.pop(key, None)
        return holder

    def _cancel_orphan(self, thread: threading.Thread) -> str:
        """Best-effort source-side cancellation of an abandoned query.

        Never raises: the timeout is the outcome the caller cares about, and a
        failed cancel must not mask it. The returned description is recorded on
        the timing entry so "we stopped waiting" and "the source stopped" stay
        distinguishable in the trace.
        """
        try:
            from app.data_sources import query_cancellation

            ident = thread.ident
            if ident is None:
                return "not_running"
            outcome = query_cancellation.cancel_thread(self._original, ident)
            logger.info(
                "Query timed out after %ss; source cancellation: %s",
                self._query_timeout_seconds, outcome,
            )
            return outcome
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Could not request query cancellation: %s", e)
            return f"failed: {type(e).__name__}"

    def _enforce_rate_limit(self, query: str) -> None:
        """Hard-block this query if the connection is over its per-window rate
        limit (enterprise `connection_rate_limit`).

        Runs only when a usage context is present — i.e. the agent data-query
        path. Indexing / connection-test paths carry no usage context, so they
        are exempt automatically. RateLimitExceeded is not an OperationalError,
        so it propagates (the block) even on SQLite.
        """
        context = self._usage_context
        if context is None or context.session_maker is None:
            return
        connection_id = self._connection_id()
        if not connection_id:
            return
        try:
            context.run_blocking(
                connection_rate_limit_service.check_and_consume_with_context(
                    context,
                    connection_id=str(connection_id),
                    metadata=self._usage_metadata(query),
                )
            )
        except OperationalError as e:
            # SQLite-only best-effort, same policy as the usage recorder: a
            # locked bookkeeping write shouldn't crash the query. Enforcement
            # (RateLimitExceeded) is not an OperationalError and still propagates.
            if not _is_sqlite_lock_error(e):
                raise
            logger.debug("Skipping rate-limit check; SQLite is locked")

    def _consume_query_quota(self, query: str) -> None:
        """Enforce, then buffer — never write to the DB from the sandbox thread.

        The old shape ran the counter+event WRITE synchronously here; on SQLite
        it queued behind the agent's writer lock, waited out busy_timeout (30s)
        and got skipped — +30s per query with the metering lost anyway (see
        docs/feedback-loops/agent-latency-deep-dive.md). Enforcement is now a
        cached READ (`check_data_query`, WAL-safe, in-memory after first load);
        the usage event is buffered on the context and persisted by the same
        end-of-run flush() the token/cost buffers use.
        """
        context = self._usage_context
        if context is None or context.session_maker is None:
            return
        connection_id = self._connection_id()
        if not connection_id:
            return
        metadata = self._usage_metadata(query)
        try:
            # Raises UsageLimitExceeded when over quota — propagates, same as before.
            context.run_blocking(context.check_data_query(str(connection_id)))
        except OperationalError as e:
            # SQLite-only: the rare cache-refresh READ can still lose to a
            # locked DB in exotic states. Enforcement is best-effort there;
            # UsageLimitExceeded is not an OperationalError and still propagates.
            if not _is_sqlite_lock_error(e):
                raise
            logger.debug("Skipping data-query quota check; SQLite is locked")
        context.add_data_query(str(connection_id), metadata)

    def _consume_data_bytes_quota(self, query: str, result_bytes: int, rows: Optional[int]) -> None:
        context = self._usage_context
        if context is None or context.session_maker is None or result_bytes <= 0:
            return
        connection_id = self._connection_id()
        if not connection_id:
            return
        metadata = {
            **self._usage_metadata(query),
            "rows": rows,
            "result_bytes": result_bytes,
        }
        try:
            context.run_blocking(context.check_data_bytes(str(connection_id), result_bytes))
        except OperationalError as e:
            if not _is_sqlite_lock_error(e):
                raise
            logger.debug("Skipping data-bytes quota check; SQLite is locked")
        context.add_data_bytes(str(connection_id), result_bytes, metadata)

    def _connection_id(self) -> Optional[str]:
        connection_id = getattr(self._original, "_bow_connection_id", None)
        return str(connection_id) if connection_id else None

    def _usage_metadata(self, query: str) -> dict:
        return {
            "client_key": self._client_key or getattr(self._original, "_bow_client_key", None),
            "connection_name": getattr(self._original, "_bow_connection_name", None),
            "data_source_id": getattr(self._original, "_bow_data_source_id", None),
            "data_source_name": getattr(self._original, "_bow_data_source_name", None),
            "sql": query[:500] if isinstance(query, str) else None,
        }

    def query(self, query: str, *args, **kwargs):
        """Alias for execute_query.

        Model-generated code often calls `.query(...)` instead of
        `.execute_query(...)`. Route it through our own `execute_query` so the
        call is still captured, timed, and metered — delegating via __getattr__
        would hit the raw client and bypass all of that instrumentation.
        """
        return self.execute_query(query, *args, **kwargs)

    def __getattr__(self, name):
        """Delegate a SANCTIONED attribute to the original client; refuse the rest.

        ★This used to delegate everything (`return getattr(self._original, name)`),
        and the AST validator only ever rejected DUNDER attribute names — so every
        ordinary attribute of the raw client was readable from model-authored
        code, with no escape trick required:

            for k in db_clients:
                print(db_clients[k].password, db_clients[k].pg_uri)

        Every SQL client stores its credentials as plain attributes
        (`postgresql_client.py` — `self.password`, and `self.pg_uri` =
        "postgresql://user:password@host"), and 33 clients follow that shape. So
        one validator-clean line returned the plaintext credentials for every
        connected warehouse, straight into the step output the conversation shows.
        Confirmed by reading the code 2026-08-09; not executed against the live app.

        The allow-list is what generated code is actually told to call: a sweep of
        every `ds_clients[...]`/`db_clients[...]` usage in the AI prompts and code
        finds `execute_query` (40) and `execute_mcp` (10) and nothing else. Anything
        outside it raises AttributeError, which is what a missing attribute would
        have done anyway — no new failure mode for legitimate code.

        ★Do not "fix" a broken analysis by adding a credential-bearing name here.
        If a new capability is genuinely needed, expose a method on this wrapper
        that returns only what the model should see.
        """
        if name in _CLIENT_PASSTHROUGH:
            return getattr(self._original, name)
        raise AttributeError(
            f"{type(self._original).__name__!s} attribute {name!r} is not available "
            f"to generated code — use execute_query(...) to read data"
        )


def wrap_clients_for_capture(
    ds_clients: Dict,
    captured_queries: List[str],
    captured_timings: List[dict],
    usage_context: Optional[UsageLimitContext] = None,
    organization_settings: Optional[OrganizationSettingsConfig] = None,
    parked_queries: Optional[Dict[str, Any]] = None,
) -> Dict:
    """Wrap all database clients to capture queries and per-query timing.

    The per-query timeout is resolved per-client so that a single tool
    invocation hitting multiple connections gets the right value for each
    underlying database.

    `parked_queries` is the run's map of abandoned-but-still-running queries.
    Pass the same object on every attempt so a retry can collect the scan the
    previous attempt gave up on; omit it and each wrapper gets its own empty
    map, which is what silently defeated parking before. Never share one across
    runs — the entries are keyed by connection and SQL only, so on a
    per-user-credentialed connection a shared map would serve one person's rows
    to another.
    """
    wrapped = {}
    for key, client in (ds_clients or {}).items():
        if client is not None and hasattr(client, 'execute_query'):
            _soft = resolve_query_timeout(client, organization_settings)
            wrapped[key] = QueryCapturingClientWrapper(
                client,
                captured_queries,
                captured_timings,
                usage_context=usage_context,
                client_key=str(key),
                query_timeout_seconds=_soft,
                hard_timeout_seconds=resolve_hard_timeout(client, organization_settings, _soft),
                max_concurrent_queries=query_concurrency.effective_limit(client, organization_settings),
                parked_queries=parked_queries,
            )
        else:
            wrapped[key] = client
    return wrapped


class CodeExecutionManager:
    """
    Deprecated shim. Use StreamingCodeExecutor instead.
    Provides only minimal helpers to preserve imports.
    """
    def __init__(self, logger=None, project_manager=None, db=None, report=None, head_completion=None, widget=None, step=None, organization_settings: OrganizationSettingsConfig = None):
        self.logger = logger
        self.organization_settings = organization_settings
        # Other params are ignored; legacy API compatibility only

    async def generate_and_execute_with_retries(self, *args, **kwargs):
        raise RuntimeError("CodeExecutionManager.generate_and_execute_with_retries is deprecated. Use StreamingCodeExecutor.generate_and_execute_stream.")

    def execute_code(self, code: str, db_clients: Dict, excel_files: List, loadables: Optional[Dict] = None):
        executor = StreamingCodeExecutor(organization_settings=self.organization_settings, logger=self.logger)
        return executor.execute_code(code=code, ds_clients=db_clients, excel_files=excel_files, loadables=loadables)

    def format_df_for_widget(self, df: pd.DataFrame, max_rows: Optional[int] = None, for_artifact: bool = False) -> Dict:
        # DEF-004: forward the artifact-cap opt-in so this legacy shim can't
        # quietly re-apply the display cap to artifact-bound data.
        executor = StreamingCodeExecutor(organization_settings=self.organization_settings, logger=self.logger)
        return executor.format_df_for_widget(df=df, max_rows=max_rows, for_artifact=for_artifact)


def apply_readable_number_printing() -> None:
    """Make a printed DataFrame carry its full value.

    ★Generated code almost always ends with `print("df head:", df.head())`, and
    that printed text is the model's PRIMARY view of what its own query
    returned — the stdout log goes straight back into the conversation. Pandas
    prints floats at six significant figures and flips to scientific notation
    for large magnitudes, so a real total arrived back as:

        City Mart     2.332757e+09      (the true value is 2,332,757,360)
        Ocean         9.470862e+08      (the true value is 947,086,167)

    Measured on the live product, asking for net sales by banner three times:
    ranks 3, 4 and 5 came back exact every time and ranks 1 and 2 never did —
    once as "largest (see chart)", once reconstructed from a rounded millions
    column and out by 2,640, once as the word "highest". The model said as much
    in its own trace: "a couple of large figures were hard to read back
    cleanly." It was right, and it was reading what we handed it.

    Money here is Myanmar Kyat, where an ordinary total is ten digits, so this
    is crossed by everyday questions rather than by edge cases.

    This changes PRINTING only. The DataFrame, its dtype, the stored step data
    and every number the API returns are untouched — and integer columns are
    left alone, because a year rendered as 2,026.00 would be its own bug.

    ★A flat `,.2f` fixes the top of the scale by destroying the bottom of it.
    Two decimals carry a ten-digit total exactly and flatten every value below
    0.005 to `0.00`, which is strictly worse than the notation it replaced:
    `2.1e-05` is at least recoverable, `0.00` is a confident wrong answer, and
    the model reads it as "no conversions". Both magnitudes routinely share one
    frame — a total beside its share of the total — so one format has to serve
    both. See `_readable_number`.
    """
    try:
        pd.set_option("display.float_format", _readable_number)
    except Exception:  # never let a display preference break an analysis
        pass


# Below this magnitude a significant-digit format would write more decimals
# than anyone can read (1e-300 is 300 of them), and scientific notation is the
# honest rendering rather than a loss.
_SCIENTIFIC_BELOW = 1e-12
_SIGNIFICANT_DIGITS = 4


def _readable_number(v) -> str:
    """Render one float so its value can be read back out of the text.

    Three ranges, one rule — never print a digit the reader would have to
    invent, and never print one they cannot use:

      * 2 decimals wherever they are exact or the magnitude makes them
        sufficient (money, and anything at or above 1)
      * enough decimals for four significant digits below that, trailing
        zeros stripped, so 0.0034 stays 0.0034 rather than becoming 0.00
      * scientific notation only below 1e-12, where a fixed rendering is
        unreadable to a person and to the model alike
    """
    try:
        if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
            return str(v)
        magnitude = abs(v)
        if magnitude == 0 or magnitude >= 1 or round(v, 2) == v:
            return f"{v:,.2f}"
        if magnitude < _SCIENTIFIC_BELOW:
            return f"{v:.{_SIGNIFICANT_DIGITS - 1}e}"
        # math.floor(log10) gives the position of the leading digit; the number
        # of decimals that reaches _SIGNIFICANT_DIGITS of them follows from it.
        decimals = _SIGNIFICANT_DIGITS - 1 - math.floor(math.log10(magnitude))
        text = f"{v:,.{decimals}f}".rstrip("0")
        return text if not text.endswith(".") else text + "00"
    except Exception:  # a display format must never be able to fail a run
        return str(v)


def code_retries_setting(organization_settings, default: int = 2) -> int:
    """Org-configured codegen attempt count (`limit_code_retries`), clamped 1-10
    so an edited setting can't disable codegen or retry unboundedly."""
    try:
        cfg = organization_settings.get_config("limit_code_retries") if organization_settings else None
        val = int(getattr(cfg, "value", default) or default)
    except (TypeError, ValueError):
        val = default
    return max(1, min(10, val))


IDENTICAL_FAILURE_NOTICE = (
    "Stopping early: the last two attempts produced identical code AND an "
    "identical error, so another attempt cannot add any information. An exact "
    "repeat usually means the cause is not in the generated code — check the "
    "platform-side handling of the generated code, or the data source itself."
)


def _normalize_for_compare(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _repeated_identical_failure(code_and_error_messages) -> bool:
    """True when the last two attempts produced the same code AND the same error.

    A byte-identical repeat carries no new information, so burning the remaining
    attempts is pure cost — and, more usefully, an exact repeat is the signature
    of a fault OUTSIDE the model (a platform-side transform of the generated
    code, a dead endpoint), which no amount of rewriting can fix.

    Deliberately strict: same error with DIFFERENT code may still be converging,
    so that case is left to run its normal course.
    """
    if not code_and_error_messages or len(code_and_error_messages) < 2:
        return False
    try:
        prev_code, prev_err = code_and_error_messages[-2]
        last_code, last_err = code_and_error_messages[-1]
    except (TypeError, ValueError):  # unexpected shape — never block the loop
        return False
    return (_normalize_for_compare(prev_code) == _normalize_for_compare(last_code)
            and _normalize_for_compare(prev_err) == _normalize_for_compare(last_err))


class StreamingCodeExecutor:
    """
    Pure, tool-first streaming executor with retries. No project_manager/DB side-effects.
    """
    def __init__(
        self,
        organization_settings: OrganizationSettingsConfig = None,
        logger=None,
        context_hub=None,
        usage_context: Optional[UsageLimitContext] = None,
    ):
        self.organization_settings = organization_settings
        self.logger = logger
        self.context_hub = context_hub
        self.usage_context = usage_context
        # Set by execute_code_async on every call: where the last execution ran
        # ({"executed_on": "local"|"server", ...}), or None when the user has no
        # paired local runtime / the feature flag is off.
        self.last_execution_provenance: Optional[Dict] = None
        # Queries abandoned at their hard limit but still running at the source,
        # so a retry waits on the scan already in flight instead of starting a
        # second one beside it. Owned here because this object lives for exactly
        # one run by one user, while the wrappers that read it are rebuilt on
        # every attempt. ★Never widen past the run: entries are keyed by
        # connection and SQL alone, so on a per-user-credentialed connection a
        # shared map would hand one person's rows to another.
        self._parked_queries: Dict[str, Any] = {}

    def execute_code(self, *, code: str, ds_clients: Dict, excel_files: List,
                     captured_timings: Optional[List[dict]] = None,
                     captured_queries: Optional[List[str]] = None,
                     loadables: Optional[Dict] = None) -> Tuple[pd.DataFrame, str, List[str]]:
        """Execute Python code and return the resulting DataFrame, captured stdout log, and executed queries.

        captured_timings: if provided, per-query wall-clock timings are appended to this list.

        Security:
            - Validates Python code via AST analysis before execution
            - Checks all string literals for dangerous SQL operations (INSERT, DELETE, DROP, etc.)

        Returns:
            Tuple of (DataFrame, stdout_log, executed_queries) where executed_queries
            contains all query strings passed to client.execute_query() during execution.

        Raises:
            UnsafePythonError: If code contains forbidden imports, calls, or attributes
            UnsafeSQLError: If code contains SQL strings with write/modify operations
        """
        with _tracer.start_as_current_span("code_execution.execute_code") as span:
            span.set_attribute("code_execution.code_chars", len(code or ""))
            span.set_attribute("code_execution.clients", len(ds_clients or {}))
            span.set_attribute("code_execution.excel_files", len(excel_files or []))

            # Security: Validate Python code and SQL strings before execution
            validate_python_code(code)

            output_log = ""
            executed_queries: List[str] = captured_queries if captured_queries is not None else []
            _timings: List[dict] = captured_timings if captured_timings is not None else []

            # Wrap clients to capture all queries passed to execute_query.
            # ★These wrappers are rebuilt on every attempt, so the run's parked
            # queries are handed in rather than owned by them — otherwise a
            # retry cannot see the scan the previous attempt abandoned.
            wrapped_clients = wrap_clients_for_capture(
                ds_clients,
                executed_queries,
                _timings,
                self.usage_context,
                organization_settings=self.organization_settings,
                parked_queries=self._parked_queries,
            )

            # Inject a sync HTTP client when the org has web fetch enabled. The
            # client owns concurrency internally so model code never imports
            # asyncio/threading/socket (all of which are AST-forbidden).
            http_client = self._build_http_client()

            # Pre-resolved loadables (see loadables.py). Build pure in-memory
            # lookup closures — no DB/I/O happens inside the sandbox thread.
            # load_step is gated by the org's enable_load_step setting; when off
            # the closure raises so stray calls fail clearly.
            load_step, load_entity = self._build_loadable_closures(
                loadables, enable_load_step=self._load_step_enabled()
            )

            # ★Before anything the model wrote can print. See
            # apply_readable_number_printing: the stdout log IS how the model
            # reads its own result back, and pandas' default abbreviates any
            # large number out of usable precision.
            apply_readable_number_printing()

            local_namespace = {
                # ★Without this key CPython injects the REAL builtins at exec
                # time, leaving the AST denylist as the only wall. See
                # _build_safe_builtins: name resolution now fails for anything
                # not explicitly allowed, so a missed denial is no longer a hole.
                '__builtins__': _build_safe_builtins(),
                'pd': pd,
                'np': np,
                'db_clients': wrapped_clients,
                'excel_files': excel_files,
                'load_step': load_step,
                'load_entity': load_entity,
                # The only way to read a plain-text file in here. `open` is an
                # AST-forbidden builtin (and os/pathlib/glob are forbidden
                # imports), so before this a .txt/.log had no reader at all:
                # the model's only option was pd.read_csv, which on prose
                # returns a plausible-looking frame of nonsense instead of
                # failing. Scoped to the files this run was given.
                'read_text': _build_read_text(excel_files),
            }
            if http_client is not None:
                local_namespace['http'] = http_client

            if self.logger:
                self.logger.debug(f"Executing code:\n{code}")
            wait_started = _time.monotonic()
            router = _stdout_router()
            capture_started_at = _time.monotonic()
            stdout_capture = io.StringIO()
            router.bind(stdout_capture)
            try:
                # Span name + attributes kept from the lock era so existing
                # dashboards/queries keep working; lock_wait_ms is now just
                # the (near-zero) router install/lookup time, and a non-zero
                # regression here means the router got re-serialized somehow.
                with _tracer.start_as_current_span("code_execution.stdout_lock") as lock_span:
                    lock_span.set_attribute("code_execution.lock_wait_ms", round((capture_started_at - wait_started) * 1000.0, 3))
                    lock_span.set_attribute("code_execution.code_chars", len(code or ""))
                    exec(code, local_namespace)
                    generate_df = local_namespace.get('generate_df')
                    if not generate_df:
                        raise Exception("No generate_df function found in code")
                    df = self._invoke_generate_df(
                        generate_df, wrapped_clients, excel_files, http_client,
                        load_step=load_step, load_entity=load_entity,
                    )
                    output_log = stdout_capture.getvalue()
                    lock_span.set_attribute("code_execution.lock_held_ms", round((_time.monotonic() - capture_started_at) * 1000.0, 3))
            finally:
                router.unbind()
                stdout_capture.close()
            span.set_attribute("code_execution.query_count", len(executed_queries))
            span.set_attribute("code_execution.stdout_chars", len(output_log or ""))
            # An empty result on top of a failed query means the code swallowed
            # the error. Raise so the retry loop sees it instead of shipping a
            # 0-row "success". Checked after stdout is unbound so the model's own
            # printed error still reaches the execution log.
            self._raise_if_query_errors_were_swallowed(df, _timings, span=span)
            return df, output_log, executed_queries

    @staticmethod
    def _raise_if_query_errors_were_swallowed(df, timings: List[dict], span=None) -> None:
        """Turn a silently-empty result into a real failure.

        Only fires when BOTH hold: the returned frame is empty, and at least one
        `execute_query` call recorded an error. Either alone is legitimate — a
        query can correctly return no rows, and code can recover from a failed
        query and go on to return real data.
        """
        if df is None or not isinstance(df, pd.DataFrame) or not df.empty:
            return
        errors = [
            str(t.get("error"))
            for t in (timings or [])
            if isinstance(t, dict) and t.get("error")
        ]
        if not errors:
            return
        if span is not None:
            span.set_attribute("code_execution.swallowed_query_errors", len(errors))
        raise SwallowedQueryError(errors)

    def _build_http_client(self) -> Optional[SafeHttpClient]:
        """Return a SafeHttpClient when `enable_web_fetch` is on, else None."""
        from app.core.feature_flags import setting_enabled
        if not setting_enabled(self.organization_settings, "enable_web_fetch"):
            return None
        return SafeHttpClient()

    def _load_step_enabled(self) -> bool:
        """Whether `load_step` is enabled for this org (default off)."""
        from app.core.feature_flags import setting_enabled
        return setting_enabled(self.organization_settings, "enable_load_step")

    @staticmethod
    def _build_loadable_closures(loadables: Optional[Dict], *, enable_load_step: bool = True):
        """Build pure-lookup `load_step` / `load_entity` over a resolved registry.

        The registry maps the exact literal ref used in the code to a
        DataFrame. A miss raises a clear error naming what's available — it
        only fires for dynamic (non-literal) refs that bypassed pre-resolution.

        When `enable_load_step` is False the `load_step` closure is a defensive
        stub that always raises — the feature is advertised nowhere in that
        case, so any call is a stray one and should fail clearly (and feed the
        retry loop) rather than silently succeed. `load_entity` is unaffected.
        """
        reg = loadables or {}
        steps = reg.get("steps") or {}
        entities = reg.get("entities") or {}

        def load_step(id_or_name):
            if not enable_load_step:
                raise RuntimeError(
                    "load_step is disabled for this organization. "
                    "Do not call load_step; query the data source instead."
                )
            key = str(id_or_name)
            if key in steps:
                return steps[key].copy()
            raise KeyError(
                f"load_step({key!r}) is not available. "
                f"Loadable steps: {list(steps.keys())}. "
                f"Use a string-literal id or name so it can be pre-loaded."
            )

        def load_entity(id_or_name):
            key = str(id_or_name)
            if key in entities:
                return entities[key].copy()
            raise KeyError(
                f"load_entity({key!r}) is not available. "
                f"Loadable entities: {list(entities.keys())}. "
                f"Use a string-literal id or name so it can be pre-loaded."
            )

        return load_step, load_entity

    @staticmethod
    def _invoke_generate_df(
        fn: Callable, wrapped_clients: Dict, excel_files: List,
        http_client: Optional[SafeHttpClient],
        load_step: Optional[Callable] = None, load_entity: Optional[Callable] = None,
    ):
        """Call generate_df, binding injectables by parameter name.

        `ds_clients` and `excel_files` are always passed positionally. Any of
        `http`, `load_step`, `load_entity` are passed by keyword only when the
        function declares a parameter of that name — so legacy two-arg
        `(ds_clients, excel_files)` and three-arg `(…, http)` signatures keep
        working unchanged.
        """
        injectables = {
            "http": http_client,
            "load_step": load_step,
            "load_entity": load_entity,
        }
        try:
            names = set(inspect.signature(fn).parameters.keys())
        except (TypeError, ValueError):
            names = set()
        kwargs = {k: v for k, v in injectables.items() if k in names}
        return fn(wrapped_clients, excel_files, **kwargs)

    async def execute_code_async(self, *, code: str, ds_clients: Dict, excel_files: List,
                                 captured_timings: Optional[List[dict]] = None,
                                 captured_queries: Optional[List[str]] = None,
                                 loadables: Optional[Dict] = None) -> Tuple[pd.DataFrame, str, List[str]]:
        """Execution dispatch. Default: server sandbox (byte-identical to the
        historical path). When HYBRID_LOCAL_RUNTIME is ON and the requesting
        user has a connected local helper, the same code runs on their laptop
        instead (see app/services/local_runtime); any remote failure or
        unsupported job falls back to the server path so chat never breaks.

        Validates the code (AST gate + SQL string check) BEFORE either lane.
        ★That used to live only inside `execute_code`, i.e. the server path —
        so on any machine with a helper paired, unvalidated generated code was
        shipped to somebody's laptop and executed there, and the gate only ran
        in the branch that fires when the laptop is NOT available. `execute_code`
        keeps its own call: it is reached directly from elsewhere, and
        validating the same string twice costs nothing worth saving."""
        from app.settings.config import settings as _settings  # lazy: no import cycle
        validate_python_code(code)
        self.last_execution_provenance = None
        if getattr(_settings, "hybrid_local_runtime", False):
            prov: Dict = {}
            # A folder attached from the user's device is reachable ONLY through
            # their helper: ds_clients here has no "local:<folder>" entry, so a
            # server run would die on a KeyError. Such jobs are pinned local and
            # their failures are raised (LocalFolderUnavailable), not swallowed —
            # the agent must be able to say "your device is offline" rather than
            # silently produce a wrong answer from the wrong data.
            local_folders = []
            if getattr(_settings, "hybrid_local_folder_attach", False):
                try:
                    from app.services.local_runtime_exec import referenced_local_folders
                    local_folders = referenced_local_folders(code)
                except Exception:
                    local_folders = []
            try:
                remote = await self._try_run_remote(
                    code=code, ds_clients=ds_clients, excel_files=excel_files,
                    loadables=loadables, provenance_out=prov,
                    require_local=bool(local_folders), local_folders=local_folders,
                )
            except Exception:
                if local_folders:
                    self.last_execution_provenance = prov or None
                    raise
                remote = None  # never let the remote lane break execution
            # Empty dict => the user has no paired runtime at all: stay None so
            # nothing renders for them (the overwhelmingly common case).
            self.last_execution_provenance = prov or None
            if remote is not None:
                return remote
        return await self._run_server_async(
            code=code, ds_clients=ds_clients, excel_files=excel_files,
            captured_timings=captured_timings, captured_queries=captured_queries,
            loadables=loadables,
        )

    async def _try_run_remote(self, *, code: str, ds_clients: Dict, excel_files: List,
                              loadables: Optional[Dict] = None,
                              provenance_out: Optional[Dict] = None,
                              require_local: bool = False,
                              local_folders: Optional[List[str]] = None) -> Optional[Tuple[pd.DataFrame, str, List[str]]]:
        """Attempt execution on the user's paired local helper. Returns the
        (df, output_log, executed_queries) tuple on success, or None to fall
        back to the server sandbox (helper absent/busy/unsupported job).

        `provenance_out` is filled in place with where the code ran, for the
        "Computed on your device" chat badge."""
        from app.services.local_runtime_exec import try_run_remote  # lazy: no cycle
        return await try_run_remote(
            usage_context=self.usage_context,
            code=code,
            ds_clients=ds_clients,
            excel_files=excel_files,
            loadables=loadables,
            provenance_out=provenance_out,
            require_local=require_local,
            local_folders=local_folders,
        )

    async def _run_server_async(self, *, code: str, ds_clients: Dict, excel_files: List,
                                captured_timings: Optional[List[dict]] = None,
                                captured_queries: Optional[List[str]] = None,
                                loadables: Optional[Dict] = None) -> Tuple[pd.DataFrame, str, List[str]]:
        """Run execute_code in a thread so it doesn't block the event loop."""
        loop = asyncio.get_running_loop()
        if self.usage_context is not None:
            self.usage_context.loop = loop
        with _tracer.start_as_current_span("code_execution.execute_code_async") as span:
            span.set_attribute("code_execution.pool_max_workers", _CODE_EXEC_POOL._max_workers)
            span.set_attribute("code_execution.code_chars", len(code or ""))
            started = _time.monotonic()
            worker_context = contextvars.copy_context()

            def _run_execute_code():
                return worker_context.run(
                    self.execute_code,
                    code=code,
                    ds_clients=ds_clients,
                    excel_files=excel_files,
                    captured_timings=captured_timings,
                    captured_queries=captured_queries,
                    loadables=loadables,
                )

            result = await loop.run_in_executor(
                _CODE_EXEC_POOL,
                _run_execute_code,
            )
            span.set_attribute("code_execution.total_ms", round((_time.monotonic() - started) * 1000.0, 3))
            return result

    def get_df_info(self, df: pd.DataFrame) -> Dict:
        """Extract comprehensive information from a DataFrame."""
        def convert_to_native(obj):
            if isinstance(obj, (np.int64, np.int32, np.int16, np.int8)):
                return int(obj)
            if isinstance(obj, (np.float64, np.float32, np.float16)):
                return float(obj)
            if isinstance(obj, np.bool_):
                return bool(obj)
            if isinstance(obj, (np.datetime64, datetime.datetime, datetime.date)):
                return pd.Timestamp(obj).isoformat()
            if isinstance(obj, pd.Timestamp):
                return obj.isoformat()
            if isinstance(obj, datetime.time):
                return obj.isoformat()
            if isinstance(obj, (datetime.timedelta, pd.Timedelta)):
                return str(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            if isinstance(obj, uuid.UUID):
                return str(obj)
            # Fallback for any other non-JSON-serializable types
            try:
                json.dumps(obj)
                return obj
            except (TypeError, ValueError):
                return str(obj)
        def make_hashable(value: Any) -> Any:
            """
            Convert potentially unhashable values (dict, list, set, ndarray, Timestamp)
            into a hashable representation so nunique/value_counts won't crash.
            """
            try:
                # Fast path: already hashable
                hash(value)
                return value
            except Exception:
                pass
            # Normalize common container types
            if isinstance(value, (pd.Timestamp, datetime.date)):
                return pd.Timestamp(value).isoformat()
            if isinstance(value, np.ndarray):
                return tuple(value.tolist())
            if isinstance(value, (list, tuple)):
                try:
                    return tuple(make_hashable(v) for v in value)
                except Exception:
                    return tuple(str(v) for v in value)
            if isinstance(value, set):
                try:
                    return tuple(sorted(make_hashable(v) for v in value))
                except Exception:
                    return tuple(sorted(str(v) for v in value))
            if isinstance(value, dict):
                try:
                    # Stable, readable representation
                    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
                except Exception:
                    # Fallback to tuple of items
                    try:
                        return tuple(sorted((str(k), str(v)) for k, v in value.items()))
                    except Exception:
                        return str(value)
            # Final fallback
            try:
                return str(value)
            except Exception:
                return None

        info_dict = {
            "total_rows": int(len(df)),
            "total_columns": int(len(df.columns)),
            "column_info": {},
            "memory_usage": int(df.memory_usage(deep=True).sum()),
            "dtypes_count": {str(k): int(v) for k, v in df.dtypes.value_counts().items()},
        }
        # describe(include='all') may fail on unhashable objects (e.g., dict cells). Guard it.
        try:
            desc_dict = df.describe(include='all').to_dict()
        except Exception:
            desc_dict = {}
        for column in df.columns:
            column_info = {
                "dtype": str(df[column].dtype),
                "non_null_count": int(df[column].count()),
                "memory_usage": int(df[column].memory_usage(deep=True)),
                "null_count": int(df[column].isna().sum()),
                # nunique may fail for unhashable objects; fall back to a hashable projection
                "unique_count": 0,
            }
            try:
                column_info["unique_count"] = int(df[column].nunique(dropna=True))
            except Exception:
                try:
                    projected = df[column].map(make_hashable)
                    column_info["unique_count"] = int(projected.nunique(dropna=True))
                except Exception:
                    column_info["unique_count"] = 0
            if column in desc_dict:
                try:
                    stats = {stat: convert_to_native(value) for stat, value in desc_dict[column].items() if pd.notna(value)}
                    column_info.update(stats)
                except Exception:
                    # Best-effort; skip stats if conversion fails
                    pass
            info_dict["column_info"][column] = column_info
        return info_dict

    def format_df_for_widget(self, df: pd.DataFrame, max_rows: Optional[int] = None, for_artifact: bool = False) -> Dict:
        """Format a DataFrame into a widget-compatible structure.

        Uses pandas' native JSON serialization which handles datetime, time,
        timedelta, numpy types, NaN/NaT, and other edge cases robustly.

        Args:
            df: The DataFrame to format
            max_rows: Maximum rows to include. If None, uses an organization
                      setting (see `for_artifact`) or defaults to 1000.
            for_artifact: DEF-004. Select the ARTIFACT cap ('artifact_row_limit',
                      default 10000) instead of the display cap
                      ('limit_row_count', default 1000). A table on screen is
                      unreadable past a few hundred rows, but a chart is
                      comfortable with tens of thousands — and because the cut is
                      a PREFIX in the query's own sort order, the display cap
                      silently dropped a time-ordered result's most recent
                      periods from every chart built on it. Callers that render
                      to a table or feed the LLM preview must leave this False,
                      which keeps their behaviour byte-identical.
        """
        # DEF-004: which cap applies is the caller's choice; everything else about
        # the resolution (including "0 means no limit") is identical for both.
        setting_name = "artifact_row_limit" if for_artifact else "limit_row_count"
        default_rows = 10000 if for_artifact else 1000
        # Determine row limit: None means no limit (disabled)
        row_limit_disabled = False
        if max_rows is None:
            if self.organization_settings is not None:
                try:
                    limit_config = self.organization_settings.get_config(setting_name)
                    # "Set to 0 for no limit": a non-positive value means no cap,
                    # regardless of the persisted state flag (the state may be
                    # stored as ENABLED because the schema-level validator that
                    # maps <=0 to DISABLED does not run when a FeatureConfig is
                    # rebuilt through the settings-update path).
                    value = int(limit_config.value)
                    if limit_config.state == FeatureState.DISABLED or value <= 0:
                        row_limit_disabled = True
                    else:
                        max_rows = value
                except (AttributeError, TypeError, ValueError):
                    # Also covers an older settings row that predates
                    # 'artifact_row_limit': get_config returns None -> AttributeError
                    # -> the artifact default, rather than falling back to the
                    # display cap this split exists to escape.
                    max_rows = default_rows
            else:
                max_rows = default_rows
        columns = [{"headerName": str(col), "field": str(col)} for col in df.columns]
        if df.empty:
            rows = []
            df_info = {
                "total_rows": 0,
                "total_columns": int(len(df.columns)),
                "column_info": {str(col): {
                    "dtype": str(df[col].dtype),
                    "non_null_count": 0,
                    "memory_usage": 0,
                    "null_count": 0,
                    "unique_count": 0,
                } for col in df.columns},
                "memory_usage": int(df.memory_usage(deep=True).sum()),
                "dtypes_count": {str(k): int(v) for k, v in df.dtypes.value_counts().items()},
            }
        else:
            # Use pandas' native JSON serialization for robust type handling:
            # - date_format='iso' handles datetime, date, time, Timestamp
            # - default_handler=str catches anything else (UUID, Decimal, etc.)
            df_to_serialize = df if row_limit_disabled else df.head(max_rows)
            rows = json.loads(
                df_to_serialize.to_json(orient='records', date_format='iso', default_handler=str)
            )
            df_info = self.get_df_info(df)
        payload = {
            "rows": rows,
            "columns": columns,
            "loadingColumn": False,
            "info": df_info,
        }
        # DEF-004: `rows` is `df.head(<whichever cap applied>)` — a PREFIX in the query's own
        # ORDER BY, not a sample. A time-ordered result therefore loses its most
        # recent periods entirely. `info.total_rows` held the true count but nothing
        # said the rows were partial, so every consumer read them as the whole
        # dataset: a dashboard built on a 1,000-row prefix of 1,903 rows displayed
        # NET SALES 56.4B against a true 98.9B, and covered 10 of 17 months with no
        # indication. Declare it here, at the point of truncation.
        if not df.empty and len(rows) < len(df):
            payload["rows_truncated"] = True
            payload["rows_total"] = int(len(df))
        return payload

    async def generate_and_execute_stream(
        self,
        *,
        data_model: Dict,
        prompt: str,
        schemas: str,
        ds_clients: Dict,
        excel_files: List,
        code_context_builder: 'CodeContextBuilder',
        code_generator_fn: Callable,
        max_retries: int = 2,
        sigkill_event=None,
    ):
        """
        Async generator that yields dict events:
          { "type": "progress"|"stdout", "payload": {...} }
        At the end, returns (df, code, code_and_error_messages, execution_log)
        """
        retries = 0
        code_and_error_messages: List[Tuple[str, str]] = []
        final_code = ""
        exec_df = pd.DataFrame()
        execution_log = ""
        executed_successfully = False
        # Where the code actually ran (local runtime vs server); None when the
        # user has no paired device.
        execution_provenance: Optional[Dict] = None
        while retries < max_retries:
            # Every failure path below appends (code, error) and continues, so one
            # check here covers all of them without touching their branches.
            if _repeated_identical_failure(code_and_error_messages):
                yield {"type": "progress", "payload": {"stage": "stopped_identical_failure", "attempt": retries}}
                yield {"type": "stdout", "payload": IDENTICAL_FAILURE_NOTICE}
                break
            # Cooperative cancellation check at loop start
            if sigkill_event and hasattr(sigkill_event, 'is_set') and sigkill_event.is_set():
                break

            yield {"type": "progress", "payload": {"stage": "code_generation", "attempt": retries}}
            try:
                # Cancellation before expensive LLM call
                if sigkill_event and hasattr(sigkill_event, 'is_set') and sigkill_event.is_set():
                    break
                _t_codegen = _time.monotonic()
                final_code = await code_generator_fn(
                    data_model=data_model,
                    prompt=prompt,
                    schemas=schemas,
                    ds_clients=ds_clients,
                    excel_files=excel_files,
                    code_and_error_messages=code_and_error_messages,
                    memories="",
                    previous_messages="",
                    retries=retries,
                    sigkill_event=sigkill_event,
                    code_context_builder=code_context_builder,
                )
                codegen_ms = round((_time.monotonic() - _t_codegen) * 1000.0, 1)
                yield {"type": "progress", "payload": {"stage": "code_generated", "attempt": retries, "code": final_code, "timing": False}}
            except Exception as e:
                from app.ai.agents.coder.coder import CodegenRefused
                if isinstance(e, CodegenRefused):
                    # The coder declined, and the same files and the same rules
                    # would meet a second attempt — retrying only buys another
                    # invented answer. Same shape as LocalFolderUnavailable
                    # below: report the real reason and stop. The message names
                    # `read_file`, so the planner's next step is an action.
                    code_and_error_messages.append((final_code, e.reason))
                    yield {"type": "stdout", "payload": e.reason}
                    break
                msg = f"Code generation error: {str(e)}"
                code_and_error_messages.append((final_code, msg))
                yield {"type": "stdout", "payload": msg}
                retries += 1
                if retries < max_retries:
                    yield {"type": "progress", "payload": {"stage": "retry", "attempt": retries, "timing": False}}
                continue

            # Executing code
            yield {"type": "progress", "payload": {"stage": "data_query_execution", "attempt": retries}}
            try:
                # Cancellation before executing user code
                if sigkill_event and hasattr(sigkill_event, 'is_set') and sigkill_event.is_set():
                    break
                _t_exec = _time.monotonic()
                query_timings: List[dict] = []
                exec_df, execution_log, executed_queries = await self.execute_code_async(
                    code=final_code, ds_clients=ds_clients, excel_files=excel_files, captured_timings=query_timings
                )
                execution_provenance = getattr(self, "last_execution_provenance", None)
                execution_ms = round((_time.monotonic() - _t_exec) * 1000.0, 1)
                yield {
                    "type": "progress",
                    "payload": {
                        "stage": "post_execution",
                        "attempt": retries,
                        "execution_ms": execution_ms,
                    },
                }
                executed_successfully = True
                break
            except Exception as e:
                from app.services.local_runtime_exec import LocalFolderUnavailable
                if isinstance(e, LocalFolderUnavailable):
                    # Rewriting the code cannot conjure an offline laptop, and a
                    # retry would tempt the coder to "fix" it by silently
                    # switching to warehouse data. Report and stop.
                    code_and_error_messages.append((final_code, str(e)))
                    yield {"type": "stdout", "payload": str(e)}
                    break
                msg = augment_db_error_hint(f"Execution error: {str(e)}")
                code_and_error_messages.append((final_code, msg))
                yield {"type": "stdout", "payload": msg}
                retries += 1
                if retries < max_retries:
                    yield {"type": "progress", "payload": {"stage": "retry", "attempt": retries, "timing": False}}
                continue

        # If cancelled, emit a final done with empty results to let caller stop cleanly
        if sigkill_event and hasattr(sigkill_event, 'is_set') and sigkill_event.is_set():
            yield {
                "type": "done",
                "payload": {
                    "df": pd.DataFrame(),
                    "code": final_code,
                    "errors": code_and_error_messages,
                    # `errors` is the history of EVERY attempt, including ones a
                    # later attempt recovered from, and it is emitted on the success
                    # path too — so it cannot be used as a success test. This is the
                    # outcome of the run.
                    "executed_successfully": False,
                    "execution_log": execution_log,
                    "executed_queries": [],
                    "query_timings": [],
                    "codegen_ms": None,
                    "execution_ms": None,
                },
            }
            return
        else:
            # If we never executed successfully (e.g., validation failed up to max retries),
            # signal failure by returning df=None so callers can treat as error.
            if not executed_successfully and code_and_error_messages:
                yield {
                    "type": "done",
                    "payload": {
                        "df": None,
                        "code": final_code,
                        "errors": code_and_error_messages,
                        "executed_successfully": False,
                        "execution_log": execution_log,
                        "executed_queries": [],
                        "query_timings": [],
                        "codegen_ms": None,
                        "execution_ms": None,
                    },
                }
            else:
                # Emit a final done event carrying the results instead of returning values
                yield {
                    "type": "done",
                    "payload": {
                        "df": exec_df,
                        "code": final_code,
                        "errors": code_and_error_messages,
                        "executed_successfully": True,
                        "execution_log": execution_log,
                        "executed_queries": executed_queries,
                        "query_timings": query_timings,
                        "codegen_ms": codegen_ms,
                        "execution_ms": execution_ms,
                        "execution_provenance": execution_provenance,
                    },
                }

    async def generate_and_execute_stream_v2(
        self,
        *,
        request: CodeGenRequest,
        ds_clients: Dict,
        excel_files: List,
        code_context_builder: Optional['CodeContextBuilder'] = None,
        code_generator_fn: Callable = None,
        sigkill_event=None,
        loadable_resolver_fn: Optional[Callable] = None,
    ):
        """
        V2: Typed context-based generator. Yields the same event shapes as v1.
        """
        retries = 0
        # Respect explicit values (including 0→1). `or 2` was swallowing
        # retries=0 and silently running two attempts. Unset falls back to the
        # org's `limit_code_retries` setting.
        _req_retries = getattr(request, "retries", None)
        if _req_retries is not None:
            max_retries = max(1, int(_req_retries))
        else:
            max_retries = code_retries_setting(self.organization_settings)
        code_and_error_messages: List[Tuple[str, str]] = []
        final_code = ""
        exec_df = pd.DataFrame()
        execution_log = ""
        executed_successfully = False
        ctx: CodeGenContext = request.context
        # Derive prompt/schemas for legacy generator signature
        derived_prompt = ctx.user_prompt
        derived_interpreted_prompt = ctx.interpreted_prompt
        derived_schemas = ctx.schemas_excerpt

        # Hoisted so the wrapper's capture survives an exception inside
        # execute_code_async — the failure branch can surface the failing SQL.
        query_timings: List[dict] = []
        executed_queries: List[str] = []
        # Where the code actually ran (local runtime vs server). None for every
        # user without a paired device — the badge simply doesn't render.
        execution_provenance: Optional[Dict] = None

        while retries < max_retries:
            # Every failure path below appends (code, error) and continues, so one
            # check here covers all of them without touching their branches — in
            # particular the local-runtime dispatch inside execute_code_async is
            # left completely alone.
            if _repeated_identical_failure(code_and_error_messages):
                yield {"type": "progress", "payload": {"stage": "stopped_identical_failure", "attempt": retries}}
                yield {"type": "stdout", "payload": IDENTICAL_FAILURE_NOTICE}
                break
            if sigkill_event and hasattr(sigkill_event, 'is_set') and sigkill_event.is_set():
                break
            yield {"type": "progress", "payload": {"stage": "code_generation", "attempt": retries}}
            try:
                if sigkill_event and hasattr(sigkill_event, 'is_set') and sigkill_event.is_set():
                    break
                # Call code generator with typed context and legacy params populated from context
                _t_codegen = _time.monotonic()
                final_code = await code_generator_fn(
                    data_model={},
                    prompt=derived_prompt,
                    interpreted_prompt=derived_interpreted_prompt,
                    schemas=derived_schemas,
                    ds_clients=ds_clients,
                    excel_files=excel_files,
                    code_and_error_messages=code_and_error_messages,
                    memories="",
                    previous_messages="",
                    retries=retries,
                    sigkill_event=sigkill_event,
                    code_context_builder=None,
                    context=ctx,
                )
                codegen_ms = round((_time.monotonic() - _t_codegen) * 1000.0, 1)
                yield {"type": "progress", "payload": {"stage": "code_generated", "attempt": retries, "code": final_code, "timing": False}}
            except Exception as e:
                from app.ai.agents.coder.coder import CodegenRefused
                if isinstance(e, CodegenRefused):
                    # The coder declined, and the same files and the same rules
                    # would meet a second attempt — retrying only buys another
                    # invented answer. Same shape as LocalFolderUnavailable
                    # below: report the real reason and stop. The message names
                    # `read_file`, so the planner's next step is an action.
                    code_and_error_messages.append((final_code, e.reason))
                    yield {"type": "stdout", "payload": e.reason}
                    break
                msg = f"Code generation error: {str(e)}"
                code_and_error_messages.append((final_code, msg))
                yield {"type": "stdout", "payload": msg}
                retries += 1
                if retries < max_retries:
                    yield {"type": "progress", "payload": {"stage": "retry", "attempt": retries, "timing": False}}
                continue

            # Pre-resolve load_step()/load_entity() references before exec. A
            # resolution miss is folded into the error feedback so the coder
            # regenerates (same path as a bad column), rather than failing the
            # sandbox call.
            loadables = None
            if loadable_resolver_fn is not None and final_code:
                try:
                    step_refs, entity_refs = extract_loadable_refs(final_code)
                    if step_refs or entity_refs:
                        resolved = await loadable_resolver_fn(step_refs, entity_refs)
                        loadables = {
                            "steps": resolved.get("steps", {}),
                            "entities": resolved.get("entities", {}),
                        }
                        resolve_errors = resolved.get("errors") or []
                        if resolve_errors:
                            msg = "Loadable resolution failed: " + " | ".join(resolve_errors)
                            code_and_error_messages.append((final_code, msg))
                            yield {"type": "stdout", "payload": msg}
                            retries += 1
                            if retries < max_retries:
                                yield {"type": "progress", "payload": {"stage": "retry", "attempt": retries, "timing": False}}
                            continue
                except Exception as e:
                    yield {"type": "stdout", "payload": f"Loadable resolution error: {str(e)}"}

            yield {"type": "progress", "payload": {"stage": "data_query_execution", "attempt": retries}}
            try:
                if sigkill_event and hasattr(sigkill_event, 'is_set') and sigkill_event.is_set():
                    break
                _t_exec = _time.monotonic()
                # Fresh per-attempt capture — on success we keep these; on
                # exception the wrapper's partial writes still reach the outer
                # scope so the failure branch can surface the failing SQL / DB error.
                query_timings.clear()
                executed_queries.clear()
                exec_df, execution_log, _returned_queries = await self.execute_code_async(
                    code=final_code, ds_clients=ds_clients, excel_files=excel_files,
                    captured_timings=query_timings, captured_queries=executed_queries,
                    loadables=loadables,
                )
                # A laptop (local-runtime) run proxies its queries through the
                # helper, so the server's in-place capture list stays empty and
                # only the RETURNED list has them. Fold those in, or everything
                # downstream — the retry message and the done payload — would
                # conclude "no query ran" for every local execution.
                # The server path returns that same list object, hence the
                # identity check: extending it with itself would double it.
                if _returned_queries and _returned_queries is not executed_queries:
                    executed_queries.extend(str(q) for q in _returned_queries)
                execution_provenance = getattr(self, "last_execution_provenance", None)
                execution_ms = round((_time.monotonic() - _t_exec) * 1000.0, 1)
                yield {
                    "type": "progress",
                    "payload": {
                        "stage": "post_execution",
                        "attempt": retries,
                        "execution_ms": execution_ms,
                    },
                }
                # Treat None/empty-columns DataFrame as a soft failure so the
                # LLM gets a chance to fix defensive stub code that never
                # actually calls execute_query — but only when there's an SQL
                # client or file to query against. URL-fetch-only runs (no
                # ds_clients, no excel_files) legitimately may have nothing
                # to return; the printed output is the deliverable.
                _has_queryable_source = bool(ds_clients) or bool(excel_files)
                if _has_queryable_source and (exec_df is None or not hasattr(exec_df, 'columns') or len(exec_df.columns) == 0):
                    # ★ Two unrelated faults land here, and naming the wrong one
                    # sends the coder chasing a phantom for every remaining
                    # attempt. Report only what was actually observed:
                    #   queries ran     -> connection + SQL were fine; the
                    #                      function simply didn't return the result
                    #   no query ran    -> the function never queried anything
                    # `executed_queries` is cleared before each attempt, so it
                    # describes THIS attempt only.
                    _ran_queries = [q for q in (executed_queries or []) if q]
                    if _ran_queries:
                        msg = (
                            f"{len(_ran_queries)} quer{'y' if len(_ran_queries) == 1 else 'ies'} "
                            "executed successfully, so the connection, the client_key and the SQL "
                            "are all fine — but the function returned None (or an object with 0 "
                            "columns) instead of the query result. Return the DataFrame you built "
                            "from execute_query(...). Do NOT return an empty pd.DataFrame() as a "
                            "defensive fallback, and make sure the final `return` is the last "
                            "statement of generate_df itself — a `return` inside a nested helper "
                            "does not return from generate_df."
                        )
                    else:
                        msg = (
                            "No query was executed, and the function returned None or an empty "
                            "DataFrame (0 columns). You MUST call "
                            "ds_clients[\"<client_key>\"].execute_query(...) using the EXACT "
                            "client_key from <connection_clients> and return the resulting DataFrame. "
                            "Do NOT return an empty pd.DataFrame() as a defensive fallback and do NOT "
                            "wrap the query in 'if client is None' branches — the client_key is guaranteed to exist."
                        )
                        # Echo the ACTUAL table names the agent can query (file agents
                        # expose them on the client's _table_map after connect) so a
                        # first attempt that didn't know what to query can aim at a
                        # real table. Only in this branch: when a query already ran,
                        # the table name is demonstrably not the problem.
                        #
                        # ★ Never for code that targets a local folder. Those tables
                        # live on the user's device and are not in ds_clients at all,
                        # so everything we could list here belongs to some OTHER
                        # connector — naming them steers the next attempt away from
                        # the folder it was supposed to read.
                        try:
                            _targets_local_folder = False
                            try:
                                from app.services.local_runtime_exec import referenced_local_folders
                                _targets_local_folder = bool(referenced_local_folders(final_code))
                            except Exception:
                                _targets_local_folder = 'ds_clients["local:' in (final_code or "")
                            _avail_tables: List[str] = []
                            if not _targets_local_folder:
                                for _c in (ds_clients or {}).values():
                                    _tm = getattr(_c, "_table_map", None)
                                    if isinstance(_tm, dict):
                                        _avail_tables.extend([str(k) for k in _tm.keys()])
                            # De-dup, preserve order.
                            _seen: set = set()
                            _avail_tables = [t for t in _avail_tables if not (t in _seen or _seen.add(t))]
                            if _avail_tables:
                                msg += (
                                    " Available tables: " + ", ".join(_avail_tables[:20]) +
                                    " — use these EXACT table names in your SQL FROM clause "
                                    "(the query targets a TABLE, not the client_key)."
                                )
                        except Exception:
                            pass
                    code_and_error_messages.append((final_code, msg))
                    yield {"type": "stdout", "payload": msg}
                    retries += 1
                    if retries < max_retries:
                        yield {"type": "progress", "payload": {"stage": "retry", "attempt": retries, "timing": False}}
                    continue
                if exec_df is None:
                    exec_df = pd.DataFrame()
                executed_successfully = True
                break
            except CodeSecurityError as e:
                # Tag security violations distinctly so callers can audit them
                violation_type = "unsafe_python" if isinstance(e, UnsafePythonError) else "unsafe_sql"
                msg = f"Security violation ({violation_type}): {str(e)}"
                code_and_error_messages.append((final_code, msg))
                yield {"type": "security_violation", "payload": {"violation_type": violation_type, "message": str(e), "code_snippet": final_code[:500]}}
                yield {"type": "stdout", "payload": msg}
                if violation_type == "unsafe_python":
                    # AST validation runs BEFORE exec() — nothing has executed,
                    # so this is a correctable style problem (e.g. the coder
                    # used getattr()). Feed the violation back and regenerate.
                    retries += 1
                    if retries < max_retries:
                        yield {"type": "progress", "payload": {"stage": "retry", "attempt": retries, "timing": False}}
                    continue
                # unsafe_sql fires mid-execution (a write query reached a real
                # client wrapper), so the attempt is not safely repeatable.
                break
            except Exception as e:
                from app.services.local_runtime_exec import LocalFolderUnavailable
                if isinstance(e, LocalFolderUnavailable):
                    # Rewriting the code cannot conjure an offline laptop, and a
                    # retry would tempt the coder to "fix" it by silently
                    # switching to warehouse data. Report and stop.
                    code_and_error_messages.append((final_code, str(e)))
                    yield {"type": "stdout", "payload": str(e)}
                    break
                msg = augment_db_error_hint(f"Execution error: {str(e)}")
                code_and_error_messages.append((final_code, msg))
                yield {"type": "stdout", "payload": msg}
                retries += 1
                if retries < max_retries:
                    yield {"type": "progress", "payload": {"stage": "retry", "attempt": retries, "timing": False}}
                continue

        if sigkill_event and hasattr(sigkill_event, 'is_set') and sigkill_event.is_set():
            yield {
                "type": "done",
                "payload": {
                    "df": pd.DataFrame(),
                    "code": final_code,
                    "errors": code_and_error_messages,
                    # `errors` is the history of EVERY attempt, including ones a
                    # later attempt recovered from, and it is emitted on the success
                    # path too — so it cannot be used as a success test. This is the
                    # outcome of the run.
                    "executed_successfully": False,
                    "execution_log": execution_log,
                    "executed_queries": [],
                    "query_timings": [],
                    "codegen_ms": None,
                    "execution_ms": None,
                },
            }
            return
        else:
            if not executed_successfully and code_and_error_messages:
                yield {
                    "type": "done",
                    "payload": {
                        "df": None,
                        "code": final_code,
                        "errors": code_and_error_messages,
                        "executed_successfully": False,
                        "execution_log": execution_log,
                        "executed_queries": executed_queries,
                        "query_timings": query_timings,
                        "codegen_ms": None,
                        "execution_ms": None,
                    },
                }
            else:
                yield {
                    "type": "done",
                    "payload": {
                        "df": exec_df,
                        "code": final_code,
                        "errors": code_and_error_messages,
                        "executed_successfully": True,
                        "execution_log": execution_log,
                        "executed_queries": executed_queries,
                        "query_timings": query_timings,
                        "codegen_ms": codegen_ms,
                        "execution_ms": execution_ms,
                        "execution_provenance": execution_provenance,
                    },
                }

    async def execute_and_update_step(self,
                              data_model: Dict,
                              code_generator_fn: Callable,
                              db_clients: Dict = None,
                              excel_files: List = None,
                              step=None,  # Optional override for current step
                              **generator_kwargs) -> bool:
        """
        Execute code generation/execution process and update the step with results

        Args:
            data_model: The data model to generate code for
            code_generator_fn: Function that generates code
            db_clients: Database clients
            excel_files: Excel files
            step: Override for the step object (uses self.step if None)
            **generator_kwargs: Additional arguments to pass to code_generator_fn

        Returns:
            Boolean indicating if execution was successful
        """
        # Use provided step or fall back to instance step
        current_step = step or self.step
        if not current_step:
            if self.logger:
                self.logger.error("No step provided for execute_and_update_step")
            return False

        df, final_code, code_and_error_messages = await self.generate_and_execute_with_retries(
            data_model=data_model,
            code_generator_fn=code_generator_fn,
            db_clients=db_clients,
            excel_files=excel_files,
            step=current_step,
            max_retries=code_retries_setting(self.organization_settings),
            **generator_kwargs
        )
        
        # Check if the DataFrame has columns, which indicates success even if empty
        if len(df.columns) > 0:
            # Format the data for widget display
            widget_data = self.format_df_for_widget(df)
            
            # Update step with data
            try:
                await self.project_manager.update_step_with_data(self.db, current_step, widget_data)
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error updating step with data: {str(e)}")
                return False
            return True
        else:
            # Handle error case if all retries failed and we have no columns
            try:
                if self.report and self.head_completion and self.widget:
                    await self.project_manager.create_message(
                        report=self.report,
                        db=self.db,
                        message="I faced some issues while generating data. The result had no columns. Can you try explaining again?",
                        status="success",
                        completion=self.head_completion,
                        widget=self.widget,
                        role="ai_agent"
                    )
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error creating error message: {str(e)}")
            return False
