"""Every HTTP route declares how it is authorized — and the declaration works.

WHY THIS FILE EXISTS
--------------------
This codebase has already shipped four distinct authorization defects that all
*look correct in review*. Reading a route and nodding is not a check; this file
is the check.

    1. GATE ABOVE ROUTER. ``@requires_permission`` written ABOVE ``@router.get``
       means FastAPI registers the UNWRAPPED function. The gate is dead code
       that still greps as present. Six routes in ``routes/completion.py`` had
       it. Decorators apply bottom-up, so the router must be the TOP one.

    2. A GATE THAT CANNOT REACH ITS OBJECT. ``requires_permission`` only runs
       its object-level check under ``if model and object_id is not None``, and
       ``object_id`` is read from a HARDCODED list of eight parameter names
       (``permissions_decorator.py``). Pass ``model=`` on a route whose path
       parameter is not one of those eight and the entire object block —
       org-scoping AND ``owner_only`` — is skipped in silence.

    3. NO ORG SCOPING IN THE SERVICE. ``report_service.get_report`` once lacked
       ``.filter(Report.organization_id == organization.id)``. A perfectly gated
       route still returns another org's row when the query never scoped.

    4. AN OVER-TIGHT GATE IS ALSO A BUG. ``/plans`` was set to
       ``manage_settings``, which members lack, and locked members out of their
       own conversations. "Can the legitimate owner still do this?" is half the
       question; refusal-only reasoning never asks it.

★★★ GREEN HERE DOES NOT MEAN "NO AUTHORIZATION BUGS".
It means: no route has a *newly* dead gate, and every ungated route is one this
file already knows about. ``KNOWN_DEAD_MODEL_GATES`` and
``PUBLIC_SURFACES_ON_THE_LEGACY_VISIBILITY_CHECK`` below are **open defects**,
pinned so they cannot spread and so that fixing one turns this file red and asks
for the entry to be deleted. They are not a clean bill of health. Read them.

★★★ PROVEN TO FAIL, WHICH IS THE ONLY REASON TO TRUST IT.
``test_the_scanner_detects_every_bug_shape`` runs the scanner against synthetic
sources carrying each defect and asserts it fires. That test is what stops this
file from becoming, in the words of the last one of these we wrote, "a comment
with a test's salary". The scanner was also run against the real pre-fix file
``routes/completion.py`` as it stood before the decorator-order fix: it reported
**5 gate-above-router hits and 1 over-tight gate there, and 0 on both HEAD and
the working tree**.

★ AST ONLY, NEVER REGEX. Four separate regex-based verification attempts in this
repo produced guards that could not fail: a ``#``-comment stripper that still
read the docstring, and a string-literal stripper that removed the very token
being searched for. Decorator ORDER is not expressible as a regex anyway.

★ Reads source files only — no database, no app boot — so it belongs in
``tests/unit/fork``. It also runs correctly inside ``dash-app``, because
everything it opens is under ``backend/app`` rather than ``locales/`` or
``frontend/``.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
APP = REPO / "backend" / "app"
ROUTE_FILES = sorted(APP.glob("routes/*.py")) + sorted(APP.glob("ee/**/*.py"))
MODEL_FILES = sorted(APP.glob("models/*.py"))

HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options",
                "trace", "api_route", "websocket"}
GATE_DECORATORS = {"requires_permission", "requires_resource_permission"}

# Dependencies that are themselves an authorization gate: each resolves the
# principal AND decides whether it may proceed, in place of a decorator.
GATE_DEPENDENCIES = {
    "console_scope",   # core/console_access.py — membership + CONSOLE_ADMIN_PERMISSIONS or 403
    "scim_auth",       # ee/scim/auth.py — bearer token → Organization, 401 otherwise
    "mcp_auth",        # routes/mcp.py — MCP bearer/session auth
}

# Dependencies that establish WHO is calling but decide nothing on their own.
AUTHN_DEPENDENCIES = {
    "current_user", "current_user_optional", "auth_current_user", "get_user_manager",
}

# ★Dependencies that REQUIRE a credential. `current_user_optional` deliberately
# does not: it reads a session when one is present and yields None when it is
# not, which is exactly how a share link serves an anonymous viewer while still
# recognising a signed-in one. Counting it as "authenticated" made the
# public-by-design assertion fail the moment the two anonymous widget surfaces
# were fixed to tell 'public' apart from 'internal' — a correct change reported
# as a broken claim.
AUTHN_REQUIRED_DEPENDENCIES = AUTHN_DEPENDENCIES - {"current_user_optional"}


# ─────────────────────────────────────────────────────────────────────────────
# Scanner
# ─────────────────────────────────────────────────────────────────────────────

def _module_key(path: Path) -> str:
    """'routes/report', 'ee/scim/routes' — unique, unlike the bare basename
    (``routes/report.py`` and ``ee/routes.py`` both end in a name that repeats)."""
    return path.relative_to(APP).with_suffix("").as_posix()


def _decorator_ident(node: ast.expr) -> tuple[str | None, str | None]:
    """(base, attr) for ``base.attr(...)`` / (None, name) for ``name(...)``."""
    func = node.func if isinstance(node, ast.Call) else node
    if isinstance(func, ast.Attribute):
        base = func.value.id if isinstance(func.value, ast.Name) else None
        return base, func.attr
    if isinstance(func, ast.Name):
        return None, func.id
    return None, None


def _router_names(tree: ast.Module) -> set[str]:
    """Names bound to ``APIRouter(...)``.

    Derived from the source rather than guessed from a naming convention, so a
    router called something other than ``router`` still gets scanned.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            _, called = _decorator_ident(node.value)
            if called == "APIRouter":
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """``from x import Artifact as ArtifactModel`` → {'ArtifactModel': 'Artifact'}.

    ★ Load-bearing. Without it, eleven ``model=ArtifactModel`` routes are
    reported as naming a model with no ``organization_id`` — ``Artifact`` has
    one; ``ArtifactModel`` is just what this module calls it. Eleven false
    positives is enough noise to make a real finding unfindable.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
    return aliases


def _path_params(path: str) -> list[str]:
    out, i = [], 0
    while (start := path.find("{", i)) != -1:
        end = path.find("}", start)
        if end == -1:
            break
        out.append(path[start + 1:end].split(":")[0])
        i = end + 1
    return out


def _signature_dependencies(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Callables named inside ``Depends(...)`` / ``Security(...)`` defaults."""
    found: set[str] = set()
    args = fn.args
    for default in list(args.defaults) + [d for d in args.kw_defaults if d is not None]:
        if not isinstance(default, ast.Call):
            continue
        _, called = _decorator_ident(default)
        if called not in ("Depends", "Security"):
            continue
        for arg in default.args:
            if isinstance(arg, ast.Name):
                found.add(arg.id)
            elif isinstance(arg, ast.Attribute):
                found.add(arg.attr)
    return found


