"""Moving ownership of someone's work to someone else.

One service, called by every path that changes an owner: a member handing over
their own work, an admin transferring on someone's behalf, the automatic
successor when a person is removed, and an admin claiming an orphan. They differ
only in who may call them and what they may select — the mechanics are here so
there is exactly one place that knows what "ownership" is made of.

★★★**Ownership is not one column.** A transfer that stops at
``reports.user_id`` leaves half the graph pointing at a deactivated account.
The full set is reports, artifacts, queries, scheduled_prompts, notes,
projects, shared instructions, non-private prompts and agents — plus one thing
that is not an owner column at all:

★Artifacts, queries, scheduled_prompts and notes move with their report and
only with their report. Projects, instructions, prompts and agents hang off the
organization instead, so they move only in ``transfer_everything`` — projects
behind ``include_projects``, the other three behind ``include_assets``. That
split is not tidiness: a per-report handover that also moved the
organization's glossary would rewrite half the install because one dashboard
changed hands.

★★★**Files are deliberately NOT transferred, and that is a decision.**
``File.user_id`` is not authorship on this product — it is an access grant.
``app/core/file_access.py:68`` early-returns True on an owner match, *ahead* of
the full-admin check and the report-visibility predicate, and it gates three
routes in ``app/routes/file.py``: the content bytes (:215), the extracted text
(:309), and the **embed-token mint** (:365) — a bearer credential that outlives
the session that minted it. Moving that column would therefore hand the
recipient read and embed-mint rights over everything the departing person ever
uploaded, which is the one thing this service must never do.

Nor is anything stranded by leaving it. A file is reached through the report or
the data source that references it, and both of those transfer. There is no "my
files" list to go empty — ``file_service.get_files`` is org-scoped only — and
no delete gate keyed on ``user_id``. So the correct behaviour is to do nothing,
and the next reader must not "complete" this by adding files to the loop.

★★★``shared_run_identity == 'creator'`` means the report runs its queries **as
its owner**, using that person's rows in ``user_data_source_credentials`` /
``user_connection_credentials``. Move the owner and not this, and the dashboard
either keeps querying on a departed person's tokens — a real access problem —
or dies silently when they expire. Microsoft's Power BI guidance and Metabase's
open subscription bug are both this exact failure. It is the only part of a
transfer that is not a column update, and the reason ``previous_state`` exists.

★**A transfer moves responsibility, never visibility.** Nothing here publishes,
shares more widely, or changes ``artifact_visibility``. The one share row it may
write is a grant to the PREVIOUS owner (so handing over does not mean losing
the ability to answer questions about your own work), and the one it may delete
is the recipient's own now-redundant share.

★**Delete on this product is ``status='archived'``, not ``deleted_at``.** Every
count and every selection below filters on both, or an admin is told they are
stranding forty objects when twenty-five were thrown away months ago — and the
exaggeration is what makes people stop trusting the number.

★★★**A Report IS the conversation thread.** A dashboard or a deck is an
``Artifact`` hanging off it; the report itself is the chat. So "transfer 47
reports" reads like moving 47 dashboards and mostly is not: measured against the
live database, **224 of 262 live reports are chat-only and 38 carry a dashboard
or deck** — about 85% of what a bulk transfer moves is somebody's private
conversation with the agent.

★★★That is not merely uncomfortable, it **contradicts work this fork already
shipped**. Releases 0.0.531.9/.10 were the conversation-privacy pass, which
gated six report routes ``owner_only`` on exactly the reasoning that a chat is
private to the person who had it. Handing those same chats to a colleague the
moment somebody is deprovisioned cannot also be right; one of the two has to
give, and it is not the privacy pass.

So the **deliberate** path and the **automatic** paths are split, by
``include_conversations``:

- **Self-service** — a person picking items in My content, or handing over one
  report — is unchanged and always includes conversations. They chose; it is
  their conversation to give.
- **Successor** (nobody asked; a directory switched an account off) and **admin
  offboarding** move only work that still does a job, and leave pure
  conversations with the departing account.

A report is **working** if it has a live artifact, a live scheduled prompt, or a
live share — see ``working_report_ids``. Anything else is a conversation.

★A conversation left behind is **not lost and not stranded**. It stays owned by
the deactivated account, ``summarize`` still counts it, and ``orphaned_owners``
still lists that person — so an administrator who is actually asked for one can
move it deliberately through the per-report or self-service paths, which is the
only place that judgement belongs. The next reader must not "fix" this by
re-including them in the automatic paths.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.artifact import Artifact
from app.models.connection import Connection
from app.models.data_source import DataSource
from app.models.domain_connection import domain_connection
from app.models.instruction import Instruction
from app.models.membership import Membership
from app.models.note import Note
from app.models.ownership_transfer import OwnershipTransfer
from app.models.project import Project
from app.models.prompt import Prompt
from app.models.query import Query
from app.models.report import Report
from app.models.report_share import ReportShare
from app.models.scheduled_prompt import ScheduledPrompt
from app.models.user import User

# How long a batch stays undoable. Org-overridable via
# OrganizationSettings.config['transfer_undo_days'].
DEFAULT_UNDO_DAYS = 30

RESOURCE_REPORT = "report"
RESOURCE_ARTIFACT = "artifact"
RESOURCE_QUERY = "query"
RESOURCE_SCHEDULED_PROMPT = "scheduled_prompt"
RESOURCE_PROJECT = "project"
RESOURCE_NOTE = "note"
RESOURCE_INSTRUCTION = "instruction"
RESOURCE_PROMPT = "prompt"
RESOURCE_DATA_SOURCE = "data_source"

# What ``undo`` has to re-open to put a row back.
_RESOURCE_MODELS = {
    RESOURCE_REPORT: Report,
    RESOURCE_ARTIFACT: Artifact,
    RESOURCE_QUERY: Query,
    RESOURCE_SCHEDULED_PROMPT: ScheduledPrompt,
    RESOURCE_PROJECT: Project,
    RESOURCE_NOTE: Note,
    RESOURCE_INSTRUCTION: Instruction,
    RESOURCE_PROMPT: Prompt,
    RESOURCE_DATA_SOURCE: DataSource,
}

# ★★★**Which column carries the owner is not the same question for every kind.**
# Eight of the nine spell it ``user_id``; ``DataSource`` spells it
# ``owner_user_id`` and has no ``user_id`` at all. That asymmetry is invisible at
# the write site, because SQLAlchemy lets you assign an unmapped attribute to a
# mapped object without complaining — ``ds.user_id = x`` succeeds, persists
# nothing, and ``undo`` silently reports a row it did not restore. So the
# attribute name is looked up here, once, by resource type. A chain of ``if``s
# would work today and would be the thing a tenth resource type forgets to
# extend.
_OWNER_ATTR = {
    RESOURCE_REPORT: "user_id",
    RESOURCE_ARTIFACT: "user_id",
    RESOURCE_QUERY: "user_id",
    RESOURCE_SCHEDULED_PROMPT: "user_id",
    RESOURCE_PROJECT: "user_id",
    RESOURCE_NOTE: "user_id",
    RESOURCE_INSTRUCTION: "user_id",
    RESOURCE_PROMPT: "user_id",
    RESOURCE_DATA_SOURCE: "owner_user_id",
}

# ★★★**A private item is a note-to-self and stays with the person.** A published
# glossary entry is an organizational asset that merely happens to have an
# author, and leaving it behind means the org loses a rule it depends on the
# moment somebody goes on leave. So the two org-level kinds are split on their
# own explicit privacy flag rather than on anything inferred.
#
# ★These are functions, not constants, so ``summarize`` and the transfer share
# one expression by construction. The failure mode of writing the predicate
# twice is the worst kind here: the confirmation screen says "4 instructions"
# and the transfer moves 5, and the person who approved it has no way to know.


def _shared_instructions_only():
    """Instructions that belong to the organization, not to their author.

    ★★★**This filter is load-bearing, not tidiness.** ``(is_private, user_id)``
    is the visibility PAIR in six places — ``instruction_context_builder.py``
    :118-121 (what the AI is told), ``instruction_service.py`` :741-746 (list),
    :796-800 (pending), ``instruction_is_private_to_other()`` :112-134 (build
    reads, fails closed), ``routes/data_source.py`` :826-828 and
    ``data_source_service.py`` :1806-1807. Move a private instruction and it
    starts appearing in the RECIPIENT'S AI context: somebody else's private
    note, silently steering answers, with no screen anywhere that would show
    what happened.

    ★``.isnot(True)`` rather than ``.is_(False)``. The column is
    ``nullable=False`` today, but ``Instruction.is_private``'s own comment reads
    "False/NULL = SHARED" — so NULL is a state this product considers shared,
    and ``is_(False)`` would strand every such row with a departed account while
    reporting the count as zero.

    ★Stated rather than discovered: ``user_id`` ALSO gates draft visibility of
    non-private rows (``_get_own_instructions_condition``,
    ``instruction_service.py:4002``), so the recipient inherits the departing
    person's unpublished drafts along with their published rules. That is
    intended — an unpublished draft is exactly the work a handover exists to
    keep alive — but it is a visibility consequence of an ownership move, so it
    belongs in writing here.
    """
    return Instruction.is_private.isnot(True)


def _non_private_prompts_only():
    """Prompts that are not somebody's personal draft.

    'agent' and 'global' are both organizational; only 'private' is the
    note-to-self.

    ★★★``prompt_service._is_visible`` (:113-124) returns True on a ``user_id``
    match at ANY scope, so ``user_id`` is the whole of a private prompt's
    visibility. Moving one is doubly wrong in a single write: it appears for the
    recipient AND disappears for the person who wrote it, while they are still a
    member of the organization.

    ★A plain ``!=`` is safe because ``Prompt.scope`` is ``nullable=False`` with
    a default — the same reasoning as ``Report.status != 'archived'`` above, and
    it would not hold if either column were nullable, because ``NULL !=
    'private'`` is NULL and the row would silently drop out.
    """
    return Prompt.scope != "private"


# The two connector types whose per-user sign-in has no system credential to
# fall back to — ``user_data_source_credentials_service.py:203-207`` returns
# ``effective_auth="none"`` for them *before* the ownership check is reached.
# ★Named once, here, because the split they cause is the whole point of the
# departure-risk view below and re-spelling the tuple is how the warning and
# the behaviour drift apart. The same literal appears at six sites in that
# service; this is the copy the ownership feature reads.
PER_USER_SIGNIN_TYPES = ("fabric_user", "powerbi_user")


async def _credential_bound_agents_by_owner(
    db: AsyncSession, org_id: str, user_ids: Iterable[str]
) -> dict[str, dict[str, dict]]:
    """owner_user_id -> {data_source_id: {'name', 'types'}} for user_required agents.

    The one query behind both the single-owner id list below and the
    organization-wide departure view. ★It is batched because the org-wide caller
    asks for every member at once, and ``orphaned_owners`` already spends one
    ``summarize`` per member — a second per-member query on top of that is the
    shape that makes a settings page fine with twelve members and unopenable
    with four hundred.

    ★``types`` is a SET, not a single value. The relationship is M:N, so one
    agent can sit on several ``user_required`` connections of different types,
    and which one you happen to read first would otherwise decide whether the
    next owner is locked out or silently handed the system sign-in. Collecting
    them all lets the classification be a property of the agent rather than of
    the row order.
    """
    uids = [str(u) for u in user_ids]
    if not uids:
        return {}

    out: dict[str, dict[str, dict]] = {}
    for ds_id, ds_name, owner_id, conn_type in (
        await db.execute(
            select(
                DataSource.id,
                DataSource.name,
                DataSource.owner_user_id,
                Connection.type,
            )
            .join(
                domain_connection,
                domain_connection.c.data_source_id == DataSource.id,
            )
            .join(Connection, Connection.id == domain_connection.c.connection_id)
            .where(
                DataSource.organization_id == org_id,
                DataSource.owner_user_id.in_(uids),
                DataSource.deleted_at.is_(None),
                Connection.deleted_at.is_(None),
                Connection.auth_policy == "user_required",
            )
        )
    ).all():
        agents = out.setdefault(str(owner_id), {})
        agent = agents.setdefault(str(ds_id), {"name": ds_name, "types": set()})
        agent["types"].add(conn_type)
    return out


async def _credential_bound_data_source_ids(
    db: AsyncSession, org_id: str, uid: str
) -> list[str]:
    """Agents owned by ``uid`` whose connection makes ownership a CAPABILITY.

    ★★★``owner_user_id`` is in no visibility predicate — the real one is org
    plus ``is_public`` OR a ``data_source_memberships`` row
    (``data_source_service.py:1996-2002``) — so moving it grants nobody
    anything. It is not inert either. Four places read it as a capability:

    - ``user_data_source_credentials_service.py:208`` and ``:440`` — an owner
      holding no per-user credential row falls back to the SYSTEM credentials;
    - ``connection_service.py:1971`` — the same fallback at query time;
    - ``connection_identity.py:269`` ``is_admin_or_owner`` — the owner may
      switch which identity a query runs as;
    - ``data_source_service.py:3986`` ``_admin_catalog_access`` — the owner sees
      the canonical catalog rather than their own filtered overlay.

    On a ``user_required`` connection that splits two ways, and BOTH are bad as
    a silent side effect:

    - ``fabric_user`` / ``powerbi_user`` — ``user_data_source_credentials_
      service.py:203-207`` returns ``effective_auth="none"`` for those types
      *before* the ownership check, so the fallback is deliberately off and the
      new owner simply CANNOT run the agent until they sign in themselves. An
      agent that stops answering, with nothing saying why.
    - every other ``user_required`` type — the exact opposite: the new owner
      silently GAINS the use of system credentials they were never granted.

    Hence a warning count rather than a refusal: whoever is choosing a recipient
    has to be told, and only they can judge which of the two outcomes they want.

    ★★★``auth_policy`` lives on **Connection** (``models/connection.py:32``),
    never on DataSource — the join below is why. ``getattr(data_source,
    "auth_policy", "system_only")`` returns the DEFAULT on every row because the
    attribute does not exist there, so that comparison is decided when you TYPE
    it and not when it runs. Two sites in this fork shipped with exactly that
    bug, with opposite symptoms and no error from either.

    ★**Distinct by agent, not by row.** The relationship is M:N, so an agent on
    two ``user_required`` connections arrives twice and the warning would
    overstate itself. The keying by ``data_source_id`` in
    ``_credential_bound_agents_by_owner`` is what collapses it — this used to be
    a ``.distinct()`` on its own query, and the two must stay one expression:
    the count in the confirmation dialog and the named agents in the departure
    view are the same rows, and written twice they eventually disagree about how
    many there are.
    """
    return list((await _credential_bound_agents_by_owner(db, org_id, [uid])).get(str(uid), {}))


def _live_artifacts_of(report_ids: Iterable[str]):
    """The one expression for "this report has a dashboard or a deck".

    ★Defined once and used by BOTH readers — ``working_report_ids``, which
    decides what an automatic transfer moves, and ``artifact_modes_by_report``,
    which decides the label the My content list prints beside a row. Written
    twice, the two drift and the screen says "Chat" on a row the offboarding
    path treats as a dashboard: a person is told their conversation stays and it
    does not. That is the same failure ``_shared_instructions_only`` exists to
    prevent, one surface further out.

    ★``deleted_at`` only — deliberately no ``status`` filter, even though an
    artifact carries ``'pending' | 'completed' | 'failed'``. A deck whose
    generation failed is still a piece of work somebody was building and still
    has an export route pointed at it; treating it as a pure conversation would
    hand the failure to the departing account and quietly lose the attempt.
    """
    return (
        Artifact.report_id.in_(list(report_ids)),
        Artifact.deleted_at.is_(None),
    )


async def working_report_ids(db: AsyncSession, report_ids: Iterable[str]) -> set[str]:
    """Of these reports, the ones that still do a job for somebody.

    ★★★**A Report is the conversation; the dashboard is an Artifact on it.**
    See the module docstring for the measurement and for why the automatic paths
    must not move the rest. Working means ANY of:

    - a live **artifact** — there is a dashboard or a deck to keep running;
    - a live **scheduled prompt** — it fires on a cron and somebody expects the
      output to keep arriving;
    - a live **share** — somebody else has been given access, so at least one
      other person depends on it.

    ★``ScheduledPrompt.is_active`` is deliberately NOT part of the test. A
    paused schedule is still a standing intention to run this report, and the
    offboarding sequence itself is what switches them off
    (``organization_service._revoke_departed_member_access``) — so filtering on
    it would make the answer depend on how far through a removal we are, and a
    second run of the same removal would move less than the first.

    ★A share to a GROUP counts exactly like a share to a person: ``ReportShare``
    sets one of ``user_id`` / ``group_id`` and neither is what makes the row
    meaningful. The row's existence is the dependency.

    ★**One query, not three and not one per report.** The three sources are
    UNIONed in the database. The N+1 shape is the thing to avoid here above all
    — this runs inside ``summarize``, which ``orphaned_owners`` calls once per
    deactivated member.
    """
    ids = [str(r) for r in report_ids]
    if not ids:
        return set()

    artifacts = select(Artifact.report_id.label("report_id")).where(
        *_live_artifacts_of(ids)
    )
    schedules = select(ScheduledPrompt.report_id.label("report_id")).where(
        ScheduledPrompt.report_id.in_(ids),
        ScheduledPrompt.deleted_at.is_(None),
    )
    shares = select(ReportShare.report_id.label("report_id")).where(
        ReportShare.report_id.in_(ids),
        ReportShare.deleted_at.is_(None),
    )

    rows = (await db.execute(artifacts.union(schedules, shares))).scalars().all()
    return {str(r) for r in rows}


async def artifact_modes_by_report(
    db: AsyncSession, report_ids: Iterable[str]
) -> dict[str, set[str]]:
    """report_id -> the ``mode`` of each live artifact on it ('page' / 'slides').

    Batched over the whole list for the same reason as above: the caller is a
    list screen, and a query per row is how a page that was fine with twelve
    reports stops loading at four hundred.
    """
    ids = [str(r) for r in report_ids]
    if not ids:
        return {}

    out: dict[str, set[str]] = {}
    for report_id, mode in (
        await db.execute(
            select(Artifact.report_id, Artifact.mode).where(*_live_artifacts_of(ids))
        )
    ).all():
        out.setdefault(str(report_id), set()).add(mode or "page")
    return out


def report_type_label(modes: Optional[set[str]]) -> str:
    """What kind of thing a report actually is, in one word a person can read.

    ★This replaces sending ``report_type``, which is only ever ``'regular'`` or
    ``'test'`` — a value that answered a different question entirely while the
    schema's own comment promised 'Dashboard' / 'Slides' / 'Chat'. The comment
    was right about what the screen needs; the value was the nearest column to
    hand.

    ★'Slides' wins over 'Dashboard' when a report has both, because a deck is
    the more specific thing and the one a person went looking for.

    ★Derived from the SAME artifact query the transfer split uses, so the label
    and the behaviour cannot disagree: anything this calls 'Chat' is exactly
    what an automatic transfer will leave behind.
    """
    if not modes:
        return "Chat"
    if "slides" in modes:
        return "Slides"
    return "Dashboard"


# Every reason a row can carry. 'successor' is the only one that legitimately
# has no actor.
REASONS = (
    "self_handover",
    "admin_transfer",
    "offboarding",
    "successor",
    "claim",
    "undo",
)


class TransferRefused(Exception):
    """A transfer that must not happen. Carries a sentence a user can read."""

    def __init__(self, message: str, *, code: str = "refused"):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class ContentSummary:
    """What one person owns in one organization, by kind."""

    reports: int = 0
    artifacts: int = 0
    queries: int = 0
    scheduled_prompts: int = 0
    projects: int = 0
    notes: int = 0
    # The two org-level kinds, counted SHARED-only — see
    # ``_shared_instructions_only`` / ``_non_private_prompts_only``. A person
    # reading this number is about to decide whether a handover is safe, so it
    # must count exactly the rows the transfer will move and nothing else.
    instructions: int = 0
    prompts: int = 0
    # Agents whose owner_user_id is this person. Owner only: access to an agent
    # is data_source_memberships and is_public, neither of which a transfer
    # touches.
    data_sources: int = 0
    # Reports whose queries run as their owner. Surfaced separately because it
    # is the one consequence a person choosing a recipient needs to be told
    # about before they confirm.
    creator_identity_reports: int = 0
    # Of those data_sources, the ones on a `user_required` connection — where
    # ownership is read as a CAPABILITY and the move therefore either revokes
    # the agent (fabric_user / powerbi_user) or silently grants system
    # credentials (every other type). See
    # ``_credential_bound_data_source_ids``. Same shape and same reason as
    # creator_identity_reports: a warning, not a kind of thing.
    credential_bound_data_sources: int = 0
    # Of ``reports``, the ones that are pure conversation — no dashboard, no
    # deck, no schedule, nobody else sharing them. The automatic paths leave
    # these behind (module docstring), so this is the number that says how much
    # of a bulk transfer is somebody's chat history rather than their work.
    # ★A sub-count of ``reports``, exactly like the two above, and therefore out
    # of ``total`` for the same reason.
    conversation_reports: int = 0

    @property
    def total(self) -> int:
        # ★``creator_identity_reports``, ``credential_bound_data_sources`` and
        # ``conversation_reports`` are deliberately absent. Each is a sub-count
        # OF a kind already counted — reports, data_sources and reports again —
        # not a kind of its own, and adding any of them would double-count those
        # rows in the one number an admin uses to judge how much is stranded.
        return (
            self.reports
            + self.artifacts
            + self.queries
            + self.scheduled_prompts
            + self.projects
            + self.notes
            + self.instructions
            + self.prompts
            + self.data_sources
        )

    def as_dict(self) -> dict:
        return {
            "reports": self.reports,
            "artifacts": self.artifacts,
            "queries": self.queries,
            "scheduled_prompts": self.scheduled_prompts,
            "projects": self.projects,
            "notes": self.notes,
            "instructions": self.instructions,
            "prompts": self.prompts,
            "data_sources": self.data_sources,
            "creator_identity_reports": self.creator_identity_reports,
            "credential_bound_data_sources": self.credential_bound_data_sources,
            "conversation_reports": self.conversation_reports,
            "total": self.total,
        }


@dataclass
class TransferResult:
    batch_id: str
    moved: dict = field(default_factory=dict)
    creator_identity_repointed: int = 0
    # What actually moved, of the warning above — so the confirmation dialog
    # can say which agents now need the recipient to sign in (or now reach the
    # system credentials). ★Outside ``moved``, therefore outside ``total``, for
    # the same reason it is outside ContentSummary.total: these rows are already
    # counted under 'data_source'.
    credential_bound_data_sources: int = 0
    # Reports the automatic paths deliberately did NOT move because they are
    # pure conversation. ★Reported rather than silent: a batch that says "moved
    # 12" when the person owned 47 looks like a bug or a half-finished job, and
    # an administrator who cannot see the difference between "left on purpose"
    # and "failed" will go looking for the other 35. ★Outside ``moved``, and so
    # outside ``total``, because nothing moved — ``total`` counts writes.
    conversations_left_behind: int = 0

    @property
    def total(self) -> int:
        return sum(self.moved.values())


# ──────────────────────────── validation ──────────────────────────────────


async def assert_can_receive(db: AsyncSession, organization, user_id: str) -> User:
    """The recipient must be someone who can actually act in this organization.

    ★★★The obvious version of this check — "reject a deactivated account" —
    would refuse every **service account**, whose backing users row is
    ``is_active=False`` BY DESIGN so it can never log in interactively while its
    API keys keep working. A service account is the destination the whole
    governance literature recommends for business-critical dashboards: content
    owned by a team, not a person. So the rule is *active OR service account*,
    and org binding is resolved through ``principal_belongs_to_org``, which
    already knows that service accounts bind through a ServiceAccount row and
    have no Membership at all.
    """
    from app.core.permission_resolver import principal_belongs_to_org

    user = (await db.execute(select(User).where(User.id == str(user_id)))).scalar_one_or_none()
    if user is None:
        raise TransferRefused("That person no longer exists.", code="unknown_recipient")

    if not getattr(user, "is_service_account", False) and not user.is_active:
        raise TransferRefused(
            "That account has been deactivated, so it cannot take over anyone's work.",
            code="inactive_recipient",
        )

    if not await principal_belongs_to_org(db, user, str(organization.id)):
        raise TransferRefused(
            "That person is not a member of this organization.",
            code="not_a_member",
        )

    return user


# ──────────────────────────── summarize ───────────────────────────────────


async def summarize(db: AsyncSession, organization, user_id: str) -> ContentSummary:
    """Everything ``user_id`` owns in this organization that still exists.

    ★Both filters, every time — see the module docstring. An archived report is
    gone as far as anyone using the product is concerned.
    """
    org_id = str(organization.id)
    uid = str(user_id)
    summary = ContentSummary()

    live_reports = (
        Report.organization_id == org_id,
        Report.user_id == uid,
        Report.deleted_at.is_(None),
        Report.status != "archived",
    )

    report_ids = list(
        (await db.execute(select(Report.id).where(*live_reports))).scalars().all()
    )
    summary.reports = len(report_ids)

    summary.creator_identity_reports = len(
        list(
            (
                await db.execute(
                    select(Report.id).where(
                        *live_reports, Report.shared_run_identity == "creator"
                    )
                )
            )
            .scalars()
            .all()
        )
    )

    # ★Chat-only reports, counted from the SAME predicate the automatic
    # transfer paths select on, so the screen and the batch cannot disagree
    # about which rows are conversations. One UNIONed query — see
    # ``working_report_ids``, which matters here because ``orphaned_owners``
    # calls ``summarize`` once per deactivated member.
    summary.conversation_reports = len(report_ids) - len(
        await working_report_ids(db, report_ids)
    )

    if report_ids:
        summary.artifacts = len(
            list(
                (
                    await db.execute(
                        select(Artifact.id).where(
                            Artifact.user_id == uid,
                            Artifact.report_id.in_(report_ids),
                            Artifact.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
        )
        summary.queries = len(
            list(
                (
                    await db.execute(
                        select(Query.id).where(
                            Query.user_id == uid,
                            Query.report_id.in_(report_ids),
                            Query.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
        )
        summary.scheduled_prompts = len(
            list(
                (
                    await db.execute(
                        select(ScheduledPrompt.id).where(
                            ScheduledPrompt.user_id == uid,
                            ScheduledPrompt.report_id.in_(report_ids),
                            ScheduledPrompt.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
        )
        # ``notes.report_id`` is NOT NULL, so a note is a child of a report in
        # exactly the sense an artifact or a query is — counted off the same
        # report ids, with no org filter of its own because the report already
        # carries one.
        summary.notes = len(
            list(
                (
                    await db.execute(
                        select(Note.id).where(
                            Note.user_id == uid,
                            Note.report_id.in_(report_ids),
                            Note.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
        )

    summary.projects = len(
        list(
            (
                await db.execute(
                    select(Project.id).where(
                        Project.organization_id == org_id,
                        Project.user_id == uid,
                        Project.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    )

    # ── org-level assets ──
    # These three hang off the organization, not off a report, so they are
    # counted straight from the org. ★An instruction is soft-deleted with
    # ``deleted_at`` and keeps its row — there is no ``status='archived'`` step
    # for it the way there is for a report, so ``deleted_at`` alone is the whole
    # liveness test here and adding a status filter would hide published rules.
    summary.instructions = len(
        list(
            (
                await db.execute(
                    select(Instruction.id).where(
                        Instruction.organization_id == org_id,
                        Instruction.user_id == uid,
                        Instruction.deleted_at.is_(None),
                        _shared_instructions_only(),
                    )
                )
            )
            .scalars()
            .all()
        )
    )

    summary.prompts = len(
        list(
            (
                await db.execute(
                    select(Prompt.id).where(
                        Prompt.organization_id == org_id,
                        Prompt.user_id == uid,
                        Prompt.deleted_at.is_(None),
                        _non_private_prompts_only(),
                    )
                )
            )
            .scalars()
            .all()
        )
    )

    # ★``owner_user_id``, not ``user_id`` — DataSource has no ``user_id``
    # column, and asking for one raises rather than returning nothing.
    summary.data_sources = len(
        list(
            (
                await db.execute(
                    select(DataSource.id).where(
                        DataSource.organization_id == org_id,
                        DataSource.owner_user_id == uid,
                        DataSource.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    )

    summary.credential_bound_data_sources = len(
        await _credential_bound_data_source_ids(db, org_id, uid)
    )

    return summary


async def orphaned_owners(db: AsyncSession, organization) -> list[dict]:
    """People who can no longer sign in and still own something here.

    ★★★**This is the view that makes the whole feature honest.** Every other
    surface needs somebody to already know whose work is stranded. Nothing in
    the product lists it: ``filter == "my"`` is ``Report.user_id ==
    current_user.id``, so an orphaned report appears in *nobody's* list — not
    the departed person's, because they cannot sign in, and not an admin's,
    because they do not own it. It is invisible rather than missing, which is
    why organizations discover it when a scheduled report stops arriving.

    ★A **service account** is excluded. Its backing row is ``is_active=False``
    by design and it is the *recommended destination* for exactly this content
    — listing it as an orphan would tell people to undo the correct thing.

    ★Membership rows only. Someone who was fully removed has no Membership, so
    their work does not appear here; that is what the removal dialog's transfer
    step is for, and adding them would mean listing accounts that no longer
    belong to this organization at all.
    """
    org_id = str(organization.id)

    rows = (
        await db.execute(
            select(User, Membership)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.organization_id == org_id,
                Membership.deleted_at.is_(None),
                User.is_active.is_(False),
            )
        )
    ).all()

    out: list[dict] = []
    for user, membership in rows:
        if getattr(user, "is_service_account", False):
            continue
        summary = await summarize(db, organization, str(user.id))
        if summary.total == 0:
            continue

        successor_name = None
        if membership.successor_user_id:
            successor = (
                await db.execute(
                    select(User).where(User.id == str(membership.successor_user_id))
                )
            ).scalar_one_or_none()
            if successor is not None:
                successor_name = successor.name or successor.email

        out.append(
            {
                "user_id": str(user.id),
                "membership_id": str(membership.id),
                "name": user.name or user.email,
                "email": user.email,
                # Present but never acted on: a successor recorded here means
                # the automatic handover was attempted and did NOT clear the
                # content — normally because that person has since left too.
                # Showing the name is what tells an admin to pick someone else
                # rather than wonder why the rule did not fire.
                "successor_name": successor_name,
                "summary": summary.as_dict(),
            }
        )

    out.sort(key=lambda r: r["summary"]["total"], reverse=True)
    return out


# ──────────────────────── before anybody leaves ───────────────────────────
#
# ★★★**Everything above warns you on the day it is already too late.**
# ``orphaned_owners`` is a post-mortem: it can only speak once somebody's
# account is off, and by then the dashboards have stopped and the agents have
# stopped and nobody has the sign-in that made them work. The transfer paths are
# a remedy, and a remedy needs somebody to already know what broke.
#
# What follows answers the question an administrator can still act on: **if this
# person left tomorrow, what would break?** It writes nothing, adds no table and
# schedules no job — every fact it reports is already recorded. It is the same
# counting, asked one day earlier.


async def _creator_identity_scheduled_by_owner(
    db: AsyncSession, org_id: str, user_ids: Iterable[str]
) -> dict[str, dict[str, dict]]:
    """owner -> {report_id: {'title', 'active'}} for scheduled creator-identity reports.

    The reports that run on a cron **as their owner** — ``shared_run_identity ==
    'creator'`` means the queries execute against that person's rows in
    ``user_data_source_credentials`` (module docstring). Deactivate the account
    and the schedule keeps firing into a sign-in that no longer works: no error
    reaches the person who relied on the output, because they were never the one
    running it. ★This is the failure organizations discover months late, which
    is exactly why it has to be nameable before the departure rather than
    countable after it.

    ★A report is listed once however many schedules it carries, and ``active``
    is True if ANY of them is live. Two schedules on one dashboard is one thing
    to arrange, not two, and the count is what an admin triages by.

    ★``is_active`` is carried rather than filtered, and that is a deliberate
    difference from ``working_report_ids``, which ignores the flag entirely.
    There the reason is that the offboarding sequence itself switches schedules
    off, so filtering would make the answer depend on how far through a removal
    we are. Here nobody has been removed yet, so the flag means what it says —
    and telling somebody a paused dashboard "would stop arriving" is the kind of
    overstatement the module docstring warns makes people stop trusting the
    number. So both are returned, and only the live ones are counted as risk.

    Batched over every owner in one query, for the same reason as
    ``_credential_bound_agents_by_owner``.
    """
    uids = [str(u) for u in user_ids]
    if not uids:
        return {}

    out: dict[str, dict[str, dict]] = {}
    for owner_id, report_id, title, is_active in (
        await db.execute(
            select(
                Report.user_id,
                Report.id,
                Report.title,
                ScheduledPrompt.is_active,
            )
            .join(ScheduledPrompt, ScheduledPrompt.report_id == Report.id)
            .where(
                Report.organization_id == org_id,
                Report.user_id.in_(uids),
                Report.deleted_at.is_(None),
                Report.status != "archived",
                Report.shared_run_identity == "creator",
                ScheduledPrompt.deleted_at.is_(None),
            )
        )
    ).all():
        reports = out.setdefault(str(owner_id), {})
        report = reports.setdefault(str(report_id), {"title": title, "active": False})
        report["active"] = report["active"] or bool(is_active)
    return out


async def _successors_by_user(
    db: AsyncSession, org_id: str, user_ids: Iterable[str]
) -> dict[str, dict]:
    """user_id -> {'membership_id', 'successor_user_id', 'successor_name'}.

    Two queries for the whole organization rather than the two-per-member
    ``orphaned_owners`` spends: the memberships, then one lookup for every
    distinct successor named across all of them. ★A successor who has since been
    cleaned up resolves to a name of ``None`` rather than dropping the id — the
    id is still what was nominated, and reporting the nomination as absent would
    hide the case ``orphaned_owners`` exists to surface, where the rule fired at
    somebody who had also left.
    """
    uids = [str(u) for u in user_ids]
    if not uids:
        return {}

    memberships = (
        (
            await db.execute(
                select(Membership).where(
                    Membership.organization_id == org_id,
                    Membership.user_id.in_(uids),
                    Membership.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    successor_ids = {
        str(m.successor_user_id) for m in memberships if m.successor_user_id
    }
    names: dict[str, str] = {}
    if successor_ids:
        for user in (
            (
                await db.execute(
                    select(User).where(User.id.in_(sorted(successor_ids)))
                )
            )
            .scalars()
            .all()
        ):
            names[str(user.id)] = user.name or user.email

    return {
        str(m.user_id): {
            "membership_id": str(m.id),
            "successor_user_id": str(m.successor_user_id) if m.successor_user_id else None,
            "successor_name": (
                names.get(str(m.successor_user_id)) if m.successor_user_id else None
            ),
        }
        for m in memberships
    }


def _classify_agents(agents: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Split credential-bound agents into (locked out, silently shared).

    ★★★**One number here would hide the thing that matters.** Both halves are
    consequences of the same ``user_required`` connection and they are opposites:

    - ``fabric_user`` / ``powerbi_user`` — the next owner is **locked out**.
      ``user_data_source_credentials_service.py:203-207`` returns
      ``effective_auth="none"`` for these two types *before* the ownership check
      is reached, so there is no system credential to fall back to and the agent
      simply stops answering until that person signs in themselves.
    - every other ``user_required`` type — the next owner **silently gains** the
      organization's shared sign-in, which nobody granted them.

    One is a broken agent and the other is an access change; "4 agents at risk"
    tells an administrator neither, and the two need different arrangements
    made. ★An agent carrying both kinds of connection is reported as locked out,
    because the part that cannot run decides what a person has to do about it —
    the shared sign-in on its other connection does not make it work again.

    Names, not counts: "Finance Warehouse, Sales Cube" is something somebody can
    go and arrange. "2 agents" is something they have to go and find first.
    """
    locked_out: list[dict] = []
    shares_system_credentials: list[dict] = []
    for ds_id, agent in agents.items():
        row = {
            "id": ds_id,
            "name": agent["name"],
            "connection_types": sorted(t for t in agent["types"] if t),
        }
        if any(t in PER_USER_SIGNIN_TYPES for t in agent["types"]):
            locked_out.append(row)
        else:
            shares_system_credentials.append(row)

    locked_out.sort(key=lambda r: (r["name"] or "").lower())
    shares_system_credentials.sort(key=lambda r: (r["name"] or "").lower())
    return locked_out, shares_system_credentials


