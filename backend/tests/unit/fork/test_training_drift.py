"""An agent must notice when its overview stops describing its data.

The overview is the agent's briefing, loaded on every question. CRM's names six
month tables and tells the model to UNION them. Remove one and the schema
updates while the overview does not — so the agent keeps following a description
of data that has moved on, and a wrong answer built on a stale briefing looks
exactly like a right one.

Training here has only ever followed what you ADD: first run, first model key, an
upload, a per-user sign-in. Nothing watched for change. This is the missing half,
and deliberately the cheap half — a fingerprint recorded at training time
compared against the schema now. No model call, nothing to crawl, which is what
makes noticing affordable to leave on by default.
"""
from datetime import datetime

import pytest

from app.services import training_drift as td


class _Col:
    def __init__(self, name, dtype):
        self.name, self.dtype = name, dtype


class _Table:
    def __init__(self, name, cols, is_active=True):
        self.name = name
        self.columns = [_Col(n, d) for n, d in cols.items()]
        self.is_active = is_active


class _DS:
    def __init__(self, sig=None, trained_at=None, settings=None):
        self.id = "ds-1"
        self.trained_schema_signature = sig
        self.trained_at = trained_at
        self.training_settings = settings


JAN = _Table("mm_conso_data_report_jan_25", {"slip_no": "BIGINT", "outlet": "VARCHAR"})
FEB = _Table("mm_conso_data_report_feb_25", {"slip_no": "BIGINT", "outlet": "VARCHAR"})


# ── what counts as drift ────────────────────────────────────────────────────

def test_nothing_changed_is_not_drift():
    shape = td.schema_shape([JAN, FEB])
    assert td.diff(shape, shape)["stale"] is False


def test_a_removed_table_is_drift():
    """The case that is now one click away. Remove a file, its table goes — and
    the overview still names it and still says to UNION six of them."""
    before = td.schema_shape([JAN, FEB])
    after = td.schema_shape([JAN])
    d = td.diff(before, after)
    assert d["stale"] is True
    assert d["tables_removed"] == ["mm_conso_data_report_feb_25"]
    assert "1 table removed" in td.summarize(d)


def test_an_added_column_is_drift():
    before = td.schema_shape([JAN])
    after = td.schema_shape([_Table(JAN.name, {"slip_no": "BIGINT", "outlet": "VARCHAR",
                                               "hcp_partner": "VARCHAR"})])
    d = td.diff(before, after)
    assert d["columns_added"] == ["mm_conso_data_report_jan_25.hcp_partner"]


def test_a_retyped_column_is_drift():
    """CRM's own overview warns that a usage measure is BIGINT in some months and
    DOUBLE in others, and tells the model to cast before aggregating. A silent
    retype makes that instruction wrong, which is worse than absent."""
    before = td.schema_shape([_Table("t", {"scoops": "BIGINT"})])
    after = td.schema_shape([_Table("t", {"scoops": "DOUBLE"})])
    d = td.diff(before, after)
    assert d["columns_retyped"] == ["t.scoops BIGINT→DOUBLE"]
    assert "changed type" in td.summarize(d)


def test_column_order_is_not_drift():
    """Introspection order is not stable across runs. If it counted, every agent
    would be permanently stale and the notice would mean nothing."""
    a = td.schema_shape([_Table("t", {"x": "INT", "y": "TEXT"})])
    b = td.schema_shape([_Table("t", {"y": "TEXT", "x": "INT"})])
    assert td.signature(a) == td.signature(b)
    assert td.diff(a, b)["stale"] is False


def test_an_inactive_table_is_ignored():
    """An inactive table is one the agent was told not to use, so it is absent
    from the overview. Counting it would mean an agent could never be current
    while any unused table existed."""
    shape = td.schema_shape([JAN, _Table("scratch", {"a": "INT"}, is_active=False)])
    assert list(shape) == ["mm_conso_data_report_jan_25"]


