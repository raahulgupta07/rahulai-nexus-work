"""Release notes written as prose must still reach "What's New".

The parser recognised bullets only. A version whose notes were paragraphs
produced a heading with nothing under it — 0.0.510.5, .6 and .7 all shipped
that way — and the opening paragraph of every other version, the one saying
what the release is about, was discarded before its first bullet.

Nothing errored and nothing logged. The endpoint returned `entries: []` and the
modal faithfully rendered it.
"""

from app.routes.changelog import _parse_changelog


def _by_version(md):
    return {v["version"]: v for v in _parse_changelog(md)}


PROSE = """# Release Notes

## Version 0.0.510.7 (August 2, 2026)

**Your own private instruction is yours.** A person could write a private
instruction and then be told they lacked permission to change it.

10 new tests.

## Version 0.0.510.4 (August 2, 2026)

Three faults in the Files panel, all reported as "the upload failed".

- **A file said it had not been ingested.** The panel used the upload reply.
- **Nothing else on the screen was told.**
"""


def test_a_version_written_as_paragraphs_is_not_empty():
    """The reported bug: three releases rendered as a heading and blank space."""
    v = _by_version(PROSE)["0.0.510.7"]

    assert len(v["entries"]) == 2
    assert v["entries"][0].startswith("**Your own private instruction is yours.**")
    assert v["entries"][1] == "10 new tests."


def test_a_wrapped_paragraph_stays_one_entry():
    """A line break inside a paragraph is typography, not a second note."""
    entry = _by_version(PROSE)["0.0.510.7"]["entries"][0]

    assert "instruction and then was" not in entry  # no lost join
    assert "\n" not in entry
    assert "permission to change it." in entry


def test_the_opening_paragraph_before_the_bullets_survives():
    """Dropped silently before: the sentence saying what the release is about."""
    entries = _by_version(PROSE)["0.0.510.4"]["entries"]

    assert entries[0].startswith("Three faults in the Files panel")
    assert len(entries) == 3


def test_bullets_are_unchanged():
    """375 already-published versions are bullets; their rendering must not move."""
    entries = _by_version(PROSE)["0.0.510.4"]["entries"]

    assert entries[1] == "**A file said it had not been ingested.** The panel used the upload reply."
    assert entries[2] == "**Nothing else on the screen was told.**"


def test_an_indented_bullet_stays_part_of_the_entry_above_it():
    """A sub-point is not a top-level note — the bullet pattern stays anchored."""
    md = (
        "## Version 1.0.0 (January 1, 2026)\n"
        "\n"
        "- Parent point.\n"
        "  - Sub point.\n"
    )

    entries = _by_version(md)["1.0.0"]["entries"]

    assert entries == ["Parent point. - Sub point."]


def test_a_sub_heading_is_structure_not_a_note():
    md = (
        "## Version 1.0.0 (January 1, 2026)\n"
        "\n"
        "### Security\n"
        "\n"
        "- Something was fixed.\n"
    )

    assert _by_version(md)["1.0.0"]["entries"] == ["Something was fixed."]


def test_every_published_version_has_notes():
    """The end-to-end assertion: no version in the real file renders blank.

    This is the check that would have caught the bug the day it shipped.
    """
    from pathlib import Path

    changelog = Path(__file__).resolve().parents[4] / "CHANGELOG.md"
    if not changelog.is_file():  # pragma: no cover - container layout
        return

    empty = [
        v["version"]
        for v in _parse_changelog(changelog.read_text(encoding="utf-8"))
        if not v["entries"]
    ]

    assert empty == [], f"versions rendering with no release notes: {empty}"