def _risk_row(
    user: User,
    successor: Optional[dict],
    agents: dict[str, dict],
    reports: dict[str, dict],
) -> dict:
    """Assemble one person's answer from facts already fetched.

    ★Pure, and takes no session on purpose: it is the shared tail of the
    single-person and whole-organization views, so the two cannot come to
    different conclusions from the same rows. Every query this needs happens in
    the batched helpers above, which is what keeps the org-wide view off an N+1.
    """
    locked_out, shares_system_credentials = _classify_agents(agents)

    scheduled = sorted(
        (
            {"id": rid, "title": r["title"], "active": r["active"]}
            for rid, r in reports.items()
        ),
        key=lambda r: (not r["active"], (r["title"] or "").lower()),
    )

    successor = successor or {}
    return {
        "user_id": str(user.id),
        "membership_id": successor.get("membership_id"),
        "name": user.name or user.email,
        "email": user.email,
        # Carried rather than filtered on. Somebody already deactivated is not a
        # future risk — they are ``orphaned_owners``' subject — but dropping them
        # from a view titled "what would break" would leave an administrator
        # believing an install is clear when the breakage has already happened.
        "is_active": bool(user.is_active),
        # ★A person with a successor is already covered and must not sit beside
        # an uncovered one at the same weight — see the ordering in
        # ``departure_risk_for_organization``. The nomination is the arrangement
        # this whole view exists to prompt, so showing it is showing the answer.
        "successor_user_id": successor.get("successor_user_id"),
        "successor_name": successor.get("successor_name"),
        "has_successor": bool(successor.get("successor_user_id")),
        "locked_out_agents": locked_out,
        "shares_system_credentials_agents": shares_system_credentials,
        "scheduled_reports": scheduled,
        # ★The one number, and it counts only things that would actually stop:
        # both agent kinds (each is a real consequence somebody must arrange for)
        # plus the LIVE schedules. A paused schedule is listed above and left out
        # here — it is not arriving today, so it cannot stop arriving tomorrow.
        "at_risk": (
            len(locked_out)
            + len(shares_system_credentials)
            + sum(1 for r in scheduled if r["active"])
        ),
    }


