"""Auto learn — one worker that keeps every agent current.

The product could already teach an agent, but only when a person asked, or as a
side effect of setting something up: first run, first model key, an upload, a
per-user sign-in. Nothing watched what happened afterwards. So an agent went
stale in two different ways and said nothing about either:

* **files it never read.** A document uploaded before sorting existed, or one
  whose ingest failed, sits attached and unused. It contributes no rules, no
  knowledge, no table — it is simply inert, and the file list shows it as
  "Not ingested" to nobody in particular.
* **tables that moved.** The overview names its tables and explains how to use
  them; remove one and the description keeps naming it. See `training_drift`.

This sweep is the single thing that closes both. Per agent, in order:

1. sort any file the agent has never read — which is what ADDS instructions,
   skills and knowledge, since that is where those come from,
2. if the tables no longer match what the last training described, re-read them
   and rewrite the overview.

Order matters: sorting a file can create a table, so doing it first means the
re-learn describes the schema that sorting produced rather than the one before
it — otherwise every new file would take two passes to be understood.

It only ever acts on agents whose owner turned Auto learn ON. Every other agent
is left alone entirely: the sweep costs model calls, and an agent nobody asked
to automate must not start spending because a scheduler exists.
"""
import logging
from datetime import datetime

from sqlalchemy import select

from app.models.data_source import DataSource
from app.services import training_drift

logger = logging.getLogger(__name__)

# A ceiling per tick, so one sweep cannot enqueue an unbounded amount of model
# work on an installation with hundreds of agents. The rest are picked up on the
# next tick — being slow is fine; being unbounded is not.
MAX_AGENTS_PER_SWEEP = 5


async def _unread_files(db, data_source) -> list:
    """Files attached to this agent that nothing has ever made use of.

    `source_kind == "upload"` is the product's own word for exactly that: not
    backing a table, not behind an instruction, not chunked into knowledge.
    """
    from app.models.data_source_file_association import data_source_file_association
    from app.models.file import File

    rows = (await db.execute(
        select(File)
        .join(data_source_file_association,
              data_source_file_association.c.file_id == File.id)
        .filter(
            data_source_file_association.c.data_source_id == str(data_source.id),
            File.deleted_at.is_(None),
        )
    )).scalars().all()
    return [f for f in rows if (getattr(f, "source_kind", "upload") or "upload") == "upload"]


async def auto_learn_agent(db, data_source, organization, user, now=None) -> dict:
    """Bring one agent up to date. Returns what it did, always.

    Never raises: it runs on a shared scheduler tick, and an agent whose
    connection is down must not take the sweep — or the other agents in it —
    with it.
    """
    now = now or datetime.utcnow()
    did = {"agent": getattr(data_source, "name", "?"), "files_sorted": 0,
           "retrained": False, "reason": ""}

    # 1. Files the agent has never read. This is the step that adds
    #    instructions and knowledge — sorting is where those come from.
    try:
        from app.services.file_service import FileService

        pending = await _unread_files(db, data_source)
        for f in pending:
            try:
                result = await FileService().reingest_file(
                    db, str(f.id), str(data_source.id), organization, user,
                )
                if result.get("handled") or result.get("destination"):
                    did["files_sorted"] += 1
            except Exception as err:
                logger.warning(f"auto learn: could not sort '{f.filename}': {err}")
    except Exception as err:
        logger.warning(f"auto learn: file pass failed for {data_source.id}: {err}")

    # 2. The tables. Re-read AFTER the files, because sorting one can create a
    #    table and the overview should describe the schema that resulted.
    try:
        from app.models.datasource_table import DataSourceTable

        tables = (await db.execute(
            select(DataSourceTable).filter(DataSourceTable.datasource_id == data_source.id)
        )).scalars().all()

        decision = training_drift.auto_decision(data_source, tables, now)
        did["reason"] = decision.get("reason", "")

        # A refusal can still carry work: the first sighting of a change starts
        # the quiet period, and that marker has to be written or the clock never
        # starts and the agent is never retrained.
        if decision.get("mark"):
            settings = dict(getattr(data_source, "training_settings", None) or {})
            settings.update(decision["mark"])
            data_source.training_settings = settings
            db.add(data_source)
            await db.commit()

        if decision.get("run"):
            from app.services.data_source_service import DataSourceService

            await DataSourceService().relearn_overview_now(
                str(data_source.id),
                str(user.id) if user else None,
                str(organization.id),
            )
            data_source.training_settings = training_drift.note_auto_run(data_source, now)
            db.add(data_source)
            await db.commit()
            did["retrained"] = True
            logger.info(
                f"auto learn: retrained '{did['agent']}' — {decision.get('summary', '')}"
            )
    except Exception as err:
        logger.warning(f"auto learn: table pass failed for {data_source.id}: {err}")
        did["reason"] = f"failed: {err}"

    return did


