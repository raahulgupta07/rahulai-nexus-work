"""Default-agents seeder — first-install bootstrap of ready-to-use agents.

On a FRESH installation, right after the very first organization is created
(first signup → the org-bootstrap in ``core/auth.py``), this seeds three
ready-to-use agents so a brand-new admin lands on a populated workspace instead
of an empty one:

  1. **Microsoft Fabric** — a config-less ``fabric_user`` connection + agent,
     ``is_public=true``. Each member signs in later with their own Microsoft
     account (device-code); nothing is queryable until they connect — that's
     correct, the agent starts empty.
  2. **Power BI** — identical per-user pattern with type ``powerbi_user``,
     ``is_public=true``.
  3. **City Mart Retail** — installs the bundled DuckDB sample (via the existing
     demo installer), creates a public agent on it, activates all its tables, and
     applies the sample's teaching instructions + conversation starters.

Design guarantees (the signup path must be UNBREAKABLE):
  - Flag-gated on ``settings.seed_default_agents`` (env ``SEED_DEFAULT_AGENTS``,
    default true).
  - Idempotent: a marker (``default_agents_seeded``) is stored in the org's
    ``OrganizationSettings.config`` blob and checked before doing anything.
  - Defensive per-agent skip: an agent whose name already exists in the org
    (even soft-deleted — the ``data_sources`` unique slot survives soft-delete)
    is skipped rather than colliding.
  - EVERY failure — the whole run and each individual agent — is caught and
    logged. The seeder NEVER raises, so a seeding hiccup can never break signup.
  - NO blocking LLM call is ever made on the signup path. Seeded agents are
    created with ``use_llm_sync=False``; a nice AI overview is generated LATER by
    a fire-and-forget background relearn (``schedule_overview_relearn``). Fabric/
    Power BI have no tables until a user signs in, so they need no learning.

Only ever fires for the FIRST org: the single call site
(``_ensure_org_for_first_uninvited_user``) runs only when ``total_users == 1``
and ``total_orgs == 0``. An already-provisioned install (the live org) is past
that point forever, so it is never retroactively seeded.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.data_source import DataSource
from app.models.organization import Organization
from app.models.organization_settings import OrganizationSettings
from app.models.user import User
from app.settings.config import settings

logger = logging.getLogger(__name__)

# Marker stored in OrganizationSettings.config to make seeding idempotent.
SEEDED_MARKER_KEY = "default_agents_seeded"

# Agent (data source) names — also used as the defensive "already exists" key.
FABRIC_AGENT_NAME = "Microsoft Fabric"
POWERBI_AGENT_NAME = "Power BI"
CITYMART_AGENT_NAME = "City Mart Retail"

# The bundled sample registered in the demo schema.
CITYMART_DEMO_ID = "citymart"

# ★The three agents that are part of the product, not part of anyone's data.
#
# They are seeded at first signup and, from now on, restored at every boot and
# refused by the delete path. `is_default_agent_name` is the ONE place that
# decides membership of that set, so the healer and the delete guard can never
# drift apart and disagree about which agents are permanent.
DEFAULT_AGENT_NAMES = (FABRIC_AGENT_NAME, POWERBI_AGENT_NAME, CITYMART_AGENT_NAME)


def is_default_agent_name(name: Any) -> bool:
    """True when ``name`` is one of the three permanent default agents.

    Case- and whitespace-insensitive: an admin who renames "power bi" back by
    hand should still land on the protected row, and a stray trailing space in a
    seeded name must not quietly turn a permanent agent into a deletable one.
    """
    if not isinstance(name, str):
        return False
    needle = name.strip().casefold()
    return any(needle == n.casefold() for n in DEFAULT_AGENT_NAMES)


async def _ds_name_exists(db: AsyncSession, organization_id: str, name: str) -> bool:
    """True if a data source with this name already exists in the org.

    Includes soft-deleted rows on purpose: the ``data_sources`` (organization_id,
    name) unique constraint is not filtered by ``deleted_at``, so a soft-deleted
    agent with this name still owns the slot and a create would 409. Skipping when
    the name is taken keeps the seeder idempotent + collision-free.
    """
    stmt = select(DataSource.id).where(
        DataSource.organization_id == str(organization_id),
        DataSource.name == name,
    ).execution_options(include_deleted=True)
    res = await db.execute(stmt)
    return res.scalars().first() is not None


async def _seed_user_login_agent(
    db: AsyncSession,
    organization: Organization,
    user: User,
    *,
    ds_type: str,
    agent_name: str,
    config: Dict[str, Any],
    summary: Dict[str, Any],
) -> None:
    """Seed one config-less per-user agent (fabric_user / powerbi_user).

    Creates a blank-config connection of ``ds_type`` (``auth_policy=user_required``)
    plus a public agent on it via the real ``create_data_source`` path, so
    ownership, membership, audit, and telemetry all match a hand-created agent.
    No schema, no LLM, no connection test (user_required skips validation) — users
    press Connect later and their own tables appear per-user.
    """
    from app.schemas.data_source_schema import DataSourceCreate
    from app.services.data_source_service import DataSourceService

    if await _ds_name_exists(db, organization.id, agent_name):
        summary["skipped"].append({"agent": agent_name, "reason": "already_exists"})
        logger.info("default_agents_seeder: '%s' already exists — skipping", agent_name)
        return

    ds_create = DataSourceCreate(
        name=agent_name,
        type=ds_type,
        config=config,
        credentials=None,
        auth_policy="user_required",
        is_public=True,
        use_llm_sync=False,
    )
    ds = await DataSourceService().create_data_source(
        db=db, organization=organization, current_user=user, data_source=ds_create,
    )
    summary["created"].append({
        "agent": agent_name, "type": ds_type, "data_source_id": str(ds.id),
        "is_public": True, "instructions": False,
    })
    logger.info("default_agents_seeder: created '%s' (%s) id=%s", agent_name, ds_type, ds.id)


async def _seed_citymart_agent(
    db: AsyncSession,
    organization: Organization,
    user: User,
    summary: Dict[str, Any],
) -> None:
    """Install the City Mart Retail sample and create a public agent on it.

    Reuses the existing demo installer for the connection + schema, links a public
    agent, activates ALL sample tables, and applies the sample's teaching
    instructions + conversation starters when present. use_llm_sync=False; a
    background relearn (fire-and-forget) generates the AI overview later so signup
    is never blocked on an LLM call.
    """
    from app.schemas.data_source_schema import DataSourceCreate
    from app.schemas.demo_data_source_schema import get_demo_data_source
    from app.services.data_source_service import DataSourceService
    from app.services.demo_data_source_service import DemoDataSourceService

    demo = get_demo_data_source(CITYMART_DEMO_ID)
    if not demo:
        summary["skipped"].append({"agent": CITYMART_AGENT_NAME, "reason": "demo_not_registered"})
        logger.warning("default_agents_seeder: demo '%s' not registered — skipping", CITYMART_DEMO_ID)
        return

    if await _ds_name_exists(db, organization.id, CITYMART_AGENT_NAME):
        summary["skipped"].append({"agent": CITYMART_AGENT_NAME, "reason": "already_exists"})
        logger.info("default_agents_seeder: '%s' already exists — skipping", CITYMART_AGENT_NAME)
        return

    demo_svc = DemoDataSourceService()
    ds_svc = DataSourceService()

    # 1) Register the sample database (connection + ConnectionTable schema). This
    #    reuses/resurrects an existing same-named connection when present.
    connection = await demo_svc._create_demo_data_source(
        db=db, organization=organization, current_user=user, demo=demo,
    )

    # 2) Create a PUBLIC agent linked to that connection (Mode 2 seeds
    #    DataSourceTable from the connection's catalog).
    ds_create = DataSourceCreate(
        name=CITYMART_AGENT_NAME,
        connection_id=str(connection.id),
        is_public=True,
        use_llm_sync=False,
    )
    ds_schema = await ds_svc.create_data_source(
        db=db, organization=organization, current_user=user, data_source=ds_create,
    )

    # 3) Load the agent's ORM row and activate ALL sample tables (the onboarding
    #    auto-select would otherwise leave some inactive). Reuses the demo
    #    service's loader (refresh + sync with max_auto_select=9999), which
    #    swallows its own errors.
    ds = (await db.execute(
        select(DataSource).where(DataSource.id == ds_schema.id)
    )).scalars().first()
    if ds is not None:
        try:
            await demo_svc._load_tables(db, ds, organization, user)
        except Exception as e:  # noqa: BLE001
            logger.warning("default_agents_seeder: citymart table load failed: %s", e)

        # The create_data_source onboarding sync already inserted the rows
        # inactive (ONBOARDING_MAX_TABLES=0) and the loader's re-sync preserves
        # is_active on existing rows — so activate every sample table directly.
        try:
            from app.models.datasource_table import DataSourceTable
            rows = (await db.execute(
                select(DataSourceTable).where(DataSourceTable.datasource_id == ds.id)
            )).scalars().all()
            for row in rows:
                row.is_active = True
                db.add(row)
            await db.commit()
        except Exception as e:  # noqa: BLE001
            await db.rollback()
            logger.warning("default_agents_seeder: citymart table activation failed: %s", e)

        # 4) Conversation starters (from the sample definition).
        if demo.conversation_starters:
            try:
                ds.conversation_starters = list(demo.conversation_starters)
                db.add(ds)
                await db.commit()
                await db.refresh(ds)
            except Exception as e:  # noqa: BLE001
                await db.rollback()
                logger.warning("default_agents_seeder: citymart starters failed: %s", e)

    # 5) Teaching instructions (published, scoped to this agent) when the sample
    #    carries them. Reuses the demo service's instruction builder.
    has_instructions = bool(demo.instructions)
    if has_instructions and ds is not None:
        try:
            await demo_svc._create_instructions(db, ds, organization, user, demo)
        except Exception as e:  # noqa: BLE001
            logger.warning("default_agents_seeder: citymart instructions failed: %s", e)
            has_instructions = False

    # 6) Best-effort background AI overview (never blocks, never raises).
    try:
        ds_svc.schedule_overview_relearn(
            data_source_id=str(ds_schema.id),
            user_id=str(user.id),
            organization_id=str(organization.id),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("default_agents_seeder: citymart relearn schedule failed: %s", e)

    summary["created"].append({
        "agent": CITYMART_AGENT_NAME, "type": demo.type,
        "data_source_id": str(ds_schema.id), "is_public": True,
        "instructions": bool(has_instructions),
    })
    logger.info("default_agents_seeder: created '%s' id=%s", CITYMART_AGENT_NAME, ds_schema.id)


async def _mark_seeded(db: AsyncSession, organization: Organization, user: User) -> None:
    """Stamp the idempotency marker into the org's settings config blob.

    Mirrors the ``ldap``/``smtp`` config-block pattern: mutate a copy of the
    ``json`` config, ``flag_modified``, commit.
    """
    from app.services.organization_settings_service import OrganizationSettingsService

    settings_row = await OrganizationSettingsService().get_settings(db, organization, user)
    cfg = dict(settings_row.config) if isinstance(settings_row.config, dict) else {}
    cfg[SEEDED_MARKER_KEY] = True
    settings_row.config = cfg
    flag_modified(settings_row, "config")
    db.add(settings_row)
    await db.commit()


async def _already_seeded(db: AsyncSession, organization: Organization, user: User) -> bool:
    """True if this org's config blob already carries the seeded marker."""
    from app.services.organization_settings_service import OrganizationSettingsService

    settings_row = await OrganizationSettingsService().get_settings(db, organization, user)
    cfg = settings_row.config if isinstance(settings_row.config, dict) else {}
    return bool(cfg.get(SEEDED_MARKER_KEY))


