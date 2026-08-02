"""A failed file lookup has to say what it could have asked for.

The message used to name only the ids that failed, so the model's next move was
a guess — and every guess is a fresh code-generation round. The reported run
burned three of them before stumbling onto a different tool by filename. It
already knew the file existed; it had read the id out of its own context a
moment earlier. Nobody had told it what was actually reachable.

What this must NOT do is substitute a near-miss. Quietly reading a neighbouring
file when the named one is absent is the positional-binding failure: the code
runs, the number looks fine, and it is the wrong month.
"""

from app.ai.tools.implementations._source_files import (
    _MAX_LISTED,
    unresolved_files_error,
)


class FakeFile:
    def __init__(self, fid, filename):
        self.id = fid
        self.filename = filename
        self.path = f"/files/{filename}"
        self.is_agent_readable = True


class FakeReport:
    def __init__(self, files):
        self.files = list(files)
        self.data_sources = []


CSV = FakeFile("9c1d", "MM_Conso_H1_2025.csv")
DOCX = FakeFile("d203", "CRM Agent Q&A.docx")


def _ctx(files):
    return {"report": FakeReport(files), "excel_files": [], "project_files": []}


def test_it_names_the_id_that_failed():
    msg = unresolved_files_error(_ctx([CSV]), ["a3f2"], tool="create_data")

    assert "a3f2" in msg
    assert msg.startswith("create_data:")


def test_it_names_what_is_actually_reachable():
    """The whole point. Without this the retry is blind."""
    msg = unresolved_files_error(_ctx([CSV]), ["a3f2"], tool="create_data")

    assert "MM_Conso_H1_2025.csv" in msg
    assert "file_id=9c1d" in msg


def test_a_word_document_says_how_to_read_it_instead():
    """Pointing pd.read_csv at a docx produces an error, or worse a
    plausible-looking frame of nonsense."""
    msg = unresolved_files_error(_ctx([DOCX]), ["a3f2"], tool="inspect_data")

    assert "not loadable in code" in msg
    assert "read_file(file_id='d203')" in msg


def test_a_csv_is_not_labelled_unreadable():
    msg = unresolved_files_error(_ctx([CSV]), ["a3f2"], tool="write_csv")

    assert "not loadable" not in msg


def test_a_run_with_no_files_says_so_rather_than_listing_nothing():
    """"Reachable from this run:" followed by a full stop reads as a bug."""
    msg = unresolved_files_error(_ctx([]), ["a3f2"], tool="create_data")

    assert "no readable files at all" in msg
    assert "Reachable from this run" not in msg


def test_a_long_list_is_capped_and_says_it_was_capped():
    """A silent truncation here would read as "that is everything" — the same
    class of lie the rest of this release is about."""
    many = [FakeFile(f"id{i}", f"file_{i}.csv") for i in range(_MAX_LISTED + 5)]

    msg = unresolved_files_error(_ctx(many), ["a3f2"], tool="create_data")

    assert "file_0.csv" in msg
    assert f"file_{_MAX_LISTED + 4}.csv" not in msg
    assert "5 more not listed" in msg


def test_a_short_list_does_not_claim_more_exist():
    msg = unresolved_files_error(_ctx([CSV]), ["a3f2"], tool="create_data")

    assert "not listed" not in msg


def test_it_never_offers_to_substitute_a_file():
    """★The refusal must stay a refusal. Reading a neighbouring file because
    the named one is missing is how a chart silently shows a different month."""
    msg = unresolved_files_error(_ctx([CSV]), ["a3f2"], tool="create_data").lower()

    for tempting in ("using instead", "falling back", "substitut", "assuming you meant"):
        assert tempting not in msg, msg


def test_every_tool_signs_its_own_message():
    for tool in ("create_data", "inspect_data", "write_csv"):
        assert unresolved_files_error(_ctx([CSV]), ["x"], tool=tool).startswith(f"{tool}:")


def test_no_missing_ids_still_produces_a_sentence():
    """Defensive: an empty list must not render "no file matched ." """
    msg = unresolved_files_error(_ctx([CSV]), [], tool="create_data")

    assert "the requested id(s)" in msg


def test_a_project_file_is_offered_as_an_alternative():
    """It is reachable since the resolver was unified, so it must be listed —
    the failure message reads the same pool the tools do."""
    ctx = {"report": FakeReport([]), "excel_files": [], "project_files": [CSV]}

    msg = unresolved_files_error(ctx, ["a3f2"], tool="create_data")

    assert "MM_Conso_H1_2025.csv" in msg
