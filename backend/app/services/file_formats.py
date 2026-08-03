"""One answer to the question "can generated code open this file?".

Three modules used to answer it, each with its own hand-maintained copy of the
same eight extensions:

    _source_files.py:40   _NOT_LOADABLE
    step_files.py:62      _NOT_LOADABLE       "mirrors _source_files"
    coder.py:284          _CODEGEN_UNREADABLE_EXTS

They agreed. Nothing made them agree — no test asserted it, and two of the three
carried a comment saying they mirrored a set in another file, which is a
convention rather than a guard. That is the same shape `file_scope.py` was
written to remove for "which files can this run read", and it failed the same
way here.

★But the copies were the smaller half of the problem. All three were
**block**-lists, so an extension nobody had thought about defaulted to
*loadable*, and the model was handed a bare filename with no reader named. It
guessed `pd.read_csv`. Measured 2026-08-03 against real files (see
FILE-SUPPORT-MATRIX.md):

    sample.rtf  -> FRAME 157x1     157 rows of RTF control words, as data
    sample.eml  -> FRAME 6x1       mail headers, as data
    sample.yaml -> FRAME 4x1
    sample.html -> FRAME 0x1
    sample.xml  -> FRAME 0x1

No exception, no warning — a confident wrong answer, which is strictly worse
than the crash the guard was built to prevent. Eight more formats raised
instead, which is the lucky outcome.

So membership is decided here, once, and it is decided the other way round:
**a format is loadable only if this module names a reader for it.** Adding a
format means adding a reader. Forgetting to block one is no longer possible,
because nothing is blocked by name.
"""
from __future__ import annotations

from typing import Optional

# Every reader generated code is allowed to use, keyed by extension. This set IS
# the allow-list — `loadable_in_code` is membership in it, so a format with no
# entry here is refused by construction rather than by remembering to list it
# somewhere else.
#
# `{i}` is the `excel_files` index, formatted in by the caller. `read_text` is a
# helper the execution sandbox provides.
CODEGEN_READERS = {
    "csv": "pd.read_csv(excel_files[{i}].path)",
    "tsv": "pd.read_csv(excel_files[{i}].path, sep='\\t')",
    "json": "pd.read_json(excel_files[{i}].path)",
    "ndjson": "pd.read_json(excel_files[{i}].path, lines=True)",
    "jsonl": "pd.read_json(excel_files[{i}].path, lines=True)",
    "xlsx": "pd.read_excel(excel_files[{i}].path, sheet_name=0)",
    "xls": "pd.read_excel(excel_files[{i}].path, sheet_name=0)",
    "parquet": "pd.read_parquet(excel_files[{i}].path)",
    "txt": "read_text(excel_files[{i}])",
    "log": "read_text(excel_files[{i}])",
    "md": "read_text(excel_files[{i}])",
    # ★These five are the formats that used to come back as a frame of nonsense.
    # They are plain text, so `read_text` is both honest and safe: it cannot
    # invent rows the way `pd.read_csv` did. A caller that wants the tables out
    # of an HTML file can still parse the returned string — what it can no
    # longer do is receive markup shaped like data and not notice.
    "html": "read_text(excel_files[{i}])",
    "htm": "read_text(excel_files[{i}])",
    "xml": "read_text(excel_files[{i}])",
    "yaml": "read_text(excel_files[{i}])",
    "yml": "read_text(excel_files[{i}])",
    "eml": "read_text(excel_files[{i}])",
}


def _document_exts() -> frozenset:
    """Formats `read_file` can turn into something the model can use.

    Derived from the registries that actually do the work rather than restated:
    `DOC_EXTS` is what has a text extractor, `CONVERTIBLE_EXTS` is what
    LibreOffice can render for vision. Deriving is the point — a format added to
    either one is a format this module immediately knows to redirect, with no
    second list to update.
    """
    from app.data_sources.clients._document_text import DOC_EXTS
    from app.data_sources.clients._office_convert import CONVERTIBLE_EXTS

    return frozenset(DOC_EXTS) | frozenset(CONVERTIBLE_EXTS)


# Pictures a vision model can be handed directly. `_file_tool_common` imports
# this rather than keeping its own copy, so the render side and the refusal side
# cannot disagree about what an image is.
IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "tif"})


# ---------------------------------------------------------------- connectors
#
# ★Four file connectors each kept their own `TEXT_EXTS`, and no two agreed.
# Measured 2026-08-03:
#
#     .ndjson / .jsonl   content from S3, opaque from a network directory
#     .xml / .py / .sql  content from S3 and network dirs, opaque from Drive
#
# Nothing about a file's bytes justifies any of that — the differences were four
# independently maintained lists, not four different capabilities. One list now.
CONNECTOR_TEXT_EXTS = frozenset({
    "txt", "md", "log",
    "json", "ndjson", "jsonl",
    "html", "htm", "xml",
    "yaml", "yml",
    "py", "sql",
})

# Formats a connector reads as a table rather than as characters.
CONNECTOR_TABULAR_EXTS = frozenset({"csv", "tsv", "xlsx", "xls"})


def extension(name: Optional[str]) -> str:
    """Lowercased extension of a filename or path, or "" when there is none."""
    leaf = str(name or "").rsplit("/", 1)[-1]
    return leaf.rsplit(".", 1)[-1].lower() if "." in leaf else ""


def loadable_in_code(ext: Optional[str]) -> bool:
    """Whether generated code has a reader for this extension.

    ★Default-deny. An unrecognised extension is NOT loadable, which is the whole
    inversion this module exists for.
    """
    return (ext or "").lstrip(".").lower() in CODEGEN_READERS


def refused_in_code(ext: Optional[str]) -> bool:
    """Whether to actively refuse this file, as opposed to merely having no
    reader for it.

    ★These are not the same question, and conflating them costs real work. A
    file with **no extension at all** is unknown, not unreadable — `dataset`
    with no dot is very often a CSV, and refusing it on the strength of a
    missing character would turn a default meant to stop guessing into a default
    that stops work. Default-deny applies to formats we can name; an unnamed one
    keeps the old behaviour of saying nothing and letting the run proceed.
    """
    e = (ext or "").lstrip(".").lower()
    return bool(e) and e not in CODEGEN_READERS


def reader_for(ext: Optional[str], index: int) -> Optional[str]:
    """The one concrete call that opens this file, or None if there isn't one."""
    tmpl = CODEGEN_READERS.get((ext or "").lstrip(".").lower())
    return tmpl.format(i=index) if tmpl else None


def readable_by_read_file(ext: Optional[str]) -> bool:
    """Whether `read_file` can open it — as text, or by rendering it for vision."""
    e = (ext or "").lstrip(".").lower()
    return e in _document_exts() or e in IMAGE_EXTS


def refusal_for(ext: Optional[str], file_id: str = "") -> str:
    """What to tell the model about a file generated code cannot open.

    Two different situations, and conflating them wastes a turn. A PDF has
    somewhere else to go and the model should be sent there. A `.zip` does not,
    and saying "use read_file" would send it to a tool that will also fail —
    the honest answer is that this run cannot read the file at all.
    """
    e = (ext or "").lstrip(".").lower()
    if readable_by_read_file(e):
        target = f"read_file(file_id='{file_id}')" if file_id else "the read_file tool"
        return f"NOT readable from generated code. Use {target} instead."
    return (
        f"NOT readable from generated code, and no reader exists for "
        f".{e or 'this format'} anywhere in this product — do not attempt to "
        f"load it and do not substitute another file. Say the format is "
        f"unsupported."
    )
