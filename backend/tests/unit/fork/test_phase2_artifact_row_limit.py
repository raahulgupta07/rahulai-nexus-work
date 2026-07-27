"""PHASE 2 — one cap was serving two consumers that need different numbers.

Found by: the DEF-004 dashboard. A query result was truncated exactly once, by
``limit_row_count`` (default 1000), and that single truncated copy was persisted
— so the chat table preview, the LLM's data preview AND the dashboard all read
from it. The cut is ``df.head()``, a PREFIX in the query's own ORDER BY rather
than a sample, so a month-ordered result silently lost its most recent periods
and the chart drawn from it understated its own totals (56.4B against a true
98.9B, 10 of 17 months).

A table on screen is unreadable past a few hundred rows; a chart is comfortable
with tens of thousands. So the two consumers now get their own caps:
``artifact_row_limit`` (default 10000) bounds what a dashboard or chart may be
built from, and ``limit_row_count`` keeps bounding what a table preview shows.
``format_df_for_widget`` picks between them with ``for_artifact``, which is
opt-in precisely so that every existing caller keeps its behavior byte-identical.

The critical test in here is the boring one: the DEFAULT path still yields 1000
rows. If that ever changes, the browser gets a table it cannot render and the
model gets a context window full of rows it did not ask for — the split would
have traded one regression for two.

Contract these tests pin:
  * the default path is unchanged: 1000 rows, declared truncated, true total
  * ``for_artifact=True`` uses the larger cap and marks nothing when complete
  * the artifact cap is still a cap — past it, truncation is declared again
  * an explicit ``max_rows`` overrides both caps on either path
  * ``artifact_row_limit`` defaults to 10000 and carries a description
    (a FeatureConfig without one 500s every settings read)
  * ``limit_row_count`` is still 1000 and otherwise untouched
  * a value <= 0 sets state DISABLED on the new setting, as on the old one
"""
import pandas as pd
import pytest

from app.schemas.organization_settings_schema import (
    FeatureConfig,
    FeatureState,
    OrganizationSettingsConfig,
)

DISPLAY_CAP = 1000
ARTIFACT_CAP = 10_000


PERIODS = 12


def _frame(n):
    """A time-ORDERED frame — the shape whose tail a prefix silently deletes.

    The period advances monotonically down the rows, exactly as a query with
    `ORDER BY month` returns them, so `head()` drops whole periods rather than
    thinning every period evenly.
    """
    return pd.DataFrame(
        [
            {
                "period": f"2025-{(i * PERIODS // n) + 1:02d}",
                "branch": f"B{i % 7}",
                "amount": i,
            }
            for i in range(n)
        ]
    )


class _Executor:
    """Exercises format_df_for_widget without the executor's DB/LLM dependencies."""

    from app.ai.code_execution.code_execution import StreamingCodeExecutor as _S

    format_df_for_widget = _S.format_df_for_widget
    get_df_info = _S.get_df_info

    def __init__(self):
        self.organization_settings = None  # -> schema defaults for both caps


_UNSET = object()


def _widget(df, max_rows=None, for_artifact=_UNSET):
    """Call the formatter; `for_artifact` left unset means "as every old caller"."""
    if for_artifact is _UNSET:
        return _Executor().format_df_for_widget(df=df, max_rows=max_rows)
    return _Executor().format_df_for_widget(
        df=df, max_rows=max_rows, for_artifact=for_artifact
    )


# --- 1. the regression that matters: the display path is untouched -----------

BIG = _frame(5000)


def test_phase2_default_path_still_truncates_at_the_display_cap():
    """If this ever changes, the browser AND the model's context regress."""
    out = _widget(BIG)
    assert len(out["rows"]) == DISPLAY_CAP
    assert out["rows_truncated"] is True
    assert out["rows_total"] == 5000


def test_phase2_for_artifact_false_is_the_same_as_absent():
    absent = _widget(BIG)
    explicit = _widget(BIG, for_artifact=False)
    assert len(explicit["rows"]) == len(absent["rows"]) == DISPLAY_CAP
    assert explicit["rows_truncated"] is True
    assert explicit["rows_total"] == absent["rows_total"] == 5000


def test_phase2_display_path_reports_the_true_total_not_the_prefix():
    out = _widget(BIG)
    assert out["info"]["total_rows"] == 5000
    assert out["rows_total"] != len(out["rows"])


def test_phase2_small_frame_is_untouched_on_the_display_path():
    out = _widget(_frame(120))
    assert len(out["rows"]) == 120
    assert "rows_truncated" not in out
    assert "rows_total" not in out


# --- 2. the artifact path -----------------------------------------------------


def test_phase2_artifact_path_keeps_the_whole_frame():
    """5,000 rows is nothing to a chart and everything to a monthly total."""
    out = _widget(BIG, for_artifact=True)
    assert len(out["rows"]) == 5000


def test_phase2_artifact_path_declares_no_truncation_when_complete():
    """A false alarm here would trip the Phase 1 gate on complete data."""
    out = _widget(BIG, for_artifact=True)
    assert "rows_truncated" not in out
    assert "rows_total" not in out


