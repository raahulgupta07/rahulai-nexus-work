"""One answer to the question "which files can this run read?".

Five places used to answer it independently — the `<files>` context catalog,
`agent_v2.analysis_files`, `read_file`'s session resolver, `grep_files`, and
`_source_files._candidates` for the codegen tools. Each applied a different
subset of the same rules, and the catalog was the most permissive of the five.
Because the catalog is what the MODEL reads, every disagreement was a file the
model was told about and a tool could not reach.

★That is not a bug that happened once, it is a shape that guarantees it. It
fired twice: a project-inherited file was resolvable by `read_file` and by
nothing else, so `inspect_data` / `create_data` / `write_csv` answered "None of
the requested source files exist" for a file whose id the model had just read
out of its own context — three retries, each a fresh code-generation round. The
same blindness sat in `grep_files`, unnoticed, because nobody had greped a
project file yet.

So membership is decided here, once. `purpose` changes ordering and rendering,
never who is in the set — see `readable_files`. The invariant is enforced by
`test_no_file_is_shown_that_cannot_be_read`: anything the catalog renders must
resolve in every tool resolver. Add a sixth pool to one call site and that test
fails before it ships.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional

logger = logging.getLogger(__name__)


def _files_without_io(ds: Any) -> list:
    """``ds.files``, but never at the cost of a database round trip.

    ★★★This function is synchronous and is called from an async request. Reading
    a relationship that is not currently loaded makes SQLAlchemy go and fetch
    it, and doing that outside a greenlet raises

        MissingGreenlet: greenlet_spawn has not been called; can't call
        await_only() here. Was IO attempted in an unexpected place?

    ``DataSource.files`` is declared ``lazy="selectin"``, so it is normally
    loaded with its parent and this never fires — which is exactly why it
    survived. But a commit EXPIRES loaded attributes, and the next read of an
    expired attribute is a fresh load. So any caller that commits between
    fetching its data sources and resolving scope hits it. Measured on dev:
    the whole of ``_resolve_scope`` aborted, and the turn ran with no file
    scoping at all.

    ★``getattr(ds, "files", None)`` does NOT protect against this. The default
    only applies when the attribute is missing, and it is not missing — it is
    unloaded, which is a different thing that looks identical at the call site.

    When the relationship genuinely is not loaded we say so and return nothing,
    rather than raising. The consequence is stated rather than hidden: files
    belonging to a bound agent are then counted as uploads for this turn. That
    is a smaller, visible inaccuracy than losing scope resolution entirely,
    which is what happened before.
    """
    try:
        from sqlalchemy import inspect as _sa_inspect

        state = _sa_inspect(ds)
        if "files" in state.unloaded:
            # ★★★`state.identity`, NOT `ds.id`. A commit expires EVERY
            # attribute, the primary key included, so reading `ds.id` here
            # triggers exactly the load this function exists to avoid — and
            # the warning about the problem becomes the problem. Measured:
            # the guard fired correctly and then raised MissingGreenlet from
            # its own log line. `state.identity` is the key SQLAlchemy already
            # holds and costs no IO.
            ident = state.identity[0] if state.identity else "<pending>"
            logger.warning(
                "file scope: data source %s has unloaded 'files'; treating its "
                "files as uploads for this turn. Eager-load the relationship "
                "(or re-fetch after the commit that expired it) to avoid this.",
                ident,
            )
            return []
    except Exception:
        # Not an ORM instance (a stub in tests, a plain object) — nothing to
        # expire, so the plain read below is safe.
        pass
    return list(getattr(ds, "files", None) or [])

#: Rendered as the model-facing `<files>` catalog.
PURPOSE_CATALOG = "catalog"
#: Resolving one id for `read_file` / `grep_files`.
PURPOSE_READ = "read"
#: Handed to generated code as `excel_files`, so ORDER is load-bearing.
PURPOSE_CODEGEN = "codegen"

PURPOSES = (PURPOSE_CATALOG, PURPOSE_READ, PURPOSE_CODEGEN)


def _fid(f: Any) -> str:
    return str(getattr(f, "id", "") or "")


def _dedupe(pools: Iterable[Iterable[Any]]) -> List[Any]:
    """Concatenate pools, first occurrence of an id wins.

    ★The `seen` set is updated as we go. The version this replaces built it
    once from the first pool and never added to it, so a file listed twice in
    `report.files` was appended twice — and report attachments are append-only
    with no dedup, so a working report reaches nineteen rows for six files.
    """
    out: List[Any] = []
    seen: set = set()
    for pool in pools:
        for f in pool or []:
            key = _fid(f)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            out.append(f)
    return out


def readable_files(
    *,
    report: Any = None,
    project_files: Optional[Iterable[Any]] = None,
    excel_files: Optional[Iterable[Any]] = None,
    data_sources: Optional[Iterable[Any]] = None,
    purpose: str = PURPOSE_READ,
    upload_focus: bool = True,
) -> List[Any]:
    """Every file this run may read, in the order this `purpose` needs them.

    Membership is the union of three pools and is identical for every purpose:

    * ``report.files`` — attached to this conversation,
    * ``project_files`` — inherited live from the report's folder. They live in
      `project_file_association`, a different table from
      `report_file_association`, so they are NEVER in ``report.files``,
    * ``excel_files`` — materialized earlier in this same turn (execute_mcp
      appends to it), so likewise never in ``report.files``.

    Two filters then apply, to every purpose:

    * files whose data was materialized into a queryable table are dropped —
      the agent queries the table, and reading both risks a stale second copy,
    * upload focus — when the user attached their OWN files, bound agents'
      inherited knowledge files step aside. See `scope_files_to_user_uploads`.

    ``purpose`` decides ORDER only. ``PURPOSE_CODEGEN`` puts ``excel_files``
    first because generated code indexes into that list positionally, and an
    index only means anything against the list the code generator saw.
    """
    if purpose not in PURPOSES:
        raise ValueError(f"Unknown purpose {purpose!r}; expected one of {PURPOSES}.")

    report_files = list(getattr(report, "files", None) or [])
    project = list(project_files or [])
    live = list(excel_files or [])

    if purpose == PURPOSE_CODEGEN:
        files = _dedupe((live, report_files, project))
    else:
        files = _dedupe((report_files, project, live))

    # Materialized-into-a-table files are a duplicate of something queryable.
    files = [f for f in files if getattr(f, "is_agent_readable", True)]

    if upload_focus:
        from app.services.file_service import scope_files_to_user_uploads

        files = scope_files_to_user_uploads(
            files,
            data_sources if data_sources is not None else getattr(report, "data_sources", None),
            enabled=True,
        )
    return files


def readable_files_from_ctx(runtime_ctx: dict, purpose: str = PURPOSE_READ) -> List[Any]:
    """`readable_files` for a tool, reading the pools off ``runtime_ctx``.

    The agent loop stages the project pool under its own key — ``project_files``
    — rather than merging it into ``excel_files``. Every tool that forgot to
    look there is how this module came to exist.
    """
    report = runtime_ctx.get("report")
    return readable_files(
        report=report,
        project_files=runtime_ctx.get("project_files"),
        excel_files=runtime_ctx.get("excel_files"),
        data_sources=getattr(report, "data_sources", None),
        purpose=purpose,
        upload_focus=_upload_focus_enabled(),
    )


#: The question was about the file(s) attached with this message.
SCOPE_ATTACHED = "attached"
#: The report lives in a folder and the folder holds files.
SCOPE_FOLDER = "folder"
#: Files uploaded to this report on an earlier turn — no longer "attached with
#: this message", but still the material the conversation is about.
SCOPE_UPLOADS = "uploads"
#: No file scope — the bound data agents and their tables.
SCOPE_AGENTS = "agents"
#: Everything reachable, file scope and schemas together.
SCOPE_ALL = "all"

SCOPES = (SCOPE_ATTACHED, SCOPE_FOLDER, SCOPE_UPLOADS, SCOPE_AGENTS, SCOPE_ALL)

#: Narrowest first. `decide_scope` walks this and takes the first that has files.
#:
#: ★``SCOPE_UPLOADS`` sits LAST deliberately. It exists to keep a case that the
#: old inline suppression in ``agent_v2.__init__`` already handled — a report
#: whose own uploads are the subject on turn two and after — and putting it
#: ahead of the folder would silently re-rank folder runs that are known to be
#: answering correctly today.
_PRECEDENCE = (SCOPE_ATTACHED, SCOPE_FOLDER, SCOPE_UPLOADS)


@dataclass(frozen=True)
class ScopeDecision:
    """What this turn will read, and the words for saying so.

    ``label`` is rendered twice — on the composer chip before the question is
    asked, and under the answer afterwards. A scope that is only used and never
    stated is how a report sitting in a folder came to answer from a database
    with nothing on screen to show it had.

    ★There is deliberately no ``suppress_schemas`` here any more. It used to
    mean "a file scope is in force, so empty the bound sources", and that put
    TWO owners on the question of what a run can reach: this, at the top of the
    run, and 503's focus-follow-use, which rebuilds ``clients`` from the
    context agents INSIDE the planner loop. The second one ran later, so the
    suppression it recorded was already untrue by the time the planner acted —
    a scope that says a source is unreachable while the source is reachable is
    worse than no scope at all. Scope now decides the SUBJECT and says so in
    words (`scope_notice`); nothing is taken away.
    """

    kind: str
    files: List[Any]
    label: str

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "file_count": len(self.files),
        }


def decide_scope(
    *,
    report: Any = None,
    project_files: Optional[Iterable[Any]] = None,
    excel_files: Optional[Iterable[Any]] = None,
    attached_file_ids: Optional[Iterable[str]] = None,
    data_sources: Optional[Iterable[Any]] = None,
    project_name: Optional[str] = None,
    override: Optional[str] = None,
) -> ScopeDecision:
    """Decide what a turn reads: attached files, else the folder, else the agents.

    ★The folder rung did not exist. A report in a project had its folder's files
    rendered into the model's catalog and into no readable pool at all, so a
    question about the folder was answered from whatever databases happened to
    be bound — confidently, and about the wrong subject. Reproduced on a stock
    instance: nought files on the report, seven in the folder, two Postgres
    sources, and an answer about sales.

    Narrowest wins. ``override`` is the user's own choice from the composer chip
    and beats the precedence entirely, including choosing to widen back out.
    Nothing is ever made unreachable — out of scope means "not the default", and
    naming a source in the question still switches to it.
    """
    pool = readable_files(
        report=report,
        project_files=project_files,
        excel_files=excel_files,
        data_sources=data_sources,
        purpose=PURPOSE_CODEGEN,
    )
    by_id = {_fid(f): f for f in pool}

    attached = [by_id[str(i)] for i in (attached_file_ids or []) if str(i) in by_id]
    folder = [f for f in (project_files or []) if _fid(f) in by_id]
    # A file that belongs to a bound agent is part of that agent's tables, not
    # an upload. Everything else in the pool was put there by a person.
    of_sources = {
        str(getattr(f, "id", ""))
        for ds in (data_sources or [])
        for f in _files_without_io(ds)
        if getattr(f, "id", None) is not None
    }
    uploads = [f for f in pool if _fid(f) not in of_sources]

    available = {
        SCOPE_ATTACHED: attached,
        SCOPE_FOLDER: folder,
        SCOPE_UPLOADS: uploads,
    }

    def _decide(kind, files):
        return ScopeDecision(kind, files, _label(kind, files, pool, folder, project_name))

    if override in (SCOPE_ATTACHED, SCOPE_FOLDER):
        chosen = override if available[override] else None
    elif override == SCOPE_AGENTS:
        return _decide(SCOPE_AGENTS, [])
    elif override == SCOPE_ALL:
        return _decide(SCOPE_ALL, pool)
    else:
        chosen = None

    if chosen is None:
        chosen = next((k for k in _PRECEDENCE if available[k]), None)

    if chosen is None:
        # No files anywhere. The bound agents are the only material there is,
        # and that is the correct answer rather than a fallback.
        return _decide(SCOPE_AGENTS, [])

    return _decide(chosen, available[chosen])


def scope_notice(scope: Optional[ScopeDecision]) -> str:
    """One line telling the planner what the question is ABOUT.

    ★This replaces emptying ``data_sources``. Removing the sources was an
    attempt to make the planner use the files by leaving it nothing else, and
    it failed twice over: 503's focus-follow-use put them back mid-loop, and
    when it did not, a question that genuinely needed a database ("how does
    this month compare with what is in the warehouse?") had no way to answer
    it and no way to say why.

    Naming the subject does the same job honestly. The files are the default;
    the connected data is still there for a question that actually calls for
    it; and whichever it uses, 503 records what was used.
    """
    if scope is None or scope.kind not in _PRECEDENCE or not scope.files:
        return ""
    names = ", ".join(_fname(f) for f in scope.files[:_NOTICE_NAMES])
    more = len(scope.files) - _NOTICE_NAMES
    if more > 0:
        names = f"{names}, and {more} more"
    subject = {
        SCOPE_ATTACHED: "the attached files",
        SCOPE_FOLDER: "this folder",
        SCOPE_UPLOADS: "the files uploaded to this report",
    }[scope.kind]
    return (
        f"SUBJECT OF THIS QUESTION: {subject} — {names}. Answer from these "
        "unless the question asks for something they do not contain. Connected "
        "data sources remain available; use one only when the question needs "
        "it, and say so in your answer when you do."
    )


#: How many filenames the notice spells out before it starts counting.
_NOTICE_NAMES = 8


def _fname(f: Any) -> str:
    for attr in ("filename", "file_name", "name"):
        value = getattr(f, attr, None)
        if value:
            return str(value)
    return str(_fid(f) or "an unnamed file")


def _label(
    kind: str,
    files: List[Any],
    pool: List[Any],
    folder: List[Any],
    project_name: Optional[str],
) -> str:
    """The one line repeated under the answer — for the WHOLE reachable set.

    ★It used to describe only the winning rung, and that made it wrong in the
    commonest mixed case. Attach one CSV to a report sitting in a seven-file
    folder and it read "Reading: 1 attached file" — while the tools could, and
    did, open all eight. `readable_files` is a union; the rung decides what
    comes FIRST, not what exists. A footer that under-reports what was read is
    the same class of defect as an answer that under-reports what it missed:
    plausible, specific, and not true.
    """
    count = len(files)
    plural = "file" if count == 1 else "files"

    if kind == SCOPE_ALL:
        return f"Reading: everything · {count} {plural} and connected data"
    if kind == SCOPE_AGENTS:
        return "Reading: connected data"

    if kind == SCOPE_ATTACHED:
        base = _fname(files[0]) if count == 1 else f"{count} attached files"
    elif kind == SCOPE_FOLDER:
        named = f'folder "{project_name}"' if project_name else "this folder"
        base = f"{named} · {count} {plural}"
    else:
        base = f"{count} uploaded {plural}"

    return f"Reading: {base}{_also_readable(files, pool, folder, project_name)}"


def _also_readable(
    files: List[Any],
    pool: List[Any],
    folder: List[Any],
    project_name: Optional[str],
) -> str:
    """What else this turn can open, beyond the rung that won."""
    chosen = {_fid(f) for f in files}
    extra = [f for f in pool if _fid(f) not in chosen]
    if not extra:
        return ""
    word = "file" if len(extra) == 1 else "files"
    folder_ids = {_fid(f) for f in folder}
    if folder_ids and all(_fid(f) in folder_ids for f in extra):
        named = f'folder "{project_name}"' if project_name else "this folder"
        return f", plus {len(extra)} {word} in {named}"
    return f", plus {len(extra)} more readable {word}"


def _upload_focus_enabled() -> bool:
    """The `scope_chat_uploads_to_report` flag, defaulting ON.

    Read here so no call site has to remember it. Never raises: a settings
    import failure must not decide which files an analysis can see.
    """
    try:
        from app.settings.config import settings

        return bool(getattr(settings, "scope_chat_uploads_to_report", True))
    except Exception as err:  # pragma: no cover - settings always import in app
        logger.warning("file_scope: could not read upload-focus flag (%s); leaving it on", err)
        return True
