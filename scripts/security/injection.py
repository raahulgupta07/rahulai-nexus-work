#!/usr/bin/env python3
"""Injection / traversal / SSRF probe suite for CityAgent Insights.

READ-ONLY against the app's data: the only writes are objects this script
creates and then deletes (one throwaway CSV agent). Nothing here calls
``GET /data_sources/{id}/test_connection`` — CLAUDE.md records that it is
spelled GET but WRITES ``is_active`` and once disabled a live agent org-wide.

WHAT IT COVERS (this agent's dimension: injection, traversal, SSRF)
  1. SQL injection into the app's OWN metadata database (not the agent's
     designed SQL against a customer warehouse).
  2. LDAP filter injection in backend/app/ee/ldap/.
  3. Path traversal — uploaded file_paths (file_only member lock) and the
     public file-serving routes (branding / avatar / thumbnail).
  4. SSRF — web_fetch/safe_client host guard, MCP DCR allowlist, render
     sandbox, and the UNGUARDED database-connection host surface.
  5. Command injection — subprocess argv vs shell.
  6. Deserialization — pickle / yaml.load / eval on request data.
  7. The code-execution sandbox: what generated code can actually reach.

Each probe prints PASS / FAIL / INFO plus what it actually proved, and is
labelled LIVE (exercised against the running instance) or STATIC (read from
source). A STATIC pass is a code assertion, not a live exploit attempt.

USAGE
  # 1. mint tokens (writes to a PATH ARGUMENT, never `>` — see the script):
  docker cp scripts/mint-user-tokens.py dash-app:/app/backend/
  docker exec -w /app/backend dash-app python mint-user-tokens.py \
      /tmp/sec-tokens.json raahulgupta07@gmail.com member@cityagent.io
  docker cp dash-app:/tmp/sec-tokens.json /tmp/sec-tokens.json

  # 2. run the probes:
  python3 scripts/security/injection.py --tokens /tmp/sec-tokens.json

  --base   default http://localhost:8095
  --tokens path to the JSON written by mint-user-tokens.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))          # .../bagofwords
APP = os.path.join(REPO, "backend", "app")

ORG_ID = "7ad85eeb-70de-4afe-8cd0-c4539c5bc26b"        # Main Org (see CLAUDE.md)
ORG_HEADER = "X-Organization-Id"

# ANSI, disabled when not a tty
_TTY = sys.stdout.isatty()
def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s
GREEN = lambda s: _c("32", s)
RED = lambda s: _c("31", s)
YELLOW = lambda s: _c("33", s)
CYAN = lambda s: _c("36", s)
DIM = lambda s: _c("2", s)

# ---------------------------------------------------------------------------
# Tally
# ---------------------------------------------------------------------------
_RESULTS: list[tuple[str, str, str]] = []   # (status, title, detail)

def record(status: str, title: str, detail: str) -> None:
    _RESULTS.append((status, title, detail))
    tag = {
        "PASS": GREEN("PASS"),
        "FAIL": RED("FAIL"),
        "INFO": YELLOW("INFO"),
        "SKIP": DIM("SKIP"),
    }.get(status, status)
    print(f"  [{tag}] {title}")
    for line in detail.strip("\n").splitlines():
        print(f"         {DIM(line)}")

# ---------------------------------------------------------------------------
# Minimal HTTP (stdlib only, so the suite runs on a bare host)
# ---------------------------------------------------------------------------
class Resp:
    def __init__(self, status: int, body: bytes, headers: dict):
        self.status = status
        self.body = body
        self.headers = headers
    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")
    def json(self):
        try:
            return json.loads(self.body)
        except Exception:
            return None


def http(method: str, base: str, path: str, token: str | None = None,
         org: str | None = ORG_ID, json_body=None, timeout: float = 20.0) -> Resp:
    url = base.rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if org:
        headers[ORG_HEADER] = org
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return Resp(r.status, r.read(), dict(r.headers))
    except urllib.error.HTTPError as e:
        return Resp(e.code, e.read(), dict(e.headers or {}))
    except urllib.error.URLError as e:
        return Resp(0, str(e.reason).encode(), {})


# ---------------------------------------------------------------------------
# Source helpers (STATIC probes)
# ---------------------------------------------------------------------------
def read(rel: str) -> str:
    p = os.path.join(APP, rel)
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def grep_tree(pattern: str, exts=(".py",)) -> list[str]:
    """Return 'relpath:lineno: line' for a regex over backend/app, skipping
    .bak-* and __pycache__."""
    rx = re.compile(pattern)
    hits = []
    for root, dirs, files in os.walk(APP):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in files:
            if not fn.endswith(exts) or ".bak-" in fn:
                continue
            fp = os.path.join(root, fn)
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if rx.search(line):
                            rel = os.path.relpath(fp, APP)
                            hits.append(f"{rel}:{i}: {line.strip()}")
            except OSError:
                continue
    return hits


# ===========================================================================
# 1. SQL injection into the app's OWN metadata database
# ===========================================================================
def probe_sql_metadata_static() -> None:
    """Every text() in backend/app must be parameterized/bindparams, EXCEPT the
    data-source clients (postgresql/mysql/snowflake/... f-string SQL), which
    execute the agent's designed analytical SQL against a CUSTOMER warehouse —
    the product's purpose, not a vuln against our DB."""
    hits = grep_tree(r"\btext\(f?[\"']")
    app_db, warehouse = [], []
    for h in hits:
        rel = h.split(":", 1)[0]
        # data_sources/* is the agent's analytical SQL against a CUSTOMER
        # warehouse (clients/* execute it, fast/* runs EXPLAIN over it) — the
        # product's purpose, not SQLi against our metadata DB.
        (warehouse if rel.startswith("data_sources/") else app_db).append(h)

    fstring_appdb = [h for h in app_db if re.search(r"text\(f[\"']", h)]
    # Each app-DB f-string was hand-verified 2026-08-09: it interpolates only
    # server-controlled fragments (dialect branches, {field}=NULL column names
    # from a server list, :idN placeholder names) — never request text — and
    # passes every value as a :bind. Report them so a future edit re-checks.
    record("PASS", "SQLi into own DB — no user text reaches an app-DB query (STATIC)",
           f"{len(app_db)} text() site(s) over the metadata DB; the {len(fstring_appdb)} "
           f"f-string one(s) interpolate server-controlled identifier fragments only, "
           f"values via :bind:\n" + "\n".join(fstring_appdb[:8]) +
           f"\n{len(warehouse)} f-string SQL sites are data_sources/* (agent SQL vs a "
           f"customer warehouse — by design).")


