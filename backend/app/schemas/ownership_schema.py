from typing import List, Optional

from pydantic import BaseModel, Field


class ContentSummarySchema(BaseModel):
    """What one person owns, by kind. Backs the counts on every screen."""

    reports: int = 0
    artifacts: int = 0
    queries: int = 0
    scheduled_prompts: int = 0
    projects: int = 0
    # The agent's per-report working notes. A child of a report exactly like an
    # artifact or a query, so it always moves with one.
    notes: int = 0
    # ★SHARED instructions and NON-PRIVATE prompts only. A private item is a
    # note-to-self and stays with the person; a published glossary entry is an
    # organizational asset that merely has an author. The count has to be the
    # rows the transfer will actually move, or the screen promises one thing and
    # the batch does another.
    instructions: int = 0
    prompts: int = 0
    # Agents this person owns. Ownership only — a transfer never changes
    # is_public, publish_status or who is a member of an agent.
    data_sources: int = 0
    # ★Surfaced separately, not folded into the total. These are the reports
    # whose queries run as their OWNER, using that person's data-source
    # sign-in — so a transfer changes whose credentials the dashboard uses, and
    # the person choosing a recipient has to be told before they confirm.
    creator_identity_reports: int = 0
    # ★Also a sub-count, of ``data_sources``, and also out of the total. Agents
    # on a `user_required` connection, where ownership is read as a CAPABILITY:
    # after the move a fabric_user / powerbi_user agent stops answering until
    # the recipient signs in themselves, and every other user_required type
    # instead hands them the system credentials. Both need saying out loud
    # before anyone confirms.
    credential_bound_data_sources: int = 0
    # ★A third sub-count, of ``reports``, and also out of the total. A Report on
    # this product IS the conversation thread — a dashboard or deck is an
    # Artifact hanging off it — so most of what a bulk transfer would move is
    # chat. These are the reports with no artifact, no schedule and no share:
    # pure conversation, which the automatic paths (successor, admin
    # offboarding) deliberately leave with the departing account.
    conversation_reports: int = 0
    total: int = 0


class OwnedItemSchema(BaseModel):
    """One row on the My content list."""

    id: str
    kind: str                       # 'report' | 'project'
    title: Optional[str] = None
    # 'Dashboard' / 'Slides' / 'Chat' — the sub-label the reports list shows.
    # ★Derived from the report's live artifacts by
    # ``ownership_service.report_type_label``, which is the SAME signal the
    # automatic transfer paths split on. So a row labelled 'Chat' here is
    # exactly a row an offboarding will leave behind, and the two can never
    # tell a person different things. It used to carry ``report_type``, which
    # is only ever 'regular' or 'test' — a real column answering a question
    # nobody had asked.
    type_label: Optional[str] = None
    shared_with_count: int = 0
    has_schedule: bool = False
    runs_as_owner: bool = False
    updated_at: Optional[str] = None


class TransferRequest(BaseModel):
    """Hand specific things over. Empty ``report_ids`` means 'everything'."""

    to_user_id: str
    report_ids: Optional[List[str]] = None
    include_projects: bool = True
    # Default True on the self-service path: handing over is not the same as
    # walking away, and a handover that instantly removes your ability to
    # answer questions about your own work is one people quietly avoid doing.
    keep_access: bool = True


class TransferResultSchema(BaseModel):
    batch_id: str
    moved: dict = Field(default_factory=dict)
    total: int = 0
    creator_identity_repointed: int = 0
    # How many of the moved agents were credential-bound — see
    # ContentSummarySchema. Reported after the fact so the result can say which
    # agents now need attention, not only that the batch succeeded.
    credential_bound_data_sources: int = 0
    # ★How many reports were NOT moved because they are pure conversation.
    # Nonzero only on the automatic paths. Sent so the confirmation screen can
    # say so out loud: a batch reporting "moved 12" for somebody who owned 47
    # reads as a half-finished job, and an admin who cannot tell "left on
    # purpose" from "failed" goes hunting for the other 35.
    conversations_left_behind: int = 0


class SuccessorSchema(BaseModel):
    successor_user_id: Optional[str] = None
    successor_name: Optional[str] = None
    successor_email: Optional[str] = None


class OrphanedOwnerSchema(BaseModel):
    """One deactivated person who still owns something. Needs-an-owner view."""

    user_id: str
    membership_id: str
    name: str
    email: Optional[str] = None
    # ★Present means the automatic handover ran and did NOT clear the content —
    # normally because the successor has since left too. Showing the name tells
    # an admin to pick somebody else rather than wonder why the rule never fired.
    successor_name: Optional[str] = None
    summary: ContentSummarySchema = Field(default_factory=ContentSummarySchema)


class AtRiskAgentSchema(BaseModel):
    """One agent that would stop being usable if its owner left."""

    id: str
    name: Optional[str] = None
    # The connector types of its `user_required` connections. Carried so the
    # screen can say *why* — "Microsoft Fabric" explains a lock-out in a way
    # "this agent" does not — and plural because the relationship is M:N.
    connection_types: List[str] = Field(default_factory=list)


class AtRiskReportSchema(BaseModel):
    """One scheduled dashboard that runs on its owner's data-source sign-in."""

    id: str
    title: Optional[str] = None
    # ★Whether any of its schedules is live. A paused one is still listed —
    # somebody may un-pause it — but it is left out of the `at_risk` count,
    # because a dashboard that is not arriving today cannot stop arriving
    # tomorrow, and overstating the number is what makes people ignore it.
    active: bool = False


class DepartureRiskSchema(BaseModel):
    """What would break if this person left tomorrow.

    ★★★Every other surface in this feature speaks on the day somebody's account
    is switched off, which is the day it is already too late to arrange
    anything. This one is the same counting asked a day earlier: read-only,
    assembled from rows that already exist, and the only screen an administrator
    can still act on.
    """

    user_id: str
    membership_id: Optional[str] = None
    name: str
    email: Optional[str] = None
    # False means the breakage is not hypothetical — this person is already
    # gone. Such rows are `orphaned-content`'s subject, but omitting them from a
    # view titled "what would break" would let an install look clear when it is
    # not.
    is_active: bool = True
    successor_user_id: Optional[str] = None
    successor_name: Optional[str] = None
    # ★A person who has named one is already covered and is ordered below
    # everybody who has not. The nomination is the arrangement this whole view
    # exists to prompt.
    has_successor: bool = False
    # ★★★The split that a single number would hide. Both come from the same
    # `user_required` connection and they are opposites: a fabric_user /
    # powerbi_user agent leaves the next owner LOCKED OUT until they sign in
    # themselves, while every other type silently hands them the organization's
    # shared sign-in. One is a broken agent, the other is an access change, and
    # they need different arrangements made.
    locked_out_agents: List[AtRiskAgentSchema] = Field(default_factory=list)
    shares_system_credentials_agents: List[AtRiskAgentSchema] = Field(
        default_factory=list
    )
    # Reports on a cron whose queries run as their creator. They keep firing
    # into a sign-in that no longer works, and no error reaches the people who
    # relied on the output — because they were never the ones running it. This
    # is the one organizations discover months late.
    scheduled_reports: List[AtRiskReportSchema] = Field(default_factory=list)
    # Both agent lists plus the LIVE schedules. Named things, so this is a
    # triage order rather than a score.
    at_risk: int = 0


class SuccessorUpdate(BaseModel):
    """Send null to clear. Absent and null mean the same thing here — unlike the
    sentinel-string convention used for report.model_id, because there is no
    third state to distinguish."""

    successor_user_id: Optional[str] = None
