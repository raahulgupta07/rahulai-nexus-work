"""The librarian's verdict must survive the request that produced it.

Every uploaded file is routed by an LLM "librarian" that picks a destination
(table / instruction / skill / knowledge) and returns a confidence and a
one-line reason with it. Both were written to the container log and discarded,
so the UI could show only a coloured badge — a well-founded call and a coin-flip
looked identical, and a wrong one was unreviewable.

The record now lives on ``File.preview["intake"]``. That column was chosen
because it already exists and is nullable JSON, so no migration is needed — but
it means TWO writers share one column inside a single upload request:

  1. ``_smart_file_intake`` records the verdict, early.
  2. ``generate_file_preview`` builds a fresh dict from the file's bytes and is
     assigned to ``File.preview``, later.

Step 2 knows nothing about step 1. Assigning its result directly erases the
record on every upload that produces a preview — which is most of them — and the
failure is silent: the decision simply is not there afterwards, exactly as if it
had never been recorded. ``merge_intake_into_preview`` exists for that one
reason, and this file pins it.
"""
import pytest

from app.services.file_service import (
    INTAKE_PREVIEW_KEY,
    merge_intake_into_preview,
    read_intake_decision,
)


class _FileRow:
    """Stands in for a File ORM row: only ``preview`` matters here."""

    def __init__(self, preview=None):
        self.preview = preview


VERDICT = {
    "destination": "knowledge",
    "confidence": 0.76,
    "reason": "Reads as question-and-answer prose.",
    "decided_by": "llm",
    "decided_at": "2026-07-30T12:00:00",
}


# ── the clobber this function exists to prevent ─────────────────────────────

def test_a_freshly_built_preview_does_not_erase_the_verdict():
    """The whole point. `generate_file_preview` returns a dict built from the
    file's bytes with no intake key; merging must carry the record across."""
    recorded = merge_intake_into_preview(None, VERDICT)
    fresh_preview = {"type": "text", "filename": "q_and_a.docx", "rows": 12}

    merged = merge_intake_into_preview(fresh_preview, recorded[INTAKE_PREVIEW_KEY])

    assert merged[INTAKE_PREVIEW_KEY] == VERDICT
    # ...and the preview it was merged into is still fully intact.
    assert merged["type"] == "text"
    assert merged["rows"] == 12


def test_reading_back_what_the_preview_writer_stored():
    """End to end through the two helpers the upload path actually calls."""
    row = _FileRow(merge_intake_into_preview(None, VERDICT))
    row.preview = merge_intake_into_preview({"type": "excel"}, read_intake_decision(row))

    assert read_intake_decision(row) == VERDICT


def test_the_merge_returns_a_new_object():
    """SQLAlchemy does not track in-place mutation of a JSON column, so a writer
    that edited the existing dict would have its change silently dropped at
    commit. Returning a fresh object is what makes the reassignment real."""
    original = {"type": "csv"}
    merged = merge_intake_into_preview(original, VERDICT)

    assert merged is not original
    assert INTAKE_PREVIEW_KEY not in original


# ── degrading on files that predate the record ──────────────────────────────

def test_a_file_with_no_intake_reads_as_unknown():
    """Not as low confidence. Every file uploaded before this shipped has no
    record, and presenting that as an uncertain verdict would put a warning on
    files the AI may well have called correctly."""
    assert read_intake_decision(_FileRow(None)) is None
    assert read_intake_decision(_FileRow({"type": "text"})) is None


@pytest.mark.parametrize("preview", [None, "", [], 0, "not a dict"])
def test_a_non_dict_preview_is_survivable(preview):
    """`preview` is free-form JSON written by several code paths over time. A
    reader that assumed dict would raise on the files most likely to be legacy."""
    assert read_intake_decision(_FileRow(preview)) is None
    assert merge_intake_into_preview(preview, VERDICT)[INTAKE_PREVIEW_KEY] == VERDICT


def test_merging_nothing_leaves_the_preview_alone():
    """The no-record case must not stamp an empty key that the UI would then
    have to distinguish from a real verdict."""
    assert merge_intake_into_preview({"type": "csv"}, None) == {"type": "csv"}
    assert INTAKE_PREVIEW_KEY not in merge_intake_into_preview({"type": "csv"}, None)


# ── the record is exposed to the UI ─────────────────────────────────────────

def test_the_file_schema_carries_the_record():
    """Without this field the verdict is stored and still invisible — which is
    the state this whole change exists to leave behind."""
    from app.schemas.file_schema import FileSchema

    assert "intake" in FileSchema.model_fields
    assert FileSchema.model_fields["intake"].default is None, (
        "intake must default to None so endpoints that don't derive it report "
        "'not known' rather than an empty verdict"
    )
