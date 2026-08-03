"""Bind a tool call to the files it is meant to read.

`create_data` and `write_csv` both generate code against an `excel_files` list.
Without an explicit binding the generated code has to work out *which* attached
file the prompt meant, from a one-line index entry — and when the file it wants
isn't there at all (a tool result that never got materialized, say) the model
goes looking for the data elsewhere, which is how generated code ends up trying
to call a connection directly.

`source_file_ids` closes that gap: the caller names the files, this module
resolves them, and the codegen prompt is told the path, the reader and the
column shape for each one.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ★Both of these used to be defined here — a reader table, and a hand-listed set
# of formats to block. The block list is gone: `loadable_in_code` is membership
# in the reader table, so a format with no reader is refused without anyone
# having had to think of it in advance. See `app/services/file_formats.py` for
# what that inversion was measured to be worth.
from app.services.file_formats import (  # noqa: E402
    CODEGEN_READERS as _READERS,
    loadable_in_code,
    refusal_for,
    refused_in_code,
)


def _json_reader(index: int, tabular_path: str, shape_known: bool = True) -> str:
    """The reader for a JSON artifact, given where the records sit.

    A bare `pd.read_json` only works when the file IS a list of records. The
    common MCP shape is an envelope — `{"WorkOrdersMFG": [...], "warnings":
    [...]}` — where read_json raises `All arrays must be of the same length`.
    So there are three cases, and conflating the last two costs an attempt:

    * we know the records are nested → hand over the two-step form,
    * we know the file is a bare array → the plain reader is right,
    * we never inspected the file (too large to parse) → we must NOT imply it
      is a bare array. Give the discovery form instead.
    """
    if tabular_path:
        lookup = "".join(f"[{part!r}]" for part in tabular_path.split("."))
        return (
            f"payload = pd.read_json(excel_files[{index}].path, typ='series').to_dict(); "
            f"df = pd.json_normalize(payload{lookup})"
        )
    if shape_known:
        return _READERS["json"].format(i=index)
    return (
        f"payload = pd.read_json(excel_files[{index}].path, typ='series').to_dict(); "
        "print(list(payload.keys()))  # find the key holding the records, then: "
        "df = pd.json_normalize(payload['<that key>'])  "
        "— do NOT call pd.read_json() on its own here, it raises "
        "'All arrays must be of the same length' on an enveloped payload"
    )


def _extension(filename: str) -> str:
    return (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""


def _candidates(runtime_ctx: Dict[str, Any]) -> List[Any]:
    """Every file this run could read, in codegen order.

    ★Delegates to `file_scope`. The version this replaces read `excel_files`
    plus `report.files` and stopped there, so a file inherited from the
    report's PROJECT — advertised to the model in the `<files>` catalog with
    its real id, and openable by `read_file` — resolved to nothing here. Every
    one of the three tools bound through this function then answered "None of
    the requested source files exist" for a file the model had just been shown,
    and each retry cost a fresh code-generation round.
    """
    from app.services.file_scope import PURPOSE_CODEGEN, readable_files_from_ctx

    return readable_files_from_ctx(runtime_ctx, purpose=PURPOSE_CODEGEN)


def resolve_source_files(
    runtime_ctx: Dict[str, Any],
    source_file_ids: Optional[List[str]],
) -> Tuple[Optional[List[Any]], str, List[str]]:
    """Resolve `source_file_ids` to file objects plus a codegen directive.

    Returns ``(scoped_files, directive, missing_ids)``:

    * ``scoped_files`` — what to hand the executor as `excel_files`, in the
      order the caller listed them, so `excel_files[0]` is unambiguous. None
      when no ids were given (leave the caller's own list alone).
    * ``directive`` — prose appended to the codegen prompt naming each file's
      path and reader. Empty when nothing resolved.
    * ``missing_ids`` — ids that matched no file. The caller surfaces these
      rather than generating code against a file that isn't there.
    """
    if not source_file_ids:
        return None, "", []

    by_id = {str(getattr(f, "id", "")): f for f in _candidates(runtime_ctx)}
    scoped: List[Any] = []
    missing: List[str] = []
    for fid in source_file_ids:
        found = by_id.get(str(fid))
        if found is None:
            missing.append(str(fid))
        elif found not in scoped:
            scoped.append(found)

    if not scoped:
        return [], "", missing

    lines = [
        "",
        "SOURCE FILES — read the data from these, and only these. They are "
        "already in `excel_files` at the indices below; do not search for "
        "another file and do not fetch the data from anywhere else.",
    ]
    for i, f in enumerate(scoped):
        name = getattr(f, "filename", "") or ""
        ext = _extension(name)
        hint = _observation_hint(runtime_ctx, f)
        line = f"  - excel_files[{i}]: {name} (path: {getattr(f, 'path', '')})"
        if refused_in_code(ext):
            line += " → " + refusal_for(ext, str(getattr(f, "id", "") or ""))
        elif not loadable_in_code(ext):
            # No extension at all — unknown, not unreadable. Say nothing rather
            # than either naming a reader we cannot justify or refusing a file
            # that is very often a CSV without a dot in its name.
            pass
        elif ext == "json":
            line += " → read with " + _json_reader(
                i,
                hint.get("tabular_path", ""),
                shape_known=bool(hint) and not hint.get("parse_skipped"),
            )
        else:
            # ★No trailing `elif ... in _READERS` and no silent fall-through.
            # The chain used to end on a membership test, so an extension in
            # neither set produced a line naming the file and saying nothing
            # about how to open it — and `pd.read_csv` is what a model guesses
            # from a filename. `sample.rtf` came back as a 157-row frame of
            # control words. Every branch now ends in an instruction.
            line += f" → read with {_READERS[ext].format(i=i)}"
        lines.append(line)
        for extra in _describe(hint):
            lines.append(f"      {extra}")
    if missing:
        lines.append(
            f"  - NOTE: no file matched id(s) {', '.join(missing)} — do not "
            "invent a substitute; work with the files listed above."
        )
    return scoped, "\n".join(lines), missing


#: How many reachable files to name in a failure. Enough to recognise the one
#: that was meant, short enough that the error stays readable.
_MAX_LISTED = 10


def unresolved_files_error(
    runtime_ctx: Dict[str, Any],
    missing_ids: List[str],
    *,
    tool: str,
) -> str:
    """The message for "you asked for a file that isn't here".

    ★It used to name only what failed — the ids — and stop. The model's next
    move was therefore a guess, and every guess is a fresh code-generation
    round: the reported run burned three of them before stumbling onto a
    different tool by filename. It already knew the file existed; it had read
    the id out of its own context a moment earlier. What it was never told is
    what it could have asked for instead.

    Deliberately NOT a fallback. Substituting a neighbouring file when the
    named one is missing is the positional-binding failure — generated code
    reads the wrong month and reports a confident wrong number. A refusal that
    names the alternatives can be acted on. A wrong number cannot.
    """
    named = ", ".join(missing_ids) if missing_ids else "the requested id(s)"
    lines = [f"{tool}: no file matched {named}."]

    available = _candidates(runtime_ctx)
    if not available:
        lines.append(
            "This run has no readable files at all — attach one, or query a "
            "connected data source instead."
        )
        return " ".join(lines)

    shown = available[:_MAX_LISTED]
    entries = []
    for f in shown:
        fid = str(getattr(f, "id", "") or "")
        name = getattr(f, "filename", "") or fid
        entry = f"{name} (file_id={fid})"
        if refused_in_code(_extension(name)):
            # Naming the right call matters more here than anywhere: pointing
            # pd.read_csv at a Word document produces either an error or, worse,
            # a plausible-looking frame of nonsense.
            # The trailing period is stripped because these entries are joined
            # with "; " and the sentence closes with its own "." below.
            entry += " — " + refusal_for(_extension(name), fid).rstrip(".")
        entries.append(entry)

    lines.append("Reachable from this run: " + "; ".join(entries) + ".")
    if len(available) > _MAX_LISTED:
        lines.append(f"({len(available) - _MAX_LISTED} more not listed.)")
    lines.append("Use one of these ids, or call list_files to look further.")
    return " ".join(lines)


def _observation_hint(runtime_ctx: Dict[str, Any], file_obj: Any) -> Dict[str, Any]:
    """What the tool that produced this file said about it.

    execute_mcp records `tabular_path`, `candidate_paths` and `record_shape`
    alongside the artifact. Threading them here is what lets the directive name
    the exact key the records live under and the columns they carry, instead of
    leaving the model to rediscover both from a three-row preview.
    """
    try:
        hub = runtime_ctx.get("context_hub")
        observations = getattr(getattr(hub, "observation_builder", None), "tool_observations", None) or []
        for obs in reversed(observations):
            data = obs if isinstance(obs, dict) else getattr(obs, "__dict__", {})
            if str(data.get("file_id") or "") != str(getattr(file_obj, "id", "")):
                continue
            return {
                "tabular_path": data.get("tabular_path") or "",
                "candidate_paths": data.get("candidate_paths") or [],
                "record_shape": data.get("record_shape") or {},
                # True when the producer never inspected the payload's shape,
                # so absence of a tabular_path means "unknown", not "flat".
                "parse_skipped": bool(data.get("parse_skipped")),
            }
    except Exception as e:  # never block codegen on a missing hint
        logger.debug(f"source file hint unavailable: {e}")
    return {}


def _describe(hint: Dict[str, Any]) -> List[str]:
    """Human-readable notes for one source file, from its observation hint."""
    out: List[str] = []
    shape = hint.get("record_shape") or {}
    columns = shape.get("columns") or {}
    if columns:
        named = ", ".join(f"{k}:{v}" for k, v in list(columns.items())[:25])
        more = " …" if shape.get("columns_truncated") else ""
        out.append(f"columns ({shape.get('row_count', '?')} records): {named}{more}")
    if hint.get("tabular_path"):
        out.append(
            f"the records are under the key '{hint['tabular_path']}' — everything "
            "else in the file is envelope metadata, not rows."
        )
    if hint.get("parse_skipped"):
        out.append(
            "this file was too large to inspect — its structure is UNKNOWN. "
            "Print the top-level keys first, then normalize the one holding records."
        )
    if hint.get("candidate_paths"):
        out.append(
            "other record lists in the same file (ignore unless asked): "
            + ", ".join(hint["candidate_paths"])
        )
    return out