class Route:
    __slots__ = ("module", "func", "line", "http", "path", "router_index",
                 "gates", "path_params", "deps")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    @property
    def key(self) -> tuple[str, str]:
        return (self.module, self.func)

    def __repr__(self) -> str:
        return f"{self.module}.py:{self.line} {self.http} {self.path or '(no literal path)'}"


def scan_source(source: str, module: str) -> list[Route]:
    tree = ast.parse(source)
    routers = _router_names(tree)
    routes: list[Route] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        router_indexes, gates, http, path = [], [], None, None
        for index, dec in enumerate(node.decorator_list):
            base, attr = _decorator_ident(dec)
            if base in routers and attr in HTTP_METHODS:
                router_indexes.append(index)
                if http is None:
                    http = attr.upper()
                    if isinstance(dec, ast.Call) and dec.args and isinstance(dec.args[0], ast.Constant):
                        path = dec.args[0].value
            elif attr in GATE_DECORATORS:
                keywords = {k.arg for k in dec.keywords} if isinstance(dec, ast.Call) else set()
                permission = None
                if isinstance(dec, ast.Call) and dec.args:
                    first = dec.args[0]
                    permission = first.value if isinstance(first, ast.Constant) else ast.unparse(first)
                gates.append({
                    "index": index, "name": attr, "permission": permission,
                    "keywords": keywords, "source": ast.unparse(dec),
                })
        if not router_indexes:
            continue
        routes.append(Route(
            module=module, func=node.name, line=node.lineno, http=http, path=path,
            router_index=min(router_indexes), gates=gates,
            path_params=_path_params(path or ""),
            deps=_signature_dependencies(node),
        ))
    return routes


def _all_routes() -> list[Route]:
    routes: list[Route] = []
    for path in ROUTE_FILES:
        routes.extend(scan_source(path.read_text(encoding="utf-8"), _module_key(path)))
    return routes


ROUTES = _all_routes()


# ─────────────────────────────────────────────────────────────────────────────
# What the decorator can actually see, read from the decorator itself
# ─────────────────────────────────────────────────────────────────────────────

def _extractable_id_params() -> set[str]:
    """The FALLBACK parameter names ``requires_permission`` still recognises.

    Parsed out of ``permissions_decorator.py`` rather than copied here, because
    a copy goes stale the day someone extends the real list and then this file
    reports a working gate as dead.

    ★The mechanism this parsed CHANGED on 2026-08-09, and the guard failing on
    that change is the guard working. It used to read:

        object_id = report_id or completion_id or data_source_id or ...

    a hardcoded eight-name chain — which is precisely the defect this file was
    written to expose: a route whose path parameter was not one of the eight got
    ``object_id = None``, so the whole object block was skipped in silence, and
    ``/api/visualizations/{visualization_id}`` was readable AND writable across
    tenants by any member.

    The primary lookup is now DERIVED from the declared model
    (``Visualization`` -> ``visualization_id``), so a new object route cannot
    produce a dead gate by naming its parameter differently. The list below
    survives only as a fallback for routes whose parameter does not match their
    model's name, and is read from ``_LEGACY_ID_PARAMS``.
    """
    source = (APP / "core" / "permissions_decorator.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_LEGACY_ID_PARAMS"
                        for t in node.targets)
                and isinstance(node.value, (ast.Tuple, ast.List, ast.Set))):
            return {
                e.value for e in node.value.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            }
    # Older shape, kept so this guard still reports honestly on a pre-fix tree
    # instead of collapsing to an empty set (which would make every model= check
    # below pass vacuously).
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "object_id" for t in node.targets)
                and isinstance(node.value, ast.BoolOp)
                and isinstance(node.value.op, ast.Or)):
            return {v.id for v in node.value.values if isinstance(v, ast.Name)}
    return set()


def _snake(name: str) -> str:
    """CamelCase -> snake_case, mirroring the decorator's own helper."""
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _decorator_derives_id_from_model() -> bool:
    """True when the decorator resolves the id from the declared model.

    If this is False the guard must fall back to judging gates by the legacy
    list alone — otherwise it would call a genuinely dead gate 'fine' because a
    derivation it assumed exists does not.
    """
    source = (APP / "core" / "permissions_decorator.py").read_text(encoding="utf-8")
    return "_object_id_for" in source and "def _object_id_for" in source


EXTRACTABLE_ID_PARAMS = _extractable_id_params()


def _model_columns() -> dict[str, set[str]]:
    """{model class name: every attribute it declares, inherited included}.

    Resolved across ``models/*.py`` by following base classes, because
    ``organization_id`` could plausibly have lived on ``BaseSchema``. It does
    not — which is exactly why ``model=Visualization`` can never scope.
    """
    own: dict[str, set[str]] = {}
    bases: dict[str, list[str]] = {}
    for path in MODEL_FILES:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a broken model file is its own failure
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            columns: set[str] = set()
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    columns.update(t.id for t in stmt.targets if isinstance(t, ast.Name))
                elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    columns.add(stmt.target.id)
                elif isinstance(stmt, ast.FunctionDef) and any(
                        isinstance(d, ast.Name) and d.id == "declared_attr"
                        for d in stmt.decorator_list):
                    columns.add(stmt.name)
            own[node.name] = columns
            bases[node.name] = [b.id for b in node.bases if isinstance(b, ast.Name)]

    def resolve(name: str, seen: set[str] | None = None) -> set[str]:
        seen = seen or set()
        if name in seen or name not in own:
            return set()
        seen.add(name)
        columns = set(own[name])
        for base in bases.get(name, []):
            columns |= resolve(base, seen)
        return columns

    return {name: resolve(name) for name in own}