def test_row_counts_are_not_part_of_the_shape():
    """Deliberate. The overview describes what the data IS, not how much of it
    there is — retraining on growth would fire constantly, change the text not at
    all, and teach people the notice is noise."""
    t = _Table("t", {"x": "INT"})
    t.no_rows = 10
    a = td.schema_shape([t])
    t.no_rows = 9_000_000
    assert td.signature(a) == td.signature(td.schema_shape([t]))


# ── an agent that was never recorded ────────────────────────────────────────

def test_never_recorded_is_unknown_not_stale():
    """Every agent predating this feature has no fingerprint. Reporting those as
    out of date would put a warning on agents that are perfectly current, and a
    warning that is usually wrong is one people stop reading."""
    d = td.diff(None, td.schema_shape([JAN]))
    assert d["known"] is False
    assert d["stale"] is False


def test_unknown_is_distinguishable_from_current():
    """The UI has to tell "we cannot say" from "it is fine", so `known` must not
    be inferred from `stale` being false."""
    unknown = td.drift_for(_DS(sig=None), [JAN])
    shape = td.schema_shape([JAN])
    current = td.drift_for(_DS(sig=td.encode(shape)), [JAN])
    assert unknown["known"] is False and current["known"] is True
    assert unknown["stale"] == current["stale"] == False


@pytest.mark.parametrize("junk", ["", "not json", "[]", '{"v":1}', None])
def test_a_corrupt_fingerprint_reads_as_unknown(junk):
    """Rather than raising and taking the agent page down with it."""
    assert td.decode(junk) is None
    assert td.drift_for(_DS(sig=junk), [JAN])["known"] is False


# ── what the stored form has to carry ───────────────────────────────────────

def test_the_shape_is_stored_not_only_its_hash():
    """A hash answers "has this changed?" but not "what changed?" — and that
    difference is the difference between a warning people act on and one they
    dismiss."""
    shape = td.schema_shape([JAN, FEB])
    assert td.decode(td.encode(shape)) == shape


# ── the policy ──────────────────────────────────────────────────────────────

def test_noticing_is_on_by_default_and_retraining_is_not():
    """Noticing is a comparison of things already stored. Re-learning is a model
    call every time the data moves. Only one of those is safe to assume."""
    assert td.DEFAULT_MODE == td.MODE_NOTIFY
    assert td.mode_of(_DS()) == td.MODE_NOTIFY


@pytest.mark.parametrize("settings", [None, {}, {"mode": "nonsense"}, "not a dict"])
def test_an_unreadable_policy_falls_back_to_the_default(settings):
    assert td.mode_of(_DS(settings=settings)) in td.VALID_MODES


def test_the_status_payload_carries_what_the_page_needs():
    shape = td.schema_shape([JAN, FEB])
    ds = _DS(sig=td.encode(shape), trained_at=datetime(2026, 7, 30, 14, 11))
    status = td.drift_for(ds, [JAN])
    assert status["stale"] is True
    assert status["summary"]
    assert status["trained_at"].startswith("2026-07-30")
    assert status["active_tables"] == 1
    assert status["mode"] == td.MODE_NOTIFY


def test_a_current_agent_says_nothing():
    """An empty summary is what lets the UI stay silent rather than render a
    reassurance nobody asked for."""
    shape = td.schema_shape([JAN])
    assert td.drift_for(_DS(sig=td.encode(shape)), [JAN])["summary"] == ""


# ── the routes ──────────────────────────────────────────────────────────────

def test_the_status_route_only_needs_view():
    """Training itself needs only `view`, because on a per-user connector each
    member can teach only their own overview. Asking more to merely READ whether
    it is stale would hide the notice from those same people."""
    import inspect

    import app.routes.data_source as routes

    src = inspect.getsource(routes.get_training_status)
    assert "'view'" in src or '"view"' in src


def test_changing_the_policy_needs_manage():
    """Unlike training, this decides what happens on everyone's behalf —
    including whether the agent may spend model calls unasked."""
    import inspect

    import app.routes.data_source as routes

    src = inspect.getsource(routes.update_training_settings)
    assert "'manage'" in src or '"manage"' in src


