"""Converting a file must retire what that file produced last time.

Found live, not by reading code. A probe document was uploaded (the librarian
filed it as an instruction), then converted to a skill — and afterwards the
agent carried BOTH: the original instruction and the new skill, from one file,
with nothing indicating which was current. Press convert twice and the stack
grows again.

The consequence is worse than clutter. Converting is how a user CORRECTS a
misfiling; leaving the superseded copy in place means the correction adds a
second opinion instead of replacing the first, and the file keeps its old badge
alongside the new one.

The two stores are reached through the same back-links the files API already
uses to derive a file's badge — ``Instruction.ai_source == "file:{id}"`` and
``MetadataResource.raw_data["source_file_id"]``. That reuse is deliberate:
anything the withdrawal misses is, by definition, something that still counts
toward the badge.
"""
import ast
import inspect
import textwrap

import pytest

import app.services.file_service as file_service


def _withdraw_source() -> str:
    return textwrap.dedent(
        inspect.getsource(file_service.FileService._withdraw_file_artifacts)
    )


def _intake_source() -> str:
    return textwrap.dedent(inspect.getsource(file_service.FileService._smart_file_intake))


# ── the withdrawal runs, and only when it should ────────────────────────────

def test_a_forced_conversion_withdraws_the_previous_artifacts():
    src = _intake_source()
    assert "_withdraw_file_artifacts" in src, (
        "converting no longer retires what the file produced before — the agent "
        "will read the old filing and the new one at once"
    )


def test_an_ordinary_reclassification_does_not_withdraw():
    """Re-running the classifier is not a decision to discard anything. Only an
    explicit conversion is, so the guard must test ``force_destination`` and not
    merely that intake ran."""
    src = _intake_source()
    call_at = src.index("_withdraw_file_artifacts")
    guard = src[:call_at].rsplit("if ", 1)[-1]
    assert "force_destination" in guard, (
        f"withdrawal is not gated on an explicit conversion; guard reads: {guard!r}"
    )


def test_the_withdrawal_happens_before_anything_new_is_written():
    """Order matters. Retiring after the new rows are created would sweep away
    the very artifacts just produced — the conversion would appear to succeed
    and leave the file with nothing at all."""
    src = _intake_source()
    withdraw_at = src.index("_withdraw_file_artifacts")
    create_at = src.index("_created = 0")
    assert withdraw_at < create_at


def test_keeping_the_old_filing_is_possible():
    """A Q&A document can legitimately be both: definitions as an instruction,
    full text still searchable as knowledge. Replacement is the default because
    correcting a mistake is the common case, but it must not be the only one."""
    sig = inspect.signature(file_service.FileService._smart_file_intake)
    assert "keep_existing" in sig.parameters
    assert sig.parameters["keep_existing"].default is False

    src = _intake_source()
    call_at = src.index("_withdraw_file_artifacts")
    guard = src[:call_at].rsplit("if ", 1)[-1]
    assert "keep_existing" in guard


def test_the_public_entry_point_forwards_it():
    sig = inspect.signature(file_service.FileService.reingest_file)
    assert "keep_existing" in sig.parameters

    src = textwrap.dedent(inspect.getsource(file_service.FileService.reingest_file))
    calls = [
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
        and any(kw.arg == "keep_existing" for kw in node.keywords)
    ]
    assert calls, "reingest_file accepts keep_existing but never passes it on"


# ── both stores are covered ─────────────────────────────────────────────────

def test_instructions_are_matched_by_the_back_link_the_ui_reads():
    """Not by data source, not by kind. `ai_source="file:{id}"` is exactly what
    `get_files_by_data_source` uses to decide a file's badge, so matching on
    anything else guarantees a row that survives withdrawal and keeps voting for
    the old badge."""
    src = _withdraw_source()
    assert 'f"file:{file_id}"' in src
    assert "Instruction.ai_source == marker" in src


def test_knowledge_chunks_are_matched_by_their_source_file():
    src = _withdraw_source()
    assert "source_file_id" in src
    assert '"knowledge"' in src


def test_retired_chunks_are_also_deactivated():
    """Retrieval paths filter on `is_active` as well as `deleted_at` depending
    on which one is used. A chunk that is soft-deleted but still active keeps
    being served, which is the failure this whole function exists to prevent."""
    src = _withdraw_source()
    assert "is_active = False" in src


def test_rows_are_soft_deleted_not_destroyed():
    """A conversion is a judgement call and the earlier filing may well have
    been the better one. Nothing here should be unrecoverable."""
    src = _withdraw_source()
    assert "deleted_at = datetime.utcnow()" in src
    assert "db.delete(" not in src
    assert "sql_delete" not in src


def test_a_withdrawal_failure_does_not_lose_the_conversion():
    """Best-effort by construction: the user asked for a conversion, and a
    failure to tidy up the previous one must not turn that into an error."""
    tree = ast.parse(_withdraw_source())
    handlers = [
        h for node in ast.walk(tree) if isinstance(node, ast.Try) for h in node.handlers
    ]
    assert len(handlers) >= 3, (
        "the instruction sweep, the knowledge sweep and the commit must each be "
        "survivable on their own"
    )


def test_it_reports_what_it_retired():
    """A silent replacement is indistinguishable from a silent failure. The
    counts are what let the UI say 'replaced 21 knowledge chunks' rather than
    leaving the user to wonder whether anything happened."""
    src = _withdraw_source()
    assert '"instructions": 0, "knowledge": 0' in src
    assert "return retired" in src
