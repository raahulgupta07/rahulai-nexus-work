"""Settings → Access can switch the seeded agents, and nothing else.

An admin can turn Microsoft Fabric / Power BI / City Mart Retail off from
Settings so a conflict can be shut down without hunting through the agent list.

Two properties matter more than the feature itself:

1. ★ It writes ``DataSource.publish_status`` — the SAME field the per-agent
   switch on the Agents page writes. No second flag, so the two screens cannot
   drift apart. This codebase has produced that exact failure three times (a
   permission registry vs a migration's copy, a .gitignore rule vs the tracked
   files it did not govern, a permission gate vs the build it misread).

2. ★★ The target set is intersected with the seeder's own list, so a name that
   is not a built-in agent is ignored rather than honoured. Whatever is posted
   to the endpoint, it cannot disable a customer's own agent.

No database: a fake session returns the rows, so this stays in the fast fork
suite while still driving the real service method.
"""
import pytest

from app.services.organization_settings_service import OrganizationSettingsService
from app.services.default_agents_seeder import (
    FABRIC_AGENT_NAME, POWERBI_AGENT_NAME, CITYMART_AGENT_NAME,
)


class _DS:
    def __init__(self, name, status="published"):
        self.id = f"id-{name}"
        self.name = name
        self.publish_status = status
        self.organization_id = "org-1"
        self.deleted_at = None


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Returns whatever rows the test seeded, and records commits."""

    def __init__(self, rows):
        self.rows = rows
        self.commits = 0

    async def execute(self, _stmt):
        # The service filters by name in SQL; the fake returns everything and
        # lets the service's own intersection do the work — which is exactly
        # the property under test.
        return _Result(self.rows)

    async def commit(self):
        self.commits += 1


class _Org:
    id = "org-1"


class _User:
    id = "user-1"


@pytest.fixture
def svc():
    return OrganizationSettingsService()


def _rows_with_customer_agent():
    return [
        _DS(FABRIC_AGENT_NAME),
        _DS(POWERBI_AGENT_NAME),
        _DS(CITYMART_AGENT_NAME),
        _DS("CRM"),          # a customer's own agent — must never be touched
    ]


@pytest.mark.asyncio
async def test_turn_all_off_never_touches_a_customer_agent(svc):
    rows = _rows_with_customer_agent()
    db = _FakeSession(rows)

    await svc.set_builtin_agents(db, _Org(), _User(), enabled=False, names=None)

    by_name = {r.name: r.publish_status for r in rows}
    assert by_name[FABRIC_AGENT_NAME] == "disabled"
    assert by_name[POWERBI_AGENT_NAME] == "disabled"
    assert by_name[CITYMART_AGENT_NAME] == "disabled"
    assert by_name["CRM"] == "published", (
        "Turn all off disabled an agent the customer created — the target set "
        "must be intersected with the seeder's own list"
    )


@pytest.mark.asyncio
async def test_a_forged_name_is_ignored(svc):
    """Posting someone else's agent name must not disable it."""
    rows = _rows_with_customer_agent()
    db = _FakeSession(rows)

    await svc.set_builtin_agents(db, _Org(), _User(), enabled=False, names=["CRM"])

    by_name = {r.name: r.publish_status for r in rows}
    assert by_name["CRM"] == "published"
    # ...and nothing else moved either, since no built-in was named.
    assert by_name[FABRIC_AGENT_NAME] == "published"


@pytest.mark.asyncio
async def test_single_agent_toggle(svc):
    rows = _rows_with_customer_agent()
    db = _FakeSession(rows)

    await svc.set_builtin_agents(db, _Org(), _User(), enabled=False, names=[POWERBI_AGENT_NAME])

    by_name = {r.name: r.publish_status for r in rows}
    assert by_name[POWERBI_AGENT_NAME] == "disabled"
    assert by_name[FABRIC_AGENT_NAME] == "published", "only the named agent should move"
    assert by_name["CRM"] == "published"


@pytest.mark.asyncio
async def test_turning_back_on_restores_published(svc):
    rows = [_DS(n, "disabled") for n in
            (FABRIC_AGENT_NAME, POWERBI_AGENT_NAME, CITYMART_AGENT_NAME)]
    db = _FakeSession(rows)

    await svc.set_builtin_agents(db, _Org(), _User(), enabled=True, names=None)

    assert all(r.publish_status == "published" for r in rows)


@pytest.mark.asyncio
async def test_no_commit_when_nothing_changes(svc):
    """Re-sending the current state must not write."""
    rows = [_DS(FABRIC_AGENT_NAME, "published")]
    db = _FakeSession(rows)

    await svc.set_builtin_agents(db, _Org(), _User(), enabled=True, names=[FABRIC_AGENT_NAME])

    assert db.commits == 0


@pytest.mark.asyncio
async def test_listing_reports_state_and_treats_unknown_status_as_on(svc):
    """A NULL or unexpected status must not present as switched off.

    Reading it as "off" would tell an admin the agent is disabled when the AI is
    still using it — the worst direction for this particular error.
    """
    rows = [
        _DS(FABRIC_AGENT_NAME, None),
        _DS(POWERBI_AGENT_NAME, "draft"),
        _DS(CITYMART_AGENT_NAME, "disabled"),
    ]
    out = await svc.list_builtin_agents(_FakeSession(rows), _Org())

    state = {a["name"]: a["enabled"] for a in out}
    assert state[FABRIC_AGENT_NAME] is True
    assert state[POWERBI_AGENT_NAME] is True
    assert state[CITYMART_AGENT_NAME] is False


@pytest.mark.asyncio
async def test_listing_never_shows_a_customer_agent(svc):
    """The card must not list an agent it cannot act on."""
    out = await svc.list_builtin_agents(_FakeSession(_rows_with_customer_agent()), _Org())
    assert [a["name"] for a in out] == [
        FABRIC_AGENT_NAME, POWERBI_AGENT_NAME, CITYMART_AGENT_NAME,
    ], "CRM leaked into the built-in agents card"


@pytest.mark.asyncio
async def test_listing_is_empty_on_an_unseeded_workspace(svc):
    """No seeded agents → no card, rather than rows controlling nothing."""
    out = await svc.list_builtin_agents(_FakeSession([]), _Org())
    assert out == []


def test_names_come_from_the_seeder_not_a_second_copy():
    """Guards against someone re-typing the three names into the service."""
    import inspect
    src = inspect.getsource(OrganizationSettingsService._builtin_agent_names)
    assert "default_agents_seeder" in src, (
        "the built-in agent names must be imported from the seeder, not copied — "
        "a second list is how the member-permission drift happened"
    )
