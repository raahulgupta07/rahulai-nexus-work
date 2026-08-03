"""A sync that ends must leave a record the member can find later.

Phase D. A federated Fabric sync takes minutes; nobody watches a progress strip
for minutes. The strip's own state expires after fifteen
(`connection_sync_progress._TTL_SECONDS`), so a member who starts a sync and
goes to lunch comes back to a screen that says nothing at all — about a sync
that may have succeeded, half-succeeded, or died.

★No new channel. The per-user inbox already exists, already carries an unread
badge and read/dismiss state, and is already polled. A second delivery path is
a second place the result can live, and one of them drifts.

★No schema here — the wording and the notify/stay-quiet decisions are pure, and
the wiring is asserted against source. See CLAUDE.md, split by cost.
"""
import inspect

import pytest

from app.services.sync_notifications import (
    NOTIFY_ABOVE_SECONDS,
    counts_from_progress,
    notify_sync_failed,
    notify_sync_finished,
)


class _Recorder:
    """Stands in for `inbox_service.notify_users`, capturing the one call."""

    def __init__(self):
        self.calls = []

    async def notify_users(self, db, **kwargs):
        self.calls.append(kwargs)
        return []


@pytest.fixture
def inbox(monkeypatch):
    from app.services import sync_notifications

    rec = _Recorder()
    monkeypatch.setattr(sync_notifications, "inbox_service", rec)
    return rec


async def _finish(inbox, **over):
    args = dict(
        organization_id="org", user_id="u1", data_source_id="ds1",
        data_source_name="Fabric", tables=214, workspaces_done=3,
        workspaces_failed=0, elapsed_seconds=252.0,
    )
    args.update(over)
    await notify_sync_finished(None, **args)
    return inbox.calls[0] if inbox.calls else None


# ─────────────────── success ───────────────────


@pytest.mark.asyncio
async def test_a_successful_sync_reports_what_it_built(inbox):
    call = await _finish(inbox)
    assert "Fabric is ready" == call["title"]
    assert "214 tables" in call["body"]
    assert "3 workspaces" in call["body"]
    assert "4m12s" in call["body"]
    assert call["severity"] == "info"


@pytest.mark.asyncio
async def test_a_quick_sync_stays_quiet(inbox):
    """Under half a minute the member was almost certainly still looking at the
    strip. Notifying anyway turns the inbox into a log, and a log nobody reads
    is worse than no notification at all."""
    await _finish(inbox, elapsed_seconds=5.0)
    assert inbox.calls == []


@pytest.mark.asyncio
async def test_a_member_initiated_retry_always_reports(inbox):
    """They pressed the button to find out whether it worked this time. Silence
    is not an answer to that."""
    call = await _finish(inbox, elapsed_seconds=2.0, force=True)
    assert call is not None


@pytest.mark.asyncio
async def test_the_threshold_is_a_named_constant_not_a_literal():
    assert NOTIFY_ABOVE_SECONDS > 0


# ─────────────────── the outcomes that are not "fine" ───────────────────


@pytest.mark.asyncio
async def test_a_partial_sync_is_a_warning_not_a_success(inbox):
    """"Ready" over an agent missing a third of its tables is the notification
    that gets believed and then contradicted by the first question asked."""
    call = await _finish(inbox, workspaces_done=2, workspaces_failed=1)
    assert call["severity"] == "warning"
    assert "gap" in call["title"]
    assert "1 workspace" in call["body"]


@pytest.mark.asyncio
async def test_zero_tables_is_never_reported_as_ready(inbox):
    """★The most confusing possible outcome, and exactly what an empty
    workspace selection produces. A member who deselected everything last week
    and forgot needs to be told why their agent is blank — not congratulated."""
    call = await _finish(inbox, tables=0, workspaces_done=0)
    assert call["severity"] == "warning"
    assert "no tables" in call["title"]
    assert "workspaces are selected" in call["body"]


@pytest.mark.asyncio
async def test_one_table_is_not_called_tables(inbox):
    call = await _finish(inbox, tables=1, workspaces_done=1)
    assert "1 table from 1 workspace" in call["body"]


# ─────────────────── failure ───────────────────


async def _fail(inbox, **over):
    args = dict(
        organization_id="org", user_id="u1", data_source_id="ds1",
        data_source_name="Fabric", message="RAW SENTENCE", error_kind="source",
    )
    args.update(over)
    await notify_sync_failed(None, **args)
    return inbox.calls[0] if inbox.calls else None


@pytest.mark.asyncio
async def test_a_failed_sync_always_notifies_however_fast_it_failed(inbox):
    """★The duration floor is deliberately NOT applied here. A quick success is
    unremarkable; a quick failure is not — and the fastest failures are the
    infrastructure ones, precisely the ones a member would otherwise never
    learn about."""
    call = await _fail(inbox)
    assert call is not None
    assert call["type"] == "sync_failed"


@pytest.mark.asyncio
async def test_our_own_outage_is_a_warning_not_an_error(inbox):
    """Red is a call to act, and there is nothing here for the member to act
    on — it retries itself."""
    call = await _fail(inbox, error_kind="infrastructure")
    assert call["severity"] == "warning"
    assert "interrupted" in call["title"]


@pytest.mark.asyncio
async def test_a_source_failure_is_an_error(inbox):
    call = await _fail(inbox, error_kind="source")
    assert call["severity"] == "error"
    assert "failed" in call["title"]


@pytest.mark.asyncio
async def test_the_classified_sentence_is_passed_through_not_rewritten(inbox):
    """★Two phrasings of one failure is how the inbox and the sync strip end up
    disagreeing on screen, with the member unable to tell which to believe."""
    call = await _fail(inbox, message="RAW SENTENCE")
    assert call["body"] == "RAW SENTENCE"