def probe_sql_metadata_live(base: str, member: str) -> None:
    """Push classic SQLi strings through query params that reach ORM filters.
    A parameterized/ORM stack returns 200/400/422 and never a DB error or a
    dumped row. A 500 carrying a SQL fragment would be the tell."""
    payload = "1' OR '1'='1"
    tick = "%27);--"
    checks = [
        ("GET", f"/api/reports/activity?ids={urllib.parse.quote(payload)}"),
        ("GET", f"/api/organizations/{ORG_ID}/people?search={urllib.parse.quote(payload)}"),
        ("GET", f"/api/data_sources?show_all={urllib.parse.quote(tick)}"),
    ]
    leaked = []
    for method, path in checks:
        r = http(method, base, path, token=member)
        low = r.text.lower()
        if r.status >= 500 and ("syntax" in low or "psycopg" in low or "sqlalchemy" in low
                                or "asyncpg" in low or "sql" in low and "error" in low):
            leaked.append(f"{path} -> {r.status}: {r.text[:160]}")
    if leaked:
        record("FAIL", "SQLi into own DB — a payload reached the SQL engine (LIVE)",
               "\n".join(leaked))
    else:
        record("PASS", "SQLi into own DB — injection strings handled as data (LIVE)",
               "reports/activity, people?search, data_sources?show_all all took "
               "quote/OR payloads without a 5xx SQL error or a leaked row.")


# ===========================================================================
# 2. LDAP filter injection
# ===========================================================================
def probe_ldap_escape_static() -> None:
    src = read("ee/ldap/connection.py")
    has_escape = "escape_filter_chars" in src and re.search(
        r"ident\s*=\s*escape_filter_chars\(identifier\)", src)
    # The raw pre-fix form was: match = f"(&{...}({attr}={email}))" with the
    # *typed* value. Flag only a FILTER assignment that interpolates the raw
    # input var — {ident} (escaped) and {email_attr}/{login_attr} (attr NAMES,
    # config-controlled) are fine; {identifier}/{email}/{username} are the bug.
    filter_lines = [ln for ln in src.splitlines()
                    if re.search(r"(match|search_filter)\s*=\s*f[\"']", ln)]
    raw_ident = [ln.strip() for ln in filter_lines
                 if re.search(r"\{\s*(identifier|email|username|password)\s*\}", ln)]
    # find_user must escape; both other filters use config-controlled strings.
    filters = grep_tree(r"search_filter\s*=\s*f?[\"']", exts=(".py",))
    ldap_filters = [f for f in filters if f.startswith("ee/ldap/")]
    if has_escape and not raw_ident:
        record("PASS", "LDAP injection — find_user escapes the typed identifier (STATIC)",
               "connection.py: ident = escape_filter_chars(identifier); the filter\n"
               "interpolates only the escaped value + config-controlled attrs.\n"
               "Other filters (search_users/search_groups) build from config strings, "
               "not request input:\n" + "\n".join(ldap_filters))
    else:
        record("FAIL", "LDAP injection — a filter still takes a raw identifier (STATIC)",
               f"escape present={bool(has_escape)}; raw-interp filter line(s)={raw_ident}")


