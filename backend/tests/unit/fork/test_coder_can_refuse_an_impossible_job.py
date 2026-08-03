"""The coder must be able to say no.

Its own prompt already carries the rule:

    `.pdf` / `.docx` / `.pptx` / images → NOT readable from generated code at
    all. Do not attempt it; the planner must use the `read_file` tool instead.

and `_excel_files_mapping` stamps every such entry "[NOT loadable in code]".
Then it was handed exactly that file, with no database connection beside it,
and asked for a `generate_df` anyway — and it had no way to refuse. That is the
contradiction in one sentence: **the coder is told these files are impossible
to read and is still required to return code that reads them.**

A model asked for code for an impossible job returns something. Measured
2026-08-03, a `.docx` in the folder and the prompt "summaries data for me": it
wrote three paragraphs of reasoning first, and the user got

    CSV generation failed — Execution error: invalid syntax (<string>, line 1)

★And the crash is the good outcome. The stub on the cancellation path is
`return pd.DataFrame()` — the same dead end reached quietly is an empty table
with no error at all.

The refusal is deliberately narrow (see `refusal_for_unreadable_files`): one
loadable CSV beside the PDF, or any connection to query, and the job is
possible again. A coder that refuses work it could have done is a worse bug
than the one being fixed here.
"""
import inspect

import pytest

from app.ai.agents.coder.coder import (
    CodegenRefused,
    refusal_for_unreadable_files,
)
from app.services.file_formats import IMAGE_EXTS, loadable_in_code

# ★This used to read `coder._CODEGEN_UNREADABLE_EXTS` — one of three
# hand-maintained copies of the same eight extensions, all of them block-lists.
# The registry is now an allow-list, so "unreadable" is everything it does not
# name, and the list below is written out rather than derived: a derived list
# would shrink silently the day someone adds a reader, and the point of this
# test is that the gate covers every format it refuses.
UNREADABLE = sorted(
    {"pdf", "docx", "pptx", "doc", "ppt", "odt", "odp", "rtf"} | set(IMAGE_EXTS)
)


class _File:
    def __init__(self, filename):
        self.filename = filename
        self.path = f"/uploads/{filename}"
        self.id = filename


class _Client:
    def execute_query(self, *a, **k):  # pragma: no cover - never called
        return []


# --- it refuses when there is genuinely no way -------------------------------

@pytest.mark.parametrize("ext", UNREADABLE)
def test_every_unreadable_format_alone_is_refused(ext):
    """★The gate must cover the whole list it declares, not just `.docx` —
    a list and a check that disagree is how the original hole opened."""
    assert not loadable_in_code(ext), f".{ext} gained a reader; update UNREADABLE"
    assert refusal_for_unreadable_files({}, [_File(f"report.{ext}")])


@pytest.mark.parametrize("ext", ["rtf", "odt", "odp", "doc", "ppt", "bmp", "tiff"])
def test_the_formats_the_old_block_list_missed_are_refused_too(ext):
    """★These seven were the hole. They were readable by the *renderer* and
    absent from all three block-lists, so `_impossible_request` did not fire on
    a folder holding only an `.rtf` — the coder was asked to write pandas
    against it, and measured, `pd.read_csv` returned 157 rows of control words
    with no error attached."""
    assert refusal_for_unreadable_files({}, [_File(f"report.{ext}")])


def test_a_format_nothing_can_open_is_not_sent_to_read_file():
    """A `.zip` has no route at all. Naming `read_file` there costs a turn and
    ends in a second failure."""
    reason = refusal_for_unreadable_files({}, [_File("archive.zip")])
    assert reason
    assert "read_file" not in reason, "sent to a tool that also cannot open it"


def test_the_incident_shape_is_refused():
    """A .docx in the folder, no connection. This is the measured case."""
    reason = refusal_for_unreadable_files({}, [_File("Weekly summary.docx")])
    assert reason and "Weekly summary.docx" in reason


def test_the_refusal_names_the_route_out():
    """★A refusal that only says no leaves the planner where it started. It has
    to name the tool that CAN do the job."""
    reason = refusal_for_unreadable_files({}, [_File("notes.pdf")])
    assert "read_file" in reason


