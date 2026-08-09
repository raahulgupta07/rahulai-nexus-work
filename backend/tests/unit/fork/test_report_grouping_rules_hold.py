"""The sidebar's report grouping has five rules that are invisible when broken.

`frontend/utils/reportGrouping.ts` partitions the recent-reports list into the
headings the nav renders. Every rule below fails silently if it is edited away:
a pinned report quietly filed under "Older" still appears, just in the wrong
place; a missing timestamp fallback drops never-run reports to the bottom; a
rolling `Date.now() - 86400000` boundary calls an 11pm report "today" for the
next 23 hours. None of that raises, and none of it is obvious in review.

★These read the `.ts` file as TEXT, which is all a Python suite can do — see the
note in CLAUDE.md about the 169 frontend guards. A text scan cannot execute
`groupReports`, so it cannot prove a report lands in the right bucket; it can
only prove the code that decides still says what it is supposed to say. That is
a real limit. It is still worth having, because each defect here is a rule that
disappeared from the source entirely, and disappearance is exactly what a text
scan sees. The behavioural half belongs in a browser/vitest suite.
"""

import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[4]
GROUPING = REPO / "frontend" / "utils" / "reportGrouping.ts"

GROUP_KEYS = ["pinned", "today", "yesterday", "prev7", "prev30", "older"]

# A bucket boundary compares a report's time against the start-of-day anchor.
TIME_COMPARISON = re.compile(r">=\s*startOfToday")

# The rolling-window form this file must NOT use: an age computed straight off
# the current instant instead of a local midnight.
ROLLING_BOUNDARY = re.compile(r"Date\.now\(\)\s*-|now\.getTime\(\)\s*-\s*\d")


def _source() -> str:
    assert GROUPING.exists(), (
        f"{GROUPING} is missing — the grouping util was moved or renamed, and "
        "every rule this file pins went with it"
    )
    return GROUPING.read_text(encoding="utf-8")


def strip_comments(src: str) -> str:
    """Drop `/* … */` and `// …` comments, keeping string literals intact.

    A `//` inside a URL is left alone (it is preceded by `:`), which is enough
    for a file that has no other slash-heavy literals.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?<!:)//[^\n]*", "", src)


def check_all_group_keys_declared(src: str) -> None:
    for key in GROUP_KEYS:
        assert f"'{key}'" in src or f'"{key}"' in src, (
            f"group key {key!r} is not declared — the nav renders six groups"
        )


def check_pinned_is_matched_before_the_time_buckets(src: str) -> None:
    pinned = src.find("return 'pinned'")
    assert pinned != -1, "nothing returns the 'pinned' bucket"

    first_age_test = TIME_COMPARISON.search(src)
    assert first_age_test, (
        "no start-of-day comparison found — the age buckets are not being "
        "computed from the local-midnight anchor"
    )
    assert pinned < first_age_test.start(), (
        "the pinned check runs AFTER an age comparison, so a pinned report "
        "older than 30 days falls into 'older' instead of 'pinned'"
    )

    today = src.find("return 'today'")
    assert today != -1, "nothing returns the 'today' bucket"
    assert pinned < today, "'pinned' must be decided before 'today'"


def check_timestamp_fallback_chain(src: str) -> None:
    # ★Scan CODE, not prose. Every one of these field names is also discussed in
    # this file's comments, so an unstripped scan passes on a version that reads
    # only `updated_at` — proven: the check could not fail before this line
    # existed. Stripping string literals instead would be the opposite mistake,
    # since the chain IS a list of string literals.
    src = strip_comments(src)
    positions = []
    for field in ("last_activity_at", "updated_at", "created_at"):
        idx = src.find(field)
        assert idx != -1, (
            f"{field!r} is not read — a report with no newer timestamp loses "
            "its place in the sidebar"
        )
        positions.append(idx)
    assert positions == sorted(positions), (
        "the fallback chain is out of order; it must try last_activity_at, "
        "then updated_at, then created_at"
    )


def check_empty_groups_are_filtered(src: str) -> None:
    assert re.search(r"items\.length\s*>\s*0", src), (
        "empty groups are not filtered out — a heading with nothing under it "
        "reads as a loading failure"
    )


def check_boundaries_come_from_a_local_start_of_day(src: str) -> None:
    assert "setHours(0, 0, 0, 0)" in src or "setHours(0,0,0,0)" in src, (
        "no local start-of-day anchor — 'yesterday' is a calendar word, not a "
        "rolling 24-hour window"
    )
    rolling = ROLLING_BOUNDARY.search(src)
    assert rolling is None, (
        "a boundary is computed from the current instant instead of a local "
        f"midnight: {src[rolling.start():rolling.start() + 40]!r}"
        if rolling
        else ""
    )


CHECKS = (
    check_all_group_keys_declared,
    check_pinned_is_matched_before_the_time_buckets,
    check_timestamp_fallback_chain,
    check_empty_groups_are_filtered,
    check_boundaries_come_from_a_local_start_of_day,
)


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_grouping_rule_holds(check):
    check(_source())