MODEL_COLUMNS = _model_columns()


def _model_gates():
    """Yield (route, declared name, resolved model name, gate) per ``model=`` gate."""
    for path in ROUTE_FILES:
        source = path.read_text(encoding="utf-8")
        aliases = _import_aliases(ast.parse(source))
        for route in scan_source(source, _module_key(path)):
            for gate in route.gates:
                if gate["name"] != "requires_permission" or "model" not in gate["keywords"]:
                    continue
                declared = gate["source"].split("model=")[1].split(",")[0].split(")")[0].strip()
                yield route, declared, aliases.get(declared, declared), gate


# ─────────────────────────────────────────────────────────────────────────────
# Declarations. Every route with no permission decorator must appear in exactly
# one of these, with a reason. A new one that appears in none fails the suite.
# ─────────────────────────────────────────────────────────────────────────────

# Reachable with no credential of any kind. Each entry is a decision.
PUBLIC_BY_DESIGN: dict[tuple[str, str], str] = {
    ("ee/routes", "get_license_status"):
        "Licence tier drives which UI a signed-out visitor is offered; no tenant data.",
    ("routes/dash_settings", "get_frontend_settings"):
        "The login page reads it to know which SSO buttons to draw — auth here deletes sign-in.",
    ("routes/dash_settings", "get_i18n_config"):
        "Locale list for the login page, same reason.",
    ("routes/visualization", "get_visualization_meta"):
        "Static capability table per chart type; no identifiers, no data.",
    ("routes/instruction", "get_instruction_categories"):
        "Static enum of category names.",
    ("routes/instruction", "get_instruction_statuses"):
        "Static enum of status names.",
    ("routes/excel", "get_manifest"):
        "Office add-in manifest — Microsoft fetches it unauthenticated by specification.",
    ("routes/excel", "get_commands"):
        "Static add-in HTML shell, no data.",
    ("routes/excel", "get_taskpane"):
        "Static add-in HTML shell; the pane authenticates from inside.",
    ("routes/auth", "authorize"):
        "Start of the SSO redirect — by definition pre-credential.",
    ("routes/connection_oauth", "oauth_callback"):
        "Provider redirects the browser here; the OAuth `state` parameter is the credential.",
    ("routes/oauth_server", "protected_resource_metadata"):
        "RFC 9728 discovery document, required to be anonymous.",
    ("routes/oauth_server", "authorization_server_metadata"):
        "RFC 8414 discovery document, required to be anonymous.",
    ("routes/oauth_server", "authorize_redirect"):
        "OAuth authorize entry point; bounces to login when there is no session.",
    ("routes/oauth_server", "token_endpoint"):
        "RFC 6749 token exchange — the client credential IS the request body.",
    ("routes/oauth_server", "get_client_info"):
        "Client display name/logo, rendered on the consent screen before login.",
    ("routes/branding", "get_general_icon"):
        "Instance logo, drawn on the sign-in page.",
    ("routes/branding", "get_thumbnail"):
        "Static thumbnail asset.",
    ("routes/branding", "get_user_avatar"):
        "Avatar served by opaque key, shown on shared reports.",
    ("routes/webhook_receiver", "probe_webhook"):
        "Inbound webhook probe; the unguessable {token} in the path is the credential.",
    ("routes/webhook_receiver", "receive_webhook"):
        "Same — third-party senders cannot hold a session.",
    ("routes/slack_webhook", "slack_webhook"):
        "Slack callback; authenticated by Slack signing secret inside the handler.",
    ("routes/teams_webhook", "teams_webhook"):
        "Teams callback, verified inside the handler.",
    ("routes/whatsapp_webhook", "whatsapp_webhook_verify"):
        "WhatsApp verification challenge; Meta requires an anonymous GET.",
    ("routes/whatsapp_webhook", "whatsapp_webhook"):
        "WhatsApp inbound callback, signature-verified inside the handler.",
    ("routes/external_user_mapping", "verify_integration_user_token"):
        "Single-use emailed {token} is the credential.",
    ("routes/file", "get_file_embed"):
        "Embed served against a signed file token (core/file_tokens.py).",
    ("routes/local_runtime", "pair_claim"):
        "Documented public by design: the helper has no session. Secret is the short-lived "
        "pairing code, and throttle_pair_claim makes guessing cost something.",
    ("routes/local_runtime", "register_folders"):
        "Helper bearer token via _runtime_from_token.",
    ("routes/local_runtime", "heartbeat"):
        "Helper bearer token via _runtime_from_token.",
    ("routes/local_runtime", "next_job"):
        "Helper bearer token via _runtime_from_token.",
    ("routes/local_runtime", "proxy_query"):
        "Helper bearer token via _runtime_from_token.",
    ("routes/local_runtime", "job_result"):
        "Helper bearer token via _runtime_from_token.",
    ("routes/artifact", "confirm_artifact"):
        "OPEN FINDING (medium). Resolves a pending tool confirmation with no principal at "
        "all. The uuid4 id is the only thing standing in the way, and completion.py gates "
        "the same mechanism behind create_reports + may_respond(). Two doors, one locked.",
    ("routes/widget", "get_widgets_for_public_report"):
        "OPEN FINDING (high). Public share surface, but tests status=='published', which "
        "report_service sets for 'internal' and 'shared' too. See "
        "PUBLIC_SURFACES_ON_THE_LEGACY_VISIBILITY_CHECK.",
    ("routes/text_widget", "get_widgets_for_public_report"):
        "OPEN FINDING (high). Same defect as routes/widget.",
}