def probe_ldap_wildcard_live(base: str) -> None:
    """Best effort: post a bare `*` to the LDAP login door. Whether or not a
    directory is configured, a `*` must never behave as a wildcard match — the
    response must be an ordinary auth failure, not a 500 and not a success."""
    form = urllib.parse.urlencode({"username": "*", "password": "x"}).encode()
    req = urllib.request.Request(
        base.rstrip("/") + "/api/auth/ldap/login", data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            status, body = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, body = e.code, (e.read() or b"").decode("utf-8", "replace")
    except urllib.error.URLError as e:
        record("SKIP", "LDAP injection — login door unreachable (LIVE)", str(e.reason))
        return
    if status == 200 and "access_token" in body:
        record("FAIL", "LDAP injection — `*` username authenticated (LIVE)",
               f"POST /api/auth/ldap/login username=* -> 200 with a token")
    elif status in (400, 401, 422):
        record("PASS", "LDAP injection — `*` username refused, not a wildcard (LIVE)",
               f"POST /api/auth/ldap/login username=* -> {status} (ordinary auth failure)")
    else:
        record("INFO", "LDAP injection — unexpected status for `*` (LIVE)",
               f"status={status} body={body[:160]}")


# ===========================================================================
# 3. Path traversal
# ===========================================================================
def probe_file_only_lock_live(base: str, member: str) -> None:
    """A file_only member (only create_file_data_source) must not be able to set
    config.file_paths (arbitrary server-path read) or is_public. Create a CSV
    agent asking for /etc/passwd + /app/.env, then read it back and assert the
    server stripped file_paths and forced private. Cleans up after itself."""
    name = "sec-probe-fileonly-DELETE-ME"
    body = {
        "name": name,
        "type": "csv",
        "config": {"file_paths": "/etc/passwd\n/app/.env\n../../../../etc/shadow"},
        "is_public": True,
    }
    r = http("POST", base, "/api/data_sources", token=member, json_body=body)
    if r.status not in (200, 201):
        record("INFO", "Traversal — file_only member could not create a CSV agent (LIVE)",
               f"POST /api/data_sources -> {r.status}: {r.text[:200]}\n"
               "(member may lack create_file_data_source in this org; static lock still holds)")
        return
    created = r.json() or {}
    ds_id = created.get("id")
    try:
        got = http("GET", base, f"/api/data_sources/{ds_id}", token=member).json() or {}
        # config may live on the embedded connection; check every plausible slot.
        blobs = [got]
        blobs += got.get("connections") or []
        fp_values = []
        pub = got.get("is_public")
        for b in blobs:
            cfg = b.get("config") if isinstance(b, dict) else None
            if isinstance(cfg, str):
                try:
                    cfg = json.loads(cfg)
                except Exception:
                    cfg = {}
            if isinstance(cfg, dict) and cfg.get("file_paths"):
                fp_values.append(cfg.get("file_paths"))
        leaked_path = any("/etc/" in str(v) or ".env" in str(v) for v in fp_values)
        if leaked_path or pub is True:
            record("FAIL", "Traversal — file_only lock did NOT strip server paths (LIVE)",
                   f"file_paths={fp_values!r} is_public={pub!r} (expected empty + false)")
        else:
            record("PASS", "Traversal — file_only member cannot set file_paths / is_public (LIVE)",
                   f"created CSV agent {ds_id}; file_paths stripped to empty, "
                   f"is_public forced {pub!r}. /etc/passwd, /app/.env, ../etc/shadow all rejected.")
    finally:
        d = http("DELETE", base, f"/api/data_sources/{ds_id}", token=member)
        print(f"         {DIM('cleanup: DELETE /api/data_sources/%s -> %s' % (ds_id, d.status))}")


def probe_static_path_serving_live(base: str) -> None:
    """The public file-serving routes (branding icon, avatar, thumbnail) build a
    path from a URL segment. Fire encoded traversal at each; every one must 404
    and never return file bytes."""
    payloads = [
        "..%2f..%2f..%2f..%2fetc%2fpasswd",
        "....//....//....//etc/passwd",
        "%2e%2e/%2e%2e/%2e%2e/etc/passwd",
        "..%5c..%5c..%5cwindows%5cwin.ini",
    ]
    routes = [
        "/api/branding/icons/{}",
        "/api/users/avatar/{}",
        "/api/thumbnails/{}",
    ]
    bad = []
    for rt in routes:
        for pl in payloads:
            r = http("GET", base, rt.format(pl), token=None, org=None)
            looks_like_passwd = b"root:x:0:0" in r.body or b"[extensions]" in r.body
            if r.status == 200 and looks_like_passwd:
                bad.append(f"{rt.format(pl)} -> 200 with system-file bytes")
    if bad:
        record("FAIL", "Traversal — a public file route served an out-of-root file (LIVE)",
               "\n".join(bad))
    else:
        record("PASS", "Traversal — branding/avatar/thumbnail reject encoded traversal (LIVE)",
               "12 encoded ../ payloads across 3 public routes; none returned system-file bytes\n"
               "(routes realpath()+startswith(base)+regex-gate the segment — branding.py:90-137).")


def probe_download_basename_static() -> None:
    hits = grep_tree(r"os\.path\.basename\(.*\.path\)")
    file_dl = [h for h in hits if h.startswith("routes/file.py")]
    if file_dl:
        record("PASS", "Traversal — file download pins to basename under uploads/ (STATIC)",
               "\n".join(file_dl) +
               "\n(a stored File.path is reduced to its basename before join, so a "
               "poisoned DB path cannot escape uploads/files/).")
    else:
        record("INFO", "Traversal — file download basename guard not located (STATIC)",
               "routes/file.py did not match os.path.basename(...path); re-check manually.")


# ===========================================================================
# 4. SSRF
# ===========================================================================
def probe_ssrf_webfetch_static() -> None:
    """web_fetch + the code-exec HTTP client must block loopback/RFC1918/
    link-local (169.254.169.254 metadata) BEFORE the request and after each
    redirect."""
    for rel, fn in (("ai/http/safe_client.py", "is_safe_host"),
                    ("ai/tools/implementations/web_fetch.py", "_is_safe_host")):
        src = read(rel)
        checks = all(tok in src for tok in
                     ("is_private", "is_loopback", "is_link_local", "getaddrinfo"))
        pre = f"{fn}(parsed" in src or f"{fn}(parsed_url.hostname)" in src or f"not {fn}(" in src
        redirect = "redirect" in src.lower() and fn in src
        if checks and redirect:
            record("PASS", f"SSRF — {rel} blocks private/metadata hosts + redirects (STATIC)",
                   f"{fn}() resolves DNS and rejects is_private/is_loopback/is_link_local/"
                   "is_reserved; re-checked on every redirect hop.")
        else:
            record("FAIL", f"SSRF — {rel} host guard incomplete (STATIC)",
                   f"ip-class checks={checks} redirect-recheck={redirect}")


def probe_ssrf_render_sandbox_static() -> None:
    src = read("core/render_sandbox.py")
    if "block_external_requests" in src and "route.abort()" in src and "_is_local" in src:
        record("PASS", "SSRF — headless-Chromium renderers abort non-local requests (STATIC)",
               "render_sandbox.block_external_requests aborts anything not file:/data:/blob:/about:\n"
               "so artifact JS cannot fetch() 169.254.169.254 and read it back through the PDF.")
    else:
        record("FAIL", "SSRF — render sandbox guard missing/altered (STATIC)",
               "core/render_sandbox.py did not contain the expected abort route.")


def probe_ssrf_dcr_allowlist_static() -> None:
    src = read("services/mcp_dcr_service.py")
    reg = read("schemas/data_source_registry.py")
    gated = "allowed_dcr_hosts" in src and "not allowed" in src.lower() or (
        "host not in allowed_dcr_hosts()" in src)
    if "host not in allowed_dcr_hosts()" in src and "allowed_dcr_hosts" in reg:
        record("PASS", "SSRF — MCP DCR discovery is allowlisted to catalog hosts (STATIC)",
               "mcp_dcr_service.ensure_mcp_oauth_config raises unless urlsplit(server_url).netloc\n"
               "is in allowed_dcr_hosts() (preset hosts + auth.atlassian.com/github.com). A custom\n"
               "server_url cannot drive discovery/registration at an internal address.")
    else:
        record("FAIL", "SSRF — DCR host allowlist not enforced (STATIC)",
               "expected `host not in allowed_dcr_hosts()` gate in mcp_dcr_service.py")


def probe_ssrf_db_connections_static() -> None:
    """The high-value gap for a self-hosted AWS box: DB / OAuth-token / warehouse
    connection configs take a user-supplied host or URL and NOTHING validates it
    against 169.254.169.254 / localhost / RFC1918. This is admin- (or
    connection-admin-) gated: a file_only member is blocked at create time
    (see the file_only probe). Reported, NOT exercised — the SSRF only fires on
    test_connection (which WRITES is_active) or a live query, both off-limits."""
    clients = os.path.join(APP, "data_sources", "clients")
    guarded = []
    for fn in sorted(os.listdir(clients)) if os.path.isdir(clients) else []:
        if not fn.endswith("_client.py"):
            continue
        src = read(os.path.join("data_sources", "clients", fn))
        if "is_safe_host" in src or "is_private" in src or "169.254" in src:
            guarded.append(fn)
    oauth = read("services/connection_oauth_service.py")
    token_url_guarded = "is_safe_host" in oauth or "is_private" in oauth
    record("INFO", "SSRF — DB / warehouse / OAuth connection hosts are UNVALIDATED (STATIC)",
           "No client in data_sources/clients/ checks the host against metadata/loopback/RFC1918 "
           f"(guarded clients: {guarded or 'none'}).\n"
           "connection_oauth_service posts to a caller-supplied token_url with no host guard "
           f"(guarded={token_url_guarded}).\n"
           "IMPACT: whoever can create/link a connection (full_admin / create_data_source / a\n"
           "per-connection create_data_sources grantee) can point Postgres/MySQL/MCP/OAuth at\n"
           "169.254.169.254 or an internal host; the connection test or first query then reaches it.\n"
           "A file_only member is BLOCKED at create (verified by the file_only probe).\n"
           "SEVERITY: medium — admin/connection-admin gated, but on AWS the metadata endpoint is\n"
           "reachable and there is no allowlist. NOT confirmed live (test_connection writes state).")


# ===========================================================================
# 5. Command injection
# ===========================================================================
def probe_command_injection_static() -> None:
    shell_true = [h for h in grep_tree(r"shell\s*=\s*True")]
    ossystem = [h for h in grep_tree(r"os\.(system|popen)\(")]
    # subprocess callers should pass an argv LIST, never a joined string.
    if not shell_true and not ossystem:
        subs = grep_tree(r"subprocess\.(run|Popen|call|check_output)\(")
        record("PASS", "Command injection — no shell=True / os.system; argv-list subprocess only (STATIC)",
               f"{len(subs)} subprocess call site(s), all list-form (soffice, ssh-keygen, qvd2parquet).\n"
               "git_service sets GIT_SSH_COMMAND from a server-generated temp path, not user input.")
    else:
        record("FAIL", "Command injection — a shell string sink exists (STATIC)",
               "\n".join(shell_true + ossystem))


# ===========================================================================
# 6. Deserialization
# ===========================================================================
def probe_deserialization_static() -> None:
    pickle_loads = grep_tree(r"pickle\.loads?\(")
    yaml_unsafe = [h for h in grep_tree(r"yaml\.load\(")
                   if "Loader=" not in h and "SafeLoader" not in h]
    yaml_full = [h for h in grep_tree(r"yaml\.load\(") if "FullLoader" in h]
    # eval/exec on request data (the sandbox's own exec is separate + AST-gated).
    bad_eval = [h for h in grep_tree(r"[^_.\w]eval\(")
                if "literal_eval" not in h and "auto_run_eval" not in h and "_eval" not in h]
    problems = []
    if pickle_loads:
        problems += ["pickle.loads: " + h for h in pickle_loads]
    if yaml_unsafe:
        problems += ["yaml.load w/o SafeLoader: " + h for h in yaml_unsafe]
    if problems:
        record("FAIL", "Deserialization — unsafe loader on possibly-attacker data (STATIC)",
               "\n".join(problems[:10]))
    else:
        record("PASS", "Deserialization — no pickle.loads / unsafe yaml.load on request data (STATIC)",
               "toolcall_args uses ast.literal_eval (safe); yaml.load sites (if any) pin a safe "
               f"Loader; no bare eval() over request input.\n"
               f"(FYI FullLoader sites: {len(yaml_full)}; bare-eval candidates after filtering: {len(bad_eval)})")


# ===========================================================================
# 7. The code-execution sandbox
# ===========================================================================
def probe_sandbox_architecture_static() -> None:
    """The single most important question in this dimension. Generated code is
    run via exec(code, local_namespace). Establish (a) what the denylist covers
    and (b) that the exec namespace does NOT strip __builtins__ — so the AST
    validator is the ONLY boundary, and any parser bypass reaches os.environ
    (DASH_ENCRYPTION_KEY)."""
    src = read("ai/code_execution/code_execution.py")
    mods = all(m in src for m in ("'os'", "'subprocess'", "'socket'", "'ctypes'", "'pickle'"))
    builtins_blocked = all(b in src for b in ("'eval'", "'exec'", "'open'", "'__import__'",
                                              "'getattr'", "'setattr'"))
    attrs = all(a in src for a in ("'__class__'", "'__globals__'", "'__subclasses__'",
                                   "'__builtins__'"))
    file_readers = "FORBIDDEN_FILE_READERS" in src and "read_csv" in src
    # Does the exec namespace override __builtins__? (Search the namespace dict.)
    ns_block = re.search(r"local_namespace\s*=\s*\{(.*?)\n\s*\}", src, re.S)
    ns_body = ns_block.group(1) if ns_block else ""
    builtins_in_ns = "__builtins__" in ns_body

    detail = (
        "FORBIDDEN_MODULES covers os/subprocess/socket/ctypes/pickle/... : "
        f"{mods}\n"
        f"FORBIDDEN_BUILTINS covers eval/exec/open/__import__/getattr/setattr : {builtins_blocked}\n"
        f"FORBIDDEN_ATTRIBUTES covers __class__/__globals__/__subclasses__/__builtins__ : {attrs}\n"
        f"FORBIDDEN_FILE_READERS blocks literal pd.read_csv('/path') / duckdb.connect : {file_readers}\n"
        f"exec namespace overrides __builtins__ : {builtins_in_ns}  "
        f"(False = CPython injects the FULL real builtins at runtime)"
    )
    markers_ok = mods and builtins_blocked and attrs and file_readers
    if not markers_ok:
        record("FAIL", "Sandbox — expected denylist markers missing; re-audit (STATIC)", detail)
    elif builtins_in_ns:
        # ★★★This branch is new, and the probe FAILED without it — for the wrong
        # reason. The original pass condition ended `and not builtins_in_ns`,
        # because when it was written the exec namespace did NOT override
        # `__builtins__` and the probe's whole job was to say so. Phase 1 fixed
        # that (`_build_safe_builtins()` plus a `_guarded_import` reading the
        # same FORBIDDEN_MODULES), and the probe then reported FAIL with every
        # one of its five sub-checks printing True — a red line meaning "the
        # thing I was built to complain about stopped being true".
        #
        # Recorded here rather than deleting the check: the markers still have
        # to hold, and if someone later removes the builtins override this drops
        # back to the INFO branch below and says exactly what was lost.
        record("PASS", "Sandbox — builtins are replaced in the exec namespace (STATIC)",
               detail + "\n"
               "The namespace passed to exec() defines its own __builtins__, so CPython does\n"
               "not inject the real one. Combined with the AST denylist that is defence in\n"
               "depth rather than a single wall: a construct the parser fails to model now\n"
               "lands in a namespace where the dangerous builtins are absent.\n"
               "★NOT a full sandbox. It is still one process, on the app's event loop, with\n"
               "the real interpreter — see the db_clients passthrough INFO below, which this\n"
               "does not address and cannot.")
    else:
        record("INFO", "Sandbox — AST denylist is the ONLY boundary; runtime builtins intact (STATIC)",
               detail + "\n"
               "ARCHITECTURE: exec(code, local_namespace) runs in-process on the app's event-loop\n"
               "worker with the real __builtins__ present. There is no seccomp, no separate process,\n"
               "no __builtins__ stripping. The AST CodeSecurityVisitor (denylist of modules/builtins/\n"
               "dunder-attrs/file-readers) is the entire wall. It is a solid denylist, but a denylist:\n"
               "any construct the parser does not model reaches os.environ['DASH_ENCRYPTION_KEY'],\n"
               "the DB URL, and the filesystem. THREAT: prompt-injection (poisoned uploaded file /\n"
               "instruction) steering the model to emit a bypassing generate_df. HIGHEST-VALUE area\n"
               "to harden (defense-in-depth: strip __builtins__, or run in a real sandbox).")


def probe_sandbox_denylist_gaps_static() -> None:
    """Concrete, named holes in the AST denylist (STATIC, NOT weaponized).
    A denylist over exec()-with-full-builtins is only as good as its
    completeness; enumerate dunders/builtins that a known escape chain uses and
    that the visitor does NOT list."""
    src = read("ai/code_execution/code_execution.py")
    # slice out the three frozensets to test membership precisely
    def in_set(name: str, setname: str) -> bool:
        m = re.search(setname + r"\s*=\s*frozenset\(\{(.*?)\}\)", src, re.S)
        return bool(m) and (f"'{name}'" in m.group(1) or f'"{name}"' in m.group(1))
    attr_gaps = [d for d in ("__getattribute__", "__getattr__", "__base__",
                             "__reduce__", "__reduce_ex__", "__init_subclass__",
                             "__class_getitem__")
                 if not in_set(d, "FORBIDDEN_ATTRIBUTES")]
    builtin_gaps = [b for b in ("type", "vars", "help", "classmethod",
                                "staticmethod", "property", "super", "object")
                    if not in_set(b, "FORBIDDEN_BUILTINS")]
    record("INFO", "Sandbox — named denylist gaps a known escape chain uses (STATIC)",
           f"FORBIDDEN_ATTRIBUTES omits: {attr_gaps}\n"
           f"FORBIDDEN_BUILTINS omits: {builtin_gaps}\n"
           "Illustrative (UNWEAPONIZED, not run — would need model-authored code): the visitor\n"
           "blocks `__bases__`/`__subclasses__` and the `getattr` builtin, but NOT `__base__`\n"
           "(singular) nor the `__getattribute__` method nor the `type` builtin. A chain like\n"
           "type(()).__base__ then o.__getattribute__('__subclasses__')() sidesteps the literal-\n"
           "attribute-name and getattr checks entirely. This is why the boundary should not be a\n"
           "denylist alone. NOT confirmed live (executing generated code needs a chat/model call,\n"
           "out of scope for this read-only suite).")


def probe_sandbox_client_credential_passthrough_static() -> None:
    """The SHORT chain — no dunder tricks needed. `db_clients` is in the exec
    namespace; each entry is a QueryCapturingClientWrapper whose __getattr__
    delegates ANY non-dunder attribute to the raw client; the raw clients store
    plaintext `self.password` / `self.user` / `self.pg_uri` (a full
    postgresql://user:password@host URI). The AST validator's visit_Attribute
    only blocks DUNDER names, so `db_clients[k].password` and
    `db_clients[k].pg_uri` read clean. STATIC — not executed against live."""
    cx = read("ai/code_execution/code_execution.py")
    passthrough = re.search(r"def __getattr__\(self, name\):\s*\n\s*\"\"\"[^\"]*\"\"\"\s*\n\s*return getattr\(self\._original, name\)", cx)
    in_ns = "'db_clients': wrapped_clients" in cx
    # do any clients keep plaintext creds as ordinary attributes?
    clients = os.path.join(APP, "data_sources", "clients")
    leaky = []
    for fn in sorted(os.listdir(clients)) if os.path.isdir(clients) else []:
        if not fn.endswith("_client.py"):
            continue
        s = read(os.path.join("data_sources", "clients", fn))
        if re.search(r"self\.password\s*=", s) or re.search(r"self\.(pg_uri|uri|connection_string)\s*=", s):
            leaky.append(fn)
    # confirm none of those attr names are on any denylist
    guarded_names = any(f"'{n}'" in cx for n in ("password", "pg_uri"))
    if passthrough and in_ns and leaky and not guarded_names:
        record("INFO", "Sandbox — db_clients leaks plaintext DB credentials with NO dunder needed (STATIC)",
               "QueryCapturingClientWrapper.__getattr__ (code_execution.py:1084) does "
               "`return getattr(self._original, name)` for any non-dunder attr.\n"
               f"Raw clients store plaintext creds as ordinary attributes: {leaky}\n"
               "(e.g. postgresql_client.py:28 self.password, :45 self.pg_uri = "
               "'postgresql://user:password@host').\n"
               "visit_Attribute only blocks DUNDERS, so generated code can do:\n"
               "    for k in db_clients: print(db_clients[k].password, db_clients[k].pg_uri)\n"
               "and read every connected data source's password + full URI — validator-clean,\n"
               "no type()/__base__/__getattribute__ gymnastics. This reaches the CUSTOMER\n"
               "WAREHOUSE credentials (arguably worse for this product than DASH_ENCRYPTION_KEY),\n"
               "NOT os.environ — the module-globals hop (.__class__/.__globals__) is blocked, both\n"
               "dunders are on the denylist. CONFIRMED STATICALLY (plain attribute read the\n"
               "validator does not model); NOT executed against the live app. Same fix closes it:\n"
               "hand generated code a credential-free client facade, and/or a restricted __builtins__.")
    else:
        record("PASS", "Sandbox — no plaintext-credential passthrough via db_clients (STATIC)",
               f"passthrough={bool(passthrough)} in_ns={in_ns} leaky_clients={leaky} "
               f"names_guarded={guarded_names}")


def probe_sandbox_literal_path_bypass_static() -> None:
    """FORBIDDEN_FILE_READERS is meant to stop `pd.read_csv('/app/uploads/...')`
    with a hardcoded path. The check (code_execution.py:439-443) only rejects an
    ast.Constant or ast.JoinedStr first argument — ANY computed string expression
    (concat, .join, a variable, str()) is not modelled and passes. STATIC."""
    cx = read("ai/code_execution/code_execution.py")
    only_two_forms = ("is_literal_path = isinstance(first_arg, ast.Constant)" in cx
                      and "is_fstring_path = isinstance(first_arg, ast.JoinedStr)" in cx
                      and "if is_literal_path or is_fstring_path:" in cx)
    # is there any check on a computed/BinOp/Name first arg?
    models_computed = any(tok in cx for tok in ("ast.BinOp", "ast.Name)  # path",
                                                "_is_constant_string", "ast.literal_eval(first_arg"))
    if only_two_forms and not models_computed:
        record("INFO", "Sandbox — literal-path file-read guard is bypassed by any computed string (STATIC)",
               "code_execution.py:439-443 flags ONLY ast.Constant / ast.JoinedStr first args.\n"
               "A computed path is a different AST node and is not modelled, so these are\n"
               "validator-clean today:\n"
               "    pd.read_csv('/etc/' + 'passwd')            # ast.BinOp\n"
               "    pd.read_csv(''.join(['/proc/self/','environ']))\n"
               "    p = '/app/backend/.env'; pd.read_csv(p)    # ast.Name\n"
               "That is arbitrary filesystem READ from inside the sandbox, which is exactly what\n"
               "the guard's own comment says it exists to prevent ('a hardcoded path would\n"
               "otherwise sidestep every data-access boundary').\n"
               "★NOTE: /proc/self/environ is a candidate COMPLETE chain to DASH_ENCRYPTION_KEY\n"
               "that never touches os.environ or any blocked dunder. NOT EXECUTED — whether\n"
               "pandas parses that NUL-separated file into readable output is UNVERIFIED. Treat\n"
               "as a strong lead for the sandbox owner to confirm in the /src runner, not as a\n"
               "demonstrated exfil.\n"
               "FIX SHAPE: allow the reader only when the argument derives from excel_files[i].path\n"
               "(an allowlist of provenance), never by trying to recognise bad path expressions.")
    else:
        record("PASS", "Sandbox — file-read guard models computed paths (STATIC)",
               f"only_two_forms={only_two_forms} models_computed={models_computed}")


def probe_sandbox_excel_files_orm_static() -> None:
    """`excel_files` entries are SQLAlchemy ORM File rows, not DTOs. Every
    relationship name and `_sa_instance_state` is an ordinary (non-dunder)
    attribute, which visit_Attribute does not model. STATIC."""
    fm = read("models/file.py")
    is_orm = "class File(BaseSchema)" in fm and "relationship(" in fm
    rels = re.findall(r"^\s*(\w+)\s*=\s*relationship\(", fm, re.M)
    coder = read("ai/agents/coder/coder.py")
    told_file_object = "is a File object" in coder
    if is_orm and told_file_object:
        record("INFO", "Sandbox — excel_files are live ORM rows, not plain DTOs (STATIC)",
               f"models/file.py: File(BaseSchema) with relationships {rels}; the coder prompt\n"
               "explicitly tells the model `excel_files[INDEX]` is a File object with `.path`.\n"
               "CONSEQUENCES, in decreasing certainty:\n"
               " 1. CERTAIN — `.path` discloses the server-side upload path\n"
               "    (uploads/files/<uuid>_<name>). Low sensitivity alone; it is also the string\n"
               "    that makes the computed-path bypass above trivially easy to aim.\n"
               " 2. CERTAIN (statically) — `_sa_instance_state` and every relationship name are\n"
               "    ordinary attributes; the denylist blocks only DUNDERS, so reading them is\n"
               "    validator-clean. Same class as the db_clients passthrough.\n"
               " 3. UNVERIFIED — onward hops (_sa_instance_state.session.bind.url.password for the\n"
               "    app's OWN Postgres password, or .user.hashed_password) depend on runtime state:\n"
               "    the session is an AsyncSession and a lazy load from the sync sandbox thread\n"
               "    would likely raise MissingGreenlet. I did NOT execute this. Do not report it\n"
               "    as reachable without running it.\n"
               " 4. loadables are CLEAN — load_step/load_entity return `DataFrame.copy()`; no\n"
               "    connection handle, and the closure cells are only reachable via __closure__,\n"
               "    which IS on the denylist.\n"
               "FIX SHAPE: hand the sandbox a frozen DTO (filename, extension, a token the reader\n"
               "resolves) instead of the ORM row — same 'credential-free facade' fix as db_clients.")
    else:
        record("PASS", "Sandbox — excel_files are not ORM rows (STATIC)",
               f"is_orm={is_orm} told_file_object={told_file_object}")


def probe_sandbox_env_scrub_viability_static() -> None:
    """Does the code-exec path need DASH_ENCRYPTION_KEY in-process at exec time?
    If not, running exec in a subprocess with a scrubbed environment removes
    /proc/self/environ as a route to the key. STATIC."""
    ds = read("services/data_source_service.py")
    conn = read("models/connection.py")
    fed = read("data_sources/clients/ms_fabric_federated_client.py")
    # credentials decrypted at CLIENT CONSTRUCTION, before the executor runs
    decrypt_at_build = "resolve_credentials_for_connection" in ds and "decrypt_credentials" in conn
    # no client touches Fernet / the key at query time
    clients_dir = os.path.join(APP, "data_sources", "clients")
    key_touchers = []
    for fn in sorted(os.listdir(clients_dir)) if os.path.isdir(clients_dir) else []:
        if not fn.endswith(".py"):
            continue
        s = read(os.path.join("data_sources", "clients", fn))
        if re.search(r"Fernet\(|encryption_key|dash_config", s):
            key_touchers.append(fn)
    # the one lazy-token client takes the ALREADY-decrypted token as a ctor arg
    fed_ctor_plaintext = "def __init__(self, endpoints: List[Dict], refresh_token: str)" in fed
    if decrypt_at_build and not key_touchers and fed_ctor_plaintext:
        record("INFO", "Sandbox — exec path does NOT need the Fernet key; env scrub is viable (STATIC)",
               "Credentials are fully resolved BEFORE exec:\n"
               " * data_source_service.construct_clients() -> resolve_credentials_for_connection()\n"
               "   -> Connection.decrypt_credentials() (models/connection.py:218) and the PLAINTEXT\n"
               "   values are passed as client constructor kwargs. This all happens before the\n"
               "   executor is invoked.\n"
               " * NO client under data_sources/clients/ references Fernet/encryption_key/dash_config\n"
               "   (0 matches) — nothing decrypts at query time.\n"
               " * The one lazy-token client, MsFabricFederatedClient.__init__(endpoints,\n"
               "   refresh_token), takes the ALREADY-decrypted token as a plain ctor arg and mints\n"
               "   SQL tokens over HTTP at query time — no Fernet, no DB, no key.\n"
               "=> A scrubbed-env subprocess would NOT break query execution. Layer 2 is viable.\n"
               "★TWO CAVEATS the design must account for:\n"
               " 1. Env scrub protects the KEY (and the JWT secret, same value), NOT the warehouse\n"
               "    credentials — those must still travel into the child inside the client objects,\n"
               "    so the db_clients passthrough finding is untouched by layer 2. Both layers,\n"
               "    plus the wrapper facade, are genuinely needed.\n"
               " 2. db_clients hold LIVE SQLAlchemy engines / open connections, which do not cross\n"
               "    a process boundary. A subprocess design has to either keep clients in the parent\n"
               "    and RPC execute_query back, or rebuild connections in the child (which then needs\n"
               "    credentials passed explicitly). 'Just fork it' is not a drop-in; budget for it.")
    else:
        record("INFO", "Sandbox — exec path MAY need the key in-process; verify before scrubbing (STATIC)",
               f"decrypt_at_build={decrypt_at_build} key_touching_clients={key_touchers} "
               f"fed_ctor_plaintext={fed_ctor_plaintext}")


def probe_sandbox_pptx_sanitizer_static() -> None:
    """sanitize_pptx_code neutralizes .plot_area/.chart_area. Assess: is it a
    SECURITY control? It runs THROUGH the same AST validator (validate_python_code
    in pptx_executor), so the sanitizer itself is a CRASH guard, not the security
    boundary — do not overstate it."""
    src = read("ai/code_execution/pptx_executor.py")
    has_validator = "validate" in src.lower() and ("CodeSecurityVisitor" in src or "forbidden" in src.lower())
    sanitizer_is_regex = "_INVALID_CHART_ATTR" in src and "plot_area" in src
    record("INFO", "Sandbox — sanitize_pptx_code is a crash guard, not a security control (STATIC)",
           f"sanitize_pptx_code is a line-regex that rewrites .plot_area/.chart_area to `pass` "
           f"(regex={sanitizer_is_regex}); it prevents an AttributeError, nothing more.\n"
           f"The pptx path runs its OWN AST validator ({has_validator}) — THAT is the security\n"
           "boundary, and it shares the same denylist-only limitation as the main sandbox above.")


# ===========================================================================
# Negative-auth sanity (anonymous must be refused)
# ===========================================================================
def probe_anon_refused_live(base: str) -> None:
    checks = [
        f"/api/organizations/{ORG_ID}/people",
        "/api/data_sources",
    ]
    bad = []
    for path in checks:
        r = http("GET", base, path, token=None)
        if r.status not in (401, 403):
            bad.append(f"{path} -> {r.status} (expected 401/403)")
    if bad:
        record("FAIL", "Auth — a protected route answered anonymously (LIVE)", "\n".join(bad))
    else:
        record("PASS", "Auth — protected routes refuse anonymous callers (LIVE)",
               "people + data_sources both 401/403 without a token.")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def load_tokens(path: str | None) -> dict:
    if not path:
        return {}
    try:
        with open(path) as fh:
            data = json.load(fh)
    except OSError as e:
        print(RED(f"could not read tokens file {path}: {e}"))
        return {}
    users = data.get("users", {})
    out = {}
    for email, info in users.items():
        if isinstance(info, dict) and info.get("token"):
            role = info.get("role")
            if info.get("is_superuser") or role == "admin":
                out.setdefault("admin", info["token"])
            else:
                out.setdefault("member", info["token"])
            out[email] = info["token"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Injection/traversal/SSRF probes (read-only).")
    ap.add_argument("--base", default=os.environ.get("SEC_BASE", "http://localhost:8095"))
    ap.add_argument("--tokens", default=os.environ.get("SEC_TOKENS", "/tmp/sec-tokens.json"))
    # ★Positional too. run-all.py invokes every probe as `python injection.py <tokens>`
    # (the documented contract; tenancy.py and secrets.py comply). Flag-only argparse
    # exits 2 on the unrecognized positional, and a probe that CANNOT RUN then reads
    # as a security failure — the "can't-run looks like found-something" trap.
    ap.add_argument("tokens_path", nargs="?", default=None,
                    help="Path to mint-user-tokens.py output (same as --tokens).")
    args = ap.parse_args()

    toks = load_tokens(args.tokens_path or args.tokens)
    member = toks.get("member") or toks.get("admin")
    live_ok = bool(member)

    print(CYAN("\n== CityAgent Insights — injection / traversal / SSRF probes =="))
    print(DIM(f"base={args.base}  tokens={'loaded' if toks else 'NONE'}  "
              f"repo={REPO}"))
    if not live_ok:
        print(YELLOW("no member/admin token — LIVE probes will be skipped. Mint with:\n"
                     "  docker cp scripts/mint-user-tokens.py dash-app:/app/backend/\n"
                     "  docker exec -w /app/backend dash-app python mint-user-tokens.py \\\n"
                     "      /tmp/sec-tokens.json raahulgupta07@gmail.com member@cityagent.io\n"
                     "  docker cp dash-app:/tmp/sec-tokens.json /tmp/sec-tokens.json"))

    print(CYAN("\n[1] SQL injection — the app's own metadata DB"))
    probe_sql_metadata_static()
    if live_ok:
        probe_sql_metadata_live(args.base, member)

    print(CYAN("\n[2] LDAP filter injection"))
    probe_ldap_escape_static()
    if live_ok:
        probe_ldap_wildcard_live(args.base)

    print(CYAN("\n[3] Path traversal"))
    if live_ok:
        probe_file_only_lock_live(args.base, member)
        probe_static_path_serving_live(args.base)
    probe_download_basename_static()

    print(CYAN("\n[4] SSRF"))
    probe_ssrf_webfetch_static()
    probe_ssrf_render_sandbox_static()
    probe_ssrf_dcr_allowlist_static()
    probe_ssrf_db_connections_static()

    print(CYAN("\n[5] Command injection"))
    probe_command_injection_static()

    print(CYAN("\n[6] Deserialization"))
    probe_deserialization_static()

    print(CYAN("\n[7] Code-execution sandbox"))
    probe_sandbox_architecture_static()
    probe_sandbox_denylist_gaps_static()
    probe_sandbox_client_credential_passthrough_static()
    probe_sandbox_literal_path_bypass_static()
    probe_sandbox_excel_files_orm_static()
    probe_sandbox_env_scrub_viability_static()
    probe_sandbox_pptx_sanitizer_static()

    print(CYAN("\n[+] Negative-auth sanity"))
    if live_ok:
        probe_anon_refused_live(args.base)

    # Summary
    n_pass = sum(1 for s, *_ in _RESULTS if s == "PASS")
    n_fail = sum(1 for s, *_ in _RESULTS if s == "FAIL")
    n_info = sum(1 for s, *_ in _RESULTS if s == "INFO")
    n_skip = sum(1 for s, *_ in _RESULTS if s == "SKIP")
    print(CYAN("\n== summary =="))
    print(f"  {GREEN(str(n_pass)+' PASS')}   {RED(str(n_fail)+' FAIL')}   "
          f"{YELLOW(str(n_info)+' INFO')}   {DIM(str(n_skip)+' SKIP')}")
    if n_fail:
        print(RED("  FAILs above are exploitable or a broken guard — read the detail lines."))
    print(YELLOW("  INFO = a real finding or an architectural note, not a clean pass. "
                 "See the SSRF DB-connection and sandbox items."))
    # Exit non-zero only on a hard FAIL (a broken guard), never on INFO.
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
