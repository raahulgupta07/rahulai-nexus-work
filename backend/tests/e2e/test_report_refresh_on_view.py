"""
Refresh-on-view: opening a shared report page (/r/{id}) reruns the queries
behind its artifact, subject to a hardcoded rate limit.

The endpoint is reachable without authentication because it serves the public
page, so the guards are the feature. What is asserted here:

  * an anonymous viewer of a PUBLIC report triggers a real rerun,
  * a second view inside the interval is refused (the staleness gate is the
    rate limit — with the interval hardcoded, no configuration can raise the
    rerun frequency above one per REFRESH_ON_VIEW_MIN_INTERVAL_SECONDS),
  * once the gate opens, the single-flight claim still collapses a herd of
    simultaneous viewers to one rerun — and, because that claim is keyed on the
    `last_run_at` they all read rather than on the wall clock, it releases as
    soon as that value is actually replaced,
  * a report that never opted in is refused,
  * a private report is refused to a stranger, at the same status the page
    itself returns,
  * the flag survives turning the schedule off, and is exposed by GET
    /reports/{id} so the modal reopens in the right state.

Run:
    cd backend
    uv run pytest tests/e2e/test_report_refresh_on_view.py -v
"""
import asyncio
import uuid
from datetime import datetime, timedelta

import pytest

from app.dependencies import async_session_maker
from app.models.artifact import Artifact
from app.models.query import Query
from app.models.report import Report
from app.models.step import Step
from app.models.visualization import Visualization
from app.models.widget import Widget
from app.core.scheduler import claim_scheduled_run
from app.services.report_service import (
    REFRESH_ON_VIEW_MIN_INTERVAL_SECONDS,
    refresh_claim_epoch,
)


def _run(coro):
    return asyncio.run(coro)


# Deterministic — ds_clients is unused, so no data source or external boundary.
STEP_CODE = """
def generate_df(ds_clients, excel_files):
    import pandas as pd
    return pd.DataFrame({"month": ["2024-01", "2024-02"], "revenue": [10, 20]})
"""


async def _seed_artifact(report_id: str):
    """Attach a one-query artifact dashboard whose default step holds no data.

    Queries/visualizations/artifacts come from the AI completion flow in
    production and have no public CRUD API, so the graph is seeded directly
    (same approach as tests/e2e/test_report_rerun_artifact.py).
    """
    suffix = uuid.uuid4().hex[:8]
    async with async_session_maker() as db:
        report = await db.get(Report, report_id)
        org_id, user_id = report.organization_id, report.user_id

        widget = Widget(title=f"W {suffix}", slug=f"w-{suffix}", report_id=report_id)
        db.add(widget)
        await db.flush()

        query = Query(title="Q", report_id=report_id, widget_id=widget.id,
                      organization_id=org_id, user_id=user_id)
        db.add(query)
        await db.flush()

        step = Step(title="Default", slug=f"default-{suffix}", status="success",
                    widget_id=widget.id, query_id=query.id, code=STEP_CODE, data=None)
        db.add(step)
        await db.flush()
        query.default_step_id = step.id

        viz = Visualization(title="Viz", status="success", report_id=report_id,
                            query_id=query.id, view={"type": "bar_chart"})
        db.add(viz)
        await db.flush()

        db.add(Artifact(report_id=report_id, user_id=user_id, organization_id=org_id,
                        title="Dashboard", mode="page", version=1, status="completed",
                        content={"code": "function App() {}", "visualization_ids": [str(viz.id)]}))
        await db.commit()
        return {"query_id": str(query.id), "step_id": str(step.id)}


async def _backdate_last_run(report_id: str, seconds: int):
    """Age the report's data past the staleness gate without waiting."""
    async with async_session_maker() as db:
        report = await db.get(Report, report_id)
        report.last_run_at = datetime.utcnow() - timedelta(seconds=seconds)
        await db.commit()


async def _last_run_at(report_id: str):
    """The value a viewer would read — and therefore the claim key they compute."""
    async with async_session_maker() as db:
        report = await db.get(Report, report_id)
        return report.last_run_at


async def _read_step(step_id: str):
    async with async_session_maker() as db:
        step = await db.get(Step, step_id)
        return step.data