# Authenticated, but authorization is enforced in the handler or the service
# rather than by a decorator (super-admin checks, _require_resource_manage,
# report_service._check_visibility, per-user ownership filters, …).
#
# ★ This is an INVENTORY, not a certificate. Freezing it is what makes a NEW
#   undecorated route fail this suite instead of blending into a crowd of 149.
#   Adding an entry here is a claim you have read the handler and found the
#   check. Do not add one to make the suite green.
AUTHENTICATED_UNGATED_BASELINE: set[tuple[str, str]] = {
    ("ee/routes", "get_license_usage"),
    ("routes/agent_yaml", "apply_agent"),
    ("routes/agent_yaml", "get_agent_yaml"),
    ("routes/agent_yaml", "list_agent_manifests"),
    ("routes/api_key", "create_api_key"),
    ("routes/api_key", "delete_api_key"),
    ("routes/api_key", "list_api_keys"),
    ("routes/auth", "callback"),
    ("routes/auth", "ldap_login"),
    ("routes/build", "diff_builds"),
    ("routes/build", "diff_builds_detailed"),
    ("routes/build", "get_build"),
    ("routes/build", "get_build_contents"),
    ("routes/build", "get_main_build"),
    ("routes/build", "list_builds"),
    ("routes/changelog", "get_changelog"),
    ("routes/connection", "delete_my_connection_credentials"),
    ("routes/connection", "get_connection_indexing"),
    ("routes/connection", "get_connection_tables"),
    ("routes/connection", "get_connection_tools_list"),
    ("routes/connection", "get_connection_user_roster"),
    ("routes/connection", "list_connections"),
    ("routes/connection", "refresh_my_connection_schema"),
    ("routes/connection", "set_connection_query_identity"),
    ("routes/connection", "test_my_connection_credentials"),
    ("routes/connection_oauth", "oauth_authorize"),
    ("routes/data_source", "create_data_source"),
    ("routes/data_source", "download_setup_doc"),
    ("routes/data_source", "get_active_data_sources"),
    ("routes/data_source", "get_available_data_sources"),
    ("routes/data_source", "get_connected_channels"),
    ("routes/data_source", "get_connector_toggles"),
    ("routes/data_source", "get_connectors_catalog"),
    ("routes/data_source", "get_custom_api_presets"),
    ("routes/data_source", "get_data_source_fields"),
    ("routes/data_source", "get_data_sources"),
    ("routes/data_source", "get_hidden_data_sources"),
    ("routes/data_source", "hide_data_source"),
    ("routes/data_source", "unhide_data_source"),
    ("routes/entity", "get_entity"),
    ("routes/entity", "list_entities"),
    ("routes/eval_yaml", "apply_eval"),
    ("routes/eval_yaml", "export_eval_yaml"),
    ("routes/eval_yaml", "list_evals"),
    ("routes/external_user_mapping", "complete_verification"),
    ("routes/file_reference", "create_file_reference"),
    ("routes/file_reference", "delete_file_reference"),
    ("routes/file_reference", "list_connection_files"),
    ("routes/file_reference", "list_file_references"),
    ("routes/instance_features", "get_instance_features"),
    ("routes/instance_features", "set_instance_feature"),
    ("routes/instruction", "analyze_instruction_endpoint"),
    ("routes/instruction", "compare_instruction_versions"),
    ("routes/instruction", "create_private_instruction"),
    ("routes/instruction", "get_available_references"),
    ("routes/instruction", "get_instruction"),
    ("routes/instruction", "get_instruction_activity"),
    ("routes/instruction", "get_instruction_activity_entry"),
    ("routes/instruction", "get_instruction_build_verdict"),
    ("routes/instruction", "get_instruction_counts"),
    ("routes/instruction", "get_instruction_pending_builds"),
    ("routes/instruction", "get_instruction_resolved_evals"),
    ("routes/instruction", "get_instruction_review_hunks"),
    ("routes/instruction", "get_instruction_source_types"),
    ("routes/instruction", "get_instruction_version"),
    ("routes/instruction", "get_instruction_versions"),
    ("routes/instruction", "get_instructions"),
    ("routes/instruction", "get_pending_change_instruction_ids"),
    ("routes/instruction", "list_instruction_directories"),
    ("routes/instruction", "list_instruction_labels"),
    ("routes/instruction", "search_knowledge"),
    ("routes/keeper", "keeper_activity"),
    ("routes/keeper", "keeper_overview"),
    ("routes/keeper", "keeper_run"),
    ("routes/keeper", "keeper_schedule"),
    ("routes/keeper", "keeper_sync_all"),
    ("routes/llm", "get_models"),
    ("routes/local_runtime", "folder_request_status"),
    ("routes/local_runtime", "list_folders"),
    ("routes/local_runtime", "pair_start"),
    ("routes/local_runtime", "request_folder"),
    ("routes/local_runtime", "runtime_status"),
    ("routes/local_runtime", "runtime_toggle"),
    ("routes/local_runtime", "runtime_unpair"),
    ("routes/local_runtime", "unshare_folder"),
    ("routes/mentions", "get_available_mentions"),
    ("routes/notification", "count_notifications"),
    ("routes/notification", "dismiss_notification"),
    ("routes/notification", "list_notifications"),
    ("routes/notification", "read_all_notifications"),
    ("routes/notification", "read_notification"),
    ("routes/oauth_server", "authorize_approve"),
    ("routes/organization", "create_organization"),
    ("routes/organization", "get_organization_groups"),
    ("routes/organization", "get_organizations"),
    ("routes/organization_settings", "get_organization_locale"),
    ("routes/organization_settings", "get_organization_settings"),
    ("routes/organization_settings", "get_organization_timezone"),
    ("routes/organization_settings", "get_organization_week_start"),
    ("routes/organization_settings", "list_supported_timezones"),
    ("routes/prompt", "create_prompt"),
    ("routes/prompt", "delete_prompt"),
    ("routes/prompt", "get_prompt"),
    ("routes/prompt", "list_prompts"),
    ("routes/prompt", "run_prompt"),
    ("routes/prompt", "run_prompt_for"),
    ("routes/prompt", "run_prompt_for_targets"),
    ("routes/prompt", "update_prompt"),
    ("routes/rbac", "create_resource_grant"),
    ("routes/rbac", "delete_resource_grant"),
    ("routes/rbac", "get_permissions_registry"),
    ("routes/rbac", "update_resource_grant"),
    ("routes/report", "export_public_report_pdf"),
    ("routes/report", "get_public_artifact"),
    ("routes/report", "get_public_artifacts"),
    ("routes/report", "get_public_conversation"),
    ("routes/report", "get_public_file_embed_token"),
    ("routes/report", "get_public_layouts"),
    ("routes/report", "get_public_queries"),
    ("routes/report", "get_public_query_step"),
    ("routes/report", "get_public_report"),
    ("routes/report", "refresh_public_report_on_view"),
    ("routes/report", "viewer_run_report"),
    ("routes/review", "count_review"),
    ("routes/review", "dismiss_review"),
    ("routes/review", "list_review"),
    ("routes/review", "read_all_review"),
    ("routes/review", "read_review"),
    ("routes/review", "resolve_review"),
    ("routes/scheduled_prompt", "list_all_scheduled_prompts"),
    ("routes/trigger", "create_trigger"),
    ("routes/trigger", "delete_trigger"),
    ("routes/trigger", "get_trigger"),
    ("routes/trigger", "get_trigger_runs"),
    ("routes/trigger", "list_triggers"),
    ("routes/trigger", "rotate_trigger_secret"),
    ("routes/trigger", "update_trigger"),
    ("routes/usage_limits", "get_my_daily_usage"),
    ("routes/user_password", "change_my_password"),
    ("routes/user_password", "my_password_status"),
    ("routes/user_password", "set_user_password"),
    ("routes/user_profile", "delete_my_avatar"),
    ("routes/user_profile", "get_my_default_model"),
    ("routes/user_profile", "get_my_instructions"),
    ("routes/user_profile", "get_user_profile"),
    ("routes/user_profile", "update_my_default_model"),
    ("routes/user_profile", "update_my_instructions"),
    ("routes/user_profile", "upload_my_avatar"),
    ("routes/webhook", "list_sources"),
}