async def seed_default_agents(
    db: AsyncSession,
    organization: Organization,
    user: User,
) -> Dict[str, Any]:
    """Seed the three default agents for a freshly-created org. Never raises.

    Returns a summary dict: ``{status, created:[...], skipped:[...], errors:[...]}``.
    The whole body is wrapped so any failure is logged and swallowed — a seeding
    problem must never break the signup that triggered it.
    """
    summary: Dict[str, Any] = {"status": "ok", "created": [], "skipped": [], "errors": []}

    if not getattr(settings, "seed_default_agents", True):
        summary["status"] = "disabled"
        logger.info("default_agents_seeder: SEED_DEFAULT_AGENTS off — skipping")
        return summary

    try:
        if await _already_seeded(db, organization, user):
            summary["status"] = "already_seeded"
            logger.info("default_agents_seeder: org %s already seeded — skipping", organization.id)
            return summary
    except Exception as e:  # noqa: BLE001
        # If we can't even read the marker, log and bail — do NOT seed blindly.
        logger.warning("default_agents_seeder: marker check failed for org %s: %s", organization.id, e)
        summary["status"] = "marker_check_failed"
        summary["errors"].append({"stage": "marker_check", "error": str(e)})
        return summary

    logger.info("default_agents_seeder: seeding default agents for org %s", organization.id)

    # Each agent is independent — one failing must not stop the others. Roll the
    # session back after a failure so the next create starts from a clean slate.
    for label, coro_factory in (
        ("fabric", lambda: _seed_user_login_agent(
            db, organization, user,
            ds_type="fabric_user", agent_name=FABRIC_AGENT_NAME,
            config={"server_hostname": "", "database": "", "schema": "",
                    "tenant_id": "", "auth_type": "user_login"},
            summary=summary,
        )),
        ("powerbi", lambda: _seed_user_login_agent(
            db, organization, user,
            ds_type="powerbi_user", agent_name=POWERBI_AGENT_NAME,
            config={"default_tenant_id": "", "auth_type": "user_login"},
            summary=summary,
        )),
        ("citymart", lambda: _seed_citymart_agent(db, organization, user, summary)),
    ):
        try:
            await coro_factory()
        except Exception as e:  # noqa: BLE001
            logger.warning("default_agents_seeder: seeding '%s' failed: %s", label, e, exc_info=True)
            summary["errors"].append({"agent": label, "error": str(e)})
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                pass

    # Attach the shipped built-in skills to whatever Fabric agent was just
    # seeded. Kept OUT of the marker below on purpose: this call is idempotent
    # on its own and must stay re-runnable for orgs that add a Fabric agent
    # later. Its own wrapper swallows every error.
    try:
        from app.services.builtin_skills_seeder import sync_builtin_skills_safe
        summary["builtin_skills"] = await sync_builtin_skills_safe(db, organization, user)
    except Exception as e:  # noqa: BLE001
        logger.warning("default_agents_seeder: builtin skills failed: %s", e, exc_info=True)

    # Stamp the marker so a re-entry can't double-seed. Best-effort: even if we
    # created nothing (all skipped), marking prevents pointless re-runs.
    try:
        await _mark_seeded(db, organization, user)
    except Exception as e:  # noqa: BLE001
        logger.warning("default_agents_seeder: could not set seeded marker for org %s: %s", organization.id, e)
        summary["errors"].append({"stage": "mark_seeded", "error": str(e)})

    logger.info(
        "default_agents_seeder: done for org %s — created=%d skipped=%d errors=%d",
        organization.id, len(summary["created"]), len(summary["skipped"]), len(summary["errors"]),
    )
    return summary


