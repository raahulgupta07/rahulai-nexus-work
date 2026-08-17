"""A downloadable copy of one person's work.

The transfer paths (0.0.531.4–.6) all move ownership *inside* this install. This
is the layer underneath them: a file somebody can keep. It answers the case none
of the others can — the install itself is gone, or rolled back, or the
organization is leaving the product.

★★★**What the bundle contains, and the one decision behind it.**

`Step.data` holds the last computed RESULT ROWS, so a bundle that includes them
is a copy of real business data rather than a copy of the definitions. The
tempting rule is "exclude results when an admin exports somebody else's work" —
and it is **theatre**. An administrator holding `full_admin_access` can already
GET every one of those steps through the ordinary report API; withholding them
here removes nothing they cannot have, while making the backup useless for the
exact scenario it exists for. The honest control is not to block a capability
they already hold, but to make bulk use of it **visible**: every export writes
an audit row naming who exported whose content and how much.

★★★**What it must NEVER contain: credentials, or anything reachable from a
connection.** Every field below is named explicitly. Nothing here dumps an ORM
object, because a dump exports whatever a future migration adds — and the day
somebody adds a decrypted field to a model, a whitelist keeps it out of a file
users email to each other and a dump does not. `data_source` is represented by
name only, never by config.

★A report title becomes a path inside the zip. Titles are user-supplied, so
every one is sanitized: a report called ``../../etc/passwd`` must not decide
where a file lands when somebody unzips this on their laptop.

★★★**Uploaded files are not in here, and that is the same decision the transfer
made.** ``ownership_service``'s module docstring explains why ``File.user_id``
is never moved — it is an access grant, not authorship. Exporting the bytes
would be the same mistake wearing different clothes: a zip that hands whoever
opens it everything the person ever uploaded, with none of the report-visibility
checks that gate ``routes/file.py``. Nothing is stranded by leaving them out —
a file is reached through the report or the agent that references it, and both
of those are still in the product. The README says so out loud rather than
letting a reader assume a complete backup.

★★★**The private filters are IMPORTED, never re-spelled.** A bundle containing
a private instruction the transfer refused to move is a leak with an audit row
pointing straight at us, and two spellings of one rule is how that day arrives.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.artifact import Artifact
from app.models.data_source import DataSource
from app.models.instruction import Instruction
from app.models.note import Note
from app.models.project import Project
from app.models.prompt import Prompt
from app.models.report import Report
from app.models.scheduled_prompt import ScheduledPrompt
from app.models.step import Step
from app.models.user import User
from app.models.widget import Widget
from app.services.ownership_service import (
    _non_private_prompts_only,
    _shared_instructions_only,
)

# A cap on the result rows carried in one bundle. Beyond this, steps keep their
# definition and lose their data — recorded in the manifest, never silent. A
# bundle that quietly stops including things is worse than one that says it
# stopped, because it is indistinguishable from a complete backup.
DEFAULT_MAX_RESULT_BYTES = 64 * 1024 * 1024

_UNSAFE = re.compile(r"[^A-Za-z0-9 ._-]+")


def _safe(name: Optional[str], fallback: str) -> str:
    """A user-supplied title, made safe to be a path inside a zip.

    ★Path traversal is the obvious half. The subtle half is that `..` survives
    a naive character filter — dots are legal in filenames — so the collapsed
    result is checked against the traversal forms explicitly rather than trusted
    to the character class.
    """
    cleaned = _UNSAFE.sub("", (name or "").strip()).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)[:60].strip()
    if not cleaned or set(cleaned) <= {"."}:
        return fallback
    return cleaned


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else None


@dataclass
class BundleStats:
    reports: int = 0
    steps: int = 0
    artifacts: int = 0
    projects: int = 0
    schedules: int = 0
    # A note is a child of its report, counted here in total across the bundle
    # the way steps and artifacts are, and written inside the report it explains.
    notes: int = 0
    # The three org-level kinds. ★``instructions`` and ``prompts`` count SHARED
    # and NON-PRIVATE rows only — the same rows the transfer moves, by the same
    # expression. If this number ever exceeds what the transfer reports, a
    # private row has reached the zip.
    instructions: int = 0
    prompts: int = 0
    data_sources: int = 0
    result_bytes: int = 0
    # Steps whose definition is present and whose rows were dropped for size.
    steps_without_results: list = field(default_factory=list)


async def build_bundle(
    db: AsyncSession,
    organization,
    user_id: str,
    *,
    include_results: bool = True,
    max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES,
) -> tuple[bytes, BundleStats]:
    """Everything ``user_id`` owns in this organization, as a zip.

    Selects on the OWNER's id and never touches the owner's account, so it works
    identically for somebody still here and somebody deprovisioned two years
    ago — the same property the admin transfer path depends on.
    """
    org_id = str(organization.id)
    uid = str(user_id)
    stats = BundleStats()

    owner = (
        await db.execute(select(User).where(User.id == uid))
    ).scalar_one_or_none()

    reports = list(
        (
            await db.execute(
                select(Report)
                .where(
                    Report.organization_id == org_id,
                    Report.user_id == uid,
                    Report.deleted_at.is_(None),
                    Report.status != "archived",
                )
                .order_by(Report.created_at)
            )
        )
        .scalars()
        .all()
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        used_dirs: set[str] = set()

        for index, report in enumerate(reports, start=1):
            # The index prefix is not decoration: two reports may legitimately
            # share a title, and without it the second silently overwrites the
            # first inside the zip.
            folder = f"reports/{index:03d}-{_safe(report.title, 'report')}"
            used_dirs.add(folder)
            stats.reports += 1

            bundle.writestr(
                f"{folder}/report.json",
                json.dumps(
                    {
                        "id": str(report.id),
                        "title": report.title,
                        "slug": report.slug,
                        "status": report.status,
                        "report_type": report.report_type,
                        "mode": report.mode,
                        "cron_schedule": report.cron_schedule,
                        # ★Recorded because a transfer changes it. A bundle that
                        # does not say the report ran as its owner cannot explain
                        # why it stopped working after a handover.
                        "shared_run_identity": report.shared_run_identity,
                        "artifact_visibility": report.artifact_visibility,
                        "conversation_visibility": report.conversation_visibility,
                        "created_at": _iso(report.created_at),
                        "updated_at": _iso(report.updated_at),
                        "last_run_at": _iso(report.last_run_at),
                    },
                    indent=2,
                    default=str,
                ),
            )

            widget_ids = list(
                (
                    await db.execute(
                        select(Widget.id).where(
                            Widget.report_id == str(report.id),
                            Widget.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )

            steps = []
            if widget_ids:
                steps = list(
                    (
                        await db.execute(
                            select(Step)
                            .where(
                                Step.widget_id.in_(widget_ids),
                                Step.deleted_at.is_(None),
                            )
                            .order_by(Step.created_at)
                        )
                    )
                    .scalars()
                    .all()
                )

            for s_index, step in enumerate(steps, start=1):
                stem = f"{folder}/steps/{s_index:03d}-{_safe(step.title, 'step')}"
                stats.steps += 1
                bundle.writestr(
                    f"{stem}.json",
                    json.dumps(
                        {
                            "id": str(step.id),
                            "title": step.title,
                            "type": step.type,
                            "status": step.status,
                            "prompt": step.prompt,
                            "description": step.description,
                            "data_model": step.data_model,
                            "view": step.view,
                        },
                        indent=2,
                        default=str,
                    ),
                )
                if step.code:
                    # As its own file, readable without a JSON viewer. This is
                    # the part somebody rebuilding the work actually reads.
                    bundle.writestr(f"{stem}.py", step.code)

                if not include_results or not step.data:
                    continue
                payload = json.dumps(step.data, indent=2, default=str)
                encoded = payload.encode("utf-8")
                if stats.result_bytes + len(encoded) > max_result_bytes:
                    stats.steps_without_results.append(str(step.id))
                    continue
                stats.result_bytes += len(encoded)
                bundle.writestr(f"{stem}.data.json", payload)

            artifacts = list(
                (
                    await db.execute(
                        select(Artifact)
                        .where(
                            Artifact.report_id == str(report.id),
                            Artifact.deleted_at.is_(None),
                        )
                        .order_by(Artifact.created_at)
                    )
                )
                .scalars()
                .all()
            )
            for a_index, artifact in enumerate(artifacts, start=1):
                stats.artifacts += 1
                bundle.writestr(
                    f"{folder}/artifacts/{a_index:03d}-{_safe(artifact.title, 'artifact')}.json",
                    json.dumps(
                        {
                            "id": str(artifact.id),
                            "title": artifact.title,
                            "mode": artifact.mode,
                            "version": artifact.version,
                            "status": artifact.status,
                            "content": artifact.content,
                            "generation_prompt": artifact.generation_prompt,
                            "created_at": _iso(artifact.created_at),
                        },
                        indent=2,
                        default=str,
                    ),
                )

            # ``notes.report_id`` is NOT NULL — a note cannot exist without its
            # report — so it belongs in the report's own folder beside the steps
            # and artifacts, not at the bundle root.
            #
            # ★Selected by report, NOT by ``user_id``, exactly like the steps and
            # artifacts above. A note is usually written by the AGENT rather than
            # by the person, so ``Note.user_id`` is the acting user of whichever
            # run produced it; filtering on it would drop most of the working
            # record of the reports being exported, silently.
            notes = list(
                (
                    await db.execute(
                        select(Note)
                        .where(
                            Note.report_id == str(report.id),
                            Note.deleted_at.is_(None),
                        )
                        .order_by(Note.created_at)
                    )
                )
                .scalars()
                .all()
            )
            for n_index, note in enumerate(notes, start=1):
                n_stem = f"{folder}/notes/{n_index:03d}-{_safe(note.title, 'note')}"
                stats.notes += 1
                bundle.writestr(
                    f"{n_stem}.json",
                    json.dumps(
                        {
                            "id": str(note.id),
                            "title": note.title,
                            # 'agent' or 'user'. Worth carrying: it is the
                            # difference between the person's own working note
                            # and the model's scratchpad.
                            "source": note.source,
                            "agent_execution_id": (
                                str(note.agent_execution_id)
                                if note.agent_execution_id
                                else None
                            ),
                            "created_at": _iso(note.created_at),
                            "updated_at": _iso(note.updated_at),
                        },
                        indent=2,
                        default=str,
                    ),
                )
                # The body is markdown and is the part a person actually reads,
                # so it is its own file for the same reason a step's code is.
                if note.content:
                    bundle.writestr(f"{n_stem}.md", note.content)

        projects = list(
            (
                await db.execute(
                    select(Project).where(
                        Project.organization_id == org_id,
                        Project.user_id == uid,
                        Project.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        stats.projects = len(projects)
        bundle.writestr(
            "projects.json",
            json.dumps(
                [
                    {
                        "id": str(p.id),
                        "name": getattr(p, "name", None),
                        "created_at": _iso(p.created_at),
                    }
                    for p in projects
                ],
                indent=2,
                default=str,
            ),
        )

        report_ids = [str(r.id) for r in reports]
        schedules = []
        if report_ids:
            schedules = list(
                (
                    await db.execute(
                        select(ScheduledPrompt).where(
                            ScheduledPrompt.report_id.in_(report_ids),
                            ScheduledPrompt.user_id == uid,
                            ScheduledPrompt.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
        stats.schedules = len(schedules)
        bundle.writestr(
            "schedules.json",
            json.dumps(
                [
                    {
                        "id": str(s.id),
                        "report_id": str(s.report_id),
                        "cron_schedule": s.cron_schedule,
                        "is_active": bool(s.is_active),
                    }
                    for s in schedules
                ],
                indent=2,
                default=str,
            ),
        )

        # ── org-level assets ──
        # These three hang off the ORGANIZATION rather than off a report, so
        # they are written at the bundle root. Same split, and the same reason,
        # as ``transfer_everything``'s ``include_assets``.

        instructions = list(
            (
                await db.execute(
                    select(Instruction)
                    .where(
                        Instruction.organization_id == org_id,
                        Instruction.user_id == uid,
                        Instruction.deleted_at.is_(None),
                        # ★The imported predicate, never a second spelling of
                        # it. A private instruction is a note-to-self; putting
                        # one in a zip an administrator can download is exactly
                        # the leak the transfer refuses to perform.
                        _shared_instructions_only(),
                    )
                    .order_by(Instruction.created_at)
                )
            )
            .scalars()
            .all()
        )
        stats.instructions = len(instructions)
        bundle.writestr(
            "instructions.json",
            json.dumps(
                [
                    {
                        "id": str(i.id),
                        "title": i.title,
                        "text": i.text,
                        "description": i.description,
                        "category": i.category,
                        "kind": i.kind,
                        "status": i.status,
                        "load_mode": i.load_mode,
                        "applicable_modes": i.applicable_modes,
                        "applicable_channels": i.applicable_channels,
                        "source_type": i.source_type,
                        "created_at": _iso(i.created_at),
                        "updated_at": _iso(i.updated_at),
                    }
                    # ★No ``data_sources``/``labels``: both relationships are
                    # ``lazy="raise"`` on this model, so touching either here
                    # turns an export into a 500 rather than a slow query.
                    for i in instructions
                ],
                indent=2,
                default=str,
            ),
        )

        prompts = list(
            (
                await db.execute(
                    select(Prompt)
                    .where(
                        Prompt.organization_id == org_id,
                        Prompt.user_id == uid,
                        Prompt.deleted_at.is_(None),
                        _non_private_prompts_only(),
                    )
                    .order_by(Prompt.created_at)
                )
            )
            .scalars()
            .all()
        )
        stats.prompts = len(prompts)
        bundle.writestr(
            "prompts.json",
            json.dumps(
                [
                    {
                        "id": str(p.id),
                        "title": p.title,
                        "text": p.text,
                        "scope": p.scope,
                        "mode": p.mode,
                        "is_starter": bool(p.is_starter),
                        "parameters": p.parameters,
                        "created_at": _iso(p.created_at),
                        "updated_at": _iso(p.updated_at),
                    }
                    # ★``mentions`` and ``model_id`` are deliberately out: both
                    # are lists of ids that mean nothing outside this install,
                    # and a bundle exists to be read somewhere else.
                    for p in prompts
                ],
                indent=2,
                default=str,
            ),
        )

        agents = list(
            (
                await db.execute(
                    select(DataSource)
                    .where(
                        DataSource.organization_id == org_id,
                        # ★``owner_user_id``. DataSource has no ``user_id``
                        # column at all, and asking for one raises.
                        DataSource.owner_user_id == uid,
                        DataSource.deleted_at.is_(None),
                    )
                    .order_by(DataSource.created_at)
                )
            )
            .scalars()
            .all()
        )
        stats.data_sources = len(agents)
        bundle.writestr(
            "agents.json",
            json.dumps(
                [
                    {
                        "id": str(a.id),
                        "name": a.name,
                        "description": a.description,
                        # Ownership-relevant only: who could see it and whether a
                        # human had published it. Enough to rebuild the decision,
                        # nothing that would let anybody reach the data.
                        "is_public": bool(a.is_public),
                        "publish_status": a.publish_status,
                        "is_active": bool(a.is_active),
                        "created_at": _iso(a.created_at),
                        "updated_at": _iso(a.updated_at),
                    }
                    # ★★★**Nothing connection-shaped, and no ``config``.** Type,
                    # config and credentials live on ``Connection``
                    # (``models/data_source.py`` :22) and are reachable from here
                    # through ``a.connections`` — one attribute away, in a file
                    # people email to each other. The agent's ``context`` and
                    # ``summary`` are out for a quieter reason: they are the
                    # trained description of somebody's warehouse, which is
                    # schema, not the person's own authored work.
                    for a in agents
                ],
                indent=2,
                default=str,
            ),
        )

        bundle.writestr(
            "manifest.json",
            json.dumps(
                {
                    "exported_at": datetime.utcnow().isoformat(),
                    "organization": {"id": org_id, "name": organization.name},
                    "owner": {
                        "id": uid,
                        "name": getattr(owner, "name", None),
                        "email": getattr(owner, "email", None),
                    },
                    "counts": {
                        "reports": stats.reports,
                        "steps": stats.steps,
                        "artifacts": stats.artifacts,
                        "notes": stats.notes,
                        "projects": stats.projects,
                        "schedules": stats.schedules,
                        "instructions": stats.instructions,
                        "prompts": stats.prompts,
                        "data_sources": stats.data_sources,
                    },
                    "results_included": include_results,
                    # ★Named, not counted. "12 steps lost their data" leaves the
                    # reader unable to tell WHICH twelve, so nobody can go and
                    # fetch them individually.
                    "steps_without_results": stats.steps_without_results,
                    "excluded": [
                        "data source connections, configuration and credentials",
                        "other people's reports, including ones shared with this person",
                        # ★Named here as well as in the README, because a script
                        # reading the manifest deserves the same honesty a person
                        # opening the README gets.
                        "uploaded files — they stay in the product, reachable through the report or agent that uses them",
                        "private instructions and private prompts, which stay with their author",
                    ],
                },
                indent=2,
                default=str,
            ),
        )

        bundle.writestr("README.txt", _readme(stats, include_results))

    return buffer.getvalue(), stats


def _readme(stats: BundleStats, include_results: bool) -> str:
    """★Says what the bundle is NOT.

    Somebody opens this months later, during exactly the incident it was made
    for. A bundle that looks complete and is not is worse than no bundle, so
    the two things it cannot do are stated before anything else.
    """
    truncated = (
        f"\n{len(stats.steps_without_results)} step(s) were too large to include "
        "their result rows. Their definitions are here; see "
        "steps_without_results in manifest.json for the list.\n"
        if stats.steps_without_results
        else ""
    )
    return (
        "CityAgent Insights — content export\n"
        "===================================\n\n"
        "A copy of the reports, dashboards, generated code, working notes,\n"
        "schedules, shared instructions, saved prompts and agents owned by one\n"
        "person, taken at the moment named in manifest.json.\n\n"
        "WHAT THIS IS NOT\n"
        "----------------\n"
        "* Not a restore. There is no import; rebuilding from this is manual.\n"
        "* Not a copy of your data sources. agents.json names the agents this\n"
        "  person owned and nothing more — no connection settings and no\n"
        "  credentials — so the queries here cannot be re-run until they are\n"
        "  pointed at a database again.\n"
        "* Not a copy of uploaded files. Spreadsheets, documents and images\n"
        "  attached to a report or an agent are NOT in this zip. They have not\n"
        "  gone anywhere: each one is still reachable in the product through the\n"
        "  report or the agent that uses it, and download it from there if you\n"
        "  need a copy. It is left out on purpose — a file in the product is\n"
        "  reached through checks that a zip cannot carry with it.\n"
        "* Not everything this person could SEE — only what they OWNED.\n"
        "  Reports shared with them belong to somebody else and stay there.\n"
        "* Not their private notes-to-self. Instructions marked private and\n"
        "  prompts scoped private stay with their author and are not here.\n\n"
        "LAYOUT\n"
        "------\n"
        "  manifest.json          who, when, and what was counted\n"
        "  reports/NNN-<title>/   one folder per report\n"
        "    report.json          settings, including whether it ran as its owner\n"
        "    steps/NNN-<title>.py    the generated code\n"
        "    steps/NNN-<title>.json  the step definition\n"
        + ("    steps/NNN-<title>.data.json  the last computed rows\n" if include_results else "")
        + "    artifacts/           dashboards and documents\n"
        "    notes/NNN-<title>.md    the working notes kept while answering\n"
        "    notes/NNN-<title>.json  who wrote each note, and when\n"
        "  projects.json          folders\n"
        "  schedules.json         what ran by itself\n"
        "  instructions.json      shared instructions this person wrote\n"
        "  prompts.json           saved prompts, private ones excluded\n"
        "  agents.json            agents they owned, by name — no connections\n"
        + truncated
    )