def _view(test_client, report_id):
    """Open the shared page's refresh endpoint as an anonymous visitor."""
    return test_client.post(f"/api/r/{report_id}/rerun")


def _publish(set_visibility, report_id, user_token, org_id):
    set_visibility(report_id, "artifact", "public", user_token=user_token, org_id=org_id)


@pytest.mark.e2e
def test_refresh_on_view_reruns_for_an_anonymous_viewer(
    create_report, create_user, login_user, whoami, set_visibility, schedule_report, test_client
):
    """The whole point: a stranger opening the shared link gets fresh data,
    executed with the owner's access, without logging in."""
    user = create_user()
    token = login_user(user["email"], user["password"])
    org_id = whoami(token)["organizations"][0]["id"]

    report = create_report(title="Refreshing dashboard", user_token=token, org_id=org_id, data_sources=[])
    seeded = _run(_seed_artifact(report["id"]))
    _publish(set_visibility, report["id"], token, org_id)
    schedule_report(report["id"], "None", user_token=token, org_id=org_id, refresh_on_view=True)

    assert _run(_read_step(seeded["step_id"])) is None

    body = _view(test_client, report["id"]).json()

    assert body["skipped"] is False
    assert body["steps_total"] == 1
    assert body["steps_succeeded"] == 1
    assert body["last_run_at"] is not None

    rows = (_run(_read_step(seeded["step_id"])) or {}).get("rows")
    assert rows, "the viewer's open did not refresh the step the dashboard renders"
    assert {r["revenue"] for r in rows} == {10, 20}


@pytest.mark.e2e
def test_refresh_on_view_is_rate_limited_by_the_staleness_gate(
    create_report, create_user, login_user, whoami, set_visibility, schedule_report, test_client
):
    """Traffic must not translate into query volume: a second view inside the
    interval is refused, whoever asks and however often."""
    user = create_user()
    token = login_user(user["email"], user["password"])
    org_id = whoami(token)["organizations"][0]["id"]

    report = create_report(title="Rate limited", user_token=token, org_id=org_id, data_sources=[])
    _run(_seed_artifact(report["id"]))
    _publish(set_visibility, report["id"], token, org_id)
    schedule_report(report["id"], "None", user_token=token, org_id=org_id, refresh_on_view=True)

    assert _view(test_client, report["id"]).json()["skipped"] is False

    for _ in range(5):
        body = _view(test_client, report["id"]).json()
        assert body["skipped"] is True
        assert body["steps_total"] == 0
        assert "fresh" in body["message"]


@pytest.mark.e2e
def test_refresh_on_view_single_flights_once_the_gate_opens(
    create_report, create_user, login_user, whoami, set_visibility, schedule_report, test_client
):
    """The gate alone races: concurrent viewers all read the same stale
    last_run_at. The claim is what stops N viewers becoming N reruns.

    The herd is defined by the state its members READ, which is why the claim
    is keyed on `last_run_at` and not on the wall clock. So the in-flight
    viewer is simulated the way one really exists — by taking the claim for the
    epoch this viewer is about to read.
    """
    user = create_user()
    token = login_user(user["email"], user["password"])
    org_id = whoami(token)["organizations"][0]["id"]

    report = create_report(title="Herd", user_token=token, org_id=org_id, data_sources=[])
    _run(_seed_artifact(report["id"]))
    _publish(set_visibility, report["id"], token, org_id)
    schedule_report(report["id"], "None", user_token=token, org_id=org_id, refresh_on_view=True)

    assert _view(test_client, report["id"]).json()["skipped"] is False

    # Age the data past the gate, so only the claim can turn the next viewer away.
    _run(_backdate_last_run(report["id"], REFRESH_ON_VIEW_MIN_INTERVAL_SECONDS + 60))
    stale_at = _run(_last_run_at(report["id"]))

    # Stand in for a viewer who arrived first and is still running: it read this
    # same `last_run_at` and has not written a new one yet.
    assert claim_scheduled_run(
        f"report_view_{report['id']}",
        REFRESH_ON_VIEW_MIN_INTERVAL_SECONDS,
        refresh_claim_epoch(stale_at),
    ) is True

    body = _view(test_client, report["id"]).json()
    assert body["skipped"] is True
    assert "in flight" in body["message"]

    # ...and the claim must not outlive the state it was taken against. Once the
    # data really is replaced, the next stale window is a new herd and reruns.
    # Keying on the wall clock could not tell these two cases apart: it refused
    # this one too, suppressing a legitimate rerun for the rest of the window.
    _run(_backdate_last_run(report["id"], REFRESH_ON_VIEW_MIN_INTERVAL_SECONDS + 3600))
    assert _run(_last_run_at(report["id"])) != stale_at
    assert _view(test_client, report["id"]).json()["skipped"] is False