async def departure_risk(db: AsyncSession, organization, user_id: str) -> dict:
    """If this person left tomorrow, what would break?

    Three answers, all assembled from rows that already exist:

    1. **Agents that would stop working** — the ``user_required`` ones from
       ``_credential_bound_data_source_ids``, split by consequence: locked out
       versus silently handed the organization's sign-in. See
       ``_classify_agents``; the split is the point.
    2. **Scheduled dashboards that would stop arriving** — reports on a cron
       that run as their creator. See ``_creator_identity_scheduled_by_owner``.
    3. **Whether a successor is already named**, because that is the
       arrangement this view exists to prompt and somebody who has made it is
       not at the same risk as somebody who has not.

    ★Reads nothing that a transfer would write. This is the warning, not the
    remedy — the remedy is ``transfer_everything`` and the member's own
    ``/me/successor``, and keeping them apart is what lets an administrator look
    at the whole install without being one mis-click from moving somebody's work.

    ★Delegates to the same batched helpers as the org-wide view with a
    one-element list rather than growing single-row variants. Two spellings of
    "which agents are credential-bound" is precisely the drift
    ``_credential_bound_agents_by_owner`` was made to prevent.
    """
    org_id = str(organization.id)
    uid = str(user_id)

    user = (
        await db.execute(select(User).where(User.id == uid))
    ).scalar_one_or_none()
    if user is None:
        raise TransferRefused("That person no longer exists.", code="unknown_user")

    return _risk_row(
        user,
        (await _successors_by_user(db, org_id, [uid])).get(uid),
        (await _credential_bound_agents_by_owner(db, org_id, [uid])).get(uid, {}),
        (await _creator_identity_scheduled_by_owner(db, org_id, [uid])).get(uid, {}),
    )