def test_phase2_artifact_path_covers_every_period_the_display_path_lost():
    """The defect stated as data, not as row counts."""
    display = _widget(BIG)
    artifact = _widget(BIG, for_artifact=True)
    assert len({r["period"] for r in display["rows"]}) < PERIODS
    assert len({r["period"] for r in artifact["rows"]}) == PERIODS


def test_phase2_artifact_path_still_reports_the_true_total():
    out = _widget(BIG, for_artifact=True)
    assert out["info"]["total_rows"] == 5000


def test_phase2_artifact_path_empty_frame_is_safe():
    out = _widget(BIG.iloc[0:0], for_artifact=True)
    assert out["rows"] == []
    assert "rows_truncated" not in out


# --- 3. the artifact cap is still a cap --------------------------------------

OVERSIZE = _frame(ARTIFACT_CAP + 500)


def test_phase2_frame_beyond_the_artifact_cap_is_truncated():
    out = _widget(OVERSIZE, for_artifact=True)
    assert len(out["rows"]) == ARTIFACT_CAP
    assert out["rows_truncated"] is True


def test_phase2_beyond_the_artifact_cap_the_total_is_the_real_one():
    out = _widget(OVERSIZE, for_artifact=True)
    assert out["rows_total"] == ARTIFACT_CAP + 500
    assert out["info"]["total_rows"] == ARTIFACT_CAP + 500


def test_phase2_the_two_caps_are_different_numbers():
    """The whole point: same frame, two answers."""
    assert len(_widget(OVERSIZE)["rows"]) == DISPLAY_CAP
    assert len(_widget(OVERSIZE, for_artifact=True)["rows"]) == ARTIFACT_CAP


# --- 4. an explicit max_rows wins over both ----------------------------------


@pytest.mark.parametrize("for_artifact", [False, True])
def test_phase2_explicit_max_rows_overrides_the_cap(for_artifact):
    out = _widget(BIG, max_rows=37, for_artifact=for_artifact)
    assert len(out["rows"]) == 37
    assert out["rows_truncated"] is True
    assert out["rows_total"] == 5000


def test_phase2_explicit_max_rows_above_the_display_cap_is_honored():
    """Proof it is an override, not a second floor."""
    out = _widget(BIG, max_rows=2500)
    assert len(out["rows"]) == 2500


def test_phase2_explicit_max_rows_above_the_artifact_cap_is_honored():
    out = _widget(OVERSIZE, max_rows=ARTIFACT_CAP + 500, for_artifact=True)
    assert len(out["rows"]) == ARTIFACT_CAP + 500
    assert "rows_truncated" not in out


# --- 5. the settings themselves ----------------------------------------------


def _config():
    return OrganizationSettingsConfig()


def test_phase2_artifact_row_limit_exists():
    assert hasattr(_config(), "artifact_row_limit")


def test_phase2_artifact_row_limit_defaults_to_ten_thousand():
    assert _config().artifact_row_limit.value == ARTIFACT_CAP


def test_phase2_artifact_row_limit_has_a_description():
    """★ FeatureConfig REQUIRES a description — a partial write 500s EVERY
    settings read, which takes the whole settings page down, not just this row."""
    cfg = _config().artifact_row_limit
    assert isinstance(cfg.description, str)
    assert cfg.description.strip()


def test_phase2_artifact_row_limit_is_named_and_editable():
    cfg = _config().artifact_row_limit
    assert cfg.name.strip()
    assert cfg.editable is True


def test_phase2_artifact_row_limit_starts_enabled():
    assert _config().artifact_row_limit.state != FeatureState.DISABLED


def test_phase2_limit_row_count_is_untouched():
    """The display cap keeps its old value; nothing about it was renamed."""
    cfg = _config().limit_row_count
    assert cfg.value == DISPLAY_CAP
    assert isinstance(cfg.description, str) and cfg.description.strip()
    assert cfg.editable is True


def test_phase2_the_artifact_cap_is_the_larger_default():
    c = _config()
    assert c.artifact_row_limit.value > c.limit_row_count.value


# --- 6. the "<= 0 means no limit" validator ----------------------------------


def _feature(value, name="Row limit under test"):
    return FeatureConfig(
        value=value,
        name=name,
        description="How many rows this consumer may take.",
        is_lab=False,
        editable=True,
    )


@pytest.mark.parametrize("value", [0, -1, -10_000, 0.0])
def test_phase2_non_positive_artifact_limit_is_disabled(value):
    cfg = OrganizationSettingsConfig(artifact_row_limit=_feature(value))
    assert cfg.artifact_row_limit.state == FeatureState.DISABLED


@pytest.mark.parametrize("value", [1, 500, 10_000, 250_000])
def test_phase2_positive_artifact_limit_stays_enabled(value):
    cfg = OrganizationSettingsConfig(artifact_row_limit=_feature(value))
    assert cfg.artifact_row_limit.state != FeatureState.DISABLED
    assert cfg.artifact_row_limit.value == value


@pytest.mark.parametrize("value", [0, -1])
def test_phase2_the_same_rule_still_holds_for_limit_row_count(value):
    """Mirrored deliberately — two caps that behave differently at 0 is a trap."""
    cfg = OrganizationSettingsConfig(limit_row_count=_feature(value))
    assert cfg.limit_row_count.state == FeatureState.DISABLED