@pytest.mark.e2e
def test_refresh_on_view_skipped_when_not_enabled(
    create_report, create_user, login_user, whoami, set_visibility, test_client
):
    """Opt-in only — a shared report that never asked for this never reruns."""
    user = create_user()
    token = login_user(user["email"], user["password"])
    org_id = whoami(token)["organizations"][0]["id"]

    report = create_report(title="Not opted in", user_token=token, org_id=org_id, data_sources=[])
    seeded = _run(_seed_artifact(report["id"]))
    _publish(set_visibility, report["id"], token, org_id)

    body = _view(test_client, report["id"]).json()

    assert body["skipped"] is True
    assert "not enabled" in body["message"]
    assert _run(_read_step(seeded["step_id"])) is None


@pytest.mark.e2e
def test_refresh_on_view_denied_on_a_private_report(
    create_report, create_user, login_user, whoami, schedule_report, test_client
):
    """Triggering a rerun must be no more permissive than reading the page:
    a stranger gets the same answer the page itself gives."""
    user = create_user()
    token = login_user(user["email"], user["password"])
    org_id = whoami(token)["organizations"][0]["id"]

    report = create_report(title="Private", user_token=token, org_id=org_id, data_sources=[])
    _run(_seed_artifact(report["id"]))
    schedule_report(report["id"], "None", user_token=token, org_id=org_id, refresh_on_view=True)

    assert test_client.post(f"/api/r/{report['id']}/rerun").status_code == 404
    assert test_client.get(f"/api/r/{report['id']}").status_code == 404


@pytest.mark.e2e
def test_refresh_on_view_survives_turning_the_schedule_off(
    create_report, create_user, login_user, whoami, schedule_report, get_report
):
    """The two settings share a modal but not a lifetime: a report can refresh
    on view with no cron, so unscheduling must not silently disable it."""
    user = create_user()
    token = login_user(user["email"], user["password"])
    org_id = whoami(token)["organizations"][0]["id"]

    report = create_report(title="Both on", user_token=token, org_id=org_id, data_sources=[])

    schedule_report(report["id"], "0 8 * * *", user_token=token, org_id=org_id, refresh_on_view=True)
    assert get_report(report["id"], user_token=token, org_id=org_id)["refresh_on_view"] is True

    schedule_report(report["id"], "None", user_token=token, org_id=org_id)
    after = get_report(report["id"], user_token=token, org_id=org_id)
    assert after["cron_schedule"] is None
    assert after["refresh_on_view"] is True, "unscheduling cleared refresh-on-view"


@pytest.mark.e2e
def test_get_report_exposes_refresh_on_view(
    create_report, create_user, login_user, whoami, schedule_report, get_report
):
    """GET /reports/{id} builds its schema field-by-field, so a stored flag can
    silently serialize as its default — which shows up as a settings checkbox
    that forgets what the user saved."""
    user = create_user()
    token = login_user(user["email"], user["password"])
    org_id = whoami(token)["organizations"][0]["id"]

    report = create_report(title="Round trip", user_token=token, org_id=org_id, data_sources=[])
    assert get_report(report["id"], user_token=token, org_id=org_id)["refresh_on_view"] is False

    schedule_report(report["id"], "None", user_token=token, org_id=org_id, refresh_on_view=True)
    assert get_report(report["id"], user_token=token, org_id=org_id)["refresh_on_view"] is True

    schedule_report(report["id"], "None", user_token=token, org_id=org_id, refresh_on_view=False)
    assert get_report(report["id"], user_token=token, org_id=org_id)["refresh_on_view"] is False
