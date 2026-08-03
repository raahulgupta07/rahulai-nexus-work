"""A member's workspace selection is honoured exactly, including "none".

Phase C of the Fabric plan: a member with access to twenty workspaces usually
works in three, and every sync crawled all twenty.

★The whole risk of this feature is one branch. "No filter" and "a filter that
matches nothing" are different answers, and collapsing them means deselecting
every workspace triggers the full twenty-workspace crawl — the exact cost the
feature removes, at the exact moment the member asked for none of it. That
failure is silent: the sync succeeds, the tables appear, and the only symptom
is the bill and the wait.

Half this file is that one distinction, from every angle it can be got wrong.
"""
import inspect

import pytest

from app.services.endpoint_selection import (
    endpoint_key,
    select_endpoints,
    unmatched_selection,
)


def _eps(*names):
    return [{"database": n, "host": f"{n}.example", "workspace": f"ws-{n}"} for n in names]


ALL = _eps("Sales", "Finance", "HR", "Ops")


# ─────────────── the distinction that carries the feature ───────────────


def test_no_selection_syncs_everything():
    """NULL means "never chosen". Every install today means this, so it must
    keep meaning the old behaviour exactly."""
    assert select_endpoints(ALL, None) == ALL


def test_an_empty_selection_syncs_nothing():
    """★The trap. Not "everything" — nothing."""
    assert select_endpoints(ALL, []) == []


def test_empty_and_absent_are_not_the_same_answer():
    """Stated on its own because every wrong implementation passes one of the
    two tests above and fails this one — `if not selected:` reads as sensible
    Python and silently merges them."""
    assert select_endpoints(ALL, []) != select_endpoints(ALL, None)


def test_the_source_of_truth_says_so_too():
    """The distinction has to survive someone reading the model in six months
    and 'tidying' a nullable column into a defaulted one."""
    from app.models import user_data_source_scope

    doc = inspect.getdoc(user_data_source_scope) or ""
    assert "NULL" in doc and "[]" in doc


def test_the_selection_module_does_not_use_a_falsy_test():
    """A `if not selected:` anywhere in the decision is the bug itself."""
    from app.services import endpoint_selection

    source = inspect.getsource(endpoint_selection.select_endpoints)
    assert "if not selected" not in source
    assert "selected is None" in source


# ─────────────── ordinary filtering ───────────────


def test_only_the_chosen_workspaces_are_crawled():
    picked = select_endpoints(ALL, ["Sales", "HR"])
    assert [endpoint_key(e) for e in picked] == ["Sales", "HR"]


def test_selection_order_does_not_reorder_the_crawl():
    """Discovery order is the crawl order. A selection is a filter, not a sort
    — reordering here would make progress rows appear in an order that matches
    nothing the member has seen."""
    picked = select_endpoints(ALL, ["Ops", "Sales"])
    assert [endpoint_key(e) for e in picked] == ["Sales", "Ops"]


def test_selecting_all_of_them_is_the_same_set():
    picked = select_endpoints(ALL, ["Sales", "Finance", "HR", "Ops"])
    assert picked == ALL


def test_a_duplicate_selection_does_not_crawl_twice():
    picked = select_endpoints(ALL, ["Sales", "Sales"])
    assert len(picked) == 1


def test_selecting_something_that_does_not_exist_yields_nothing_not_everything():
    assert select_endpoints(ALL, ["Nope"]) == []


# ─────────────── the key everyone must agree on ───────────────


def test_the_key_is_the_name_the_member_sees():
    assert endpoint_key({"database": "Sales"}) == "Sales"


def test_the_key_falls_back_to_name():
    """Progress `detail` rows carry `name`; discovery carries `database`. Both
    describe the same lakehouse, and a selection saved from one surface has to
    match the other or it silently matches nothing."""
    assert endpoint_key({"name": "Sales"}) == "Sales"


def test_an_endpoint_with_no_identity_never_matches_a_selection():
    assert select_endpoints([{"host": "h"}], ["Sales"]) == []


# ─────────────── selections that have gone stale ───────────────


def test_a_renamed_workspace_is_reported_not_ignored():
    """"0 of 3 workspaces" with no reason is indistinguishable from a sync that
    ran and found nothing. Naming what went missing is what makes it fixable."""
    assert unmatched_selection(ALL, ["Sales", "Gone"]) == ["Gone"]


def test_nothing_is_reported_missing_when_nothing_was_selected():
    assert unmatched_selection(ALL, None) == []


def test_an_empty_selection_reports_no_missing_workspaces():
    """Choosing nothing cannot leave anything unmatched — reporting one here
    would put a scary warning on the screen of a member who did what they meant
    to do."""
    assert unmatched_selection(ALL, []) == []


def test_all_missing_names_are_reported_not_just_the_first():
    assert unmatched_selection(ALL, ["A", "B"]) == ["A", "B"]


