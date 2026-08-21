"""A row the platform removed has to be said in the log the model reads.

WHY THIS FILE EXISTS
--------------------
The pandas proxy strips a spreadsheet's trailing TOTAL row before generated
code ever sees the frame (DEF-012, `coercion_guard` + `sheet_trailer`). That is
right: the trailer is not an observation, and leaving it in doubles every sum.

But the disclosure went only into the step payload — the database. The
execution log is the model's ONLY view of its own run, so the model computed
over the trimmed frame, went looking for the total row, found none, and told
the user truthfully that the sheet had no total. Right number, false story.

The fix is DELIVERY, and nothing else: the notice the stripper already wrote is
appended to the execution log, one `[platform]` line per removal. Nothing is
worded here, nothing is mutated, and a failure returns the log untouched — a
disclosure may never break the run it describes.

HOW THIS IS MEASURED, AND WHY
-----------------------------
The recording half is driven for real: a genuine `.xlsx` with a genuine TOTAL
trailer, read through `build_pandas_proxy`, exactly as generated code reads it.
Hand-building a disclosure dict would prove the formatter works and say nothing
about whether a real sheet ever reaches it.

The delivery half calls the log assembly directly with a stand-in `self`. It is
delivery, so what matters is the string that comes out — and asserting the
model's OWN notice text (not a paraphrase, not a marker) is what proves the
sentence survives the trip rather than being re-invented at the far end.

★Where source text is scanned at the bottom of this file it is scanned AFTER
comments and docstrings are removed. This repo has shipped a guard that matched
its own explanation at least four times: the fix's docstring quotes the very
expression the test was hunting for.
"""
import ast
import types
from pathlib import Path

import pytest

from app.ai.code_execution import code_execution as ce
from app.ai.code_execution import coercion_guard as cg
from app.ai.code_execution.code_execution import StreamingCodeExecutor

# ★Resolved with a fallback, NOT imported by name: on the pre-fix tree a
# module-level `from ... import PLATFORM_LOG_PREFIX` dies at COLLECTION, and a
# collection error proves this file imports the fix — not that it detects the
# defect. (This repo's own recorded lesson, and it fired on this very file.)
# With the fallback, the pre-fix tree runs the tests and FAILS them, which is
# what a red proof is. `test_the_prefix_constant_is_published` pins the name.
PLATFORM_LOG_PREFIX = getattr(ce, "PLATFORM_LOG_PREFIX", "[platform]")


def test_the_prefix_constant_is_published():
    assert getattr(ce, "PLATFORM_LOG_PREFIX", None) == "[platform]"

# The log assembly, called as a plain function. It reads exactly one thing off
# `self` — `_coercion_recorder` — so a namespace carrying that is a faithful
# stand-in and keeps this a pure call: no executor, no session, no database.
def _append(ex, log):
    # ★Resolved from the CLASS, lazily, because callers hand a SimpleNamespace
    # as the stand-in self (constructing a real executor needs app context).
    # Lazy so the pre-fix tree FAILS here with a named assertion instead of
    # dying at collection — an ImportError proves the guard imports the fix,
    # not that it detects the defect, and that trap fired on this very file.
    fn = getattr(StreamingCodeExecutor, "_append_platform_notices", None)
    assert fn is not None, "the fix is absent: no _append_platform_notices on StreamingCodeExecutor"
    return fn(ex, log)


def deliver(recorder, log: str) -> str:
    return _append(types.SimpleNamespace(_coercion_recorder=recorder), log)


@pytest.fixture(scope="module")
def sheet_with_a_total_row(tmp_path_factory) -> Path:
    """A real extract, written the way a person would write one.

    Three data rows and a bold `Total` beneath, no blank line between — the
    shape the detector was built against. Both numeric columns sum, AND the row
    is labelled, so this clears the detector's conservative bar twice over; a
    sheet that only just qualified would make a failure here ambiguous between
    "delivery broke" and "detection is borderline".
    """
    import pandas as pd

    path = tmp_path_factory.mktemp("sheets") / "sales.xlsx"
    pd.DataFrame(
        {
            "region": ["North", "South", "East", "Total"],
            "units": [10, 20, 30, 60],
            "revenue": [100.0, 200.0, 300.0, 600.0],
        }
    ).to_excel(path, index=False)
    return path


