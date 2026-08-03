"""No file format may be readable in one place and unknown in another.

Three modules kept their own copy of the same eight extensions, and a fourth
(the image renderer) knew about three formats none of the three had heard of.
The copies agreed on the day they were written; nothing made them keep agreeing.

★The copies were not the worst of it. All three were BLOCK-lists, so an
extension nobody had listed defaulted to "let generated code try", and the model
— handed a filename and no reader — guessed `pd.read_csv`. Measured against real
files on 2026-08-03 (FILE-SUPPORT-MATRIX.md):

    sample.rtf  -> FRAME 157x1     RTF control words returned as data
    sample.eml  -> FRAME 6x1
    sample.yaml -> FRAME 4x1

No exception. A confident wrong answer, which is worse than the crash the guard
existed to prevent.

These tests hold the inversion in place: membership is decided once, in
`app/services/file_formats.py`, and it is decided by naming a reader. Adding a
format means adding a reader. There is no list to forget to update.

★Read-only, no schema — belongs in `tests/unit/fork`. See CLAUDE.md.
"""
import re
from pathlib import Path

import pytest

from app.services.file_formats import (
    CODEGEN_READERS,
    IMAGE_EXTS,
    loadable_in_code,
    readable_by_read_file,
    reader_for,
    refusal_for,
)

BACKEND = Path(__file__).resolve().parents[3]
CONSUMERS = {
    "coder": BACKEND / "app" / "ai" / "agents" / "coder" / "coder.py",
    "source_files": BACKEND / "app" / "ai" / "tools" / "implementations" / "_source_files.py",
    "step_files": BACKEND / "app" / "services" / "step_files.py",
    "file_tool_common": BACKEND / "app" / "ai" / "tools" / "implementations" / "_file_tool_common.py",
}

# The five that came back as a frame of nonsense. They split by what they are,
# not by how they failed: four are genuinely plain text and want a text reader;
# `.rtf` is a markup container LibreOffice can lay out, so it belongs with the
# documents — `read_text` on it would hand the model control words, which is the
# same defect in a politer form. Named individually so a regression says which.
ONCE_SILENT_GARBAGE_TEXT = ["eml", "yaml", "html", "xml"]
ONCE_SILENT_GARBAGE_DOC = ["rtf"]


def test_an_unknown_extension_is_refused_not_attempted():
    """The inversion itself. A format nobody has thought about must be refused,
    because the alternative is the model guessing a reader from the filename."""
    for ext in ("zip", "sqlite", "avro", "heic", "wat", ""):
        assert not loadable_in_code(ext), (
            f".{ext} is loadable with no reader named — the block-list default "
            f"is back"
        )


def test_a_file_with_no_extension_is_unknown_not_unreadable():
    """★The one carve-out in default-deny, and it is deliberate. `dataset` with
    no dot is very often a CSV; refusing it would turn a default meant to stop
    the model guessing into a default that stops real work. So there is no
    reader to name — and also no refusal.

    This was caught by an existing test the first version of the change broke
    (`test_an_extensionless_file_is_not_refused`), which is exactly what that
    test was written for.
    """
    from app.services.file_formats import refused_in_code

    assert not loadable_in_code("")
    assert not refused_in_code(""), (
        "an extension-less file is being refused — a missing dot is not "
        "evidence of an unreadable format"
    )
    # Everything we CAN name still refuses.
    assert refused_in_code("zip")
    assert refused_in_code("rtf")


def test_every_loadable_extension_names_a_concrete_call():
    """`loadable_in_code` and `reader_for` cannot disagree: the first IS
    membership in the table the second reads."""
    for ext in CODEGEN_READERS:
        assert loadable_in_code(ext)
        call = reader_for(ext, 3)
        assert call and "excel_files[3]" in call, f"{ext} has no usable reader"
        assert "{i}" not in call, f"{ext} reader left its placeholder unformatted"


@pytest.mark.parametrize("ext", ONCE_SILENT_GARBAGE_TEXT)
def test_the_formats_that_returned_a_frame_of_nonsense_now_read_as_text(ext):
    """★These are markup and prose, not tables. They are readable — the defect
    was never that we could not open them, it was that `pd.read_csv` opened them
    into rows. `read_text` cannot invent a row."""
    call = reader_for(ext, 0)
    assert call is not None, f".{ext} lost its reader"
    assert call.startswith("read_text("), (
        f".{ext} is read with `{call}` — anything that returns a DataFrame here "
        f"is the silent-garbage defect returning"
    )


@pytest.mark.parametrize("ext", ONCE_SILENT_GARBAGE_DOC)
def test_the_document_that_returned_a_frame_of_nonsense_goes_to_read_file(ext):
    """`.rtf` measured worst of all — 157 rows of control words — and it is the
    one that must NOT get a text reader. LibreOffice can lay it out, so the
    honest route is `read_file`, which renders it."""
    assert not loadable_in_code(ext)
    assert readable_by_read_file(ext), (
        f".{ext} lost its read_file route — it is now unreadable everywhere, "
        f"which is a worse outcome than the frame of nonsense"
    )
    assert "read_file" in refusal_for(ext, "abc")


