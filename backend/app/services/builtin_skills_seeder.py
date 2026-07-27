"""Seed the shipped built-in skills (``builtin_skills.py``) into an organization.

Design
------
Idempotent by construction. Every row this creates is stamped
``ai_source = 'builtin:<slug>'``, which is both the lookup key and the rollback
predicate — one ``UPDATE ... WHERE ai_source LIKE 'builtin:%'`` undoes the whole
feature.

Unlike ``default_agents_seeder`` there is no org-level "already seeded" marker.
Skills attach to Fabric data sources, and a Fabric agent can be created long
after signup, so this is safe to call repeatedly: it creates what is missing,
updates text when ``BUILTIN_SKILLS_VERSION`` moves, and touches nothing else.

Deliberate behaviours
---------------------
- **A deleted skill stays deleted.** If a row exists with ``deleted_at`` set,
  the admin removed it on purpose; it is never resurrected.
- **Disabled stays disabled.** A version bump refreshes ``text`` and
  ``description`` only. ``status`` is left exactly as the admin set it, so
  turning a skill off survives an upgrade.
- **Never org-global.** If no Fabric data source exists yet, this does nothing
  and returns. An instruction with an empty data-source set is advertised to
  every agent in the organization, which is precisely the leak to avoid.
- Failures are swallowed by the caller-facing wrapper: seeding a skill must
  never break signup, sync, or sign-in.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_source import DataSource
from app.models.instruction import Instruction
from app.models.organization import Organization
from app.models.user import User
from app.services.builtin_skills import (
    BUILTIN_SKILLS_VERSION,
    BUILTIN_SKILL_CONNECTOR_TYPES,
    get_builtin_skills,
)

logger = logging.getLogger(__name__)

AI_SOURCE_PREFIX = "builtin:"


def _ai_source(slug: str) -> str:
    return f"{AI_SOURCE_PREFIX}{slug}"


async def _target_data_source_ids(
    db: AsyncSession, organization: Organization
) -> List[str]:
    """Data sources whose connector type these skills apply to.

    The connector type lives on ``Connection.type``, never on the DataSource —
    so connections must be eager-loaded or every row silently fails the test.
    """
    rows = (
        await db.execute(
            select(DataSource)
            .options(selectinload(DataSource.connections))
            .where(
                DataSource.organization_id == organization.id,
                DataSource.deleted_at.is_(None),
            )
        )
    ).scalars().all()

    out: List[str] = []
    for ds in rows:
        types = {getattr(c, "type", None) for c in (ds.connections or [])}
        if types & set(BUILTIN_SKILL_CONNECTOR_TYPES):
            out.append(str(ds.id))
    return out


async def _existing_by_slug(
    db: AsyncSession, organization: Organization
) -> Dict[str, Instruction]:
    """Every built-in row for this org, INCLUDING soft-deleted ones.

    Soft-deleted rows must be visible here or a deleted skill would be
    recreated on the next call.
    """
    rows = (
        await db.execute(
            select(Instruction)
            .options(selectinload(Instruction.data_sources))
            .where(
                Instruction.organization_id == organization.id,
                Instruction.ai_source.like(f"{AI_SOURCE_PREFIX}%"),
            )
        )
    ).scalars().all()
    return {(r.ai_source or "")[len(AI_SOURCE_PREFIX):]: r for r in rows}


async def sync_builtin_skills(
    db: AsyncSession,
    organization: Organization,
    current_user: User,
) -> Dict[str, int]:
    """Create/refresh the shipped skills for `organization`. Idempotent.

    Returns a small tally: ``{created, updated, skipped, deleted_kept}``.
    """
    tally = {"created": 0, "updated": 0, "skipped": 0, "deleted_kept": 0}

    ds_ids = await _target_data_source_ids(db, organization)
    if not ds_ids:
        logger.info(
            "builtin skills: no %s data source in org %s — nothing to attach to",
            "/".join(BUILTIN_SKILL_CONNECTOR_TYPES), organization.id,
        )
        return tally

    existing = await _existing_by_slug(db, organization)

    from app.schemas.instruction_schema import InstructionCreate
    from app.services.instruction_service import InstructionService

    svc = InstructionService()

    for spec in get_builtin_skills():
        slug = spec["slug"]
        row = existing.get(slug)

        if row is not None and row.deleted_at is not None:
            # Removed on purpose. Leave it removed.
            tally["deleted_kept"] += 1
            continue

        if row is not None:
            changed = False
            if (row.structured_data or {}).get("builtin_version") != BUILTIN_SKILLS_VERSION:
                # Refresh the shipped content, but never override the admin's
                # own status choice (a disabled skill stays disabled).
                row.text = spec["text"]
                row.description = spec["description"]
                row.title = spec["title"]
                sd = dict(row.structured_data or {})
                sd["builtin_version"] = BUILTIN_SKILLS_VERSION
                row.structured_data = sd
                changed = True

            # Re-attach any Fabric data source added since the last run.
            attached = {str(d.id) for d in (row.data_sources or [])}
            missing = [i for i in ds_ids if i not in attached]
            if missing:
                for ds in (
                    await db.execute(
                        select(DataSource).where(DataSource.id.in_(missing))
                    )
                ).scalars().all():
                    row.data_sources.append(ds)
                changed = True

            if changed:
                await db.commit()
                tally["updated"] += 1
            else:
                tally["skipped"] += 1
            continue

        payload = InstructionCreate(
            text=spec["text"],
            title=spec["title"],
            description=spec["description"],
            kind="skill",
            # Skills are pull-on-demand; 'intelligent' is what the UI forces
            # for kind='skill' and what the catalog expects.
            load_mode="intelligent",
            category="general",
            status="published",
            ai_source=_ai_source(slug),
            data_source_ids=ds_ids,
        )
        created = await svc.create_instruction(
            db=db,
            instruction_data=payload,
            current_user=current_user,
            organization=organization,
        )
        # Stamp the version so a later bump can refresh the text in place.
        inst = (
            await db.execute(select(Instruction).where(Instruction.id == created.id))
        ).scalar_one()
        sd = dict(inst.structured_data or {})
        sd["builtin_version"] = BUILTIN_SKILLS_VERSION
        inst.structured_data = sd
        await db.commit()
        tally["created"] += 1

    logger.info("builtin skills: org %s %s", organization.id, tally)
    return tally


async def sync_builtin_skills_safe(
    db: AsyncSession,
    organization: Organization,
    current_user: Optional[User],
) -> Dict[str, int]:
    """`sync_builtin_skills` that can never break its caller."""
    tally = {"created": 0, "updated": 0, "skipped": 0, "deleted_kept": 0}
    if current_user is None:
        return tally
    try:
        return await sync_builtin_skills(db, organization, current_user)
    except Exception:  # noqa: BLE001
        logger.warning("builtin skills: seeding failed (ignored)", exc_info=True)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return tally