# ─────────────────── never breaks what it reports on ───────────────────


@pytest.mark.asyncio
async def test_a_broken_inbox_does_not_break_the_sync(monkeypatch):
    """A notification is an account of what happened. Failing to file it must
    not change what happened."""
    from app.services import sync_notifications

    class _Broken:
        async def notify_users(self, *a, **k):
            raise RuntimeError("inbox down")

    monkeypatch.setattr(sync_notifications, "inbox_service", _Broken())
    await notify_sync_finished(
        None, organization_id="o", user_id="u", data_source_id="d",
        data_source_name="F", tables=1, elapsed_seconds=100,
    )
    await notify_sync_failed(
        None, organization_id="o", user_id="u", data_source_id="d",
        data_source_name="F", message="m",
    )


# ─────────────────── grouping and links ───────────────────


@pytest.mark.asyncio
async def test_four_retries_leave_one_notification_not_four(inbox):
    """`group_key` is what makes the inbox refresh a row instead of stacking
    them. Without it an unlucky afternoon reads as an incident."""
    await _finish(inbox)
    await _finish(inbox)
    assert inbox.calls[0]["group_key"] == inbox.calls[1]["group_key"]


@pytest.mark.asyncio
async def test_success_and_failure_do_not_share_a_group(inbox):
    """Otherwise a later success silently overwrites the failure that is still
    the reason the agent has no tables."""
    ok = await _finish(inbox)
    inbox.calls.clear()
    bad = await _fail(inbox)
    assert ok["group_key"] != bad["group_key"]


@pytest.mark.asyncio
async def test_the_notification_links_to_the_agent(inbox):
    call = await _finish(inbox)
    assert call["link"] == "/agents/ds1"


@pytest.mark.asyncio
async def test_the_source_is_registered_as_a_valid_one():
    """An unregistered source would be delivered and then filtered out of the
    inbox — invisible, with nothing failing."""
    from app.models.notification import SOURCES, SOURCE_SYNC

    assert SOURCE_SYNC in SOURCES


# ─────────────────── counts come from the progress row ───────────────────


def test_counts_are_read_off_the_progress_row_the_member_sees():
    """★Recomputing them here is how the inbox and the strip end up reporting
    different numbers for one sync."""
    state = {
        "tables": 214,
        "elapsed_ms": 252000,
        "detail": [
            {"name": "A", "status": "ok"},
            {"name": "B", "status": "completed"},
            {"name": "C", "status": "failed"},
            {"name": "D", "status": "pending"},
        ],
    }
    counts = counts_from_progress(state)
    assert counts["tables"] == 214
    assert counts["workspaces_done"] == 2
    assert counts["workspaces_failed"] == 1
    assert counts["elapsed_seconds"] == 252.0


def test_both_spellings_of_success_are_counted():
    """The tracker writes 'ok' per endpoint and 'completed' at the top; both
    appear in `detail` depending on which code path wrote the row."""
    assert counts_from_progress(
        {"detail": [{"status": "ok"}, {"status": "completed"}]}
    )["workspaces_done"] == 2


def test_a_progress_row_with_nothing_in_it_yields_zeros():
    counts = counts_from_progress({})
    assert counts["tables"] == 0
    assert counts["workspaces_done"] == 0
    assert counts["elapsed_seconds"] is None


def test_junk_in_detail_does_not_break_the_count():
    assert counts_from_progress(
        {"detail": ["nonsense", None, {"status": "ok"}]}
    )["workspaces_done"] == 1


# ─────────────────── the sync actually calls it ───────────────────


def _sync_source() -> str:
    from app.routes import fabric_user_signin

    return inspect.getsource(fabric_user_signin._run_federated_sync)


def test_both_endings_notify():
    source = _sync_source()
    assert "notify_sync_finished" in source
    assert "notify_sync_failed" in source


def test_the_failure_path_opens_its_own_session():
    """★`db` and `ds` belong to an `async with` that has already exited by the
    time the handler runs. Touching them raises inside the handler and loses
    the failure we came there to report."""
    source = _sync_source()
    tail = source[source.index("except Exception as e:"):]
    assert "async_session_maker()" in tail


def test_a_failed_notification_cannot_swallow_the_sync_result():
    source = _sync_source()
    assert source.count("notification failed for ds=") >= 2


def test_both_connectors_notify_not_just_fabric():
    """An asymmetry here means one connector tells the member what happened and
    the other leaves them guessing, for the same length of wait."""
    from app.routes import powerbi_user_signin

    source = inspect.getsource(powerbi_user_signin._run_tenant_merge)
    assert "notify_sync_finished" in source
    assert "notify_sync_failed" in source


# ─────────────────── it reaches the panel ───────────────────


def _modal_source() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[4]
        / "frontend" / "components" / "NotificationModal.vue"
    ).read_text(encoding="utf-8")


def test_the_inbox_panel_has_an_icon_for_a_sync():
    """★Without these the row renders a generic bell — the same mark as every
    other source, in the one panel whose job is telling them apart at a
    glance."""
    source = _modal_source()
    assert "sync_finished:" in source
    assert "sync_failed:" in source


def test_the_panel_does_not_filter_sources_out():
    """The sidebar bell shows everything; a source allowlist anywhere on this
    path would deliver sync results into a list that never displays them —
    invisible, with nothing failing."""
    from app.services import inbox_service as mod

    source = inspect.getsource(mod.InboxService.list_for_user)
    # `source` is an optional narrowing argument, never a hardcoded set.
    assert "if source:" in source
    assert "SOURCES" not in source