def test_an_unknown_mode_is_refused():
    import inspect

    import app.routes.data_source as routes

    src = inspect.getsource(routes.update_training_settings)
    assert "status_code=400" in src


def test_the_policy_write_reassigns_the_json_column():
    """SQLAlchemy does not track in-place edits to a JSON column; a mutated dict
    is silently dropped at commit. This has bitten this codebase before."""
    import inspect

    import app.routes.data_source as routes

    src = inspect.getsource(routes.update_training_settings)
    assert "ds.training_settings = settings" in src


# ── the notice reaches the screen ───────────────────────────────────────────

def test_the_agent_page_shows_the_drift_notice():
    """Detection nobody sees is detection that has not shipped."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[4] / "frontend" / "components"
           / "KnowledgeExplorer.vue").read_text()
    assert "training-status" in src
    assert "trainingStatus?.stale" in src, (
        "the banner is not gated on `stale`, so it would also appear for agents "
        "whose drift is merely unknown"
    )
    assert "agentsPage.driftTitle" in src


def test_the_notice_offers_the_fix_next_to_the_problem():
    """A warning with no action attached is one people learn to scroll past."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[4] / "frontend" / "components"
           / "KnowledgeExplorer.vue").read_text()
    banner = src[src.index("trainingStatus?.stale"):]
    assert "trainAgent(agentView.agentId)" in banner[:2200]