@pytest.fixture
def recorder_that_read_the_sheet(sheet_with_a_total_row):
    """A recorder in the state a real run leaves it in.

    Driven through `build_pandas_proxy(...).read_excel(...)`, which is the same
    call generated code makes — the proxy is what the sandbox hands it as
    `pd`.
    """
    import pandas as pd

    recorder = cg.CoercionRecorder()
    frame = cg.build_pandas_proxy(pd, recorder).read_excel(sheet_with_a_total_row)

    assert len(frame) == 3, (
        "the trailer was not stripped, so this fixture is not in the state the "
        "rest of this file assumes — the defect under test cannot arise"
    )
    return recorder


# ═══════════════════════════════════════════════════════════════════════════
# The removal is recorded, by a real read of a real sheet
# ═══════════════════════════════════════════════════════════════════════════


def test_reading_a_sheet_with_a_total_row_records_the_removal(
    recorder_that_read_the_sheet,
):
    assert len(recorder_that_read_the_sheet.trailers) == 1


def test_the_recorded_removal_carries_its_own_sentence(recorder_that_read_the_sheet):
    """The wording belongs to the stripper, which is the only thing that knows
    which columns summed and what the row called itself."""
    notice = recorder_that_read_the_sheet.trailers[0]["notice"]

    assert notice.strip()
    assert "TOTAL" in notice


# ═══════════════════════════════════════════════════════════════════════════
# The sentence reaches the log the model reads
# ═══════════════════════════════════════════════════════════════════════════


def test_the_model_is_told_the_total_row_was_taken_out(recorder_that_read_the_sheet):
    """The defect, stated directly: the model must be able to read this."""
    log = deliver(recorder_that_read_the_sheet, "rows: 3\n")

    assert PLATFORM_LOG_PREFIX in log


def test_the_line_carries_the_strippers_own_words_not_a_paraphrase(
    recorder_that_read_the_sheet,
):
    """Delivery, not authorship.

    A summary written at the delivery point would drift from the one in the
    step payload, and the model and the database would then disagree about
    what happened to the same sheet.
    """
    notice = recorder_that_read_the_sheet.trailers[0]["notice"]

    log = deliver(recorder_that_read_the_sheet, "rows: 3\n")

    assert f"{PLATFORM_LOG_PREFIX} {notice}" in log


def test_the_code_s_own_output_is_kept(recorder_that_read_the_sheet):
    """★POSITIVE CONTROL. The notice is APPENDED; it does not replace the log.

    The execution log is the model's view of its own run. A delivery that
    returned only the platform lines would satisfy both assertions above and
    delete everything the generated code printed.
    """
    log = deliver(recorder_that_read_the_sheet, "rows: 3\nmean revenue: 200.0\n")

    assert "rows: 3" in log
    assert "mean revenue: 200.0" in log


def test_the_notice_lands_on_a_line_of_its_own(recorder_that_read_the_sheet):
    """A log whose last line had no newline must not get the notice glued to it.

    `print("done")` without a trailing newline is ordinary; a run that produced
    `done[platform] The last row…` is a sentence the model has to parse out of
    another one.
    """
    log = deliver(recorder_that_read_the_sheet, "done")

    assert "\n" + PLATFORM_LOG_PREFIX in log
    assert "done" in log


def test_a_run_that_printed_nothing_still_gets_the_notice(
    recorder_that_read_the_sheet,
):
    """Silence is the case where the model has least to go on."""
    log = deliver(recorder_that_read_the_sheet, "")

    assert PLATFORM_LOG_PREFIX in log


