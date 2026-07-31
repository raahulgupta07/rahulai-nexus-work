"""Auto learn: one worker that keeps every opted-in agent current.

An agent went stale in two independent ways, and said nothing about either. A
file it never read contributes no rules, no knowledge and no table — it is
attached and inert. And when a table moves, the overview goes on naming the one
that is gone. Training only ever ran when a person asked, or as a side effect of
setup, so nothing closed either gap.

This sweep closes both, in one pass, for agents whose owner turned Auto learn
on — and only those, because it spends model calls.
"""
import inspect
import textwrap

import pytest

from app.services import auto_learn, training_drift


def _src(fn) -> str:
    return textwrap.dedent(inspect.getsource(fn))


# ── it only touches agents that asked for it ────────────────────────────────

def test_the_sweep_acts_only_on_agents_that_opted_in():
    """The decisive property. A scheduler existing is not consent: an agent
    nobody automated must not start spending model calls because a tick fired."""
    src = _src(auto_learn.sweep_auto_learn)
    assert "MODE_AUTO" in src
    assert "mode_of(a)" in src


def test_the_sweep_is_bounded_per_tick():
    """One pass over hundreds of agents would enqueue an unbounded amount of
    model work. Being slow is fine; being unbounded is not."""
    assert auto_learn.MAX_AGENTS_PER_SWEEP > 0
    src = _src(auto_learn.sweep_auto_learn)
    assert "MAX_AGENTS_PER_SWEEP" in src


def test_the_remainder_is_reported_not_dropped():
    """Silently processing the first five of two hundred looks identical to
    processing all of them."""
    src = _src(auto_learn.sweep_auto_learn)
    assert "left for the next pass" in src


def test_the_sweep_cannot_raise():
    """It shares a tick with other jobs; a failure costs freshness, not
    correctness."""
    src = _src(auto_learn.sweep_auto_learn)
    assert "except Exception" in src


# ── what it actually does, and in which order ──────────────────────────────

def test_it_reads_unsorted_files_before_it_rereads_the_tables():
    """Order is load-bearing. Sorting a file can create a table, so doing it
    first means the re-learn describes the schema that sorting produced. The
    other way round, every new file would take two passes to be understood."""
    src = _src(auto_learn.auto_learn_agent)
    assert src.index("reingest_file") < src.index("auto_decision")


def test_sorting_files_is_what_adds_instructions():
    """The ask was for the agent to add instructions, not only to rewrite its
    overview. Instructions, skills and knowledge all come from sorting a file —
    so an unread file is a missing instruction, and this is where it is found."""
    src = _src(auto_learn.auto_learn_agent)
    assert "reingest_file" in src
    assert "files_sorted" in src


def test_only_files_nothing_has_used_are_touched():
    """`source_kind == "upload"` is the product's own word for a file that backs
    no table, no instruction and no knowledge. Re-sorting a file that already
    produced something would duplicate it."""
    src = _src(auto_learn._unread_files)
    assert '"upload"' in src


def test_a_single_bad_file_does_not_stop_the_agent():
    """Otherwise one unreadable document freezes every later file and the
    re-learn behind them."""
    src = _src(auto_learn.auto_learn_agent)
    files_pass = src[:src.index("auto_decision")]
    assert files_pass.count("except Exception") >= 2


def test_the_quiet_period_marker_is_written_even_when_it_declines():
    """A refusal still carries work: the first sighting of a change is what
    starts the clock. Drop it and the clock never starts, so an agent in auto
    mode is never retrained at all — a bug that looks exactly like the feature
    being off."""
    src = _src(auto_learn.auto_learn_agent)
    assert 'decision.get("mark")' in src
    assert "training_settings = settings" in src


def test_a_run_is_counted_against_the_daily_ceiling():
    src = _src(auto_learn.auto_learn_agent)
    assert "note_auto_run" in src


def test_the_learn_is_awaited_not_fired_into_the_background():
    """The sweep IS a background task. Firing another from it would mean nothing
    could observe when the learn finished — and the counter below would be
    written before the work happened."""
    src = _src(auto_learn.auto_learn_agent)
    assert "relearn_overview_now" in src
    assert "schedule_overview_relearn" not in src


def test_it_runs_as_the_agent_owner():
    """A learn has to run as somebody. On a per-user connector the tables it can
    see — and therefore the overview it writes — depend on whose account asks."""
    src = _src(auto_learn.sweep_auto_learn)
    assert "owner_user_id" in src


def test_every_agent_reports_what_was_done():
    """A sweep that skips silently cannot be told from one that never ran."""
    src = _src(auto_learn.auto_learn_agent)
    assert '"files_sorted": 0' in src and '"retrained": False' in src
    assert '"reason"' in src


# ── it is actually scheduled ────────────────────────────────────────────────

def test_the_sweep_is_registered_on_the_scheduler_leader():
    """Unscheduled, it is indistinguishable from absent — and without the leader
    guard every worker would run it at once."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[3] / "main.py").read_text()
    assert "sweep_auto_learn" in src
    assert "auto_learn_sweep" in src
    assert "is_scheduler_leader" in src[:src.index("auto_learn_sweep")]


# ── the switch ──────────────────────────────────────────────────────────────

def test_the_ui_offers_one_switch_called_auto_learn():
    """One word, one control. Three modes on screen would ask the user to choose
    between "tell me" and "do it" and "do nothing", when the third is only ever
    reached by turning the notice off — which costs nothing to leave on."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[4] / "frontend" / "components"
           / "KnowledgeExplorer.vue").read_text()
    assert "agentsPage.autoLearn" in src
    assert "toggleAutoLearn" in src


def test_turning_it_off_leaves_the_notice_on():
    """Off is `notify`, not silence. Noticing compares two stored values and
    costs nothing, so there is no reason to stop watching just because nobody
    wants model calls spent unasked."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[4] / "frontend" / "components"
           / "KnowledgeExplorer.vue").read_text()
    body = src[src.index("const toggleAutoLearn"):]
    assert "'notify'" in body[:400]
    assert "'manual'" not in body[:400]


def test_the_switch_strings_are_translatable():
    import json
    from pathlib import Path

    keys = json.loads((Path(__file__).resolve().parents[4] / "locales" / "en.json").read_text())["agentsPage"]
    for k in ("autoLearn", "autoLearnOnHint", "autoLearnOffHint",
              "autoLearnEnabled", "autoLearnDisabled"):
        assert k in keys


def test_the_hints_say_what_it_will_do_not_what_it_is():
    """"Auto learn" alone does not tell anyone what changes if they flip it."""
    import json
    from pathlib import Path

    keys = json.loads((Path(__file__).resolve().parents[4] / "locales" / "en.json").read_text())["agentsPage"]
    assert "rewrites" in keys["autoLearnOnHint"]
    assert "files" in keys["autoLearnOnHint"]
    assert "waits" in keys["autoLearnOffHint"]
