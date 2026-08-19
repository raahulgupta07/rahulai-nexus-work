"""The registry build stays correct, and stays manual.

This deployment builds its images ON THE SERVER: the repo is checked out under
/opt and `docker compose build` produces a native amd64 image there. A registry
image is therefore an alternative delivery path, not the one in use — an
automatic build per release costs ~20 minutes of Actions minutes for an
artifact nobody pulls.

What must not rot is the workflow's correctness, because the day it IS needed
is the day someone cannot build on the server. So this file pins the things
that would quietly make it useless:

Two facts about this project that together make releases fragile:

  1. Every image built by hand here is **arm64** — the maintainer works on a
     Mac. The deployment targets are **x86_64**. An arm64 image cannot run on
     them at all.
  2. The GHCR workflow that builds linux/amd64 was `workflow_dispatch` only.

So 0.0.543.2 was tagged, pushed, and had no runnable artifact anywhere — a
release nobody could deploy, with nothing failing to say so. Pushing a `v*` tag
now builds and publishes automatically, and the image is named after the
VERSION file so a deployment can pull a predictable tag rather than a
branch-and-sha string.

★The workflow also refuses to build when the tag and the VERSION file disagree.
Naming an image after a release the tree is not is exactly the version drift
this repo has been bitten by before.
"""
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[4]
WF = REPO / ".github" / "workflows" / "docker-image-branch.yml"


def _wf() -> dict:
    d = yaml.safe_load(WF.read_text(encoding="utf-8"))
    # PyYAML parses the bare key `on:` as the boolean True.
    return d


def _triggers(d: dict) -> dict:
    return d[True] if True in d else d.get("on", {})


def test_the_registry_build_is_manual():
    """★Deliberately manual. This deployment builds on the server, so an
    automatic registry build spends ~20 minutes per release producing an
    artifact nobody pulls. The workflow stays correct and ready for the case
    where server-side building is not wanted."""
    trig = _triggers(_wf())
    assert "workflow_dispatch" in trig, "the registry build can no longer be run at all"
    assert "push" not in trig, (
        "the registry build runs automatically again — it costs ~20 minutes a "
        "release and this deployment builds its images on the server"
    )


def test_it_still_builds_amd64():
    """★The whole reason this workflow exists. Do not simplify the platform away."""
    src = WF.read_text(encoding="utf-8")
    assert "linux/amd64" in src, "the workflow no longer targets the servers' architecture"
    assert src.count("platforms: linux/amd64") >= 2, (
        "both the build and the push must pin the platform, or the pushed "
        "image can differ from the one that was scanned"
    )


def test_a_tag_build_is_named_after_the_version():
    src = WF.read_text(encoding="utf-8")
    assert "tr -d '[:space:]' < VERSION" in src, (
        "a release image is not named after the VERSION file, so a deployment "
        "cannot predict the tag to pull"
    )


def test_a_mismatched_tag_refuses_to_build():
    """★Version drift is caught at the build, not discovered after deploy."""
    src = WF.read_text(encoding="utf-8")
    assert 'does not match VERSION' in src, (
        "the workflow will happily name an image after a release the tree is not"
    )
    assert "exit 1" in src, "the mismatch is reported but does not stop the build"


def test_the_push_step_runs_only_on_success():
    src = WF.read_text(encoding="utf-8")
    push_at = src.index("Push Docker image")
    window = src[push_at:push_at + 400]
    assert "if: success()" in window, (
        "a failed build could still publish an image"
    )