def test_two_removals_are_two_lines(sheet_with_a_total_row):
    """One line per trailer — two sheets read, two rows removed, two sentences.

    A delivery that reported only the first would tell the model a true thing
    about one file and nothing about the other, which is the same silence this
    fix exists to end.
    """
    import pandas as pd

    recorder = cg.CoercionRecorder()
    proxy = cg.build_pandas_proxy(pd, recorder)
    proxy.read_excel(sheet_with_a_total_row)
    proxy.read_excel(sheet_with_a_total_row)

    log = deliver(recorder, "")

    assert log.count(PLATFORM_LOG_PREFIX) == 2


# ═══════════════════════════════════════════════════════════════════════════
# ★ The absence controls — a run with nothing to disclose says nothing
# ═══════════════════════════════════════════════════════════════════════════


def test_a_sheet_with_no_total_row_produces_no_platform_line(tmp_path):
    """★THE POSITIVE CONTROL FOR SILENCE, driven end to end.

    A `[platform]` line on a run where nothing was removed is a false fact, and
    the model would go looking for a row that was never there. The frame here
    is deliberately one the detector must decline: a last row that is the
    biggest, with no label and no column summing.
    """
    import pandas as pd

    path = tmp_path / "plain.xlsx"
    pd.DataFrame(
        {
            "region": ["North", "South", "Total Logistics Ltd"],
            "units": [10, 20, 45],
        }
    ).to_excel(path, index=False)

    recorder = cg.CoercionRecorder()
    frame = cg.build_pandas_proxy(pd, recorder).read_excel(path)

    assert len(frame) == 3, "a legitimate final row was deleted"
    assert recorder.trailers == []
    assert deliver(recorder, "rows: 3\n") == "rows: 3\n"


def test_a_run_with_no_recorder_at_all_is_left_alone():
    """Nothing instrumented the run, so there is nothing to say about it."""
    assert deliver(None, "rows: 3\n") == "rows: 3\n"


def test_a_broken_recorder_costs_the_notice_and_not_the_run():
    """A disclosure may never break the run it describes.

    The one hard rule the whole coercion-guard module is written to: an
    instrumented parse that fails to record still returns what the model asked
    for. Delivery inherits it.
    """
    class Exploding:
        @property
        def trailers(self):
            raise RuntimeError("boom")

    assert deliver(Exploding(), "rows: 3\n") == "rows: 3\n"


# ═══════════════════════════════════════════════════════════════════════════
# ★ The older delivery is still there — this fix ADDS a surface
# ═══════════════════════════════════════════════════════════════════════════


def executable_source(module) -> str:
    """A module's source with every comment and docstring removed.

    ★This repo's fixes quote the broken form in their own comments, and a
    plain scan has therefore matched its own explanation at least four times.
    Parsing and re-unparsing drops comments outright and this drops the
    docstrings, so what remains is only code that runs.

    ★Note `ast.unparse` normalises string quoting to single quotes, so scan for
    `payload['coercion']`, never for the double-quoted spelling in the file.
    """
    tree = ast.parse(Path(module.__file__).read_text())
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            body.pop(0)
            if not body:
                body.append(ast.Pass())
    return ast.unparse(ast.fix_missing_locations(tree))


def test_the_step_payload_still_carries_the_disclosure():
    """★POSITIVE CONTROL. The database surface predates this fix and stays.

    The log reaches the model; the payload reaches every later consumer of the
    step — the widget, the refresh, anyone reading the run afterwards. This fix
    adds the first WITHOUT trading away the second, and a "move it to the log"
    simplification would satisfy every other case in this file.
    """
    assert "payload['coercion']" in executable_source(ce)


def test_the_removed_rows_are_still_named_in_the_disclosure_report():
    """And the payload still carries the trailers under their own key.

    `total_rows_excluded` is what a later reader looks for. Asserted against
    `coercion_guard`, which is where the report is assembled — the log side
    reads `notice` off each trailer and never touches this key.
    """
    assert "total_rows_excluded" in executable_source(cg)


def test_the_marker_is_unmistakable_for_something_the_code_printed():
    """The prefix has to be unlike anything pandas or a `print()` emits.

    The model reads the execution log back as its own result; a platform line
    it mistakes for its own output is a line it may repeat to the user as
    something it computed.
    """
    assert PLATFORM_LOG_PREFIX == "[platform]"
