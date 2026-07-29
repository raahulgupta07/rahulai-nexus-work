"""One artifact per run per mode (behavioural defect 1).

A single run used to be able to insert two independent version-1 artifacts of
the same mode, leaving an orphan and no answer to "which one is current".
Nothing here names a dataset, connector or tool — the rule is a property of a
run, not of any data.
"""

import pytest

from app.ai.tools.implementations._artifact_run_scope import (
    find_run_artifact,
    next_run_version,
    pick_run_artifact,
)


class FakeArtifact:
    def __init__(self, id, completion_id, mode, version=1, created_at=0, deleted_at=None):
        self.id = id
        self.completion_id = completion_id
        self.mode = mode
        self.version = version
        self.created_at = created_at
        self.deleted_at = deleted_at


RUN = "run-1"
OTHER_RUN = "run-2"


# --- pick_run_artifact -----------------------------------------------------

def test_no_artifacts_yet_means_a_fresh_build():
    assert pick_run_artifact([], completion_id=RUN, mode="page") is None


def test_finds_the_artifact_this_run_already_built():
    a = FakeArtifact("a", RUN, "page")
    assert pick_run_artifact([a], completion_id=RUN, mode="page") is a


def test_ignores_an_artifact_from_a_different_run():
    a = FakeArtifact("a", OTHER_RUN, "page")
    assert pick_run_artifact([a], completion_id=RUN, mode="page") is None


def test_ignores_a_different_mode_so_one_run_may_ship_several_deliverables():
    a = FakeArtifact("a", RUN, "doc")
    assert pick_run_artifact([a], completion_id=RUN, mode="page") is None
    assert pick_run_artifact([a], completion_id=RUN, mode="doc") is a


@pytest.mark.parametrize("mode", ["page", "doc", "slides"])
def test_rule_holds_for_every_mode(mode):
    a = FakeArtifact("a", RUN, mode)
    assert pick_run_artifact([a], completion_id=RUN, mode=mode) is a


def test_ignores_a_soft_deleted_artifact():
    a = FakeArtifact("a", RUN, "page", deleted_at="2026-01-01")
    assert pick_run_artifact([a], completion_id=RUN, mode="page") is None


def test_picks_the_head_of_the_version_chain():
    v1 = FakeArtifact("v1", RUN, "doc", version=1, created_at=1)
    v2 = FakeArtifact("v2", RUN, "doc", version=2, created_at=2)
    assert pick_run_artifact([v1, v2], completion_id=RUN, mode="doc") is v2
    assert pick_run_artifact([v2, v1], completion_id=RUN, mode="doc") is v2


def test_same_version_falls_back_to_most_recent():
    older = FakeArtifact("old", RUN, "page", version=1, created_at=1)
    newer = FakeArtifact("new", RUN, "page", version=1, created_at=2)
    assert pick_run_artifact([older, newer], completion_id=RUN, mode="page") is newer


def test_unidentified_run_never_adopts_an_existing_artifact():
    # An artifact whose run is unknown must not be claimed by a build that also
    # has no run id — that would let unrelated turns overwrite each other.
    a = FakeArtifact("a", None, "page")
    assert pick_run_artifact([a], completion_id=None, mode="page") is None
    assert pick_run_artifact([a], completion_id="", mode="page") is None


def test_missing_mode_is_never_a_match():
    a = FakeArtifact("a", RUN, "page")
    assert pick_run_artifact([a], completion_id=RUN, mode=None) is None


def test_completion_id_compares_across_str_and_uuid_like_types():
    class UuidLike:
        def __init__(self, v):
            self.v = v

        def __str__(self):
            return self.v

    a = FakeArtifact("a", UuidLike(RUN), "page")
    assert pick_run_artifact([a], completion_id=RUN, mode="page") is a


# --- next_run_version ------------------------------------------------------

def test_first_build_of_a_run_is_version_one():
    assert next_run_version(None) == 1


def test_second_build_supersedes_rather_than_restarting_at_one():
    assert next_run_version(FakeArtifact("a", RUN, "page", version=1)) == 2
    assert next_run_version(FakeArtifact("a", RUN, "page", version=7)) == 8


def test_missing_version_is_treated_as_one():
    a = FakeArtifact("a", RUN, "page", version=None)
    assert next_run_version(a) == 2


# --- find_run_artifact (thin DB wrapper) -----------------------------------

class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, rows=None, raises=False):
        self.rows = rows or []
        self.raises = raises
        self.calls = 0

    async def execute(self, stmt):
        self.calls += 1
        if self.raises:
            raise RuntimeError("db down")
        return FakeResult(self.rows)


@pytest.mark.asyncio
async def test_find_returns_the_runs_artifact():
    a = FakeArtifact("a", RUN, "page")
    db = FakeSession([a])
    got = await find_run_artifact(db, report_id="r", completion_id=RUN, mode="page")
    assert got is a


@pytest.mark.asyncio
async def test_find_degrades_to_a_fresh_build_when_the_lookup_fails():
    # A failed lookup must never fail the build — it falls back to today's
    # behaviour of inserting a new artifact.
    db = FakeSession(raises=True)
    got = await find_run_artifact(db, report_id="r", completion_id=RUN, mode="page")
    assert got is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "report_id,completion_id,mode",
    [(None, RUN, "page"), ("r", None, "page"), ("r", RUN, None)],
)
async def test_find_does_not_query_without_a_complete_key(report_id, completion_id, mode):
    db = FakeSession([FakeArtifact("a", RUN, "page")])
    got = await find_run_artifact(
        db, report_id=report_id, completion_id=completion_id, mode=mode
    )
    assert got is None
    assert db.calls == 0
