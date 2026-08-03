"""Rebuild the `excel_files` list a saved step's code was written against.

★★★THE BUG THIS EXISTS TO CLOSE.

Codegen hands the model a list and tells it the indices:

    SOURCE FILES — read the data from these, and only these.
      - excel_files[0]: MM Conso Data Report (Jan'25).csv → pd.read_csv(excel_files[0].path)
      - excel_files[1]: MM Conso Data Report (Feb'25).csv → pd.read_csv(excel_files[1].path)

The saved code therefore stores POSITIONS. But that list is turn-scoped: images
stripped, often narrowed to just the files uploaded this turn, and reordered to
whatever the caller named in `source_file_ids`. Re-running a step passed
`report.files` instead — every file ever attached to the report, in no defined
order, growing with each upload.

One report accumulated 19 attachments across four uploads, three of them .docx.
`excel_files[0]` became a Word document:

    'utf-8' codec can't decode byte 0xa3 in position 14: invalid start byte

That byte is the fifteenth byte of a zip container. The loud version. The quiet
version is the dangerous one: had slot 0 landed on a different CSV, the refresh
would have finished green and the dashboard would show the wrong month's
numbers with nothing to indicate it.

★WHY THERE IS NO CLEVER FALLBACK FOR OLD STEPS.

A step written before `source_file_ids` existed has no record of its list, and
the order cannot be reconstructed: it was not creation order, not filename
order, and not the report's attachment order — it was whatever the tool call
named. Sorting the files by *something* and hoping produces exactly the silent
wrong-data case above. So legacy steps keep today's behaviour while the file set
is unambiguous, and REFUSE with an explanation the moment it isn't. A refusal
that names the problem is recoverable; a wrong number is not.
"""

from __future__ import annotations

import logging
import re
from typing import Any, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class StepFileBindingError(RuntimeError):
    """The step's files cannot be resolved to what its code expects.

    Raised instead of letting pandas fail on whatever happened to land at that
    index. The message is shown to whoever pressed Refresh, so it names the
    problem and the fix rather than the byte offset.
    """


# ★This was a second hand-maintained copy of the same eight extensions, with a
# comment saying it "mirrors _source_files" — a convention, not a guard. It now
# asks the one registry instead, so a format the codegen side learns to open (or
# stops opening) cannot leave the refresh guard behind. Membership is
# default-deny: an extension with no reader is proof the binding drifted.
from app.services.file_formats import loadable_in_code

# `excel_files[7]` — the positions a piece of saved code actually reads.
_INDEX_RE = re.compile(r"excel_files\s*\[\s*(\d+)\s*\]")

# Any use at all, including `excel_files[i]` inside a loop and a bare iteration.
_USE_RE = re.compile(r"\bexcel_files\b")

# ★EVERY generated function declares the parameter, whether or not it reads it:
#     def generate_df(ds_clients, excel_files):
# so a bare search for the name matches a pure database query too, and the
# legacy guard would refuse to refresh charts that never touch a file. The def
# line is removed before looking.
_DEF_LINE_RE = re.compile(r"^\s*def\s+\w+\s*\([^)]*\)\s*:", re.MULTILINE)


def indexed_positions(code: str) -> List[int]:
    """Every LITERAL index the code reads out of `excel_files`.

    ★Deliberately does not try to resolve computed indices. The step that
    exposed this bug reads

        for i, label in enumerate(month_labels):
            df = pd.read_csv(excel_files[i].path)

    and returns [] from here — which is exactly why `uses_excel_files` exists
    below and why the legacy check cannot rely on positions alone. The first
    version of this module checked only literal slots, passed this step
    cleanly, and let the .docx through.
    """
    return sorted({int(m.group(1)) for m in _INDEX_RE.finditer(code or "")})


def uses_excel_files(code: str) -> bool:
    """Whether the code READS the uploaded-file list, in any form.

    Declaring the parameter does not count — every generated function declares
    it. Only a reference in the body does.
    """
    body = _DEF_LINE_RE.sub("", code or "")
    return bool(_USE_RE.search(body))


def _extension(filename: str) -> str:
    name = filename or ""
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


