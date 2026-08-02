"""A saved chart must re-read the files it was written against.

Generated code reads uploads positionally — `pd.read_csv(excel_files[2].path)`.
Refresh rebuilt that list from `report.files`, the report's entire attachment
history in no defined order, so a later upload re-pointed every index.

The loud symptom was a crash: a report reached 19 attachments, slot 0 became a
Word document, and refresh died with

    'utf-8' codec can't decode byte 0xa3 in position 14: invalid start byte

The quiet symptom is the one these tests exist for. Had slot 0 landed on a
different CSV, refresh would have finished green over the wrong month's numbers.
"""

import asyncio

import pytest

from app.services.step_files import (
    StepFileBindingError,
    indexed_positions,
    record_source_files,
    resolve_step_excel_files,
    uses_excel_files,
)


class _File:
    def __init__(self, fid, filename):
        self.id = fid
        self.filename = filename
        self.path = f"/uploads/{fid}_{filename}"


class _Step:
    def __init__(self, code="", source_file_ids=None):
        self.code = code
        self.source_file_ids = source_file_ids


class _Report:
    def __init__(self, files):
        self.files = files


LOOP_CODE = (
    "def generate_df(ds_clients, excel_files):\n"
    "    for i, label in enumerate(month_labels):\n"
    "        df = pd.read_csv(excel_files[i].path)\n"
)
LITERAL_CODE = (
    "def generate_df(ds_clients, excel_files):\n"
    "    a = pd.read_csv(excel_files[0].path)\n"
    "    b = pd.read_csv(excel_files[1].path)\n"
)
NO_FILE_CODE = "def generate_df(ds_clients, excel_files):\n    return ds_clients['x'].query('select 1')\n"


class _EmptyResult:
    def scalars(self):
        return self

    def all(self):
        return []


class _FakeDb:
    """Stands in for the session used to fetch files detached from the report.

    Returns nothing, which is the case worth testing: a recorded file that no
    longer exists anywhere must stop the refresh rather than be skipped.
    """

    async def execute(self, *_args, **_kwargs):
        return _EmptyResult()


def _resolve(step, report):
    return asyncio.run(resolve_step_excel_files(_FakeDb(), step, report))


def test_the_recorded_files_are_used_in_the_recorded_order():
    """The binding is an identity, so appending files changes nothing."""
    jan, feb = _File("f-jan", "Jan.csv"), _File("f-feb", "Feb.csv")
    later = _File("f-new", "Something.csv")
    step = _Step(LOOP_CODE, source_file_ids=["f-jan", "f-feb"])
    # Deliberately out of order and with an extra file, i.e. the exact drift.
    report = _Report([later, feb, jan])

    got = _resolve(step, report)

    assert [f.id for f in got] == ["f-jan", "f-feb"]


def test_a_recorded_file_that_is_gone_stops_the_refresh():
    """Silently dropping it would shift every later index by one."""
    step = _Step(LOOP_CODE, source_file_ids=["f-jan", "f-deleted"])
    report = _Report([_File("f-jan", "Jan.csv")])

    with pytest.raises(StepFileBindingError) as e:
        _resolve(step, report)

    assert "no longer exist" in str(e.value)


def test_a_word_document_in_the_list_stops_a_legacy_refresh():
    """The reported crash: excel_files[0] became a .docx, pandas read a zip."""
    step = _Step(LOOP_CODE, source_file_ids=None)
    report = _Report([
        _File("f-doc", "CRM Agent Q&A , Logic.docx"),
        _File("f-jan", "Jan.csv"),
    ])

    with pytest.raises(StepFileBindingError) as e:
        _resolve(step, report)

    assert "not a data file" in str(e.value)


def test_the_same_file_uploaded_twice_stops_a_legacy_refresh():
    """Each re-upload appends a NEW row, so every later index moves."""
    step = _Step(LOOP_CODE, source_file_ids=None)
    report = _Report([
        _File("f-1", "Jan.csv"),
        _File("f-2", "Feb.csv"),
        _File("f-3", "Jan.csv"),
    ])

    with pytest.raises(StepFileBindingError) as e:
        _resolve(step, report)

    assert "more than once" in str(e.value)


def test_an_unchanged_legacy_report_still_refreshes():
    """The guard must not break reports whose files never moved."""
    step = _Step(LOOP_CODE, source_file_ids=None)
    report = _Report([_File("f-1", "Jan.csv"), _File("f-2", "Feb.csv")])

    assert [f.id for f in _resolve(step, report)] == ["f-1", "f-2"]


def test_code_that_reads_no_files_is_never_blocked():
    """A pure database query has no positional binding to protect."""
    step = _Step(NO_FILE_CODE, source_file_ids=None)
    report = _Report([_File("f-doc", "notes.docx"), _File("f-doc2", "notes.docx")])

    assert len(_resolve(step, report)) == 2


def test_reading_past_the_end_of_the_list_stops_the_refresh():
    step = _Step(LITERAL_CODE, source_file_ids=None)
    report = _Report([_File("f-1", "Jan.csv")])

    with pytest.raises(StepFileBindingError) as e:
        _resolve(step, report)

    assert "no longer exists" in str(e.value)


def test_a_computed_index_is_not_mistaken_for_no_file_use():
    """★The first version of this guard checked literal indices only.

    The step that exposed the bug indexes with a loop variable, so it reported
    zero positions, passed the check, and the .docx went through anyway.
    """
    assert indexed_positions(LOOP_CODE) == []
    assert uses_excel_files(LOOP_CODE) is True
    assert indexed_positions(LITERAL_CODE) == [0, 1]
    # ★Declaring the parameter is not using it. Every generated function has
    # `excel_files` in its signature, so a bare name search called a pure
    # database query a file reader and refused to refresh it.
    assert uses_excel_files(NO_FILE_CODE) is False
    assert uses_excel_files("df = ds_clients['x'].query('select 1')") is False


def test_recording_stores_ids_in_order_and_nothing_for_an_empty_run():
    step = _Step()
    record_source_files(step, [_File("b", "B.csv"), _File("a", "A.csv")])
    assert step.source_file_ids == ["b", "a"]

    # No files means no positional binding — NULL, not an empty list, so the
    # legacy path stays distinguishable from "ran against nothing".
    record_source_files(step, [])
    assert step.source_file_ids is None