async def departure_risk_for_organization(db: AsyncSession, organization) -> list[dict]:
    """The same answer for everybody here at once, worst first.

    ★★★**Four queries for the whole organization, not four per member.** The
    per-member shape is what this had to avoid: ``orphaned_owners`` already
    spends a ``summarize`` — itself a dozen queries — on each deactivated
    person, and a settings page that adds another handful per row is fine on the
    install it was written against and unopenable on a real one. So the members
    are read once, and each of the three facts is one batched query keyed by
    owner, exactly the way ``working_report_ids`` unions across reports instead
    of asking per report.

    ★**Service accounts are excluded**, the same rule as ``orphaned_owners`` and
    for a stronger reason here: a service account cannot leave, and it is the
    *recommended* destination for content that should not depend on a person.
    Listing one as a departure risk would advise undoing the correct thing.

    ★**People with nothing at risk are omitted.** An administrator opening this
    is looking for what to arrange; a row reading "nothing would break" is not
    an action, and a list where most rows say it is one nobody finishes reading.
    A missing person means nothing of theirs would stop, which the caller may
    say out loud — it is not an incomplete answer.

    ★Ordering puts **uncovered people first**, then by how much would break.
    Somebody with a named successor still appears, because a successor is a
    person who may also leave and the nomination is worth seeing, but they sit
    below everybody who has made no arrangement at all — which is the whole
    difference between a warning and a list.
    """
    org_id = str(organization.id)

    rows = (
        await db.execute(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.organization_id == org_id,
                Membership.deleted_at.is_(None),
                Membership.user_id.isnot(None),
            )
        )
    ).all()

    # A pending invite has a Membership and no user, and the join drops it —
    # correct, since it owns nothing by construction.
    #
    # ★Deduplicated by user id. Nothing forbids a second live Membership row for
    # the same person in the same organization, and the join would then report
    # them twice with identical content — a duplicate that reads as double the
    # risk rather than as a data oddity.
    seen: set[str] = set()
    users: list[User] = []
    for (user,) in rows:
        if getattr(user, "is_service_account", False):
            continue
        if str(user.id) in seen:
            continue
        seen.add(str(user.id))
        users.append(user)
    if not users:
        return []

    uids = [str(u.id) for u in users]
    successors = await _successors_by_user(db, org_id, uids)
    agents = await _credential_bound_agents_by_owner(db, org_id, uids)
    scheduled = await _creator_identity_scheduled_by_owner(db, org_id, uids)

    out = [
        row
        for row in (
            _risk_row(
                user,
                successors.get(str(user.id)),
                agents.get(str(user.id), {}),
                scheduled.get(str(user.id), {}),
            )
            for user in users
        )
        if row["at_risk"] > 0
    ]

    out.sort(
        key=lambda r: (
            r["has_successor"],
            -r["at_risk"],
            -len(r["locked_out_agents"]),
            (r["name"] or "").lower(),
        )
    )
    return out