def test_the_notice_refreshes_with_the_rest_of_the_header():
    """Otherwise it survives the training that resolved it, and the user is told
    to fix something they have just fixed."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[4] / "frontend" / "components"
           / "KnowledgeExplorer.vue").read_text()
    body = src[src.index("const refreshAgent"):src.index("const trainAgent")]
    assert "loadTrainingStatus(id)" in body


def test_the_notice_strings_are_translatable():
    import json
    from pathlib import Path

    keys = json.loads((Path(__file__).resolve().parents[4] / "locales" / "en.json").read_text())["agentsPage"]
    for k in ("driftTitle", "driftSince", "trainNow", "dismiss"):
        assert k in keys
    assert "{changes}" in keys["driftSince"], "the notice does not name what changed"


# ── auto mode ───────────────────────────────────────────────────────────────

from datetime import timedelta  # noqa: E402

NOW = datetime(2026, 7, 30, 15, 0)


def _auto(**cfg):
    base = {"mode": "auto"}
    base.update(cfg)
    return base


def test_auto_does_not_run_for_an_agent_that_is_not_in_auto_mode():
    ds = _DS(sig=td.encode(td.schema_shape([JAN, FEB])), settings={"mode": "notify"})
    d = td.auto_decision(ds, [JAN], NOW)
    assert d["run"] is False and "auto" in d["reason"]


def test_auto_does_not_run_when_nothing_was_ever_recorded():
    """No fingerprint means no evidence of drift. Spending a model call on an
    agent that may be perfectly current is not something to do unasked."""
    d = td.auto_decision(_DS(sig=None, settings=_auto()), [JAN], NOW)
    assert d["run"] is False


def test_auto_does_not_run_when_the_agent_is_current():
    shape = td.schema_shape([JAN])
    d = td.auto_decision(_DS(sig=td.encode(shape), settings=_auto()), [JAN], NOW)
    assert d["run"] is False and d["reason"] == "up to date"


def test_the_first_sighting_only_starts_the_clock():
    """A change is not acted on the moment it appears. A migration arrives as a
    stream of separate changes seconds apart; running on the first would describe
    a schema that is still moving."""
    ds = _DS(sig=td.encode(td.schema_shape([JAN, FEB])), settings=_auto())
    d = td.auto_decision(ds, [JAN], NOW)
    assert d["run"] is False
    assert d["mark"]["drift_seen_at"] == NOW.isoformat()
    assert d["mark"]["drift_sig"]


def test_it_runs_once_the_schema_has_settled():
    current_sig = td.signature(td.schema_shape([JAN]))
    ds = _DS(sig=td.encode(td.schema_shape([JAN, FEB])),
             settings=_auto(drift_sig=current_sig,
                            drift_seen_at=(NOW - timedelta(minutes=45)).isoformat()))
    d = td.auto_decision(ds, [JAN], NOW)
    assert d["run"] is True
    assert d["summary"]


def test_it_keeps_waiting_inside_the_quiet_period():
    current_sig = td.signature(td.schema_shape([JAN]))
    ds = _DS(sig=td.encode(td.schema_shape([JAN, FEB])),
             settings=_auto(drift_sig=current_sig,
                            drift_seen_at=(NOW - timedelta(minutes=5)).isoformat()))
    assert td.auto_decision(ds, [JAN], NOW)["run"] is False


def test_a_further_change_restarts_the_clock():
    """The marker is keyed to the shape it was taken for. Otherwise a migration
    that keeps going would be described half-finished, because the timer started
    on its first step."""
    stale_marker = td.signature({"something": "else"})
    ds = _DS(sig=td.encode(td.schema_shape([JAN, FEB])),
             settings=_auto(drift_sig=stale_marker,
                            drift_seen_at=(NOW - timedelta(hours=3)).isoformat()))
    d = td.auto_decision(ds, [JAN], NOW)
    assert d["run"] is False
    assert d["mark"]["drift_seen_at"] == NOW.isoformat()


def test_the_daily_ceiling_stops_it():
    """A connector rewriting its schema in a loop must not be able to spend all
    day describing itself."""
    current_sig = td.signature(td.schema_shape([JAN]))
    ds = _DS(sig=td.encode(td.schema_shape([JAN, FEB])),
             settings=_auto(drift_sig=current_sig,
                            drift_seen_at=(NOW - timedelta(hours=2)).isoformat(),
                            auto_day=NOW.date().isoformat(),
                            auto_runs=td.DEFAULT_MAX_PER_DAY))
    d = td.auto_decision(ds, [JAN], NOW)
    assert d["run"] is False and d["reason"] == "daily limit reached"


def test_the_ceiling_resets_the_next_day():
    current_sig = td.signature(td.schema_shape([JAN]))
    ds = _DS(sig=td.encode(td.schema_shape([JAN, FEB])),
             settings=_auto(drift_sig=current_sig,
                            drift_seen_at=(NOW - timedelta(hours=2)).isoformat(),
                            auto_day="2026-07-29", auto_runs=99))
    assert td.auto_decision(ds, [JAN], NOW)["run"] is True


def test_every_refusal_gives_a_reason():
    """A sweep that skips silently cannot be told from one that never ran — the
    exact failure shape this whole area kept producing."""
    for ds in (_DS(settings={"mode": "notify"}),
               _DS(sig=None, settings=_auto()),
               _DS(sig=td.encode(td.schema_shape([JAN])), settings=_auto())):
        assert td.auto_decision(ds, [JAN], NOW)["reason"]


def test_a_run_is_counted_and_the_marker_spent():
    ds = _DS(settings=_auto(drift_sig="abc", drift_seen_at="x",
                            auto_day=NOW.date().isoformat(), auto_runs=1))
    cfg = td.note_auto_run(ds, NOW)
    assert cfg["auto_runs"] == 2
    assert "drift_sig" not in cfg and "drift_seen_at" not in cfg, (
        "the spent marker would make the next change look like it had already "
        "been waiting"
    )


def test_note_auto_run_returns_rather_than_mutating():
    """SQLAlchemy does not track in-place edits to a JSON column, so a function
    that mutated the stored dict would have its work silently dropped."""
    original = _auto(auto_runs=1, auto_day=NOW.date().isoformat())
    ds = _DS(settings=original)
    cfg = td.note_auto_run(ds, NOW)
    assert cfg is not original
    assert original["auto_runs"] == 1