# ─────────────── the crawl actually applies it ───────────────


def _crawl_source() -> str:
    from app.services.data_source_service import DataSourceService

    return inspect.getsource(DataSourceService._merge_all_fabric_endpoints)


def test_the_selection_is_applied_before_any_endpoint_is_crawled():
    """Filtering after the crawl would save nothing at all — the cost is the
    per-endpoint SQL walk, not the discovery call."""
    source = _crawl_source()
    assert "select_endpoints(" in source
    assert source.index("select_endpoints(") < source.index("set_endpoints(")


def test_an_empty_selection_finishes_the_run_instead_of_falling_through():
    """★`return None` from this method means "fall back to the generic
    single-client path". For an empty selection that would quietly sync
    something the member deselected. It has to be a terminal, truthful result.
    """
    source = _crawl_source()
    assert "selection_empty" in source
    # The run is settled as a completed sync of zero tables...
    assert "_prog.finish(_ds_id, _uid, tables=0)" in source
    # ...and the branch returns an empty list, NOT None. `None` is this
    # method's "fall back to the generic single-client path" signal, which for
    # an empty selection would sync something the member deselected.
    # ★The LAST `if not endpoints:` before the publish — there is an earlier
    # one guarding "discovery returned nothing", which legitimately does
    # `return None`. Matching the first occurrence tested that branch instead.
    head = source[: source.index("# Publish the SELECTED")]
    block = head[head.rindex("if not endpoints:"):]
    assert "return []" in block
    assert "return None" not in block


def test_the_published_workspace_list_is_the_selected_one():
    """The progress strip names what it is waiting for. Publishing all twenty
    while crawling three would show seventeen workspaces stuck at pending
    forever."""
    source = _crawl_source()
    assert "set_endpoints(_ds_id, _uid, endpoints)" in source
    # ...and `endpoints` has been narrowed by then.
    assert source.index("endpoints = select_endpoints(") < source.index(
        "set_endpoints(_ds_id, _uid, endpoints)"
    )


# ─────────────── reading the stored value ───────────────


def test_a_read_failure_syncs_everything_rather_than_a_guess():
    """★Wrong-and-slow is recoverable. Wrong-and-confident is not: syncing a
    subset nobody asked for and reporting it complete leaves the member with a
    catalog they believe is whole."""
    from app.services import user_scope_service

    source = inspect.getsource(user_scope_service.get_selected_endpoints)
    assert "except Exception:" in source
    assert "return None" in source


def test_a_malformed_stored_value_is_not_treated_as_a_selection():
    from app.services import user_scope_service

    source = inspect.getsource(user_scope_service.get_selected_endpoints)
    assert "isinstance(value, list)" in source


def test_saving_none_clears_the_selection():
    from app.services import user_scope_service

    source = inspect.getsource(user_scope_service.set_selected_endpoints)
    assert "if selected is not None:" in source


@pytest.mark.parametrize("field", ["selected", "syncs_everything"])
def test_the_api_reports_whether_a_selection_exists_at_all(field):
    """The picker cannot render honestly without this: twenty unticked boxes
    reads as "nothing is selected", which is the opposite of what NULL means."""
    from app.routes import fabric_user_signin

    source = inspect.getsource(fabric_user_signin.fabric_signin_workspaces)
    assert field in source


def _picker_source() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[4]
        / "frontend" / "components" / "datasources" / "WorkspaceScopePicker.vue"
    ).read_text(encoding="utf-8")


def test_the_picker_keeps_never_chosen_distinct_from_chose_nothing():
    """★A `Set` alone cannot carry the distinction — an empty Set would mean
    "chose nothing", the opposite instruction. The null has to survive
    alongside it and only collapse at save time."""
    source = _picker_source()
    assert "savedSelection" in source
    assert "savedSelection.value === null" in source


def test_an_unset_selection_renders_as_everything_ticked():
    """Twenty empty checkboxes over a source that syncs all twenty states the
    opposite of what is true."""
    source = _picker_source()
    assert "available.value.map(w => w.name)" in source


def test_ticking_every_box_saves_null_not_a_full_list():
    """★Otherwise a workspace the member gains access to tomorrow is silently
    excluded by a selection they made today when it meant "all of them"."""
    source = _picker_source()
    assert "ticked.value.size === available.value.length" in source
    assert "all ? null :" in source


def test_the_zero_selected_state_says_what_it_will_do():
    """It is a legitimate answer and one mis-click from being an accident."""
    source = _picker_source()
    assert "ticked.size === 0" in source
    assert "scopeNoneWarning" in source


def test_saving_a_selection_does_not_start_a_sync():
    """Saving a preference and paying for a twenty-minute crawl are separate
    decisions. A picker that syncs on every tick is unusable."""
    from app.routes import fabric_user_signin

    source = inspect.getsource(fabric_user_signin.fabric_signin_set_workspaces)
    assert "_kick_off_sync" not in source
