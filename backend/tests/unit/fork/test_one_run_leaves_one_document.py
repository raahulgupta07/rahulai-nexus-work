"""A run that edits its own deliverable must not leave the draft behind.

★ Found live, not by review. One question produced TWO documents, both current,
same completion, ninety seconds apart:

    2b8f5a54…  doc  v1  completion 20ee4a36…
    dc03047e…  doc  v2  completion 20ee4a36…

The tool sequence was `create_doc` … `edit_doc`. `create_doc` already obeyed
"one deliverable per run per mode"; the edit path appended unconditionally, and
justified it in its own docstring:

    "edit_artifact / edit_doc still append a new row so history is preserved.
     That is a different run and a deliberate act."

It was not a different run. The agent was still working on its own document
inside a single turn. The sentence was a claim nothing tested — which is how
every defect in this file's neighbourhood has started.

What must stay true, and is tested here in both directions:
  * an edit of THIS run's artifact overwrites it — one live document
  * an edit of an EARLIER run's artifact appends — history preserved
  * anything unattributable appends, because an edit that cannot prove it owns
    a row must never overwrite it
"""
from app.ai.tools.implementations import _artifact_run_scope as scope


class _Artifact:
    """The two fields the decision reads. Deliberately not a real model — the
    rule is about run ownership and must not need a database to state."""

    def __init__(self, completion_id=None, version=1):
        self.completion_id = completion_id
        self.version = version


THIS_RUN = "11111111-1111-1111-1111-111111111111"
EARLIER_RUN = "22222222-2222-2222-2222-222222222222"


def test_an_edit_of_this_runs_own_document_overwrites_it():
    art = _Artifact(completion_id=THIS_RUN)
    assert scope.supersedes_in_place(art, completion_id=THIS_RUN) is True


def test_an_edit_of_an_earlier_runs_document_appends():
    """★The half that must NOT change. A user revising yesterday's document is
    building version history on purpose, and collapsing that would destroy it."""
    art = _Artifact(completion_id=EARLIER_RUN)
    assert scope.supersedes_in_place(art, completion_id=THIS_RUN) is False


def test_an_unattributable_edit_appends():
    """Fails to the safe side. Overwriting is destructive and irreversible, so
    it needs proof of ownership; appending is merely untidy."""
    assert scope.supersedes_in_place(_Artifact(completion_id=None),
                                     completion_id=THIS_RUN) is False
    assert scope.supersedes_in_place(_Artifact(completion_id=THIS_RUN),
                                     completion_id=None) is False
    assert scope.supersedes_in_place(None, completion_id=THIS_RUN) is False


def test_the_comparison_survives_uuid_objects():
    """Ids arrive as UUID objects from the ORM and as strings from a payload.
    An identity comparison would silently append forever."""
    class _Uuid:
        def __init__(self, s):
            self._s = s

        def __str__(self):
            return self._s

    art = _Artifact(completion_id=_Uuid(THIS_RUN))
    assert scope.supersedes_in_place(art, completion_id=THIS_RUN) is True


def test_the_version_still_advances_when_overwriting():
    """Superseding in place is not the same as losing the version count — the
    reader must still be able to see the document was revised."""
    art = _Artifact(completion_id=THIS_RUN, version=3)
    assert scope.next_run_version(art) == 4


# ---------------------------------------------------------------------------
# Both edit paths must actually USE the rule. A shared helper nobody calls is
# the same defect wearing a nicer hat.
# ---------------------------------------------------------------------------

import pathlib  # noqa: E402

_IMPL = pathlib.Path(__file__).resolve().parents[3] / "app" / "ai" / "tools" / "implementations"


def _source(name: str) -> str:
    return (_IMPL / name).read_text(encoding="utf-8")


def test_both_edit_paths_ask_before_appending():
    for name in ("edit_doc.py", "edit_artifact.py"):
        src = _source(name)
        assert "supersedes_in_place" in src, (
            f"{name} still appends unconditionally — a second live artifact per run"
        )


def test_neither_edit_path_kept_a_second_unconditional_insert():
    """★The shape that would quietly undo this: keeping the guarded branch AND
    a stray `db.add(Artifact(...))` elsewhere in the same function. Count the
    constructions — one guarded construction per edit path is the whole rule."""
    for name in ("edit_doc.py", "edit_artifact.py"):
        src = _source(name)
        assert src.count("new_artifact = Artifact(") == 1, (
            f"{name} builds more than one artifact row; only the else-branch may"
        )
