"""Instance-wide feature switches: who may flip them, and what the states mean.

App Analytics shipped complete in `0.0.489.6` and was invisible on a live
deployment until 2026-08-03 — eleven releases — because it was gated on an
environment variable nobody had set. A feature nobody can find is
indistinguishable from a feature nobody wrote. So the switch moved into the
product, and this file holds down the three things that makes non-obvious.

**Three states, not two.** on / off / not-chosen. If "reset" wrote `false`
instead of clearing the key, the deployment's own default would become
unreachable and an operator setting the env var would find it silently ignored.

**`is_superuser`, not `manage_settings`.** Every other Settings screen gates on
the latter, which an organization admin holds. These switches change the product
for every organization on the deployment, so an org admin holding one would be
administering other people's organizations.

**The endpoint obeys the switch too.** Hiding a nav item is a display choice.
`/api/console/app-analytics` returns org-wide usage, per-user activity and cost;
when a super admin turns the page off, that has to stop being served, not merely
stop being linked.

★These need a schema, so they live here and NOT in `tests/unit/fork` — see
CLAUDE.md.
"""
from __future__ import annotations

import pytest

from app.dependencies import async_session_maker
from app.models.instance_settings import InstanceSettings
from app.services import instance_features
from app.settings.config import settings


# ────────────────────────── the three states ──────────────────────────────


@pytest.mark.asyncio
async def test_app_analytics_is_on_when_nobody_has_chosen():
    """★The fix for the eleven silent releases. Nothing stored, and the answer
    is still yes — because the shipped default now says yes."""
    async with async_session_maker() as db:
        value, source = await instance_features.resolve_with_source(db, "app_analytics")

    assert value is True
    assert source == "default"
    assert settings.hybrid_app_analytics is True, (
        "the environment default itself must be on — this is what every "
        "deployment that never sets the variable inherits"
    )


@pytest.mark.asyncio
async def test_a_stored_choice_outranks_the_default():
    async with async_session_maker() as db:
        await instance_features.set_feature(db, "app_analytics", False)
        value, source = await instance_features.resolve_with_source(db, "app_analytics")

    assert value is False
    assert source == "db", "the UI must be able to say a person chose this"


@pytest.mark.asyncio
async def test_it_can_be_turned_back_on_again():
    """Both directions. A switch that only latches one way is a trap."""
    async with async_session_maker() as db:
        await instance_features.set_feature(db, "app_analytics", False)
        await instance_features.set_feature(db, "app_analytics", True)
        value, source = await instance_features.resolve_with_source(db, "app_analytics")

    assert value is True and source == "db"


@pytest.mark.asyncio
async def test_resetting_clears_the_override_rather_than_storing_false():
    """★The state that is easy to get wrong. `None` must REMOVE the key.

    Writing `False` for a reset would look identical from the UI and pin the
    switch off forever: the deployment's default could never apply again, and an
    operator flipping the env var would see no effect and no explanation.
    """
    async with async_session_maker() as db:
        await instance_features.set_feature(db, "app_analytics", False)
        await instance_features.set_feature(db, "app_analytics", None)

        value, source = await instance_features.resolve_with_source(db, "app_analytics")
        assert value is True and source == "default"

        # And the key is genuinely gone, not stored as null.
        inst = await InstanceSettings.get_or_create(db)
        block = (inst.config or {}).get("features") or {}
        assert "app_analytics" not in block


@pytest.mark.asyncio
async def test_read_all_reports_the_default_alongside_the_value():
    """So the UI can offer "use the server default" without a second request,
    and an operator can see what removing the override would do."""
    async with async_session_maker() as db:
        await instance_features.set_feature(db, "app_analytics", False)
        payload = await instance_features.read_all(db)

    assert payload["app_analytics"] == {
        "value": False, "source": "db", "default": True,
    }


@pytest.mark.asyncio
async def test_an_unknown_switch_is_refused_not_stored():
    """A typo that is accepted sits in the database looking exactly like a real
    switch, while nothing reads it."""
    async with async_session_maker() as db:
        with pytest.raises(ValueError):
            await instance_features.set_feature(db, "app_analytcs", True)

        assert await instance_features.resolve(db, "app_analytcs") is False


@pytest.mark.asyncio
async def test_a_broken_settings_row_falls_back_instead_of_failing():
    """`/api/settings` is public and hit on every page load, including the login
    page. A settings read must never be able to take it down."""
    async with async_session_maker() as db:
        inst = await InstanceSettings.get_or_create(db)
        # A shape nothing should ever write, but a hand-edited row could.
        inst.config = {"features": "not-a-dict"}
        await db.commit()

        assert await instance_features.resolve(db, "app_analytics") is True


# ─────────────────────────── who may flip it ──────────────────────────────


class _Person:
    def __init__(self, is_superuser: bool):
        self.is_superuser = is_superuser


def test_an_org_admin_who_is_not_a_super_admin_is_refused():
    """★The permission boundary. `manage_settings` administers ONE organization;
    this switch changes the product for all of them."""
    from fastapi import HTTPException
    from app.routes.instance_features import _require_super_admin

    with pytest.raises(HTTPException) as caught:
        _require_super_admin(_Person(is_superuser=False))

    assert caught.value.status_code == 403
    detail = str(caught.value.detail)
    assert "every organization" in detail and "super admin" in detail, (
        "an org admin holds every permission their own screens ask for — the "
        "refusal has to say which power is missing, not just say no"
    )


def test_a_super_admin_is_allowed():
    from app.routes.instance_features import _require_super_admin

    _require_super_admin(_Person(is_superuser=True))  # must not raise


def test_a_user_object_with_no_flag_at_all_is_refused():
    """Fail closed. A caller shape we did not anticipate must not be treated as
    privileged because an attribute happened to be missing."""
    from fastapi import HTTPException
    from app.routes.instance_features import _require_super_admin

    class _Nothing:
        pass

    with pytest.raises(HTTPException):
        _require_super_admin(_Nothing())


# ──────────────────── the endpoint obeys the switch ───────────────────────


@pytest.mark.asyncio
async def test_turning_it_off_also_stops_the_data_being_served():
    """★A hidden nav item over a live endpoint is not "off" — it is off for
    people who do not use the network tab. This endpoint returns org-wide usage,
    per-user activity and cost."""
    import inspect
    from app.routes import console

    source = inspect.getsource(console.get_app_analytics)
    assert 'resolve(db, "app_analytics")' in source, (
        "the App Analytics endpoint must resolve the same switch the nav item "
        "does; without this the page is merely unlinked"
    )
    guard = source.index('resolve(db, "app_analytics")')
    body = source.index("get_app_analytics(")
    assert guard < source.index("app_analytics_service.get_app_analytics"), (
        "the switch must be checked BEFORE the payload is computed"
    )
    assert body < guard


@pytest.mark.asyncio
async def test_the_public_feed_serves_the_resolved_value_not_the_raw_default():
    """`/api/settings` is what the nav item reads. If it kept serving
    `settings.hybrid_app_analytics` directly, the toggle would save correctly
    and change nothing anyone could see."""
    import inspect
    from app.routes import dash_settings

    source = inspect.getsource(dash_settings.get_frontend_settings)
    assert '"app_analytics": _app_analytics' in source
    assert '"app_analytics": settings.hybrid_app_analytics' not in source