# ★★★ OPEN DEFECTS, pinned so they cannot multiply. Each entry is a gate that
# reads as protection and enforces nothing. Fix one → this test goes red →
# delete its entry. See the findings write-up for severity and impact.
KNOWN_DEAD_MODEL_GATES: dict[tuple[str, str], str] = {
    # ── FIXED 2026-08-09, entries retired ────────────────────────────────────
    # ("routes/visualization", "get_visualization") and "patch_visualization"
    # were CRITICAL: model=Visualization with neither organization_id nor
    # user_id, and visualization_id was not extractable, so org-scoping and
    # owner_only were both skipped and VisualizationService did not filter by
    # org. Any member of any org could read AND patch any chart by id, and the
    # write stuck (confirmed live).
    #
    # The four routes/entity entries were LOW — inert gate, but entity_service
    # scoped every load by organization_id, so defence-in-depth only.
    #
    # Both classes are closed by the same change: the object id is now DERIVED
    # from the declared model instead of looked up in a hardcoded eight-name
    # list, and models that hang off a Report (Visualization, Widget,
    # TextWidget, Step) are authorized through the parent report's visibility
    # in core/object_scope.py rather than through columns they do not have.
    #
    # They are deleted rather than left with a "fixed" note, because this file's
    # own stale-entry test exists to force exactly that — a pinned defect that
    # scans clean must leave the baseline, or the baseline stops meaning
    # "currently broken".
    ("routes/external_platform", "get_integration"):
        "LOW. platform_id not extractable; external_platform_service scopes by org.",
    ("routes/external_platform", "update_integration"): "LOW. As get_integration.",
    ("routes/external_platform", "delete_integration"): "LOW. As get_integration.",
    ("routes/external_platform", "test_integration"): "LOW. As get_integration.",
}

# Public share surfaces that gate on the legacy ``status == 'published'`` flag
# instead of ``artifact_visibility``. report_service.py sets
# ``status = 'published' if visibility != 'none'``, so 'internal' (org members
# only) and 'shared' (named users only) both satisfy it — from an anonymous
# request. The canonical check is report_service._check_visibility, which
# answers 401 for both.
# ── FIXED 2026-08-09, both entries retired ───────────────────────────────────
# `widget_service.get_widgets_for_public_report` and
# `text_widget_service.get_text_widgets_for_public_report` both gated on
# `report.status != 'published'`. Since report_service sets
# `status = 'published' if visibility != 'none'`, that was true for 'internal'
# (organization-only) and 'shared' (named people) as well — so an ANONYMOUS
# caller holding a report id received the widgets, and `last_step` carries the
# generated SQL and the result rows. Confirmed live.
#
# Both now call `ReportService._check_visibility(..., 'artifact_visibility', user)`
# — the canonical rule the eleven sibling /r/ routes in report.py already used —
# and both routes take `current_user_optional`, so a signed-in viewer who may
# legitimately see an internal report is not refused alongside the anonymous
# caller who may not. The None-dereference that turned an unknown id into a 500
# is fixed in the same edit.
#
# Emptied rather than annotated: this file's stale-entry assertion exists to
# force a fixed defect out of the baseline, so that what remains always means
# "still broken".
PUBLIC_SURFACES_ON_THE_LEGACY_VISIBILITY_CHECK: dict[str, str] = {}


# ─────────────────────────────────────────────────────────────────────────────
# The scanner must work at all
# ─────────────────────────────────────────────────────────────────────────────

def test_the_scanner_sees_the_whole_route_surface():
    """A refactor that breaks the scanner must go RED, not quietly pass.

    Every assertion below is 'no route has defect X'. All of them are trivially
    satisfied by finding no routes, which is exactly the failure this repo has
    already had once.
    """
    assert len(ROUTES) >= 550, f"only {len(ROUTES)} routes found — scanner is broken"
    modules = {r.module for r in ROUTES}
    assert len(modules) >= 55, f"only {len(modules)} route modules — scanner is broken"
    assert sum(1 for r in ROUTES if r.gates) >= 300, "gate detection found almost nothing"