def test_documents_and_images_are_sent_to_read_file_not_declared_impossible():
    """A PDF is unreadable *from code* and perfectly readable by `read_file`.
    Conflating the two sends the model to a dead end or leaves it guessing."""
    for ext in ("pdf", "docx", "pptx", "doc", "rtf", "odt", "odp", "ppt"):
        assert not loadable_in_code(ext)
        assert readable_by_read_file(ext), f".{ext} lost its read_file route"
        assert "read_file" in refusal_for(ext, "abc")
    for ext in sorted(IMAGE_EXTS):
        assert not loadable_in_code(ext)
        assert readable_by_read_file(ext)


def test_a_format_nothing_can_read_says_so_instead_of_naming_a_tool():
    """★Telling the model to try `read_file` on a `.zip` buys a second failure
    and a wasted turn. The two refusals are deliberately different sentences."""
    msg = refusal_for("zip", "abc")
    assert "read_file" not in msg, "sent to a tool that also cannot open it"
    assert "unsupported" in msg.lower()


def test_no_consumer_keeps_its_own_copy_of_the_sets():
    """The named sets are gone from all four modules. A new literal set of
    extensions in any of them is how this defect comes back."""
    dead = ("_NOT_LOADABLE", "_CODEGEN_UNREADABLE_EXTS")
    for label, path in CONSUMERS.items():
        src = path.read_text(encoding="utf-8")
        for name in dead:
            assert not re.search(rf"^{name}\s*=", src, re.M), (
                f"{label} defines {name} again — the copies are back"
            )
        assert "from app.services.file_formats import" in src, (
            f"{label} no longer asks the one registry"
        )


def test_the_renderer_and_the_refusal_share_one_idea_of_an_image():
    """`bmp`/`tiff`/`tif` were renderable for vision and simultaneously offered
    to generated code with no reader. Same object now, not two equal sets."""
    from app.ai.tools.implementations import _file_tool_common as ftc

    assert ftc._RENDERABLE_IMAGE_EXTS is IMAGE_EXTS
    for ext in ("bmp", "tiff", "tif"):
        assert ext in IMAGE_EXTS
        assert not loadable_in_code(ext)


def test_the_coder_prompt_never_contradicts_the_registry():
    """The rules block is prose the model reads; the registry is what the
    per-file directive uses. A format the prose says to read with pandas and the
    registry refuses is a contradiction the model resolves by guessing."""
    src = CONSUMERS["coder"].read_text(encoding="utf-8")
    block = src[src.index("def _file_access_rules"):]
    block = block[: block.index("\n\ndef ")]
    for ext in ("pdf", "docx", "pptx", "doc", "rtf", "odt", "odp", "ppt"):
        assert f"`.{ext}`" in block, (
            f".{ext} is refused by the registry but the prompt never mentions it"
        )
    assert "ANY other extension" in block, (
        "the rules must close with a default-deny clause, or an unlisted format "
        "reads as an omission the model may fill in"
    )
    assert "pd.read_parquet" in block


def test_all_four_file_connectors_agree_on_what_text_is():
    """★Four connectors kept four `TEXT_EXTS`, and no two matched. Measured:
    `.ndjson` was content from S3 and opaque from a network directory; `.xml`,
    `.py` and `.sql` were content from S3 and network dirs and opaque from both
    Drives. Nothing about the bytes justified any of it.

    Google Drive is the one deliberate difference: it has no tabular branch at
    all, so `csv`/`tsv` ride in its text set or they are unreadable there. The
    carve-out is asserted rather than allowed, so it stays a decision.
    """
    from app.data_sources.clients import (
        google_drive_client,
        graph_drive_client,
        network_dir_client,
        s3_client,
    )
    from app.services.file_formats import (
        CONNECTOR_TABULAR_EXTS,
        CONNECTOR_TEXT_EXTS,
    )

    for client in (s3_client, network_dir_client, graph_drive_client):
        assert client.TEXT_EXTS is CONNECTOR_TEXT_EXTS, (
            f"{client.__name__} keeps its own idea of a text file"
        )
        assert client.TABULAR_EXTS is CONNECTOR_TABULAR_EXTS

    assert google_drive_client.TEXT_EXTS == CONNECTOR_TEXT_EXTS | {"csv", "tsv"}
    assert not hasattr(google_drive_client, "TABULAR_EXTS"), (
        "Drive gained a tabular branch — fold csv/tsv back into the shared set"
    )

    for ext in ("ndjson", "jsonl", "xml", "py", "sql"):
        assert ext in CONNECTOR_TEXT_EXTS, f".{ext} was readable somewhere and is not now"


def test_the_refresh_guard_and_the_codegen_directive_agree():
    """★`step_files` decides whether pressing Refresh refuses. If it and the
    codegen directive disagree, a file is loadable when the code is written and
    proof of drift when it is re-run — the report breaks on refresh with no
    change to the data."""
    from app.services import step_files

    assert step_files.loadable_in_code is loadable_in_code