def test_several_unreadable_files_are_all_named():
    reason = refusal_for_unreadable_files({}, [_File("a.pdf"), _File("b.docx")])
    assert "a.pdf" in reason and "b.docx" in reason


# --- what must NOT be refused ------------------------------------------------

def test_one_loadable_file_beside_the_pdf_is_still_a_job():
    assert refusal_for_unreadable_files({}, [_File("a.pdf"), _File("sales.csv")]) is None


def test_a_connection_makes_it_possible_whatever_the_files_are():
    """The PDF may be context; the numbers come from the warehouse."""
    assert refusal_for_unreadable_files({"main": _Client()}, [_File("a.pdf")]) is None


def test_no_files_at_all_is_not_a_refusal():
    assert refusal_for_unreadable_files({}, []) is None
    assert refusal_for_unreadable_files({}, None) is None


def test_ordinary_data_files_are_never_refused():
    for name in ("sales.csv", "book.xlsx", "rows.json", "log.txt", "data.tsv"):
        assert refusal_for_unreadable_files({}, [_File(name)]) is None, name


def test_an_extensionless_file_is_not_refused():
    """Unknown is not the same as unreadable — guessing here would refuse real
    work on the strength of a missing dot."""
    assert refusal_for_unreadable_files({}, [_File("dataset")]) is None


# --- it is wired into both entry points --------------------------------------

@pytest.mark.parametrize(
    "method", ["data_model_to_code", "generate_code"]
)
def test_the_coder_checks_before_calling_the_model(method):
    """★A refusal helper nothing calls fixes nothing. It must run BEFORE the
    LLM call, or the cost is paid and the answer is invented anyway.

    Both codegen entry points: `data_model_to_code` (the data-model path) and
    `generate_code` (the v2 prompt path). `generate_inspection_code` and
    `generate_transform_code` are deliberately not listed — they run against
    data already in hand, not against the report's file set.
    """
    from app.ai.agents.coder.coder import Coder

    body = inspect.getsource(getattr(Coder, method))
    assert "refusal_for_unreadable_files(" in body, f"{method} cannot refuse"
    assert "raise CodegenRefused(" in body
    assert body.index("raise CodegenRefused(") < body.index("self.llm.inference"), (
        "the refusal is checked after the model has already been called"
    )


def test_both_entry_points_are_covered():
    """Named separately so adding a third entry point without the check is a
    visible gap rather than a silent one."""
    from app.ai.agents.coder.coder import Coder

    source = inspect.getsource(Coder)
    assert source.count("raise CodegenRefused(") == 2


# --- a refusal is terminal, not a retry --------------------------------------

@pytest.mark.parametrize(
    "loop", ["generate_and_execute_stream", "generate_and_execute_stream_v2"]
)
def test_the_retry_loop_stops_on_a_refusal(loop):
    """★Retrying a refusal is how a "no" becomes a fabricated "yes": the same
    files and the same rules meet the second attempt, and the only thing that
    can change is the model's willingness to invent."""
    from app.ai.code_execution.code_execution import StreamingCodeExecutor

    body = inspect.getsource(getattr(StreamingCodeExecutor, loop))
    assert "CodegenRefused" in body, f"{loop} does not recognise a refusal"
    marker = body.index("isinstance(e, CodegenRefused)")
    tail = body[marker:marker + 600]
    assert "break" in tail, "the refusal falls through into the retry path"
    assert "e.reason" in tail, "the user gets a generic error instead of the reason"


def test_the_reason_reaches_the_user_not_just_the_log():
    from app.ai.code_execution.code_execution import StreamingCodeExecutor

    body = inspect.getsource(StreamingCodeExecutor.generate_and_execute_stream)
    marker = body.index("isinstance(e, CodegenRefused)")
    tail = body[marker:marker + 600]
    assert '"type": "stdout"' in tail


def test_a_refusal_is_an_exception_carrying_its_reason():
    exc = CodegenRefused("use read_file")
    assert exc.reason == "use read_file"
    assert str(exc) == "use read_file"