async def on_member_deactivated(
    db: AsyncSession,
    organization,
    user_id: str,
    *,
    actor_user_id: Optional[str] = None,
) -> Optional[TransferResult]:
    """The successor fires. Called when an account is switched off.

    ★★★**This must never be able to fail a deprovisioning.** The caller is a
    directory telling us somebody has left the company; switching the account
    off is the security-critical act and the transfer is a courtesy on top of
    it. So every refusal is swallowed and the content simply stays orphaned —
    where ``orphaned_owners`` will list it for an administrator. The inverse
    ordering is genuinely correct one layer up, in the removal dialog: there a
    human explicitly asked for the transfer, so refusing it must abort the whole
    request rather than silently strand the content they asked to rescue.

    ★Returns ``None`` when nothing happened — no successor, or a successor who
    can no longer receive. A caller must not read that as failure.

    ★This path moves **working reports only** — see ``include_conversations``
    below and in the module docstring. A result whose ``total`` is far smaller
    than the person's content summary is therefore expected, not a partial run;
    ``conversations_left_behind`` says how much was left on purpose.
    """
    membership = (
        await db.execute(
            select(Membership).where(
                Membership.user_id == str(user_id),
                Membership.organization_id == str(organization.id),
                Membership.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    if membership is None or not membership.successor_user_id:
        return None

    try:
        result = await transfer_everything(
            db,
            organization,
            from_user_id=str(user_id),
            to_user_id=str(membership.successor_user_id),
            actor_user_id=actor_user_id,
            reason="successor",
            include_projects=True,
            # ★★★**Nobody asked for this one.** A directory said somebody left;
            # no human chose a recipient, chose the items, or saw a confirmation
            # screen. That is precisely the case where handing over a private
            # chat thread cannot be justified — so the successor takes the work
            # that still does a job and the conversations stay with the account.
            # They remain visible to an administrator through
            # ``orphaned_owners`` and can be moved deliberately if anyone asks.
            include_conversations=False,
            # A person who has just been deprovisioned must not be left holding
            # a share on the work that moved. Same reasoning as the offboarding
            # path, and the opposite of a voluntary handover.
            keep_access_for_previous_owner=False,
        )
    except TransferRefused:
        return None

    return result


# ──────────────────────────── transfer ────────────────────────────────────


async def transfer_reports(
    db: AsyncSession,
    organization,
    report_ids: Iterable[str],
    *,
    to_user_id: str,
    actor_user_id: Optional[str],
    reason: str,
    keep_access_for_previous_owner: bool = True,
) -> TransferResult:
    """Move a specific set of reports and everything hanging off them.

    Returns without touching anything if ``report_ids`` is empty — a no-op must
    not write a ledger row, or "nothing happened" becomes indistinguishable
    from "something happened" in the history.
    """
    if reason not in REASONS:
        raise TransferRefused(f"Unknown transfer reason: {reason}", code="bad_reason")

    org_id = str(organization.id)
    to_uid = str(to_user_id)
    ids = [str(r) for r in report_ids]
    result = TransferResult(batch_id=str(uuid.uuid4()))
    if not ids:
        return result

    await assert_can_receive(db, organization, to_uid)

    rows = (
        (
            await db.execute(
                select(Report).where(
                    Report.id.in_(ids),
                    Report.organization_id == org_id,
                    Report.deleted_at.is_(None),
                    Report.status != "archived",
                )
            )
        )
        .scalars()
        .all()
    )

    moved_report_ids: list[str] = []
    for report in rows:
        from_uid = str(report.user_id)
        if from_uid == to_uid:
            # Already theirs. Skipped rather than refused: a bulk handover of 23
            # items should not fail because one of them was already moved.
            continue

        previous_state: dict[str, Any] = {
            "shared_run_identity": report.shared_run_identity,
        }

        # ★The half that is not a column rename. See the module docstring.
        if report.shared_run_identity == "creator":
            result.creator_identity_repointed += 1

        report.user_id = to_uid

        await _clear_redundant_share(db, str(report.id), to_uid)

        if keep_access_for_previous_owner:
            granted = await _grant_share(db, str(report.id), from_uid)
            previous_state["granted_share_to_previous_owner"] = granted

        db.add(
            OwnershipTransfer(
                organization_id=org_id,
                resource_type=RESOURCE_REPORT,
                resource_id=str(report.id),
                from_user_id=from_uid,
                to_user_id=to_uid,
                actor_user_id=str(actor_user_id) if actor_user_id else None,
                reason=reason,
                batch_id=result.batch_id,
                previous_state=json.dumps(previous_state),
            )
        )
        moved_report_ids.append(str(report.id))

    result.moved[RESOURCE_REPORT] = len(moved_report_ids)

    if moved_report_ids:
        for model, kind in (
            (Artifact, RESOURCE_ARTIFACT),
            (Query, RESOURCE_QUERY),
            (ScheduledPrompt, RESOURCE_SCHEDULED_PROMPT),
            # A note is the agent's scratchpad for one report and cannot exist
            # without it (``report_id`` is NOT NULL). It needs no path of its
            # own — it was simply never listed here, so every plan, finding and
            # ruled-out hypothesis stayed attributed to the departed account
            # while the report it explains moved.
            (Note, RESOURCE_NOTE),
        ):
            result.moved[kind] = await _move_children(
                db,
                model,
                kind,
                moved_report_ids,
                org_id=org_id,
                to_uid=to_uid,
                actor_user_id=actor_user_id,
                reason=reason,
                batch_id=result.batch_id,
            )

    await db.flush()
    return result


async def transfer_everything(
    db: AsyncSession,
    organization,
    *,
    from_user_id: str,
    to_user_id: str,
    actor_user_id: Optional[str],
    reason: str,
    include_projects: bool = True,
    include_assets: bool = True,
    include_conversations: bool = True,
    keep_access_for_previous_owner: bool = False,
) -> TransferResult:
    """Everything one person owns in this organization, in one batch.

    ``keep_access_for_previous_owner`` defaults to **False** here and True on
    the per-report path, and the difference is deliberate: handing over a
    project you still work on is not the same act as offboarding someone who
    has left.

    ``include_conversations`` separates the deliberate act from the automatic
    ones. ★★★It defaults to **True**, which is not a preference — it is what
    keeps every existing caller behaving as it did, and in particular it is
    CORRECT for the self-service path: somebody choosing to hand their work to a
    colleague is choosing to hand over their conversations too, and silently
    withholding them would be the service second-guessing an explicit decision.
    The successor and admin-offboarding paths pass **False**, because nobody
    asked them for anything — see the module docstring for the measurement (85%
    of reports are chat-only) and for why this cannot be reconciled with the
    conversation-privacy work any other way.

    ``include_assets`` covers the three ORG-LEVEL kinds — shared instructions,
    non-private prompts and agents. They are separable from the report graph
    because they answer a different question: an admin cleaning up after a
    departure wants them, whereas somebody handing one project to a colleague
    may well not. ★They are reachable only from here, never from
    ``transfer_reports``: they have no ``report_id``, so a per-report handover
    that dragged them along would move the whole organizational glossary
    because one dashboard changed hands.
    """
    org_id = str(organization.id)
    from_uid = str(from_user_id)
    to_uid = str(to_user_id)

    if from_uid == to_uid:
        raise TransferRefused(
            "That is already the owner — nothing to transfer.", code="same_owner"
        )

    await assert_can_receive(db, organization, to_uid)

    report_ids = list(
        (
            await db.execute(
                select(Report.id).where(
                    Report.organization_id == org_id,
                    Report.user_id == from_uid,
                    Report.deleted_at.is_(None),
                    Report.status != "archived",
                )
            )
        )
        .scalars()
        .all()
    )

    # ★Narrowed BEFORE the transfer, not filtered out of the result afterwards.
    # The children — artifacts, queries, scheduled prompts, notes — are selected
    # off ``moved_report_ids`` inside ``transfer_reports``, so a conversation
    # that never enters that list keeps its whole subtree with the departing
    # account. Dropping rows from the result instead would move them and lie
    # about it.
    conversations_left_behind = 0
    if not include_conversations:
        working = await working_report_ids(db, report_ids)
        kept = [r for r in report_ids if str(r) in working]
        conversations_left_behind = len(report_ids) - len(kept)
        report_ids = kept

    result = await transfer_reports(
        db,
        organization,
        report_ids,
        to_user_id=to_uid,
        actor_user_id=actor_user_id,
        reason=reason,
        keep_access_for_previous_owner=keep_access_for_previous_owner,
    )
    # ★Carried on the result rather than inferred by the caller from
    # ``summary.reports - moved['report']``. That subtraction is wrong anyway —
    # a report already owned by the recipient is skipped too — and a caller
    # doing arithmetic to discover a deliberate decision is a caller that will
    # eventually report it as a failure.
    result.conversations_left_behind = conversations_left_behind

    if include_projects:
        projects = (
            (
                await db.execute(
                    select(Project).where(
                        Project.organization_id == org_id,
                        Project.user_id == from_uid,
                        Project.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for project in projects:
            project.user_id = to_uid
            db.add(
                OwnershipTransfer(
                    organization_id=org_id,
                    resource_type=RESOURCE_PROJECT,
                    resource_id=str(project.id),
                    from_user_id=from_uid,
                    to_user_id=to_uid,
                    actor_user_id=str(actor_user_id) if actor_user_id else None,
                    reason=reason,
                    batch_id=result.batch_id,
                    previous_state=None,
                )
            )
        result.moved[RESOURCE_PROJECT] = len(projects)

    if include_assets:
        # ★Counted BEFORE the move, while the rows still carry the departing
        # owner. Asking afterwards returns zero — every one of them now belongs
        # to the recipient — and the dialog would report a clean transfer of
        # exactly the agents it most needed to warn about.
        result.credential_bound_data_sources = len(
            await _credential_bound_data_source_ids(db, org_id, from_uid)
        )

        for model, kind, extra in (
            (Instruction, RESOURCE_INSTRUCTION, (_shared_instructions_only(),)),
            (Prompt, RESOURCE_PROMPT, (_non_private_prompts_only(),)),
            # ★★★**Owner only.** ``is_public``, ``publish_status`` and
            # ``data_source_memberships`` are untouched, here and everywhere: a
            # transfer moves responsibility, never visibility (module
            # docstring). This is the easiest place in the whole service to
            # break that rule, because re-owning an agent *looks* like it should
            # also hand over who can reach it — and quietly publishing a private
            # agent, or adding the recipient to its membership list, is an
            # access change nobody asked for and nobody would see.
            (DataSource, RESOURCE_DATA_SOURCE, ()),
        ):
            result.moved[kind] = await _move_org_assets(
                db,
                model,
                kind,
                org_id=org_id,
                from_uid=from_uid,
                to_uid=to_uid,
                extra_where=extra,
                actor_user_id=actor_user_id,
                reason=reason,
                batch_id=result.batch_id,
            )

    await db.flush()
    return result


# ──────────────────────────── undo ────────────────────────────────────────


async def undo(
    db: AsyncSession,
    organization,
    batch_id: str,
    *,
    actor_user_id: Optional[str],
    undo_days: int = DEFAULT_UNDO_DAYS,
) -> TransferResult:
    """Put a whole batch back exactly, including the run identity.

    ★Restoring the owner alone is a half-undo. If the report was running as its
    creator, the identity moved too, and leaving it pointed at the new owner
    means the dashboard now runs as somebody who was never chosen.
    """
    org_id = str(organization.id)
    rows = (
        (
            await db.execute(
                select(OwnershipTransfer).where(
                    OwnershipTransfer.batch_id == str(batch_id),
                    OwnershipTransfer.organization_id == org_id,
                    OwnershipTransfer.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise TransferRefused("That transfer no longer exists.", code="unknown_batch")

    if any(r.reverted_at is not None for r in rows):
        raise TransferRefused(
            "That transfer has already been undone.", code="already_reverted"
        )

    oldest = min((r.created_at for r in rows if r.created_at), default=None)
    if oldest is not None and datetime.utcnow() - oldest > timedelta(days=undo_days):
        raise TransferRefused(
            f"That transfer is more than {undo_days} days old and can no longer be undone.",
            code="window_expired",
        )

    result = TransferResult(batch_id=str(uuid.uuid4()))
    now = datetime.utcnow()

    for row in rows:
        model = _RESOURCE_MODELS.get(row.resource_type)
        if model is None:
            continue

        obj = (
            await db.execute(select(model).where(model.id == row.resource_id))
        ).scalar_one_or_none()
        if obj is None:
            # Deleted since the transfer. Not an error — mark the ledger row
            # reverted so the batch does not stay half-undoable forever.
            row.reverted_at = now
            continue

        # ★Not ``obj.user_id`` — see ``_OWNER_ATTR``. An agent's owner column is
        # ``owner_user_id``, and writing the wrong name here neither raises nor
        # persists: the batch would report itself fully reverted while every
        # agent stayed with the person who left.
        setattr(obj, _OWNER_ATTR[row.resource_type], row.from_user_id)

        state = json.loads(row.previous_state) if row.previous_state else {}
        if row.resource_type == RESOURCE_REPORT and "shared_run_identity" in state:
            obj.shared_run_identity = state["shared_run_identity"]
        if state.get("granted_share_to_previous_owner"):
            await _clear_redundant_share(db, str(obj.id), str(row.from_user_id))

        row.reverted_at = now
        result.moved[row.resource_type] = result.moved.get(row.resource_type, 0) + 1

        db.add(
            OwnershipTransfer(
                organization_id=org_id,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                from_user_id=row.to_user_id,
                to_user_id=row.from_user_id,
                actor_user_id=str(actor_user_id) if actor_user_id else None,
                reason="undo",
                batch_id=result.batch_id,
                previous_state=None,
            )
        )

    await db.flush()
    return result


# ──────────────────────────── internals ───────────────────────────────────


async def _move_children(
    db: AsyncSession,
    model,
    kind: str,
    report_ids: list[str],
    *,
    org_id: str,
    to_uid: str,
    actor_user_id: Optional[str],
    reason: str,
    batch_id: str,
) -> int:
    """Re-own the rows hanging off a set of reports, and record each one."""
    rows = (
        (
            await db.execute(
                select(model).where(
                    model.report_id.in_(report_ids),
                    model.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    moved = 0
    for obj in rows:
        from_uid = str(obj.user_id) if obj.user_id else None
        if from_uid == to_uid:
            continue
        obj.user_id = to_uid
        db.add(
            OwnershipTransfer(
                organization_id=org_id,
                resource_type=kind,
                resource_id=str(obj.id),
                from_user_id=from_uid,
                to_user_id=to_uid,
                actor_user_id=str(actor_user_id) if actor_user_id else None,
                reason=reason,
                batch_id=batch_id,
                previous_state=None,
            )
        )
        moved += 1
    return moved


async def _move_org_assets(
    db: AsyncSession,
    model,
    kind: str,
    *,
    org_id: str,
    from_uid: str,
    to_uid: str,
    extra_where: tuple = (),
    actor_user_id: Optional[str],
    reason: str,
    batch_id: str,
) -> int:
    """Re-own one person's org-level rows of one kind, and record each one.

    The sibling of ``_move_children`` for things that hang off the organization
    rather than off a report. Two differences, both forced by what these rows
    are:

    - the owner column is looked up in ``_OWNER_ATTR`` rather than assumed to be
      ``user_id`` — ``DataSource`` is the reason;
    - ``extra_where`` carries the privacy predicate, so the rows this moves are
      the same rows ``summarize`` counted. It is passed in rather than derived
      here on purpose: one expression, defined once, used by both.

    Nothing but the owner column is written. No visibility flag, no membership
    row, no publish state.
    """
    owner_attr = _OWNER_ATTR[kind]
    owner_col = getattr(model, owner_attr)

    rows = (
        (
            await db.execute(
                select(model).where(
                    model.organization_id == org_id,
                    owner_col == from_uid,
                    model.deleted_at.is_(None),
                    *extra_where,
                )
            )
        )
        .scalars()
        .all()
    )

    moved = 0
    for obj in rows:
        setattr(obj, owner_attr, to_uid)
        db.add(
            OwnershipTransfer(
                organization_id=org_id,
                resource_type=kind,
                resource_id=str(obj.id),
                from_user_id=from_uid,
                to_user_id=to_uid,
                actor_user_id=str(actor_user_id) if actor_user_id else None,
                reason=reason,
                batch_id=batch_id,
                # Nothing here is a run identity, so there is no second half to
                # restore — the owner column is the entire change, and an empty
                # previous_state says so rather than hiding an omission.
                previous_state=None,
            )
        )
        moved += 1
    return moved


async def _clear_redundant_share(db: AsyncSession, report_id: str, user_id: str) -> None:
    """Drop a share row that the new ownership makes meaningless.

    ★``report_shares`` is unique on ``(report_id, user_id, share_type)``. If the
    recipient already had a share and we leave it, a later re-share collides on
    that constraint — and an owner needing a share row to reach their own report
    is a state nobody should have to reason about.
    """
    rows = (
        (
            await db.execute(
                select(ReportShare).where(
                    ReportShare.report_id == report_id,
                    ReportShare.user_id == user_id,
                    ReportShare.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.deleted_at = datetime.utcnow()


async def _grant_share(db: AsyncSession, report_id: str, user_id: str) -> bool:
    """Let the previous owner keep working on what they just handed over.

    Figma's rule: the person transferring stays on as an admin rather than being
    ejected. A handover that instantly removes your ability to answer questions
    about your own work is a handover people quietly avoid doing.

    Returns False when a share already existed, so ``undo`` knows not to remove
    something it did not create.
    """
    existing = (
        await db.execute(
            select(ReportShare).where(
                ReportShare.report_id == report_id,
                ReportShare.user_id == user_id,
                ReportShare.share_type == "artifact",
                ReportShare.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False

    db.add(
        ReportShare(report_id=report_id, user_id=user_id, share_type="artifact")
    )
    return True