async def org_policy(db, organization) -> dict:
    """The organisation's Auto learn switch and budget.

    Read through the settings service so it goes past the same validation every
    other setting does. Never raises: a settings row that cannot be read must
    leave the sweep switched OFF rather than running unbudgeted.
    """
    try:
        # Read the row directly: the service's getter wants a current_user and a
        # scheduler tick has none. Reading is safe — it is WRITING an org setting
        # by hand that breaks the settings surface, because the stored object
        # must be a complete, validated block. Writes go through the API.
        from app.models.organization_settings import OrganizationSettings

        settings = (await db.execute(
            select(OrganizationSettings).filter(
                OrganizationSettings.organization_id == organization.id
            )
        )).scalars().first()
        cfg = (getattr(settings, "config", None) or {}) if settings else {}
        block = cfg.get("auto_learn") if isinstance(cfg, dict) else None
        if isinstance(block, dict):
            return {
                "enabled": bool(block.get("enabled", False)),
                "quiet_minutes": int(block.get("quiet_minutes", 30) or 30),
                "max_runs_per_day": int(block.get("max_runs_per_day", 12) or 12),
                "notify_on_train": bool(block.get("notify_on_train", True)),
            }
    except Exception as err:
        logger.warning(f"auto learn: could not read org policy: {err}")
    return {"enabled": False, "quiet_minutes": 30, "max_runs_per_day": 12,
            "notify_on_train": True}


async def runs_today(db, organization, now) -> int:
    """How many automatic runs this organisation has already spent today.

    Counted across every agent, because that total is the number a person
    actually cares about — a per-agent ceiling looks tidier and hides it.
    """
    today = now.date().isoformat()
    total = 0
    try:
        agents = (await db.execute(
            select(DataSource).filter(
                DataSource.organization_id == organization.id,
                DataSource.deleted_at.is_(None),
            )
        )).scalars().all()
        for a in agents:
            cfg = getattr(a, "training_settings", None) or {}
            if isinstance(cfg, dict) and cfg.get("auto_day") == today:
                total += int(cfg.get("auto_runs", 0) or 0)
    except Exception as err:
        logger.warning(f"auto learn: could not count today's runs: {err}")
    return total


async def sweep_auto_learn() -> None:
    """Scheduler entry point: every agent with Auto learn on, in one pass.

    Opens its own session and never raises — it shares a tick with other jobs,
    and a failure here costs freshness, never correctness.
    """
    try:
        from app.dependencies import async_session_maker
        from app.models.organization import Organization
        from app.models.user import User

        async with async_session_maker() as db:
            agents = (await db.execute(
                select(DataSource).filter(DataSource.deleted_at.is_(None))
            )).scalars().all()
            due = [a for a in agents if training_drift.mode_of(a) == training_drift.MODE_AUTO]
            if not due:
                return

            logger.info(f"auto learn: {len(due)} agent(s) opted in")
            # The organisation's switch and budget, cached per org so a sweep
            # over many agents does not re-read the same settings row each time.
            policies: dict = {}
            spent: dict = {}
            now = datetime.utcnow()

            for agent in due[:MAX_AGENTS_PER_SWEEP]:
                org = (await db.execute(
                    select(Organization).filter(Organization.id == agent.organization_id)
                )).scalar_one_or_none()
                if org is None:
                    continue

                oid = str(org.id)
                if oid not in policies:
                    policies[oid] = await org_policy(db, org)
                    spent[oid] = await runs_today(db, org, now)
                policy = policies[oid]

                # The master switch. An agent may be opted in individually and
                # still not run: turning the organisation's switch off has to
                # stop everything at once, without visiting each agent.
                if not policy["enabled"]:
                    continue

                # One ceiling shared by every agent. Reached, the sweep says so
                # rather than going quiet, because a silent stop and a broken
                # sweep look identical from outside.
                if spent[oid] >= policy["max_runs_per_day"]:
                    logger.info(
                        f"auto learn: daily limit reached for org {oid} "
                        f"({spent[oid]}/{policy['max_runs_per_day']}) — "
                        f"remaining agents wait until tomorrow"
                    )
                    continue
                # Attributed to the agent's owner. A learn has to run as
                # somebody: on a per-user connector the tables it can see, and
                # therefore the overview it writes, depend on whose account is
                # asking.
                owner = None
                if getattr(agent, "owner_user_id", None):
                    owner = (await db.execute(
                        select(User).filter(User.id == agent.owner_user_id)
                    )).scalar_one_or_none()
                did = await auto_learn_agent(db, agent, org, owner)
                if did["retrained"]:
                    spent[oid] += 1
                if did["files_sorted"] or did["retrained"]:
                    logger.info(f"auto learn: {did}")
            if len(due) > MAX_AGENTS_PER_SWEEP:
                logger.info(
                    f"auto learn: {len(due) - MAX_AGENTS_PER_SWEEP} agent(s) left for the next pass"
                )
    except Exception as err:
        logger.warning(f"auto learn sweep failed: {err}")