# ---------------------------------------------------------------------------
# Boot-time self-heal
# ---------------------------------------------------------------------------
#
# ★Why this exists, and why the signup seeder was not enough.
#
# `seed_default_agents` fires once, for the FIRST org, and stamps
# `default_agents_seeded` into the org's settings. After that it can never run
# again. The three default agents were therefore protected only by nobody ever
# deleting their rows — and `delete_data_source` is a HARD delete
# (`await db.delete(data_source)`), leaving no tombstone to notice the loss by.
#
# So the failure is silent and permanent: the rows go, the marker still reads
# `true`, and the workspace comes up missing agents the product considers part
# of itself. That is exactly what happened on this install — the marker said
# seeded while Microsoft Fabric and Power BI were gone.
#
# This restores anything missing at every boot. It is deliberately NOT gated on
# the marker: the marker records that signup ran, which is a different question
# from whether the agents are present now.


async def _owner_user_for_org(db: AsyncSession, organization: Organization):
    """A user to attribute a healed agent to. Admin first, then any member.

    Creation goes through the real `create_data_source` path, which needs a
    `current_user` for ownership, audit and telemetry. Preferring an admin keeps
    the healed row indistinguishable from one the admin made by hand.
    """
    from app.models.membership import Membership

    stmt = (
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.organization_id == str(organization.id),
            Membership.deleted_at.is_(None),
        )
        # admin first, then oldest membership — deterministic across boots so a
        # healed agent does not change owner every restart.
        .order_by((Membership.role != "admin"), Membership.created_at.asc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


async def ensure_default_agents(
    db: AsyncSession,
    organization: Organization,
) -> Dict[str, Any]:
    """Recreate any of the three default agents that are missing from this org.

    Never raises. Returns ``{status, restored:[...], present:[...], errors:[...]}``.

    Each agent is checked by name and created through the same helpers the
    signup seeder uses, so a healed agent is byte-for-byte the agent a fresh
    install would have got. An agent that is already there is left completely
    alone — this never touches configuration, credentials, tables or
    instructions on an existing row.
    """
    summary: Dict[str, Any] = {"status": "ok", "restored": [], "present": [], "errors": []}

    if not getattr(settings, "seed_default_agents", True):
        summary["status"] = "disabled"
        return summary

    try:
        user = await _owner_user_for_org(db, organization)
    except Exception as e:  # noqa: BLE001
        logger.warning("ensure_default_agents: owner lookup failed for org %s: %s", organization.id, e)
        summary["status"] = "owner_lookup_failed"
        summary["errors"].append({"stage": "owner_lookup", "error": str(e)})
        return summary

    if user is None:
        # An org with no members cannot own an agent. Not an error: a half-built
        # org is a normal transient state during signup.
        summary["status"] = "no_owner"
        return summary

    # A seeder summary shape, so the per-agent helpers can be reused verbatim.
    seed_summary: Dict[str, Any] = {"created": [], "skipped": [], "errors": []}

    for label, agent_name, factory in (
        ("fabric", FABRIC_AGENT_NAME, lambda: _seed_user_login_agent(
            db, organization, user,
            ds_type="fabric_user", agent_name=FABRIC_AGENT_NAME,
            config={"server_hostname": "", "database": "", "schema": "",
                    "tenant_id": "", "auth_type": "user_login"},
            summary=seed_summary,
        )),
        ("powerbi", POWERBI_AGENT_NAME, lambda: _seed_user_login_agent(
            db, organization, user,
            ds_type="powerbi_user", agent_name=POWERBI_AGENT_NAME,
            config={"default_tenant_id": "", "auth_type": "user_login"},
            summary=seed_summary,
        )),
        ("citymart", CITYMART_AGENT_NAME, lambda: _seed_citymart_agent(
            db, organization, user, seed_summary,
        )),
    ):
        try:
            if await _ds_name_exists(db, organization.id, agent_name):
                summary["present"].append(agent_name)
                continue
        except Exception as e:  # noqa: BLE001
            # ★An unreadable existence check is NOT permission to create. A
            # duplicate would collide on the (organization_id, name) unique slot
            # and, worse, could shadow the real agent.
            logger.warning("ensure_default_agents: existence check failed for '%s': %s", agent_name, e)
            summary["errors"].append({"agent": label, "stage": "exists_check", "error": str(e)})
            continue

        try:
            await factory()
            summary["restored"].append(agent_name)
            logger.info(
                "ensure_default_agents: restored missing default agent '%s' for org %s",
                agent_name, organization.id,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "ensure_default_agents: restoring '%s' failed for org %s: %s",
                agent_name, organization.id, e, exc_info=True,
            )
            summary["errors"].append({"agent": label, "error": str(e)})
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                pass

    if summary["restored"]:
        # Built-in skills attach to the Fabric agent; re-run when we just made
        # one. Idempotent on its own and swallows its own errors.
        try:
            from app.services.builtin_skills_seeder import sync_builtin_skills_safe
            await sync_builtin_skills_safe(db, organization, user)
        except Exception as e:  # noqa: BLE001
            logger.warning("ensure_default_agents: builtin skills failed: %s", e, exc_info=True)

    return summary


async def ensure_default_agents_all_orgs() -> Dict[str, Any]:
    """Boot entry point: heal every organization. Never raises.

    Opens its own session because it runs from the startup event, outside any
    request. Leader-gated by the caller so N workers do not race to create the
    same agent.
    """
    result: Dict[str, Any] = {"orgs": 0, "restored": 0, "errors": 0}

    try:
        from app.settings.database import create_async_session_factory
        session_factory = create_async_session_factory()
    except Exception as e:  # noqa: BLE001
        logger.warning("ensure_default_agents_all_orgs: no session factory: %s", e)
        return result

    try:
        async with session_factory() as db:
            orgs = (await db.execute(
                select(Organization).where(Organization.deleted_at.is_(None))
            )).scalars().all()
            for org in orgs:
                result["orgs"] += 1
                summary = await ensure_default_agents(db, org)
                result["restored"] += len(summary.get("restored") or [])
                result["errors"] += len(summary.get("errors") or [])
    except Exception as e:  # noqa: BLE001
        logger.warning("ensure_default_agents_all_orgs failed: %s", e, exc_info=True)
        result["errors"] += 1

    if result["restored"]:
        logger.info(
            "ensure_default_agents_all_orgs: restored %d default agent(s) across %d org(s)",
            result["restored"], result["orgs"],
        )
    return result