async def resolve_step_excel_files(
    db: AsyncSession,
    step: Any,
    report: Any,
) -> List[Any]:
    """The list to hand the executor as `excel_files` for this step.

    Recorded binding → exactly those files, in the recorded order.
    No recorded binding → the report's files, but only while that is safe.
    """
    recorded = list(getattr(step, "source_file_ids", None) or [])
    report_files = list(getattr(report, "files", None) or [])

    if not recorded:
        return _legacy_files(step, report_files)

    by_id = {str(getattr(f, "id", "")): f for f in report_files}

    # A file can be detached from the report after the step was written, so the
    # report's own list is not sufficient to resolve every recorded id. Fetch the
    # stragglers directly — a detached file is still the file the code read, and
    # refusing to refresh over a bookkeeping change would be its own bug.
    missing_ids = [str(i) for i in recorded if str(i) not in by_id]
    if missing_ids:
        from app.models.file import File
        rows = (await db.execute(select(File).where(File.id.in_(missing_ids)))).scalars().all()
        for f in rows:
            by_id[str(f.id)] = f

    resolved: List[Any] = []
    gone: List[str] = []
    for fid in recorded:
        f = by_id.get(str(fid))
        if f is None:
            gone.append(str(fid))
        else:
            resolved.append(f)

    if gone:
        # Dropping the missing entries would shift every later index by one —
        # the exact failure this module exists to prevent, reintroduced as a
        # "graceful" fallback.
        raise StepFileBindingError(
            f"{len(gone)} of the {len(recorded)} files this chart was built from "
            "no longer exist, so its data cannot be rebuilt. Re-upload them, or "
            "ask the agent to rebuild this chart from the files you have now."
        )

    return resolved


def _legacy_files(step: Any, report_files: List[Any]) -> List[Any]:
    """Files for a step written before the binding was recorded.

    Behaviour is unchanged while the report's file set is unambiguous, so
    reports whose files never changed keep refreshing exactly as before. When
    the set has drifted in a way that provably breaks positional indexing, this
    refuses rather than returning a plausible wrong answer.
    """
    code = getattr(step, "code", "") or ""
    if not uses_excel_files(code):
        # The code reads no uploaded files at all — a pure database query. There
        # is no positional binding to protect and nothing to check.
        return report_files

    positions = indexed_positions(code)

    if positions and positions[-1] >= len(report_files):
        raise StepFileBindingError(
            f"This chart reads file #{positions[-1] + 1} of its source list, but "
            f"the report now has {len(report_files)} file(s). The list it was "
            "built against no longer exists. Ask the agent to rebuild this chart."
        )

    # Duplicate filenames mean the same file was uploaded more than once. Each
    # upload appended a NEW row, so every index past the first duplicate points
    # at a different file than it did when the code was written.
    names = [str(getattr(f, "filename", "") or "") for f in report_files]
    duplicated = sorted({n for n in names if n and names.count(n) > 1})

    # A slot that resolves to a format no generated reader can open is proof on
    # its own: codegen is told in the prompt never to read these from code, so
    # it cannot have been the file at that index originally.
    #
    # ★When the indices are computed at runtime (`excel_files[i]` in a loop) the
    # slots are unknowable, so EVERY entry is a slot the code might read. That
    # is not conservatism for its own sake — it is the case that actually
    # occurred, and checking only literal slots let a .docx straight through.
    checked = positions if positions else range(len(names))
    unreadable = sorted({
        names[i] for i in checked
        if i < len(names) and not loadable_in_code(_extension(names[i]))
    })

    if duplicated or unreadable:
        detail = []
        if unreadable:
            detail.append(
                f"its file list now includes {', '.join(unreadable[:3])}"
                + ("…" if len(unreadable) > 3 else "")
                + ", which is not a data file"
            )
        if duplicated:
            detail.append(
                f"{len(duplicated)} file name(s) are attached more than once "
                f"({', '.join(duplicated[:3])}{'…' if len(duplicated) > 3 else ''})"
            )
        raise StepFileBindingError(
            "This chart was built against a different set of files than the "
            "report holds now — " + "; and ".join(detail) + ". Refreshing it "
            "would read the wrong files, so it has been stopped. Ask the agent "
            "to rebuild this chart from the current files."
        )

    return report_files


def record_source_files(step: Any, excel_files: Optional[List[Any]]) -> None:
    """Store the ordered ids of the list a step's code was generated against.

    Called at the moment the code is persisted, from the same scope that ran it,
    so what is recorded is what actually executed — not a list rebuilt later
    from the report, which is the assumption that failed in the first place.
    """
    ids = [str(getattr(f, "id", "")) for f in (excel_files or []) if getattr(f, "id", None)]
    step.source_file_ids = ids or None
