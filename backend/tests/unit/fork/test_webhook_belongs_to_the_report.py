"""A webhook may only be touched through the report it was created on.

The routes authorize the REPORT — `@requires_permission('update_reports',
model=Report, owner_only=True)` reads `report_id` out of the path and refuses
anyone who may not touch that report. Then they threw that decision away and
looked the webhook up by `webhook_id` alone, with no report filter and no
organization filter.

Two ids in the path, one of them checked. So:

    PUT /reports/<a report I own>/webhooks/<somebody else's webhook_id>

passed the gate and mutated a webhook belonging to another report — in another
organization. `webhook_id` is not a secret: it rides in report payloads, so a
member of one tenant can hold the id of a webhook in another and repoint it,
disable it, or rotate its secret out from under its owner.

These tests read the SQL the service actually builds, so they fail if the
filter is dropped again — not merely if the wording of a signature changes.
"""
import asyncio
import inspect
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.webhook_service import WebhookService

REPO = Path(__file__).resolve().parents[4]
ROUTES = REPO / "backend" / "app" / "routes" / "webhook.py"


class _Result:
    def scalar_one_or_none(self):
        return None


class _RecordingSession:
    """Enough AsyncSession to capture the statement and force the 404 path."""

    def __init__(self):
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result()


def _lookup_sql(**kwargs) -> str:
    """The WHERE clause `_get_or_404` builds, with real values inlined."""
    session = _RecordingSession()
    with pytest.raises(HTTPException) as exc:
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            WebhookService()._get_or_404(session, **kwargs)
        )
    assert exc.value.status_code == 404
    assert session.statements, "the lookup ran no query"
    compiled = session.statements[0].compile(compile_kwargs={"literal_binds": True})
    return str(compiled)


def _call_kwargs() -> dict:
    """Every parameter `_get_or_404` takes, filled with a distinct marker."""
    params = inspect.signature(WebhookService._get_or_404).parameters
    values = {
        "webhook_id": "WH-UNDER-TEST",
        "report_id": "REPORT-IN-THE-PATH",
        "organization_id": "ORG-OF-THE-CALLER",
    }
    return {n: values[n] for n in params if n in values}


# --- the binding itself ----------------------------------------------------

def test_the_lookup_is_scoped_to_the_report_in_the_path():
    """★The whole defect. The report is what the caller was authorized for;
    the webhook must be one of THAT report's webhooks."""
    params = inspect.signature(WebhookService._get_or_404).parameters
    assert "report_id" in params, "the authorized report never reaches the lookup"
    sql = _lookup_sql(**_call_kwargs())
    assert "REPORT-IN-THE-PATH" in sql, "report_id is accepted and then not used"


def test_the_lookup_is_scoped_to_the_callers_organization():
    """Defence in depth: even if a report id were ever guessed or reused, the
    row must still belong to the tenant asking for it."""
    params = inspect.signature(WebhookService._get_or_404).parameters
    assert "organization_id" in params
    sql = _lookup_sql(**_call_kwargs())
    assert "ORG-OF-THE-CALLER" in sql


def test_the_webhook_id_is_still_matched():
    """The guard must narrow the lookup, not replace it."""
    sql = _lookup_sql(**_call_kwargs())
    assert "WH-UNDER-TEST" in sql


def test_soft_deleted_webhooks_stay_invisible():
    """Pre-existing behaviour that must survive the fix."""
    sql = _lookup_sql(**_call_kwargs()).lower()
    assert "deleted_at is null" in sql


def test_a_missing_row_is_a_404_not_a_500():
    """Refusal must be indistinguishable from absence — a 403 here would tell
    an attacker that the webhook id is real and belongs to someone else."""
    session = _RecordingSession()
    with pytest.raises(HTTPException) as exc:
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            WebhookService()._get_or_404(session, **_call_kwargs())
        )
    assert exc.value.status_code == 404


# --- the callers -----------------------------------------------------------

@pytest.mark.parametrize("method", ["update_webhook", "delete_webhook", "rotate_secret"])
def test_every_mutating_service_method_carries_the_scope(method):
    """All three route-facing mutators must pass the scope down; a single one
    left unscoped reopens the hole on its own."""
    params = inspect.signature(getattr(WebhookService, method)).parameters
    assert "report_id" in params, f"{method} cannot scope its lookup"
    assert "organization_id" in params, f"{method} cannot scope its lookup"


@pytest.mark.parametrize(
    "call",
    [
        "webhook_service.update_webhook(",
        "webhook_service.delete_webhook(",
        "webhook_service.rotate_secret(",
    ],
)
def test_the_routes_hand_over_the_report_they_authorized(call):
    """★The decorator's decision is only worth anything if the same
    `report_id` reaches the query."""
    src = ROUTES.read_text(encoding="utf-8")
    i = src.index(call)
    args = src[i: src.index(")", i)]
    assert "report_id" in args, f"{call}…) drops the authorized report"
    assert "organization" in args, f"{call}…) drops the organization"


def test_listing_is_already_scoped_to_the_report():
    """The one place that was always right — kept honest so a later
    refactor cannot quietly widen it."""
    sql_src = inspect.getsource(WebhookService.list_webhooks)
    assert re.search(r"Webhook\.report_id\s*==\s*report_id", sql_src)