def test_the_extraction_list_was_actually_parsed():
    """``EXTRACTABLE_ID_PARAMS`` is read out of the decorator, so it must parse."""
    assert len(EXTRACTABLE_ID_PARAMS) >= 5, (
        f"parsed {EXTRACTABLE_ID_PARAMS!r} from permissions_decorator.py — the "
        "`object_id = a or b or ...` assignment moved or changed shape. Every "
        "model= check below silently weakens until this is fixed."
    )
    assert "report_id" in EXTRACTABLE_ID_PARAMS


def test_the_model_column_map_was_actually_built():
    assert len(MODEL_COLUMNS) >= 40, "models/*.py did not parse into a column map"
    assert "organization_id" in MODEL_COLUMNS["Report"], "Report lost organization_id?"


# ─────────────────────────────────────────────────────────────────────────────
# Defect 1 — a gate above its router is dead code
# ─────────────────────────────────────────────────────────────────────────────

def test_no_permission_gate_sits_above_its_router():
    """Decorators apply bottom-up: the router decorator must be the topmost one.

    When ``@router.get`` is applied first it registers the RAW function, and the
    permission wrapper built afterwards is never what FastAPI calls.
    """
    offenders = [
        f"{route!r} — {gate['source']} is above the router decorator"
        for route in ROUTES for gate in route.gates
        if gate["index"] < route.router_index
    ]
    assert not offenders, (
        "Permission decorator registered ABOVE the router decorator; FastAPI "
        "serves the UNGATED function:\n  " + "\n  ".join(offenders)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Defect 2 — a gate that cannot reach the object it claims to protect
# ─────────────────────────────────────────────────────────────────────────────

def test_every_model_gate_can_actually_reach_its_object():
    """``model=`` does nothing unless the decorator can resolve the object id.

    Two ways it can now: the name DERIVED from the declared model
    (``Visualization`` -> ``visualization_id``), which is the primary lookup, or
    one of the legacy fallback names. Before 2026-08-09 only the legacy list
    existed, and a route naming its parameter anything else got a silently dead
    gate — the visualization cross-tenant read/write.
    """
    derives = _decorator_derives_id_from_model()
    offenders = {}
    for route, declared, resolved, gate in _model_gates():
        ids = [p for p in route.path_params if p.endswith("_id")]
        if not ids:
            continue
        reachable = any(p in EXTRACTABLE_ID_PARAMS for p in ids)
        if derives and resolved:
            reachable = reachable or f"{_snake(resolved)}_id" in ids
        if not reachable:
            offenders[route.key] = (
                f"{route!r} — model={declared} but {ids} is neither the derived "
                f"name {_snake(resolved or '')}_id nor in "
                f"{sorted(EXTRACTABLE_ID_PARAMS)}; the object check never runs"
            )
    _assert_matches_known_defects(offenders, "unreachable model= gate")


def _report_scoped_models() -> set[str]:
    """Models the decorator authorizes through their parent Report.

    Read from ``core/object_scope.py`` rather than copied, for the same reason
    the id list is parsed rather than copied: a copy goes stale and then this
    guard calls a working gate dead.
    """
    path = APP / "core" / "object_scope.py"
    if not path.exists():
        return set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "REPORT_SCOPED_MODELS"
                        for t in node.targets)
                and isinstance(node.value, ast.Dict)):
            return {
                k.value for k in node.value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    return set()


REPORT_SCOPED = _report_scoped_models()


def test_every_model_gate_names_an_org_scoped_model():
    """``model=X`` scopes with ``X.organization_id`` — unless X hangs off a Report.

    ★Visualization, Widget, TextWidget and Step have NO organization_id and no
    user_id. Adding their ids to the extraction list alone would have turned a
    silent hole into a 500 (AttributeError on ``X.organization_id``), which is
    why the fix routes them through the parent report instead. Those models are
    therefore legitimately exempt HERE, and are covered by their own path in
    ``core/object_scope.py``.
    """
    offenders = {}
    for route, declared, resolved, gate in _model_gates():
        if resolved in REPORT_SCOPED:
            continue
        columns = MODEL_COLUMNS.get(resolved)
        if columns is not None and "organization_id" not in columns:
            offenders[route.key] = (
                f"{route!r} — model={declared} resolves to {resolved}, which has no "
                "organization_id column and is not report-scoped"
            )
    _assert_matches_known_defects(offenders, "model= names a model with no organization_id")


def test_every_owner_only_gate_names_a_model_that_has_an_owner():
    """``owner_only=True`` reads ``obj.user_id``; without that column the
    decorator's own code raises 500 rather than enforcing ownership."""
    offenders = {}
    for route, declared, resolved, gate in _model_gates():
        if "owner_only" not in gate["keywords"] or "owner_only=True" not in gate["source"]:
            continue
        # Report-scoped models have no owner of their own by design; ownership
        # means "the parent report is yours", enforced in object_scope.py.
        if resolved in REPORT_SCOPED:
            continue
        columns = MODEL_COLUMNS.get(resolved)
        if columns is not None and "user_id" not in columns:
            offenders[route.key] = (
                f"{route!r} — owner_only=True but {resolved} has no user_id column"
            )
    _assert_matches_known_defects(offenders, "owner_only= on a model with no owner")


def _assert_matches_known_defects(offenders: dict, label: str) -> None:
    """Offenders must be exactly the pinned set — no new ones, no stale ones."""
    new = {k: v for k, v in offenders.items() if k not in KNOWN_DEAD_MODEL_GATES}
    assert not new, (
        f"NEW {label}:\n  " + "\n  ".join(new.values()) +
        "\n\nThis gate looks like protection and enforces nothing. Fix the route; "
        "do not add it to KNOWN_DEAD_MODEL_GATES to go green."
    )


def test_no_stale_entries_in_the_dead_gate_baseline():
    """Fixing one of the pinned defects must be noticed here, not left rotting."""
    # ★This must use the SAME detection as the tests above. It did not, briefly:
    # after the id lookup became model-derived and report-scoped models gained
    # their own path, the checks above reported the visualization gates as
    # healthy while this one still counted them broken — so the pinned entries
    # could never be retired and the baseline would have frozen a fixed defect
    # in place forever. Two definitions of "broken" in one file is the same
    # class of drift the file exists to catch.
    derives = _decorator_derives_id_from_model()
    still_broken = set()
    for route, declared, resolved, gate in _model_gates():
        ids = [p for p in route.path_params if p.endswith("_id")]
        columns = MODEL_COLUMNS.get(resolved)

        reachable = any(p in EXTRACTABLE_ID_PARAMS for p in ids) if ids else True
        if ids and derives and resolved:
            reachable = reachable or f"{_snake(resolved)}_id" in ids

        unscopable = (
            resolved not in REPORT_SCOPED
            and columns is not None
            and "organization_id" not in columns
        )
        if (not reachable) or unscopable:
            still_broken.add(route.key)
    fixed = set(KNOWN_DEAD_MODEL_GATES) - still_broken
    assert not fixed, (
        "These are pinned as broken but now scan clean — delete their entries from "
        f"KNOWN_DEAD_MODEL_GATES: {sorted(fixed)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Every ungated route is declared
# ─────────────────────────────────────────────────────────────────────────────

def test_every_ungated_route_is_declared():
    """A route with no permission decorator must be a decision someone recorded."""
    undeclared = []
    for route in ROUTES:
        if route.gates or (route.deps & GATE_DEPENDENCIES):
            continue
        if route.key in PUBLIC_BY_DESIGN or route.key in AUTHENTICATED_UNGATED_BASELINE:
            continue
        how = "UNAUTHENTICATED" if not (route.deps & AUTHN_DEPENDENCIES) else "authenticated"
        undeclared.append(f"{route!r} [{how}] deps={sorted(route.deps)}")
    assert not undeclared, (
        "Route with no permission gate and no declaration:\n  " + "\n  ".join(undeclared) +
        "\n\nEither gate it, or add it to PUBLIC_BY_DESIGN (with a reason) / "
        "AUTHENTICATED_UNGATED_BASELINE (having read the handler's own check)."
    )


def test_public_by_design_routes_really_are_reachable_without_a_credential():
    """An entry here claims 'no credential needed'. If the route grew an auth
    dependency, the claim is stale and the reason text is now misleading."""
    wrong = [
        f"{route!r} is listed PUBLIC_BY_DESIGN but depends on "
        f"{sorted(route.deps & AUTHN_REQUIRED_DEPENDENCIES)}"
        for route in ROUTES
        if route.key in PUBLIC_BY_DESIGN and (route.deps & AUTHN_REQUIRED_DEPENDENCIES)
    ]
    assert not wrong, "\n  ".join(wrong)


def test_no_stale_declarations():
    """A deleted or renamed route must not leave its allowlist entry behind,
    where it would later silently bless an unrelated new function."""
    live = {r.key for r in ROUTES}
    stale_public = set(PUBLIC_BY_DESIGN) - live
    stale_baseline = AUTHENTICATED_UNGATED_BASELINE - live
    assert not stale_public, f"PUBLIC_BY_DESIGN names routes that no longer exist: {sorted(stale_public)}"
    assert not stale_baseline, f"AUTHENTICATED_UNGATED_BASELINE is stale: {sorted(stale_baseline)}"


def test_routes_that_gained_a_gate_leave_the_baseline():
    gated_but_listed = [
        route.key for route in ROUTES
        if route.gates and (route.key in AUTHENTICATED_UNGATED_BASELINE or route.key in PUBLIC_BY_DESIGN)
    ]
    assert not gated_but_listed, (
        f"Now gated — remove from the ungated declarations: {sorted(gated_but_listed)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Defect 4 — an over-tight gate locks the legitimate owner out
# ─────────────────────────────────────────────────────────────────────────────

ADMIN_PERMISSIONS = {
    "manage_settings", "manage_llm", "manage_members", "manage_identity_providers",
    "view_audit_logs", "manage_service_accounts",
}
MEMBER_OWNED_PREFIXES = (
    "/reports", "/api/reports", "/completions", "/api/completions", "/widgets",
    "/queries", "/steps", "/artifacts", "/projects", "/visualizations",
)


def test_a_members_own_work_is_not_gated_behind_an_admin_permission():
    """``/api/completions/{id}/plans`` was ``manage_settings`` — members could
    not read the plan for a conversation they had just had. Refusal-only
    reasoning never catches this; ask whether the OWNER can still act."""
    offenders = [
        f"{route!r} needs {gate['permission']!r}, which a member does not hold"
        for route in ROUTES for gate in route.gates
        if gate["permission"] in ADMIN_PERMISSIONS
        and any((route.path or "").startswith(p) for p in MEMBER_OWNED_PREFIXES)
    ]
    assert not offenders, (
        "Admin-level permission on a member's own resource:\n  " + "\n  ".join(offenders)
    )


def test_every_gate_names_a_registered_permission():
    """A typo'd permission string is a gate nobody on earth can satisfy."""
    registry = (APP / "core" / "permissions_registry.py").read_text(encoding="utf-8")
    known = set()
    for node in ast.walk(ast.parse(registry)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            known.add(node.value)
    assert len(known) >= 20, "permissions_registry.py did not parse into a permission set"
    known.add("full_admin_access")
    unknown = [
        f"{route!r} requires {gate['permission']!r}, which is not in permissions_registry.py"
        for route in ROUTES for gate in route.gates
        if gate["name"] == "requires_permission" and isinstance(gate["permission"], str)
        and gate["permission"] not in known
    ]
    assert not unknown, "\n  ".join(unknown)


# ─────────────────────────────────────────────────────────────────────────────
# Defect 3 — the public share surfaces must use the canonical visibility check
# ─────────────────────────────────────────────────────────────────────────────

def _public_report_service_functions():
    for name in ("widget_service.py", "text_widget_service.py"):
        path = APP / "services" / name
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                    node.name.endswith("_for_public_report"):
                yield name, node


def test_public_report_surfaces_use_the_canonical_visibility_check():
    """``status == 'published'`` is not a visibility check.

    ``report_service`` sets ``status = 'published' if visibility != 'none'``, so
    it is true for 'internal' AND 'shared'. The canonical
    ``_check_visibility`` raises 401 for both when there is no user.
    """
    offenders = {}
    for module, fn in _public_report_service_functions():
        body = ast.unparse(fn)
        checks_visibility = "artifact_visibility" in body or "_check_visibility" in body
        checks_legacy_status = any(
            isinstance(n, ast.Compare)
            and isinstance(n.left, ast.Attribute) and n.left.attr == "status"
            and any(isinstance(c, ast.Constant) and c.value == "published" for c in n.comparators)
            for n in ast.walk(fn)
        )
        if checks_legacy_status and not checks_visibility:
            offenders[f"{module}:{fn.name}"] = f"services/{module}:{fn.lineno} {fn.name}"
    new = {k: v for k, v in offenders.items() if k not in PUBLIC_SURFACES_ON_THE_LEGACY_VISIBILITY_CHECK}
    assert not new, (
        "Anonymous share surface gated on the legacy status flag, which is set for "
        "'internal' and 'shared' reports too:\n  " + "\n  ".join(new.values())
    )
    fixed = set(PUBLIC_SURFACES_ON_THE_LEGACY_VISIBILITY_CHECK) - set(offenders)
    assert not fixed, (
        "Now uses the real visibility check — delete from "
        f"PUBLIC_SURFACES_ON_THE_LEGACY_VISIBILITY_CHECK: {sorted(fixed)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# ★★★ THE RED PROOF. A guard never seen failing is decoration.
# ─────────────────────────────────────────────────────────────────────────────

_GATE_ABOVE_ROUTER = '''
from fastapi import APIRouter
router = APIRouter()

@requires_permission('create_reports')
@router.post("/api/completions/{completion_id}/sigkill")
async def update_completion_sigkill(completion_id: str):
    return None
'''

_CORRECT_ORDER = '''
from fastapi import APIRouter
router = APIRouter()

@router.post("/api/completions/{completion_id}/sigkill")
@requires_permission('create_reports')
async def update_completion_sigkill(completion_id: str):
    return None
'''

_ALIASED_ROUTER = '''
from fastapi import APIRouter
well_known_router = APIRouter()

@requires_permission('manage_settings')
@well_known_router.get("/.well-known/thing")
async def thing():
    return None
'''


def test_the_scanner_detects_a_gate_above_the_router():
    """Shape taken verbatim from ``routes/completion.py`` before the fix.

    Run against the real pre-fix file this scanner reported 5 hits; against
    HEAD and the working tree, 0.
    """
    bad = scan_source(_GATE_ABOVE_ROUTER, "synthetic")
    assert len(bad) == 1, "scanner did not even find the route"
    assert bad[0].gates[0]["index"] < bad[0].router_index, (
        "scanner failed to flag a permission decorator above the router — "
        "test_no_permission_gate_sits_above_its_router cannot fail, so it proves nothing"
    )

    good = scan_source(_CORRECT_ORDER, "synthetic")
    assert good[0].gates[0]["index"] > good[0].router_index, (
        "scanner flags the CORRECT order as broken — it would fail on everything"
    )


def test_the_scanner_follows_a_router_that_is_not_called_router():
    """Router names come from ``APIRouter()`` bindings, not a naming convention.
    ``ee/scim/routes.py`` and the ``.well-known`` router are not called `router`."""
    found = scan_source(_ALIASED_ROUTER, "synthetic")
    assert len(found) == 1, "a router bound to another name was invisible to the scanner"
    assert found[0].gates[0]["index"] < found[0].router_index


def test_the_scanner_detects_every_bug_shape():
    """One synthetic source per defect class; each must be detected."""
    src = '''
from fastapi import APIRouter
from app.models.visualization import Visualization as VisualizationModel
router = APIRouter()

@router.get("/{visualization_id}")
@requires_permission('view_reports', model=VisualizationModel, owner_only=True)
async def get_visualization(visualization_id: str):
    return None

@router.get("/{report_id}/ok")
@requires_permission('view_reports', model=Report)
async def fine(report_id: str):
    return None

@router.get("/naked/{thing_id}")
async def ungated(thing_id: str):
    return None
'''
    routes = {r.func: r for r in scan_source(src, "synthetic")}
    assert set(routes) == {"get_visualization", "fine", "ungated"}

    # (2) id param the decorator cannot extract
    dead = routes["get_visualization"]
    ids = [p for p in dead.path_params if p.endswith("_id")]
    assert ids == ["visualization_id"]
    assert not any(p in EXTRACTABLE_ID_PARAMS for p in ids), (
        "visualization_id became extractable — re-check KNOWN_DEAD_MODEL_GATES"
    )

    # the same shape on an extractable id must NOT be flagged
    ok = routes["fine"]
    assert any(p in EXTRACTABLE_ID_PARAMS for p in ok.path_params), (
        "report_id must stay extractable, or this scanner flags every working gate"
    )

    # alias resolution: the declared name is not the model name
    aliases = _import_aliases(ast.parse(src))
    assert aliases["VisualizationModel"] == "Visualization"

    # model without organization_id / user_id is detected
    columns = MODEL_COLUMNS["Visualization"]
    assert "organization_id" not in columns and "user_id" not in columns, (
        "Visualization gained the columns its gate needs — delete its "
        "KNOWN_DEAD_MODEL_GATES entries"
    )

    # (no gate at all) is detected
    assert routes["ungated"].gates == []


def test_the_over_tight_gate_check_can_fire():
    """The historical ``/plans`` defect, reconstructed."""
    src = '''
from fastapi import APIRouter
router = APIRouter()

@router.get("/api/completions/{completion_id}/plans")
@requires_permission('manage_settings')
async def get_completion_plans(completion_id: str):
    return None
'''
    route = scan_source(src, "synthetic")[0]
    fired = (route.gates[0]["permission"] in ADMIN_PERMISSIONS
             and any((route.path or "").startswith(p) for p in MEMBER_OWNED_PREFIXES))
    assert fired, "the over-tight-gate check cannot detect the bug it was written for"


@pytest.mark.parametrize("source", [_GATE_ABOVE_ROUTER, _CORRECT_ORDER, _ALIASED_ROUTER])
def test_the_scanner_never_returns_nothing_for_a_real_router(source):
    assert scan_source(source, "synthetic"), "scanner silently found no routes"
